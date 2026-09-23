# Plano de aceitação — migração incremental Python → Rust

## Escopo e regra de decisão

Este plano valida uma migração por fatias, com **Python como referência** e Rust como implementação candidata. Cada fatia deve ter um flag/roteamento reversível e ser avaliada isoladamente. Não aprovar uma fatia por compilação apenas: o gate é comportamento observável, isolamento, recuperação e medição.

**Regra de memória:** é proibido afirmar “reduziu RAM” sem experimento controlado abaixo. Sem pelo menos um par baseline/candidato comparável, o resultado deve ser reportado como **UNVERIFICADO** (ou “não medido”), nunca como redução.

## Baseline e comparação antes/depois

Registrar, para baseline e candidato, em artefatos versionados fora do checkout ou em diretório temporário:

- SHA (`git rev-parse HEAD`), dirty diff (`git diff --binary`), versão Python/Rust, kernel, CPU, memória, allocator e configuração;
- comando exato, `HERMES_HOME` temporário, perfil, portas, limites/cgroup, número de workers e variáveis de feature flag;
- dataset/fixtures, ordem e número de operações, concorrência, timeout, warm-up e critério de parada;
- resposta/eventos/payloads em JSONL, com segredos e tokens redigidos; hash SHA-256 dos artefatos;
- resultado por caso: pass/fail, latência p50/p95/p99, erros, cancelamento observado e estado persistido.

Executar baseline e candidato em processos novos, com o mesmo fixture e ordem randomizada ou alternada (A/B/A/B), no mesmo host ocioso. Fazer warm-up descartado, no mínimo 5 execuções medidas por cenário; reportar mediana e dispersão. Não comparar uma execução Python quente contra Rust frio, nem misturar mudanças não relacionadas.

Para memória, coletar a árvore completa do processo durante o mesmo workload:

- RSS e PSS por PID e total da árvore (`/proc/<pid>/status`, `smaps_rollup` quando disponível), amostrados em intervalo fixo;
- pico e série temporal, não somente uma leitura final;
- número de processos/threads, tamanho do banco/WAL e bytes de payload/transcript;
- ao fim, após drenagem e GC/idle equivalentes, retenção residual.

Se PSS não estiver disponível, marcar PSS como indisponível; não substituí-lo silenciosamente por RSS. Só aceitar alegação de redução se o intervalo de confiança/critério previamente definido mostrar redução no pico e/ou retenção para o mesmo cenário, sem regressão funcional. Caso contrário: **sem conclusão de economia**.

## Matriz de aceitação

| ID | Cenário | Procedimento mínimo | Aceitação |
|---|---|---|---|
| R1 | Paridade de resposta | Reproduzir fixture determinístico em Python e Rust; comparar eventos finais, ordem, status, erro e conteúdo normalizado | igualdade exata quando contrato for byte-a-byte; nos demais casos, diferenças somente em campos explicitamente voláteis e schema válido |
| R2 | Perfis A→B→A | Criar sessão/dado no perfil A; alternar para B e criar dado homônimo; voltar a A; repetir após novo processo | A recupera apenas dados/auth/config de A; B nunca aparece em A; IDs, escopo e transcript permanecem estáveis |
| R3 | Auth positivo/negativo | Sem credencial, credencial inválida, válida, expirada/revogada; testar cookie/header e reinício | 401/403 conforme contrato, sem bypass acidental; segredo nunca no log/payload; sessão válida sobrevive somente conforme `remember` |
| R4 | Concorrência | N requisições simultâneas na mesma sessão e em sessões/perfis distintos; incluir duas escritas conflitantes | sem duplicação/perda, sem mistura de perfis, serialização determinística por sessão e erro explícito em conflito |
| R5 | Cancelamento | Cancelar antes de iniciar, durante stream e após terminal; repetir cancelamento e consultar estado | terminal único (`cancelled` ou contrato equivalente), sem “success” inventado, sem worker órfão, operação idempotente |
| R6 | Persistência/restart | Escrever mensagens e metadados; parar/reiniciar; recarregar; testar DB indisponível/corrompido conforme fallback suportado | transcript, IDs, flags e end reason preservados; recuperação/fallback explícitos; nenhuma perda silenciosa |
| R7 | Payload/protocolo | Capturar request/response/eventos Python e Rust; validar campos obrigatórios, tipos, limites, headers e SSE boundaries | schema e semântica iguais; `data: [DONE]`, erro upstream, stream truncado e payload inválido tratados conforme contrato |
| R8 | RSS/PSS | Rodar protocolo de medição acima em workload pequeno, típico e saturado | relatório baseline/candidato completo; sem claim de redução sem repetição e comparação controlada |
| R9 | Fallback Python | Forçar Rust ausente, falha de spawn, timeout e resposta inválida; executar a mesma fixture | fallback selecionado de forma observável, resposta/paridade mantida, erro Rust não mascarado, sem loop de retry infinito |

## Fixtures obrigatórias

1. Resposta simples, resposta vazia e Unicode/bytes limítrofes.
2. Stream com heartbeat, múltiplos eventos, `[DONE]`, erro upstream e desconexão sem terminal.
3. Payload desconhecido/campo extra, campo ausente, tipo inválido e tamanho no limite.
4. Sessões A/B com o mesmo `chat_id`, nomes e IDs deliberadamente coincidentes.
5. Escrita concorrente, cancelamento repetido, restart durante escrita e banco somente-leitura.
6. Rust indisponível e Python indisponível: cada caminho deve falhar fechado com diagnóstico distinto.

## Execução no checkout

- Python direcionado: `bash scripts/run_tests.sh <testes>`.
- Rust direcionado: `cargo test --manifest-path packages/haos-edge/Cargo.toml --lib` e, quando houver servidor/integração, os testes do workspace aplicáveis.
- Persistência existente: `python3 evals/gateway/session_time_persistence_ab.py .`.
- Controles de persistência/configuração: `python3 evals/gateway/session_time_persistence_controls.py .`.
- Antes de atribuir falha à migração, executar em checkout limpo ou separar falhas pré-existentes do diff do agente; nunca “consertar” DSH/OpenCode/agy/:8790 neste plano.

## Gates de revisão

1. **Gate funcional:** R1–R7 e R9 verdes, ou exceção documentada no contrato.
2. **Gate de isolamento:** A→B→A verde com dois `HERMES_HOME` reais e processo novo; mocks isolados não bastam.
3. **Gate de memória:** R8 reproduzível, com RSS/PSS e árvore de processos. Falta de medição bloqueia qualquer alegação quantitativa.
4. **Gate de rollback:** desligar a fatia retorna ao caminho Python e os testes R1/R5/R6 continuam verdes.
5. **Gate de diff:** revisar somente arquivos da fatia; rejeitar formatação/reorganização não relacionada, mudança de contrato implícita ou fallback silencioso.

## Estado observado nesta revisão

- `cargo test --manifest-path packages/haos-edge/Cargo.toml --lib`: **7 passed**.
- `python3 evals/gateway/session_time_persistence_ab.py .`: **passou** nos modos `idle`, `daily`, `both` e `none`.
- Suíte direcionada via `scripts/run_tests.sh`: **23 passed, 3 failed**; há falhas em `test_standalone_webui.py` (2) e `test_harness_registry.py` (1), portanto o gate global não está verde.
- `python3 evals/gateway/session_time_persistence_controls.py .`: **falhou** na asserção `default_reset_policy`; investigar compatibilidade do fixture/config antes de usar como evidência.
- `git diff --check`: **falhou** por trailing whitespace em `tools/file_tools.py` e `tools/skills_tool.py`.

Esses resultados são evidência do checkout atual, não prova de paridade nem de redução de memória. O documento não altera DSH, OpenCode ou `agy/:8790`.
