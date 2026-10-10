# HD-06: Procedimento de Retenção e Arquivamento de `vectors.db.corrupt`

**Data:** 2026-10-09  
**Status:** AUDITADO E PENDENTE DE APROVAÇÃO DO OPERADOR (NO-GO PARA EXCLUSÃO AUTOMÁTICA)  
**Arquivo de Produção Alvo:** `/root/.haos/memory/vectors.db.corrupt`  

---

## 1. Contexto Forense e Diagnóstico

Durante os ciclos anteriores de recuperação e compactação de memória vetorial, o subsistema de vetorização isolou um arquivo de índice corrompido nomeando-o como `vectors.db.corrupt`.

### Invariantes Estritas de Produção:
1. **Nenhum arquivo em produção foi ou será removido ou renomeado automaticamente nesta missão.**
2. O arquivo permanece intocado em `/root/.haos/memory/vectors.db.corrupt`.
3. O índice ativo do HAOS opera de forma independente e isolada.

---

## 2. Procedimento Seguro de Retenção e Arquivamento Futuro

Quando o operador humano conceder autorização explícita (`operator_approved=true`), o arquivamento definitivo deverá seguir os seguintes passos determinísticos:

### Passo 1: Geração de Checksum SHA-256 (Cadeia de Custódia)
```bash
sha256sum /root/.haos/memory/vectors.db.corrupt > /root/.haos/memory/vectors.db.corrupt.sha256
```

### Passo 2: Empacotamento Comprimido em Quarentena Cold-Storage
```bash
mkdir -p /root/.haos/memory/quarantine
tar -czf /root/.haos/memory/quarantine/vectors.db.corrupt.$(date +%Y%m%d_%H%M%S).tar.gz \
  -C /root/.haos/memory vectors.db.corrupt vectors.db.corrupt.sha256
```

### Passo 3: Verificação de Integridade do Arquivo de Quarentena
```bash
tar -tzf /root/.haos/memory/quarantine/vectors.db.corrupt.$(date +%Y%m%d_%H%M%S).tar.gz
```

### Passo 4: Remoção Segura do Arquivo Corrompido Ativo (Apenas com Autorização Explícita)
```bash
rm -f /root/.haos/memory/vectors.db.corrupt
```

---

## 3. Matriz de Risco e Plano de Rollback

| Risco | Probabilidade | Impacto | Mitigação |
|---|---|---|---|
| Perda de vetores históricos legados | Nula | Baixo | Os vetores são derivados regeneráveis a partir de `fabric.db` canônico (HD-03). |
| Exclusão acidental sem autorização | Zero | Médio | Bloqueado por contrato operacional — remoção requer intervenção manual do operador. |
