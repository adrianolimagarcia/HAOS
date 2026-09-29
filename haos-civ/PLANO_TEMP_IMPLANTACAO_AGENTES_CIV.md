# Plano temporário de implantação — HAOS Civilization

**Estado:** planejamento; não executar as etapas abaixo sem revisão dos gates. **Decisão do operador:** manter usuário/senha do dashboard `admin`/`admin` e acesso HTTP na porta 9119, inclusive via tailnet; não incluir troca obrigatória de senha nem migração compulsória para HTTPS nesta implantação. Risco aceito: credenciais fracas e HTTP direto na tailnet não fornecem confidencialidade TLS na camada HTTP; SSH continua disponível como alternativa. Não registrar senha em logs/comandos de implantação.

## Onde paramos — evidência e limites

- `haos-civ/TASK_LEDGER.md` registra **F0, V1, V2, V3, V4 e X como concluídos** no nível de componentes/contratos; isso **não prova rollout de agentes autônomos em produção**. `IMPLEMENTATION_STATUS.md` declara dual-stack verificado em testes anteriores, não implantação viva.
- A demonstração `scripts/demo_civ_simulation.py` usa dados sintéticos em diretório temporário, não invoca agentes LLM ao vivo. Testes do observatório e E2E local passaram anteriormente; não substituem teste de workflow real.
- O dashboard `/civilization` e a API de leitura autenticada existem; por design, o endpoint usa o `events.db` canônico do perfil e não cria fatos. O perfil `/root/.haos` respondeu `haos civ status --json` com **0 Bots, 0 Councils, 0 propostas, 0 memórias e nenhuma constituição**. Zero no painel é coerente; não copiar simulação para produção.
- `CouncilManager.start_session/submit_position/record_decision` registra posições/síntese fornecidas pelo chamador, mas **não executa Bots/LLMs por si** (`hermes/platform/council/manager.py`). `ShadowLeafManager` sem `executor_fn` apenas prepara worktree e conclui worker padrão (`hermes/platform/shadow_leaf.py`). **Isso não significa que todos os agentes HAOS sejam simulados:** o dispatcher/worker de tarefas existente possui execução real de CLI (`hermes/platform/execution/dispatcher.py`, `hermes/platform/execution/lane_executor.py`, `hermes/platform/webui/standalone.py`); falta comprovar/conectar este caminho à identidade civilizacional, ao Council e ao Leaf. Antes de ativar o modo civilizacional, demonstrar a integração real, não afirmar autonomia a partir do ledger.
- Dashboard ativo na sessão atual em `127.0.0.1:9119`, Tailscale `tcp://100.77.31.78:9119 -> 127.0.0.1:9119`, ambos HTTP. Login `admin`/`admin` foi validado. Esse dashboard foi iniciado como job, **não serviço reiniciável automaticamente**. Outros serviços HAOS existentes não devem ser reiniciados por este plano.

## Fase D1 — serviço durável do dashboard (pendente)

1. Inventariar `systemctl`/serviços de dashboard e processo na 9119; escolher **uma** unidade dedicada ou serviço existente apropriado, sem conflito com `haos-gateway`, `haos-edge` ou `haos-webui`.
2. Definir serviço apontando a este checkout/artefato aprovado, `HERMES_HOME=/root/.haos`, usuário/permissions adequados, bind `127.0.0.1:9119`, `--no-open --skip-build`; definir `HERMES_DASHBOARD_PUBLIC_URL` coerente com HTTP/Tailscale se necessário para links. Configuração de credenciais permanece no config `0600`, sem copiar valores para unidade ou logs.
3. Planejar janela curta: parar apenas o job atual, iniciar unidade, habilitar reinício automático e verificar `systemctl is-active`, PID único na 9119, `/login`=200, `/civilization` sem sessão=302, `/api/civilization/overview` sem sessão=401. Reiniciar a unidade e repetir. Não instalar um segundo servidor que mascare a instância verificada.
4. Preservar configuração Tailscale `--tcp=9119` e portas 443/8646 existentes. Testar por nome/IP tailnet e túnel SSH; rollback: desabilitar/parar a unidade nova e restaurar o processo anterior, sem tocar `events.db`.

**Gate D1:** página sobrevive a reinício da unidade e volta após boot simulado/verificado; demais serviços intactos. Não alegar sobrevivência a reboot até testá-la.

## Fase A1 — integração viva mínima V1 (pendente operacional)

1. Escolher perfil de teste persistente **isolado** do `/root/.haos` de produção; verificar resolução de `HERMES_HOME`, `events.db`, backup/restore e isolamento ao alternar perfil. Não usar o banco da demonstração.
2. Mapear entrada real de tarefa → `BotSpecManager.submit_to_dispatcher`/dispatcher → worker CLI existente → Bot persistente → `IdentityManager`/versão → prompt pré-sessão → execução `AIAgent` → evento com IDs. Conferir se o prompt efetivamente recebe `bot_id`/bundle no caminho real; corrigir integração com teste que prova identidade no prompt sem alterar a SOUL pai nem sessão ativa.
3. Implementar/ligar um worker real opt-in para Leaf (não o worker padrão de preparação de worktree), propagando snapshot de identidade, orçamento, timeout, cancelamento, retry idempotente, erro e resultado/evidência. Provar com **uma tarefa real delimitada**, custo/modelo/IDs rastreáveis e nenhuma escrita na identidade original.
4. Implementar/ligar orquestração real opt-in do Council: dois Bots distintos executam análise independente; posições chegam ao `CouncilManager`, síntese/dissenso são registrados e ações dependem de gate externo. Jamais tratar `submit_position()` manual como deliberação autônoma.
5. Definir flag off/shadow/opt-in por perfil e caminho de retorno legado sem duplicar efeitos; testar falhas de provider, cancelamento, sessão ativa, reboot/replay e isolamento entre perfis.

**Gate A1:** eventos do perfil de teste aparecem no observatório com Bot, Leaf, Council e decisão corretos, vinculados ao mesmo stream; traces e evidências verificáveis; teste com LLM real explicitamente distinguido de fixture.

## Fase A2 — sociedade V2 em execução (pendente operacional)

- Ligar delegação, papéis e seleção por reputação a evidências de trabalho real; proibir autoendosso e contagem de execução sintética como reputação.
- Exercitar dupla tarefa/relacionamento, retry sem duplicação, causalidade e debate com dissenso real. Comparar projeção antes/depois de replay e seu efeito nos próximos participantes.
- Gate: papel/reputação mudam somente mediante evidência permitida; sem privilégio adicional concedido por votação.

## Fase A3 — evolução V3 governada (pendente operacional)

- Capturar experiência real, gerar proposta versionada, classificar risco e requerer aprovação explícita para identidade/valores críticos. Testar versão base obsoleta, aplicação em boundary de sessão, rollback compensatório e lineage, sem autoescrita silenciosa de SOUL.
- Gate: versões/eventos/auditoria sobrevivem reinício; nenhuma sessão em curso muda identidade.

## Fase A4 — civilização V4 conectada ao enforcement (pendente operacional)

- Definir/enact constituição de teste, integrar `evaluate_policy` **antes** de ações reais com efeito externo; demonstrar hard-deny mesmo com consenso e tratamento de advisory mediante aprovação. A existência de método no manager não prova enforcement em todo executor.
- Criar afirmações institucionais com provenance real, tratar conflitos/temporalidade, testar rebuild do world-model; somente após integração comprovada avaliar jobs agendados idempotentes de manutenção (compaction/drift), sem autoaprovação.
- Gate: ação negada não é executada nem no fallback; restore/replay mantém política e evidência.

## Fase R — rollout e operação (pendente)

1. Backup consistente de `events.db` (incluindo WAL) e ensaio de restore em perfil descartável; verificar export/import antes de promoção. Criar alertas para falha de store, leituras lentas, eventos órfãos, erros de executor e quedas do dashboard.
2. Medir latência/tamanho do EventStore e custo de `GET /api/civilization/overview`: a projeção varre eventos em cada poll; estabelecer baseline, limite de carga e estratégia paginada/cache *sem* transformar projeção em fonte canônica.
3. Promover `off → shadow → opt-in → canary → default-on` **se e somente se** gates A1–A4/recovery passarem, medindo erros, custo, latência e compatibilidade legada. Reverter pela flag/serviço, nunca apagar eventos.
4. Registrar versão implantada, evidências de teste, responsável, janela, critérios quantitativos acordados e plano de rollback. Manter `admin`/`admin` e HTTP conforme decisão explícita do operador; documentar o risco aceito e restringir acesso à tailnet/SSH existente (sem expor via Funnel/Internet).

## Ordem imediata recomendada

**D1 → A1 → A2 → A3 → A4 → R.** O HAOS já tem execução real de tarefas via dispatcher/CLI, e estruturas V1–V4/UI estão adiantadas em relação à **integração dessas execuções com a semântica civilizacional**. A etapa atual é **pré-rollout/integração operacional A1**, não uma nova V5 nem V4 em produção. Este arquivo é temporário como artefato de trabalho solicitado; remover somente após execução/revisão com o operador.
