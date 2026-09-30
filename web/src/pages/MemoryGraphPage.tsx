import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Brain,
  Database,
  FileText,
  Layers,
  Network,
  RefreshCw,
  Search,
  Share2,
  Workflow,
  X,
  Code2,
  AlertCircle,
} from "lucide-react";
import { fetchJSON } from "@/lib/api";
import {
  ObsidianGraphView,
  type GraphNode,
  type GraphEdge,
} from "@/components/ObsidianGraphView";
import { colorOf } from "@/components/CivConstellation";
import "./MemoryGraphPage.css";

interface MemoryOverview {
  active_provider: string;
  fabric: {
    records: number;
    projection_acks: number;
    outbox_pending: number;
    healthy: boolean;
  };
  graphrag: {
    entities: number;
    relations: number;
    communities: number;
    applied_events: number;
    healthy: boolean;
  };
  obsidian: {
    total_notes: number;
    folders: Record<string, number>;
    healthy: boolean;
  };
  decisions: {
    total: number;
  };
  graphify: {
    nodes: number;
    edges: number;
    healthy: boolean;
  };
  curator: Record<string, number>;
  providers: Array<{ name: string; status: string; description?: string }>;
  scopes: string[];
}

interface GraphData {
  schema_version: number;
  view: string;
  nodes: GraphNode[];
  edges: GraphEdge[];
  group_counts?: Record<string, number>;
  counts: {
    nodes: number;
    edges: number;
  };
}

interface NodeDetail {
  id: string;
  type: string;
  content?: string;
  path?: string;
  entity?: string;
  entity_type?: string;
  description?: string;
  relations?: Array<{ target: string; relation: string; desc: string }>;
  record_id?: string;
  scope?: string;
  kind?: string;
  status?: string;
  confidence?: number;
  confidence_tier?: string;
  parent_id?: string | null;
  parent_content?: string | null;
  created_at?: string;
  metadata?: Record<string, any>;
  name?: string;
  file?: string;
  line?: number;
  snippet?: string;
  error?: string;
}

type ViewMode = "unified" | "graphrag" | "obsidian" | "db" | "graphify";

export default function MemoryGraphPage() {
  const [overview, setOverview] = useState<MemoryOverview | null>(null);
  const [graphData, setGraphData] = useState<GraphData | null>(null);
  const [viewMode, setViewMode] = useState<ViewMode>("unified");
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [nodeDetail, setNodeDetail] = useState<NodeDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [searchTerm, setSearchTerm] = useState("");
  const [confidenceFilter, setConfidenceFilter] = useState<"all" | "high" | "verified" | "exploratory">("all");
  const [error, setError] = useState<string | null>(null);

  const fetchOverview = useCallback(async () => {
    try {
      const data = await fetchJSON<MemoryOverview>("/api/memory/graph/overview");
      setOverview(data);
    } catch (err) {
      console.warn("Failed to load memory overview:", err);
    }
  }, []);

  const fetchGraph = useCallback(async (view: ViewMode) => {
    try {
      setError(null);
      const data = await fetchJSON<GraphData>(`/api/memory/graph?view=${view}&limit=500`);
      setGraphData(data);
    } catch (err: any) {
      setError(err?.message || "Failed to load memory graph");
    }
  }, []);

  const fetchNodeDetail = useCallback(async (id: string) => {
    try {
      setDetailLoading(true);
      const data = await fetchJSON<NodeDetail>(`/api/memory/graph/node/${encodeURIComponent(id)}`);
      setNodeDetail(data);
    } catch (err: any) {
      setNodeDetail({ id, type: "error", error: err?.message });
    } finally {
      setDetailLoading(false);
    }
  }, []);

  const refreshAll = async () => {
    setRefreshing(true);
    await Promise.all([fetchOverview(), fetchGraph(viewMode)]);
    if (selectedId) {
      fetchNodeDetail(selectedId);
    }
    setRefreshing(false);
  };

  useEffect(() => {
    setLoading(true);
    Promise.all([fetchOverview(), fetchGraph(viewMode)]).finally(() => {
      setLoading(false);
    });
  }, [fetchOverview, fetchGraph, viewMode]);

  useEffect(() => {
    if (selectedId) {
      fetchNodeDetail(selectedId);
    } else {
      setNodeDetail(null);
    }
  }, [selectedId, fetchNodeDetail]);

  const filteredNodes = useMemo(() => {
    if (!graphData?.nodes) return [];
    const term = searchTerm.trim().toLowerCase();
    return graphData.nodes.filter(n => {
      if (term) {
        const matches =
          n.label.toLowerCase().includes(term) ||
          n.id.toLowerCase().includes(term) ||
          (n.kind && n.kind.toLowerCase().includes(term));
        if (!matches) return false;
      }
      if (confidenceFilter !== "all") {
        const tier = n.meta?.confidence_tier;
        if (tier && tier !== confidenceFilter) return false;
      }
      return true;
    });
  }, [graphData?.nodes, searchTerm, confidenceFilter]);

  const selectedNode = useMemo(() => {
    if (!selectedId || !graphData?.nodes) return null;
    return graphData.nodes.find(n => n.id === selectedId) || null;
  }, [selectedId, graphData?.nodes]);

  const activeEdges = useMemo(() => {
    if (!graphData?.edges) return [];
    if (!searchTerm.trim() && confidenceFilter === "all") return graphData.edges;
    const nodeIds = new Set(filteredNodes.map(n => n.id));
    return graphData.edges.filter(
      e => nodeIds.has(e.source) && nodeIds.has(e.target)
    );
  }, [graphData?.edges, filteredNodes, searchTerm, confidenceFilter]);

  return (
    <div className="mem-page">
      <div className="mem-orb mem-orb-one" />
      <div className="mem-orb mem-orb-two" />

      <div className="mem-shell">
        {/* Header */}
        <header className="mem-heading">
          <div>
            <div className="mem-eyebrow">
              <span className="mem-pulse" />
              <span>FEDERATED MEMORY ARCHITECTURE</span>
              <span className="mem-eyebrow-line" />
            </div>
            <h1>
              Graph <span>Memory</span>
            </h1>
            <p>
              Topologia multi-camadas do ecossistema de memória HAOS. Visualize
              o Memory Fabric central, Canonical SQLite, Projeções Obsidian,
              GraphRAG, Base de Decisões e o Knowledge Graph de código Graphify.
            </p>
          </div>

          <div className="mem-actions">
            <button
              type="button"
              className="mem-refresh-btn"
              onClick={refreshAll}
              disabled={refreshing}
            >
              <RefreshCw
                className={`mem-icon ${refreshing ? "mem-spin" : ""}`}
                size={16}
              />
              {refreshing ? "Sincronizando..." : "Atualizar Grafo"}
            </button>
          </div>
        </header>

        {error && (
          <div className="mem-notice mem-error">
            <AlertCircle size={16} />
            <span>Falha ao carregar grafo de memória: {error}</span>
          </div>
        )}

        {/* Multi-Tier Metrics Cards */}
        {overview && (
          <section className="mem-metrics">
            <div className="mem-metric">
              <div className="mem-metric-label">
                <span>CANONICAL SQLITE (FABRIC)</span>
                <Database size={15} />
              </div>
              <div className="mem-metric-value">
                <strong>{overview.fabric.records}</strong>
                <span className="mem-badge-status mem-status-active">Ativo</span>
              </div>
              <small>
                {overview.fabric.projection_acks} acks · {overview.fabric.outbox_pending} outbox
              </small>
            </div>

            <div className="mem-metric">
              <div className="mem-metric-label">
                <span>GRAPHRAG STORE</span>
                <Share2 size={15} />
              </div>
              <div className="mem-metric-value">
                <strong>{overview.graphrag.entities}</strong>
                <small className="mem-metric-sub">entidades</small>
              </div>
              <small>
                {overview.graphrag.relations} relações · {overview.graphrag.communities} clusters
              </small>
            </div>

            <div className="mem-metric">
              <div className="mem-metric-label">
                <span>OBSIDIAN VAULT</span>
                <FileText size={15} />
              </div>
              <div className="mem-metric-value">
                <strong>{overview.obsidian.total_notes}</strong>
                <small className="mem-metric-sub">notas MD</small>
              </div>
              <small>
                {Object.keys(overview.obsidian.folders || {}).length} pastas estruturadas
              </small>
            </div>

            <div className="mem-metric">
              <div className="mem-metric-label">
                <span>GRAPHIFY (CODE KG)</span>
                <Code2 size={15} />
              </div>
              <div className="mem-metric-value">
                <strong>{overview.graphify.nodes}</strong>
                <small className="mem-metric-sub">símbolos</small>
              </div>
              <small>{overview.graphify.edges} chamadas AST</small>
            </div>

            <div className="mem-metric">
              <div className="mem-metric-label">
                <span>ACTIVE PROVIDER</span>
                <Brain size={15} />
              </div>
              <div className="mem-metric-value">
                <span className="mem-active-provider">
                  {overview.active_provider}
                </span>
              </div>
              <small>{overview.providers?.length || 0} provedores registrados</small>
            </div>
          </section>
        )}

        {/* View Switcher & Search Bar */}
        <div className="mem-nav-bar mem-glass">
          <div className="mem-tabs">
            <button
              type="button"
              className={`mem-tab ${viewMode === "unified" ? "is-active" : ""}`}
              onClick={() => {
                setViewMode("unified");
                setSelectedId(null);
              }}
            >
              <Workflow size={15} />
              <span>Topologia Unificada</span>
            </button>
            <button
              type="button"
              className={`mem-tab ${viewMode === "graphrag" ? "is-active" : ""}`}
              onClick={() => {
                setViewMode("graphrag");
                setSelectedId(null);
              }}
            >
              <Share2 size={15} />
              <span>GraphRAG Subgrafo</span>
            </button>
            <button
              type="button"
              className={`mem-tab ${viewMode === "obsidian" ? "is-active" : ""}`}
              onClick={() => {
                setViewMode("obsidian");
                setSelectedId(null);
              }}
            >
              <FileText size={15} />
              <span>Obsidian Vault</span>
            </button>
            <button
              type="button"
              className={`mem-tab ${viewMode === "db" ? "is-active" : ""}`}
              onClick={() => {
                setViewMode("db");
                setSelectedId(null);
              }}
            >
              <Database size={15} />
              <span>Canonical Fabric DB</span>
            </button>
            <button
              type="button"
              className={`mem-tab ${viewMode === "graphify" ? "is-active" : ""}`}
              onClick={() => {
                setViewMode("graphify");
                setSelectedId(null);
              }}
            >
              <Code2 size={15} />
              <span>Graphify Code KG</span>
            </button>
          </div>

          <div className="mem-filter-group" style={{ display: "flex", alignItems: "center", gap: "10px" }}>
            <div className="mem-conf-filter" style={{ display: "flex", alignItems: "center", gap: "4px", background: "rgba(0, 0, 0, 0.25)", padding: "3px 6px", borderRadius: "10px", border: "1px solid var(--line)" }}>
              <span style={{ fontSize: "0.72rem", color: "var(--muted)", paddingLeft: "4px" }}>Confiança:</span>
              <button
                type="button"
                className={`mem-pill-btn ${confidenceFilter === "all" ? "is-active" : ""}`}
                onClick={() => setConfidenceFilter("all")}
                title="Mostrar todos os nós"
              >
                Todas
              </button>
              <button
                type="button"
                className={`mem-pill-btn ${confidenceFilter === "high" ? "is-active" : ""}`}
                onClick={() => setConfidenceFilter("high")}
                title="≥ 85% — Auto-aprovado de alta confiança"
                style={{ color: confidenceFilter === "high" ? "#34d399" : undefined }}
              >
                Alta (≥85%)
              </button>
              <button
                type="button"
                className={`mem-pill-btn ${confidenceFilter === "verified" ? "is-active" : ""}`}
                onClick={() => setConfidenceFilter("verified")}
                title="≥ 70% — Auto-aprovado verificado"
                style={{ color: confidenceFilter === "verified" ? "#38bdf8" : undefined }}
              >
                Verificada (≥70%)
              </button>
              <button
                type="button"
                className={`mem-pill-btn ${confidenceFilter === "exploratory" ? "is-active" : ""}`}
                onClick={() => setConfidenceFilter("exploratory")}
                title="< 70% — Fato exploratório / em consolidação"
                style={{ color: confidenceFilter === "exploratory" ? "#fbbf24" : undefined }}
              >
                Exploratória (&lt;70%)
              </button>
            </div>

            <div className="mem-search-box">
              <Search size={14} className="mem-search-icon" />
              <input
                type="text"
                placeholder="Buscar nós por nome, ID ou tipo..."
                value={searchTerm}
                onChange={e => setSearchTerm(e.target.value)}
                className="mem-search-input"
              />
              {searchTerm && (
                <button
                  type="button"
                  className="mem-search-clear"
                  onClick={() => setSearchTerm("")}
                >
                  <X size={13} />
                </button>
              )}
            </div>
          </div>
        </div>

        {/* Main Stage: 3D Constellation + Detail Inspector Drawer */}
        <div className="mem-workspace">
          <div className="mem-canvas-container mem-glass">
            <div className="mem-canvas-header">
              <div className="mem-canvas-title">
                <Network size={16} />
                <span>
                  Grafo de Conhecimento Interligado (Estilo Obsidian) —{" "}
                  {viewMode === "unified"
                    ? "Ecossistema Completo"
                    : viewMode === "graphrag"
                    ? "GraphRAG Knowledge Graph"
                    : viewMode === "obsidian"
                    ? "Obsidian Markdown Notes"
                    : viewMode === "db"
                    ? "Fatos Canônicos (SQLite)"
                    : "Código AST (Graphify)"}
                </span>
              </div>
              <div className="mem-canvas-counts">
                <span>{filteredNodes.length} nós</span>
                <span>·</span>
                <span>{activeEdges.length} links</span>
              </div>
            </div>

            {loading ? (
              <div className="mem-loading-stage">
                <RefreshCw className="mem-spin" size={32} />
                <span>Carregando topologia de memória...</span>
              </div>
            ) : filteredNodes.length === 0 ? (
              <div className="mem-empty-stage">
                <AlertCircle size={28} />
                <span>Nenhum nó encontrado para esta visualização ou busca.</span>
              </div>
            ) : (
              <ObsidianGraphView
                nodes={filteredNodes}
                edges={activeEdges}
                selectedId={selectedId}
                onSelect={setSelectedId}
                className="mem-constellation"
              />
            )}
          </div>

          {/* Inspector Panel */}
          <aside className="mem-inspector mem-glass">
            <div className="mem-inspector-head">
              <h2>Inspetor de Memória</h2>
              {selectedId && (
                <button
                  type="button"
                  className="mem-close-btn"
                  onClick={() => setSelectedId(null)}
                  aria-label="Fechar"
                >
                  <X size={15} />
                </button>
              )}
            </div>

            {selectedNode ? (
              <div className="mem-inspector-body">
                <div className="mem-node-header">
                  <span
                    className="mem-kind-tag"
                    style={{
                      background: `rgba(${colorOf(selectedNode.kind).join(",")}, 0.18)`,
                      color: `rgb(${colorOf(selectedNode.kind).join(",")})`,
                      borderColor: `rgba(${colorOf(selectedNode.kind).join(",")}, 0.35)`,
                    }}
                  >
                    {selectedNode.kind}
                  </span>
                  <h3>{selectedNode.label}</h3>
                  <code className="mem-node-id">{selectedNode.id}</code>
                </div>

                {detailLoading ? (
                  <div className="mem-detail-loading">
                    <RefreshCw className="mem-spin" size={20} />
                    <span>Lendo dados da memória...</span>
                  </div>
                ) : nodeDetail ? (
                  <div className="mem-node-content">
                    {/* Obsidian Note Preview */}
                    {nodeDetail.type === "obsidian_note" && (
                      <div className="mem-note-view">
                        <div className="mem-view-banner">
                          <FileText size={14} />
                          <span>Arquivo Markdown no Vault</span>
                        </div>
                        <pre className="mem-code-block">{nodeDetail.content}</pre>
                      </div>
                    )}

                    {/* GraphRAG Entity */}
                    {nodeDetail.type === "graphrag_entity" && (
                      <div className="mem-entity-view">
                        <div className="mem-field">
                          <label>Tipo de Entidade</label>
                          <div>{nodeDetail.entity_type || "Geral"}</div>
                        </div>
                        {nodeDetail.description && (
                          <div className="mem-field">
                            <label>Descrição</label>
                            <p>{nodeDetail.description}</p>
                          </div>
                        )}
                        {nodeDetail.relations && nodeDetail.relations.length > 0 && (
                          <div className="mem-field">
                            <label>Relações no Grafo ({nodeDetail.relations.length})</label>
                            <ul className="mem-rel-list">
                              {nodeDetail.relations.map((r, i) => (
                                <li key={i}>
                                  <span className="mem-rel-type">{r.relation}</span>
                                  <span className="mem-rel-target">{r.target}</span>
                                </li>
                              ))}
                            </ul>
                          </div>
                        )}
                      </div>
                    )}

                    {/* Canonical Fact */}
                    {nodeDetail.type === "canonical_fact" && (
                      <div className="mem-fact-view">
                        <div className="mem-field">
                          <label>Escopo & Status</label>
                          <div style={{ display: "flex", gap: "8px", alignItems: "center", flexWrap: "wrap" }}>
                            <span className="mem-badge-scope">{nodeDetail.scope}</span>
                            {nodeDetail.status === "active" ? (
                              <span className="mem-badge-status mem-status-active">
                                ✓ Auto-Aprovado ({nodeDetail.confidence_tier === "high" ? "Alta Confiança" : nodeDetail.confidence_tier === "verified" ? "Verificado" : "Ativo"})
                              </span>
                            ) : (
                              <span className="mem-badge-status" style={{ background: "rgba(239, 68, 68, 0.2)", color: "#f87171", border: "1px solid rgba(239, 68, 68, 0.4)" }}>
                                ⚠️ {nodeDetail.status || "Conflito / Suspenso"}
                              </span>
                            )}
                          </div>
                        </div>
                        <div className="mem-field">
                          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "4px" }}>
                            <label style={{ margin: 0 }}>Confiança Baseada em Evidência</label>
                            <span style={{ fontSize: "0.8rem", fontWeight: 700, color: (nodeDetail.confidence || 1) >= 0.85 ? "#34d399" : (nodeDetail.confidence || 1) >= 0.70 ? "#38bdf8" : "#fbbf24" }}>
                              {((nodeDetail.confidence || 1) * 100).toFixed(0)}%
                            </span>
                          </div>
                          <div className="mem-progress-bar">
                            <div
                              className="mem-progress-fill"
                              style={{
                                width: `${(nodeDetail.confidence || 1) * 100}%`,
                                background: (nodeDetail.confidence || 1) >= 0.85
                                  ? "linear-gradient(90deg, #10b981, #34d399)"
                                  : (nodeDetail.confidence || 1) >= 0.70
                                  ? "linear-gradient(90deg, #0284c7, #38bdf8)"
                                  : "linear-gradient(90deg, #d97706, #fbbf24)"
                              }}
                            />
                          </div>
                          <small style={{ color: "var(--muted)", fontSize: "0.72rem", marginTop: "4px", display: "block" }}>
                            {(nodeDetail.confidence || 1) >= 0.70
                              ? "✓ Aprovado e consolidado diretamente pelo motor (zero quarentena manual desnecessária)."
                              : "⏳ Fato em estágio exploratório — promovido automaticamente após reincidência factual."}
                          </small>
                        </div>
                        <div className="mem-field">
                          <label>Conteúdo do Fato (Child Chunk)</label>
                          <blockquote className="mem-fact-quote">
                            {nodeDetail.content}
                          </blockquote>
                        </div>

                        {/* Parent-Child Chunking Display */}
                        {nodeDetail.parent_id && (
                          <div className="mem-parent-chunk-box" style={{ marginTop: "12px", padding: "10px 12px", background: "rgba(147, 51, 234, 0.08)", border: "1px solid rgba(147, 51, 234, 0.3)", borderRadius: "10px" }}>
                            <div style={{ display: "flex", alignItems: "center", gap: "6px", marginBottom: "6px", color: "#c084fc", fontSize: "0.75rem", fontWeight: 600 }}>
                              <Layers size={14} />
                              <span>Parent Context (Chunking Hierárquico)</span>
                            </div>
                            <div style={{ fontSize: "0.72rem", color: "var(--muted)", marginBottom: "4px" }}>
                              Origem / Pai: <code style={{ color: "#e9d5ff" }}>{nodeDetail.parent_id}</code>
                            </div>
                            {nodeDetail.parent_content && (
                              <pre className="mem-code-block" style={{ maxHeight: "150px", overflowY: "auto", fontSize: "0.72rem" }}>
                                {nodeDetail.parent_content}
                              </pre>
                            )}
                          </div>
                        )}

                        {nodeDetail.created_at && (
                          <div className="mem-field" style={{ marginTop: "10px" }}>
                            <label>Registrado em</label>
                            <small>{nodeDetail.created_at}</small>
                          </div>
                        )}
                      </div>
                    )}

                    {/* Code Symbol Preview */}
                    {nodeDetail.type === "code_symbol" && (
                      <div className="mem-code-view">
                        <div className="mem-field">
                          <label>Símbolo / Tipo</label>
                          <div style={{ display: "flex", gap: "8px", alignItems: "center" }}>
                            <span className="mem-kind-tag">{nodeDetail.kind || "symbol"}</span>
                            <span style={{ fontWeight: 600 }}>{nodeDetail.name}</span>
                          </div>
                        </div>
                        <div className="mem-field">
                          <label>Arquivo</label>
                          <code style={{ fontSize: "0.75rem", color: "#38bdf8" }}>
                            {nodeDetail.file} (linha {nodeDetail.line})
                          </code>
                        </div>
                        {nodeDetail.snippet && (
                          <div className="mem-field">
                            <label>Código Fonte (Preview)</label>
                            <pre className="mem-code-block">{nodeDetail.snippet}</pre>
                          </div>
                        )}
                      </div>
                    )}

                    {/* Generic metadata */}
                    {selectedNode.meta && Object.keys(selectedNode.meta).length > 0 && (
                      <div className="mem-metadata-section">
                        <h4>Metadados</h4>
                        <dl className="mem-meta-dl">
                          {Object.entries(selectedNode.meta).map(([k, v]) => (
                            <div key={k}>
                              <dt>{k}</dt>
                              <dd>{typeof v === "object" ? JSON.stringify(v) : String(v)}</dd>
                            </div>
                          ))}
                        </dl>
                      </div>
                    )}
                  </div>
                ) : (
                  <p className="mem-no-detail">Nenhum detalhe adicional disponível.</p>
                )}
              </div>
            ) : (
              <div className="mem-inspector-empty">
                <Network size={36} />
                <strong>Nenhum nó selecionado</strong>
                <p>
                  Clique em qualquer estrela da constelação 3D para inspecionar
                  seus metadados, texto do vault, relações ou registros de banco de dados.
                </p>
              </div>
            )}
          </aside>
        </div>

        {/* Architecture Topology Explanation Footer */}
        <section className="mem-explainer mem-glass">
          <div className="mem-explainer-head">
            <Workflow size={18} />
            <h2>Como as Memórias do HAOS Estão Interligadas</h2>
          </div>
          <div className="mem-explainer-grid">
            <div className="mem-explainer-card">
              <span className="mem-step-num">01</span>
              <h4>Memory Fabric Spine</h4>
              <p>
                O coordenador central recebe fatos e eventos durante a execução dos agentes.
                Ele consolida registros imutáveis no diário canônico SQLite (<code>fabric.db</code>)
                garantindo versionamento, controle de concorrência e preservação de histórico.
              </p>
            </div>

            <div className="mem-explainer-card">
              <span className="mem-step-num">02</span>
              <h4>Fan-Out Projections</h4>
              <p>
                A partir do diário canônico, o Fabric dispara projeções duráveis:
                gera notas humanas no <strong>Obsidian Vault</strong> com links wiki e ADRs,
                constrói o grafo semântico no <strong>GraphRAG</strong>, e indexa vetores
                no banco de embeddings para busca por similaridade.
              </p>
            </div>

            <div className="mem-explainer-card">
              <span className="mem-step-num">03</span>
              <h4>GraphRAG & Graphify</h4>
              <p>
                O <strong>GraphRAG</strong> extrai entidades, componentes e relações dos fatos e notas,
                agrupando-os em comunidades semânticas. O <strong>Graphify</strong> complementa
                com a árvore AST determinística de símbolos de código, permitindo que a IA
                navegue tanto no conhecimento conceitual quanto na arquitetura de código.
              </p>
            </div>

            <div className="mem-explainer-card">
              <span className="mem-step-num">04</span>
              <h4>Scoped Access & Providers</h4>
              <p>
                Acesso segmentado em 4 escopos: <em>Global</em>, <em>Project</em>, <em>Team</em> e <em>Private</em>.
                Provedores externos plugáveis (Holographic, Mem0, Honcho, Supermemory)
                são orquestrados sob a mesma interface unificada sem acoplamento ao núcleo.
              </p>
            </div>
          </div>
        </section>
      </div>
    </div>
  );
}
