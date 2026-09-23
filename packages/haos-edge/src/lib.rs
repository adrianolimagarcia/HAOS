pub mod compactor;
pub mod okf;
pub mod worker_snapshot;
pub mod writer_lock;

use compactor::{ChatMessage, ContextCompactor};
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
/// Grava o vetor f32 diretamente como BLOB binário (little-endian bytes) na coluna vector_json.
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

    let byte_len = (vector_len as usize) * std::mem::size_of::<f32>();
    let bytes_slice = unsafe { std::slice::from_raw_parts(vector_ptr as *const u8, byte_len) };

    upsert_blob_internal(
        db_path_cstr,
        record_id_cstr,
        model_version_cstr,
        bytes_slice,
        vector_len as usize,
    )
}

/// Grava diretamente um buffer de bytes brutos (BLOB) como vetor na coluna vector_json.
/// blob_len deve ser múltiplo de 4 (tamanho de f32).
#[no_mangle]
pub extern "C" fn vector_engine_upsert_blob(
    db_path_cstr: *const c_char,
    record_id_cstr: *const c_char,
    model_version_cstr: *const c_char,
    blob_ptr: *const u8,
    blob_len: c_int,
) -> c_int {
    if db_path_cstr.is_null()
        || record_id_cstr.is_null()
        || model_version_cstr.is_null()
        || blob_ptr.is_null()
        || blob_len <= 0
        || (blob_len as usize % std::mem::size_of::<f32>() != 0)
    {
        return -1;
    }

    let dimensions = (blob_len as usize) / std::mem::size_of::<f32>();
    let bytes_slice = unsafe { std::slice::from_raw_parts(blob_ptr, blob_len as usize) };

    upsert_blob_internal(
        db_path_cstr,
        record_id_cstr,
        model_version_cstr,
        bytes_slice,
        dimensions,
    )
}

fn upsert_blob_internal(
    db_path_cstr: *const c_char,
    record_id_cstr: *const c_char,
    model_version_cstr: *const c_char,
    blob_bytes: &[u8],
    dimensions: usize,
) -> c_int {
    let db_path = unsafe { CStr::from_ptr(db_path_cstr).to_string_lossy() };
    let record_id = unsafe { CStr::from_ptr(record_id_cstr).to_string_lossy() };
    let model_version = unsafe { CStr::from_ptr(model_version_cstr).to_string_lossy() };

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

    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs_f64())
        .unwrap_or(0.0);

    let res = conn.execute(
        "INSERT OR REPLACE INTO memory_vectors (record_id, model_version, dimensions, vector_json, updated_at)
         VALUES (?1, ?2, ?3, ?4, ?5);",
        params![record_id, model_version, dimensions as i64, blob_bytes, now],
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
        std::ptr::copy_nonoverlapping(
            json_bytes.as_ptr() as *const c_char,
            out_buf,
            json_bytes.len(),
        );
        *out_buf.add(json_bytes.len()) = 0; // null-terminator
    }

    json_bytes.len() as c_int
}

/// Compacta mensagens de contexto JSON via C-ABI de alta performance (<1ms).
///
/// Assinatura:
/// `compact_context_json(messages_json_cstr: *const c_char, max_tool_chars: c_int, keep_last: c_int, out_buf: *mut c_char, out_buf_cap: c_int) -> c_int`
///
/// Retornos:
///  > 0 : Quantidade de bytes gravados em `out_buf` (excluindo null-terminator).
///   -1 : Parâmetros de ponteiro ou capacidades inválidos.
///   -2 : Erro de decodificação UTF-8 da string de entrada C.
///   -3 : Falha ao deserializar JSON de mensagens.
///   -4 : Falha ao serializar JSON compactado.
///   -5 : Buffer de saída (`out_buf_cap`) insuficiente para acomodar o JSON gerado.
#[no_mangle]
pub extern "C" fn compact_context_json(
    messages_json_cstr: *const c_char,
    max_tool_chars: c_int,
    keep_last: c_int,
    out_buf: *mut c_char,
    out_buf_cap: c_int,
) -> c_int {
    if messages_json_cstr.is_null() || out_buf.is_null() || out_buf_cap <= 1 {
        return -1;
    }

    let input_str = match unsafe { CStr::from_ptr(messages_json_cstr) }.to_str() {
        Ok(s) => s,
        Err(_) => return -2,
    };

    let messages: Vec<ChatMessage> = match serde_json::from_str(input_str) {
        Ok(m) => m,
        Err(_) => return -3,
    };

    let effective_max_tool = if max_tool_chars > 0 {
        max_tool_chars as usize
    } else {
        2000
    };
    let effective_keep_last = if keep_last >= 0 {
        keep_last as usize
    } else {
        6
    };

    let (compacted_messages, _truncated, _saved) =
        ContextCompactor::compact_messages(messages, effective_max_tool, effective_keep_last);

    let json_bytes = match serde_json::to_vec(&compacted_messages) {
        Ok(b) => b,
        Err(_) => return -4,
    };

    if json_bytes.len() >= (out_buf_cap as usize) {
        return -5; // Buffer insuficiente
    }

    unsafe {
        std::ptr::copy_nonoverlapping(
            json_bytes.as_ptr() as *const c_char,
            out_buf,
            json_bytes.len(),
        );
        *out_buf.add(json_bytes.len()) = 0; // null-terminator
    }

    json_bytes.len() as c_int
}

// ---------------------------------------------------------------------------
// High-performance C-ABI extensions for GraphRAG and Canonical Store
// ---------------------------------------------------------------------------

#[inline]
fn open_readonly_nomutex<P: AsRef<Path>>(path: P) -> rusqlite::Result<Connection> {
    Connection::open_with_flags(
        path,
        OpenFlags::SQLITE_OPEN_READ_ONLY | OpenFlags::SQLITE_OPEN_NO_MUTEX,
    )
}

#[inline]
unsafe fn copy_json_to_buffer(
    json_bytes: &[u8],
    out_buf: *mut c_char,
    out_buf_cap: c_int,
) -> c_int {
    if json_bytes.len() >= (out_buf_cap as usize) {
        return -7; // Buffer do chamador insuficiente
    }
    std::ptr::copy_nonoverlapping(
        json_bytes.as_ptr() as *const c_char,
        out_buf,
        json_bytes.len(),
    );
    *out_buf.add(json_bytes.len()) = 0;
    json_bytes.len() as c_int
}

/// Consulta rápida de entidade e suas relações imediatas (vizinhos/find_related).
/// Retorna JSON: {"entity": {...}, "relations": [...], "neighbors": [...]}
#[no_mangle]
pub extern "C" fn graphrag_engine_find_related_buffered(
    db_path_cstr: *const c_char,
    entity_name_cstr: *const c_char,
    max_hops: c_int,
    out_buf: *mut c_char,
    out_buf_cap: c_int,
) -> c_int {
    if db_path_cstr.is_null() || entity_name_cstr.is_null() || out_buf.is_null() || out_buf_cap <= 1
    {
        return -1;
    }

    let db_path = unsafe { CStr::from_ptr(db_path_cstr).to_string_lossy() };
    let entity_name = unsafe { CStr::from_ptr(entity_name_cstr).to_string_lossy() };

    let p = Path::new(&*db_path);
    if !p.exists() {
        return -2;
    }

    let conn = match open_readonly_nomutex(p) {
        Ok(c) => c,
        Err(_) => return -3,
    };
    let _ = conn.execute_batch("PRAGMA busy_timeout=30000;");

    // 1. Busca nó principal (get_node)
    let mut node_stmt = match conn.prepare(
        "SELECT entity, entity_type, description, community_id, superseded_by, updated_at \
         FROM entities WHERE entity = ?1 COLLATE NOCASE LIMIT 1;",
    ) {
        Ok(s) => s,
        Err(_) => return -4,
    };

    let node_res = node_stmt.query_row(params![entity_name], |row| {
        Ok(serde_json::json!({
            "entity": row.get::<_, String>(0)?,
            "entity_type": row.get::<_, String>(1)?,
            "description": row.get::<_, String>(2)?,
            "community_id": row.get::<_, Option<String>>(3)?,
            "superseded_by": row.get::<_, Option<String>>(4)?,
            "updated_at": row.get::<_, f64>(5)?,
        }))
    });

    let node_json = node_res.ok();

    // 2. Busca relações de 1 salto (ou 2 se max_hops >= 2)
    let hops = if max_hops <= 1 { 1 } else { 2 };
    let mut relations = Vec::new();
    let mut neighbors = std::collections::HashSet::new();

    let mut rel_stmt = match conn.prepare(
        "SELECT source, target, relation_type, description, created_at \
         FROM relations \
         WHERE source = ?1 COLLATE NOCASE OR target = ?1 COLLATE NOCASE \
         ORDER BY source, target, relation_type;",
    ) {
        Ok(s) => s,
        Err(_) => return -5,
    };

    let rel_rows = rel_stmt.query_map(params![entity_name], |row| {
        let src: String = row.get(0)?;
        let tgt: String = row.get(1)?;
        let rel_type: String = row.get(2)?;
        let desc: String = row.get(3)?;
        let created_at: f64 = row.get(4)?;
        Ok((src, tgt, rel_type, desc, created_at))
    });

    let target_lower = entity_name.to_lowercase();
    if let Ok(iter) = rel_rows {
        for item in iter.flatten() {
            let (src, tgt, rel_type, desc, created_at) = item;
            if src.to_lowercase() == target_lower {
                neighbors.insert(tgt.clone());
            } else {
                neighbors.insert(src.clone());
            }
            relations.push(serde_json::json!({
                "source": src,
                "target": tgt,
                "relation_type": rel_type,
                "description": desc,
                "created_at": created_at,
            }));
        }
    }

    if hops >= 2 && !neighbors.is_empty() {
        for n in neighbors.clone() {
            if let Ok(mut second_stmt) = conn.prepare(
                "SELECT source, target, relation_type, description, created_at \
                 FROM relations \
                 WHERE (source = ?1 COLLATE NOCASE OR target = ?1 COLLATE NOCASE) \
                 LIMIT 50;",
            ) {
                if let Ok(rows2) = second_stmt.query_map(params![n], |row| {
                    Ok((
                        row.get::<_, String>(0)?,
                        row.get::<_, String>(1)?,
                        row.get::<_, String>(2)?,
                        row.get::<_, String>(3)?,
                        row.get::<_, f64>(4)?,
                    ))
                }) {
                    for r in rows2.flatten() {
                        let (s, t, rt, d, ca) = r;
                        let s_low = s.to_lowercase();
                        let t_low = t.to_lowercase();
                        if s_low != target_lower {
                            neighbors.insert(s.clone());
                        }
                        if t_low != target_lower {
                            neighbors.insert(t.clone());
                        }
                        relations.push(serde_json::json!({
                            "source": s,
                            "target": t,
                            "relation_type": rt,
                            "description": d,
                            "created_at": ca,
                        }));
                    }
                }
            }
        }
    }

    let neighbors_vec: Vec<String> = neighbors.into_iter().collect();
    let result_obj = serde_json::json!({
        "entity": node_json,
        "relations": relations,
        "neighbors": neighbors_vec,
    });

    let json_bytes = match serde_json::to_vec(&result_obj) {
        Ok(b) => b,
        Err(_) => return -6,
    };

    unsafe { copy_json_to_buffer(&json_bytes, out_buf, out_buf_cap) }
}

/// Busca rápida de entidades no GraphRAG (equivalente ultra-rápido de search_entities)
/// Realiza matching direto e substring em entity, description e community_id em paralelo/C sem GIL.
#[no_mangle]
pub extern "C" fn graphrag_engine_search_entities_buffered(
    db_path_cstr: *const c_char,
    terms_json_cstr: *const c_char,
    out_buf: *mut c_char,
    out_buf_cap: c_int,
) -> c_int {
    if db_path_cstr.is_null() || terms_json_cstr.is_null() || out_buf.is_null() || out_buf_cap <= 1
    {
        return -1;
    }

    let db_path = unsafe { CStr::from_ptr(db_path_cstr).to_string_lossy() };
    let terms_str = match unsafe { CStr::from_ptr(terms_json_cstr) }.to_str() {
        Ok(s) => s,
        Err(_) => return -2,
    };

    let p = Path::new(&*db_path);
    if !p.exists() {
        return -3;
    }

    let terms: Vec<String> = match serde_json::from_str(terms_str) {
        Ok(t) => t,
        Err(_) => return -4,
    };

    let clean_terms: Vec<String> = terms
        .into_iter()
        .map(|t| t.trim().to_lowercase())
        .filter(|t| !t.is_empty())
        .collect();

    let conn = match open_readonly_nomutex(p) {
        Ok(c) => c,
        Err(_) => return -5,
    };
    let _ = conn.execute_batch("PRAGMA busy_timeout=30000;");

    let mut stmt = match conn.prepare(
        "SELECT entity, entity_type, description, community_id, superseded_by, updated_at \
         FROM entities ORDER BY entity;",
    ) {
        Ok(s) => s,
        Err(_) => return -6,
    };

    let rows = match stmt.query_map([], |row| {
        Ok((
            row.get::<_, String>(0)?,
            row.get::<_, String>(1)?,
            row.get::<_, String>(2)?,
            row.get::<_, Option<String>>(3)?,
            row.get::<_, Option<String>>(4)?,
            row.get::<_, f64>(5)?,
        ))
    }) {
        Ok(r) => r,
        Err(_) => return -7,
    };

    let mut matched = Vec::new();
    for r in rows.flatten() {
        let (entity, entity_type, description, community_id, superseded_by, updated_at) = r;
        let mut hit = clean_terms.is_empty();
        if !hit {
            let ent_lower = entity.to_lowercase();
            let desc_lower = description.to_lowercase();
            let comm_lower = community_id.as_deref().unwrap_or("").to_lowercase();
            for t in &clean_terms {
                if ent_lower.contains(t) || desc_lower.contains(t) || comm_lower.contains(t) {
                    hit = true;
                    break;
                }
            }
        }
        if hit {
            matched.push(serde_json::json!({
                "entity": entity,
                "entity_type": entity_type,
                "description": description,
                "community_id": community_id,
                "superseded_by": superseded_by,
                "updated_at": updated_at,
            }));
        }
    }

    let json_bytes = match serde_json::to_vec(&matched) {
        Ok(b) => b,
        Err(_) => return -8,
    };

    unsafe { copy_json_to_buffer(&json_bytes, out_buf, out_buf_cap) }
}

/// Leitura ultra-rápida de registros canônicos por IDs e scopes (sem GIL nem contenção de lock).
#[no_mangle]
pub extern "C" fn canonical_engine_read_records_buffered(
    db_path_cstr: *const c_char,
    record_ids_json_cstr: *const c_char,
    scopes_json_cstr: *const c_char,
    out_buf: *mut c_char,
    out_buf_cap: c_int,
) -> c_int {
    if db_path_cstr.is_null()
        || record_ids_json_cstr.is_null()
        || scopes_json_cstr.is_null()
        || out_buf.is_null()
        || out_buf_cap <= 1
    {
        return -1;
    }

    let db_path = unsafe { CStr::from_ptr(db_path_cstr).to_string_lossy() };
    let ids_str = match unsafe { CStr::from_ptr(record_ids_json_cstr) }.to_str() {
        Ok(s) => s,
        Err(_) => return -2,
    };
    let scopes_str = match unsafe { CStr::from_ptr(scopes_json_cstr) }.to_str() {
        Ok(s) => s,
        Err(_) => return -3,
    };

    let p = Path::new(&*db_path);
    if !p.exists() {
        return -4;
    }

    let record_ids: Vec<String> = match serde_json::from_str(ids_str) {
        Ok(ids) => ids,
        Err(_) => return -5,
    };
    let scopes: Vec<String> = match serde_json::from_str(scopes_str) {
        Ok(s) => s,
        Err(_) => return -6,
    };

    if record_ids.is_empty() || scopes.is_empty() {
        let empty = b"[]";
        return unsafe { copy_json_to_buffer(empty, out_buf, out_buf_cap) };
    }

    let conn = match open_readonly_nomutex(p) {
        Ok(c) => c,
        Err(_) => return -7,
    };
    let _ = conn.execute_batch("PRAGMA busy_timeout=30000;");

    // Cria cláusulas IN parametrizadas dinamicamente
    let id_placeholders: String = (1..=record_ids.len())
        .map(|i| format!("?{}", i))
        .collect::<Vec<_>>()
        .join(",");
    let scope_start = record_ids.len() + 1;
    let scope_placeholders: String = (scope_start..scope_start + scopes.len())
        .map(|i| format!("?{}", i))
        .collect::<Vec<_>>()
        .join(",");

    let sql = format!(
        "SELECT record_id, logical_id, revision, scope, kind, status, content, \
                content_hash, confidence, provenance_json, metadata_json, \
                valid_from, valid_until, supersedes_json \
         FROM memory_records \
         WHERE status='active' AND record_id IN ({}) AND scope IN ({});",
        id_placeholders, scope_placeholders
    );

    let mut stmt = match conn.prepare(&sql) {
        Ok(s) => s,
        Err(_) => return -8,
    };

    let mut params: Vec<&dyn rusqlite::ToSql> = Vec::with_capacity(record_ids.len() + scopes.len());
    for id in &record_ids {
        params.push(id);
    }
    for sc in &scopes {
        params.push(sc);
    }

    let rows = match stmt.query_map(rusqlite::params_from_iter(params), |row| {
        Ok(serde_json::json!({
            "record_id": row.get::<_, String>(0)?,
            "logical_id": row.get::<_, String>(1)?,
            "revision": row.get::<_, i64>(2)?,
            "scope": row.get::<_, String>(3)?,
            "kind": row.get::<_, String>(4)?,
            "status": row.get::<_, String>(5)?,
            "content": row.get::<_, String>(6)?,
            "content_hash": row.get::<_, String>(7)?,
            "confidence": row.get::<_, f64>(8)?,
            "provenance_json": row.get::<_, String>(9)?,
            "metadata_json": row.get::<_, String>(10)?,
            "valid_from": row.get::<_, f64>(11)?,
            "valid_until": row.get::<_, Option<f64>>(12)?,
            "supersedes_json": row.get::<_, String>(13)?,
        }))
    }) {
        Ok(r) => r,
        Err(_) => return -9,
    };

    let mut results = Vec::new();
    for r in rows.flatten() {
        results.push(r);
    }

    let json_bytes = match serde_json::to_vec(&results) {
        Ok(b) => b,
        Err(_) => return -10,
    };

    unsafe { copy_json_to_buffer(&json_bytes, out_buf, out_buf_cap) }
}

/// Busca FTS5 rápida em memória canônica diretamente em Rust nativo.
#[no_mangle]
pub extern "C" fn canonical_engine_search_fts_buffered(
    db_path_cstr: *const c_char,
    fts_expression_cstr: *const c_char,
    scopes_json_cstr: *const c_char,
    limit: c_int,
    out_buf: *mut c_char,
    out_buf_cap: c_int,
) -> c_int {
    if db_path_cstr.is_null()
        || fts_expression_cstr.is_null()
        || scopes_json_cstr.is_null()
        || limit <= 0
        || out_buf.is_null()
        || out_buf_cap <= 1
    {
        return -1;
    }

    let db_path = unsafe { CStr::from_ptr(db_path_cstr).to_string_lossy() };
    let expression = match unsafe { CStr::from_ptr(fts_expression_cstr) }.to_str() {
        Ok(s) => s,
        Err(_) => return -2,
    };
    let scopes_str = match unsafe { CStr::from_ptr(scopes_json_cstr) }.to_str() {
        Ok(s) => s,
        Err(_) => return -3,
    };

    let p = Path::new(&*db_path);
    if !p.exists() {
        return -4;
    }

    let scopes: Vec<String> = match serde_json::from_str(scopes_str) {
        Ok(s) => s,
        Err(_) => return -5,
    };

    if scopes.is_empty() || expression.trim().is_empty() {
        let empty = b"[]";
        return unsafe { copy_json_to_buffer(empty, out_buf, out_buf_cap) };
    }

    let conn = match open_readonly_nomutex(p) {
        Ok(c) => c,
        Err(_) => return -6,
    };
    let _ = conn.execute_batch("PRAGMA busy_timeout=30000;");

    let scope_placeholders: String = (2..=scopes.len() + 1)
        .map(|i| format!("?{}", i))
        .collect::<Vec<_>>()
        .join(",");
    let sql = format!(
        "SELECT r.record_id, r.logical_id, r.revision, r.scope, r.kind, r.status, r.content, \
                r.content_hash, r.confidence, r.provenance_json, r.metadata_json, \
                r.valid_from, r.valid_until, r.supersedes_json \
         FROM memory_fts f \
         JOIN memory_records r ON r.record_id = f.record_id \
         WHERE memory_fts MATCH ?1 AND r.status = 'active' AND r.scope IN ({}) \
         ORDER BY bm25(memory_fts) LIMIT ?{};",
        scope_placeholders,
        scopes.len() + 2
    );

    let mut stmt = match conn.prepare(&sql) {
        Ok(s) => s,
        Err(_) => return -7,
    };

    let mut params: Vec<&dyn rusqlite::ToSql> = Vec::with_capacity(scopes.len() + 2);
    params.push(&expression);
    for sc in &scopes {
        params.push(sc);
    }
    let lim_i64 = limit as i64;
    params.push(&lim_i64);

    let rows = match stmt.query_map(rusqlite::params_from_iter(params), |row| {
        Ok(serde_json::json!({
            "record_id": row.get::<_, String>(0)?,
            "logical_id": row.get::<_, String>(1)?,
            "revision": row.get::<_, i64>(2)?,
            "scope": row.get::<_, String>(3)?,
            "kind": row.get::<_, String>(4)?,
            "status": row.get::<_, String>(5)?,
            "content": row.get::<_, String>(6)?,
            "content_hash": row.get::<_, String>(7)?,
            "confidence": row.get::<_, f64>(8)?,
            "provenance_json": row.get::<_, String>(9)?,
            "metadata_json": row.get::<_, String>(10)?,
            "valid_from": row.get::<_, f64>(11)?,
            "valid_until": row.get::<_, Option<f64>>(12)?,
            "supersedes_json": row.get::<_, String>(13)?,
        }))
    }) {
        Ok(r) => r,
        Err(_) => return -8,
    };

    let mut results = Vec::new();
    for r in rows.flatten() {
        results.push(r);
    }

    let json_bytes = match serde_json::to_vec(&results) {
        Ok(b) => b,
        Err(_) => return -9,
    };

    unsafe { copy_json_to_buffer(&json_bytes, out_buf, out_buf_cap) }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_fts5_support() {
        let conn = Connection::open_in_memory().unwrap();
        let res = conn.execute_batch(
            "CREATE VIRTUAL TABLE test_fts USING fts5(record_id UNINDEXED, content);
             INSERT INTO test_fts(record_id, content) VALUES ('1', 'hello world');",
        );
        assert!(res.is_ok(), "FTS5 should be supported by bundled sqlite");
    }

    #[test]
    fn test_graphrag_c_abi_functions() {
        let temp_dir = std::env::temp_dir();
        let db_path = temp_dir.join(format!(
            "test_graphrag_{}.db",
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        let conn = Connection::open(&db_path).unwrap();
        conn.execute_batch(
            "CREATE TABLE entities (entity TEXT PRIMARY KEY, entity_type TEXT NOT NULL, description TEXT NOT NULL DEFAULT '', community_id TEXT, superseded_by TEXT, updated_at REAL NOT NULL);
             CREATE TABLE relations (source TEXT NOT NULL, target TEXT NOT NULL, relation_type TEXT NOT NULL, description TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL, PRIMARY KEY (source, target, relation_type));
             INSERT INTO entities VALUES ('Alpha', 'service', 'Core Alpha Service', 'comm_1', NULL, 100.0);
             INSERT INTO entities VALUES ('Beta', 'database', 'Beta Storage DB', 'comm_1', NULL, 100.0);
             INSERT INTO relations VALUES ('Alpha', 'Beta', 'calls', 'calls beta', 100.0);"
        ).unwrap();
        drop(conn);

        let c_db_path = std::ffi::CString::new(db_path.to_str().unwrap()).unwrap();
        let c_entity = std::ffi::CString::new("Alpha").unwrap();
        let mut buf = vec![0u8; 4096];

        let ret = graphrag_engine_find_related_buffered(
            c_db_path.as_ptr(),
            c_entity.as_ptr(),
            1,
            buf.as_mut_ptr() as *mut c_char,
            buf.len() as c_int,
        );
        assert!(ret > 0, "Expected find_related to succeed");
        let parsed: serde_json::Value = serde_json::from_slice(&buf[..ret as usize]).unwrap();
        assert_eq!(parsed["entity"]["entity"], "Alpha");
        assert_eq!(parsed["relations"].as_array().unwrap().len(), 1);
        assert_eq!(parsed["neighbors"].as_array().unwrap().len(), 1);

        // Test search_entities
        let c_terms = std::ffi::CString::new("[\"core\", \"beta\"]").unwrap();
        let ret_search = graphrag_engine_search_entities_buffered(
            c_db_path.as_ptr(),
            c_terms.as_ptr(),
            buf.as_mut_ptr() as *mut c_char,
            buf.len() as c_int,
        );
        assert!(ret_search > 0);
        let search_parsed: Vec<serde_json::Value> =
            serde_json::from_slice(&buf[..ret_search as usize]).unwrap();
        assert_eq!(search_parsed.len(), 2);

        let _ = std::fs::remove_file(db_path);
    }

    #[test]
    fn test_canonical_c_abi_functions() {
        let temp_dir = std::env::temp_dir();
        let db_path = temp_dir.join(format!(
            "test_canonical_{}.db",
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        let conn = Connection::open(&db_path).unwrap();
        conn.execute_batch(
            "CREATE TABLE memory_records (record_id TEXT PRIMARY KEY, logical_id TEXT NOT NULL, revision INTEGER NOT NULL, scope TEXT NOT NULL, kind TEXT NOT NULL, status TEXT NOT NULL, content TEXT NOT NULL, content_hash TEXT NOT NULL, confidence REAL NOT NULL, provenance_json TEXT NOT NULL, metadata_json TEXT NOT NULL, valid_from REAL NOT NULL, valid_until REAL, supersedes_json TEXT NOT NULL, created_at REAL NOT NULL);
             CREATE VIRTUAL TABLE memory_fts USING fts5(record_id UNINDEXED, content);
             INSERT INTO memory_records VALUES ('rec1', 'log1', 1, 'project', 'fact', 'active', 'Rust SQLite engine', 'h1', 1.0, '[]', '{}', 100.0, NULL, '[]', 100.0);
             INSERT INTO memory_records VALUES ('rec2', 'log2', 1, 'project', 'fact', 'superseded', 'Old note', 'h2', 1.0, '[]', '{}', 90.0, NULL, '[]', 90.0);
             INSERT INTO memory_fts VALUES ('rec1', 'Rust SQLite engine');
             INSERT INTO memory_fts VALUES ('rec2', 'Old note');"
        ).unwrap();
        drop(conn);

        let c_db_path = std::ffi::CString::new(db_path.to_str().unwrap()).unwrap();
        let mut buf = vec![0u8; 4096];

        // Read records by IDs
        let c_ids = std::ffi::CString::new("[\"rec1\", \"rec2\"]").unwrap();
        let c_scopes = std::ffi::CString::new("[\"project\"]").unwrap();
        let ret_read = canonical_engine_read_records_buffered(
            c_db_path.as_ptr(),
            c_ids.as_ptr(),
            c_scopes.as_ptr(),
            buf.as_mut_ptr() as *mut c_char,
            buf.len() as c_int,
        );
        assert!(ret_read > 0);
        let read_parsed: Vec<serde_json::Value> =
            serde_json::from_slice(&buf[..ret_read as usize]).unwrap();
        // rec2 is superseded so only active rec1 should return
        assert_eq!(read_parsed.len(), 1);
        assert_eq!(read_parsed[0]["record_id"], "rec1");

        // Search FTS
        let c_expr = std::ffi::CString::new("\"Rust\" \"SQLite\"").unwrap();
        let ret_fts = canonical_engine_search_fts_buffered(
            c_db_path.as_ptr(),
            c_expr.as_ptr(),
            c_scopes.as_ptr(),
            10,
            buf.as_mut_ptr() as *mut c_char,
            buf.len() as c_int,
        );
        assert!(ret_fts > 0);
        let fts_parsed: Vec<serde_json::Value> =
            serde_json::from_slice(&buf[..ret_fts as usize]).unwrap();
        assert_eq!(fts_parsed.len(), 1);
        assert_eq!(fts_parsed[0]["record_id"], "rec1");

        let _ = std::fs::remove_file(db_path);
    }
}

// =========================================================================
// STATE DB ENGINE: LEITURA ULTRA-RÁPIDA DE SESSÕES E MENSAGENS EM RUST
// =========================================================================

/// Carrega mensagens de uma sessão diretamente do state.db em Rust com flags Read-Only + No-Mutex.
/// Retorna o número de bytes gravados em `out_buf` com JSON de mensagens formatado.
#[no_mangle]
pub extern "C" fn state_engine_get_messages_buffered(
    db_path_cstr: *const c_char,
    session_id_cstr: *const c_char,
    limit: c_int,
    offset: c_int,
    out_buf: *mut c_char,
    out_buf_cap: c_int,
) -> c_int {
    if db_path_cstr.is_null() || session_id_cstr.is_null() || out_buf.is_null() || out_buf_cap <= 2
    {
        return -1;
    }

    let db_path = unsafe { CStr::from_ptr(db_path_cstr).to_string_lossy() };
    let session_id = unsafe { CStr::from_ptr(session_id_cstr).to_string_lossy() };
    let p = Path::new(&*db_path);
    if !p.exists() {
        return -2;
    }

    let conn = match Connection::open_with_flags(
        p,
        OpenFlags::SQLITE_OPEN_READ_ONLY | OpenFlags::SQLITE_OPEN_NO_MUTEX,
    ) {
        Ok(c) => c,
        Err(_) => return -3,
    };

    let sql = if limit > 0 {
        "SELECT id, role, content, tool_calls, tool_call_id, name, created_at
         FROM messages WHERE session_id = ?1 AND active = 1 ORDER BY id ASC LIMIT ?2 OFFSET ?3;"
    } else {
        "SELECT id, role, content, tool_calls, tool_call_id, name, created_at
         FROM messages WHERE session_id = ?1 AND active = 1 ORDER BY id ASC;"
    };

    let mut stmt = match conn.prepare(sql) {
        Ok(s) => s,
        Err(_) => return -4,
    };

    let mut messages: Vec<serde_json::Value> = Vec::new();
    let row_mapper = |row: &rusqlite::Row| {
        let id: i64 = row.get(0)?;
        let role: String = row.get(1)?;
        let content: Option<String> = row.get(2)?;
        let tool_calls: Option<String> = row.get(3)?;
        let tool_call_id: Option<String> = row.get(4)?;
        let name: Option<String> = row.get(5)?;
        let created_at: Option<f64> = row.get(6)?;
        Ok((
            id,
            role,
            content,
            tool_calls,
            tool_call_id,
            name,
            created_at,
        ))
    };

    let rows_res: Result<Vec<_>, _> = if limit > 0 {
        stmt.query_map(params![&*session_id, limit, offset], row_mapper)
            .map(|r| r.flatten().collect())
    } else {
        stmt.query_map(params![&*session_id], row_mapper)
            .map(|r| r.flatten().collect())
    };

    let rows = match rows_res {
        Ok(r) => r,
        Err(_) => return -5,
    };

    for r in rows {
        let (id, role, content_raw, tool_calls_raw, tool_call_id, name, created_at) = r;
        let content_val = content_raw
            .as_deref()
            .and_then(|c| serde_json::from_str(c).ok())
            .unwrap_or(serde_json::Value::String(content_raw.unwrap_or_default()));
        let tool_calls_val: Option<serde_json::Value> = tool_calls_raw
            .as_deref()
            .and_then(|tc| serde_json::from_str(tc).ok());

        messages.push(serde_json::json!({
            "id": id,
            "role": role,
            "content": content_val,
            "tool_calls": tool_calls_val,
            "tool_call_id": tool_call_id,
            "name": name,
            "created_at": created_at,
        }));
    }

    let json_bytes = match serde_json::to_vec(&messages) {
        Ok(b) => b,
        Err(_) => return -6,
    };

    if json_bytes.len() >= (out_buf_cap as usize) {
        return -7; // buffer insuficiente
    }

    unsafe {
        std::ptr::copy_nonoverlapping(
            json_bytes.as_ptr() as *const c_char,
            out_buf,
            json_bytes.len(),
        );
        *out_buf.add(json_bytes.len()) = 0;
    }

    json_bytes.len() as c_int
}
