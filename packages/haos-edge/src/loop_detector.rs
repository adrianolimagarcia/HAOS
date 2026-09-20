//! Detector Finito de Loops Infinitos de Tools (adaptado de rustfox).
//!
//! Monitora um buffer circular FIFO de invocações de ferramentas por agente / sessão.
//! Se o agente invocar a mesma ferramenta repetidamente com os mesmos argumentos normalizados
//! acima do limiar configurado (ex: 3 vezes), um alarme de loop é disparado com precisão matemática.

use serde::{Deserialize, Serialize};
use std::collections::VecDeque;
use std::hash::{DefaultHasher, Hash, Hasher};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ToolCallRecord {
    pub tool_name: String,
    pub args_hash: u64,
    pub iteration: usize,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct LoopAlert {
    pub loop_detected: bool,
    pub tool_name: String,
    pub call_count: usize,
    pub message: String,
}

pub struct LoopDetector {
    window: VecDeque<ToolCallRecord>,
    threshold: usize,
    enabled: bool,
}

impl LoopDetector {
    pub fn new(threshold: usize, enabled: bool) -> Self {
        let capacity = if enabled { threshold + 2 } else { 0 };
        Self {
            window: VecDeque::with_capacity(capacity),
            threshold,
            enabled,
        }
    }

    /// Normaliza os argumentos JSON (chaves ordenadas) e computa hash estável de 64 bits
    pub fn compute_hash(tool_name: &str, arguments: &str) -> u64 {
        let normalized = serde_json::from_str::<serde_json::Value>(arguments)
            .ok()
            .map(|v| Self::normalize_json(&v))
            .unwrap_or_else(|| arguments.trim().to_string());

        let mut hasher = DefaultHasher::new();
        tool_name.hash(&mut hasher);
        "|".hash(&mut hasher);
        normalized.hash(&mut hasher);
        hasher.finish()
    }

    fn normalize_json(v: &serde_json::Value) -> String {
        match v {
            serde_json::Value::Object(map) => {
                let mut sorted_keys: Vec<_> = map.keys().collect();
                sorted_keys.sort();
                let parts: Vec<String> = sorted_keys
                    .into_iter()
                    .map(|k| format!("{}:{}", k, Self::normalize_json(&map[k])))
                    .collect();
                format!("{{{}}}", parts.join(","))
            }
            serde_json::Value::Array(arr) => {
                let parts: Vec<String> = arr.iter().map(Self::normalize_json).collect();
                format!("[{}]", parts.join(","))
            }
            serde_json::Value::String(s) => s.trim().to_string(),
            other => other.to_string(),
        }
    }

    /// Registra uma chamada de ferramenta e avalia se há loop
    pub fn record_and_evaluate(
        &mut self,
        tool_name: &str,
        arguments: &str,
        iteration: usize,
    ) -> Option<LoopAlert> {
        if !self.enabled {
            return None;
        }

        let hash = Self::compute_hash(tool_name, arguments);
        self.window.push_back(ToolCallRecord {
            tool_name: tool_name.to_string(),
            args_hash: hash,
            iteration,
        });

        while self.window.len() > self.threshold {
            self.window.pop_front();
        }

        if self.window.len() >= self.threshold {
            let first = self.window.front()?;
            let all_same = self.window.iter().all(|r| r.args_hash == first.args_hash);
            if all_same {
                return Some(LoopAlert {
                    loop_detected: true,
                    tool_name: first.tool_name.clone(),
                    call_count: self.window.len(),
                    message: format!(
                        "Loop infinito de ferramenta detectado! A ferramenta '{}' foi chamada {} vezes consecutivas com os mesmos argumentos exatos.",
                        first.tool_name,
                        self.window.len()
                    ),
                });
            }
        }
        None
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_loop_detection() {
        let mut detector = LoopDetector::new(3, true);
        let alert1 = detector.record_and_evaluate("terminal", "{\"command\":\"ls -la\"}", 1);
        assert!(alert1.is_none());

        let alert2 = detector.record_and_evaluate("terminal", "{\"command\":\"ls -la\"}", 2);
        assert!(alert2.is_none());

        // Terceira chamada idêntica dispara o alerta de loop
        let alert3 = detector.record_and_evaluate("terminal", "{\"command\":\"ls -la\"}", 3);
        assert!(alert3.is_some());
        let alert = alert3.unwrap();
        assert!(alert.loop_detected);
        assert_eq!(alert.tool_name, "terminal");
        assert_eq!(alert.call_count, 3);
    }
}
