# Plano verificável: corrigir isolamento do terminal e recuperar o pai

**Status:** proposta operacional/técnica; não implementada nem implantada por este documento.
**Escopo:** corrigir vazamento de marcadores de delegação para snapshots bash persistidos, proteger a autoridade parent/child Kanban e restaurar com segurança a sessão pai já contaminada.
**Restrições:** não executar limpeza/deploy, não remover guardas de autorização, não usar `env -u`, não alterar configs/runtime neste trabalho de planejamento.

## Objetivo e critérios de conclusão

Após o patch, uma sequência real pai → child → pai no mesmo `LocalEnvironment` deve provar simultaneamente: (a) o child mantém a negação de mutação no board de origem; (b) o comando subsequente do pai não herda `HERMES_DELEGATED_CHILD_CONTEXT` nem identidade/claim do child e mantém shell state legítimo; (c) snapshots recém-escritos não contêm marcadores proibidos; (d) snapshots antigos não conseguem reintroduzir esses marcadores quando carregados; (e) configurações/autoridade legítimas do dispatcher e das ferramentas não são perdidas; (f) restart, falha de gravação e execução concorrente não publicam estado parcial nem apagam snapshot bom.

Encerrar somente com evidência anexada para cada gate abaixo, aprovação de segurança independente, canary do pai aprovado pelo dono do board e rollback praticável. Resultados e status pertencem ao harness/Kanban; não aceitar relatório textual de agente como prova.

## Evidência e incerteza

### Observado no repositório e ambiente de inspeção

- `tools/environments/base_session_env.py:49-78` constrói a remoção de variáveis antes de `export -p`; :80-104 monta bootstrap e :123-157 o wrapper que regrava snapshot via arquivo temporário e `mv`. O comportamento funciona tanto na criação como na atualização do snapshot.
- `tools/environments/base.py:165-178,268-285,327-333` resolve o caminho do snapshot por sessão e inicializa o snapshot no `init_session`; `tools/environments/local.py:871-899` prefere cache no `HERMES_HOME/cache/terminal` quando disponível. Portanto um novo snapshot limpo não reescreve automaticamente snapshots antigos até o próximo dump bem-sucedido.
- `agent/delegation_context.py:15-25,28-45,76-80,91-107,110-131,142-153` separa contexto in-process de marcador de processo, define `KANBAN_ENV_KEYS` (inclui também `HERMES_KANBAN_GOAL_MODE` e `HERMES_KANBAN_GOAL_MAX_TURNS`) e propaga fence para descendentes. A fence é deliberada; `scrub_kanban_env` retira identidade de worker e mantém o marcador/board de origem.
- `tools/environments/local.py:216-233,267-276` injeta contexto de sessão e aplica guardas/scrub em env de processos. `hermes_cli/kanban.py:215-239` nega ações mutáveis ao child se o caminho do board estiver fenced; `agent/delegation_context.py:110-131` é a verificação de caminho.
- O candidato `4a0f0f47565cfa3b92a9af96b59a1ddcda1a5a44` afeta `base_session_env.py` e dois testes. Ele acrescenta o regex para `HERMES_DELEGATED_CHILD_CONTEXT`, `HERMES_KANBAN_TASK`, `HERMES_KANBAN_RUN_ID`, `HERMES_KANBAN_CLAIM_LOCK` e os desexporta durante o dump. É um candidato, não autorização de produção.
- Nos snapshots existentes sob `/root/.haos/cache/terminal/`, encontrei linhas `declare -x HERMES_DELEGATED_CHILD_CONTEXT="/root/.haos"`; não encontrei `HERMES_KANBAN_*` nos mesmos snapshots inspecionados. São artefatos observados neste ambiente, não prova de inventário completo. A correção de geração não remove snapshots existentes automaticamente.
- O teste adicionado em `tests/tools/test_terminal_snapshot_delegation_marker_isolation.py` invoca `LocalEnvironment` no mesmo processo Python onde entra `delegated_child_context()`. O wrapper local usa env de processo; porém o `ContextVar` não é automaticamente herdado por outro thread. O teste não demonstra por si só que o comando shell child recebeu o marcador. A execução de teste registrada por terceiros deve ser recuperada/verificada no runner canônico antes de considerar o gate aprovado.
- Há uma inconsistência a esclarecer antes de merge: `base_session_env.py` mantém comentário sobre regex/contrato Python, mas o dump real faz `unset` por prefixo/nome. O regex aparenta ser usado como contrato de teste, não no shell de dump. Teste funcional é decisivo.

### Relato externo ainda não verificado independentemente

Foi relatado que o sintoma da sessão WebUI `a6972bfd5c9f` é snapshot antigo reintroduzindo `/root/.haos` e negando mutações CLI, e que houve teste focado com vários passes/skips. Este plano não confirma isso em sessão pai viva nem valida o total relatado. O bloqueio pode ser do ambiente da sessão, do subprocesso que herda env ou de outro estado; distinguir por reprodução e registrar o `returncode`/linha de erro.

### Hipótese causal a provar

Uma origem possível é `HERMES_DELEGATED_CHILD_CONTEXT` do contexto filho chegar ao processo local do terminal, ser incluída em `export -p` e sobreviver na snapshot compartilhada; depois o pai `source`ia arquivo com a fence do child. Há snapshots com o marcador e o caminho de código é compatível, mas isso não demonstra qual comando/identidade escreveu aqueles arquivos nem comprova a cadeia causal da sessão alvo. Confirmar com teste controlado e evidência sanitizada antes de chamar RCA confirmada.

## Contrato de isolamento a preservar

1. Contexto de delegação continua fail-closed: criança in-process e subprocessos descendentes mantêm a fence para o board/raiz correta. Limpar um snapshot não pode virar mecanismo de autorização.
2. O pai mantém env/claim que recebeu legitimamente do dispatcher. Não remover `HERMES_KANBAN_TASK/RUN_ID/CLAIM_LOCK` indiscriminadamente do `os.environ`, do perfil, do board ou do comando do dispatcher. Somente impedir que identidade contextual de uma execução contaminante seja persistida/reviva no snapshot compartilhado; documentar e testar a exceção correta de autoridade.
3. Perfil, `HERMES_HOME`, board DB, `HERMES_KANBAN_BOARD`, identidade do worker, `PATH`, cwd e shell state legítimo são domínios separados. Não inferir legitimidade pelo nome `HERMES_*` nem copiar o env de um child para o pai.
4. Nenhuma gravação de dados de snapshot aceita texto como código sem escaping. Exclusions devem ser aplicadas antes de exportar (não `grep` em linhas, por risco de multiline/continuation). Publicação atômica mantém o arquivo anterior se construção falhar.
5. Não persistir secrets em evidências, plano, comentários Kanban ou logs. Snapshot contém exports sensíveis; coletar apenas nomes/resultado e conteúdo redigido.

## Responsabilidades paralelas (três agentes + dono)

Criar cartões READY separados no Kanban pelo **pai legítimo** somente depois de recuperar sua autoridade, sem pedir a um child que mutile board. Um cartão por fatia, workspace isolado, escopo de arquivo/teste exclusivo. Se não for possível claim seguro, parar e pedir operador; não contornar o guard.

| Papel | Responsabilidade/arquivos | Entrega verificável | Não pode |
|---|---|---|---|
| Agente A — runtime/snapshot | `tools/environments/base_session_env.py`, testes unitários do dump e casos de snapshot legado; mapear bootstrap vs wrapper e regras de exclusão | patch mínimo, teste funcional red-green para novo dump e snapshot legado, diff revisável | alterar regras de autorização Kanban; limpar dados do host |
| Agente B — isolamento/segurança | testes de `agent/delegation_context.py`, LocalEnvironment spawn e CLI read-only em fixtures/board temporário; provar fence child e pai | relatório com sequência child/parent, caminhos/board IDs temporários e evidência de deny/allow; teste não regressivo de filho | mexer em produção ou relaxar guard/fence |
| Agente C — recuperação/integração | runbook de recuperação específico de snapshot para sessão de teste; teste de isolamento profile A→B→A; avaliar concorrência/falhas e fluxo de deploy supervisionado | procedimento idempotente com backup/verificação e matriz de falhas; validação por diretório temporário | editar o plano deste arquivo em paralelo; remover cache global/service restart |
| Pai/reviewer | integra relatórios, aprova desenho e owns sessão/board; executa recovery autorizado e valida UI/CLI do pai após merge; decide canary/deploy com operador | checklist de gates assinado por commit SHA, testes e observação parent; cards atualizados | declarar sucesso com base só em relato de subagente |

Separação temporal: primeiro A/B/C só leitura e testes em temp; depois consolidar um patch único de produção em nova worktree. Ninguém escreve no mesmo arquivo/teste simultaneamente. Comentários Kanban registrados apenas pela identidade parent/autorizada.

## Fases verticais, gates e rollback

### Fase 0 — preservar evidência e baseline (read-only)

- Dono registra SHA candidato/base, versão executada, hora, sessão afetada, mensagem CLI completa redigida, qual superfície (`terminal` local vs terminal de agente), `HERMES_HOME`/perfil/board path resolvidos sem expor segredo e os snapshots exatos da sessão via path conhecido. Não fazer dump de todo env.
- Confirmar se snapshot de sessão é aquele em `LocalEnvironment._snapshot_path`, conteúdo somente em whitelist de variáveis suspeitas e permissões do arquivo. Copiar snapshot alvo para backup restrito com hash; não editar original ainda. Verificar processos/threads em curso que possam regravar arquivo e aguardar/encerrar de forma normal conforme supervisor; não matar processos às cegas.
- Executar teste de reprodução em `tmp_path` com subprocess/process boundary real, sequências parent→child→parent e teste de erro de claim no board temporário. Baseline deve falhar no comportamento defeituoso e mostrar estado antes/depois, sem tocar em board real.
- **Gate 0:** cadeia causal reproduzível, artefato apontado por identidade de sessão e explicação de onde marcador entra; backup+hash e plano de recuperação confirmados. Se não reproduzir, anotar hipóteses alternativas e ampliar observabilidade read-only; não editar host.
- **Rollback:** nenhum efeito; abandonar hipótese/patch se teste não reproduz ou se causa diferente surgir.

### Fase 1 — corrigir persistência e legado

- Reusar boundary/semântica existente do dump e acrescentar teste focado. A regra precisa abranger bootstrap e re-dump e os marcadores de fence/claim que não podem ser persistidos por snapshot compartilhado. Auditar a lista oficial em `KANBAN_ENV_KEYS` e determinar conscientemente o tratamento de `GOAL_MODE/GOAL_MAX_TURNS`; não usar exclusão cega de prefixo que apague contexto necessário a execução legítima.
- Ao `source`ar snapshot antigo, uma solução deve remover/restaurar markers contaminantes antes do comando de forma scoped e segura, ou invalidar/reconstruir apenas snapshot de sessão inequivocamente selecionado. Preferir tratamento compatível, sem migração de cache global. Especificar como conservar variáveis legítimas em pai dispatcher e como filho recebe fence via processo/contexto dedicado, não via estado compartilhado.
- Nenhum schema/serialização versionada é exigida se o formato continua shell e a limpeza legado é prova funcional. Se introduzir versão de formato, incluir campo/schema version explícito, versão desconhecida deve falhar seguro sem source legado arbitrário; suportar N-1 por migração determinística/atômica, preservar `mode 0600`, backup e reverter para legado somente após verificação. Documentar formato e limites. Não inventar versão agora.
- TDD: primeiro demonstração vermelha no baseline; depois patch; teste passa no patch. Adicionar fixture de snapshot legado com marcador e valor shell perigoso multiline (deve nunca executar); pai mantém `PATH`/variáveis custom legítimas, contexto de perfil A/B não vaza, child continua negado no board de origem.
- **Gate 1:** todos os critérios comportamentais passam; não há fonte/execução de variável multiline inesperada; revisão independente confirma que exclusão não remove fence do descendente nem autoridade legítima de dispatcher.
- **Rollback:** reverter patch em código se falhar; não restaurar snapshot contaminado. Manter cópia imutável de antes do tratamento e reconstruir só sessão alvo por caminho controlado, ou retornar código à versão anterior enquanto sessão suspeita permanece parada/reaberta limpa conforme fase 2.

### Fase 2 — recuperar uma sessão pai específica

- Impedir que a sessão continue escrevendo snapshot contaminado durante recuperação: suspender somente novos comandos naquela sessão pela interface normal, verificar execução ativa e aguardar conclusão/cancelamento cooperativo. Não parar gateway/serviços globais sem decisão operador.
- Identificar de maneira inequívoca `session_id -> LocalEnvironment -> _snapshot_path`; checar dono/permissão/caminho canônico e preservar backup+hash. Nunca inferir arquivo pelo `mtime` ou escolher por glob.
- Só depois do patch Fase 1 estar disponível no runtime da sessão, invalidar/reconstruir **aquele snapshot identificado** por mecanismo normal `init_session`/bootstrap que rode login env sob contexto pai correto. Se não há mecanismo de invalidação com API segura, escalar ao mantenedor para acrescentá-lo; não editar manualmente snapshot shell e não apagar diretório compartilhado.
- Preservar variáveis legítimas de perfil e board através do bootstrap da sessão pai. Antes de retomar, verificar leitura de env sanitizada e testar apenas operação Kanban não mutante/CLI de status. Teste de autoridade mutável deve ser feito em board descartável e apropriado; no board operacional, somente ação já aprovada, identificável e de impacto limitado.
- Retomar a sessão e repetir: leitura CLI no perfil/board correto; pai consegue executar uma mutação de teste autorizada em fixture; novo spawn de agente permanece fenced; após sequência, pai ainda vê marker ausente e subprocess child continua negado no mesmo board de teste. Inspecionar snapshot pós-execução sem divulgar valores secretos.
- **Gate 2:** runtime executado contém SHA do patch esperado; sessão correta e snapshot correto comprovados; pai sem fence herdada; child deny intacto; variáveis legítimas/profile-A→B→A preservadas; nenhum cache alheio alterado. Registrar antes/depois/hash e retorno CLI redigido.
- **Rollback:** não recolocar snapshot contaminado. Se a reconstrução falhar, manter a sessão afetada fora de operações mutantes, preservar backup e abrir sessão pai limpa no mesmo perfil usando caminho oficial; manter board guard ativo; restaurar o ambiente da sessão apenas via supervisor/bootstrap. Escalar se isso exigir restart global ou alteração de identidade. Reverter deploy do patch somente com pai protegido e alternativa operacional aprovada.

### Fase 3 — falhas, concorrência e canário

- Testar de modo isolado dois wrappers disputando publish do snapshot; falha de `mktemp`, escrita, permissão, `mv`, timeout e cancelamento no meio do dump; leitura deve ver snapshot antigo completo ou novo completo, nunca partial. Confirmar falha de dump não substitui arquivo bom. Cobrir snapshot ausente/corrompido/versão desconhecida e retry idempotente.
- Testar contexto pai/child concorrente em threads e processos separados, ContextVar não herdado implicitamente, herança de env e context manager que restaura token em exceção. A execução da shell deve ser criada de forma explícita conforme API real, sem mudar `os.environ` global.
- Canário começa em sessão descartável e perfil/board fixture, não na sessão do pai. Após passar, pai autoriza sessão-alvo com operador olhando. Nenhum deploy enquanto integrações permanecem suspensas; o runbook de deploy precisa respeitar `docs/haos/DEV_WORKFLOW_VM.md` (aprovação, `main` versionamento +1 e fluxo de promoção) e política de supervisor. Este plano não autoriza, prepara nem executa deploy.
- **Gate 3:** teste real e unitário correlacionados, concorrência/falhas atendidas, sem regressão em scoped env, logs observáveis e revisão de segurança; checklist manual parent é positivo. Um único gate falho bloqueia rollout.
- **Rollback:** desligar apenas o canário/feature scoped pelo mecanismo oficial (se houver) ou reverter commit no deploy supervisionado; preservar snapshots e não limpar cache geral. Manter guard de child ativo; se regressão no novo código, voltar código e reconstruir somente snapshot da sessão com bootstrap seguro, nunca reinstalar snapshot antigo contaminado.

## Runner canônico e evidências

- Localizar instruções e ambiente do repo em `AGENTS.md`, `tools/AGENTS.md`, `agent/AGENTS.md` e usar `scripts/run_tests.sh` como runner canônico, com comandos exatos e retorno observável. Testar pelo menos unitário do snapshot, integração `LocalEnvironment`, `delegation_context`/descendant, Kanban CLI guard e escopo de perfil; usar HERMES_HOME temporário/fixtures e não credenciais reais. Não copiar contagens de execuções de outra worktree; informar cada execução, pass/fail/skipped e motivo real. Nenhum total de testes é pressuposto.
- Ordem red-green: primeiro executar regressão nova contra base e guardar resultado vermelho esperado; depois corrigir e executar regressão; depois arquivos específicos adjacentes; depois conjunto direcionado via scripts runner. Testes skipped não satisfazem um gate; rodar no ambiente compatível ou marcar bloqueado com razão.
- Cobertura de comportamento, não inspeção de texto fonte: teste observa env do processo shell, arquivo de snapshot e decisão de autorização na API/CLI, não regex no código como única evidência.
- Artefatos do gate: commit SHA/diff, comandos e exit code, evidência sem segredo, hash do snapshot antes/depois, IDs de fixture, relatório dos três agentes e checklist aprovado por pai/reviewer. Não anexar snapshot cru.

## Fila Kanban/merge/deploy

1. Depois de Gate 2 e com parent legítimo, criar/atualizar três cartões separados; nenhum child marca/claim/completa cards. Cartões explicitam worktree e arquivos sem sobreposição, critérios de aceite e bloqueios. Status READY antes de dispatch; cada agente trabalha em worktree isolada.
2. Reviewer pai integra, repete testes canônicos na branch de integração e verifica `git diff`/estado dos worktrees. Merge após todos os gates; cumprir a regra de incremento de versão para commit em `main` via fluxo oficial, sem inventar bump antes de promover.
3. Deploy/integrations ficam suspensos até autorização humana explícita. Depois de merge e aprovação, operator verifica checklist de `DEV_WORKFLOW_VM.md`, executa implantação pelo fluxo supervisionado do host e verifica código rodando, health/status do supervisor e teste de smoke do pai. Não alegar deploy sem SHA/runtime conferido.
4. Após rollout, reabrir operações normais somente quando canário confirmado. Fechar cartões como DONE pelo pai com links/IDs/evidências. Se sistema Kanban continuar negando o pai, parar e solicitar reparo do supervisor/operador; não contornar.

## Checklist de aprovação final

- [ ] RCA testada e artefato de sessão ligado ao caminho de código, ou hipótese reclassificada.
- [ ] Contrato child-fail-closed provado no subprocesso real.
- [ ] Pai→child→pai provado, incluindo snapshot existente contaminado e shell state legítimo.
- [ ] Scope/profile A→B→A prova ausência de cruzamento e board/claim do dispatcher válido.
- [ ] Atomicidade/falha/concurrency/retry verificados; formato legado/versionamento decidido.
- [ ] `scripts/run_tests.sh` direcionado executado no commit final; nenhum skip silencioso.
- [ ] Recuperação scoped feita em ambiente temporário; no pai live, backup e autorização antes de ação.
- [ ] Revisão independente de segurança e gate do dono aprovados.
- [ ] Deploy explicitamente aprovado e verificado, ou continua marcado suspenso.
- [ ] Rollback não depende de remover guarda, limpar cache global, `env -u` nem restaurar snapshot contaminado.
