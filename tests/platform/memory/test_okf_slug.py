"""Testes do slug de nome de arquivo do OKF.

Regressão: o slug tratava cada caractere acentuado como separador, então
"Medição de recuperação" virava `medi-o-de-recupera-o` — ilegível e sem relação
com o título. O correto é transliterar para ASCII (ç→c, ã→a).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from hermes.platform.memory.okf import OKFStore, _slugify  # noqa: E402


@pytest.mark.parametrize(
    "titulo,esperado",
    [
        # o caso que motivou o conserto: acentos viram letra, não hífen
        (
            "Medição de recuperação: zero silencioso e controle obsoleto",
            "medicao-de-recuperacao-zero-silencioso-e-controle-obsoleto",
        ),
        ("Medição", "medicao"),
        ("recuperação", "recuperacao"),
        ("Ação e coração", "acao-e-coracao"),
        ("Índice denso", "indice-denso"),
        ("Ünïcödé", "unicode"),
        ("ÃÉÎÕÜ Ç", "aeiou-c"),
        # sem acento continua igual ao comportamento antigo
        ("plain ascii title", "plain-ascii-title"),
        ("already-slugged", "already-slugged"),
        ("under_score kept", "under_score-kept"),
        # pontuação e espaços colapsam em hífen único...
        ("Pontuação! (parênteses) [colchetes]", "pontuacao-parenteses-colchetes"),
        # ...mas hífen LITERAL é preservado (comportamento pré-existente, fora do
        # escopo do conserto dos acentos: o título tinha 3 hífens, saem 3 + os 2
        # que separam as palavras).
        ("Multi   space --- dash", "multi-space-----dash"),
        # bordas
        ("   ", ""),
        ("---", ""),
        ("!!!", ""),
    ],
)
def test_slugify_translitera_acentos(titulo: str, esperado: str) -> None:
    assert _slugify(titulo) == esperado


def test_slugify_nao_deixa_hifen_solto_entre_letras() -> None:
    """O defeito antigo produzia hífen onde havia letra acentuada."""
    antigo = "medi-o-de-recupera-o"
    assert _slugify("Medição de recuperação") != antigo
    assert "--" not in _slugify("Medição de recuperação")


def test_save_document_usa_slug_transliterado(tmp_path: Path) -> None:
    store = OKFStore(tmp_path)
    doc = store.save_document(
        title="Medição de recuperação: caso real",
        content="# corpo\n\ntexto",
        doc_type="runbook",
        tags=["medicao"],
    )
    assert doc.relative_path == "medicao-de-recuperacao-caso-real.md"
    assert (tmp_path / "medicao-de-recuperacao-caso-real.md").exists()
    # o título original (com acento) sobrevive no frontmatter
    assert doc.metadata["title"] == "Medição de recuperação: caso real"


def test_save_document_com_folder(tmp_path: Path) -> None:
    store = OKFStore(tmp_path)
    doc = store.save_document(
        title="Lição: índice denso",
        content="corpo",
        folder="licoes",
    )
    assert doc.relative_path == "licoes/licao-indice-denso.md"
    assert (tmp_path / "licoes" / "licao-indice-denso.md").exists()


def test_filename_explicito_tem_precedencia(tmp_path: Path) -> None:
    store = OKFStore(tmp_path)
    doc = store.save_document(
        title="Medição acentuada",
        content="corpo",
        filename="nome-fixo.md",
    )
    assert doc.relative_path == "nome-fixo.md"
