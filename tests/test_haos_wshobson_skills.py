"""Tests for wshobson/agents marketplace catalog integration in HAOS."""

import argparse
from hermes.platform.skills.wshobson_catalog import WshobsonCatalog
from tools.skills_guard import TRUSTED_REPOS
from hermes_cli.haos_cmd import cmd_haos_skills_search


def test_wshobson_trusted_repo():
    assert "wshobson/agents" in TRUSTED_REPOS
    assert "affaan-m/ECC" in TRUSTED_REPOS


def test_wshobson_catalog_search():
    catalog = WshobsonCatalog()
    results = catalog.search("kubernetes")
    assert len(results) >= 4
    names = [r.name for r in results]
    assert "k8s-manifest-generator" in names
    assert "gitops-workflow" in names

    # Search by python
    py_results = catalog.search("python")
    assert len(py_results) >= 10
    py_names = [r.name for r in py_results]
    assert "async-python-patterns" in py_names

    # Search ECC skills (agentic, tdd)
    ecc_results = catalog.search("agentic-engineering")
    assert len(ecc_results) >= 1
    assert ecc_results[0].repo == "affaan-m/ECC"


def test_wshobson_catalog_get():
    catalog = WshobsonCatalog()
    meta = catalog.get("k8s-manifest-generator")
    assert meta is not None
    assert meta.name == "k8s-manifest-generator"
    assert meta.trust_level == "trusted"
    assert "kubernetes-operations" in meta.tags
    assert meta.path == "plugins/kubernetes-operations/skills/k8s-manifest-generator"

    # Get ECC skill
    ecc_meta = catalog.get("agentic-engineering")
    assert ecc_meta is not None
    assert ecc_meta.name == "agentic-engineering"
    assert ecc_meta.repo == "affaan-m/ECC"


def test_cmd_haos_skills_search(capsys):
    args = argparse.Namespace(query="kubernetes", limit=5)
    exit_code = cmd_haos_skills_search(args)
    assert exit_code == 0
    captured = capsys.readouterr()
    assert "k8s-manifest-generator" in captured.out
    assert "CATÁLOGO DE HABILIDADES" in captured.out
