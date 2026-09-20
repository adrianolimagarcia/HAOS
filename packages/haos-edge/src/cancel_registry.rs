//! Registro Central de Cancelamento Assíncrono (adaptado de rustfox).
//!
//! Permite cancelar qualquer tarefa longa, Sombra (Shadow Leaf) ou ferramenta em voo
//! através de um ID opaco via oneshot channel, sem bloqueios de thread ou subshell residual.

use std::collections::HashMap;
use std::sync::Arc;
use tokio::sync::oneshot;
use tokio::sync::Mutex;

#[derive(Clone)]
pub struct CancelRegistry {
    inner: Arc<Mutex<HashMap<String, oneshot::Sender<()>>>>,
}

impl CancelRegistry {
    pub fn new() -> Self {
        Self {
            inner: Arc::new(Mutex::new(HashMap::new())),
        }
    }

    pub async fn register(&self, id: String, tx: oneshot::Sender<()>) {
        let mut map = self.inner.lock().await;
        map.insert(id, tx);
    }

    pub async fn cancel(&self, id: &str) -> bool {
        let mut map = self.inner.lock().await;
        if let Some(tx) = map.remove(id) {
            let _ = tx.send(());
            true
        } else {
            false
        }
    }

    pub async fn unregister(&self, id: &str) {
        let mut map = self.inner.lock().await;
        map.remove(id);
    }

    pub async fn is_active(&self, id: &str) -> bool {
        let map = self.inner.lock().await;
        map.contains_key(id)
    }
}

impl Default for CancelRegistry {
    fn default() -> Self {
        Self::new()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn test_cancel_flow() {
        let reg = CancelRegistry::new();
        let (tx, mut rx) = oneshot::channel();
        reg.register("shadow_task_1".into(), tx).await;
        assert!(reg.is_active("shadow_task_1").await);

        let cancelled = reg.cancel("shadow_task_1").await;
        assert!(cancelled);
        assert!(!reg.is_active("shadow_task_1").await);
        assert!(rx.try_recv().is_ok());
    }
}
