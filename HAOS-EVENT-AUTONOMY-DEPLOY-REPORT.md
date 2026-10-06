# Deploy autonomia por eventos — 0.21.94

## Release observada
Commit91b73a5041e041afc6aa6d50e021e80b0b574297, árvorec4e5021c22ed3c76365ee7a12b04a7fc54e2605c. Publicação atomic fast-forward main e haos-standalone confirmada; install /usr/local/lib/haos-agent e metadados0.21.94. Desenvolvimento/commit isolados em .worktrees/event-driven-autonomy, sem reset ou blanket-stage do workspace principal sujo.

## Verificação executada
- Local163Python passaram,0falhas, retries desativados;14Vitest passaram e build React/TypeScript aprovado.
- VM160Python passaram,0falhas, retries0, além serviço contínuo com investigador injetado, persistência de evento/reabertura, savedplan, pausaCLI e API autenticada+assets HTTP byte-idênticos.
- IA real: smoke sintético gemini-3.8-flash-low/custom recebeu2277tokens entrada113saída, diagnóstico estruturado sem ferramentas.
- Serviço implantado consumiu job_abf806917cc84a5cab0ed36c6444deb2 automaticamente: completed/pending_authorization, usage canônico verificado6452entrada348saída, plano salvo existente, verificationFalse (nenhum reparo alegado).
- Tailscale GET /api/framework/autonomy autenticado200; não autenticado401. Chunk FrameworkPage-BRA32ElV.js servido byte-idêntico à release.
- Race de pausa/revogação durante espera de lock corrigida com mutation_guard e releitura de política no executor imediatamente antes de aplicar; testes determinísticos reais passaram.

## Operação ativada
Unit /etc/systemd/system/haos-autonomy.service habilitada no boot, active (PID observado3366490), WorkingDirectory installtree; dashboard active e reiniciado. HOME/HAOS_HOME/HERMES_HOME explícitos; NoNewPrivileges e umask0077; deadline worker subprocesso com kill do processgroup.
Config perfil/root: framework.autonomy.enabled=true, auto_apply=true SOMENTE grants exatos existentes,6investigações/hora,90s por investigação,2048tokens saída. Concorrência1, dedup/cooldown300s, backlog128. Resultado IA jamais emite grants. Eventos novos do events.db canônico e warnings telemetria/read-only disparam IA; inscrição inicial usa tail atual, não backlog histórico. Eventos internos pipeline/autonomy excluídos.
URL http://cachyosdell.tailbb7185.ts.net:9119/framework . Atualizar página para painel Event-driven autonomy; pausa/resume controla novos trabalhos e autorização rechecadas antes da aplicação, não desfaz ação já aplicada.

## Limites e ressalvas
Não implementa reparo OS geral: ação registrada segue relatório JSON reversível profile-local64KiB. Novos parâmetros precisam autorização exata; por isso primeiro evento completou pending_authorization. Investigação usa evidência fornecida por coletores, sem ferramentas de shell/alteração; plugins locais importados não equivalem a sandbox contra Python malicioso. Evidência operacional limitada é enviada ao provedor configurado; não inserir segredos nos eventos. Job/time/outputtoken limites não representam orçamento monetário; custoUSD completo desconhecido. Model timeout/retries internos podem emitir mais de um request dentro do prazo.
Fila é bounded; plans/memória/audit do framework anterior ainda não têm retenção automática. Recuperações/receipts parciais impedem repetição automática incerta e requerem revisão humana. Não houve stress/ISO ou alteração DSH3080/custom8787/edge8788.
CLI exibiu alerta já existente de gateways potencialmente com módulos antigos; nenhum gateway não relacionado foi reiniciado, não se afirma deploy fleet-wide. systemd-analyze verify exit0 também apontou warnings antigos do drop-in hermes-graph-refresh (Timeout* em seçãoUnit), não causados pela unidade nova.

## Evidência e rollback
Backup/root/haos-event-autonomy-backup-20261002T162953Z contém config original(private), unitdashboard, dist anterior, trackedpatch(empty) e7untrackedfiles. Unitautonomy inexistia antes. Para desativar: systemctl disable --now haos-autonomy.service; haos framework autonomy pause e framework.autonomy.enabled=false via config canônica. Restaurar config precisa preservar alterações posteriores, não sobrescrever cegamente.
Código anterior53175916da5ebf9de49d18adfeb923052fa07458; restaurar em árvore preservada, metadata e dist archived antes de restartdashboard, nunca reset de workspace sujo.
VM:/tmp/haos-new02194-validation-m4Tjvi/tree, logs targeted-tests.log e acceptance-probe.log. Host receipt/tmp/haos-new02194-host-RFOq4y/ACCEPTANCE.md. SourcearchiveSHA2562ac30de7878b56f1caec28958086abec028c0417cd400488fa2d96a0844f87c0; webarchivebbf0527357ba9d12fc42115a38bec5257f2a2dd1d4cd19a36d2873404ad244d7. Não foi necessário instalar dependências na VM nem modificar/opt/haos.
