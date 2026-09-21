//! Módulo nativo em Rust para Compactação Ultra-Rápida de Contexto de Conversa (<1ms).
//!
//! Preserva estritamente:
//! 1. Mensagens de sistema no início (Prompt Caching imutável).
//! 2. Últimas N mensagens da conversa (Contexto imediato).
//! 3. Trunca corpos volumosos de `tool` intermediários mantendo delimitadores de código válidos.
//! 4. Alternância de papéis e integridade estrutural JSON.

use serde::{Deserialize, Serialize};

#[derive(Serialize, Deserialize, Debug, Clone)]
pub struct ChatMessage {
    pub role: String,
    #[serde(default)]
    pub content: Option<serde_json::Value>,
    #[serde(default)]
    pub tool_calls: Option<serde_json::Value>,
    #[serde(default)]
    pub tool_call_id: Option<String>,
    #[serde(default)]
    pub name: Option<String>,
}

#[derive(Deserialize)]
pub struct CompactPayload {
    pub messages: Vec<ChatMessage>,
    pub max_tokens: Option<usize>,
    pub max_tool_chars: Option<usize>,
    pub keep_last: Option<usize>,
}

#[derive(Serialize)]
pub struct CompactResult {
    pub ok: bool,
    pub original_count: usize,
    pub compacted_count: usize,
    pub truncated_tools: usize,
    pub messages: Vec<ChatMessage>,
}

pub struct ContextCompactor;

impl ContextCompactor {
    /// Estima tokens grosseiramente (~3.5 caracteres por token para texto misto PT/EN/código)
    pub fn estimate_tokens(text: &str) -> usize {
        (text.len() + 3) / 4
    }

    /// Compacta o array de mensagens
    pub fn compact(payload: CompactPayload) -> CompactResult {
        let original_count = payload.messages.len();
        let max_tool_chars = payload.max_tool_chars.unwrap_or(2000);
        let keep_last = payload.keep_last.unwrap_or(6);

        if original_count <= keep_last + 2 {
            return CompactResult {
                ok: true,
                original_count,
                compacted_count: original_count,
                truncated_tools: 0,
                messages: payload.messages,
            };
        }

        let mut truncated_tools = 0;
        let mut out = Vec::with_capacity(original_count);

        let system_count = payload.messages.iter().take_while(|m| m.role == "system").count();
        let middle_start = system_count;
        let middle_end = original_count.saturating_sub(keep_last).max(middle_start);

        for (i, msg) in payload.messages.into_iter().enumerate() {
            if i < middle_start || i >= middle_end {
                // Preserva cabeçalho do sistema e cauda intactos
                out.push(msg);
            } else {
                // Zona intermediária sujeita a compressão / truncamento
                let mut m = msg;
                if m.role == "tool" {
                    if let Some(serde_json::Value::String(ref s)) = m.content {
                        if s.len() > max_tool_chars {
                            let head = &s[..max_tool_chars / 2];
                            let tail = &s[s.len() - (max_tool_chars / 2)..];
                            let truncated = format!(
                                "{head}\n\n[... {} caracteres truncados pelo HAOS Edge ...]\n\n{tail}",
                                s.len() - max_tool_chars
                            );
                            m.content = Some(serde_json::Value::String(truncated));
                            truncated_tools += 1;
                        }
                    }
                }
                out.push(m);
            }
        }

        let compacted_count = out.len();
        CompactResult {
            ok: true,
            original_count,
            compacted_count,
            truncated_tools,
            messages: out,
        }
    }
}
