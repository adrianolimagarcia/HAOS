"""Testes de regressão para contains_hypothesis, MemoryCandidate e integração com Dream / Memory Consolidation."""

import pytest
from hermes.platform.context.memory.candidate import (
    MemoryCandidate,
    contains_hypothesis,
    contains_question,
    contains_secrets,
    validate_candidate,
)


class TestContainsHypothesis:
    """Validação da detecção de hipóteses em português e inglês sem falsos positivos."""

    @pytest.mark.parametrize(
        "text",
        [
            # Português
            "Talvez o banco SQLite precise de WAL habilitado para evitar lock.",
            "Provavelmente a porta 8080 está em uso por outro serviço.",
            "Possivelmente há um vazamento de memória no loop de eventos.",
            "Esta é uma hipótese que precisamos validar com testes.",
            "A hipotese levantada pelo agente é plausível.",
            "Hipoteticamente falando, poderíamos particionar por dia.",
            "Suponho que a migração não tenha rodado no nó secundário.",
            "Supondo uma carga de 10k requisições por segundo, o pool esgota.",
            "Isso é apenas uma suposição sem base empírica.",
            "Não temos logs, é pura suposicao.",
            "Acho que devemos rodar o script com timeout de 30s.",
            "Pode ser que o arquivo esteja corrompido.",
            "Será que a conexão foi fechada pelo gateway?",
            "Não tenho certeza se o cache expira em 60 minutos.",
            "Nao tenho certeza se o serviço está ativo.",
            "Suspeita-se que o deadlock ocorre no checkpoint.",
            "Suspeito que a race condition foi introduzida no último commit.",
            "O analista suspeita que a taxa de erro subiu.",
            "Aparentemente o gateway rejeitou a conexão com HTTP 502.",
            "Especula-se sobre a causa raiz do incidente.",
            "Este comportamento é especulativo.",
            # Inglês
            "Maybe we should increase the timeout to 60 seconds.",
            "Perhaps the container runs out of disk space.",
            "Probably the network packet was dropped.",
            "Possibly the driver is incompatible with this kernel.",
            "This is just a hypothesis to test.",
            "Under a hypothetical scenario, the buffer overflows.",
            "Hypothetically, the replica could lag behind the primary.",
            "I suppose the daemon died unexpectedly.",
            "Supposing the server crashes, failover will trigger.",
            "I think that the issue is in the connection pool.",
            "I think the memory footprint grew too fast.",
            "I suspect that the token has expired.",
            "The engineer suspects that the deadlock is in the mutex.",
            "It is suspected that memory limits were exceeded.",
            "Presumably the background worker has finished.",
            "This conclusion is pure speculation without benchmarks.",
            "The explanation remains speculative.",
            "I am not sure if the index was rebuilt properly.",
            "The delay might be caused by slow disk I/O.",
            "It could be that the port is blocked by firewall.",
        ],
    )
    def test_detects_hypotheses_pt_en(self, text: str):
        assert contains_hypothesis(text) is True, f"Deveria detectar hipótese em: {text}"

    @pytest.mark.parametrize(
        "text",
        [
            "O comando pytest executou 8 testes com sucesso em 1.10s.",
            "A porta padrão do serviço é 8080 conforme documentado no README.",
            "O banco SQLite utiliza o modo WAL por padrão no HAOS.",
            "O arquivo candidate.py define a classe MemoryCandidate e métodos de validação.",
            "The database schema defines a unique index on session_id and memory_key.",
            "All unit tests passed with exit code 0.",
            "Python 3.11 is the runtime version used across the project.",
            "HTTP 404 indicates that the resource was not found on the server.",
            "A constante INSTINCT_PROJECT_SCOPE vale 'global'.",
            "A tabela memories possui colunas id, fact, content, created_at.",
            "The latency p95 is 12ms measured under 100 concurrent requests.",
        ],
    )
    def test_does_not_flag_objective_facts(self, text: str):
        assert contains_hypothesis(text) is False, f"Falso positivo detectado em fato objetivo: {text}"

    def test_empty_or_whitespace_text(self):
        assert contains_hypothesis("") is False
        assert contains_hypothesis("   \n\t  ") is False


class TestMemoryCandidateHypothesisPromotionGate:
    """Garante que candidatos com hipótese não sofram promoção automática nem validação direta."""

    def test_candidate_with_hypothesis_in_fact_rejected_by_is_eligible_for_auto_promotion(self):
        candidate = MemoryCandidate(
            fact="Talvez o gateway precise de buffer maior.",
            confidence=0.99,  # Alta confiança artificial
        )
        assert candidate.is_high_confidence() is True
        assert candidate.is_eligible_for_auto_promotion() is False

    def test_candidate_with_hypothesis_in_content_rejected_by_is_eligible_for_auto_promotion(self):
        candidate = MemoryCandidate(
            content="Perhaps the memory leak is in the worker thread.",
            confidence=0.95,
        )
        assert candidate.is_eligible_for_auto_promotion() is False

    def test_candidate_with_hypothesis_fails_validate_candidate(self):
        candidate = MemoryCandidate(
            fact="Acho que a porta 5432 está aberta no firewall.",
            confidence=0.90,
        )
        valid, reason = validate_candidate(candidate)
        assert valid is False
        assert "hipótese" in reason

    def test_objective_fact_high_confidence_is_eligible_for_auto_promotion(self):
        candidate = MemoryCandidate(
            fact="O serviço Redis roda na porta 6379 com autenticação obrigatória.",
            confidence=0.90,
        )
        assert candidate.is_high_confidence() is True
        assert candidate.is_eligible_for_auto_promotion() is True
        valid, reason = validate_candidate(candidate)
        assert valid is True
        assert reason == "ok"


class TestDreamIntegrationCleanImport:
    """Garante que o subsistema Dream importa e referencia contains_hypothesis corretamente."""

    def test_dream_clean_import(self):
        from hermes.platform.memory.dream import contains_hypothesis as dream_contains_hypothesis
        assert dream_contains_hypothesis is contains_hypothesis

    def test_dream_extraction_skips_hypothesis_preview(self):
        """Simula a guarda determinística usada no loop de extração do Dream."""
        raw_msg = "Talvez a sessão anterior tenha travado por timeout no socket."
        # No Dream: if contains_hypothesis(raw_source): continue
        assert contains_hypothesis(raw_msg) is True

        lesson_msg = "Maybe socket timeout caused previous session failure"
        assert contains_hypothesis(lesson_msg) is True
