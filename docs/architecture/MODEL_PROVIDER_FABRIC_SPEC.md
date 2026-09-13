# HAOS Model & Provider Fabric Specification

- **Status:** Accepted (Canonical SOTA Specification)
- **Layer:** Model & Provider Routing Fabric (K6 / Etapa 6)
- **Module Path:** `hermes/platform/models/`

---

## 1. O Axioma Arquitetural Fundamental: `Model != Provider`

Na quase totalidade das arquiteturas de agentes legadas, "modelo" e "provedor" são tratados como sinônimos ou strings acopladas (`provider/model-name`). Esse erro conceitual gera duas falhas desastrosas em produção:
1. **Vendor Lock-in Artificial:** O sistema fica preso a uma única API comercial para acessar uma família de modelos.
2. **Degradação Silenciosa de Raciocínio (Silent Model Degradation):** Quando o provedor primário de um modelo de fronteira (ex: Claude 3.7 Sonnet) sofre rate limit ou timeout, sistemas ingênuos fazem fallback para modelos menores e mais baratos (ex: Claude 3 Haiku ou GPT-4o-mini). O agente continua executando, mas seu raciocínio colapsa, gerando código corrompido, revisões rasas e alucinações.

### O Axioma HAOS
> **Um Modelo é uma Identidade Cognitiva** com capacidades específicas de raciocínio, contexto e pesos matemáticos.  
> **Um Provedor é apenas uma Venue de Execução** (um endpoint de API que oferece acesso àquela identidade com uma determinada latência, custo e limite de taxa).

---

## 2. Proibição de Degradação Silenciosa (`ModelDegradationPreventedError`)

O HAOS estabelece como **invariante de segurança inegociável**:

```
                 Requisição de Inferência (Postura Coder)
                                   │
                                   ▼
                  Modelo Exato: `deepseek-v3`
                                   │
                     ┌─────────────┴─────────────┐
                     ▼                           ▼
          [ Provedor 1: DeepSeek ]      [ Provedor 2: OpenRouter ]
          (Prioridade 1 - Direto)       (Prioridade 2 - Fallback)
                     │                           │
                     ▼                           ▼
            Falhou? (429/500)             Falhou? (Timeout)
                     │                           │
                     └─────────────┬─────────────┘
                                   │
                                   ▼
          Todas as rotas para o modelo exato esgotadas!
                                   │
                     ┌─────────────┴─────────────┐
                     ▼                           ▼
            [ PROIBIDO: Degradar ]     [ CORRETO: Fail-Closed ]
            Fallback p/ Haiku/Mini     Levanta `ModelDegradationPreventedError`
            (Rejeitado terminantemente) ou emite evento ao Orchestrator
```

Se todas as venues de execução cadastradas para um modelo exato estiverem em quarentena ou sob rate-limit, o `ExactModelFailoverRouter` levanta `ModelDegradationPreventedError`. **Nenhum agente sofre downgrade silencioso de inteligência**.

---

## 3. Schemas Canônicos e Identidade de Modelo

Definido em `hermes/platform/models/unified_fabric.py`:

```python
@dataclass(frozen=True)
class ModelIdentity:
    family: str                      # Ex: "deepseek-v3", "claude-3-7-sonnet"
    variant: str = "default"         # Ex: "default", "thinking", "extended-context"
    strict_identity: bool = True     # Proíbe terminantemente rotas com pesos diferentes

@dataclass
class ProviderPriorityEntry:
    provider_id: str                 # Ex: "deepseek", "openrouter", "together"
    provider_model_id: str           # String específica da API (ex: "deepseek/deepseek-chat")
    priority: int = 1                # Prioridade de despacho (1 = mais prioritário)
    metadata: Dict[str, Any] = field(default_factory=dict)

@dataclass
class ModelProfile:
    id: str                          # Ex: "profile-coder"
    model_name: str                  # Nome canônico de referência
    context_window: int              # Janela de contexto (ex: 128000)
    max_output: int                  # Limite de tokens de saída (ex: 8192)
    parameters: Dict[str, Any]       # Temperature, top_p, etc.
    posture_assignments: List[str]   # ["coder", "implementer"]
    model_identity: Optional[ModelIdentity] = None
    provider_priority_list: List[ProviderPriorityEntry] = field(default_factory=list)
```

---

## 4. Matriz Canônica de Posturas do HAOS

Para maximizar a precisão operacional e a especialização de tarefas, o HAOS distribui as posturas em perfis de modelo dedicados:

| Postura | Modelo Exato | Provedor Primário | Provedor Secundário (Failover) |
|---|---|---|---|
| **`architect`** | `claude-3-7-sonnet` | Anthropic Direct (`claude-3-7-sonnet-20250219`) | OpenRouter (`anthropic/claude-3.7-sonnet`) |
| **`coder`** | `deepseek-v3` | DeepSeek Direct (`deepseek-chat`) | OpenRouter (`deepseek/deepseek-chat`) |
| **`reviewer`** | `claude-3-5-sonnet` | Anthropic Direct (`claude-3-5-sonnet-20241022`) | OpenRouter (`anthropic/claude-3.5-sonnet`) |
| **`researcher`** | `deepseek-v3` | DeepSeek Direct (`deepseek-chat`) | OpenRouter (`deepseek/deepseek-chat`) |

---

## 5. Resiliência: Circuit Breaker Composto & Rate Limit Handling

O roteador monitora o estado de saúde de cada rota `(provider, model_identity)` através de chave composta:
$$\text{route\_key} = \text{provider\_id} + \text{":"} + \text{family} + \text{":"} + \text{variant}$$

### Regras de Despacho do `ExactModelFailoverRouter`
1. **Ordenação por Prioridade:** Rotas são avaliadas em ordem estrita de prioridade configurada (`priority = 1`, depois `priority = 2`, etc.).
2. **Checagem de Rate Limit Ativo:** Se o provedor recebeu um HTTP `429` com header `Retry-After`, a venue é ignorada até a expiração do timestamp.
3. **Checagem de Circuit Breaker:** Se o provedor acumulou 3 falhas consecutivas (erros 5xx ou timeouts), o breaker entra em estado `OPEN` durante a janela de cooldown (60s).
4. **Decisão Auditável (`RoutingDecision`):** Toda seleção registra a venue escolhida, número de tentativas avaliadas e a lista de provedores que foram ignorados por falha/limite.
5. **Recuperação Automática:** Uma requisição concluída com sucesso via `record_success` limpa o status de rate-limit e fecha o circuit breaker.

---

## 6. Preservação do Prompt Cache e Invariantes de Custo

1. **Parâmetros Estáveis:** O `ModelProfile` congela parâmetros como `temperature`, `top_p` e limites de output, evitando variações que degradem a estabilidade do prefix cache.
2. **Mapeamento Transparente de Tokens:** As diferenças de nomenclatura entre provedores para o mesmo modelo são isoladas na camada de adaptação; o prompt original permanece byte-estável.
3. **Isolamento de Contabilidade:** Custos por milhão de tokens de entrada e saída são medidos por provedor, alimentando as métricas do `MetricsCollector` para auditoria financeira sem misturar custos entre venues.
