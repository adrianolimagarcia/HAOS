"""MemoryStagingStore — degrau de staging persistente para MemoryCandidate (P11).

O modelo de dados ja previa ``MemoryCandidate.status == "pending"`` (candidate.py),
mas nenhum componente persistia esse estado: ``MemoryRouter.process_candidate``
marcava o candidato de baixa confianca como ``rejected`` e o descartava. Sem um
degrau de staging, a unica alternativa era escrever direto na arvore canonica
(okf/) ou perder o candidato.

Este store e a persistencia que faltava: um JSON por home, FORA da arvore
canonica (<home>/memory/staging/pending_candidates.json), com proveniencia
(ids de sessao), confianca e destino proposto. A decisao de promocao vive fora
dele (InstinctStore conta a recorrencia entre sessoes e o MemoryRouter aplica o
limiar de confianca); aqui so mora o estado observavel do candidato.

Regras:
- ``stage_candidate`` acumula proveniencia entre sessoes (mesmo fato visto em
  mais de uma sessao soma sids) — e esse acumulo que alimenta o reforco.
- ``mark_promoted`` registra o desfecho (caminho do documento canonico), mantendo
  o registro para auditoria em vez de apagar.
- Construir o store nao escreve nada (mkdir e lazy, em ``_save``): o dry-run do
  dream pode instanciar e ler sem tocar no disco.
- Escrita em delta (P12): cada mutacao ADICIONA uma linha ao jornal aditivo
  ``pending_candidates.delta.jsonl`` ({seq, op, key, record, ts}); o SNAPSHOT
  continua autoritativo em ``load()`` (P5 assina pending_candidates.json e o
  teste de governanca grava no snapshot direto) e o jornal e o degrau de
  RECUPERACAO (``recover_from_delta``) quando o snapshot some/corrompe. O
  jornal compacta (trunca) ao atingir 128 linhas e nao entra em
  canonical_files()/validate_json_index (superficie de contrato do P5).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from hermes.platform.context.memory.candidate import MemoryCandidate

logger = logging.getLogger(__name__)

PENDING = "pending"
PROMOTED = "promoted"
# P5 — TTL: candidato demovido por validade/obsolescência (nunca apagado).
EXPIRED = "expired"
STAGING_FILENAME = "pending_candidates.json"
# P12 — jornal aditivo (escrita em delta): cada mutação ADICIONA uma linha em
# vez de reescrever histórico. Não entra em canonical_files()/validate_json_index
# (superfície de contrato do P5 inalterada): é o degrau de RECUPERAÇÃO.
DELTA_FILENAME = "pending_candidates.delta.jsonl"
# Compactação: ao atingir este limiar de linhas o jornal é truncado (o snapshot
# recém-gravado já contém tudo; o jornal recomeça em seq 1).
DELTA_COMPACT_THRESHOLD = 128


def candidate_key(fact: str) -> str:
    """Chave estavel e deterministica de um fato.

    Reforco so e reforco se o MESMO fato volta: a chave e o fato normalizado
    (strip + lower), nunca o id do candidato (uuid por instancia).
    """
    return hashlib.sha256(fact.strip().lower().encode("utf-8")).hexdigest()[:12]


class MemoryStagingStore:
    """Persistencia dos candidatos de memoria ainda nao promovidos (status pending)."""

    def __init__(self, root_dir: Optional[Path] = None):
        from hermes_constants import get_hermes_home  # function-level: lint A6

        self.root = Path(root_dir) if root_dir is not None else Path(get_hermes_home()) / "memory" / "staging"
        self._lock = threading.RLock()

    @property
    def index_file(self) -> Path:
        return self.root / STAGING_FILENAME

    @property
    def delta_file(self) -> Path:
        """Jornal aditivo append-only (P12): ``pending_candidates.delta.jsonl``."""
        return self.root / DELTA_FILENAME

    def load(self) -> Dict[str, Dict[str, Any]]:
        # SNAPSHOT-autoritativo (P5/P12): o jornal NUNCA sobrepõe o snapshot
        # em load() — quem grava no snapshot direto (ex.: teste P5 _age_record)
        # precisa ser enxergado aqui, e o baseline de integridade assina o
        # arquivo do snapshot, não o jornal.
        if not self.index_file.exists():
            return {}
        try:
            data = json.loads(self.index_file.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("Staging ilegivel em %s: %s; tentando recovery", self.index_file, exc)
            self.recover_from_delta()
            try:
                data = json.loads(self.index_file.read_text(encoding="utf-8"))
            except Exception:
                return {}
        return data if isinstance(data, dict) else {}

    def _save(self, records: Dict[str, Dict[str, Any]]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)  # lazy: instanciar o store nao escreve
        payload = json.dumps(records, indent=2, ensure_ascii=False, sort_keys=True)
        fd, tmp_name = tempfile.mkstemp(prefix=f".{self.index_file.name}.", dir=self.root)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(payload)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp_name, self.index_file)
            dir_fd = os.open(self.root, os.O_DIRECTORY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        finally:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)
        self._maybe_compact()

    # ── Jornal aditivo (P12 — escrita em delta) ───────────────────────────
    def _append_journal(self, op: str, key: str, record: Dict[str, Any]) -> None:
        """ADICIONA uma linha {seq, op, key, record, ts} ao jornal (append-only).

        A escrita é aditiva por construção (modo 'a'); a linha carrega o
        registro COMPLETO após a mutação, então o jornal é auto-suficiente
        para reconstruir o snapshot em recuperação.
        """
        self.root.mkdir(parents=True, exist_ok=True)
        seq = self.journal_record_count() + 1
        line = json.dumps(
            {"seq": seq, "op": op, "key": key, "record": record, "ts": time.time()},
            ensure_ascii=False, sort_keys=True,
        )
        with open(self.delta_file, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")

    def journal_record_count(self) -> int:
        if not self.delta_file.exists():
            return 0
        try:
            text = self.delta_file.read_text(encoding="utf-8")
        except Exception as exc:
            logger.warning("Jornal ilegivel em %s: %s", self.delta_file, exc)
            return 0
        return len([ln for ln in text.splitlines() if ln.strip()])

    def _maybe_compact(self) -> None:
        """Trunca o jornal ao atingir o limiar: o snapshot recém-gravado já
        contém tudo; o jornal recomeça em seq 1 (append-only por segmento)."""
        if self.journal_record_count() >= DELTA_COMPACT_THRESHOLD:
            try:
                self.delta_file.unlink()
            except FileNotFoundError:
                pass
            except Exception as exc:
                logger.warning("Compactacao do jornal falhou em %s: %s", self.delta_file, exc)

    def recover_from_delta(self) -> bool:
        """Reconstrói o SNAPSHOT a partir do jornal (P12 — recuperação).

        Para o caso de o snapshot estar ausente/corrompido: re-joga o jornal
        em ordem de seq (upsert/promote/expire carregam o registro completo
        após a mutação), grava um snapshot novo e trunca o jornal (que passa
        a estar subsumido). Devolve True se um snapshot foi gravado.
        """
        if not self.delta_file.exists():
            return False
        records: Dict[str, Dict[str, Any]] = {}
        try:
            lines = [ln for ln in self.delta_file.read_text(encoding="utf-8").splitlines() if ln.strip()]
        except Exception as exc:
            logger.warning("Recuperacao via jornal falhou em %s: %s", self.delta_file, exc)
            return False
        entries = []
        for ln in lines:
            try:
                entries.append(json.loads(ln))
            except Exception as exc:
                logger.warning("Linha de jornal invalida ignorada: %s", exc)
        entries.sort(key=lambda e: int(e.get("seq") or 0))
        for entry in entries:
            rec = entry.get("record")
            if isinstance(rec, dict) and rec.get("key"):
                records[rec["key"]] = rec
        if not records:
            return False
        self._save(records)
        # Snapshot recém-gravado é autoritativo: jornal truncado.
        try:
            self.delta_file.unlink()
        except FileNotFoundError:
            pass
        return True

    def get(self, key: str) -> Optional[Dict[str, Any]]:
        return self.load().get(key)

    def is_promoted(self, key: str) -> bool:
        rec = self.get(key)
        return bool(rec and rec.get("status") == PROMOTED)

    def stage_candidate(self, candidate: MemoryCandidate, reason: str = "") -> Dict[str, Any]:
        """Persiste o candidato como pending, acumulando proveniencia entre sessoes."""
        key = candidate_key(candidate.fact)
        records = self.load()
        rec = records.get(key)
        now = time.time()
        if rec is None:
            rec = {
                "key": key,
                "fact": candidate.fact,
                "source_uri": candidate.source_uri,
                "confidence": candidate.confidence,
                "proposed_destination": candidate.proposed_destination,
                "scope": candidate.scope,
                "provenance": list(candidate.provenance),
                "status": PENDING,
                "gate": reason,
                "created_at": now,
                "last_seen_at": now,
                "promoted_path": None,
            }
        else:
            rec["confidence"] = max(float(rec.get("confidence") or 0.0), candidate.confidence)
            rec["status"] = PENDING
            rec["gate"] = reason
            rec["last_seen_at"] = now
            merged = list(rec.get("provenance") or [])
            for prov in candidate.provenance:
                if prov not in merged:
                    merged.append(prov)
            rec["provenance"] = merged
        records[key] = rec
        self._append_journal("upsert", key, rec)  # P12: delta aditivo
        self._save(records)
        return rec

    def mark_promoted(self, key: str, doc_path: Optional[str] = None) -> bool:
        records = self.load()
        rec = records.get(key)
        if rec is None:
            return False
        rec["status"] = PROMOTED
        rec["promoted_path"] = str(doc_path) if doc_path else None
        rec["promoted_at"] = time.time()
        self._append_journal("promote", key, rec)  # P12: delta aditivo
        self._save(records)
        return True

    def expire(self, now: Optional[float] = None, ttl_days: int = 30) -> int:
        """Política de TTL (P5, memory_governance): demove candidatos ``pending``
        para ``expired``.

        Um candidato expira quando (a) ``valid_to`` está vencido ou (b) fica
        ``ttl_days`` (default 30) sem reforço — ``last_seen_at`` antigo, o proxy
        de acesso do staging. Demover NUNCA apaga: o registro permanece com
        status ``expired`` (e ``expired_at``) como trilha de auditoria, e sai de
        ``list_pending``. Retorna quantos foram demovidos.
        """
        now = time.time() if now is None else float(now)
        records = self.load()
        expired = 0
        for rec in records.values():
            if rec.get("status") != PENDING:
                continue
            valid_to = rec.get("valid_to")
            ttl_passed = (now - float(rec.get("last_seen_at") or 0.0)) > ttl_days * 86400
            if (isinstance(valid_to, (int, float)) and float(valid_to) < now) or ttl_passed:
                rec["status"] = EXPIRED
                rec["expired_at"] = now
                self._append_journal("expire", rec.get("key") or "", rec)  # P12: delta aditivo
                expired += 1
        if expired:
            self._save(records)
        return expired

    def list_pending(self) -> List[Dict[str, Any]]:
        return [r for r in self.load().values() if r.get("status") == PENDING]
