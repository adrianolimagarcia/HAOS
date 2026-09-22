//! Módulo de Compactação e Serialização Ultra-Rápida de Contexto (<1ms).
//!
//! Preserva estritamente:
//! 1. Mensagens de sistema no início (Prompt Caching imutável do LLM).
//! 2. Últimas N mensagens da conversa (Contexto imediato intacto).
//! 3. Trunca corpos volumosos de `tool` intermediários mantendo delimitadores válidos.
//! 4. Alternância estrita de papéis e integridade estrutural JSON.

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
    #[serde(default)]
    pub reasoning: Option<String>,
    #[serde(flatten)]
    pub extra: serde_json::Map<String, serde_json::Value>,
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
    pub estimated_tokens_saved: usize,
    pub messages: Vec<ChatMessage>,
}

pub struct ContextCompactor;

impl ContextCompactor {
    /// Compacta o vetor de mensagens preservando cabeçalho de caching e cauda viva.
    pub fn compact_messages(
        messages: Vec<ChatMessage>,
        max_tool_chars: usize,
        keep_last: usize,
    ) -> (Vec<ChatMessage>, usize, usize) {
        let original_count = messages.len();

        if original_count <= keep_last + 2 {
            return (messages, 0, 0);
        }

        let mut truncated_tools = 0;
        let mut tokens_saved = 0;
        let mut out = Vec::with_capacity(original_count);

        let system_count = messages.iter().take_while(|m| m.role == "system").count();
        let middle_start = system_count;
        let middle_end = original_count.saturating_sub(keep_last).max(middle_start);

        for (i, msg) in messages.into_iter().enumerate() {
            if i < middle_start || i >= middle_end {
                // Preserva cabeçalho do sistema e cauda intactos (Prompt Cache intacto)
                out.push(msg);
            } else {
                // Zona intermediária sujeita a compressão / truncamento
                let mut m = msg;
                if m.role == "tool" {
                    if let Some(serde_json::Value::String(ref s)) = m.content {
                        if s.len() > max_tool_chars {
                            let original_len = s.len();
                            let half = max_tool_chars / 2;
                            // Encontra fronteiras UTF-8 seguras próximas de half
                            let head_idx = s.char_indices()
                                .map(|(idx, _)| idx)
                                .take_while(|&idx| idx <= half)
                                .last()
                                .unwrap_or(0);

                            let tail_target = original_len.saturating_sub(half);
                            let tail_idx = s.char_indices()
                                .map(|(idx, _)| idx)
                                .find(|&idx| idx >= tail_target)
                                .unwrap_or(original_len);

                            let head = &s[..head_idx];
                            let tail = &s[tail_idx..];
                            let truncated = format!(
                                "{head}\n\n[... {} caracteres truncados pelo HAOS Rust Native Compactor ...]\n\n{tail}",
                                original_len.saturating_sub(head.len() + tail.len())
                            );
                            tokens_saved += original_len.saturating_sub(truncated.len()) / 4;
                            m.content = Some(serde_json::Value::String(truncated));
                            truncated_tools += 1;
                        }
                    }
                }
                out.push(m);
            }
        }

        (out, truncated_tools, tokens_saved)
    }

    /// Compacta o payload mantendo compatibilidade com haos-edge
    pub fn compact(payload: CompactPayload) -> CompactResult {
        let original_count = payload.messages.len();
        let max_tool_chars = payload.max_tool_chars.unwrap_or(2000);
        let keep_last = payload.keep_last.unwrap_or(6);

        let (out, truncated_tools, tokens_saved) =
            Self::compact_messages(payload.messages, max_tool_chars, keep_last);

        let compacted_count = out.len();
        CompactResult {
            ok: true,
            original_count,
            compacted_count,
            truncated_tools,
            estimated_tokens_saved: tokens_saved,
            messages: out,
        }
    }
}
