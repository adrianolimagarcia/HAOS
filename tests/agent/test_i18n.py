"""Tests for agent.i18n -- catalog parity, fallback, language resolution."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from agent import i18n


LOCALES_DIR = Path(__file__).resolve().parents[2] / "locales"


def _load_raw(lang: str) -> dict:
    with (LOCALES_DIR / f"{lang}.yaml").open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _flatten(d, prefix="") -> dict:
    flat = {}
    for k, v in (d or {}).items():
        key = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            flat.update(_flatten(v, key))
        else:
            flat[key] = v
    return flat


# ---------------------------------------------------------------------------
# Catalog completeness -- this is the key invariant test.  If someone adds a
# new key to en.yaml they MUST add it to every other locale, else runtime
# falls back to English for those users and defeats the feature.
# ---------------------------------------------------------------------------



@pytest.mark.parametrize("lang", [l for l in i18n.SUPPORTED_LANGUAGES if l != "en"])
def test_catalog_keys_match_english(lang: str):
    """Every non-English catalog must have exactly the same key set as English."""
    en_keys = set(_flatten(_load_raw("en")).keys())
    lang_keys = set(_flatten(_load_raw(lang)).keys())
    missing = en_keys - lang_keys
    extra = lang_keys - en_keys
    assert not missing, f"{lang}.yaml missing keys: {sorted(missing)}"
    assert not extra, f"{lang}.yaml has keys not in en.yaml: {sorted(extra)}"


@pytest.mark.parametrize("lang", list(i18n.SUPPORTED_LANGUAGES))
def test_catalog_placeholders_match_english(lang: str):
    """Every translated value must use the same {placeholder} tokens as English.

    A mistranslated placeholder (e.g. ``{description}`` typoed as ``{descricao}``)
    would either raise KeyError at runtime or silently drop the interpolated
    value.  Pin parity at the test layer.
    """
    import re
    placeholder_re = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")
    en_flat = _flatten(_load_raw("en"))
    lang_flat = _flatten(_load_raw(lang))
    for key, en_value in en_flat.items():
        en_placeholders = set(placeholder_re.findall(en_value))
        lang_value = lang_flat.get(key, "")
        lang_placeholders = set(placeholder_re.findall(lang_value))
        assert en_placeholders == lang_placeholders, (
            f"{lang}.yaml key={key!r}: placeholders {lang_placeholders} "
            f"don't match English {en_placeholders}"
        )


# ---------------------------------------------------------------------------
# Language resolution
# ---------------------------------------------------------------------------











def test_default_when_nothing_set(monkeypatch):
    """With no env var and no config override, falls back to English."""
    monkeypatch.delenv("HERMES_LANGUAGE", raising=False)
    # Force config lookup to return None -- patch the cached reader.
    i18n.reset_language_cache()
    monkeypatch.setattr(i18n, "_config_language", lambda: None)
    assert i18n.get_language() == "en"


def test_language_is_per_profile_under_multiplex(monkeypatch, tmp_path):
    """HERMES_LANGUAGE in the DEFAULT profile's environ must not leak into a secondary profile's
    turn, and the config-language cache must not freeze one profile's ``display.language`` for all."""
    from agent import secret_scope

    default_home = tmp_path / "default"; default_home.mkdir()
    prof_b = tmp_path / "b"; prof_b.mkdir()
    (default_home / "config.yaml").write_text("display:\n  language: fr\n")
    (prof_b / "config.yaml").write_text("display:\n  language: de\n")
    monkeypatch.setenv("HERMES_LANGUAGE", "zh")  # default profile's .env, bridged into environ
    i18n.reset_language_cache()
    secret_scope.set_multiplex_active(True)
    token = secret_scope.set_secret_scope({})
    try:
        monkeypatch.setenv("HERMES_HOME", str(default_home))
        assert i18n.get_language() == "fr"  # scoped miss: env ignored, this profile's config wins
        monkeypatch.setenv("HERMES_HOME", str(prof_b))
        assert i18n.get_language() == "de"  # not the first profile's cached "fr"
    finally:
        secret_scope.reset_secret_scope(token)
        secret_scope.set_multiplex_active(False)
        i18n.reset_language_cache()


# ---------------------------------------------------------------------------
# t() semantics
# ---------------------------------------------------------------------------







def test_t_missing_key_in_non_english_falls_back_to_english(tmp_path, monkeypatch):
    """If a key exists in English but not in the target locale, fall back."""
    # Stand up a fake incomplete locale under a temp locales dir.
    fake_locales = tmp_path / "locales"
    fake_locales.mkdir()
    (fake_locales / "en.yaml").write_text("foo: English Foo\n", encoding="utf-8")
    (fake_locales / "zh.yaml").write_text("# intentionally empty\n", encoding="utf-8")
    monkeypatch.setattr(i18n, "_locales_dir", lambda: fake_locales)
    i18n.reset_language_cache()
    try:
        assert i18n.t("foo", lang="zh") == "English Foo"
    finally:
        # Clear the cache on teardown so subsequent tests don't see the
        # fake "foo: English Foo" catalog instead of the real locales/*.yaml.
        i18n.reset_language_cache()




# ---------------------------------------------------------------------------
# _locales_dir resolution ladder -- regression for #23943 / #27632 / #35374.
# Sealed installs (Nix store venv, pip wheel) have no source tree next to
# agent/, so _locales_dir must resolve via env override or the data scheme.
# ---------------------------------------------------------------------------



def test_locales_dir_env_override_ignored_when_missing(tmp_path, monkeypatch):
    """A bogus HERMES_BUNDLED_LOCALES falls through to source/wheel resolution
    instead of returning a path that doesn't exist."""
    monkeypatch.setenv("HERMES_BUNDLED_LOCALES", str(tmp_path / "does-not-exist"))
    result = i18n._locales_dir()
    assert result != tmp_path / "does-not-exist"
    # In a source checkout this is the repo-root locales dir.
    assert result.name == "locales"


# ---------------------------------------------------------------------------
# {cli} — a marca do binario vem do produto, nunca do catalogo
# ---------------------------------------------------------------------------


def _catalog(monkeypatch, value: str) -> None:
    monkeypatch.setattr(i18n, "_load_catalog", lambda lang: {"probe": value})


def test_cli_placeholder_renders_the_active_brand(monkeypatch, tmp_path):
    """``{cli}`` resolve sozinho a partir de ``product_cli_name()``.

    O fork expoe ``haos`` e o catalogo guardava ``hermes`` fixo, entao a copy mandava o
    usuario rodar comandos que nao existem no appliance. O chamador nao passa ``cli``.
    """
    _catalog(monkeypatch, "run {cli} update")
    monkeypatch.setenv("HAOS_HOME", str(tmp_path / "haos"))
    assert i18n.t("probe", lang="en") == "run haos update"
    monkeypatch.delenv("HAOS_HOME")
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes"))
    assert i18n.t("probe", lang="en") == "run hermes update"


def test_value_without_the_placeholder_is_not_formatted(monkeypatch):
    """Sem ``{cli}`` o valor volta byte a byte — chaves literais inclusive.

    E o que mantem as 100+ chaves sem placeholder fora de ``str.format``; formata-las
    todas faria um ``{`` literal virar falha de format (e um WARNING por render).
    """
    _catalog(monkeypatch, "keep {this} literal")
    assert i18n.t("probe", lang="en") == "keep {this} literal"


def test_caller_supplied_cli_wins(monkeypatch):
    """``setdefault``: um ``cli=`` explicito do chamador nao e sobrescrito."""
    _catalog(monkeypatch, "run {cli}")
    assert i18n.t("probe", lang="en", cli="FORCED") == "run FORCED"


def test_home_placeholder_renders_the_products_home(monkeypatch, tmp_path):
    """``{home}`` resolve sozinho a partir de ``display_hermes_home()``.

    Os catalogos nomeavam ``~/.hermes/logs/...``. No appliance o home e ``~/.haos``, entao a
    copy mandava o usuario para um diretorio que nao existe la — e o AGENTS.md raiz proibe
    exatamente esse literal. O helper encurta um home sob ``$HOME`` de volta para ``~``, o que
    mantem o texto do upstream byte-identico.
    """
    _catalog(monkeypatch, "logs in {home}/logs")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / ".hermes"))
    assert i18n.t("probe", lang="en") == "logs in ~/.hermes/logs"


def test_caller_supplied_home_wins(monkeypatch):
    """``gateway.profile.home`` passa ``home=`` explicito — o default nao pode sobrepor."""
    _catalog(monkeypatch, "home {home}")
    assert i18n.t("probe", lang="en", home="/explicit") == "home /explicit"


def test_every_catalog_injected_key_renders_without_a_leftover_placeholder():
    """Contrato no catalogo real: toda chave que usa ``{cli}`` ou ``{home}`` resolve de fato.

    Sem a assercao de que existe pelo menos uma chave, este teste passaria vazio.
    """
    import re

    en = _flatten(_load_raw("en"))
    injected = sorted(k for k, v in en.items() if "{cli}" in v or "{home}" in v)
    assert injected, "nenhuma chave usa {cli}/{home} — o teste seria vacuoso"
    for key in injected:
        # Preenche os OUTROS placeholders da chave (ex.: {waited}) para que o format
        # chegue ao fim e o unico token em teste seja o injetado pelo loader.
        others = {
            p: "X"
            for p in re.findall(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}", en[key])
            if p not in ("cli", "home")
        }
        out = i18n.t(key, lang="en", **others)
        assert "{" not in out, f"{key}: placeholder nao resolvido em {out!r}"


