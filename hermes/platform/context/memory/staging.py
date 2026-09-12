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
"""

from __future__ import annotations

import hashlib
import json
import logging
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

    @property
    def index_file(self) -> Path:
        return self.root / STAGING_FILENAME

    def load(self) -> Dict[str, Dict[str, Any]]:
        if not self.index_file.exists():
            return {}
        try:
            data = json.loads(self.index_file.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("Staging ilegivel em %s: %s", self.index_file, exc)
            return {}
        return data if isinstance(data, dict) else {}

    def _save(self, records: Dict[str, Dict[str, Any]]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)  # lazy: instanciar o store nao escreve
        self.index_file.write_text(
            json.dumps(records, indent=2, ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
        )

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
                expired += 1
        if expired:
            self._save(records)
        return expired

    def list_pending(self) -> List[Dict[str, Any]]:
        return [r for r in self.load().values() if r.get("status") == PENDING]
