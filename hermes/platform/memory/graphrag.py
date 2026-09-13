"""B4 — GraphRAG real: memória relacional via SERVIÇO (forma B) + fallback de
índice local determinístico.

Dois modos, fail-closed:
- serviço (``service_url``): cliente HTTP (urllib seam injetável) sobre
  ``POST /query`` com ``{question, method: global|local}`` — o deployment real
  roda o GraphRAG out-of-process; este processo NUNCA importa o pacote.
- índice local (``index_dir`` com ``entities.csv``/``relationships.csv``):
  busca por palavras do ``entities.csv`` sem embeddings — contexto honesto,
  determinístico, para CI/demo e quando o serviço está ausente.

``available()``: no modo serviço, probe ``GET /health`` (200); no modo índice,
exigência de entities.csv. Se nenhum modo configurado -> available False e
``query_*`` levanta ``GraphRAGError``.
"""

import csv
import json
import os
import urllib.error
import urllib.request
from typing import Any, Callable, Dict, List, Optional, Tuple

Transport = Callable[[str, str, Optional[bytes], Dict[str, str]],
                     Tuple[int, bytes]]


class GraphRAGError(RuntimeError):
    pass


class GraphRAGRemoteError(GraphRAGError):
    def __init__(self, status: int, detail: str):
        super().__init__(f"graphrag service HTTP {status}: {detail}")
        self.status = status


def _default_transport(method: str, url: str, body: Optional[bytes],
                       headers: Dict[str, str]) -> Tuple[int, bytes]:
    req = urllib.request.Request(url, data=body, method=method)
    for key, value in headers.items():
        req.add_header(key, value)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()
    except urllib.error.URLError as exc:
        raise GraphRAGError(f"graphrag transport failure: {exc}") from exc


def _load_entities(index_dir: str) -> List[Dict[str, str]]:
    path = os.path.join(index_dir, "entities.csv")
    if not os.path.isfile(path):
        raise GraphRAGError(f"index missing entities.csv in {index_dir!r}")
    with open(path, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        rows = [dict(row) for row in reader if any(row.values())]
    return rows


class GraphRAGClient:
    """Memória relacional (entidades/comunidades) — serviço ou índice local."""

    def __init__(self, *, service_url: Optional[str] = None,
                 index_dir: Optional[str] = None,
                 transport: Optional[Transport] = None,
                 timeout_seconds: float = 30.0):
        if service_url is None and index_dir is None:
            raise GraphRAGError(
                "GraphRAGClient needs service_url or index_dir (fail-closed)")
        self.service_url = (service_url or "").rstrip("/")
        self.index_dir = index_dir
        self.transport = transport or _default_transport
        self.timeout_seconds = timeout_seconds

    @property
    def mode(self) -> str:
        if self.service_url:
            return "service"
        return "index"

    def available(self) -> bool:
        if self.service_url:
            try:
                status, _ = self.transport(
                    "GET", f"{self.service_url}/health", None,
                    {"Accept": "application/json"})
                return status == 200
            except Exception:
                return False
        if self.index_dir:
            return os.path.isfile(os.path.join(self.index_dir, "entities.csv"))
        return False

    def _require(self) -> None:
        if not self.available():
            raise GraphRAGError(
                f"graphrag {self.mode} mode not available (fail-closed)")

    # ------------------------------------------------------------------ #
    def query_local(self, question: str) -> Dict[str, Any]:
        return self._query(question, method="local")

    def query_global(self, question: str) -> Dict[str, Any]:
        return self._query(question, method="global")

    def _query(self, question: str, *, method: str) -> Dict[str, Any]:
        self._require()
        if self.service_url:
            body = json.dumps({"query": question, "method": method,
                               "response_type": "multiple"}).encode("utf-8")
            status, payload = self.transport(
                "POST", f"{self.service_url}/query", body,
                {"Content-Type": "application/json",
                 "Accept": "application/json"})
            if status < 200 or status >= 300:
                raise GraphRAGRemoteError(
                    status, payload.decode("utf-8", errors="replace")[:300])
            try:
                data = json.loads(payload.decode("utf-8"))
            except (ValueError, UnicodeDecodeError) as exc:
                raise GraphRAGError(
                    f"graphrag service returned non-JSON: {exc}") from exc
            if not isinstance(data, dict):
                raise GraphRAGError("graphrag service result must be an object")
            return {
                "mode": "service",
                "method": method,
                "response": data.get("response", ""),
                "sources": data.get("sources") or [],
                "entities": data.get("entities") or [],
            }
        return self._index_query(question, method=method)

    def _index_query(self, question: str, *, method: str) -> Dict[str, Any]:
        rows = _load_entities(self.index_dir or "")
        terms = [t for t in question.lower().split() if len(t) > 2]
        matched = [
            row for row in rows
            if any(t in str(row.get("entity", "")).lower()
                   or t in str(row.get("description", "")).lower()
                   for t in terms)
        ]
        relationships: List[Dict[str, str]] = []
        rel_path = os.path.join(self.index_dir or "", "relationships.csv")
        if os.path.isfile(rel_path):
            with open(rel_path, newline="", encoding="utf-8") as fh:
                reader = csv.DictReader(fh)
                matched_ids = {r.get("entity") for r in matched}
                for row in reader:
                    source = str(row.get("source", ""))
                    target = str(row.get("target", ""))
                    # Relação toca o contexto quando UM dos lados casou.
                    if source in matched_ids or target in matched_ids:
                        relationships.append(row)
        entities = [{"entity": r.get("entity", ""),
                     "type": r.get("type", ""),
                     "description": r.get("description", "")}
                    for r in matched]
        return {
            "mode": "index",
            "method": method,
            "response": f"Local GraphRAG context: {len(matched)} entities "
                        f"matched for {len(terms)} terms.",
            "entities": entities,
            "sources": [{"source": r.get("entity", ""),
                         "description": r.get("description", "")}
                        for r in matched],
            "relationships": relationships,
        }
