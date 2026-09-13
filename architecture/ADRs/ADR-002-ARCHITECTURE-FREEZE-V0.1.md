# ADR-002: Architecture Freeze v0.1 — Phase 1 Platform Kernel

- **Status:** Accepted (Frozen v0.1 Contracts)
- **Date:** 2026-09-08
- **Phase:** Phase 1 — Hermes Platform Kernel
- **Supersedes:** Rascunhos exploratórios e interfaces ad-hoc pré-kernel
- **Governed By:** ADR-001 (HAOS Multi-Agent SOTA)

---

## 1. Contexto e Motivação

Após a concepção detalhada e especificação formal dos 6 Fabrics da plataforma:
1. Memory Fabric (`docs/architecture/MEMORY_FABRIC_SPEC.md`)
2. Skills / Procedural Intelligence (`docs/architecture/SKILLS_PROCEDURAL_SPEC.md`)
3. Universal Capability / Plugins (`docs/architecture/CAPABILITY_FABRIC_SPEC.md`)
4. MCP Unified Fabric (`docs/architecture/MCP_FABRIC_SPEC.md`)
5. LSP Unified Intelligence (`docs/architecture/LSP_FABRIC_SPEC.md`)
6. Model & Provider Fabric (`docs/architecture/MODEL_PROVIDER_FABRIC_SPEC.md`)

Inicia-se a **Phase 1 — Hermes Platform Kernel**.  
Para viabilizar a implementação controlada de um **Vertical Slice End-to-End** sem retrabalho, acoplamento prematuro ou quebras de compatibilidade com o upstream do Hermes, este ADR declara o **Architecture Freeze v0.1**.

---

## 2. Invariante do Architecture Freeze

> **Contrato Congelado:** As 10 interfaces centrais abaixo são formalmente imutáveis em seus campos e assinaturas essenciais para a versão `0.1.x`.  
> Qualquer adição deve ser **puramente aditiva com defaults opcionais**. Quebras de contrato exigem emissão de nova versão semântica de interface e novo ADR formal.

---

## 3. As 10 Interfaces Centrais Congeladas (v0.1)

```
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                                 OS 10 CONTRATOS NUCLEARES                              │
├───────────────────────────────┬────────────────────────────────────────────────────────┤
│ 1. `TaskSpec`                 │ Intenção do usuário, critérios de aceitação e bounds. │
├───────────────────────────────┼────────────────────────────────────────────────────────┤
│ 2. `AssignmentSpec`           │ Vinculação imutável: Task + Postura + Workspace Lease. │
├───────────────────────────────┼────────────────────────────────────────────────────────┤
│ 3. `PostureSpec` / `AgentSpec`│ Papel cognitivo, postura, skills e limites operacionais.│
├───────────────────────────────┼────────────────────────────────────────────────────────┤
│ 4. `ContextPackage`           │ Pacote de contexto prefix-cached montado p/ inferência.│
├───────────────────────────────┼────────────────────────────────────────────────────────┤
│ 5. `MemoryItem`               │ Fato atômico com proveniência, escopo e supersessão.   │
├───────────────────────────────┼────────────────────────────────────────────────────────┤
│ 6. `CapabilityMetadata`       │ Descritor universal de ferramentas (MCP/LSP/Kilo/etc). │
├───────────────────────────────┼────────────────────────────────────────────────────────┤
│ 7. `SkillSpec`                │ Procedimento versionado SemVer 2.0 com checksum SHA-256│
├───────────────────────────────┼────────────────────────────────────────────────────────┤
│ 8. `PluginManifest`           │ Extensão com permissões, hooks e isolamento de runtime.│
├───────────────────────────────┼────────────────────────────────────────────────────────┤
│ 9. `ModelProfile`             │ Identidade cognitiva (pesos/parâmetros), não provider. │
├───────────────────────────────┼────────────────────────────────────────────────────────┤
│ 10. `ProviderRoute`           │ Venue física de execução (API endpoint, prioridade).   │
└───────────────────────────────┴────────────────────────────────────────────────────────┘
```

### 3.1. `TaskSpec` (Intenção Imutável)
*Módulo:* `hermes/platform/tasks/spec.py`
```python
@dataclass
class TaskSpec:
    id: str                                  # UUID da tarefa
    goal: str                                # Intenção descritiva do usuário
    acceptance_criteria: List[str]           # Critérios verificáveis de sucesso
    assigned_posture: str                    # "architect" | "coder" | "reviewer" | "researcher"
    scope: str = "project"                   # "private" | "team" | "project" | "global"
    required_capabilities: List[str] = field(default_factory=list)
    timeout_sec: float = 300.0
    status: str = "pending"                  # pending | scheduled | leased | completed | failed
    created_at: float = field(default_factory=time.time)
```

### 3.2. `AssignmentSpec` (Lease de Execução & Decisão de Runtime)
*Módulo:* `hermes/platform/execution/assignment.py`
```python
@dataclass
class AssignmentSpec:
    task_id: str                             # Chave estrangeira para TaskSpec
    task_revision: int
    run_id: str
    execution_shape: str                     # "worker_lane" | "in_process" | "remote_node"
    lane: str                                # "kilo" | "hermes_cli" | "sandbox"
    agent_mode: str                          # "ephemeral" | "long_running"
    posture_id: str                          # Postura vinculada (ex: "coder")
    model_profile_id: str                    # Perfil de modelo selecionado
    resolved_model_family: str               # "deepseek-v3"
    resolved_model_variant: str              # "default"
    ordered_provider_routes: List[Dict[str, Any]] = field(default_factory=list)
    resolved_capabilities: List[str] = field(default_factory=list)
    workspace_path: str = ""
```

### 3.3. `PostureSpec` (Identidade e Restrições do Agente)
*Módulo:* `hermes/platform/tasks/posture.py`
```python
@dataclass
class PostureSpec:
    id: str                                  # Ex: "coder", "reviewer", "architect"
    name: str                                # Nome legível
    allowed_tools: List[str]                 # Lista de ferramentas autorizadas
    forbidden_tools: List[str]               # Lista de ferramentas estritamente proibidas
    skills_preferred: List[str]              # Skills prioritárias
    system_prompt_template: str              # Template base de instrução
```

### 3.4. `ContextPackage` (Envelope de Prompt Prefix-Cached)
*Módulo:* `hermes/platform/context/`
```python
@dataclass
class ContextPackage:
    task_id: str
    posture_id: str
    system_prompt_stable_prefix: str         # Bloco imutável para prefix-cache
    declarative_facts: List[Dict[str, Any]]  # Fatos atômicos ativos no escopo
    adr_summaries: List[Dict[str, Any]]      # Sumário de decisões de arquitetura
    tool_definitions: List[Dict[str, Any]]   # Tool schemas congelados
    token_budget: int = 8192
```

### 3.5. `MemoryItem` (Conhecimento Declarativo)
*Módulo:* `hermes/platform/context/memory/schemas.py`
```python
@dataclass
class MemoryItem:
    id: str
    content: str
    scope: str                               # "private" | "team" | "project" | "global"
    confidence: float                        # 0.0 a 1.0
    provenance: List[str]                    # URIs rastreáveis da origem
    author: str
    created_at: float
    valid_from: float
    valid_to: Optional[float] = None
    supersedes: Optional[str] = None
    superseded_by: Optional[str] = None
    status: str = "active"                   # "candidate" | "active" | "superseded" | "expired"
```

### 3.6. `CapabilityMetadata` (Descritor Universal de Ferramentas)
*Módulo:* `hermes/platform/capabilities/universal_registry.py`
```python
@dataclass
class CapabilityMetadata:
    id: str                                  # Ex: "lsp:python", "mcp:github"
    name: str
    category: CapabilityCategory             # PLUGIN, MCP, LSP, KILO, VISION, BROWSER, etc.
    description: str
    provider_source: str
    failure_policy: FailurePolicy            # FAIL_OPEN | FAIL_CLOSED
    allowed_postures: List[str]
    timeout_sec: float = 30.0
    schema: Dict[str, Any] = field(default_factory=dict)
```

### 3.7. `SkillSpec` (Inteligência Procedural)
*Módulo:* `hermes/platform/skills/spec.py`
```python
@dataclass
class SkillSpec:
    name: str                                # kebab-case
    description: str
    version: str = "1.0.0"                   # SemVer 2.0
    author: str = "haos-core"
    dependencies: List[str] = field(default_factory=list)
    capabilities_required: List[str] = field(default_factory=list)
    postures_allowed: List[str] = field(default_factory=list)
    procedural_steps: List[str] = field(default_factory=list)
    status: str = "candidate"                # candidate | sandbox | eval | active | deprecated
    eval_score: Optional[float] = None
    checksum_sha256: str = ""
```

### 3.8. `PluginManifest` (Extensão Segura)
*Módulo:* `hermes/platform/extensions/registry.py`
```python
@dataclass
class PluginManifest:
    id: str
    name: str
    version: str
    entry_point: str
    capabilities_provided: List[str]
    required_trust_tier: str                 # CORE, VERIFIED, COMMUNITY, UNTRUSTED
```

### 3.9. `ModelProfile` (Identidade Cognitiva)
*Módulo:* `hermes/platform/models/profiles.py` & `unified_fabric.py`
```python
@dataclass
class ModelProfile:
    id: str                                  # Ex: "profile-coder"
    model_name: str                          # Ex: "deepseek-v3"
    context_window: int                      # Ex: 128000
    max_output: int                          # Ex: 8192
    parameters: Dict[str, Any]               # temperature, top_p, etc.
    posture_assignments: List[str]
    model_identity: ModelIdentity
    provider_priority_list: List[ProviderPriorityEntry]
```

### 3.10. `ProviderRoute` / `ProviderPriorityEntry` (Venue de Execução)
*Módulo:* `hermes/platform/models/profiles.py`
```python
@dataclass
class ProviderPriorityEntry:
    provider_id: str                         # Ex: "deepseek", "openrouter"
    provider_model_id: str                   # ID na API do provedor
    priority: int = 1                        # Ordem de tentativa
    metadata: Dict[str, Any] = field(default_factory=dict)
```

---

## 4. Camada de Compatibilidade com Hermes Upstream

Para manter o drift em **0 commits** e permitir rebases e upgrades triviais:
1. **Zero invasão no core:** O core do Hermes (`agent/`, `tools/`, `gateway/`) permanece intocado como *narrow waist*.
2. **Namespace PEP-420 Puro:** O HAOS reside 100% dentro de `hermes/platform/` com **zero arquivos `__init__.py`**.
3. **Ponto de Seam Não Invasivo:** O `HermesCliLaneWorker` invoca a CLI do Hermes via `--cli --accept-hooks ... chat -q` em worktree isolado Kilo, comunicando-se via arquivos estruturados `.haos/spec.json` e `.haos/result.json`.

---

## 5. Event Bus Canônico & Tópicos de Telemetria (v0.1)

O `KnowledgeEventBus` / `EventStore` emite e consome os seguintes eventos padronizados:

- `task.created`: Nova intenção de usuário submetida.
- `assignment.resolved`: Task vinculada a uma Postura e Workspace Lease.
- `model.resolved`: Perfil de modelo selecionado para a postura.
- `provider.failed_over`: Failover exato de provedor disparado por falha/rate-limit.
- `capability.resolved`: Toolset filtrado e validado contra breakers.
- `context.built`: ContextPackage prefix-cached montado para a inferência.
- `worker.started`: Lane efêmera Kilo instanciada e processo disparado.
- `artifact.created`: Patch git, relatório de teste ou documento gerado.
- `review.completed`: Parecer do Reviewer (aprovado ou reprovado com blast radius).
- `task.completed`: Critérios de aceitação validados; task finalizada.
