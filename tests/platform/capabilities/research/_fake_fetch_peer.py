#!/usr/bin/env python3
"""Peer de fetch scriptado (tests only) — subprocesso de pesquisa real.

Emula um CLI/serviço de fetch out-of-process: lê a pergunta do argv,
escreve UMA linha JSON ``{"results": [...]}`` no stdout (resultados
canônicos {url,title,content,score}) e sai 0. Dirigido por env:
- ``HAOS_PEER_MODE`` ok (default) | empty | bad_json | nonzero.
- ``HAOS_PEER_JSON`` caminho de JSON com os resultados a devolver.
"""

import json
import os
import sys


def main() -> int:
    question = sys.argv[1] if len(sys.argv) > 1 else ""
    mode = os.environ.get("HAOS_PEER_MODE", "ok")
    if mode == "nonzero":
        sys.stderr.write("fetch peer: boom\n")
        return 4
    if mode == "bad_json":
        print("{not json")
        return 0
    payload = {"results": []}
    if mode == "empty":
        return 0  # stdout vazio, rc 0 (contrato: linha JSON obrigatória)
    custom = os.environ.get("HAOS_PEER_JSON")
    if custom and os.path.isfile(custom):
        with open(custom, encoding="utf-8") as fh:
            payload = json.load(fh)
    else:
        payload = {"results": [
            {"url": "https://docs.example.com/1", "title": "Doc One",
             "content": f"about {question}", "score": 0.9},
            {"url": "https://docs.example.com/2", "title": "Doc Two",
             "content": "more details", "score": 0.4},
        ]}
    print(json.dumps(payload))
    return 0


if __name__ == "__main__":
    sys.exit(main())
