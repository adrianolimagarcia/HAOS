# Plano de entrega segura — Kanban Rust no runtime

**Status:** análise/proposta, não aprovada para execução. Nenhum deploy, restart, alteração de configuração, índice Git ou runtime foi realizado. Evidência local consultada em 7 de outubro de 2026.

## Resumo executivo

O bloqueio não é apenas publicar um commit: o runtime Python carregado e o checkout de desenvolvimento divergem em ancestry e conteúdo; o venv está em modo editable apontando ao checkout externo. O binário `haos-edge` instalado expõe `kanban-select`, `kanban-claim` e `kanban-heartbeat`, mas o runtime Python não contém `kanban_rust_selector.py` nem `kanban_rust_claim.py`. Portanto os subcomandos disponíveis no binário não provam que o dispatcher runtime os usa.

Recomendação: preservar primeiro deltas exclusivos e estado do runtime em snapshot/ramo privado de salvamento, sem segredos/artefatos sensíveis; depois preparar release durável separada, baseada em ancestry aprovado e commit revisado, incorporando somente o Kanban requerido. Não atualizar diretamente `/usr/local/lib/haos-agent` de estado sujo, nem substituir checkout por `origin/main`/workspace inteiro: isso pode descartar fixes ativos ou introduzir features não avaliadas.

## Evidência observada

### Git e ancestry real

- Workspace: `7e0ca7cd5a7e523c4b566e91eb1d730d489bbd76`, limpo na leitura.
- Runtime `/usr/local/lib/haos-agent`: `282ff112a1bcf02172ff940480c1c7a9cd201929`, com 17 arquivos modificados e 3 untracked.
- `origin/main` obtido via `git ls-remote`: `a00371ba59b09ec28f0f54bcdfd39f77fc51ea01`.
- Runtime: `merge-base(HEAD, origin/main)=0d7e13f147c3b0723761d7c3b8c28aa5f264699a`; runtime tem 3 commits exclusivos e remote 88 exclusivos após essa base. As contagens foram corroboradas por rev-list/log, não inferidas de tracking status.
- Workspace e remote têm base comum `4088f295c3a1e904885c5ed4c046725f8af8f401`; workspace tem 5 commits próprios e remote 3 próprios após essa base. Runtime e workspace não são ancestry direta um do outro. O merge-base cruzado não foi calculável no workspace sem buscar/importar objeto runtime; não inferir relação adicional.
- Commits exclusivos do runtime: `71235d3ae7` (reverse-bot test), `442f4b73b6` (bump 0.21.101→0.21.102) e `282ff112a1` (dedup/promotion gate/context limits). `71235d3ae7` tem alteração funcionalmente representada no workspace por `f8e445ce71`, mas não é o mesmo commit/diff idêntico. `promotion_gate.py` e `test_dream_sanitization.py` introduzidos no runtime não existem no workspace atual. Não declarar esses commits “já contidos em main” sem reconciliação de patch/semântica.
- Remote a003 contém merge `cee88337e9` da fase Kanban Rust, bump de versão e `b6908ead0f` (adoção de claim Rust concluído após timeout/fallback). Não são ancestry do runtime HEAD.

### Delta runtime não commitado (inventário sem segredos)

- `git diff --stat`: 17 arquivos, 925 inserções / 50 remoções.
- Categorias: loop/contexto/agente/delegate; civilization routing/delegation; memória temporal/governança/reconciliação; engine/store/score de decisão; testes correspondentes.
- Untracked: `gateway/shutdown_forensics.py.bak-20260913-systemd-scope` (backup antigo, não publicar sem triagem), `hermes/platform/decision/jev_scorer.py` e `tests/platform/test_jev_scorer.py` (os dois últimos byte-idênticos aos correspondentes do workspace).
- Comparação runtime working tree vs workspace: vários arquivos coincidem, incluindo `loop_hygiene.py`, `tool_executor.py` e módulos/testes de memória/decisão; divergências relevantes em `agent/context_compressor.py`, `hermes/platform/civilization/delegation.py`, `hermes/platform/context/memory/schemas.py`, `tools/delegate_tool.py` e teste de routing. Há ainda promotion gate do runtime ausente no workspace. Não apagar nem assumir equivalência pelo nome de commit.
- Não foi gravado conteúdo de diffs ou valores de config; só categorias e diffstat.

### Fonte carregada, homes e units

- `/usr/local/lib/haos-agent/venv/bin/python3` importa `hermes_cli` de `/run/media/adriano/.../HERMES-TURBO/hermes_cli/__init__.py`; metadata: `hermes-agent 0.21.102`.
- `.pth` editable e finder em site-packages apontam dezenas de módulos (`hermes_cli`, `gateway`, `agent`, `tools`, `hermes.platform`) diretamente ao workspace. O runtime depende, portanto, de source fora do checkout de deploy e em mídia removível.
- `haos-gateway.service` usa esse venv e `/root/.haos`; PID 892918 estava ativo com `python -m hermes_cli.main gateway run`, cwd `/root/.haos`, `HERMES_HOME=/root/.haos`. `haos-autonomy.service` usa venv e declara `HAOS_HOME` + `HERMES_HOME` como `/root/.haos`. Dashboard também usa venv e declara ambos, mas não estava ativo. `haos-webui.service` usa agent-dir/runtime e esse venv. `haos-stt.service` referencia script do checkout runtime/venv.
- `haos-edge.service` e `haos-controlplane-edge.service` executam `/usr/local/bin/haos-edge`; controlplane referencia static-dir sob `/usr/local/lib/haos-agent`. São consumers do artefato/config, não prova de import ou uso do Kanban.
- `hermes-agent-bridge.service` executa Node de `hermes-studio`, não o bridge Python do dispatcher.
- Regra: tratar `HAOS_HOME` e `HERMES_HOME` como variáveis distintas em cada teste/unit; observar valores efetivos. Nem todo unit explicita as duas. Há referência de state em `/root/.hermes` enquanto gateway unit/processo mostra `/root/.haos`; reconciliar qual home governa cada recurso antes de alterar units.
- Inventário de units acima é parcial; determinar todos os consumers do venv/checkout é gate antes da mudança.

### Estado Kanban observado

- `/usr/local/bin/haos-edge` versão observada `0.1.0` e expõe selector/claim/heartbeat; o binário de workspace também mostra selector.
- Runtime Python não contém `hermes_cli/kanban_rust_selector.py` nem `hermes_cli/kanban_rust_claim.py`; workspace contém ambos e testes do selector. Binário sozinho não integra dispatcher.
- Runbook local define selector read-only, Python como único scheduler/writer, modos off/shadow/rust e fallback. Isso é documentação, não prova de config efetiva atual. Commit upstream de claim trata timeout/fallback; comportamento do binário instalado e do cliente em conjunto precisa ser testado antes de ativar writes.

## RCA e bloqueios

1. **Source drift via editable install:** venv supostamente runtime resolve imports no workspace externo. Atualizar apenas checkout runtime não define o código importado; desmontar/alterar workspace pode afetar processo vivo.
2. **Ancestry divergente + árvore runtime suja:** runtime guarda commits e deltas próprios enquanto upstream tem 88 commits posteriores; pull/reset ingênuo pode perder fixes. Promoção integral do workspace mistura features não avaliadas.
3. **Desalinhamento binário/Python:** subcomandos Rust existem, mas cliente Python não existe no runtime. Não há prova de dispatcher usando Rust; claim ativo pode duplicar estado se fallback/timeout não reconciliados.
4. **Homes e dependências unit incompletas:** diferença `HAOS_HOME`/`HERMES_HOME` e consumers não exaustivamente mapeados impedem cutover seguro ainda.

## Opções e recomendação

- **Snapshot/ramo de salvamento do runtime:** obrigatório primeiro, apenas preservação. Guardar HEAD, patches e untracked aprovados em armazenamento privado; excluir `.env`, cookies, tokens, DB/state, logs e backups sem triagem. Validar hashes e restauração em clone isolado. Não limpar nem resetar runtime.
- **Release durável separada:** recomendada depois da reconciliação. Base aprovada, commits Kanban seletivos/revisados, commits separados para selector read-only e claim/write. Preservar autoria quando possível. Não incluir JEv/GBrain/forensics ou outros deltas em avaliação. Produzir package/venv e binário independentes do checkout dev, com commit/version/hash manifestados.
- **Promover workspace inteiro ou resetar runtime para a003:** rejeitado; mistura features não avaliadas e pode perder fixes locais.
- **Manter editable para checkout de desenvolvimento:** não é release reproduzível; apenas laboratório temporário explicitamente controlado.

## Etapas reversíveis e gates

### A — baseline e preservação, sem deploy

1. Capturar HEAD, hash remoto, status, diffstat e manifest dos untracked. Patch binário só em local privado, após revisão; nunca exportar segredos/state.
2. Criar ramo local privado de salvamento a partir do runtime HEAD; preservar untracked aprovados separadamente sem limpar árvore. Validar hashes e restauração numa cópia isolada.
3. Registrar `ExecStart`, `WorkingDirectory`, `Environment` redigido, PID, interpreter/import paths e versão dos units consumidores; capturar baseline Kanban sem mutações.
4. **Gate A:** snapshot recuperável; cada delta classificado como preservar, comprovadamente equivalente, aprovado para descarte ou pendente. Nada inexplicado é removido.

### B — reconciliação seletiva

1. Em clone/worktree de integração isolado, comparar commits por patch-id/diff e conteúdo: idêntico, equivalente reescrito, parcial, ausente ou conflito. Não usar título/patch-stat como prova.
2. Revisar deltas runtime por domínio; separar fixes críticos do escopo Kanban. Decidir explicitamente status de promotion gate/context/delegation. Selecionar apenas changes necessários à entrega.
3. Verificar config/mode real, owner único do scheduler, fallback, timeout, caminho/hash de binário e schema/lease. Não alterar config aqui.
4. **Gate B:** release commit list/hash revisados; todo delta runtime explicado; contrato de claim/fencing e timeout aprovado. Se não resolvido, manter claim Rust desligado.

### C — artefato durável e testes

1. Criar release Python não-editable, em diretório versionado imutável, e binário Rust correspondente. Nenhum import deve resolver no workspace de dev. Se editável for requisito, apontar só a checkout versionado imutável sob `/usr/local/lib`, documentando exceção.
2. Testar selector Rust, testes Python Kanban/dispatcher e regressão. Em SQLite temporário: concorrência de dois dispatchers, timeout após claim, fallback, retry/review, lease expirado, profiles A→B→A, home isolada. Provar que fallback não faz segunda claim ambígua.
3. **Smoke com novo interpreter:** executar subprocesso com `HAOS_HOME` e `HERMES_HOME` definidos explicitamente e testados separadamente; conferir `sys.executable`, metadata version, `hermes_cli.__file__`, módulos Kanban e ausência do workspace dev. Testar A→B→A e confirmar isolamento de DB/config/credentials.
4. **Gate C:** saídas reais de testes registradas; pacote reproduzível; import paths, versão/hash Python/Rust e home isolation validados; nenhum segredo em logs.

### D — shadow e canário read-only

1. Resolver scheduler owner. Shadow apenas compara seleção sem writes/spawn; registrar discrepâncias, timeout e fallback. Não habilitar selector Rust até paridade de seleção/ordem/reasons e revalidação Python provadas.
2. Canário por perfil/board isolado; Python continua claims/spawn. Observar logs, DB integrity, locks e candidate deltas. Selector shadow não autoriza claims Rust.
3. **Gate D:** discrepâncias explicadas/aceitas, sem erros de home/path; rollback read-only ensaiado.

### E — claim/write (aprovação independente)

1. Exigir writer único por DB/board, transação/CAS compatível, lease + fencing token, request ID idempotente e reconciliação de resultado ambíguo. Timeout não prova que a claim falhou; fallback não tenta claim Python até reconciliar.
2. Canary de escrita em perfil/board não crítico somente com autorização humana, medindo claims ambíguas, duplicadas, expiradas, fallback e paridade de task_runs/eventos.
3. **Gate E:** concurrency/fault injection, rollback/drain e ausência de claims duplicadas/perdidas demonstrados; aprovação humana. Até lá, selector off/shadow e writer Python.

## Cutover e smoke ao vivo — futuro, com autorização

1. Arquivar config vigente e checksums/versões do venv/release/binário; anotar unit/PID/imports. Redigir segredos; não copiar config entre homes automaticamente.
2. Validar `HAOS_HOME` e `HERMES_HOME` por unit/processo, seus configs/DB/credentials/profile. Resolver alias ou diferença antes do cutover.
3. Instalar release em diretório novo; executar smoke em DB temporária com interpreter novo. Mudar um escopo/unit por vez; desativar writer antigo apenas no board/profile canário e confirmar fence antes do Rust.
4. Smoke pós-cutover: unit healthy, import só do release versionado, homes corretos; selector em DB descartável com conjunto/ordem esperados; no claim canário autorizado, uma task de teste é claimada uma vez e heartbeat/complete preservam runs/eventos sem fallback duplicado. Não usar DB real de produção para smoke mutável sem autorização explícita.
5. Promover só após janela observada e aprovação.

## Rollback

- **Config:** restaurar snapshot exato de config/drop-ins e validar `HAOS_HOME` e `HERMES_HOME` separadamente; não reverter dados junto com config.
- **Venv/source:** voltar a apontamento da unit para release imutável anterior (troca atômica de symlink/selector se disponível). Não usar editable para checkout dev como rollback permanente. Se restaurar venv, validar interpreter/imports em subprocess antes do tráfego.
- **Binário:** restaurar artefato anterior com checksum, compatível com a Python bridge; manter selector off/Python-only se versões não compatíveis.
- **Claims:** parar novas admissions, drenar/conservar workers existentes, reconciliar task_runs/token/PID de claims ambíguas; não limpar locks/leases à força nem retry cego. Fencing do writer Rust antes de reativar Python.
- Retomar somente com unit healthy, paths/versões/homes esperados, DB íntegra, sem runs duplicadas/perdidas e scheduler único. Se faltar evidência, manter dispatch pausado e escalar; preservar logs redigidos e snapshot.

## Pendências e riscos a reconciliar

- Comparar diff exato e comportamento do cliente Kanban da release a003 com binário instalado; source commit/hash do binário instalado não foi provado.
- Identificar todos os consumers do venv/checkout e compatibilidade de cada unit; inventário atual parcial.
- Resolver referência `/root/.hermes` versus unit/processo `/root/.haos`; confirmar home canônica por recurso.
- Revisar delta runtime completo em ambiente apropriado e decidir destino dos fixes exclusivos, principalmente promotion gate/context/delegation. Diffstat não prova semântica.
- Reconfirmar hash remoto na janela futura de release.
- Confirmar config efetiva Kanban; documentação diz selector default off, mas valor runtime não foi verificado.
- Validar dependências, assets, subprocessos/plugins e cache dos processos long-lived; import smoke isolado não cobre tudo.
- Equivalência de commits/deltas ainda pendente; resolver com patch-id e testes direcionados antes de integrar.

## Limites

Análise read-only; esta página é a única escrita. Não foram executados testes, builds, snapshot, deploy, restart ou alterações em config/units/binário. Observações refletem leituras desta sessão; equivalência semântica, configuração Kanban efetiva e segurança do release permanecem pendentes.
