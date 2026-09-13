# HAOS LSP Fabric Specification (Language Server Protocol Intelligence)

- **Status:** Accepted (Canonical SOTA Specification)
- **Layer:** LSP Unified Intelligence (K5 / Etapa 5)
- **Module Path:** `hermes/platform/capabilities/lsp/`

---

## 1. Visão Geral e Filosofia

A maioria das plataformas de agentes de IA comete o erro fatal de tratar código como texto bruto semântico. Quando um agente edita uma função ou renomeia um método:
1. **Refatoração às Cegas:** Ele não enxerga quem chama aquele método em outros módulos.
2. **Blast Radius Alucinado:** O agente não sabe quais suítes de teste realmente precisam ser executadas para validar a alteração.
3. **Desperdício de CI:** Ou roda a suíte inteira do projeto (custo proibitivo em codebases com milhares de testes), ou não roda nada e introduz quebras em produção.

O **HAOS LSP Fabric** resolve isso transformando o Language Server Protocol em uma **malha de inteligência semiótica e estrutural**:
- **Grafo Semântico de Símbolos (`CodeSymbolGraph`):** Rastreia definições, referências e a árvore de chamadas transitivas entre funções e classes.
- **Analisador de Blast Radius (`ImpactAnalyzer`):** Calcula com precisão matemática quais arquivos, chamadores e testes foram impactados por um git diff.
- **Portão de AutoMerge Baseado em Impacto (`AutoMergeGate`):** Executa cirurgicamente apenas os testes afetados antes de aprovar uma branch efêmera na `MergeQueue`.

---

## 2. Arquitetura em Camadas do LSP Fabric

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           INTELLIGENCE LAYER                                │
│          `CodeSymbolGraph` ◄──────────────► `ImpactAnalyzer`                 │
│    (Definições, Referências,               (Cálculo de Blast Radius          │
│     Call Graph Transitivo)                  e Descoberta de Testes)         │
└─────────────────────────────────────┬───────────────────────────────────────┘
                                      │
┌─────────────────────────────────────▼───────────────────────────────────────┐
│                             MANAGER LAYER                                   │
│                        `LSPManager` (Workspace)                             │
│       ┌─────────────────────────────┴─────────────────────────────┐         │
│       ▼                                                           ▼         │
│ `LSPClient` (Real Process)                             `StaticLSPClient`    │
│ (Pyright, Gopls, Rust-Analyzer, etc.)                  (AST Stub Explícito) │
└─────────────────────────────────────┬───────────────────────────────────────┘
                                      │
┌─────────────────────────────────────▼───────────────────────────────────────┐
│                           WIRE / PROTOCOL LAYER                             │
│              `LSPWireFramer` (Content-Length framed JSON-RPC)               │
│                        Estritamente Stdlib-Only                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 3. Grafo de Símbolos (`CodeSymbolGraph`)

Definido em `hermes/platform/capabilities/lsp/unified_intelligence.py`:

```python
@dataclass
class SymbolLocation:
    file_path: str
    line: int
    character: int

@dataclass
class SymbolNode:
    id: str                          # Ex: "hermes/platform/execution/scheduler.py::HAOSScheduler"
    name: str                        # "HAOSScheduler"
    kind: SymbolKind                 # FUNCTION, CLASS, METHOD, VARIABLE, INTERFACE
    file_path: str
    location: SymbolLocation
    qualified_name: str = ""
    signature: str = ""
```

O `CodeSymbolGraph` mantém indexações cruzadas em memória:
- `file_symbols[file_path]`: Todos os símbolos pertencentes a um arquivo.
- `symbol_references[symbol_id]`: Conjunto de todas as ocorrências de uso do símbolo.
- `call_callees[caller_id]`: Funções invocadas pelo símbolo.
- `call_callers[callee_id]`: Funções que invocam o símbolo.

### Resolução de Chamadas Transitivas
O método `get_transitive_callers(symbol_id, max_depth=3)` executa uma busca em largura (BFS) determinística, identificando a cadeia completa de impacto:
$$f_1 \leftarrow f_2 \leftarrow f_3$$
Se $f_1$ for modificado, o grafo alerta que $f_2$ e $f_3$ foram atingidos.

---

## 4. Cálculo Cirúrgico de Blast Radius (`ImpactAnalyzer`)

A partir de um diff ou lista de símbolos modificados, o `ImpactAnalyzer` computa o `BlastRadius`:

```python
@dataclass
class BlastRadius:
    modified_files: Set[str]         # Arquivos diretamente alterados no commit/diff
    affected_files: Set[str]         # Arquivos chamadores indiretos
    affected_callers: Set[str]       # Nomes qualificados das funções/métodos afetados
    affected_test_suites: Set[str]   # Testes que precisam ser executados
    severity: str = "low"            # "low" | "medium" | "high" | "critical"
    transitive_call_depth: int = 0
```

### Classificação de Severidade
- **`low`**: $\le 2$ arquivos afetados; nenhum contrato público quebrado.
- **`medium`**: $3$ a $9$ arquivos afetados ou alteração em métodos centrais com profundidade de chamada transitiva $\ge 2$.
- **`high`**: $\ge 10$ arquivos afetados ou mutação em schemas/protocolos centrais da plataforma.

### Mapeamento Automático de Testes (`_infer_test_suite_for_file`)
Para cada arquivo impactado (direto ou indireto), o analisador infere os caminhos de teste canônicos:
- `hermes/platform/execution/lane_executor.py` $\longrightarrow$ `tests/platform/execution/test_lane_agentic.py`
- `hermes/platform/capabilities/lsp/manager.py` $\longrightarrow$ `tests/platform/capabilities/test_lsp.py`

---

## 5. Integração com o `AutoMergeGate` e `MergeQueue`

No ciclo de vida do Lane Kilo, o `AutoMergeGate` substitui a execução cega de testes por verificação orientada a impacto:

```
                  Git Diff no Worktree Efêmero
                                │
                                ▼
               [ parse_diff_impact(diff_text) ]
                                │
                                ▼
            [ ImpactAnalyzer.analyze_impact(mod_files) ]
                                │
                                ▼
                     Retorna `BlastRadius`:
               • modified_files: 2
               • affected_files: 4
               • affected_test_suites: ['tests/platform/execution/test_lane_agentic.py']
                                │
                                ▼
           [ Executa SOMENTE as Suítes Afetadas! ]
                                │
                 ┌──────────────┴──────────────┐
                 ▼                             ▼
             Sucesso                         Falha
                 │                             │
                 ▼                             ▼
         [ Enfileira na ]              [ Rejeita Patch ]
         [ `MergeQueue` ]              [ Reviewer emite ]
         (Rebase Serial)               [ feedback ]
```

### Ganhos de Performance e Segurança
1. **Velocidade:** Execução de testes de 1 a 3 segundos em vez de 30+ minutos da suíte completa de dezenas de milhares de testes.
2. **Zero Regressão:** Se uma função indireta for quebrada a 3 níveis de profundidade na árvore de chamadas, o LSP força a execução daquele teste correspondente.
3. **Isolamento de Branch:** O merge só avança para a branch principal se todos os testes no blast radius passarem com 100% de sucesso.
