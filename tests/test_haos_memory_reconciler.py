"""Tests for HAOS Memory Reconciler (Mem0-inspired Mutation & Conflict Resolution)."""

import tempfile
from pathlib import Path

from hermes.platform.memory.reconciler import MemoryReconciler
from hermes.platform.memory.hybrid_router import HybridKnowledgeRouter


def test_reconciler_add_noop_update_supersede():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test_memory.db"
        reconciler = MemoryReconciler(db_path=db_path)

        # 1. ADD: First statement about database
        r1 = reconciler.reconcile(
            topic="database",
            content="Utilizamos PostgreSQL para persistência relacional do projeto.",
            category="architecture",
        )
        assert r1.action == "ADD"
        assert r1.memory_id.startswith("mem_")

        active = reconciler.get_active_memories(topic="database")
        assert len(active) == 1
        assert active[0].content == "Utilizamos PostgreSQL para persistência relacional do projeto."
        assert active[0].status == "active"
        assert active[0].superseded_by is None

        # 2. NOOP: Same fact repeated
        r2 = reconciler.reconcile(
            topic="database",
            content="Utilizamos PostgreSQL para persistência relacional do projeto.",
        )
        assert r2.action == "NOOP"
        active_after_noop = reconciler.get_active_memories(topic="database")
        assert len(active_after_noop) == 1

        # 3. UPDATE: Refinement / enrichment of existing fact
        r3 = reconciler.reconcile(
            topic="database",
            content="Utilizamos PostgreSQL para persistência relacional do projeto com pooling pgbouncer.",
            category="architecture",
        )
        assert r3.action == "UPDATE"
        active_after_update = reconciler.get_active_memories(topic="database")
        assert len(active_after_update) == 1
        assert "pgbouncer" in active_after_update[0].content

        # 4. SUPERSEDE: Migration / contradiction
        r4 = reconciler.reconcile(
            topic="database",
            content="Não usamos mais PostgreSQL, migramos tudo para SQLite com WAL mode.",
            category="architecture",
        )
        assert r4.action == "SUPERSEDE"
        assert r4.target_memory_id == r1.memory_id or r4.target_memory_id == r3.memory_id
        assert r4.memory_id != r4.target_memory_id

        # Check that old memory is marked as superseded
        old_mem = reconciler.get_memory(r4.target_memory_id)
        assert old_mem is not None
        assert old_mem.status == "superseded"
        assert old_mem.superseded_by == r4.memory_id

        # Check that ONLY the new memory is active
        active_memories = reconciler.get_active_memories(topic="database")
        assert len(active_memories) == 1
        assert active_memories[0].id == r4.memory_id
        assert active_memories[0].status == "active"
        assert "SQLite com WAL mode" in active_memories[0].content


def test_hybrid_router_integration_with_reconciler():
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        okf_dir = root / "okf"
        db_path = root / "reconciled.db"
        router = HybridKnowledgeRouter(okf_dir=okf_dir, reconciler_db_path=db_path)

        # Reconcile memory through router
        router.reconcile_memory(
            topic="ui_framework",
            content="Adotamos TailwindCSS para todo o frontend.",
            category="tech_stack",
        )

        # Query should find it from RECONCILED_MEMORY
        res = router.query("ui_framework")
        assert res["found"] is True
        assert res["source"] == "RECONCILED_MEMORY"
        assert "TailwindCSS" in res["content"]

        # Now supersede it
        router.reconcile_memory(
            topic="ui_framework",
            content="Mudou para CSS puro com variáveis customizadas, Tailwind removido.",
            category="tech_stack",
        )

        res2 = router.query("ui_framework")
        assert res2["found"] is True
        assert "CSS puro com variáveis" in res2["content"]
        assert "TailwindCSS para todo o frontend" not in res2["content"]


def test_prompt_context_excludes_superseded():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test_prompt.db"
        reconciler = MemoryReconciler(db_path=db_path)

        reconciler.reconcile(
            topic="auth",
            content="Autenticação é feita via OAuth2 Google.",
        )
        reconciler.reconcile(
            topic="auth",
            content="Substituído por autenticação local via JWT e chaves Ed25519.",
        )

        prompt_str = reconciler.format_prompt_context()
        assert "JWT e chaves Ed25519" in prompt_str
        assert "OAuth2 Google" not in prompt_str
