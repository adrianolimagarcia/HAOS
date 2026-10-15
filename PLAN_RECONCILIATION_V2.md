# Plano Executivo de Reconciliação HAOS (Produção vs Main HERMES-TURBO)

## Grafo de Dependências e Frentes (Linked Tasks)

```
       [AGENTE A: Memory Gates & 5 Falhas]        [AGENTE B: Auditoria 41 Deltas & Behavior]
                         \                                      /
                          \                                    /
                           ▼                                  ▼
                     [INTEGRAÇÃO ISOLADA NA BRANCH DE RECONCILIAÇÃO]
                                           |
                                           ▼
                            [REGRESSÃO TÉCNICA ABRANGENTE]
                                           |
                                           +<---- [AGENTE C: Runbook Executável & Rollback]
                                           |
                                           ▼
                                 [DECISÃO GO / NO-GO]
```

### Frentes de Execução:
1. **AGENTE A**:
   - Escopo: Investigar as 5 falhas de gate `FACT_VERIFIED` em `tests/test_haos_memory_instinct_harness.py` (4 falhas) e `tests/platform/context/test_memory_candidates_and_router.py` (1 falha).
   - Invariante: Jamais relaxar o gate fail-closed de GBrain v2. Identificar se o problema é de contrato de fixtures/mocks ou se o gate necessita de verifier padrão mockado nas fixtures de teste.
   - Entregável: Testes passando 100%, novos testes adversariais para garantir que asserções sem evidências continuam rejeitadas, commit isolado.
2. **AGENTE B**:
   - Escopo: Auditar o inventário exato das 41 alterações em `/usr/local/lib/haos-agent` (31 tracked modificados + 10 untracked) contra a branch `reconcile/prod-to-main-sync`.
   - Invariante: Zero toque em runtime/serviços de produção. Validar comportamento funcional de Kanban, Gateway, Agents e Skills.
   - Entregável: Matriz de confirmação de preservação/adaptação e commit isolado com quaisquer correções pendentes.
3. **AGENTE C**:
   - Escopo: Elaborar runbook executável e determinístico de deploy e rollback para `/usr/local/lib/haos-agent`.
   - Invariante: Somente comandos dry-run / não-destrutivos. Sem restart de serviços, sem sudo.
   - Entregável: Documento `docs/haos/RUNBOOK_DEPLOY_ROLLBACK_RECONCILIATION.md` contendo procedimentos passo a passo, tratamento do gatilho `haos-webui-agent-watch.path`, healthchecks HTTP e critérios objetivos de abort/rollback.
4. **COORDENADOR**:
   - Integrar os resultados na branch `reconcile/prod-to-main-sync`.
   - Executar suíte abrangente de testes de regressão.
   - Emitir parecer técnico fundamentado de GO / NO-GO com hashes e pendências.
