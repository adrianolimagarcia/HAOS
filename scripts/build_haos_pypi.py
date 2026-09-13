#!/usr/bin/env python3
"""Build and packaging script for publishing HAOS to PyPI.

Prepares:
- Standalone wheel (haos-X.Y.Z-py3-none-any.whl)
- Source distribution (haos-X.Y.Z.tar.gz)

Usage:
  python3 scripts/build_haos_pypi.py [--check] [--upload] [--test-pypi]
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_DIST_DIR = _ROOT / "dist"
_PACKAGES_DIR = _ROOT / "packages" / "haos"


def build_package(clean: bool = True) -> Path:
    """Builds the HAOS distribution package into dist/."""
    if clean and _DIST_DIR.exists():
        print(f"🧹 Limpando {_DIST_DIR}...")
        shutil.rmtree(_DIST_DIR)
    _DIST_DIR.mkdir(parents=True, exist_ok=True)

    print("📦 Construindo pacote HAOS para PyPI...")

    env = dict(os.environ)
    env["HAOS_BUILD"] = "1"
    env["HAOS_PYPI_BUILD"] = "1"

    # Executa build utilizando python -m build apontando para packages/haos
    cmd = [
        sys.executable,
        "-m",
        "pip",
        "wheel",
        "--no-deps",
        "-w",
        str(_DIST_DIR),
        str(_PACKAGES_DIR),
    ]

    print(f"Executando: {' '.join(cmd)}")
    res = subprocess.run(cmd, cwd=str(_ROOT), env=env)
    if res.returncode != 0:
        # Fallback para setuptools direto
        print("Tentando fallback via setuptools bdist_wheel...")
        cmd_fallback = [
            sys.executable,
            "-m",
            "pip",
            "install",
            "wheel",
            "build",
        ]
        subprocess.run(cmd_fallback, cwd=str(_ROOT), env=env)
        cmd_build = [
            sys.executable,
            "-m",
            "build",
            str(_PACKAGES_DIR),
            "--outdir",
            str(_DIST_DIR),
        ]
        res = subprocess.run(cmd_build, cwd=str(_ROOT), env=env)

    wheels = list(_DIST_DIR.glob("*.whl"))
    print(f"✅ Artefatos gerados em {_DIST_DIR}:")
    for artifact in _DIST_DIR.iterdir():
        print(f"  • {artifact.name} ({artifact.stat().st_size / 1024:.1f} KB)")

    return _DIST_DIR


def check_artifacts():
    """Valida artefatos gerados via twine check."""
    print("🔍 Validando artefatos via twine check...")
    try:
        res = subprocess.run(
            [sys.executable, "-m", "twine", "check", f"{_DIST_DIR}/*"],
            capture_output=True,
            text=True,
        )
        print(res.stdout)
        if res.returncode == 0:
            print("✅ Verificação de integridade aprovada!")
        else:
            print(f"⚠️ Alerta na verificação: {res.stderr}")
    except FileNotFoundError:
        print("ℹ️ 'twine' não encontrado no PATH. Instale com 'pip install twine' para validação estrita.")


def upload_artifacts(test_pypi: bool = False):
    """Envia os pacotes para o PyPI ou TestPyPI."""
    repo_flag = ["--repository", "testpypi"] if test_pypi else []
    target = "TestPyPI" if test_pypi else "PyPI Oficial"
    print(f"🚀 Publicando artefatos no {target}...")

    cmd = [sys.executable, "-m", "twine", "upload"] + repo_flag + [f"{_DIST_DIR}/*"]
    subprocess.run(cmd)


def main():
    parser = argparse.ArgumentParser(description="Builder de pacote PyPI do HAOS")
    parser.add_argument("--check", action="store_true", help="Executa validação twine após o build")
    parser.add_argument("--upload", action="store_true", help="Faz upload para o PyPI oficial após o build")
    parser.add_argument("--test-pypi", action="store_true", help="Faz upload para o TestPyPI")
    args = parser.parse_args()

    build_package()

    if args.check:
        check_artifacts()

    if args.upload:
        upload_artifacts(test_pypi=args.test_pypi)


if __name__ == "__main__":
    main()
