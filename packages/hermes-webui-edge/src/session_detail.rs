use serde::Deserialize;
use serde_json::Value;
use std::fs::File;
use std::io::{BufRead, BufReader};
use std::path::{Path, PathBuf};

#[derive(Deserialize, Debug)]
pub struct SessionQuery {
    pub session_id: String,
    pub messages: Option<String>,
    pub resolve_model: Option<String>,
    pub msg_limit: Option<usize>,
    pub msg_before: Option<usize>,
}

/// Resultado da tentativa nativa do handler /api/session.
pub enum NativeOutcome {
    /// Resposta completa pronta em Rust (cache/prefixo/parse pequeno).
    Ready(Value),
    /// Proxyar ao Python (merge canônico) e alimentar o cache de cauda com a
    /// resposta, para que os polls seguintes sejam servidos nativamente.
    ProxyAndCache,
    /// Apenas proxyar (sessões com paginação, sem msg_limit, ou ativas).
    Proxy,
}

/// Caminho do sidecar, com sanitização do session_id. None se inválido.
pub fn sidecar_path(haos_home: &Path, sid: &str) -> Option<PathBuf> {
    if sid.is_empty() || sid.contains('/') || sid.contains('\\') || sid.contains("..") {
        return None;
    }
    Some(haos_home.join("webui").join("sessions").join(format!("{sid}.json")))
}

/// Limite de segurança para parse completo em Rust. O serviço agora roda com
/// MemoryHigh=256M e MemoryMax=400M; parsear sidecars de até 64 MB em serde_json::Value
/// cabe com folga dentro do limite (~150MB pico). Acima de 64 MB, o merge
/// canônico é do Python e o Rust só serve a partir do cache de cauda.
const MAX_FULL_PARSE_BYTES: u64 = 64 * 1024 * 1024;

pub fn load_and_prepare_session(haos_home: &Path, query: &SessionQuery) -> NativeOutcome {
    let proxy = || NativeOutcome::Proxy;

    let session_file = match sidecar_path(haos_home, &query.session_id) {
        Some(p) => p,
        None => return proxy(),
    };
    let sid = &query.session_id;
    let _ = sid;

    let load_messages = query.messages.as_deref().unwrap_or("1") != "0";

    // 1) messages=0: só metadados. Zero full-file parse — os metadados residem
    //    ANTES da chave "messages": [...] no sidecar; ler só o prefixo cai de
    //    centenas de ms para <5ms mesmo em sessões de 40MB.
    if !load_messages {
        if let Some(mut meta_val) = read_sidecar_prefix(&session_file) {
            prepare_metadata_fields(&mut meta_val, 0, false, 0);
            return NativeOutcome::Ready(meta_val);
        }
        return proxy();
    }

    // 2) Cauda com msg_limit (o caso dominante do frontend: msg_limit=100 ou reload até 2000):
    //    cache hit nativo; cache miss: se o arquivo cabe no teto nativo (<=64MB),
    //    faz o parse nativo direto e alimenta o tail_cache com até 2000 msgs;
    //    se exceder 64MB, repassa ao Python e alimenta o cache pela resposta.
    if let Some(limit) = query.msg_limit.filter(|_| query.msg_before.is_none()) {
        if limit > 0 {
            if let Some(cached) = tail_cache::lookup(&session_file, limit) {
                if let Some(mut meta_val) = read_sidecar_prefix(&session_file) {
                    let total = cached.total_count;
                    let n = cached.messages.len();
                    let offset = total.saturating_sub(n);
                    let is_truncated = total > n;
                    meta_val["messages"] = Value::Array(cached.messages);
                    prepare_metadata_fields(&mut meta_val, total, is_truncated, offset);
                    return NativeOutcome::Ready(meta_val);
                }
            }
            let size = std::fs::metadata(&session_file).map(|m| m.len()).unwrap_or(u64::MAX);
            if size > MAX_FULL_PARSE_BYTES {
                return NativeOutcome::ProxyAndCache;
            }
            // Arquivo <= MAX_FULL_PARSE_BYTES: cai no parse nativo abaixo,
            // que fatia a cauda solicitada e popula o tail_cache com até 2000 msgs.
        }
    }

    // 3) Demais formas (sem msg_limit, paginação explícita): parse nativo só
    //    para arquivos até MAX_FULL_PARSE_BYTES; maiores vão para o Python.
    let size = std::fs::metadata(&session_file).map(|m| m.len()).unwrap_or(u64::MAX);
    if size > MAX_FULL_PARSE_BYTES {
        return proxy();
    }

    let file = match File::open(&session_file) {
        Ok(f) => f,
        Err(_) => return proxy(),
    };
    let reader = BufReader::new(file);
    let mut session_val: Value = match serde_json::from_reader(reader) {
        Ok(v) => v,
        Err(_) => return proxy(),
    };
    if !session_val.is_object() {
        return proxy();
    }

    // Move o array de mensagens para fora do Value (sem deep clone).
    let mut sidecar_messages: Vec<Value> = session_val
        .as_object_mut()
        .and_then(|obj| obj.remove("messages"))
        .and_then(|v| match v {
            Value::Array(a) => Some(a),
            other => other.as_array().cloned(),
        })
        .unwrap_or_default();

    let total_count = sidecar_messages.len();

    // Guarda até 2000 mensagens da cauda mais recente para o tail_cache antes de fatiar
    let tail_for_cache = if query.msg_before.is_none() && total_count > 0 {
        let cache_take = total_count.min(2000);
        let cache_start = total_count - cache_take;
        Some(sidecar_messages[cache_start..].to_vec())
    } else {
        None
    };

    let (truncated_msgs, offset) = if let Some(limit) = query.msg_limit {
        let before = query.msg_before.unwrap_or(total_count).min(total_count);
        let start = before.saturating_sub(limit);
        let _dropped_tail = sidecar_messages.split_off(before);
        let kept = if sidecar_messages.len() <= limit {
            std::mem::take(&mut sidecar_messages)
        } else {
            sidecar_messages.split_off(sidecar_messages.len() - limit)
        };
        (kept, start)
    } else {
        (sidecar_messages, 0)
    };

    // Alimenta o tail_cache com a janela ampla (até 2000 msgs) para que
    // qualquer pedido subsequente (100, 500, 1011, 2000) seja atendido na hora.
    if let Some(cache_msgs) = tail_for_cache {
        tail_cache::store(&session_file, total_count, cache_msgs);
    }

    let is_truncated = query.msg_limit.is_some() && offset > 0;

    session_val["messages"] = Value::Array(truncated_msgs);
    let effective_total_count = if total_count > 0 {
        total_count
    } else {
        session_val
            .get("message_count")
            .and_then(|v| v.as_u64())
            .unwrap_or(0) as usize
    };

    prepare_metadata_fields(&mut session_val, effective_total_count, is_truncated, offset);

    NativeOutcome::Ready(session_val)
}

fn prepare_metadata_fields(session_val: &mut Value, total_count: usize, is_truncated: bool, offset: usize) {
    // Remove estruturas pesadas internas que não pertencem ao payload da API
    // e geravam tráfego de megabytes e lentidão no parser de regex / rede.
    if let Some(obj) = session_val.as_object_mut() {
        obj.remove("anchor_activity_scenes");
        obj.remove("context_messages");
        obj.remove("anchor_scene_index");

        // Limitar tool_calls apenas ao tail relevante (máximo 50 itens mais recentes)
        // se o array estiver inflado com centenas de execuções passadas
        if let Some(tc_val) = obj.get_mut("tool_calls") {
            if let Some(tc_arr) = tc_val.as_array_mut() {
                if tc_arr.len() > 50 {
                    let drain_count = tc_arr.len() - 50;
                    tc_arr.drain(0..drain_count);
                }
            }
        }
    }

    if total_count > 0 || session_val.get("message_count").is_none() {
        session_val["message_count"] = Value::from(total_count);
    }
    session_val["_messages_truncated"] = Value::Bool(is_truncated);
    session_val["_messages_offset"] = Value::from(offset);
    session_val["_msg_limit_max"] = Value::from(2000);
    session_val["is_streaming"] = Value::Bool(false);
    session_val["has_pending_user_message"] = Value::Bool(false);

    if session_val.get("last_message_at").is_none() {
        let last_at = session_val.get("updated_at").cloned().unwrap_or(Value::from(0));
        session_val["last_message_at"] = last_at;
    }

    if session_val.get("cache_hit_percent").is_none() {
        session_val["cache_hit_percent"] = Value::from(0);
    }

    if session_val.get("tool_calls").is_none() {
        session_val["tool_calls"] = Value::Array(Vec::new());
    }
    if session_val.get("pending_attachments").is_none() {
        session_val["pending_attachments"] = Value::Array(Vec::new());
    }
    if session_val.get("context_length").is_none() || session_val["context_length"] == 0 {
        session_val["context_length"] = Value::from(1_048_576);
    }
}

/// Lê apenas o prefixo do sidecar até a chave "messages": e devolve o objeto de
/// metadados com messages=[]. O sidecar do WebUI é escrito com json.dump(indent=2)
/// e a chave "messages" fica por último, então o prefixo contém todos os metadados.
fn read_sidecar_prefix(path: &Path) -> Option<Value> {
    let file = File::open(path).ok()?;
    let reader = BufReader::new(file);
    let mut prefix_buf: Vec<u8> = Vec::with_capacity(64 * 1024);
    let mut found = false;

    for line in reader.lines().map_while(Result::ok) {
        if line.trim_start().starts_with("\"messages\":") {
            prefix_buf.extend_from_slice(b"\"messages\": []\n}");
            found = true;
            break;
        }
        prefix_buf.extend_from_slice(line.as_bytes());
        prefix_buf.push(b'\n');
    }

    if !found {
        return None;
    }
    let val: Value = serde_json::from_slice(&prefix_buf).ok()?;
    if val.is_object() {
        Some(val)
    } else {
        None
    }
}

// ============================================================================
// Cache de cauda: evita re-proxar/re-parsear a cada poll do frontend.
// Invalidado por (tamanho, mtime) do arquivo — qualquer escrita no sidecar
// muda o mtime e derruba o cache automaticamente.
// ============================================================================
pub mod tail_cache {
    use serde_json::Value;
    use std::collections::HashMap;
    use std::fs;
    use std::path::Path;
    use std::sync::{Arc, Mutex, OnceLock};
    use std::time::{Duration, UNIX_EPOCH};

    pub struct CachedTail {
        pub total_count: usize,
        pub messages: Vec<Value>,
    }

    struct Entry {
        size: u64,
        mtime_nanos: u128,
        inserted_at: std::time::Instant,
        total_count: usize,
        messages: Arc<Vec<Value>>,
    }

    const TTL: Duration = Duration::from_secs(120);
    const MAX_ENTRIES: usize = 24;

    fn cache() -> &'static Mutex<HashMap<String, Entry>> {
        static CACHE: OnceLock<Mutex<HashMap<String, Entry>>> = OnceLock::new();
        CACHE.get_or_init(|| Mutex::new(HashMap::new()))
    }

    fn file_stamp(path: &Path) -> Option<(u64, u128)> {
        let meta = fs::metadata(path).ok()?;
        let mtime = meta
            .modified()
            .ok()?
            .duration_since(UNIX_EPOCH)
            .ok()?
            .as_nanos();
        Some((meta.len(), mtime))
    }

    pub fn lookup(path: &Path, limit: usize) -> Option<CachedTail> {
        let (size, mtime) = file_stamp(path)?;
        let key = path.to_string_lossy().to_string();
        let guard = cache().lock().ok()?;
        let e = guard.get(&key)?;
        if e.size != size || e.mtime_nanos != mtime || e.inserted_at.elapsed() > TTL {
            return None;
        }
        // A cauda em cache só atende requisições que pedem NO MÁXIMO o que foi
        // armazenado — exceto quando a sessão inteira cabe no cache (total<=limit),
        // caso em que devolver tudo é exatamente o pedido.
        if e.messages.len() < limit && e.total_count > limit {
            return None;
        }
        let take = limit.min(e.messages.len());
        let start = e.messages.len() - take;
        Some(CachedTail {
            total_count: e.total_count,
            messages: e.messages[start..].to_vec(),
        })
    }

    pub fn store(path: &Path, total_count: usize, messages: Vec<Value>) {
        let Some((size, mtime)) = file_stamp(path) else { return };
        let key = path.to_string_lossy().to_string();
        let mut guard = match cache().lock() {
            Ok(g) => g,
            Err(_) => return,
        };
        if guard.len() >= MAX_ENTRIES && !guard.contains_key(&key) {
            if let Some(oldest_key) = guard
                .iter()
                .min_by_key(|(_, e)| e.inserted_at)
                .map(|(k, _)| k.clone())
            {
                guard.remove(&oldest_key);
            }
        }
        guard.insert(
            key,
            Entry {
                size,
                mtime_nanos: mtime,
                inserted_at: std::time::Instant::now(),
                total_count,
                messages: Arc::new(messages),
            },
        );
    }
}
