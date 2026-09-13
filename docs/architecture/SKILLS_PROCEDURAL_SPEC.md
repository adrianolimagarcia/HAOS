# HAOS Procedural Intelligence & Skills Specification

- **Status:** Accepted (Canonical SOTA Specification)
- **Layer:** Skills / Procedural Intelligence Fabric (K2 / Etapa 2)
- **Module Path:** `hermes/platform/skills/`

---

## 1. Visão Geral e Filosofia

No HAOS, **Skills não são meros prompts em texto** ou arquivos soltos em pastas não versionadas. Uma Skill é um **artefato formal de engenharia de software**, com:
1. **SemVer 2.0 estrito** (`MAJOR.MINOR.PATCH`) para versionamento determinístico e resolução de dependências.
2. **Declaração explícita de dependências e capabilities exigidas** (`capabilities_required`, `dependencies`).
3. **Assinatura criptográfica SHA-256 e proveniência** para segurança de supply-chain.
4. **Ciclo de vida auditado e governado por máquina de estados** (`candidate` → `sandbox` → `eval` → `active` → `deprecated`).
5. **Execução segura em sandbox** e injeção **cache-aware** que preserva a invariante sagrada do prefix-cache de prompt.

---

## 2. Taxonomia de Skills

O ecossistema categoriza as skills em quatro origens ontológicas:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                             ORIGENS DE SKILLS                                │
├───────────────────────────────┬─────────────────────────────────────────────┤
│ 1. Built-in Kernel Skills     │ Embarcadas no core (~/.hermes/skills/).     │
│                               │ Imutáveis durante o runtime.                │
├───────────────────────────────┼─────────────────────────────────────────────┤
│ 2. Dynamic Human Skills       │ Criadas ou editadas pelo operador humano    │
│                               │ via Obsidian Vault ou CLI (`/skills new`).  │
├───────────────────────────────┼─────────────────────────────────────────────┤
│ 3. Synthesized Skills         │ Geradas de forma autônoma pelo Ouroboros a  │
│    (Ouroboros Self-Evolution) │ partir da análise de traços repetidos.     │
├───────────────────────────────┼─────────────────────────────────────────────┤
│ 4. Federated / Mesh Skills    │ Importadas de nós remotos do mesh ANP com   │
│                               │ verificação de assinatura HMAC e sandbox.   │
└───────────────────────────────┴─────────────────────────────────────────────┘
```

---

## 3. Schema Canônico: `SkillSpec`

Definido em `hermes/platform/skills/spec.py`, o schema canônico estabelece os metadados e contratos:

```python
@dataclass
class SkillSpec:
    name: str                                        # Identificador kebab-case (ex: "deploy-k8s-pod")
    description: str                                 # Sumário sucinto do que o procedimento realiza
    version: str = "1.0.0"                           # SemVer 2.0 (MAJOR.MINOR.PATCH)
    author: str = "haos-core"                        # Postura, operador ou agente autor
    license: str = "Apache-2.0"                      # Licença de execução/distribuição
    dependencies: List[str] = field(default_factory=list) # Skills pré-requisito (ex: ["git-worktree@1.2.0"])
    capabilities_required: List[str] = field(default_factory=list) # Capabilities necessárias (ex: ["mcp:bash", "lsp"])
    postures_allowed: List[str] = field(default_factory=list)     # Restrição de posturas (ex: ["coder", "architect"])
    entry_script: Optional[str] = None               # Código ou script executável do procedimento
    procedural_steps: List[str] = field(default_factory=list)     # Passos sequenciais determinísticos
    input_schema: Dict[str, Any] = field(default_factory=dict)    # Schema JSON-schema dos parâmetros de entrada
    output_schema: Dict[str, Any] = field(default_factory=dict)   # Schema JSON-schema das evidências de saída
    status: SkillLifecycleStatus = "candidate"       # candidate | sandbox | eval | active | deprecated
    eval_score: Optional[float] = None               # Score atingido no gate de avaliação (0.0 a 1.0)
    checksum_sha256: str = ""                        # Hash de integridade de supply chain
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
```

---

## 4. Ciclo de Vida Formal e Máquina de Estados

Nenhuma skill gerada por LLM ou importada da rede pode ser executada em produção sem passar pelo pipeline:

```
                  ┌──────────────────────────────────────────────┐
                  │                  CANDIDATE                   │
                  │  (Descoberta via Ouroboros / Proposta inicial│
                  └──────────────────────┬───────────────────────┘
                                         │
                                         ▼ [advance_to_sandbox]
                                         │ • Validação de checksum SHA-256
                                         │ • Resolução estrita de dependências
                                         │ • Resolução de capabilities
                  ┌──────────────────────┴───────────────────────┐
                  │                   SANDBOX                    │
                  │ (Execução isolada em worktree efêmero Kilo)  │
                  └──────────────────────┬───────────────────────┘
                                         │
                                         ▼ [advance_to_eval]
                                         │ • Bateria de testes automatizados
                                         │ • Simulação em mock de ambiente
                  ┌──────────────────────┴───────────────────────┐
                  │                    EVAL                      │
                  │ (Verificação de pontuação: score >= baseline)│
                  └──────────────────────┬───────────────────────┘
                                         │
                                         ▼ [promote_to_active]
                                         │ • eval_score >= min_eval_score (0.80)
                                         │ • Verificação anti-regressão
                  ┌──────────────────────┴───────────────────────┐
                  │                   ACTIVE                     │
                  │ (Disponível no SkillRegistry para dispatch)  │
                  └──────────────────────┬───────────────────────┘
                                         │
                                         ▼ [deprecate]
                  ┌──────────────────────┴───────────────────────┐
                  │                 DEPRECATED                   │
                  │ (Substituída por versão SemVer superior)     │
                  └──────────────────────────────────────────────┘
```

### Regras de Transição Invariantes
1. **Transição Reversa Proibida:** É terminantemente proibido regredir de `active` para `candidate` ou reativar diretamente uma versão `deprecated`. Para atualizar, emite-se uma nova versão SemVer (`bump version`).
2. **Gate de Avaliação Estrito:** Se `eval_score < min_eval_score` (default `0.80`), o método `promote("active")` levanta `PermissionError` fail-closed.
3. **Supply-Chain Integrity:** A alteração de qualquer caractere em `entry_script` ou `procedural_steps` invalida o `checksum_sha256`, impedindo a carga da skill até novo cômputo e aprovação.

---

## 5. Mecanismo de Síntese Procedural (`SkillGenerator`)

O `SkillGenerator` transforma experiência empírica repetitiva em procedimentos formais:

```
[ TaskExecutionRecord ] ──┐
[ TaskExecutionRecord ] ──┼──► [ Frequency Analysis ] ──► [ Pattern Identification ] ──► [ Candidate SkillSpec ]
[ TaskExecutionRecord ] ──┘     (Min Frequency >= 2)       (Sequência de Ações)          (SemVer 1.0.0-candidate)
```

1. **Ingestão de Traços:** Coleta `TaskExecutionRecord` contendo nome da tarefa, sequência de ações executadas com sucesso, ferramentas utilizadas e contexto.
2. **Mineração de Sub-sequências:** Identifica subsequências de ações contíguas que se repetem em execuções bem-sucedidas com frequência $\ge \text{min\_pattern\_frequency}$.
3. **Geração do Candidato:** Instancia um `SkillSpec` com `status="candidate"`, gera passos procedurais parametrizáveis e calcula o checksum SHA-256 inicial.

---

## 6. Injeção Cache-Aware e Preservação do Prompt Cache

### A Regra de Ouro do Prompt Cache
> **Invariante:** O system prompt do agente é estável por byte durante toda a vida da sessão de conversa. Mutações dinâmicas de ferramentas ou injeção de novos blocos de prompt no meio da sessão invalidam o prefix cache do modelo e multiplicam exponencialmente o custo e a latência de inferência.

### Como o HAOS entrega as Skills ao Agente:
1. **Sumário Estático no System Prompt:**
   - O agente recebe apenas um índice ultracompacto (nome e descrição em 1 linha) das skills `active` disponíveis no boot da sessão.
2. **Injeção Just-in-Time como Mensagem de Usuário / Tool Call:**
   - Quando o agente decide executar uma skill, o procedimento completo é carregado dinamicamente via comando ou chamada de ferramenta (ex.: `read_skill("deploy-k8s")`), aparecendo como turno de mensagem ou retorno de ferramenta, **nunca mutando o system prompt prefix-cached**.
3. **Comando Slash `/skills` com Invalidação Deferida:**
   - Atualizações pelo operador humano via `/skills install` aplicam-se por padrão na **próxima sessão** (deferred). O uso de `--now` é uma escolha consciente e opt-in de invalidar o cache da sessão corrente.

---

## 7. Resolução Semântica e Conflitos no `SkillRegistry`

O `SkillRegistry` gerencia o catálogo em memória com suporte a múltiplas versões:
- `get(name, version=None)`: Se a versão for omitida, retorna deterministicamente a maior versão estável (`major.minor.patch`).
- **Resolução de Colisão:** Tentar registrar a mesma versão para um nome existente levanta `ValueError` a menos que `overwrite=True` seja explicitamente declarado.
- **Depreciação Graciosa:** Quando `Skill-B v2.0.0` é ativada, `Skill-B v1.x` é marcada como `deprecated`, permitindo que tarefas em andamento terminem sua execução com a versão com que foram iniciadas.
