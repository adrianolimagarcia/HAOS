# ADR-020: IDF Ordena, Gate Autoriza — Papéis Distintos na Cascata de Memória e Auto-Indexação (Document-Keys)

## Status
Aceito (0.21.92, 2026-10-02)

## Contexto
Na evolução da arquitetura de recuperação de memória do HAOS (OKF + RAGFlow + Reconciled Memory), observamos que misturar os conceitos de *ordenação/relevância estatística* e *autorização/interceptação determinística* gerou confusões conceituais e empíricas:
1. **Tentativa de usar IDF no Gate (0.21.82 - Refutado)**: tentou-se usar threshold de Inverse Document Frequency (IDF) para decidir se uma tag do OKF poderia ou não autorizar a resposta antes da busca híbrida. A medição no corpus real (418 documentos) mostrou que termos legítimos e termos de interceptações fracas compartilhavam a mesma distribuição de frequência documental — não há piso escalar que separe intenção no portão de interceptação sem causar overfitting.
2. **Tentativa de usar Max(Score) para desempate léxico (SELF_INDEX_PLAN §0.3 - Refutado)**: em passadas léxicas, pontuar chunks pelo `max(tf)` do termo favoreceu falsos-positivos onde uma palavra comum repetida 10x vencia documentos com múltiplos termos raros distintos. O somatório de `idf * sqrt(tf)` com desempate determinístico (doc_path) é matematicamente robusto.
3. **Contaminação por Chaves Sintéticas sem Portões de Escrita (arXiv:2609.19656 / dream.py)**: como evidenciado na ablação do Self-Index (Tabela 5 do artigo: 26.6 → 16.1 sem portões), gerar e persistir chaves sintéticas ou destilar lições sem portões prévios degrada o índice. Nós vivemos isso empiricamente com 28 falsos-positivos originados de lições salvas sem portão de separação/fidelidade no `dream.py`.

## Decisão Arquitetural

Definimos com clareza a separação formal entre os dois papéis:

```
┌────────────────────────────────────────────────────────────────────────┐
│                        CASCATA DE RECUPERAÇÃO                          │
├────────────────────────────────────────────────────────────────────────┤
│ 1. GATES AUTORIZAM (Binário: Match Autorizado vs Delegar Próxima Rota) │
│    - OKF Canonical Gate: match exato de path/slug, título ou tags com  │
│      cobertura >= 50% dos tokens de conteúdo da query.                 │
│    - RAGFlow Specificity Gate: o documento alvo deve ser recuperado    │
│      no top-k pela própria chave antes que ela seja persistida.       │
│    - RAGFlow Separation Gate: chaves de docs distintos devem ter      │
│      Jaccard < 0.80 para evitar colisões entre tópicos.                │
│    - Dream Write Gate: propostas duplicadas/quase-duplicadas (Jaccard  │
│      >= 0.80) sofrem merge no documento canônico existente.           │
├────────────────────────────────────────────────────────────────────────┤
│ 2. IDF E RRF ORDENAM (Estatístico / Contínuo dentro da busca híbrida) │
│    - Lexical Pass: TF-IDF com penalidade BM25 e exact-phrase boost.    │
│    - Fusion: Reciprocal Rank Fusion (RRF) combinando lexical, FTS5     │
│      e Document-Keys (haos_rag_keys_fts).                              │
│    - Budget: RetrievalBudget limita estritamente o número de nós       │
│      avaliados por query.                                              │
└────────────────────────────────────────────────────────────────────────┘
```

### Invariantes
1. **Portão nunca usa IDF escalar para corte subjetivo**: o portão opera sobre propriedades invariantes estruturais (cobertura frasal, jaccard de conjunto de termos, recuperação determinística no harness).
2. **IDF nunca autoriza isoladamente**: o IDF pondera termos raros durante a ordenação dos rankings de fusão (RRF).
3. **Escrita blindada (Write-Time Gates)**: nenhuma chave ou lição entra no índice canônico (`haos_rag_keys` ou `okf/`) sem passar pelos portões de especificidade, separação e fidelidade.
