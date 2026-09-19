"""scripts/scan_plugin_source.py flags dangerous plugin primitives without flagging honest code.

The rule set backs ``plugin-catalog/README.md`` rule 5, which commits to running self-updater and
credential-store checks on swept-in entries. The tests below are two-sided on purpose: every HIGH
rule must fire on its minimal trigger, and the shapes that look similar but are legitimate
(``re.compile``, ``model.eval``, ``subprocess.run`` without ``shell=True``, ``yaml.load`` with
``SafeLoader``) must stay silent — a scanner that cries wolf on its own plugins/ tree gets muted,
and a muted scanner enforces nothing.
"""
import importlib.util
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "scan_plugin_source.py"


def _load():
    spec = importlib.util.spec_from_file_location("scan_plugin_source", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    # dataclasses resolve string annotations through sys.modules[cls.__module__] (3.11).
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _rules_hit(source: str) -> set[str]:
    mod = _load()
    return {f.rule for f in mod.scan_source(source, "plugin.py")}


# ── every HIGH rule fires on its minimal trigger ────────────────────────────────────────────────

def test_dynamic_code_execution_is_flagged():
    assert _rules_hit("exec(payload)\n") == {"PLG001"}
    assert _rules_hit("eval(user_input)\n") == {"PLG001"}


def test_shell_invocation_is_flagged():
    assert "PLG002" in _rules_hit("import subprocess\nsubprocess.run(cmd, shell=True)\n")
    assert "PLG002" in _rules_hit("import os\nos.system(cmd)\n")


def test_unsafe_deserialization_is_flagged():
    assert "PLG003" in _rules_hit("import pickle\npickle.loads(blob)\n")
    assert "PLG003" in _rules_hit("import marshal\nmarshal.loads(blob)\n")


def test_plugin_rewriting_its_own_files_is_flagged():
    """The catalog's whole trust model is the SHA pin; a self-updater moves a copy past it."""
    source = "from pathlib import Path\nPath(__file__).write_text(new_body)\n"
    assert "PLG004" in _rules_hit(source)
    assert "PLG004" in _rules_hit("open(__file__, 'w').write(new_body)\n")


def test_decoded_blob_fed_to_exec_is_flagged():
    source = "import base64\nexec(base64.b64decode(blob))\n"
    assert "PLG005" in _rules_hit(source)


def test_credential_store_passed_to_a_call_is_flagged():
    assert "PLG007" in _rules_hit("open('/home/u/.ssh/id_rsa').read()\n")
    assert "PLG007" in _rules_hit("Path('~/.aws/credentials').expanduser()\n")


def test_credential_name_in_a_denylist_is_not_flagged():
    """Regression: catalog entry health-data names these files in LOCAL_INSTALL_SECRET_NAMES.

    That code REFUSES to install secret files. Flagging the name punishes the author for being
    careful, so only a literal that reaches a call counts.
    """
    denylist = (
        "LOCAL_INSTALL_SECRET_NAMES = {\n"
        "    '.env',\n"
        "    'credentials.json',\n"
        "    'google_token.json',\n"
        "}\n"
    )
    assert _rules_hit(denylist) == set()
    assert _rules_hit("CREDENTIAL_FILES = ('.ssh/id_rsa', '.aws/credentials')\n") == set()


def test_bare_compile_is_not_dynamic_code_execution():
    """Regression: catalog entry cashew calls compile() purely as a syntax check.

    compile() builds a code object and executes nothing, so it is not the PLG001 shape; the
    dangerous form ``exec(compile(...))`` is caught on the outer exec.
    """
    assert _rules_hit("compile(source, str(script), 'exec')\n") == set()
    assert "PLG001" in _rules_hit("exec(compile(source, '<s>', 'exec'))\n")


def test_runtime_resolved_import_is_flagged_at_medium():
    mod = _load()
    findings = mod.scan_source("import importlib\nimportlib.import_module(name)\n", "plugin.py")
    assert [(f.rule, f.confidence) for f in findings] == [("PLG006", mod.MEDIUM)]


# ── legitimate shapes must stay silent ──────────────────────────────────────────────────────────

def test_other_compile_and_eval_methods_are_not_dynamic_code_execution():
    """``re.compile`` and ``torch.eval`` share a leaf name with the builtins but are other functions."""
    assert _rules_hit("import re\nre.compile(rule['regex'])\n") == set()
    assert _rules_hit("model.eval()\n") == set()
    assert _rules_hit("df.eval('a + b')\n") == set()


def test_subprocess_without_shell_is_not_flagged():
    assert _rules_hit("import subprocess\nsubprocess.run(['ls', '-l'])\n") == set()
    assert _rules_hit("import subprocess\nsubprocess.run(cmd, shell=False)\n") == set()


def test_yaml_load_with_safe_loader_is_not_flagged():
    assert _rules_hit("import yaml\nyaml.load(text, Loader=yaml.SafeLoader)\n") == set()
    assert "PLG008" in _rules_hit("import yaml\nyaml.load(text)\n")


def test_literal_code_builders_are_not_flagged():
    """``compile('1+1', '<s>', 'eval')`` is a literal; nothing is assembled at runtime."""
    assert _rules_hit("compile('1 + 1', '<s>', 'eval')\n") == set()
    assert _rules_hit("exec('x = 1')\n") == set()


def test_interpolated_fstring_is_not_a_literal():
    """An f-string that interpolates is computed at runtime, however constant it looks."""
    assert "PLG001" in _rules_hit('exec(f"{payload}")\n')
    assert _rules_hit('exec(f"x = 1")\n') == set()


def test_asyncio_shell_variant_is_flagged():
    """``create_subprocess_shell`` takes a command string by construction — no shell= to inspect."""
    assert "PLG002" in _rules_hit("import asyncio\nasyncio.create_subprocess_shell(cmd)\n")


def test_reading_own_files_is_not_self_update():
    """Reading ``__file__`` is normal; only writing to it defeats the pin."""
    assert _rules_hit("from pathlib import Path\nPath(__file__).read_text()\n") == set()


def test_unparsable_source_yields_no_findings():
    mod = _load()
    assert mod.scan_source("def broken(:\n", "plugin.py") == []


def test_findings_are_ordered_and_carry_the_source_line():
    mod = _load()
    findings = mod.scan_source("import os\nos.system(a)\nos.system(b)\n", "plugin.py")
    assert [f.line for f in findings] == [2, 3]
    assert findings[0].text == "os.system(a)"
    assert findings[0].path == "plugin.py"


# ── CLI contract ────────────────────────────────────────────────────────────────────────────────

def _plugin(tmp_path, body: str, name="candidate"):
    root = tmp_path / name
    root.mkdir(parents=True)
    (root / "__init__.py").write_text(body, encoding="utf-8")
    return root


def test_advisory_by_default_and_strict_on_request(tmp_path):
    mod = _load()
    root = _plugin(tmp_path, "import os\nos.system(cmd)\n")
    assert mod.main([str(root)]) == 0
    assert mod.main(["--strict", str(root)]) == 1


def test_clean_plugin_passes_strict(tmp_path):
    mod = _load()
    root = _plugin(tmp_path, "def register(ctx):\n    ctx.register_tool('t', None)\n")
    assert mod.main(["--strict", str(root)]) == 0


def test_medium_findings_do_not_fail_strict(tmp_path):
    mod = _load()
    root = _plugin(tmp_path, "import importlib\nimportlib.import_module(name)\n")
    assert mod.main(["--strict", str(root)]) == 0


def test_missing_directory_is_a_usage_error(tmp_path):
    mod = _load()
    assert mod.main([str(tmp_path / "absent")]) == 2


def test_json_output_round_trips(tmp_path):
    import json

    mod = _load()
    root = _plugin(tmp_path, "import os\nos.system(cmd)\n")
    out = tmp_path / "findings.json"
    assert mod.main(["--json", str(out), str(root)]) == 0
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert [f["rule"] for f in payload] == ["PLG002"]
    assert payload[0]["confidence"] == "high"
