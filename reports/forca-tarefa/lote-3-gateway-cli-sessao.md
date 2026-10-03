# Lote 3 — gateway, CLI e sessão

**Data da execução:** 2026-09-24  
**HEAD observado no worktree:** `d689488c0e test: verify isolated delegation artifact delivery`  
**Escopo:** somente execução e triagem; nenhum código de produção/teste foi alterado.

## Limitação de ambiente

`./scripts/run_tests.sh` não pôde iniciar no worktree porque não havia um venv local com `pytest`:

```text
error: no virtualenv with pytest found in .../.worktrees/.../.venv or .../venv
```

Esse resultado é **BLOQUEIO DE AMBIENTE**, não falha de código. Conforme orientação, os testes foram executados com o interpretador já existente no workspace principal:

```text
/run/media/adriano/e681b5ac-a4fb-44d4-aebf-9d6584065787/dsh-projetos/HERMES-TURBO/.venv/bin/python -m pytest
```

A execução agrupada foi: **16 failed, 256 passed, 1 skipped**, em 135,95 s. Para obter evidência por arquivo, cada arquivo foi reexecutado isoladamente com `--tb=short`.

## Resultado por arquivo

| Arquivo | Resultado | Evidência observada | Classificação |
|---|---:|---|---|
| `tests/gateway/test_failure_writer_ownership.py` | 1 failed, 4 passed | `test_gateway_failure_writer_preserves_accepted_turn_identity`: esperava 20 itens, observou 3 | **FALHA REPRODUZIDA**; causa não estabelecida |
| `tests/gateway/test_platform_base.py` | 2 failed, 104 passed, 1 skipped | dois testes de sandbox/media aceitaram `/root/.hermes/auth.json`, onde esperavam `None` | **FALHA REPRODUZIDA**; causa não estabelecida |
| `tests/hermes_cli/test_approvals_suggest.py` | 1 failed, 14 passed | normalização retornou `git checkout -- /root/project/file.txt`, não contendo `~/project/file.txt` | **FALHA REPRODUZIDA**; causa não estabelecida |
| `tests/hermes_cli/test_compat_manifest_targets.py` | 1 failed, 1 passed | ponteiro `hermes_state:SCHEMA_VERSION` resolveu `hermes_state_replay`, divergindo de `hermes_state_common` | **FALHA REPRODUZIDA**; causa não estabelecida |
| `tests/hermes_cli/test_model_alias_credentials.py` | 1 failed, 58 passed | teste encontrou escritas `DIRECT_ALIASES.clear/update` no loader `_ensure_direct_aliases`; a busca também capturou worktrees presentes no checkout | **FALHA REPRODUZIDA**, com possível interferência do escopo de busca; causalidade **não estabelecida** |
| `tests/hermes_cli/test_doctor_command_install.py` | 3 failed, 1 passed | mensagens esperadas de symlink/venv/Termux não apareceram | **FALHA REPRODUZIDA**; causa não estabelecida |
| `tests/hermes_cli/test_doctor.py` | 4 failed, 73 passed | hints de `image_gen`, Termux, Codex e TCC/macOS divergiram; um caso teve `StopIteration` | **FALHA REPRODUZIDA**; causa não estabelecida |
| `tests/test_dispatch_session_id.py` | 3 failed, 1 passed | `session_id`/`task_id` esperados não foram encaminhados (`None`/dicionário vazio) | **FALHA REPRODUZIDA**; causa não estabelecida |

## O que não foi concluído

- Não foi possível atribuir causalidade a commit, alteração local ou regressão apenas com a execução dos testes.
- Não foram feitas correções nem tentativas de “consertar” os testes.
- Não foi feita execução válida via `scripts/run_tests.sh` por falta do ambiente exigido no worktree; portanto os resultados acima são testes diretos com o venv do checkout principal, não um recibo completo do runner isolado.

## Estado Git e comparação

No worktree isolado, antes do relatório:

```text
## hermes-subagent/subagent-sa-1-7532c090
```

Não havia diff de código. Após a criação deste relatório, o único arquivo novo esperado é:

```text
?? reports/forca-tarefa/lote-3-gateway-cli-sessao.md
```

O checkout principal foi observado como já sujo, com `packages/hermes-webui-edge/src/main.rs` modificado e diversos arquivos não rastreados, incluindo os documentos de planejamento/triagem. Essas alterações são **externas a este worktree e não foram modificadas nem atribuídas às falhas**.

## Conclusão

Há 16 falhas reproduzíveis nos oito arquivos-alvo quando executados com o venv principal. A única conclusão causal segura é que as expectativas falham contra o estado de código testado; a relação com mudanças locais, worktrees auxiliares ou commits específicos permanece **UNVERIFIED**. O bloqueio do `scripts/run_tests.sh` no worktree é ambiental e separado das falhas de asserção.
