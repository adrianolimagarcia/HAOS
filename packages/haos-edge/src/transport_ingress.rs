//! Ingress de transporte de mensageria nativo em Rust (Telegram / A2A / Webhook).
//!
//! Executa polling contínuo ou escuta webhooks sem sobrecarregar o runtime Python,
//! entregando eventos normalizados para o EventHub em sub-milissegundos.

use crate::event_hub::{EventHub, PlatformEvent};
use reqwest::Client;
use serde::{Deserialize, Serialize};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::time::Duration;
use tracing::{error, info, warn};

#[derive(Clone)]
pub struct TransportIngress {
    event_hub: EventHub,
    client: Client,
    running: Arc<AtomicBool>,
}

#[derive(Serialize, Deserialize, Debug, Clone)]
pub struct InboundMessagePayload {
    pub platform: String,
    pub chat_id: String,
    pub user_id: String,
    pub username: Option<String>,
    pub text: String,
    pub message_id: Option<String>,
    pub timestamp: f64,
}

impl TransportIngress {
    pub fn new(event_hub: EventHub) -> Self {
        Self {
            event_hub,
            client: Client::builder()
                .timeout(Duration::from_secs(35))
                .build()
                .unwrap_or_default(),
            running: Arc::new(AtomicBool::new(false)),
        }
    }

    /// Publica uma mensagem recebida de qualquer adaptador como evento normalizado no hub
    pub fn ingest_inbound_message(&self, payload: InboundMessagePayload) -> Result<(), String> {
        let event = PlatformEvent {
            event_id: Some(format!(
                "msg_{}_{}",
                payload.platform,
                payload.message_id.clone().unwrap_or_default()
            )),
            name: format!("messaging.{}.inbound", payload.platform),
            payload: serde_json::to_value(&payload).map_err(|e| e.to_string())?,
            trace_id: None,
            correlation_id: payload.message_id.clone(),
            causation_id: None,
            trust_level: Some("transport_ingress".to_string()),
            schema_version: Some(1),
            timestamp: Some(payload.timestamp),
            seq: None,
        };

        self.event_hub
            .publish(event)
            .map_err(|e| format!("Failed to publish inbound message: {e}"))?;
        Ok(())
    }

    /// Inicia worker em segundo plano para polling do Telegram se o token for configurado
    pub fn spawn_telegram_poller(&self, bot_token: String) {
        if self.running.swap(true, Ordering::SeqCst) {
            warn!("Telegram poller already running");
            return;
        }

        let hub = self.event_hub.clone();
        let client = self.client.clone();
        let running = self.running.clone();

        tokio::spawn(async move {
            info!("Native Rust Telegram Poller started");
            let mut offset = 0i64;

            while running.load(Ordering::Relaxed) {
                let url = format!(
                    "https://api.telegram.org/bot{}/getUpdates?offset={}&timeout=30",
                    bot_token, offset
                );

                match client.get(&url).send().await {
                    Ok(resp) => {
                        if let Ok(json) = resp.json::<serde_json::Value>().await {
                            if let Some(updates) = json.get("result").and_then(|r| r.as_array()) {
                                for upd in updates {
                                    if let Some(upd_id) =
                                        upd.get("update_id").and_then(|u| u.as_i64())
                                    {
                                        offset = upd_id + 1;
                                    }

                                    if let Some(msg) = upd.get("message") {
                                        let text = msg
                                            .get("text")
                                            .and_then(|t| t.as_str())
                                            .unwrap_or("")
                                            .to_string();
                                        let chat_id = msg
                                            .get("chat")
                                            .and_then(|c| c.get("id"))
                                            .map(|id| id.to_string())
                                            .unwrap_or_default();
                                        let user_id = msg
                                            .get("from")
                                            .and_then(|f| f.get("id"))
                                            .map(|id| id.to_string())
                                            .unwrap_or_default();
                                        let username = msg
                                            .get("from")
                                            .and_then(|f| f.get("username"))
                                            .and_then(|u| u.as_str())
                                            .map(|s| s.to_string());
                                        let msg_id = msg.get("message_id").map(|m| m.to_string());

                                        let now = std::time::SystemTime::now()
                                            .duration_since(std::time::UNIX_EPOCH)
                                            .unwrap_or_default()
                                            .as_secs_f64();

                                        let payload = InboundMessagePayload {
                                            platform: "telegram".to_string(),
                                            chat_id,
                                            user_id,
                                            username,
                                            text,
                                            message_id: msg_id,
                                            timestamp: now,
                                        };

                                        let event = PlatformEvent {
                                            event_id: Some(format!("tg_upd_{}", offset - 1)),
                                            name: "messaging.telegram.inbound".to_string(),
                                            payload: serde_json::to_value(&payload)
                                                .unwrap_or_default(),
                                            trace_id: None,
                                            correlation_id: payload.message_id.clone(),
                                            causation_id: None,
                                            trust_level: Some("transport_ingress".to_string()),
                                            schema_version: Some(1),
                                            timestamp: Some(now),
                                            seq: None,
                                        };

                                        let _ = hub.publish(event);
                                    }
                                }
                            }
                        }
                    }
                    Err(err) => {
                        error!("Telegram polling error: {err}");
                        tokio::time::sleep(Duration::from_secs(3)).await;
                    }
                }
            }
            info!("Native Rust Telegram Poller stopped");
        });
    }
}
