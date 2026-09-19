#!/usr/bin/env python3
"""Static security scan of a candidate plugin's SOURCE, for catalog admission review.

``plugin-catalog/README.md`` rule 5 commits to a specific process for swept-in entries: "every pin
validated and scanned at the pinned commit, self-updater and credential-store checks run". Two of
those three clauses are enforced today; one is not, and the other stops at the JS boundary:

  * "validated"        -> ``plugin-catalog-ci.yml`` clones the pin and runs ``hermes plugins validate``
  * "self-updater"     -> an inline grep in the same workflow, restricted to
                          ``*.js``/``*.mjs``/``*.cjs``/``*.ts`` — a self-updater written in Python
                          is not seen at all
  * "credential-store" -> nothing in this repository implements it

Neither ``scripts/validate_plugin_catalog.py`` (entry YAML) nor ``hermes_cli/plugin_validate.py``
(``manifest.yaml`` plus a subprocess-isolated capability probe that records what ``register()``
DECLARES) reads the plugin's Python, so neither can see what a registered tool handler does when it
runs. This closes the two gaps that leaves: credential-store access, and a Python self-updater.

This scans the Python source at the pinned commit and reports the primitives that make a plugin
dangerous independently of what its manifest claims. It is deliberately DETERMINISTIC: an admission
gate must produce the same verdict twice, must run in CI with no model credentials, and must be
argued with by quoting a line rather than paraphrasing a judgment.

ADVISORY by default (exit 0), matching how this repo introduces every other new lint
(``check_profile_scope_patterns.py``, the public-surface diff): most rules have legitimate uses, and
the reviewer reads each hit against its rule. ``--strict`` exits 1 on any HIGH finding for the
catalog lane.

Usage:
    python scripts/scan_plugin_source.py <plugin-dir> [<plugin-dir> ...]
    python scripts/scan_plugin_source.py --strict <plugin-dir>
    python scripts/scan_plugin_source.py --json out.json <plugin-dir>
"""
from __future__ import annotations

import argparse
import ast
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, Iterator

# Confidence is about the RULE, not the finding: HIGH means "a plugin has no legitimate reason to do
# this, so the hit is the plugin's problem"; MEDIUM means "common in honest code, read the context".
HIGH = "high"
MEDIUM = "medium"

# Credential stores outside the plugin's own config. Plugins read secrets through the plugin ctx /
# secret scope; reaching for these paths directly is the shape that turns a plugin into an
# exfiltration primitive. Bare ".env" is NOT here on purpose — too many honest plugins ship one.
# "credentials.json" / "service_account.json" are NOT here either: they are the standard Google
# OAuth client-config filenames, and a plugin doing Google auth legitimately names them. A real
# catalog entry (health-data) names "credentials.json" inside a LOCAL_INSTALL_SECRET_NAMES denylist
# — code that REFUSES to install secret files, which substring matching flagged as the inverse.
CREDENTIAL_PATHS = (
    ".ssh/id_rsa", ".ssh/id_ed25519", ".ssh/id_ecdsa", ".ssh/id_dsa",
    ".aws/credentials", ".aws/config",
    ".docker/config.json", ".git-credentials", ".netrc", ".pypirc",
    ".kube/config", ".pgpass", ".my.cnf",
    "id_rsa",
)

# Calls whose first argument being non-literal means code is being built at runtime. Matched on the
# EXACT dotted name: these are builtins, so ``re.compile`` / ``model.eval`` / ``df.eval`` are other
# functions entirely and must not match (a bare ``leaf in`` test flagged ``re.compile(rule[...])``
# in this repo's own plugins/security-guidance).
#
# ``compile`` is deliberately ABSENT. It builds a code object and executes nothing, so a bare
# ``compile(source, path, "exec")`` is a syntax check — the shape a real catalog entry (cashew) uses
# in its smoke test. The dangerous form is ``exec(compile(...))``, which this rule already catches on
# the outer ``exec``.
_CODE_BUILDERS = {"exec", "eval", "builtins.exec", "builtins.eval"}
# Decoders that turn an opaque blob back into the source of a code-builder argument.
_OBFUSCATORS = {"b64decode", "decodebytes", "unhexlify", "fromhex", "decompress", "decrypt",
                "urlsafe_b64decode", "a85decode", "b85decode"}
_UNSAFE_DESERIALIZERS = {"pickle", "cPickle", "dill", "marshal", "shelve"}
_SUBPROCESS_CALLS = {"run", "Popen", "call", "check_call", "check_output"}

RULES = {
    "PLG001": (HIGH, "dynamic code execution on a computed value"),
    "PLG002": (HIGH, "shell invocation via a shell string"),
    "PLG003": (HIGH, "unsafe deserialization"),
    "PLG004": (HIGH, "plugin rewrites its own files (catalog rule 5 self-updater check)"),
    "PLG005": (HIGH, "decoded blob fed to exec/eval"),
    "PLG006": (MEDIUM, "import resolved at runtime from a computed name"),
    "PLG007": (HIGH, "reads a credential store directly (catalog rule 5 credential-store check)"),
    "PLG008": (MEDIUM, "yaml.load without an explicit safe Loader"),
}


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    rule: str
    confidence: str
    why: str
    text: str

    def render(self) -> str:
        return (f"{self.path}:{self.line}  {self.rule}/{self.confidence}  {self.why}\n"
                f"    {self.text}")


def _dotted(node: ast.AST) -> str:
    """``yaml.load`` / ``os.system`` / ``__import__`` -> the dotted name, else ''."""
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


def _is_literal(node: ast.AST) -> bool:
    """A constant, or a collection of constants — something an author typed, not computed."""
    if isinstance(node, ast.Constant):
        return True
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return all(_is_literal(e) for e in node.elts)
    if isinstance(node, ast.Dict):
        return all(_is_literal(k) for k in node.keys if k is not None)
    if isinstance(node, ast.JoinedStr):
        # An f-string is literal only when it interpolates nothing. Filtering out the
        # FormattedValue nodes and checking the rest made ``exec(f"{payload}")`` look like a
        # constant — exactly the shape this rule exists to catch.
        return not any(isinstance(v, ast.FormattedValue) for v in node.values)
    return False


def _mentions_dunder_file(node: ast.AST) -> bool:
    for sub in ast.walk(node):
        if isinstance(sub, ast.Name) and sub.id == "__file__":
            return True
    return False


def _keyword(node: ast.Call, name: str) -> ast.AST | None:
    for kw in node.keywords:
        if kw.arg == name:
            return kw.value
    return None


def _first_arg(node: ast.Call) -> ast.AST | None:
    return node.args[0] if node.args else None


def _findings_for_call(node: ast.Call) -> Iterator[tuple[str, str]]:
    """(rule_id, why) for one call node. Split out so each rule reads as its own sentence."""
    dotted = _dotted(node.func)
    leaf = dotted.rsplit(".", 1)[-1]
    first = _first_arg(node)

    if dotted in _CODE_BUILDERS and first is not None and not _is_literal(first):
        yield "PLG001", f"{dotted}() on a value built at runtime"

    if dotted == "os.system" or dotted == "os.popen":
        yield "PLG002", f"{dotted}() runs through a shell"
    elif dotted == "asyncio.create_subprocess_shell":
        # The ``_shell`` variant takes a command STRING by construction — there is no
        # shell= keyword to inspect, so the check above never reaches it.
        yield "PLG002", f"{dotted}() runs through a shell"
    elif leaf in _SUBPROCESS_CALLS and dotted.startswith(("subprocess", "asyncio.create_subprocess")):
        shell = _keyword(node, "shell")
        if isinstance(shell, ast.Constant) and shell.value is True:
            yield "PLG002", f"{dotted}(shell=True)"

    if leaf in {"load", "loads", "Unpickler"}:
        root = dotted.split(".", 1)[0]
        if root in _UNSAFE_DESERIALIZERS:
            yield "PLG003", f"{dotted}() deserializes arbitrary objects"

    # A plugin replacing its own source defeats the SHA pin that is the catalog's entire trust
    # model (plugin-catalog/README.md rule 3). Writing to a __file__-derived path is that act, and
    # __file__ reaches the call either as an argument (open(__file__, 'w')) or as the receiver
    # (Path(__file__).write_text(...)) — checking only the arguments missed the common form.
    receiver = node.func.value if isinstance(node.func, ast.Attribute) else None
    targets_file = (any(_mentions_dunder_file(a) for a in node.args)
                    or (receiver is not None and _mentions_dunder_file(receiver)))
    if leaf in {"write_text", "write_bytes", "open", "write"} and targets_file:
        yield "PLG004", f"{dotted}() writes to a path derived from __file__"

    if dotted in _CODE_BUILDERS and first is not None and isinstance(first, ast.Call):
        if _dotted(first.func).rsplit(".", 1)[-1] in _OBFUSCATORS:
            yield "PLG005", f"{dotted}() over a decoded blob ({_dotted(first.func)}())"

    if dotted == "__import__" and first is not None and not _is_literal(first):
        yield "PLG006", "__import__() on a computed name"
    if dotted.endswith("import_module") and first is not None and not _is_literal(first):
        yield "PLG006", f"{dotted}() on a computed name"

    if leaf == "load" and dotted.split(".", 1)[0] == "yaml":
        loader = _keyword(node, "Loader")
        if loader is None or not (_dotted(loader).endswith("SafeLoader")
                                  or _dotted(loader).endswith("CSafeLoader")):
            yield "PLG008", f"{dotted}() without SafeLoader"


def scan_source(text: str, display: str) -> list[Finding]:
    """Every finding in one file's source. A syntax error yields no findings (the loader will fail)."""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    lines = text.splitlines()
    out: list[Finding] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            for rule, why in _findings_for_call(node):
                confidence = RULES[rule][0]
                snippet = lines[node.lineno - 1].strip() if 0 < node.lineno <= len(lines) else ""
                out.append(Finding(display, node.lineno, rule, confidence, why, snippet))

    # Credential-store references, restricted to literals passed to a CALL. Matching a bare literal
    # anywhere inverts the signal: a real catalog entry (health-data) names "credentials.json" and
    # ".env" inside a LOCAL_INSTALL_SECRET_NAMES denylist — code that REFUSES to install secret
    # files — and flagging the name punishes the author for being careful. A path that reaches a
    # call is a path that gets opened; a path that only appears in a collection is a declaration.
    # LIMIT: a plugin that stores the path in a variable first and opens that is not seen. Accepted
    # deliberately — this gate must not cry wolf on the honest majority, and the indirection is rare
    # next to the plain form.
    call_literals = {
        id(arg)
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        for arg in (*node.args, *(kw.value for kw in node.keywords))
    }
    for node in ast.walk(tree):
        if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                and id(node) in call_literals):
            for needle in CREDENTIAL_PATHS:
                if needle in node.value:
                    snippet = lines[node.lineno - 1].strip() if 0 < node.lineno <= len(lines) else ""
                    out.append(Finding(display, node.lineno, "PLG007", HIGH,
                                       f"passes the credential path {needle!r} to a call", snippet))
                    break

    return sorted(out, key=lambda f: (f.line, f.rule))


def iter_plugin_sources(root: Path) -> Iterator[Path]:
    for path in sorted(root.rglob("*.py")):
        if "__pycache__" in path.parts or ".venv" in path.parts or "node_modules" in path.parts:
            continue
        yield path


def scan_dir(root: Path) -> list[Finding]:
    findings: list[Finding] = []
    for path in iter_plugin_sources(root):
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        findings.extend(scan_source(text, path.as_posix()))
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("paths", nargs="+", help="plugin directories to scan")
    parser.add_argument("--json", dest="json_out", default=None, help="write findings as JSON")
    parser.add_argument("--strict", action="store_true",
                        help="exit 1 when any HIGH-confidence finding is present")
    args = parser.parse_args(argv)

    findings: list[Finding] = []
    for raw in args.paths:
        root = Path(raw)
        if not root.is_dir():
            print(f"not a directory: {raw}", file=sys.stderr)
            return 2
        findings.extend(scan_dir(root))

    if args.json_out:
        Path(args.json_out).write_text(
            json.dumps([asdict(f) for f in findings], indent=2), encoding="utf-8")

    for finding in findings:
        print(finding.render())

    high = [f for f in findings if f.confidence == HIGH]
    scanned = sum(1 for raw in args.paths for _ in iter_plugin_sources(Path(raw)))
    print(f"\nscanned {scanned} file(s): {len(findings)} finding(s), "
          f"{len(high)} high-confidence")

    if args.strict and high:
        print("\n--strict: HIGH-confidence findings must be resolved or justified before admission "
              "(plugin-catalog/README.md rule 5).", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
