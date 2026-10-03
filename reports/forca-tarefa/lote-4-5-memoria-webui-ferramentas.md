# Relatório — lote 4-5: memória, WebUI, evals e ferramentas

## Escopo e método

- Execução: `HERMES_PYTHON=/usr/local/lib/haos-agent/venv/bin/python scripts/run_tests.sh <10 arquivos> --tb=long -v`.
- O runner descobriu **10 arquivos / ~74 testes** e executou em subprocessos isolados (`TZ=UTC`, `LANG=C.UTF-8`, `PYTHONHASHSEED=0`, `HERMES_HOME` temporário).
- Resultado observado: **22 testes falharam**, em todos os 10 arquivos; os demais passaram.
- A primeira tentativa sem `HERMES_PYTHON` foi bloqueada por ausência de venv local; a execução válida usou o venv HAOS explicitamente. Isso é limitação de ambiente, não falha do código.
- `git status`/`git diff` antes da investigação estavam limpos. Após a execução, não houve mudanças de código; somente os artefatos temporários da investigação foram removidos antes do relatório. O relatório é a única alteração intencional.

## Falhas observadas

### Memória

1. `tests/platform/memory/test_skill_promotion.py`: **9 falhas / 13 testes**.
   - Sintoma comum: `LessonSkillPromoter.run()` não cria propostas (`list_proposals()[0]` gera `IndexError`); isso derruba promoção, idempotência, deduplicação/refine, provenance e `mark_applied`.
   - Evidência representativa: `test_mark_applied_preserva_o_registro`, linha 339, `IndexError: list index out of range`.
   - Classificação: **OBSERVADO — falha funcional de geração/seleção de propostas**. Causa raiz não determinada nesta fase.

2. `tests/platform/memory/test_memory_governance.py`: **2 falhas / 12 testes**.
   - `test_licao_canonica_carrega_proveniencia_completa`: frontmatter contém apenas `title` e `tags`; falta `source_session` (e o teste exige também `destination`, `confidence`, `provenance`, `extracted_at`, `model`, `evidence_span`, `skill_afetada`).
   - `test_licao_obsoleta_reportada_e_demovida`: `list_obsolete_lessons(...)` retorna lista vazia quando o documento salvo tem `valid_to=1000.0`.
   - Classificação: **OBSERVADO — contratos de proveniência e obsolescência não atendidos**.

3. `tests/test_haos_hybrid_memory.py`: **1 falha / 3 testes**.
   - `test_okf_save_and_find_deterministic`: busca exata pelo título funciona, mas busca determinística pelo tag `revenue` retorna `None`.
   - Classificação: **OBSERVADO — busca por tag não indexa/resolve o documento salvo**.

### WebUI

4. `tests/platform/webui/test_agent_hierarchy.py`: **1 falha / 3 testes**.
   - Ao criar hierarquia com `model="m"`, `_validate_model_binding` rejeita com `HierarchyError: unknown configured model/profile 'm'` em `hermes/platform/webui/agent_hierarchy.py:181`.
   - Classificação: **OBSERVADO — fixture/contrato do teste usa binding que a validação atual considera desconhecido**. Não é possível concluir nesta etapa se o defeito é do teste, da fixture de resolução ou da implementação.

5. `tests/platform/webui/test_council_adapter.py`: **1 falha / 3 testes**.
   - Mesmo padrão: `model="mgr"` é rejeitado por `HierarchyError: unknown configured model/profile 'mgr'` em `agent_hierarchy.py:181`.
   - Classificação: **OBSERVADO — mesma incompatibilidade de binding do caminho WebUI**; causa raiz **UNVERIFICADA**.

### Evals / contratos de plataforma

6. `tests/platform/test_evals_real.py`: **3 falhas / 9 testes**.
   - Lint stdlib: imports de `hermes_constants` em `hermes/platform/decision/store.py` e `hermes/platform/evolution/rsi_loop.py` são reportados como fora da stdlib.
   - PEP-420: encontra `hermes/platform/decision/__init__.py`.
   - `run_all_real`: suite `platform.fork.structure` tem `pass_rate=0.3333333333333333` (compileall passa; `pep420` e `stdlib_lint` falham).
   - Classificação: **OBSERVADO — invariantes estruturais/lint do eval falham**.

7. `tests/platform/test_canonical_adr_contract.py`: **2 falhas / 19 testes**.
   - Mirror ausente: `.hermes/obsidian_vault/architecture/ADR-001-HAOS-MULTIAGENT-SOTA.md` (e o teste não chega a validar o segundo mirror após essa asserção).
   - PEP-420 encontra `hermes/platform/decision/__init__.py`.
   - Classificação: **OBSERVADO — contrato documental ausente e violação PEP-420**.

### Ferramentas / footguns

8. `tests/tools/test_subprocess_stdin_guard.py`: **1 falha / 4 testes**.
   - Scanner reporta `tools/environments/local_health_agent.py:71`: `subprocess.run(...)` sem `stdin=`.
   - Classificação: **OBSERVADO — chamada detectada fora do contrato do guard**.

9. `tests/scripts/test_footgun_subprocess_encoding.py`: **1 falha / 9 testes**.
   - Scan encontra match inesperado em `tools/environments/local_hermes_exec.py` (linha 36), `subprocess.run(..., text=True, ...)` sem `encoding=`; o conjunto esperado do teste não inclui esse arquivo.
   - Classificação: **OBSERVADO — footgun Windows de encoding não suprimido/corrigido**.

10. `tests/scripts/test_windows_footguns_full_repo_scan.py`: **1 falha / 1 teste**.
    - Scan real `--all`: exatamente 1 footgun em `tools/environments/local_hermes_exec.py:36`; percorreu 1648 arquivos e terminou com código 1.
    - Classificação: **OBSERVADO — falha reproduzida independentemente pelo scanner integral**.

## Comparação de estado Git

- Estado inicial observado: limpo (`git status --short` e `git diff --stat` sem saída).
- Estado final esperado deste worktree: apenas `reports/forca-tarefa/lote-4-5-memoria-webui-ferramentas.md` modificado/adicionado.
- Nenhum arquivo de produção ou teste foi alterado.

## Limitações e não conclusões

- Não houve correção nem bisect/redução de causa raiz, conforme solicitado.
- Não foi possível distinguir, para os dois casos WebUI, bug de implementação versus fixtures deliberadamente desatualizadas sem investigação adicional do resolver e do histórico.
- O runner informou `~74` testes; o resumo final confirmou 22 falhas, mas não fornece um total exato por arquivo além dos valores acima.
