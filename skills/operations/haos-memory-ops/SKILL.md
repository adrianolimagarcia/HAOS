---
name: haos-memory-ops
description: Diagnóstico de integridade do Memory Fabric.
version: 1.0.0
author: Adriano Lima, Hermes Agent
license: MIT
platforms: [linux, macos]
category: operations
tags: [haos, memory, obsidian, okf, journal, operations, diagnostics]
---

# HAOS Memory Fabric Operations Skill

Diagnóstico governado, auditoria de consistência e runbook operacional para o Memory Fabric (Journal de Memória, Obsidian Vault e OKF) no HAOS.

## When to Use

Use para verificar a integridade da camada de memória persistente do HAOS, auditar o Journal de Memória, validar a consistência e integridade das notas do Obsidian Vault e base OKF, investigar divergências de índices vetoriais/FTS e diagnosticar problemas de recall sem causar perda de dados.

## Prerequisites

- Diretório do vault e base de memória acessíveis (`obsidian_vault/`, `~/.haos/council/REGISTRY.md`, bases OKF).
- Scripts de auditoria canônicos do HAOS disponíveis (ex.: `registry_guard.py`, rotinas de verificação FTS/SQLite).
- Permissão de leitura no repositório de memória e logs operacionais.

## How to Run

Execute diagnósticos exclusivamente em modo read-only, conferindo hashes, symlinks e integridade estrutural:

```bash
python -m hermes_cli.main memory status
grep -c "verificado_em" ~/.haos/council/REGISTRY.md
```

## Quick Reference

| Componente | Caminho / Alvo | Verificação |
|---|---|---|
| Journal / REGISTRY | `~/.haos/council/REGISTRY.md` | Checagem de symlink e cabeçalhos |
| Obsidian Vault | `obsidian_vault/curadoria/` | Validação de notas espelho (modo 444) |
| Base OKF / Lições | `~/.haos/skills/` & docs OKF | Checagem de formato e índices |
| Guardião do Registro | `~/.haos/scripts/registry_guard.py` | Execução em modo verificação |

## Procedure

1. **Diagnóstico Read-Only**:
   - Inspecione se o symlink `~/.haos/council/REGISTRY.md` aponta corretamente para `~/.hermes/council/REGISTRY.md`.
   - Valide se não há blocos órfãos ou cabeçalhos colados no REGISTRY usando `grep -c -- "-03 ·.*- 2026-"`.
   - Verifique se as permissões das notas geradas de curadoria no Obsidian estão protegidas (ex.: modo somente-leitura 444 no espelho).
   - Audite os índices FTS do grafo de memória para assegurar ausência de termos truncados ou registros corrompidos.
   - NUNCA grave diretamente no arquivo REGISTRY com redirecionamentos de shell (`cat >>` ou `open(p, "w")`).

2. **Pré-condições**:
   - Confirmar que nenhuma rotina automática de consolidação (como `registry_rotate.py` ou `dream_distill.py`) está em execução simultânea.
   - Isolar a sessão de diagnóstico garantindo que `HAOS_HOME` e `HERMES_HOME` coincidam.

3. **Snapshot**:
   - Criar cópia de segurança com timestamp de todos os arquivos de memória antes de qualquer intervenção:
     `cp ~/.haos/council/REGISTRY.md /tmp/registry_snapshot_$(date +%s).md`
   - Registrar o hash SHA-256 do arquivo original para fins de auditoria e validação comparativa.

4. **Autorização**:
   - Qualquer mutação, reparo de cronologia (`registry_fix_chronology.py`) ou descarte de memórias corrompidas EXIGE autorização formal e explícita do operador.
   - Apresente o diff proposto, os identificadores de lições afetadas e a garantia de não-regressão.

5. **Rollback**:
   - Em caso de inconsistência decorrente de reparos, restaurar o arquivo original usando o snapshot seguro:
     `cp /tmp/registry_snapshot_<timestamp>.md ~/.hermes/council/REGISTRY.md`
   - Revalidar o hash SHA-256 para assegurar integridade idêntica ao estado anterior.

## Pitfalls

- **Escrever no espelho do Obsidian Vault**: `obsidian_vault/curadoria/registry-espelho.md` é uma projeção somente-leitura gerada unidirecionalmente. Escrever nele causa divergência silenciosa.
- **Append manual sem lock (flock)**: a concorrência entre agentes destrói a cronologia do REGISTRY; use sempre o script canônico `registry_append.py`.
- **Tratar `REGISTRY.md` como histórico infinito**: entradas com mais de 24 horas devem ser rotacionadas para `council/log/` para não saturar a janela de contexto das sessões.

## Verification

- Confirmar que a verificação de sanidade do REGISTRY passa com código de saída 0.
- Assegurar que os testes de recall ou busca FTS retornam os registros esperados sem exceções de I/O.
- Validar que as permissões de leitura e escrita do vault permanecem estritamente intactas.
