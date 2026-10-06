# Plano — verificação de sucesso real em tarefas delegadas HAOS

**Status:** plano proposto; nenhuma alteração de código executada.  
**Escopo:** tornar a conclusão de tarefas de engenharia delegadas dependente de evidência verificável, reduzindo falsos sucessos.  
**Princípio:** a mensagem de conclusão do agente é uma alegação; o harness decide o estado terminal com verificações independentes proporcionais ao risco.

## Objetivo e critérios de aceite

Ao fechar uma tarefa delegada, o HAOS deve registrar o que foi pedido, quais evidências foram exigidas, quais verificações realmente rodaram e seu resultado. Não marcar como sucesso se o agente apenas disser que terminou, se o processo expirar/interromper, se artefatos obrigatórios faltarem ou se verificações falharem.

Aceite quando:

1. Resultado do processo (exit/cancel/timeout) e resultado declarado pelo agente são estados distintos.
2. A tarefa carrega critérios de aceite verificáveis, determinados antes da execução; critérios ausentes não viram “passou” por inferência.
3. As verificações executadas produzem evidência estruturada e ligada à execução/worktree/commit correto.
4. Timeout, cancelamento, falha de teste, ausência de diff/artefato exigido ou erro no verificador nunca resultam em sucesso.
5. Verificação proporcional: testes focados por padrão; E2E real para mudanças em caminhos de integração, I/O, API, autenticação, persistência, runtime, deploy ou estado compartilhado.
6. O fluxo legado e tarefas sem verificadores definidos continuam explícitos como `unverified`, sem bloquear indevidamente tarefas conversacionais ou puramente analíticas.

## Diagnóstico prévio obrigatório

Antes de implementar, rastrear ponta a ponta a delegação: criação da tarefa → prompt/AC → runner e isolamento → coleta de exit status → localização do diff/artefatos → pós-validação → atualização do Kanban/resultado apresentado. Determinar onde o estado de sucesso é atribuído e localizar chamadas silenciosas de fallback, especialmente timeout, cancelamento, ausência de commit e worktree compartilhado. Observar no código e logs, sem presumir que falhas anteriores provam bug no harness.

## Plano em fatias verticais

### Fase 0 — contrato e baseline
- Mapear o fluxo e escolher o menor ponto de integração no agregador de resultados existente.
- Definir estados terminais inequívocos: `verified_success`, `reported_success_unverified`, `verification_failed`, `timed_out`, `cancelled`, `execution_failed`.
- Definir esquema mínimo de evidência: execução, workspace/branch, HEAD/diff, comandos de teste executados, exit codes, artefatos e timestamps; evitar armazenar segredos/logs integrais.
- Construir fixtures de falsos sucessos e registrar comportamento atual (baseline), sem mudar produção.

### Fase 1 — gate contra sucesso fabricado
- Separar “agente declarou concluído” de “harness verificou concluído”.
- Exigir sucesso de processo e prova de artefato/diff quando a tarefa pede código.
- Propagar timeout/cancelamento/falha sem converter em sucesso; preservar motivo e proveniência.
- Acrescentar testes de contrato no caminho real do agregador, incluindo retorno parcial, ausência de artefato e sucesso textual com exit não zero.

### Fase 2 — verificador de tarefa proporcional
- Acrescentar verificação independente configurável/derivada do tipo de tarefa: teste(s) direcionado(s), lint/typecheck/build quando pertinente, inspeção do diff e prova do efeito externo para integração.
- Executar verificações no mesmo worktree/commit produzido, em ambiente limpo e com limites de tempo/recursos.
- Não aceitar nomes de teste ou logs declarados pelo agente como evidência: o harness executa o comando e captura exit code.
- Para E2E, validar pela interface pública real e checar o efeito observável (por exemplo, resposta HTTP e persistência), com cleanup e isolamento.

### Fase 3 — política de risco e fallback seguro
- Classificar mudanças de baixo risco (docs/pure logic) versus integração/segurança/estado persistente; exigir E2E nas categorias relevantes, não para toda tarefa.
- Se não houver verificador disponível, registrar `reported_success_unverified`, explicar a lacuna e não rotular como sucesso verificado.
- Verificador indisponível, erro de infraestrutura ou evidência ambígua: falhar fechado quanto ao status de sucesso, mas não apagar artefatos nem descartar o trabalho.
- Impedir que verificações executem comandos arbitrários fornecidos como prova pelo agente; usar perfis/allowlists de verificadores e sandbox apropriado.

### Fase 4 — rollout e calibração
- Shadow mode: computar o novo verdict sem afetar estado/UX; comparar com resultados existentes em tarefas reais anonimizadas/minimizadas.
- Revisar falsos positivos e falsos negativos com amostra humana; ajustar critérios antes de qualquer enforcement.
- Ativar primeiro para novas tarefas de engenharia locais isoladas; habilitar por superfície/runner progressivamente, com kill switch e rollback simples.
- Só então atualizar cartões/UX para mostrar status, testes executados e evidência resumida.

## Matriz mínima de testes

- Agente diz “feito”, sem alteração exigida → não é sucesso verificado.
- Testes falham, agente diz que passaram → falha de verificação.
- Processo exit 0, mas teste exigido não rodou → não verificado.
- Timeout/cancelamento com diff parcial → preserva artefato e status inconclusivo, nunca sucesso.
- Teste passa no checkout errado ou em outro HEAD → rejeitar evidência por proveniência.
- Mesmo artefato, teste direcionado passa, mas endpoint/efeito persistente falha → E2E reprova.
- Tarefa puramente analítica/docs sem código → regra específica, sem exigir teste de runtime indevido.
- Verificador indisponível → explícito unverified/error, nunca sucesso silencioso.
- Reexecução/retry → idempotente, não duplica conclusão nem perde evidências anteriores.

## Métricas (sem alegações prematuras)

Medir antes/depois: taxa de sucesso reportado versus verificado, taxa de falhas descobertas por verificadores E2E, falsos negativos/falsos positivos revisados, duração/custo adicional, taxa de tarefas `unverified` e falhas do próprio harness. Não usar o número de SWE-Serve como baseline HAOS; benchmark e workload são diferentes. Não otimizar duração antes de ter baseline.

## Segurança e operação

- Verificações não recebem credenciais além do mínimo e não devem publicar/deployar como efeito colateral.
- Capturar saída limitada/redigida; nunca registrar tokens, ambientes secretos ou transcript integral por padrão.
- Isolar worktree e diretório de teste; não confiar no checkout pai nem em estado compartilhado.
- Garantir cleanup em sucesso, erro, timeout e cancelamento sem apagar trabalho parcial que precise revisão.
- Persistir proveniência suficiente para reproduzir o verdict (commit, comando, versão/config do runner), sem depender apenas de logs textuais.

## Fora de escopo inicial

- Importar SWE-Serve integralmente ou reproduzir seus 53 tasks/H100.
- Criar um novo sistema geral de benchmark ou agente revisor baseado em LLM.
- Tratar score/parecer de LLM como substituto de testes executáveis.
- Reescrever o sistema Kanban ou a infraestrutura inteira de delegação.

## Sequência de implementação

1. Diagnóstico line-level e contrato atual do fluxo.
2. Testes de regressão de falso sucesso, demonstrando primeiro o comportamento atual.
3. Gate determinístico de status/proveniência e evidência mínima.
4. Verificadores direcionados/E2E por categoria de mudança.
5. Shadow mode com comparação auditável.
6. Rollout gradual, métricas e revisão periódica.

## Referência e interpretação

SWE-Serve reporta uma diferença entre avaliação completa e pontuação sem E2E no seu próprio benchmark (19 tarefas de serving; mesmas patches). Isso motiva validar o caminho real, mas não é uma estimativa da taxa de erro do HAOS. O plano transfere o princípio — não a suíte, hardware ou números — e requer medição HAOS independente.

Fontes: [paper SWE-Serve](https://arxiv.org/html/2609.26777v1), [repositório NVIDIA](https://github.com/NVIDIA/swe-serve), [artigo Medium fornecido](https://agentnativedev.medium.com/swe-serve-rejects-fake-agent-success-a9c0e6362009).
