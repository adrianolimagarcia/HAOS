# HAOS Civilization: Bot Research & Skill temporal-research

**Subsistema:** HAOS Civilization
**Bot:** Research (`id: research-bot`, `name: Research`)
**Skill:** `temporal-research` (`skills/research/temporal-research/SKILL.md`)
**Motor Compartilhado:** `hermes.platform.research.temporal_engine`
**Conformidade:** ADR-028 (Ciclo de Vida de Memória Temporal) & Contrato de Pesquisa Temporal HAOS

---

## 1. Visão Geral e Arquitetura

O **Bot Research** é um especialista de domínio autônomo registrado no **subsistema Civilization**, focado em pesquisa e verificação temporal com janelas dinâmicas rolling em UTC (ex: `last30days`, `last7days`, `24h`, `quarter`).

O fluxo oficial de pesquisa opera sem duplicação de lógica:
$$\text{Bot Research} \longrightarrow \text{Skill temporal-research} \longrightarrow \text{temporal\_engine (Core Compartilhado)}$$

### Componentes:
1. **Civilization Bot (`hermes.platform.bots.builtin.research_bot`)**:
   - `BotSpec` com ID `research-bot`, nome `Research`, capabilities `['temporal-research', 'web-search']`.
   - Rotinas periódicas: `monthly-sweep` (`last30days`) e `weekly-sweep` (`last7days`).
   - Roteamento determinístico em `hermes.platform.civilization.delegation.py` através de `_DOMAINS["research-bot"]` cobrindo termos temporais (`last30days`, `temporal`, `temporal-research`, `recent-news`, `past-month`, etc.).
2. **Skill Invocável (`skills/research/temporal-research/SKILL.md`)**:
   - Interface declarativa para o agente orquestrador ou uso manual.
   - Padrão oficial de frontmatter, descrição rigorosa ≤60 caracteres, validada por `scripts/audit_skills.py`.
3. **Engine Central Reutilizável (`hermes.platform.research.temporal_engine`)**:
   - Cálculo determinístico de janelas temporais UTC (`compute_temporal_window`).
   - Execução estruturada com metadados de proveniência (`execute_temporal_research`).
   - Mapeamento de cobertura e degradação suave por canal (`CoverageItem`).

---

## 2. Invariantes de Segurança e Isolamento

- **Zero Auto-escrita em Memória Canônica:** O motor e o bot nunca gravam diretamente em `CanonicalMemoryStore`, `GBrain`, `GraphRAG` ou Obsidian Vault. Conteúdo recuperado da web/redes sociais é classificado como `UNTRUSTED_CONTENT`.
- **Honestidade Temporal Estrita:** É expressamente proibido alucinar ou extrapolar datas. Fatos sem âncora de data estrita recebem status `freshness="unverified"`.
- **Degradação Graciosa:** Canais sem credenciais configuradas (ex: X/Twitter, YouTube) reportam status explícito `skipped_no_credentials` ou `inaccessible` sem quebrar o fluxo.
- **Fail-Closed em Limites:** Respeita o orçamento de tokens, tempo e custo definidos na `BotPolicy`.

---

## 3. Verificação e Testes

- `tests/platform/civilization/test_research_bot.py`: Validação de ciclo de vida do bot, registro no `EventStore` e `IdentityManager`, e roteamento de tarefas via `_DOMAINS`.
- `tests/platform/research/test_temporal_engine.py`: Testes unitários do cálculo de janelas UTC e contratos do motor compartilhado.
- `scripts/audit_skills.py`: Auditoria mecânica da skill `temporal-research` (0 violações).
