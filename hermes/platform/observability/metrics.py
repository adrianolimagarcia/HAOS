from typing import Any, Dict, List, Optional

# Número máximo de amostras de latência retidas por operação. Janela
# fixa e bounded: alimenta o K7 (proposals derivadas de séries reais)
# sem crescimento ilimitado de memória em runtimes longos.
_MAX_LATENCY_SAMPLES_PER_OP = 256


class MetricsCollector:
    def __init__(self):
        self.counters: Dict[str, int] = {}
        self.latencies: Dict[str, float] = {}
        self._latency_history: Dict[str, List[float]] = {}
        self.costs_usd: float = 0.0

    def increment(self, metric: str, amount: int = 1):
        self.counters[metric] = self.counters.get(metric, 0) + amount

    def record_latency(self, operation: str, duration_sec: float):
        """Registra latência mantendo a última amostra e uma série temporal bounded.

        ``summary()["latencies"]`` preserva a semântica legada (último valor por
        operação); a série completa fica disponível via ``latency_series`` para
        deltas de custo/latência reais consumidos pelo OuroborosAnalyzer (K7).
        """
        self.latencies[operation] = duration_sec
        series = self._latency_history.setdefault(operation, [])
        series.append(duration_sec)
        if len(series) > _MAX_LATENCY_SAMPLES_PER_OP:
            del series[: len(series) - _MAX_LATENCY_SAMPLES_PER_OP]

    def latency_series(self, operation: str) -> List[float]:
        """Série temporal (ordem de chegada) das latências de ``operation``."""
        return list(self._latency_history.get(operation, []))

    def latency_stats(self, operation: str) -> Optional[Dict[str, float]]:
        """min/max/avg da série de ``operation`` (None quando sem amostras)."""
        series = self._latency_history.get(operation)
        if not series:
            return None
        return {
            "min": min(series),
            "max": max(series),
            "avg": sum(series) / len(series),
            "samples": float(len(series)),
        }

    def record_cost(self, usd: float):
        self.costs_usd += usd

    def summary(self) -> Dict[str, Any]:
        return {
            "counters": self.counters,
            "latencies": self.latencies,
            "total_cost_usd": self.costs_usd
        }
