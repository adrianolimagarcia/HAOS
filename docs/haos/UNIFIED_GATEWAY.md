# HAOS Universal Protocol Gateway: Unificação A2A + MCP

## 1. Visão Geral

O **UniversalProtocolGateway** do HAOS centraliza em um único barramento de protocolo soberano o tráfego de:
* **MCP (Model Context Protocol):** Comunicação entre agentes e ferramentas heterogêneas via JSON-RPC.
* **A2A (Agent-to-Agent):** Delegação e troca de mensagens entre agentes pares e subagentes.
* **ANP (Agent Network Protocol) & ACP (IDE Adapters):** Federação e integração com interfaces externas.

Operando sob o princípio de **Data Plane vs. Control Plane**, o gateway provê roteamento dinâmico, telemetria unificada (`event_store`), isolamento de falhas e reputação de nós remotos.

---

## 2. Arquitetura do Barramento Unificado

```
                     ┌───────────────────────────────────┐
                     │          HAOS Core Agent          │
                     └─────────────────┬─────────────────┘
                                       │ ProtocolEnvelope
                                       ▼
                     ┌───────────────────────────────────┐
                     │     UniversalProtocolGateway      │
                     │ (hermes/platform/protocols/gateway)
                     └───────┬───────────────────┬───────┘
                             │                   │
               ProtocolType.MCP                  │ ProtocolType.A2A
                             ▼                   ▼
                     ┌───────────────┐   ┌───────────────┐
                     │  Local MCP    │   │  A2A Peer     │
                     │  Aggregator   │   │  Responder /  │
                     │ (Namespacing, │   │  Federated    │
                     │   Breaker)    │   │  Directory    │
                     └───────────────┘   └───────────────┘
```

---

## 3. Envelope Canônico (`ProtocolEnvelope`)

Todas as mensagens transitam encapsuladas em envelopes com garantia de fronteira de confiança (`TrustBoundary`):

* `protocol_type`: `ProtocolType.MCP`, `ProtocolType.A2A`, etc.
* `sender` & `recipient`: Identificadores de remetente e destinatário.
* `trust_boundary`: `KERNEL`, `LOCAL_SECURE`, `AGENT_SANDBOX`, `FEDERATED`, `UNTRUSTED`.
* `payload`: Carga JSON-RPC ou parâmetros da chamada.

---

## 4. Integração do MCP Gateway

Quando uma mensagem com `protocol_type == ProtocolType.MCP` chega ao gateway:
1. Métodos `tools/list` são resolvidos consultando o catálogo unificado de ferramentas do `LocalMCPAggregator`.
2. Chamadas `tools/call` com prefixo `<server>_<tool>` passam pelo router do agregador, que aplica o circuit breaker, isolamento de timeout e despacha para o servidor downstream correto.
3. Eventos `gateway.envelope_dispatched` e `gateway.envelope_processed` são registrados no `EventStore` para auditoria e rastreabilidade fim-a-fim.
