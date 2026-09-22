//! HAOS STT Engine - Rust Native Inference Router & Forwarder
//!
//! Fornece endpoints padrão OpenAI (`/v1/audio/transcriptions`) diretamente dentro do `haos-edge`.
//! Faz proxy transparente de alta velocidade para o worker de aceleração CUDA/int8 (porta 8645)
//! ativado sob demanda via systemd socket activation, gerenciando timeouts, multipart streams e buffers.

use std::time::Instant;
use tracing::{info, error};

pub struct SttEngine;

#[derive(Debug, serde::Serialize, serde::Deserialize)]
pub struct TranscriptionResponse {
    pub text: String,
    pub duration: f64,
    pub elapsed_s: f64,
}

impl SttEngine {
    /// Encaminha a transcrição para o worker acelerado por GPU CUDA (socket activated)
    /// com buffer assíncrono e timeout estendido de 60 segundos.
    pub async fn transcribe_audio(
        audio_bytes: &[u8],
        filename: &str,
        model: &str,
        language: Option<&str>,
    ) -> Result<TranscriptionResponse, String> {
        let t0 = Instant::now();
        let token = std::env::var("HAOS_STT_TOKEN").unwrap_or_else(|_| "ef3c564ddd8896464499d5522726e1ca754b3f9ca3e70cb3".to_string());

        let client = reqwest::Client::builder()
            .timeout(std::time::Duration::from_secs(90))
            .build()
            .map_err(|e| format!("Falha ao construir cliente HTTP HTTP/STT: {}", e))?;

        let part = reqwest::multipart::Part::bytes(audio_bytes.to_vec())
            .file_name(filename.to_string())
            .mime_str("audio/ogg")
            .unwrap_or_else(|_| reqwest::multipart::Part::bytes(audio_bytes.to_vec()));

        let mut form = reqwest::multipart::Form::new()
            .part("file", part)
            .text("model", model.to_string())
            .text("response_format", "json");

        if let Some(lang) = language {
            form = form.text("language", lang.to_string());
        }

        let stt_worker_url = "http://100.77.31.78:8645/v1/audio/transcriptions";
        let resp = client
            .post(stt_worker_url)
            .header("Authorization", format!("Bearer {}", token))
            .multipart(form)
            .send()
            .await
            .map_err(|e| format!("Falha ao comunicar com worker STT (100.77.31.78:8645): {}", e))?;

        if !resp.status().is_success() {
            let status = resp.status();
            let err_text = resp.text().await.unwrap_or_default();
            return Err(format!("Worker STT retornou status {}: {}", status, err_text));
        }

        let json_body = resp
            .json::<TranscriptionResponse>()
            .await
            .map_err(|e| format!("Falha ao deserializar resposta JSON do STT: {}", e))?;

        let elapsed = t0.elapsed().as_secs_f64();
        info!(
            "STT Rust Edge: concluído em {:.2}s. Modelo: {}, Texto: '{}'",
            elapsed, model, json_body.text
        );

        Ok(TranscriptionResponse {
            text: json_body.text,
            duration: json_body.duration,
            elapsed_s: (elapsed * 100.0).round() / 100.0,
        })
    }
}
