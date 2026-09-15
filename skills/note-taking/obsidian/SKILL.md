---
name: obsidian
description: Read, search, create, and edit notes in the Obsidian vault.
version: 1.0.0
author: Teknium (teknium1), Hermes Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [Obsidian, Notes, Markdown, Vault]
    related_skills: []
---

# Obsidian Vault

Use this skill for filesystem-first Obsidian vault work: reading notes, listing notes, searching note files, creating notes, appending content, and adding wikilinks.

## Vault path

**No HAOS o vault canônico é `<HERMES_HOME>/obsidian_vault`** (aqui: `/root/.haos/obsidian_vault`);
o OKF é `<HERMES_HOME>/okf`. Isso é definido no código
(`hermes/platform/context/memory/provider.py`, `hermes/platform/memory/dream.py`,
`hermes/platform/memory/haos_memory_sync.py`) — não é convenção de usuário.

**Pitfall que já custou um split-brain (2026-09-11)**: escrever em `<home>/vault` (sem o prefixo
`obsidian_`) cria um vault que **nenhum leitor do HAOS enxerga** — as tools de memória, o hybrid
router e o dream resolvem tudo por `obsidian_vault`. Confirme o caminho antes de criar nota
(`ls <home>/obsidian_vault`).

Fora do HAOS, a convenção documentada é a env `OBSIDIAN_VAULT_PATH` (ex.: de
`${HERMES_HOME:-~/.hermes}/.env`); sem ela, `~/Documents/Obsidian Vault`.

File tools do not expand shell variables. Do not pass paths containing `$OBSIDIAN_VAULT_PATH` to `read_file`, `write_file`, `patch`, or `search_files`; resolve the vault path first and pass a concrete absolute path. Vault paths may contain spaces, which is another reason to prefer file tools over shell commands.

If the vault path is unknown, `terminal` is acceptable for resolving `OBSIDIAN_VAULT_PATH` or checking whether the fallback path exists. Once the path is known, switch back to file tools.

## Read a note

Use `read_file` with the resolved absolute path to the note. Prefer this over `cat` because it provides line numbers and pagination.

## List notes

Use `search_files` with `target: "files"` and the resolved vault path. Prefer this over `find` or `ls`.

- To list all markdown notes, use `pattern: "*.md"` under the vault path.
- To list a subfolder, search under that subfolder's absolute path.

## Search

Use `search_files` for both filename and content searches. Prefer this over `grep`, `find`, or `ls`.

- For filenames, use `search_files` with `target: "files"` and a filename `pattern`.
- For note contents, use `search_files` with `target: "content"`, the content regex as `pattern`, and `file_glob: "*.md"` when you want to restrict matches to markdown notes.

## Create a note

Use `write_file` with the resolved absolute path and the full markdown content. Prefer this over shell heredocs or `echo` because it avoids shell quoting issues and returns structured results.

## Append to a note

Prefer a native file-tool workflow when it is not awkward:

- Read the target note with `read_file`.
- Use `patch` for an anchored append when there is stable context, such as adding a section after an existing heading or appending before a known trailing block.
- Use `write_file` when rewriting the whole note is clearer than constructing a fragile patch.

For an anchored append with `patch`, replace the anchor with the anchor plus the new content.

For a simple append with no stable context, `terminal` is acceptable if it is the clearest safe option.

## Targeted edits

Use `patch` for focused note changes when the current content gives you stable context. Prefer this over shell text rewriting.

## Wikilinks

Obsidian links notes with `[[Note Name]]` syntax. When creating notes, use these to link related content.

## HAOS: ADRs, OKF e as tools de memória (preferir as tools)

No HAOS não escreva o arquivo à mão quando existir tool — as tools gravam no caminho canônico e
sincronizam os stores de memória:

| objetivo | tool | observação |
|---|---|---|
| gravar/atualizar nota no vault | `obsidian_save_note(title, content, folder)` | `folder="adrs"` para ADRs |
| ler uma ADR canônica | `obsidian_get_adr(adr_id)` | ex.: `ADR-001` |
| consultar conhecimento canônico (OKF) | `haos_hybrid_memory_query(query, mode="okf")` | resposta determinística, com fonte |
| gravar spec/contrato canônico | `haos_okf_save_document(...)` | OKF em `<home>/okf` |
| recall relacional do histórico | `mcp__graphrag__recall(pergunta)` | ver skill `hermes-graph` |

**Convenções do vault canônico:**

- ADR: `adrs/ADR-###-slug.md`, com linha `**Status:** Aceito|Proposto|Substituído`. ADR aceita é
  **imutável** — correção vira ADR novo, nunca edição da história.
- Diário: `diario/YYYY-MM-DD.md` — gravado automaticamente pela manutenção diária (04:30); não
  duplicar à mão.
- O vault entra no corpus do GraphRAG como perfil `vault` (via `refresh.sh`); nota nova só é
  pesquisável depois do próximo refresh.
