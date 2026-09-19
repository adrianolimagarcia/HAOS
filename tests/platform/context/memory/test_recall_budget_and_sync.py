"""O budget de recall é um cap real, e o sync converge o journal ao vault.

Três contratos, cada um preso à falha que o motivou (19/09/2026, appliance HAOS):

**O budget não é uma dica.** O bloco de memória é anexado à mensagem do usuário em todo turno,
então o que passar do budget é pago em toda requisição. Uma query devolveu 52.869 chars com
`budget_chars=5000` porque `retrieve` admite o melhor hit independente do tamanho (de propósito:
um registro grande e relevante não pode ficar invisível) e `format_context` não tinha cap algum.
O cap passou a ser do texto renderizado, com truncamento marcado.

**Documento não é memória.** Um espelho auto-gerado de 52k entrou no journal e vencia o rank de
qualquer query, empurrando as ADRs para fora do budget. O teto de ingestão fica no degrau que o
vault real mostra (ADRs até ~10k, documentos acima de 25k).

**Sync que não sincroniza é pior que nenhum.** O id do registro deriva da URI, então uma nota
editada era contada como "já existente" e o journal seguia servindo o texto velho em silêncio.
Agora a revisão ativa é superseded, e re-rodar não duplica.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hermes.platform.context.memory.canonical_store import CanonicalMemoryStore
from hermes.platform.context.memory.federated_fabric import FederatedMemoryCoordinator
from hermes.platform.context.memory.migration import MemoryMigrator
from hermes.platform.context.memory.retrieval import HybridMemoryRetriever

TERM = "kubernetes"


def _retriever(tmp_path: Path) -> HybridMemoryRetriever:
    return HybridMemoryRetriever(CanonicalMemoryStore(tmp_path / "fabric.db"))


def _fill(store: CanonicalMemoryStore, sizes: list[int]) -> None:
    for index, size in enumerate(sizes):
        store.append(content=("%s " % TERM) + ("x" * size), scope="project")


# --------------------------------------------------------------------------
# O budget é um cap, não uma dica
# --------------------------------------------------------------------------


@pytest.mark.parametrize("budget", [400, 500, 1000, 5000])
def test_the_rendered_context_never_exceeds_the_budget(tmp_path: Path, budget: int) -> None:
    retriever = _retriever(tmp_path)
    try:
        _fill(retriever.store, [60_000, 3000, 2500, 900, 800])
        rendered = retriever.format_context(TERM, ("project",), limit=8, budget_chars=budget)
        assert rendered, "com hits no journal o recall não pode voltar vazio"
        assert len(rendered) <= budget, "o bloco de memória é pago em todo turno"
    finally:
        retriever.store.close()


def test_a_huge_record_is_truncated_and_marked_not_silently_cut(tmp_path: Path) -> None:
    retriever = _retriever(tmp_path)
    try:
        _fill(retriever.store, [60_000])
        rendered = retriever.format_context(TERM, ("project",), limit=8, budget_chars=5000)
        assert "truncado" in rendered, "corte silencioso deixa o modelo ler um fragmento como se fosse tudo"
        assert len(rendered) <= 5000
    finally:
        retriever.store.close()


def test_one_large_record_cannot_starve_the_rest_of_the_recall(tmp_path: Path) -> None:
    """Um budget que devolve sempre um único documento não é um budget de recall."""
    retriever = _retriever(tmp_path)
    try:
        _fill(retriever.store, [60_000, 3000, 2500, 900, 800])
        rendered = retriever.format_context(TERM, ("project",), limit=8, budget_chars=5000)
        assert rendered.count("[memory:") >= 2, "os hits menores precisam sobreviver ao maior"
    finally:
        retriever.store.close()


@pytest.mark.parametrize("budget", [0, 1, 12, 50, 120])
def test_a_degenerate_budget_still_holds_the_cap(tmp_path: Path, budget: int) -> None:
    """O marcador de truncamento não pode custar mais que o budget que ele reporta."""
    retriever = _retriever(tmp_path)
    try:
        _fill(retriever.store, [60_000, 3000, 2500])
        rendered = retriever.format_context(TERM, ("project",), limit=8, budget_chars=budget)
        assert len(rendered) <= budget, "budget=%d devolveu %d chars" % (budget, len(rendered))
    finally:
        retriever.store.close()


def test_an_empty_journal_renders_nothing(tmp_path: Path) -> None:
    retriever = _retriever(tmp_path)
    try:
        assert retriever.format_context(TERM, ("project",), budget_chars=5000) == ""
    finally:
        retriever.store.close()


# --------------------------------------------------------------------------
# Teto de ingestão: documento não é memória
# --------------------------------------------------------------------------


def _vault(tmp_path: Path, *, oversized: bool = True) -> Path:
    vault = tmp_path / "vault"
    (vault / "adrs").mkdir(parents=True)
    (vault / "adrs" / "ADR-001.md").write_text("# ADR-001\n\nDecisão real.\n", encoding="utf-8")
    if oversized:
        (vault / "espelho.md").write_text("# Espelho\n\n" + ("y" * 30_000), encoding="utf-8")
    return vault


def test_an_oversized_document_is_left_in_the_vault(tmp_path: Path) -> None:
    coordinator = FederatedMemoryCoordinator(vault_path=tmp_path / "fabric-vault")
    try:
        migrator = MemoryMigrator(coordinator.canonical_store, coordinator.projection_runner)
        report = migrator.backfill_obsidian(_vault(tmp_path), rebuild=False)
        assert report.skipped_oversized == 1
        assert report.imported == 1, "a ADR pequena entra; o documento de 30k fica no vault"
        assert coordinator.canonical_store.operational_counters()["records_active"] == 1
    finally:
        coordinator.canonical_store.close()


def test_the_ceiling_is_adjustable_so_it_is_not_a_hardcoded_verdict(tmp_path: Path) -> None:
    coordinator = FederatedMemoryCoordinator(vault_path=tmp_path / "fabric-vault")
    try:
        migrator = MemoryMigrator(coordinator.canonical_store, coordinator.projection_runner)
        report = migrator.backfill_obsidian(
            _vault(tmp_path), rebuild=False, max_content_chars=100_000
        )
        assert report.skipped_oversized == 0
        assert report.imported == 2
    finally:
        coordinator.canonical_store.close()


# --------------------------------------------------------------------------
# Sync de verdade: a nota mudou, o journal segue
# --------------------------------------------------------------------------


def test_an_edited_note_supersedes_its_revision_instead_of_going_stale(tmp_path: Path) -> None:
    vault = _vault(tmp_path, oversized=False)
    note = vault / "adrs" / "ADR-001.md"
    coordinator = FederatedMemoryCoordinator(vault_path=tmp_path / "fabric-vault")
    try:
        store = coordinator.canonical_store
        migrator = MemoryMigrator(store, coordinator.projection_runner)

        assert migrator.backfill_obsidian(vault, rebuild=False).imported == 1

        note.write_text("# ADR-001\n\nDecisão CORRIGIDA depois.\n", encoding="utf-8")
        second = migrator.backfill_obsidian(vault, rebuild=False)
        assert second.updated == 1, "a nota mudou; pular seria servir texto velho em silêncio"
        assert second.imported == 0

        counters = store.operational_counters()
        assert counters["records_active"] == 1, "a revisão antiga sai do conjunto ativo"
        assert counters["records_superseded"] == 1

        # Re-rodar sem mudança não pode acumular revisões: a comparação é contra a revisão viva.
        third = migrator.backfill_obsidian(vault, rebuild=False)
        assert third.updated == 0
        assert third.skipped_existing == 1
        assert store.operational_counters()["records_active"] == 1
    finally:
        coordinator.canonical_store.close()


def test_the_recall_answers_with_the_corrected_text(tmp_path: Path) -> None:
    vault = _vault(tmp_path, oversized=False)
    note = vault / "adrs" / "ADR-001.md"
    coordinator = FederatedMemoryCoordinator(vault_path=tmp_path / "fabric-vault")
    try:
        store = coordinator.canonical_store
        migrator = MemoryMigrator(store, coordinator.projection_runner)
        migrator.backfill_obsidian(vault, rebuild=False)

        note.write_text("# ADR-001\n\nDecisão CORRIGIDA depois.\n", encoding="utf-8")
        migrator.backfill_obsidian(vault, rebuild=False)

        rendered = HybridMemoryRetriever(store).format_context("corrigida", ("project",))
        assert "CORRIGIDA" in rendered, "o recall lê registros ativos: a revisão nova é a que responde"
    finally:
        coordinator.canonical_store.close()


# --------------------------------------------------------------------------
# O adapter de Obsidian escreve no home canônico, não no CWD
# --------------------------------------------------------------------------


def test_the_obsidian_adapter_defaults_to_the_canonical_home() -> None:
    """A projeção escreve por este adapter; um default relativo criava um vault por CWD."""
    from hermes_constants import get_hermes_home
    from hermes.platform.context.memory.obsidian import ObsidianAdapter

    adapter = ObsidianAdapter()
    assert adapter.vault_path.is_absolute(), "um vault relativo segue o diretório do processo"
    assert adapter.vault_path == Path(get_hermes_home()) / "obsidian_vault"


def test_an_explicit_vault_still_wins() -> None:
    from hermes.platform.context.memory.obsidian import ObsidianAdapter

    assert ObsidianAdapter("/tmp/vault-explicito").vault_path == Path("/tmp/vault-explicito")


def test_a_projection_lands_in_the_vault_and_the_next_migration_skips_it(tmp_path: Path) -> None:
    """O ciclo vault<->projeção só é seguro porque a projeção escreve onde a migração lê.

    Antes da correção do adapter a projeção gravava num vault relativo ao CWD, então este
    guarda nunca disparava contra o vault real: não havia projeção lá para reconhecer.
    """
    vault = tmp_path / "vault"
    coordinator = FederatedMemoryCoordinator(vault_path=vault)
    try:
        store = coordinator.canonical_store
        store.append(content="decisao de arquitetura sobre kubernetes", scope="project", kind="decision")
        coordinator.projection_runner.drain(limit_per_projection=8)

        written = list(vault.rglob("*.md"))
        assert written, "a projeção obsidian precisa escrever no vault que a migração lê"

        migrator = MemoryMigrator(store, coordinator.projection_runner)
        report = migrator.backfill_obsidian(vault, rebuild=False)
        assert report.skipped_projected >= 1, "sem isso a execução seguinte importa a própria projeção"
        assert report.imported == 0
    finally:
        coordinator.canonical_store.close()


# --------------------------------------------------------------------------
# A declaração de cutover não pode ser lida como estado
# --------------------------------------------------------------------------


def test_a_declared_cutover_does_not_claim_there_is_something_to_serve(tmp_path: Path) -> None:
    """A config declara o cutover completo desde o primeiro boot; o journal é que diz se serve."""
    coordinator = FederatedMemoryCoordinator(vault_path=tmp_path / "fabric-vault")
    try:
        cutover = coordinator.fabric_metrics()["cutover"]
        assert cutover["declared_position"] > 0, "a configuração declara estágios ativos"
        assert len(cutover["declared_stages"]) == cutover["declared_position"]
        assert cutover["serving"] is False, "um journal vazio não serve nada, declarado ou não"
        assert "journal_empty" in cutover["blockers"]
    finally:
        coordinator.canonical_store.close()


def test_a_pending_outbox_is_evidence_the_fabric_is_not_serving_yet(tmp_path: Path) -> None:
    coordinator = FederatedMemoryCoordinator(vault_path=tmp_path / "fabric-vault")
    try:
        migrator = MemoryMigrator(coordinator.canonical_store, coordinator.projection_runner)
        # rebuild=False deixa o outbox com trabalho pendente, e isso é evidência sobre estado:
        # declarar o cutover completo não drena outbox nenhum.
        migrator.backfill_obsidian(_vault(tmp_path, oversized=False), rebuild=False)
        cutover = coordinator.fabric_metrics()["cutover"]
        assert cutover["outbox_pending"], "as projeções ainda não rodaram"
        assert "projections_pending" in cutover["blockers"]
        assert cutover["serving"] is False
        assert cutover["serving"] == (cutover["blockers"] == []), "serving é derivado dos blockers"
    finally:
        coordinator.canonical_store.close()
