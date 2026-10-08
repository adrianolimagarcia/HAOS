# Runbook Proposto: Recuperação de Isolamento de Terminal da Sessão Pai Contaminada

**Status:** PROPOSTO — NENHUM COMANDO EXECUTADO EM PRODUÇÃO.
Todos os comandos listados abaixo são estritamente para planejamento e execução controlada em janela futura de manutenção. Toda mutação requer aprovação prévia do operador/dono.

---

## 1. Objetivo e Contexto

- **Objetivo:** Recuperar a sessão pai afetada (`a6972bfd5c9f`) eliminando a contaminação cruzada do marcador de delegação (`HERMES_DELEGATED_CHILD_CONTEXT=/root/.haos`), restaurando a autoridade de orquestração legítima sem impacto global em outros processos, sem furar a cerca de segurança de subagentes e sem mutação arbitrária fora do ciclo de vida suportado.
- **Invariantes do Sistema:**
  1. A cerca de isolamento de subagentes (`delegated_child_subprocess_env` e `is_delegated_child_process_context`) é dinâmica em processo e permanece ativa.
  2. Proibido executar `env -u`, edição manual de marcadores reais ou limpeza global de cache (`cleanup_all_environments()`).
  3. Proibido reiniciar serviços, alterar banco Kanban ou executar deploys sem aprovação explícita.
  4. Serviços em operação (conforme `/root/.haos/council/REGISTRY.md`): `haos-gateway.service` e `haos-webui.service`.

---

## 2. Pré-condições e Gates Obrigatórios (Critérios de Abort)

Antes de iniciar qualquer intervenção na sessão afetada, TODOS os seguintes gates devem ser satisfeitos:

1. **Gate 1 - Patch Presente e Integrado:**
   - Commit candidato (`4a0f0f47565cfa3b92a9af96b59a1ddcda1a5a44` ou branch integrada) presente no codebase, garantindo que `tools/environments/base_session_env.py` exclui marcadores de delegação (`HERMES_DELEGATED_CHILD_CONTEXT`, `HERMES_KANBAN_TASK`, `HERMES_KANBAN_RUN_ID`, `HERMES_KANBAN_CLAIM_LOCK`) de novos snapshots (`export -p`).
   - Sanitização de leitura ativa: carga do snapshot antigo não contamina o processo pai e não apaga marcadores legítimos de processos filhos.
2. **Gate 2 - Drenagem de Subagentes Ativos:**
   - Nenhum subprocesso ou subagente filho associado à sessão pai em execução. Se houver execução concorrente, a operação deve ser imediatamente interrompida.
3. **Gate 3 - Identificação Determinística do Snapshot Alvo:**
   - Localização restrita a `<HERMES_HOME>/cache/terminal/hermes-snap-<session_id>.sh` correspondente estritamente à sessão `a6972bfd5c9f`. Nenhum glob wildcard ou exclusão em massa.
4. **Critérios de Abort Imediato:**
   - Se o patch não estiver ativo no ambiente de execução.
   - Se houver processos filhos ativos ou locks de Kanban pendentes.
   - Se a identificação do snapshot for ambígua ou symlinks apontarem para fora de `<HERMES_HOME>/cache/terminal/`.
   - Se o backup do snapshot falhar ou os checksums divergirem.

---

## 3. Procedimento de Recuperação Scoped (Ciclo de Vida Legítimo)

*Todos os comandos a seguir estão marcados como [NÃO EXECUTADO] e exigem aprovação explícita.*

### Passo 3.1: Auditoria e Backup Scoped do Snapshot da Sessão
Coleta não destrutiva de metadados e backup byte-a-byte do snapshot específico da sessão afetada:

```bash
# [NÃO EXECUTADO] Identificar e auditar arquivo de snapshot da sessão alvo
TARGET_SNAP="/root/.haos/cache/terminal/hermes-snap-a6972bfd5c9f.sh"
ls -ld "$TARGET_SNAP"
sha256sum "$TARGET_SNAP" > "/root/.haos/backups/hermes-snap-a6972bfd5c9f.sha256"

# [NÃO EXECUTADO] Criar diretório seguro e realizar backup atômico com permissões restritas (0600)
mkdir -p -m 0700 /root/.haos/backups/terminal-isolation-recovery
cp -p "$TARGET_SNAP" "/root/.haos/backups/terminal-isolation-recovery/hermes-snap-a6972bfd5c9f.sh.bak"
chmod 0600 "/root/.haos/backups/terminal-isolation-recovery/hermes-snap-a6972bfd5c9f.sh.bak"
sha256sum -c "/root/.haos/backups/hermes-snap-a6972bfd5c9f.sha256"
```

### Passo 3.2: Recriação Scoped do Ambiente de Terminal da Sessão
Utilizar o ciclo de vida legítimo do runtime (`LocalEnvironment.cleanup()` restrito à sessão ou reinicialização via lifecycle da sessão):

```bash
# [NÃO EXECUTADO] Sanitizar o snapshot legado da sessão específica mantendo o cwd íntegro
# (Acionado via API de sessão ou remoção estrita do arquivo de snapshot individual aprovada)
rm -f "$TARGET_SNAP"

# [NÃO EXECUTADO] Reinicialização limpa do ambiente de terminal da sessão via lifecycle da aplicação
# O runtime invoca init_session() gerando snapshot limpo sem marcadores de delegação
```

---

## 4. Plano de Rollback

Caso a recriação do ambiente falhe ou apresente comportamento divergente:

1. **Parar qualquer ação subsequente** e manter serviços estáveis.
2. **Restaurar snapshot a partir do backup auditado:**
   ```bash
   # [NÃO EXECUTADO] Rollback do snapshot da sessão a partir do backup
   cp -p "/root/.haos/backups/terminal-isolation-recovery/hermes-snap-a6972bfd5c9f.sh.bak" "$TARGET_SNAP"
   chmod 0600 "$TARGET_SNAP"
   sha256sum -c "/root/.haos/backups/hermes-snap-a6972bfd5c9f.sha256"
   ```
3. Se houver restart autorizado de serviço que necessite reversão, reverter para os hashes de release registrados no REGISTRY antes do início da janela.

---

## 5. Validação e Readiness (Pai → Filho → Pai)

A validação da recuperação deve seguir estritamente os testes comportamentais:

1. **Validação do Pai (Autoridade Recuperada):**
   - Executar comando de diagnóstico no terminal da sessão `a6972bfd5c9f`.
   - Verificar ausência de `HERMES_DELEGATED_CHILD_CONTEXT` no ambiente ativo do pai.
   - Confirmar que o diretório de trabalho (`cwd`) permanece `/run/media/adriano/e681b5ac-a4fb-44d4-aebf-9d6584065787/dsh-projetos/HERMES-TURBO`.
2. **Validação do Filho (Isolamento e Bloqueio Legítimo):**
   - Disparar subagente controlado de teste isolado.
   - Validar que o subprocesso do filho recebe `HERMES_DELEGATED_CHILD_CONTEXT` via `delegated_child_subprocess_env`.
   - Provar que qualquer mutação não autorizada no Kanban pelo filho é devidamente bloqueada pela guarda de segurança.
3. **Validação Pós-Filho (Pai Inalterado):**
   - Após conclusão do subagente de teste, verificar que a sessão pai retorna ao estado limpo sem herdar resíduos do filho no snapshot recém-gerado.

---

## 6. Reconciliação Kanban (Somente após Autoridade do Pai Confirmada)

A reconciliação de estado do Kanban ocorre exclusivamente após a autoridade da sessão pai ser validada e atestada:

- **Inventário Read-Only do Board (`/root/.haos/kanban/boards/1/kanban.db`):**
  - Card `t_17e89430`: estado observado `ready`, sem assignee/claim ativo. Manter inalterado até reavaliação pelo pai.
  - Cards `t_584e90b0` e `t_c1154007`: estado observado `done`. PROIBIDO reabrir ou executar mutações.
- **Regra de Mutação:**
  - Nenhuma transição de status (`claim`, `complete`, reatribuição) é permitida no runbook de recuperação.
  - Somente após conclusão e validação dos gates, a sessão pai reassume o fluxo normal de despacho pelo Kanban.
