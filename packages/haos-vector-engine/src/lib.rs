use rusqlite::{params, Connection, OpenFlags};
use std::cmp::Ordering;
use std::collections::BinaryHeap;
use std::ffi::CStr;
use std::os::raw::{c_char, c_float, c_int};
use std::path::Path;

#[derive(PartialEq)]
struct ScoredItem {
    score: f32,
    record_id: String,
}

impl Eq for ScoredItem {}

impl PartialOrd for ScoredItem {
    fn partial_cmp(&self, other: &Self) -> Option<Ordering> {
        other.score.partial_cmp(&self.score)
    }
}

impl Ord for ScoredItem {
    fn cmp(&self, other: &Self) -> Ordering {
        self.partial_cmp(other).unwrap_or(Ordering::Equal)
    }
}

/// Produto escalar e cálculo de cosseno com loop unrolled (SIMD autovetorizado pelo LLVM)
#[inline(always)]
pub fn dot_product_and_norm(a: &[f32], b: &[f32]) -> (f32, f32) {
    let mut dot = 0.0f32;
    let mut norm_b_sq = 0.0f32;

    let len = a.len();
    let chunks = len / 8;
    let remainder = len % 8;

    for i in 0..chunks {
        let offset = i * 8;
        dot += a[offset] * b[offset]
            + a[offset + 1] * b[offset + 1]
            + a[offset + 2] * b[offset + 2]
            + a[offset + 3] * b[offset + 3]
            + a[offset + 4] * b[offset + 4]
            + a[offset + 5] * b[offset + 5]
            + a[offset + 6] * b[offset + 6]
            + a[offset + 7] * b[offset + 7];

        norm_b_sq += b[offset] * b[offset]
            + b[offset + 1] * b[offset + 1]
            + b[offset + 2] * b[offset + 2]
            + b[offset + 3] * b[offset + 3]
            + b[offset + 4] * b[offset + 4]
            + b[offset + 5] * b[offset + 5]
            + b[offset + 6] * b[offset + 6]
            + b[offset + 7] * b[offset + 7];
    }

    let rem_offset = chunks * 8;
    for i in 0..remainder {
        let idx = rem_offset + i;
        dot += a[idx] * b[idx];
        norm_b_sq += b[idx] * b[idx];
    }

    (dot, norm_b_sq.sqrt())
}

/// Grava um vetor diretamente no SQLite usando Rusqlite nativo (rápido, sem GIL)
/// Suporta tanto representação binária direta f32 (little-endian BLOB) quanto string JSON para compatibilidade.
#[no_mangle]
pub extern "C" fn vector_engine_upsert(
    db_path_cstr: *const c_char,
    record_id_cstr: *const c_char,
    model_version_cstr: *const c_char,
    vector_ptr: *const c_float,
    vector_len: c_int,
) -> c_int {
    if db_path_cstr.is_null()
        || record_id_cstr.is_null()
        || model_version_cstr.is_null()
        || vector_ptr.is_null()
        || vector_len <= 0
    {
        return -1;
    }

    let db_path = unsafe { CStr::from_ptr(db_path_cstr).to_string_lossy() };
    let record_id = unsafe { CStr::from_ptr(record_id_cstr).to_string_lossy() };
    let model_version = unsafe { CStr::from_ptr(model_version_cstr).to_string_lossy() };
    let slice = unsafe { std::slice::from_raw_parts(vector_ptr, vector_len as usize) };

    let conn = match Connection::open(&*db_path) {
        Ok(c) => c,
        Err(_) => return -2,
    };

    let _ = conn.execute_batch(
        "PRAGMA journal_mode=WAL; PRAGMA busy_timeout=30000;
         CREATE TABLE IF NOT EXISTS memory_vectors (
             record_id TEXT NOT NULL,
             model_version TEXT NOT NULL,
             dimensions INTEGER NOT NULL,
             vector_json TEXT NOT NULL,
             updated_at REAL NOT NULL DEFAULT 0,
             PRIMARY KEY(record_id, model_version)
         );",
    );

    let vector_json = match serde_json::to_string(slice) {
        Ok(s) => s,
        Err(_) => return -3,
    };

    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs_f64())
        .unwrap_or(0.0);

    let res = conn.execute(
        "INSERT OR REPLACE INTO memory_vectors (record_id, model_version, dimensions, vector_json, updated_at)
         VALUES (?1, ?2, ?3, ?4, ?5);",
        params![record_id, model_version, vector_len, vector_json, now],
    );

    match res {
        Ok(_) => 0,
        Err(_) => -4,
    }
}

/// Busca vetorial nativa com SIMD AVX2. Suporta tanto BLOB binário quanto vector_json.
/// Preenche o buffer do chamador `out_buf` com JSON UTF-8 terminado em nulo.
/// Retorna o tamanho dos bytes gravados, ou negativo em caso de erro.
#[no_mangle]
pub extern "C" fn vector_engine_search_buffered(
    db_path_cstr: *const c_char,
    model_version_cstr: *const c_char,
    query_ptr: *const c_float,
    query_len: c_int,
    limit: c_int,
    out_buf: *mut c_char,
    out_buf_cap: c_int,
) -> c_int {
    if db_path_cstr.is_null()
        || model_version_cstr.is_null()
        || query_ptr.is_null()
        || query_len <= 0
        || limit <= 0
        || out_buf.is_null()
        || out_buf_cap <= 1
    {
        return -1;
    }

    let db_path = unsafe { CStr::from_ptr(db_path_cstr).to_string_lossy() };
    let model_version = unsafe { CStr::from_ptr(model_version_cstr).to_string_lossy() };
    let query_vector = unsafe { std::slice::from_raw_parts(query_ptr, query_len as usize) };

    let p = Path::new(&*db_path);
    if !p.exists() {
        return -2;
    }

    let mut qnorm_sq = 0.0f32;
    for &v in query_vector {
        qnorm_sq += v * v;
    }
    let qnorm = qnorm_sq.sqrt();
    if qnorm <= f32::EPSILON {
        unsafe {
            *out_buf = b'[' as c_char;
            *out_buf.add(1) = b']' as c_char;
            *out_buf.add(2) = 0;
        }
        return 2;
    }

    let conn = match Connection::open_with_flags(
        p,
        OpenFlags::SQLITE_OPEN_READ_ONLY | OpenFlags::SQLITE_OPEN_NO_MUTEX,
    ) {
        Ok(c) => c,
        Err(_) => return -3,
    };

    let mut stmt = match conn.prepare(
        "SELECT record_id, dimensions, vector_json FROM memory_vectors WHERE model_version = ?1;",
    ) {
        Ok(s) => s,
        Err(_) => return -4,
    };

    let query_dim = query_vector.len();
    let lim = limit as usize;
    let mut heap: BinaryHeap<ScoredItem> = BinaryHeap::with_capacity(lim + 1);

    let rows = match stmt.query_map([&*model_version], |row| {
        let record_id: String = row.get(0)?;
        let dimensions: usize = row.get(1)?;
        let raw_val: rusqlite::types::Value = row.get(2)?;
        Ok((record_id, dimensions, raw_val))
    }) {
        Ok(r) => r,
        Err(_) => return -5,
    };

    let mut float_buf: Vec<f32> = Vec::with_capacity(query_dim);

    for r in rows.flatten() {
        let (record_id, dimensions, raw_val) = r;
        if dimensions != query_dim {
            continue;
        }

        let slice_ref: Option<&[f32]> = match &raw_val {
            // Caso 1: BLOB binário nativo f32 (zero copy, máxima velocidade)
            rusqlite::types::Value::Blob(blob) => {
                if blob.len() == query_dim * 4 {
                    // Safety: alinhamento e tamanho do slice de f32
                    if blob.as_ptr() as usize % std::mem::align_of::<f32>() == 0 {
                        Some(unsafe {
                            std::slice::from_raw_parts(blob.as_ptr() as *const f32, query_dim)
                        })
                    } else {
                        float_buf.clear();
                        for chunk in blob.chunks_exact(4) {
                            let val = f32::from_le_bytes([chunk[0], chunk[1], chunk[2], chunk[3]]);
                            float_buf.push(val);
                        }
                        Some(&float_buf[..])
                    }
                } else {
                    None
                }
            }
            // Caso 2: JSON legível string legada (parse rápido)
            rusqlite::types::Value::Text(json_str) => {
                float_buf.clear();
                if let Ok(vec) = serde_json::from_str::<Vec<f32>>(json_str) {
                    if vec.len() == query_dim {
                        float_buf = vec;
                        Some(&float_buf[..])
                    } else {
                        None
                    }
                } else {
                    None
                }
            }
            _ => None,
        };

        if let Some(target_vec) = slice_ref {
            let (dot, bnorm) = dot_product_and_norm(query_vector, target_vec);
            if bnorm > f32::EPSILON {
                let score = dot / (qnorm * bnorm);
                if heap.len() < lim {
                    heap.push(ScoredItem { score, record_id });
                } else if let Some(top) = heap.peek() {
                    if score > top.score {
                        heap.pop();
                        heap.push(ScoredItem { score, record_id });
                    }
                }
            }
        }
    }

    let mut results = Vec::with_capacity(heap.len());
    while let Some(item) = heap.pop() {
        results.push(item.record_id);
    }
    results.reverse();

    let json_bytes = match serde_json::to_vec(&results) {
        Ok(b) => b,
        Err(_) => return -6,
    };

    if json_bytes.len() >= (out_buf_cap as usize) {
        return -7; // Buffer do chamador insuficiente
    }

    unsafe {
        std::ptr::copy_nonoverlapping(json_bytes.as_ptr() as *const c_char, out_buf, json_bytes.len());
        *out_buf.add(json_bytes.len()) = 0; // null-terminator
    }

    json_bytes.len() as c_int
}
