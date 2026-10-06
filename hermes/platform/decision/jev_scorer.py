"""Motor neural de pontuação de opções em uma única passada (Jev-like System-1 Scorer).

Inspirado na arquitetura Jev da TypeSafe (vinnylarouge/jevlike), implementa um
mecanismo de atenção cruzada (cross-attention) de uma única passada entre o contexto
e uma lista dinâmica de N opções textuais.

Executa inferência com latência sub-milissegundo em CPU via NumPy nativo,
sem requisições de rede ou dependência de pacotes pesados de GPU no core do HAOS.
Suporta treinamento analítico em lote (offline / batch training com Cross-Entropy).
"""

from __future__ import annotations

import logging
import math
from pathlib import Path
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

import numpy as np

from hermes_constants import get_hermes_home
from hermes.platform.decision.spec import DecisionChoice

logger = logging.getLogger(__name__)


def _softmax(x: np.ndarray, axis: int = -1) -> np.ndarray:
    """Softmax numericamente estável."""
    shifted = x - np.max(x, axis=axis, keepdims=True)
    exp = np.exp(shifted)
    denom = np.sum(exp, axis=axis, keepdims=True)
    return exp / np.maximum(denom, 1e-12)


def _layer_norm(x: np.ndarray, eps: float = 1e-5) -> np.ndarray:
    """Layer normalization simples sobre a última dimensão."""
    mean = np.mean(x, axis=-1, keepdims=True)
    variance = np.var(x, axis=-1, keepdims=True)
    return (x - mean) / np.sqrt(variance + eps)


def default_weights_path() -> Path:
    """Caminho padrão de persistência dos pesos treinados do Jev."""
    return get_hermes_home() / "models" / "jev_weights.npz"


class JevOptionScorer:
    """Scorer de opções estilo Jevlike implementado em NumPy com suporte a treino em batch.

    Dada uma string de contexto e uma lista dinâmica de N opções:
    1. Codifica contexto e opções em representações vetoriais de dimensão W.
    2. Aplica LayerNorm em ambos.
    3. Cada opção atua como Query sobre a sequência de Contexto (Key/Value).
    4. Computa atenção cruzada: Attended = Softmax(Q @ K.T / sqrt(R)) @ V.
    5. Gera o logit da opção pelo produto escalar do Query com o Contexto atendido.
    6. Normaliza os logits das N opções com Softmax, gerando probabilidades.
    """

    def __init__(
        self,
        width: int = 64,
        rank: int = 32,
        max_context_bytes: int = 512,
        seed: int = 42,
        weights_path: Optional[Union[str, Path]] = None,
    ) -> None:
        self.width = width
        self.rank = rank
        self.max_context_bytes = max_context_bytes
        self.seed = seed

        # Inicializa matrizes de projeção ortogonal/estável
        rng = np.random.default_rng(seed)
        scale = 1.0 / math.sqrt(self.width)

        # Matriz de embeddings de bytes (256 bytes possíveis + 1 padding/unknown)
        self.byte_embeddings = (rng.standard_normal((257, self.width)) * scale).astype(np.float32)

        # Pesos de projeção Linear para Query, Key e Value
        rank_scale = 1.0 / math.sqrt(self.rank)
        self.w_query = (rng.standard_normal((self.width, self.rank)) * rank_scale).astype(np.float32)
        self.w_key = (rng.standard_normal((self.width, self.rank)) * rank_scale).astype(np.float32)
        self.w_value = (rng.standard_normal((self.width, self.rank)) * rank_scale).astype(np.float32)

        # Carrega pesos salvos se disponíveis
        target_weights = Path(weights_path) if weights_path else default_weights_path()
        if target_weights.exists():
            try:
                self._load_from_file(target_weights)
                logger.info("JevOptionScorer: pesos carregados de %s", target_weights)
            except Exception as exc:
                logger.warning("JevOptionScorer: falha ao carregar pesos de %s: %s", target_weights, exc)

    def _load_from_file(self, path: Path) -> None:
        """Carrega matrizes diretamente de um arquivo .npz."""
        data = np.load(path)
        self.width = int(data["width"][0])
        self.rank = int(data["rank"][0])
        self.max_context_bytes = int(data["max_context_bytes"][0])
        self.byte_embeddings = data["byte_embeddings"]
        self.w_query = data["w_query"]
        self.w_key = data["w_key"]
        self.w_value = data["w_value"]

    def _encode_bytes(self, text: str, max_len: Optional[int] = None) -> np.ndarray:
        """Codifica texto UTF-8 em sequência de vetores de embedding."""
        raw_bytes = text.encode("utf-8", errors="replace")
        if max_len is not None and len(raw_bytes) > max_len:
            raw_bytes = raw_bytes[:max_len]

        if len(raw_bytes) == 0:
            return np.zeros((1, self.width), dtype=np.float32)

        indices = np.frombuffer(raw_bytes, dtype=np.uint8) + 1  # 0 é padding
        return self.byte_embeddings[indices]

    def _encode_option(self, option_text: str) -> np.ndarray:
        """Gera o vetor unificado de uma opção via média dos embeddings de seus bytes."""
        token_vecs = self._encode_bytes(option_text, max_len=64)
        return np.mean(token_vecs, axis=0)  # Vetor (W,)

    def score_options(
        self,
        context: str,
        options: Sequence[str],
    ) -> List[float]:
        """Calcula probabilidades normalizadas para cada opção na lista em uma passada."""
        if not options:
            return []
        if len(options) == 1:
            return [1.0]

        # 1. Codificação do contexto: matriz (L, W)
        ctx_vecs = self._encode_bytes(context, max_len=self.max_context_bytes)

        # 2. Codificação das N opções: matriz (N, W)
        opt_vecs = np.stack([self._encode_option(opt) for opt in options], axis=0)

        # 3. LayerNorm
        ctx_norm = _layer_norm(ctx_vecs)
        opt_norm = _layer_norm(opt_vecs)

        # 4. Projeções Lineares
        queries = opt_norm @ self.w_query   # (N, R)
        keys = ctx_norm @ self.w_key         # (L, R)
        values = ctx_norm @ self.w_value     # (L, R)

        # 5. Atenção Cruzada: Scores = Queries @ Keys.T / sqrt(R) -> (N, L)
        inv_sqrt_rank = 1.0 / math.sqrt(self.rank)
        raw_attn = (queries @ keys.T) * inv_sqrt_rank
        attn_weights = _softmax(raw_attn, axis=-1)  # (N, L)

        # 6. Agregação ponderada do Contexto
        attended = attn_weights @ values  # (N, R)

        # 7. Logits: produto escalar entre Query e Attended -> (N,)
        logits = np.sum(queries * attended, axis=-1) * inv_sqrt_rank

        # 8. Softmax final sobre as N opções
        probs = _softmax(logits, axis=-1)
        return [float(p) for p in probs]

    def decide(
        self,
        context: str,
        options: Sequence[str],
        domain: str = "general",
    ) -> DecisionChoice:
        """Gera uma escolha estruturada DecisionChoice compatível com o ecossistema HAOS."""
        t0 = time.perf_counter()
        if not options:
            raise ValueError("A lista de opções não pode estar vazia.")

        probs = self.score_options(context, options)
        prob_dict = {opt: round(p, 4) for opt, p in zip(options, probs)}
        best_idx = int(np.argmax(probs))
        selected = options[best_idx]
        confidence = probs[best_idx]
        latency = (time.perf_counter() - t0) * 1000.0

        return DecisionChoice(
            selected=selected,
            confidence=round(confidence, 4),
            probabilities=prob_dict,
            reason=f"Jev-like one-pass attention scorer (rank={self.rank}, width={self.width})",
            learned=True,
            latency_ms=round(latency, 2),
        )

    def train_step(
        self,
        context: str,
        options: Sequence[str],
        target_index: int,
        lr: float = 0.01,
    ) -> float:
        """Executa um passo de otimização analítica com perda de Cross-Entropy.

        Computa o gradiente exato da perda em relação a W_query, W_key e W_value
        via backpropagation na cross-attention e atualiza as matrizes.
        """
        if not options or target_index < 0 or target_index >= len(options):
            return 0.0
        if len(options) == 1:
            return 0.0

        ctx_vecs = self._encode_bytes(context, max_len=self.max_context_bytes)
        opt_vecs = np.stack([self._encode_option(opt) for opt in options], axis=0)

        ctx_norm = _layer_norm(ctx_vecs)
        opt_norm = _layer_norm(opt_vecs)

        queries = opt_norm @ self.w_query  # (N, R)
        keys = ctx_norm @ self.w_key        # (L, R)
        values = ctx_norm @ self.w_value    # (L, R)

        inv_sqrt_rank = 1.0 / math.sqrt(self.rank)
        raw_attn = (queries @ keys.T) * inv_sqrt_rank
        attn_weights = _softmax(raw_attn, axis=-1)  # (N, L)
        attended = attn_weights @ values             # (N, R)

        logits = np.sum(queries * attended, axis=-1) * inv_sqrt_rank
        probs = _softmax(logits, axis=-1)
        loss = float(-np.log(probs[target_index] + 1e-12))

        # Backward pass analítico
        d_logits = probs.copy()
        d_logits[target_index] -= 1.0  # (N,)

        d_attended = (d_logits[:, None] * queries) * inv_sqrt_rank
        d_queries = (d_logits[:, None] * attended) * inv_sqrt_rank

        d_values = attn_weights.T @ d_attended
        d_attn = d_attended @ values.T

        sum_s_ds = np.sum(attn_weights * d_attn, axis=-1, keepdims=True)
        d_raw_attn = attn_weights * (d_attn - sum_s_ds)

        d_queries += (d_raw_attn @ keys) * inv_sqrt_rank
        d_keys = (d_raw_attn.T @ queries) * inv_sqrt_rank

        dw_q = opt_norm.T @ d_queries
        dw_k = ctx_norm.T @ d_keys
        dw_v = ctx_norm.T @ d_values

        # Clipping de gradiente para estabilidade numérica
        np.clip(dw_q, -1.0, 1.0, out=dw_q)
        np.clip(dw_k, -1.0, 1.0, out=dw_k)
        np.clip(dw_v, -1.0, 1.0, out=dw_v)

        # Atualização de gradiente descendente
        self.w_query -= lr * dw_q
        self.w_key -= lr * dw_k
        self.w_value -= lr * dw_v

        return loss

    def train_batch(
        self,
        samples: Sequence[Tuple[str, Sequence[str], int]],
        epochs: int = 5,
        lr: float = 0.01,
    ) -> float:
        """Treina os pesos em lote sobre tuplas (contexto, opcoes, target_index)."""
        if not samples:
            return 0.0

        avg_loss = 0.0
        for _ in range(epochs):
            total_loss = 0.0
            valid_count = 0
            for ctx, opts, target_idx in samples:
                if not opts or target_idx < 0 or target_idx >= len(opts):
                    continue
                loss = self.train_step(ctx, opts, target_idx, lr=lr)
                total_loss += loss
                valid_count += 1
            if valid_count > 0:
                avg_loss = total_loss / valid_count

        return avg_loss

    def save_weights(self, path: Optional[Union[str, Path]] = None) -> Path:
        """Salva os parâmetros do modelo em formato .npz leve."""
        target_path = Path(path) if path else default_weights_path()
        target_path.parent.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(
            target_path,
            byte_embeddings=self.byte_embeddings,
            w_query=self.w_query,
            w_key=self.w_key,
            w_value=self.w_value,
            width=np.array([self.width], dtype=np.int32),
            rank=np.array([self.rank], dtype=np.int32),
            max_context_bytes=np.array([self.max_context_bytes], dtype=np.int32),
        )
        return target_path

    @classmethod
    def load_weights(cls, path: Optional[Union[str, Path]] = None) -> JevOptionScorer:
        """Carrega parâmetros treinados a partir de arquivo .npz."""
        target_path = Path(path) if path else default_weights_path()
        scorer = cls(weights_path=target_path)
        return scorer
