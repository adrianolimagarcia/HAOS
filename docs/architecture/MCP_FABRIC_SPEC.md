# HAOS MCP Fabric Specification

- **Status:** Accepted (Canonical SOTA Specification)
- **Layer:** MCP Unified Fabric (K4 / Etapa 4)
- **Module Path:** `hermes/platform/capabilities/mcp/`

---

## 1. Visão Geral e Filosofia

O **Model Context Protocol (MCP)** é o padrão aberto para estender agentes com ferramentas externas via transporte stdio ou SSE. No entanto, conectar dezenas de servidores MCP diretamente ao prompt do LLM cria quatro falhas críticas em sistemas de produção:
1. **Context Bloat & Token Tax:** Centenas de schemas JSON no prompt consomem 20k–50k tokens antes mesmo da primeira palavra do usuário.
2. **Quebra do Prompt Cache:** Servidores que entram e saem dinamicamente alteram os bytes do system prompt, invalidando o prefix cache do provedor e multiplicando a conta de API.
3. **Risco de Supply Chain & Prompt Injection:** Servidores MCP não verificados podem injetar ferramentas maliciosas ou exfiltrar dados.
4. **Cascata de Falhas:** Se um servidor MCP trava ou dá timeout no boot, o turno inteiro do agente é abortado.

O **HAOS MCP Fabric** resolve esses problemas com:
- **Trust Tiers Criptográficos e Operacionais** (`CORE` > `VERIFIED` > `COMMUNITY` > `UNTRUSTED`).
- **MCP Packs**: Agrupamento modular de servidores com escopo por postura e permissões estritas.
- **MCP Circuit Breaker**: Isolamento automático de servidores instáveis com recuperação graciosa.
- **MCPToolFilter**: Resolução determinística por `(TaskSpec, Posture)` limitando ferramentas por tarefa.

---

## 2. Níveis de Confiança (Trust Tiers)

Cada servidor ou pack MCP opera sob um nível estrito de confiança:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                            MCP TRUST TIERS                                  │
├───────────────────┬─────────────────────────────────────────────────────────┤
│ `CORE`            │ Servidores nativos e essenciais da plataforma           │
│                   │ (ex: `mcp:filesystem`, `mcp:memory`). Testados e        │
│                   │ blindados contra injeção.                               │
├───────────────────┼─────────────────────────────────────────────────────────┤
│ `VERIFIED`        │ Servidores auditados de terceiros ou vendors parceiros  │
│                   │ (ex: `mcp:github`, `mcp:slack`). Assinaturas validadas. │
├───────────────────┼─────────────────────────────────────────────────────────┤
│ `COMMUNITY`       │ Servidores de código aberto ou locais sem auditoria     │
│                   │ formal. Permitidos apenas com sandbox de filesystem.    │
├───────────────────┼─────────────────────────────────────────────────────────┤
│ `UNTRUSTED`       │ Servidores em quarentena ou recém-descobertos na rede.  │
│                   │ Não recebem segredos nem acesso a lanes com escrita.    │
└───────────────────┴─────────────────────────────────────────────────────────┘
```

A hierarquia é transitiva:
$$\text{CORE} \succ \text{VERIFIED} \succ \text{COMMUNITY} \succ \text{UNTRUSTED}$$
Uma tarefa que exige nível mínimo `VERIFIED` rejeita automaticamente qualquer ferramenta com tier inferior.

---

## 3. Agrupamento Modular em `MCPPack`

Definido em `hermes/platform/capabilities/mcp/unified_fabric.py`:

```python
@dataclass
class MCPPack:
    pack_id: str                                     # Identificador único (ex: "dev_tools")
    name: str                                        # Nome legível
    servers: List[str] = field(default_factory=list) # Servidores membros (ex: ["github", "bash"])
    trust_tier: MCPTrustTier = MCPTrustTier.COMMUNITY
    allowed_postures: List[str] = field(default_factory=list) # Posturas autorizadas
    description: str = ""
```

### Packs Canônicos Padrão (`DEFAULT_MCP_PACKS`)
- **`core`**: Servidores básicos essenciais (`filesystem`, `memory`). Permitido para todas as posturas (`*`).
- **`dev_tools`**: `github`, `bash`, `fetch`. Exclusivo para a postura `coder`.
- **`review_tools`**: `git`, `diff`, `ast_inspect`. Exclusivo para `reviewer`.
- **`arch_tools`**: `diagram`, `db_schema`, `graph`. Exclusivo para `architect`.

---

## 4. Resiliência e Circuit Breaker (`MCPCircuitBreaker`)

O `MCPCircuitBreaker` monitora falhas de inicialização, timeouts e erros de chamada de ferramentas MCP:

```
                            Chamada ao Servidor MCP
                                       │
                                       ▼
                             [ Ocorreu Falha? ]
                                       │
                        ┌──────────────┴──────────────┐
                        ▼                             ▼
                       NÃO                           SIM
                        │                             │
                        ▼                             ▼
                [ Sucesso: reseta ]          [ Falhas consecutivas >= 3? ]
                [ contador de falhas ]                 │
                                            ┌──────────┴──────────┐
                                            ▼                     ▼
                                           NÃO                   SIM
                                            │                     │
                                            ▼                     ▼
                                     [ Incrementa ]      [ Trip Circuit Breaker! ]
                                     [ contador   ]      [ Estado: OPEN (60s)    ]
                                                         [ Remove servidor do    ]
                                                         [ schema do agente      ]
```

### Parâmetros e Comportamento
- `failure_threshold`: 3 falhas consecutivas.
- `cooldown_sec`: 60 segundos em quarentena antes de tentar probe de reabilitação.
- `CircuitBreakerPolicy`:
  - `FAIL_OPEN` (default para ferramentas opcionais): o servidor falho é omitido do prompt; o turno continua sem travar o agente.
  - `FAIL_CLOSED` (para ferramentas críticas): aborta a execução com erro explícito para evitar corrupção de estado.

---

## 5. Filtragem Dinâmica por Tarefa e Postura (`MCPToolFilter`)

O `MCPToolFilter` atua antes da montagem do payload da API de inferência:

```python
filtered_tools = MCPToolFilter.filter_tools_for_task(
    tool_schemas=all_registered_tools,
    task=task_spec,
    posture=agent_posture,
    max_tools=50,
    circuit_breaker=active_breaker
)
```

### Regras de Filtragem
1. **Matching de Packs:** Apenas ferramentas pertencentes a packs permitidos para a postura (`posture.id`) e solicitados pela tarefa (`task.mcp_packs`).
2. **Atribuição de Servidor:** Ferramentas são identificadas via `_server`, metadado `server_name` ou convenção `server__tool_name`.
3. **Exclusão de Servidores em Quarentena:** Servidores com breaker no estado `OPEN` são purgados do array de ferramentas.
4. **Priorização por Postura:** Ferramentas preferidas da postura (`skills_preferred`) são ordenadas para o topo da lista.
5. **Teto Máximo (`max_tools = 50`):** Garante que o schema de ferramentas permaneça dentro de limites previsíveis de latência e consumo de tokens.

---

## 6. Integração com Dispatcher e Lane Kilo

1. O `HermesCliLaneWorker` no momento do spawn invoca `prepare_tools()` delegando ao `MCPToolFilter`.
2. A lista de ferramentas resolvida é escrita no manifesto `.haos/spec.json` dentro do worktree efêmero.
3. O subprocesso do worker inicia com o conjunto estrito de ferramentas e permanece **100% congelado durante a vida da tarefa**, preservando o prefix prompt-cache.
