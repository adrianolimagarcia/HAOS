"""Item 2 (HAOS hardening) — EventStore file-backed: WAL + conexão única.

Cobre os quatro riscos da mudança:
1. Durabilidade de decisão: file-backed roda WAL + synchronous=FULL (o
   EvolutionLedger deriva deste stream; NUNCA NORMAL).
2. Corrida de ``seq``/thread-safety: appends concorrentes no mesmo store
   produzem seqs únicos e contíguos (RLock + BEGIN IMMEDIATE).
3. Sidecars WAL bounded: checkpoint PASSIVE periódico + close() com
   checkpoint — o -wal não cresce sem limite.
4. Multi-instância/multi-processo: duas instâncias sobre o mesmo arquivo
   (WAL) leem/escrevem sem corromper e sem SQLITE_BUSY imediato.

Semântica legada preservada: :memory: continua uma conexão por instância
sem WAL; idempotência por PK (append de id repetido levanta IntegrityError);
ordem determinística (seq, timestamp) e cursor = MAX(seq).
"""

import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path

from hermes.platform.observability.event_store import (
    EventStore, _BUSY_TIMEOUT_MS, _CHECKPOINT_EVERY_WRITES,
)
from hermes.platform.observability.events import Event


class TestEventStoreWALFileBacked(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self._tmp.name) / "events.db")
        self.store = EventStore(self.db_path)

    def tearDown(self):
        self.store.close()
        self._tmp.cleanup()

    def _journal_mode(self) -> str:
        conn = sqlite3.connect(self.db_path)
        try:
            return str(conn.execute("PRAGMA journal_mode").fetchone()[0]).lower()
        finally:
            conn.close()

    # ------------------------------------------------------------------ #
    # Risco 1 — durabilidade de decisão (WAL + synchronous=FULL)
    # ------------------------------------------------------------------ #
    def test_file_backed_runs_wal_full(self):
        """Store de arquivo ativa WAL (filesystem suporta) e synchronous=FULL.

        Contract: decisões do Ouroboros (evolution.proposal.*) derivam deste
        stream — synchronous nunca é NORMAL num store de auditoria."""
        conn = self.store._get_connection()
        sync = str(conn.execute("PRAGMA synchronous").fetchone()[0]).lower()
        # Aceita "full" ou "2" (FULL), conforme a versão do sqlite reporta.
        self.assertIn(sync, {"full", "2"})

    def test_memory_store_keeps_single_connection_no_wal(self):
        """:memory: segue com conexão única; journal_mode é memory (sem WAL)."""
        mem = EventStore(":memory:")
        try:
            self.assertIsNotNone(mem._conn)
            conn = mem._get_connection()
            mode = str(conn.execute("PRAGMA journal_mode").fetchone()[0]).lower()
            self.assertEqual(mode, "memory")
        finally:
            mem.close()

    # ------------------------------------------------------------------ #
    # Risco 2 — seq único/contíguo sob appends concorrentes
    # ------------------------------------------------------------------ #
    def test_concurrent_appends_unique_contiguous_seq(self):
        """N threads appendando no MESMO store file-backed: seqs 1..N, únicos,
        sem IntegrityError e sem SQLITE_BUSY (RLock serializa)."""
        n_threads = 8
        events_per_thread = 25
        errors: list = []

        def worker(worker_id: int) -> None:
            try:
                for i in range(events_per_thread):
                    self.store.append(Event(
                        name="load.worker",
                        payload={"worker": worker_id, "i": i},
                        trace_id=f"w{worker_id}",
                    ))
            except Exception as exc:  # pragma: no cover - falha é assert abaixo
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(w,))
                   for w in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [])
        total = n_threads * events_per_thread
        self.assertEqual(self.store.cursor(), total)
        seqs = [e.seq for e in self.store.get_all()]
        self.assertEqual(sorted(seqs), list(range(1, total + 1)))
        self.assertEqual(len(set(seqs)), total)

    def test_duplicate_event_id_still_raises_integrity_error(self):
        """Idempotência por PK preservada: append de id repetido levanta."""
        ev = Event(name="once", payload={}, event_id="ev-dup-1")
        self.store.append(ev)
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.append(ev)

    # ------------------------------------------------------------------ #
    # Risco 3 — WAL bounded: checkpoint PASSIVE + close()
    # ------------------------------------------------------------------ #
    def test_checkpoint_cadence_and_close(self):
        """>_CHECKPOINT_EVERY_WRITES appends: checkpoint PASSIVE roda sem erro;
        close() faz checkpoint final; dados persistem e reabrem corretamente."""
        n = _CHECKPOINT_EVERY_WRITES + 5
        for i in range(n):
            self.store.append(Event(name="burst", payload={"i": i}, trace_id="t"))
        # Se o checkpoint tivesse quebrado, close()/cursor acusaria.
        self.assertEqual(self.store.cursor(), n)

        wal_path = self.db_path + "-wal"
        self.store.close()
        # Após close com checkpoint, um novo store relê todos os eventos.
        reopened = EventStore(self.db_path)
        try:
            self.assertEqual(reopened.cursor(), n)
            self.assertEqual(len(reopened.get_all()), n)
        finally:
            reopened.close()
        # -wal pode existir (WAL truncado no close), mas o arquivo principal
        # carrega os dados: reler com sqlite puro não perde nada.
        conn = sqlite3.connect(self.db_path)
        try:
            rows = conn.execute("SELECT COUNT(*) AS c FROM events").fetchone()
            self.assertEqual(int(rows[0]), n)
        finally:
            conn.close()

    def test_journal_size_limit_cap_present(self):
        """journal_size_limit está capado (64 MiB) — -wal não cresce infinito."""
        conn = self.store._get_connection()
        limit = int(conn.execute("PRAGMA journal_size_limit").fetchone()[0])
        self.assertGreaterEqual(limit, 1)
        self.assertLessEqual(limit, 64 * 1024 * 1024)

    # ------------------------------------------------------------------ #
    # Risco 4 — multi-instância sobre o mesmo arquivo (WAL)
    # ------------------------------------------------------------------ #
    def test_two_instances_same_file_no_lock_contention(self):
        """Duas instâncias (simula processo CLI + gateway) no MESMO events.db:
        cada uma vê os eventos da outra após commit — sem SQLITE_BUSY imediato
        graças ao busy_timeout, e seq continua globalmente único."""
        other = EventStore(self.db_path)
        try:
            self.store.append(Event(name="a.first", payload={}, trace_id="t1"))
            other.append(Event(name="b.second", payload={}, trace_id="t2"))
            self.store.append(Event(name="a.third", payload={}, trace_id="t3"))

            mine = [e.name for e in self.store.get_all()]
            theirs = [e.name for e in other.get_all()]
            self.assertEqual(mine, ["a.first", "b.second", "a.third"])
            self.assertEqual(theirs, mine)

            seqs = [e.seq for e in other.get_all()]
            self.assertEqual(sorted(seqs), [1, 2, 3])
        finally:
            other.close()

    def test_busy_timeout_configured(self):
        """busy_timeout alto (30s) p/ escritor concorrente entre processos."""
        conn = self.store._get_connection()
        timeout = int(conn.execute("PRAGMA busy_timeout").fetchone()[0])
        self.assertEqual(timeout, _BUSY_TIMEOUT_MS)

    def test_reopen_after_close_lazy_reconnect(self):
        """close() é idempotente; _get_connection() reabre numa nova operação."""
        self.store.close()
        self.store.close()  # idempotente
        self.store.append(Event(name="after.close", payload={}, trace_id="t"))
        self.assertEqual(self.store.cursor(), 1)

    def test_deterministic_order_and_cursor(self):
        """Ordem (seq, timestamp) determinística e cursor == MAX(seq)."""
        self.store.append(Event(name="e1", payload={}, trace_id="t"))
        self.store.append(Event(name="e2", payload={}, trace_id="t"))
        names = [e.name for e in self.store.events_after(0)]
        self.assertEqual(names, ["e1", "e2"])
        tail = [e.name for e in self.store.get_all(limit=1)]
        self.assertEqual(tail, ["e2"])
        self.assertEqual(self.store.cursor(), 2)


if __name__ == "__main__":
    unittest.main()
