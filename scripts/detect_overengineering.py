#!/usr/bin/env python3
"""Detect over-engineering shapes in Python source files.

Clean-room reimplementation of the *concept* of static over-engineering
detectors (inspired by the "ten shapes" taxonomy from lean-agent research):
structural patterns that look like diligence when written but cost reading,
testing and maintenance later.

The ten shapes checked:
  1. interface-one-impl   - abstract base class / Protocol with exactly one
                            subclass in the same file
  2. factory-one-product  - a create()/make()/build() method (or factory
                            function) that can only ever return one class
  3. config-for-constant  - a *Config/*Settings/*Options class holding only
                            constants (no behaviour)
  4. speculative-params   - function parameters never used in the body
  5. handrolled-stdlib     - reimplemented stdlib idioms (manual Counter,
                            manual flatten, hand-rolled caching, ...)
  6. delegate-wrapper     - a class whose every method only forwards to
                            another object
  7. defensive-ceremony   - try/except around code that cannot fail, retry
                            loops around local (non-IO) calls
  8. premature-concurrency- `async def` with no `await`, threads for trivial
                            inline work
  9. needless-dependency  - third-party import for something the stdlib does
                            (heuristic: flags well-known tiny packages used
                            for trivial purposes)
 10. prose-padding        - comment/docstring volume far exceeding code
                            (heuristic ratio)

Usage:
    python3 scripts/detect_overengineering.py path/to/file.py [more.py ...]
    python3 scripts/detect_overengineering.py --diff < git.diff
    python3 scripts/detect_overengineering.py --json src/

Exit codes: 0 = no findings, 1 = at least one finding, 2 = parse error.
Findings are advisory, not errors: every shape has legitimate uses.
Pair with the ponytail skill (skills/development/ponytail) for the judgement;
this script only surfaces candidates.
"""

from __future__ import annotations

import ast
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

# ---------------------------------------------------------------- findings

@dataclass
class Finding:
    shape: str
    lineno: int
    detail: str
    file: str = ""

    def as_dict(self):
        return {"file": self.file, "shape": self.shape,
                "line": self.lineno, "detail": self.detail}


# ---------------------------------------------------------------- helpers

def _is_constant(node: ast.AST) -> bool:
    return isinstance(node, ast.Constant)


def _calls_self_method_only(node: ast.AST) -> bool:
    """True if node is `return self.<attr>.<m>(...)` or `self.<attr>.<m>(...)`."""
    if isinstance(node, (ast.Return, ast.Expr)):
        node = node.value
    if not isinstance(node, ast.Call):
        return False
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    return (isinstance(func, ast.Attribute)
            and isinstance(func.value, ast.Attribute)
            and isinstance(func.value.value, ast.Name)
            and func.value.value.id == "self")


def _has_await(node: ast.AST) -> bool:
    return any(isinstance(n, ast.Await) for n in ast.walk(node))


def _has_io_call(node: ast.AST) -> bool:
    io_names = {"open", "request", "urlopen", "connect", "socket",
                "subprocess", "Popen", "run", "execute", "fetch", "get",
                "post", "input"}
    for n in ast.walk(node):
        if isinstance(n, ast.Call):
            f = n.func
            name = ""
            if isinstance(f, ast.Name):
                name = f.id
            elif isinstance(f, ast.Attribute):
                name = f.attr
            if name in io_names:
                return True
    return False


# ---------------------------------------------------------------- shape 1

def _shape_interface_one_impl(tree: ast.Module, file: str):
    """Abstract base with exactly one subclass in the same file."""
    findings = []
    abc_classes = {}   # name -> lineno
    subclasses = {}    # base name -> [linenos]
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        is_abc = any(
            (isinstance(b, ast.Attribute) and b.attr in ("ABC", "ABCMeta"))
            or (isinstance(b, ast.Name) and b.id in ("ABC", "ABCMeta", "Protocol"))
            for b in node.bases
        )
        has_abstract = any(
            isinstance(n, ast.FunctionDef)
            and any(isinstance(d, ast.Name) and d.id == "abstractmethod"
                    or isinstance(d, ast.Attribute) and d.attr == "abstractmethod"
                    for d in n.decorator_list)
            for n in node.body
        )
        if is_abc or has_abstract:
            abc_classes[node.name] = node.lineno
        else:
            for b in node.bases:
                base = b.id if isinstance(b, ast.Name) else (
                    b.attr if isinstance(b, ast.Attribute) else None)
                if base:
                    subclasses.setdefault(base, []).append(node.lineno)
    for name, lineno in abc_classes.items():
        subs = subclasses.get(name, [])
        if len(subs) == 1:
            findings.append(Finding(
                "interface-one-impl", lineno,
                f"abstract class '{name}' has exactly one subclass "
                f"(line {subs[0]}); consider deleting the abstraction",
                file))
    return findings


# ---------------------------------------------------------------- shape 2

def _shape_factory_one_product(tree: ast.Module, file: str):
    """create()/make()/build() that only ever returns one class."""
    findings = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name not in ("create", "make", "build", "get_instance",
                             "new", "factory"):
            continue
        returned = set()
        for n in ast.walk(node):
            if isinstance(n, ast.Return) and isinstance(n.value, ast.Call):
                f = n.value.func
                if isinstance(f, ast.Name):
                    returned.add(f.id)
                elif isinstance(f, ast.Attribute):
                    returned.add(f.attr)
        if len(returned) == 1:
            cls = next(iter(returned))
            findings.append(Finding(
                "factory-one-product", node.lineno,
                f"'{node.name}()' only ever returns '{cls}'; "
                f"the factory adds a layer for one product",
                file))
    return findings


# ---------------------------------------------------------------- shape 3

def _shape_config_for_constant(tree: ast.Module, file: str):
    findings = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        if not re.search(r"(Config|Settings|Options|Constants?)$", node.name):
            continue
        methods = [n for n in node.body
                   if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                   and n.name != "__init__"]
        assigns = [n for n in node.body if isinstance(n, (ast.Assign, ast.AnnAssign))]
        trivial_init = all(
            isinstance(n, ast.FunctionDef) and n.name == "__init__"
            and all(isinstance(s, (ast.Assign, ast.AnnAssign, ast.Expr, ast.Pass))
                    for s in n.body)
            for n in node.body if isinstance(n, ast.FunctionDef))
        if not methods and assigns and (trivial_init or True):
            # only constants, no behaviour
            findings.append(Finding(
                "config-for-constant", node.lineno,
                f"'{node.name}' holds only constants with no behaviour; "
                f"consider module-level constants instead",
                file))
    return findings


# ---------------------------------------------------------------- shape 4

def _shape_speculative_params(tree: ast.Module, file: str):
    findings = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name.startswith("_") and node.name != "__init__":
            continue
        arg_names = [a.arg for a in node.args.args
                     if a.arg not in ("self", "cls")]
        arg_names += [a.arg for a in node.args.kwonlyargs]
        used = set()
        for n in ast.walk(node):
            if isinstance(n, ast.Name):
                used.add(n.id)
            elif isinstance(n, ast.arg):
                pass
        # attribute access like `opts.verbose` counts as use of `opts`
        for n in ast.walk(node):
            if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name):
                used.add(n.value.id)
        unused = [a for a in arg_names if a not in used]
        # ignore *args/**kwargs themselves; dunder methods exempt
        if unused and not (node.name.startswith("__") and node.name.endswith("__")):
            findings.append(Finding(
                "speculative-params", node.lineno,
                f"'{node.name}()' never uses parameter(s): "
                f"{', '.join(unused)}; speculative options for callers "
                f"that do not exist",
                file))
    return findings


# ---------------------------------------------------------------- shape 5

def _shape_handrolled_stdlib(tree: ast.Module, file: str):
    """Heuristic: loops that reimplement Counter / flatten / any / all."""
    findings = []
    src_lines = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.For):
            # manual counter: d[k] = d.get(k, 0) + 1  or  d[k] += 1
            has_get_default = any(
                isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                and n.func.attr == "get" and len(n.args) == 2
                for n in ast.walk(node))
            has_plus_one = any(
                isinstance(n, ast.BinOp) and isinstance(n.op, ast.Add)
                and isinstance(n.right, ast.Constant) and n.right.value == 1
                for n in ast.walk(node))
            has_aug_add = any(
                isinstance(n, ast.AugAssign) and isinstance(n.op, ast.Add)
                and isinstance(n.value, ast.Constant) and n.value.value == 1
                for n in ast.walk(node))
            if (has_get_default and has_plus_one) or has_aug_add:
                findings.append(Finding(
                    "handrolled-stdlib", node.lineno,
                    "manual counting loop; collections.Counter likely does this",
                    file))
            # manual flatten: for sub in x: for item in sub: out.append(item)
            fors = [n for n in ast.walk(node) if isinstance(n, ast.For)]
            appends = [n for n in ast.walk(node)
                       if isinstance(n, ast.Call)
                       and isinstance(n.func, ast.Attribute)
                       and n.func.attr == "append"]
            if len(fors) >= 2 and len(appends) == 1:
                findings.append(Finding(
                    "handrolled-stdlib", node.lineno,
                    "nested loop with single append; itertools.chain or a "
                    "comprehension likely does this",
                    file))
    return findings


# ---------------------------------------------------------------- shape 6

def _shape_delegate_wrapper(tree: ast.Module, file: str):
    findings = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        methods = [n for n in node.body
                   if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                   and not (n.name.startswith("__") and n.name.endswith("__"))]
        if len(methods) < 2:
            continue
        if all(
            len(m.body) == 1 and _calls_self_method_only(m.body[0])
            or (len(m.body) == 2 and isinstance(m.body[0], ast.Expr)
                and isinstance(m.body[0].value, ast.Constant)  # docstring
                and _calls_self_method_only(m.body[1]))
            for m in methods
        ):
            findings.append(Finding(
                "delegate-wrapper", node.lineno,
                f"class '{node.name}': every method only delegates to "
                f"another object; consider using the wrapped object directly",
                file))
    return findings


# ---------------------------------------------------------------- shape 7

def _shape_defensive_ceremony(tree: ast.Module, file: str):
    findings = []
    for node in ast.walk(tree):
        # try/except around a single trivial statement
        if isinstance(node, ast.Try):
            if (len(node.body) == 1
                    and isinstance(node.body[0], (ast.Assign, ast.Expr, ast.Return))
                    and not _has_io_call(node.body[0])):
                findings.append(Finding(
                    "defensive-ceremony", node.lineno,
                    "try/except around a single local statement with no IO; "
                    "the guarded code cannot fail this way",
                    file))
        # retry loop around non-IO calls
        if isinstance(node, (ast.For, ast.While)):
            src = ast.dump(node)
            if ("retry" in src.lower() or "attempt" in src.lower()):
                if not _has_io_call(node):
                    findings.append(Finding(
                        "defensive-ceremony", node.lineno,
                        "retry loop around calls with no IO; retries only "
                        "make sense for fallible external operations",
                        file))
                    break
    return findings


# ---------------------------------------------------------------- shape 8

def _shape_premature_concurrency(tree: ast.Module, file: str):
    findings = []
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and not _has_await(node):
            findings.append(Finding(
                "premature-concurrency", node.lineno,
                f"async def '{node.name}' never awaits; async without await "
                f"is ceremony, not concurrency",
                file))
        if isinstance(node, ast.Call):
            f = node.func
            name = f.attr if isinstance(f, ast.Attribute) else (
                f.id if isinstance(f, ast.Name) else "")
            if name == "Thread":
                findings.append(Finding(
                    "premature-concurrency", node.lineno,
                    "threading.Thread spawned; verify the work is actually "
                    "slow/parallel before paying for threads",
                    file))
    return findings


# ---------------------------------------------------------------- shape 9

def _shape_needless_dependency(tree: ast.Module, file: str, src: str):
    # heuristic: well-known micro-packages often pulled for trivial uses
    suspects = {
        "requests": "urllib.request / http.client cover simple fetches",
        "python-dotenv": "a 5-line os.environ parser covers simple .env files",
        "click": "argparse covers simple CLIs",
        "tqdm": "a manual counter covers trivial progress",
        "pyyaml": "json/tomllib cover simple configs",
    }
    findings = []
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                imported.add(a.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    try:
        stdlib = sys.stdlib_module_names  # Python 3.10+
    except AttributeError:
        stdlib = set()
    for mod in sorted(imported):
        if mod in stdlib or mod in ("__future__",):
            continue
        if mod in suspects:
            findings.append(Finding(
                "needless-dependency", 1,
                f"third-party import '{mod}'; {suspects[mod]}",
                file))
    return findings


# ---------------------------------------------------------------- shape 10

def _shape_prose_padding(src: str, tree: ast.Module, file: str):
    lines = src.splitlines()
    code_lines = [l for l in lines if l.strip() and not l.strip().startswith("#")]
    comment_lines = [l for l in lines if l.strip().startswith("#")]
    doc_len = sum(
        len(ast.get_docstring(n).splitlines()) if ast.get_docstring(n) else 0
        for n in ast.walk(tree)
        if isinstance(n, (ast.Module, ast.ClassDef,
                          ast.FunctionDef, ast.AsyncFunctionDef)))
    total_code = max(len(code_lines), 1)
    prose = len(comment_lines) + doc_len
    if total_code >= 10 and prose / total_code > 1.0:
        return [Finding(
            "prose-padding", 1,
            f"{prose} prose lines vs {total_code} code lines; comments "
            f"explaining simple code move complexity into prose",
            file)]
    return []


# ---------------------------------------------------------------- driver

SHAPE_RUNNERS = (
    _shape_interface_one_impl,
    _shape_factory_one_product,
    _shape_config_for_constant,
    _shape_speculative_params,
    _shape_handrolled_stdlib,
    _shape_delegate_wrapper,
    _shape_defensive_ceremony,
    _shape_premature_concurrency,
)


def analyze_source(src: str, file: str = "<stdin>"):
    """Return (findings, parse_error)."""
    try:
        tree = ast.parse(src)
    except SyntaxError as e:
        return [], f"{file}:{e.lineno}: syntax error: {e.msg}"
    findings = []
    for runner in SHAPE_RUNNERS:
        try:
            findings.extend(runner(tree, file))
        except Exception:
            continue
    try:
        findings.extend(_shape_needless_dependency(tree, file, src))
    except Exception:
        pass
    try:
        findings.extend(_shape_prose_padding(src, tree, file))
    except Exception:
        pass
    findings.sort(key=lambda f: (f.lineno, f.shape))
    return findings, None


def analyze_path(path: Path):
    try:
        src = path.read_text(encoding="utf-8")
    except Exception as e:
        return [], f"{path}: cannot read: {e}"
    return analyze_source(src, str(path))


def main(argv):
    import argparse
    ap = argparse.ArgumentParser(
        description="Detect over-engineering shapes in Python files "
                    "(advisory; pair with the ponytail skill).")
    ap.add_argument("paths", nargs="*", help="files or directories to scan")
    ap.add_argument("--json", action="store_true", help="JSON output")
    ap.add_argument("--diff", action="store_true",
                    help="read unified diff from stdin, scan added Python lines "
                         "as fragments (best effort)")
    args = ap.parse_args()

    all_findings = []
    errors = []

    if args.diff:
        # crude: collect added lines per file from the diff
        current, buf = None, []
        chunks = []
        for line in sys.stdin:
            if line.startswith("+++ b/"):
                if current and buf:
                    chunks.append((current, "".join(buf)))
                current = line[6:].strip()
                buf = []
            elif line.startswith("+") and not line.startswith("+++"):
                buf.append(line[1:])
        if current and buf:
            chunks.append((current, "".join(buf)))
        for fname, frag in chunks:
            if not fname.endswith(".py"):
                continue
            findings, err = analyze_source(frag, fname)
            if err:
                errors.append(err)
            all_findings.extend(findings)
    else:
        targets = []
        for p in args.paths or ["."]:
            pp = Path(p)
            if pp.is_dir():
                targets.extend(sorted(pp.rglob("*.py")))
            elif pp.is_file():
                targets.append(pp)
        for t in targets:
            # skip vendored / generated noise
            if any(part in ("node_modules", ".venv", "venv", "__pycache__",
                            ".git", "fixtures")
                   for part in t.parts):
                continue
            findings, err = analyze_path(t)
            if err:
                errors.append(err)
            all_findings.extend(findings)

    if args.json:
        import json as _json
        print(_json.dumps([f.as_dict() for f in all_findings], indent=2))
    else:
        for f in all_findings:
            print(f"{f.file}:{f.lineno}: [{f.shape}] {f.detail}")
        if errors:
            print("--- errors ---", file=sys.stderr)
            for e in errors:
                print(e, file=sys.stderr)

    if errors:
        return 2
    return 1 if all_findings else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
