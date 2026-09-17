"""Contrato do motor de benchmark: o relatório é bem formado e o trabalho aconteceu.

Os LIMITES deste arquivo são detectores de travamento e de encanamento, NÃO portões
de performance. Motivo medido: `scripts/run_tests.sh` roda 16 arquivos em paralelo,
então nenhum teste vê máquina ociosa — uma asserção de relógio absoluto aqui mede a
carga do runner, não o código. Este arquivo já quebrou exatamente assim: o limite de
2.0s tinha 13.6x de folga ocioso (0.127-0.147s) e estourou sob a suíte completa.
Os NÚMEROS do relatório são para humanos lerem; um benchmark medido sob contenção de
16 vias não é uma medida de performance.
"""

import unittest
from hermes.platform.benchmarks.benchmark_engine import HAOSBenchmarkSuite


class TestHAOSBenchmarkSuite(unittest.TestCase):
    def test_benchmark_suite_execution(self):
        suite = HAOSBenchmarkSuite(iterations=50)
        report = suite.run_all()

        self.assertIn("sqlite_wal", report)
        self.assertIn("step_lifecycle_guard", report)
        self.assertGreater(report["total_duration_seconds"], 0.0)
        # Detecta travamento, não regressão: medido 0.127-0.147s ocioso e >2.0s sob
        # a suíte completa, então qualquer limite na casa dos segundos mede o runner.
        # Se travar de verdade, estoura isto com folga.
        self.assertLess(report["total_duration_seconds"], 30.0)

        sqlite = report["sqlite_wal"]
        self.assertIn("p50_ms", sqlite)
        self.assertIn("p95_ms", sqlite)
        # Prova que o WAL realmente escreveu (encanamento vivo), não que é rápido:
        # medido 416 ops/s ocioso e 30 ops/s sob a suíte completa.
        self.assertGreater(sqlite["ops_per_sec"], 2.0)

        guard = report["step_lifecycle_guard"]
        self.assertIn("p50_ms", guard)
        # Este é o único limite apertado que sobrevive, e por margem medida: o guard
        # custa 0.0014ms, ou seja 357x abaixo de 0.5ms. Sub-milissegundo é o contrato
        # de verdade do guard; não é um snapshot de performance.
        self.assertLess(guard["p50_ms"], 0.5)


if __name__ == "__main__":
    unittest.main()
