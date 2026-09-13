# HAOS Universal Capability & Plugin Fabric Specification

- **Status:** Accepted (Canonical SOTA Specification)
- **Layer:** Plugin + Capability Fabric (K3 / Etapa 3)
- **Module Path:** `hermes/platform/capabilities/`

---

## 1. Visão Geral e Filosofia

O HAOS supera o modelo legado de "ferramentas avulsas e plugins descontrolados" através do **Universal Capability Registry**. No HAOS:
1. **Unificação Universal:** Ferramentas locais, servidores MCP remotos, language servers LSP, worktrees efêmeros Kilo, workers multimodais de visão/áudio e instâncias de browser compartilham um **mesmo modelo formal de registro e ciclo de vida**.
2. **Sandboxing Cognitivo e Operacional por Postura:** Nenhuma ferramenta de escrita ou execução livre é exposta para posturas analíticas (`architect`, `reviewer`). O agente recebe estritamente as capacidades necessárias para seu papel.
3. **Políticas de Resiliência Determinísticas:** Tratamento explícito de indisponibilidade via `FAIL_CLOSED` (essencial à segurança) ou `FAIL_OPEN` (degradação graciosa com fallback).
4. **Agrupamento Semântico em Packs:** Configurações coesas de capacidades (`arch_pack`, `dev_pack`, `review_pack`) que simplificam a orquestração multiagente.

---

## 2. Taxonomia Universal de Categorias

Toda capacidade registrada na plataforma pertence a uma categoria formal:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                       CATEGORIAS DE CAPABILIDADE                            │
├───────────────────┬─────────────────────────────────────────────────────────┤
│ `PLUGIN`          │ Extensões in-process ou RPC do ecossistema Hermes.      │
├───────────────────┼─────────────────────────────────────────────────────────┤
│ `MCP`             │ Ferramentas externas conectadas via Model Context       │
│                   │ Protocol (stdio / SSE).                                 │
├───────────────────┼─────────────────────────────────────────────────────────┤
│ `LSP`             │ Language Server Protocol para análise estática, blast   │
│                   │ radius, jump-to-definition e diagnósticos em tempo real.│
├───────────────────┼─────────────────────────────────────────────────────────┤
│ `KILO`            │ Gerenciamento de Git Worktrees efêmeros e branches      │
│                   │ isoladas por tarefa (`haos/task-<id>`).                 │
├───────────────────┼─────────────────────────────────────────────────────────┤
│ `VISION`          │ Workers multimodais isolados (OCR, inspeção de          │
│                   │ diagramas de arquitetura e UI).                         │
├───────────────────┼─────────────────────────────────────────────────────────┤
│ `AUDIO`           │ Processamento de voz/áudio e transcrição Whisper.       │
├───────────────────┼─────────────────────────────────────────────────────────┤
│ `BROWSER`         │ Navegação web headless e automação Playwright.          │
├───────────────────┼─────────────────────────────────────────────────────────┤
│ `KNOWLEDGE`       │ Motor relacional GraphRAG e Obsidian Vault.             │
├───────────────────┼─────────────────────────────────────────────────────────┤
│ `ANP`             │ Agent Network Protocol para comunicação mesh remota.    │
└───────────────────┴─────────────────────────────────────────────────────────┘
```

---

## 3. Schema Canônico: `CapabilityMetadata`

Definido em `hermes/platform/capabilities/universal_registry.py`:

```python
class CapabilityCategory(str, Enum):
    PLUGIN = "plugin"
    MCP = "mcp"
    LSP = "lsp"
    KILO = "kilo"
    VISION = "vision"
    AUDIO = "audio"
    BROWSER = "browser"
    KNOWLEDGE = "knowledge"
    ANP = "anp"

class FailurePolicy(str, Enum):
    FAIL_OPEN = "fail_open"      # Degrada graciosamente: loga aviso e continua sem a ferramenta
    FAIL_CLOSED = "fail_closed"  # Bloqueia a execução da tarefa se a capacidade estiver doente

@dataclass
class CapabilityMetadata:
    id: str                                          # ID canônico (ex: "mcp:github", "lsp:python")
    name: str                                        # Nome legível por humanos
    category: CapabilityCategory                     # Categoria taxionômica
    description: str                                 # Sumário de função para contextualização
    provider_source: str                             # Origem ("builtin", "mcp_server", "hermes_plugin")
    version: str = "1.0.0"                           # SemVer
    failure_policy: FailurePolicy = FailurePolicy.FAIL_OPEN
    allowed_postures: List[str] = field(default_factory=list) # Posturas autorizadas ([] = todas)
    required_env: List[str] = field(default_factory=list)     # Chaves de ambiente / segredos
    cost_per_call: float = 0.0                       # Custo financeiro estimado por invocação
    timeout_sec: float = 30.0                        # Timeout de chamada
    health_check_fn: Optional[Callable[[], Union[bool, HealthStatus]]] = None
    enabled: bool = True
    schema: Dict[str, Any] = field(default_factory=dict)      # Tool schema OpenAI/Anthropic
```

---

## 4. Sandboxing por Postura (Cognitivo & Operacional)

O `CapabilityFilter` atua como barreira de segurança estrita impedindo alucinações destrutivas e contaminação de papéis:

```
┌─────────────────┬───────────────────────────────┬──────────────────────────────────────────┐
│ Postura         │ Toolsets Permitidos           │ Ferramentas Estritamente Proibidas       │
├─────────────────┼───────────────────────────────┼──────────────────────────────────────────┤
│ `architect`     │ `memory`, `knowledge`,        │ `terminal`, `write_file`, `execute_code`,│
│                 │ `obsidian`, `graphrag`, `read`│ `git_commit`, `rm`, `git_push`           │
├─────────────────┼───────────────────────────────┼──────────────────────────────────────────┤
│ `coder`         │ `terminal`, `write_file`,     │ `production_deploy`, `admin_keys`,       │
│                 │ `read_file`, `lsp`, `git`     │ `billing_settings`                       │
├─────────────────┼───────────────────────────────┼──────────────────────────────────────────┤
│ `reviewer`      │ `read_file`, `lsp`,           │ `write_file`, `terminal`, `git_commit`,  │
│                 │ `git_diff`, `run_tests`       │ `edit_file`, `bash`                      │
├─────────────────┼───────────────────────────────┼──────────────────────────────────────────┤
│ `researcher`    │ `web_search`, `fetch`,        │ `write_file`, `terminal`, `git_commit`,  │
│                 │ `browser_navigate`, `read`    │ `patch_file`                             │
└─────────────────┴───────────────────────────────┴──────────────────────────────────────────┘
```

### Invariantes de Sandboxing
1. **Reviewer Jamais Edita Código:** O `reviewer` avalia a conformidade com o ADR, analisa o blast radius pelo LSP e roda os testes. Se encontrar um erro, **emite um parecer com reprovação**, mas é incapaz de alterar o código diretamente.
2. **Architect Jamais Executa Terminal Livre:** O `architect` projeta a solução, consulta a memória federada e escreve a especificação/ADR no Obsidian. Comandos destrutivos de shell são filtrados antes de chegarem ao modelo.
3. **Coder Opera Apenas em Worktree Efêmero:** O `coder` tem acesso ao terminal e edição, mas opera estritamente dentro da raiz `.worktrees/task-<id>` provisionada pelo Lane Kilo.

---

## 5. Pacotes Canônicos de Capacidade (`CapabilityPacks`)

Para evitar a declaração repetitiva de dezenas de ferramentas em cada `TaskSpec`, o registro oferece **Capability Packs**:

- **`core_dev`**: `terminal`, `write_file`, `read_file`, `lsp:python`, `kilo:worktree`.
- **`arch_pack`**: `memory:federated`, `obsidian:vault`, `graphrag:query`, `read_file`, `vision:diagram`.
- **`review_pack`**: `read_file`, `git_diff`, `lsp:impact`, `run_tests`, `decision:check`.
- **`multimodal_pack`**: `vision:analyzer`, `vision:ocr`, `audio:transcribe`.
- **`research_pack`**: `web_search`, `fetch`, `browser:playwright`, `research:aggregate`.

O resolvedor (`resolve_capabilities`) expande os nomes dos packs de forma transparente e determinística:
```python
resolved = registry.resolve_capabilities(
    ["arch_pack"],
    posture="architect"
)
```

---

## 6. Políticas de Falha e Monitoramento de Saúde

A cada ciclo de orquestração, as ferramentas ativas são checadas via `health_check_fn`:

```
                       Início da Tarefa
                              │
                              ▼
                 [ Executa Health Check ]
                              │
               ┌──────────────┴──────────────┐
               ▼                             ▼
           Saudável                       Doente
               │                             │
               ▼                             ▼
       [ Inclui no Schema ]         Qual a FailurePolicy?
                                             │
                             ┌───────────────┴───────────────┐
                             ▼                               ▼
                      `FAIL_CLOSED`                     `FAIL_OPEN`
                             │                               │
                             ▼                               ▼
                     [ Aborta Tarefa ]              [ Degrada Gracioso ]
                     "Capacidade essencial          "Ferramenta removida
                      indisponível"                  do prompt com log"
```

1. **`FAIL_CLOSED`**: Utilizado em capacidades críticas de integridade (ex: `kilo:worktree`, `auth:broker`). Se o serviço falhar, o dispatcher recusa o início da tarefa, evitando commits não isolados ou vazamento de segredos.
2. **`FAIL_OPEN`**: Utilizado em capacidades auxiliares (ex: `vision:analyzer`, `web_search`). Se o worker remoto não responder, a ferramenta é silenciosamente omitida do schema enviado ao LLM, evitando alucinações de ferramentas inoperantes.

---

## 7. Preservação do Prompt Cache

Assim como nas Skills e Memória:
- **Zero mutação dinâmica intra-sessão:** O conjunto de ferramentas (`tools`) resolvido para a postura e tarefa é montado no **handshake de inicialização da sessão** e permanece congelado durante todo o diálogo daquela lane.
- A expansão ou ativação de novas capacidades aplica-se apenas na próxima sessão/worktree, garantindo que o prefix cache de tokens dos provedores não seja invalidado.
