#!/usr/bin/env python3
"""haos-fetch-cf — busca páginas atrás de Cloudflare/anti-bot usando Scrapling.

Estratégias:
  http     → Fetcher (curl_cffi) com impersonação de navegador (rápido).
  stealth  → StealthyFetcher (browser headless, resolve Cloudflare/JS).
  auto     → tenta http; se o corpo vier vazio/challenge, cai para stealth.

Uso:
  python scripts/haos_fetch_cf.py <url> [-o out.{html|md|txt}] [--strategy stealth] [--text]

Integração HAOS: instalado no venv de runtime (/usr/local/lib/haos-agent/venv).
Resolve PLAYWRIGHT_BROWSERS_PATH automaticamente (env → /root/.cache → ~/.cache).
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from html import unescape

_TAG_RE = re.compile(r"<[^>]+>")
_DROP_RE = re.compile(r"<(script|style|noscript|svg|template)[\s\S]*?</\1>", re.I)
_HEAD_OPEN_RE = re.compile(r"<h([1-6])[^>]*>", re.I)
_BLOCK_CLOSE_RE = re.compile(
    r"</(p|div|section|article|header|footer|main|aside|blockquote|pre|table|tr|ul|ol|figure|figcaption|dd|dt)>",
    re.I,
)


def _browsers_path() -> str | None:
    env = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if env:
        return env
    for cand in ("/root/.cache/ms-playwright", os.path.expanduser("~/.cache/ms-playwright")):
        if os.path.isdir(cand):
            return cand
    return None


def _decode(body) -> str:
    if isinstance(body, bytes):
        return body.decode("utf-8", "replace")
    return str(body or "")


def parse_cookies(blob: str) -> dict[str, str]:
    """Extrai cookies de formatos que o usuário costuma colar.

    Aceita: header `k=v; k2=v2`, JSON (dict ou export do Cookie-Editor),
    Netscape cookies.txt e dump de cURL (`Copy as cURL` do DevTools).
    """
    text = blob.strip()
    if text.startswith("curl ") or "\ncurl " in text or " -H " in text:
        found = re.findall(r"(?:-b|--cookie)\s+'([^']+)'", text)
        found += re.findall(r"(?:-H|--header)\s+'[Cc]ookie:\s*([^']+)'", text)
        if found:
            text = found[0]

    if text.startswith(("{", "[")):
        import json

        try:
            data = json.loads(text)
        except ValueError:
            data = None
        if isinstance(data, dict) and isinstance(data.get("cookies"), list):
            data = data["cookies"]
        if isinstance(data, list):
            return {
                str(c["name"]): str(c.get("value", ""))
                for c in data
                if isinstance(c, dict) and c.get("name")
            }
        if isinstance(data, dict):
            return {str(k): str(v) for k, v in data.items()}

    pairs: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "\t" in line:  # Netscape cookies.txt: domain, flag, path, secure, exp, name, value
            parts = line.split("\t")
            if len(parts) >= 7:
                pairs[parts[5]] = parts[6]
            continue
        for chunk in line.split(";"):
            if "=" in chunk:
                key, _, val = chunk.partition("=")
                key, val = key.strip(), val.strip().strip('"')
                if key:
                    pairs[key] = val
    return pairs


def load_cookies(path: str | None) -> dict[str, str]:
    path = path or os.environ.get("HAOS_FETCH_COOKIES")
    if not path:
        return {}
    try:
        with open(path, encoding="utf-8") as fh:
            cookies = parse_cookies(fh.read())
    except OSError as exc:
        sys.stderr.write(f"[haos-fetch] não consegui ler cookies de {path}: {exc}\n")
        return {}
    if cookies:
        sys.stderr.write(f"[haos-fetch] cookies carregados: {', '.join(sorted(cookies))}\n")
    return cookies


def _to_text(html: str) -> str:
    """HTML → markdown preservando estrutura (títulos, parágrafos, listas).

    Estrutura importa: o chunker hierárquico do RAGFlow divide por cabeçalhos e
    parágrafos — texto achatado em uma linha vira UM chunk gigante e recupera mal.
    """
    s = _DROP_RE.sub(" ", html)
    s = _HEAD_OPEN_RE.sub(lambda m: "\n\n" + "#" * int(m.group(1)) + " ", s)
    s = re.sub(r"</h[1-6]>", "\n\n", s, flags=re.I)
    s = re.sub(r"<li[^>]*>", "\n- ", s, flags=re.I)
    s = _BLOCK_CLOSE_RE.sub("\n\n", s)
    s = re.sub(r"<br\s*/?>", "\n", s, flags=re.I)
    s = unescape(_TAG_RE.sub(" ", s))

    lines: list[str] = []
    for raw in s.splitlines():
        line = re.sub(r"[ \t]+", " ", raw).strip()
        if line:
            lines.append(line)
        elif lines and lines[-1] != "":
            lines.append("")
    return "\n".join(lines).strip()


def fetch(url: str, strategy: str = "auto", cookies: dict[str, str] | None = None) -> tuple[str, str]:
    from scrapling.fetchers import Fetcher, StealthyFetcher

    bp = _browsers_path()
    if bp:
        os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", bp)

    cookies = cookies or {}
    if strategy in ("auto", "http"):
        try:
            r = Fetcher.get(url, impersonate="chrome", cookies=cookies or None)
            html = _decode(getattr(r, "body", None) or getattr(r, "text", "") or "")
            if html and len(html) > 500 and "Just a moment" not in html[:2000]:
                return html, "http"
        except Exception as exc:  # noqa: BLE001
            sys.stderr.write(f"[haos-fetch] http falhou ({exc}); tentando stealth...\n")
        if strategy == "http":
            raise SystemExit("✗ Estratégia http não retornou conteúdo útil.")

    # StealthyFetcher (Playwright) espera SetCookieParam: lista de dicts por domínio.
    stealth_cookies = [
        {"name": name, "value": value, "domain": ".medium.com", "path": "/"}
        for name, value in cookies.items()
    ] if "medium.com" in url else [
        {"name": name, "value": value, "path": "/"} for name, value in cookies.items()
    ]
    r = StealthyFetcher.fetch(
        url, network_idle=True, headless=True, cookies=stealth_cookies or None
    )
    html = _decode(getattr(r, "body", None) or getattr(r, "text", "") or "")
    if not html:
        raise SystemExit(f"✗ StealthyFetcher não retornou conteúdo para {url}")
    return html, "stealth"


def main() -> int:
    ap = argparse.ArgumentParser(description="HAOS fetch com bypass Cloudflare (Scrapling).")
    ap.add_argument("url", help="URL a buscar")
    ap.add_argument("-o", "--output", help="arquivo de saída (.html/.md/.txt); sem isso imprime texto")
    ap.add_argument("--strategy", choices=("auto", "http", "stealth"), default="auto")
    ap.add_argument("--text", action="store_true", help="imprime como texto simples mesmo com -o .html")
    ap.add_argument(
        "--cookies-file",
        help="arquivo com cookies de sessão (header 'k=v; k2=v2', JSON, Netscape ou dump de cURL). "
        "Também lido de $HAOS_FETCH_COOKIES.",
    )
    args = ap.parse_args()

    html, used = fetch(args.url, args.strategy, load_cookies(args.cookies_file))

    sys.stderr.write(f"[haos-fetch] {args.url} → OK via {used} ({len(html)} chars)\n")

    if not args.output:
        print(_to_text(html) if args.text else html)
        return 0

    ext = os.path.splitext(args.output)[1].lower()
    if ext == ".html" and not args.text:
        content = html
    elif ext == ".md":
        content = _to_text(html)  # markdown simples (texto); refinamento via pandoc opcional
    else:
        content = _to_text(html)

    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as fh:
        fh.write(content)
    sys.stderr.write(f"[haos-fetch] salvo em {args.output}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
