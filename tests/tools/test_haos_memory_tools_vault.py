"""Contrato do vault canônico das tools de memória do HAOS.

Leitor (``obsidian_get_adr``) e escritor (``obsidian_save_note``) DEVEM operar no
MESMO diretório — o vault canônico ``<hermes_home>/obsidian_vault``, que é o lido
por ``tools/context_expand_tool.py``, ``plugins/haos/dashboard/plugin_api.py`` e
``hermes/platform/context/memory/provider.py``.

O bug que estes testes travam: as tools apontavam para ``<hermes_home>/vault``,
um diretório divergente. Como ``ObsidianAdapter.available()`` é fail-closed
(exige ao menos um ``.md``), o leitor respondia "Obsidian Vault não disponível"
enquanto o escritor gravava notas órfãs nesse diretório fantasma — falso sucesso
que o agente nunca conseguiria ler de volta.
"""

import json
from pathlib import Path

from hermes_constants import get_hermes_home
from tools.haos_memory_tools import obsidian_get_adr, obsidian_save_note


def test_save_note_escreve_no_vault_canonico():
    home = Path(get_hermes_home())

    res = json.loads(obsidian_save_note("Contrato Vault", "conteudo", "notas"))

    assert res["success"] is True
    written = Path(res["path"])
    assert written == home / "obsidian_vault" / "notas" / "Contrato Vault.md"
    assert written.read_text(encoding="utf-8") == "conteudo"
    # O diretório divergente não pode ser usado como vault.
    assert not (home / "vault").exists()


def test_get_adr_le_o_vault_canonico():
    home = Path(get_hermes_home())
    adrs = home / "obsidian_vault" / "adrs"
    adrs.mkdir(parents=True, exist_ok=True)
    (adrs / "ADR-007-contrato.md").write_text(
        "# ADR 007 - contrato do vault\nstatus: accepted\n", encoding="utf-8"
    )

    res = json.loads(obsidian_get_adr("ADR-007"))

    assert res.get("found") is True, res
    assert "contrato do vault" in json.dumps(res)


def test_roundtrip_escrita_e_leitura_no_mesmo_vault():
    """O que o escritor grava, o leitor encontra (mesmo vault)."""
    home = Path(get_hermes_home())

    saved = json.loads(obsidian_save_note("ADR-042-roundtrip", "corpo do adr", "adrs"))
    assert saved["success"] is True

    found = json.loads(obsidian_get_adr("ADR-042"))
    assert found.get("found") is True, found
    assert "corpo do adr" in json.dumps(found)
    assert Path(saved["path"]).is_relative_to(home / "obsidian_vault")
