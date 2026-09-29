import { useCallback, useEffect, useMemo, useState } from "react";
import { Activity, ArrowUpRight, BookOpen, Bot, ChevronRight, GitBranch, Network, RefreshCw, RotateCcw, ShieldCheck, Users, X } from "lucide-react";
import { fetchJSON } from "@/lib/api";
import { errorMessage } from "@/lib/api-error";
import { useProfileScope } from "@/contexts/useProfileScope";
import "./CivilizationPage.css";

interface BotNode { bot_id: string; name: string; status: string; version: number | null; bundle_hash: string | null }
interface CouncilNode { council_id: string; purpose: string; members: string[]; version: number; decision_mode?: string }
interface LeafNode { leaf_id: string; parent_bot_id: string; council_id: string | null; council_session_id: string | null; status: string }
interface DecisionNode { decision_id: string; council_id: string; council_session_id: string; created_at: string; decision?: string; confidence?: number }
interface CivEvent { seq: number; name: string; timestamp: string | number; bot_id?: string; council_id?: string; leaf_id?: string; decision_id?: string }
interface ProposalNode { proposal_id: string; bot_id: string; status: string; risk_class: string; rationale?: string }
interface ReputationNode { bot_id: string; score: number; evidence_count: number }
interface Overview {
  bots: BotNode[];
  councils: CouncilNode[];
  leaves: LeafNode[];
  decisions: DecisionNode[];
  proposals: ProposalNode[];
  reputation: ReputationNode[];
  constitution: { version: number; title: string } | null;
  events: CivEvent[];
  cursor: number;
}

interface GraphNode {
  id: string;
  kind: "bot" | "council" | "decision" | "leaf" | "proposal";
  label: string;
  status: string;
  bot_id?: string;
  version?: number | null;
  score?: number;
}

interface GraphEdge {
  id: string;
  source: string;
  target: string;
  kind: string;
  directed: boolean;
  weight?: number;
}

interface CivGraph {
  schema_version: number;
  nodes: GraphNode[];
  edges: GraphEdge[];
  cursor: number;
  counts: Record<string, number>;
}

interface CouncilMemoryData {
  record: {
    council_id: string;
    profile_id: string;
    last_seq: number;
    content_hash: string;
    updated_at: number;
    decisions_count: number;
    members: string[];
    has_operator_override: boolean;
  };
  content: string;
}

type ViewMode = "tree" | "graph" | "memory";
type Selection = { kind: "bot" | "council" | "leaf" | "decision" | "proposal"; id: string };

function eventTime(value: string | number): string {
  const date = new Date(typeof value === "number" && value < 1e11 ? value * 1000 : value);
  return Number.isNaN(date.getTime()) ? "—" : date.toLocaleString();
}

function NodeButton({ selected, label, subtitle, icon, onClick }: { selected: boolean; label: string; subtitle: string; icon: React.ReactNode; onClick: () => void }) {
  return (
    <button type="button" className={`civ-node ${selected ? "is-selected" : ""}`} onClick={onClick} aria-pressed={selected}>
      <span className="civ-node-icon">{icon}</span>
      <span className="civ-node-copy">
        <strong>{label}</strong>
        <small>{subtitle}</small>
      </span>
      <ChevronRight size={15} aria-hidden="true" />
    </button>
  );
}

export default function CivilizationPage() {
  const { profile } = useProfileScope();
  const [data, setData] = useState<Overview | null>(null);
  const [graphData, setGraphData] = useState<CivGraph | null>(null);
  const [viewMode, setViewMode] = useState<ViewMode>("tree");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [selected, setSelected] = useState<Selection | null>(null);
  const [tick, setTick] = useState(0);
  const [updated, setUpdated] = useState<Date | null>(null);

  // Council Memory View State
  const [activeCouncilId, setActiveCouncilId] = useState<string>("");
  const [councilMemory, setCouncilMemory] = useState<CouncilMemoryData | null>(null);
  const [memoryLoading, setMemoryLoading] = useState(false);

  useEffect(() => {
    setData(null);
    setGraphData(null);
    setSelected(null);
    setLoading(true);
    setUpdated(null);
    setError("");
    setCouncilMemory(null);
  }, [profile]);

  useEffect(() => {
    const controller = new AbortController();
    let busy = false;
    async function load() {
      if (busy || document.hidden) return;
      busy = true;
      try {
        const graphPromise = Promise.resolve()
          .then(() => fetchJSON<CivGraph>("/api/civilization/graph", { signal: controller.signal }))
          .catch(() => null);
        const [overviewRes, graphRes] = await Promise.all([
          fetchJSON<Overview>("/api/civilization/overview", { signal: controller.signal }),
          graphPromise,
        ]);
        if (!controller.signal.aborted) {
          setData(overviewRes);
          if (graphRes) setGraphData(graphRes);
          if (overviewRes.councils.length > 0 && !activeCouncilId) {
            setActiveCouncilId(overviewRes.councils[0].council_id);
          }
          setError("");
          setUpdated(new Date());
        }
      } catch (cause) {
        if (!controller.signal.aborted) setError(errorMessage(cause));
      } finally {
        busy = false;
        if (!controller.signal.aborted) setLoading(false);
      }
    }
    void load();
    const interval = window.setInterval(() => void load(), 30000);
    const visibility = () => { if (!document.hidden) void load(); };
    document.addEventListener("visibilitychange", visibility);
    return () => {
      controller.abort();
      clearInterval(interval);
      document.removeEventListener("visibilitychange", visibility);
    };
  }, [tick, profile]);

  // Load Council Memory when council selected in memory view
  useEffect(() => {
    if (viewMode !== "memory" || !activeCouncilId) return;
    let cancelled = false;
    async function fetchMemory() {
      setMemoryLoading(true);
      try {
        const res = await fetchJSON<CouncilMemoryData>(`/api/civilization/councils/${encodeURIComponent(activeCouncilId)}/memory`);
        if (!cancelled) setCouncilMemory(res);
      } catch (e) {
        if (!cancelled) setError(errorMessage(e));
      } finally {
        if (!cancelled) setMemoryLoading(false);
      }
    }
    void fetchMemory();
    return () => { cancelled = true; };
  }, [viewMode, activeCouncilId, tick]);

  const refresh = useCallback(() => {
    setLoading(!data);
    setTick(v => v + 1);
  }, [data]);

  const rebuildMemory = useCallback(async () => {
    if (!activeCouncilId) return;
    setMemoryLoading(true);
    try {
      await fetchJSON(`/api/civilization/councils/${encodeURIComponent(activeCouncilId)}/memory/rebuild`, {
        method: "POST",
      });
      setTick(v => v + 1);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setMemoryLoading(false);
    }
  }, [activeCouncilId]);

  const botIds = useMemo(() => new Set(data?.bots.map(bot => bot.bot_id) ?? []), [data]);
  const councilIds = useMemo(() => new Set(data?.councils.map(c => c.council_id) ?? []), [data]);

  const chosen = selected?.kind === "bot" ? data?.bots.find(x => x.bot_id === selected.id)
    : selected?.kind === "council" ? data?.councils.find(x => x.council_id === selected.id)
      : selected?.kind === "leaf" ? data?.leaves.find(x => x.leaf_id === selected.id)
        : selected?.kind === "proposal" ? data?.proposals.find(x => x.proposal_id === selected.id)
          : data?.decisions.find(x => x.decision_id === selected?.id);

  const counts = {
    bots: data?.bots.length ?? 0,
    councils: data?.councils.length ?? 0,
    leaves: data?.leaves.length ?? 0,
    decisions: data?.decisions.length ?? 0,
  };

  const choose = (kind: Selection["kind"], id: string) => setSelected({ kind, id });

  return (
    <main className="civ-page">
      <div className="civ-orb civ-orb-one" aria-hidden="true" />
      <div className="civ-orb civ-orb-two" aria-hidden="true" />
      <div className="civ-shell">
        <header className="civ-heading">
          <div>
            <div className="civ-eyebrow">
              <span className="civ-pulse" /> HAOS / CIVILIZATION PLATFORM <span className="civ-eyebrow-line" /> ULTRA SOTA
            </div>
            <h1>Civilization <span>Observatory</span></h1>
            <p>Identidades persistentes, coordenação multi-bot, linhagem de decisões e memória projetada.</p>
          </div>
          <div style={{ display: "flex", gap: "10px", alignItems: "center" }}>
            <div style={{ display: "flex", background: "rgba(255,255,255,0.08)", borderRadius: "10px", padding: "3px" }}>
              <button
                type="button"
                className={`civ-refresh ${viewMode === "tree" ? "is-selected" : ""}`}
                style={{ padding: "7px 12px", border: "none" }}
                onClick={() => setViewMode("tree")}
              >
                <GitBranch size={14} /> Árvore
              </button>
              <button
                type="button"
                className={`civ-refresh ${viewMode === "graph" ? "is-selected" : ""}`}
                style={{ padding: "7px 12px", border: "none" }}
                onClick={() => setViewMode("graph")}
              >
                <Network size={14} /> Grafo DAG
              </button>
              <button
                type="button"
                className={`civ-refresh ${viewMode === "memory" ? "is-selected" : ""}`}
                style={{ padding: "7px 12px", border: "none" }}
                onClick={() => setViewMode("memory")}
              >
                <BookOpen size={14} /> MEMORY.md
              </button>
            </div>
            <button className="civ-refresh" onClick={refresh} type="button" disabled={loading} aria-label="Atualizar observatório">
              <RefreshCw size={16} /> Atualizar
            </button>
          </div>
        </header>

        {error ? (
          <div className="civ-notice civ-error" role="alert">
            Não foi possível atualizar: {error}. {data ? "Exibindo último estado carregado." : "Nenhum estado foi carregado."}
          </div>
        ) : null}

        <section className="civ-metrics" aria-label="Resumo da civilização">
          {([
            ["BOTS", counts.bots, Bot],
            ["CONSELHOS", counts.councils, Users],
            ["LEAVES", counts.leaves, GitBranch],
            ["DECISÕES", counts.decisions, ShieldCheck],
          ] as const).map(([label, count, Icon]) => (
            <div className="civ-metric" key={label}>
              <span className="civ-metric-label">{label}<Icon size={16} /></span>
              <strong>{!data ? "—" : count.toString().padStart(2, "0")}</strong>
              <small>entidades ativas</small>
            </div>
          ))}
        </section>

        {viewMode === "tree" && (
          <div className="civ-columns">
            <section className="civ-glass civ-tree" aria-label="Árvore civilizacional">
              <div className="civ-panel-head">
                <div><span className="civ-kicker">01 / ESTRUTURA HIERÁRQUICA</span><h2>Árvore de Linhagem</h2></div>
                <GitBranch size={19} aria-hidden="true" />
              </div>
              {loading && !data ? <p className="civ-empty" role="status">Carregando registros…</p> : null}
              {!loading && data && !counts.bots && !counts.councils && !counts.leaves && !counts.decisions && !data.proposals?.length ? (
                <div className="civ-empty">
                  <Bot size={28} /><strong>Nenhuma identidade registrada</strong>
                  <span>Eventos de Bots e Conselhos aparecerão aqui quando forem persistidos no perfil ativo.</span>
                </div>
              ) : null}
              {data?.bots.map(bot => (
                <div className="civ-branch" key={bot.bot_id}>
                  <NodeButton
                    selected={selected?.kind === "bot" && selected.id === bot.bot_id}
                    label={bot.name || bot.bot_id}
                    subtitle={`BOT · ${bot.version == null ? "sem versão" : `v${bot.version}`} · ${bot.status}`}
                    icon={<Bot size={17} />}
                    onClick={() => choose("bot", bot.bot_id)}
                  />
                  <div className="civ-children">
                    {data.leaves.filter(leaf => leaf.parent_bot_id === bot.bot_id).map(leaf => (
                      <NodeButton
                        key={leaf.leaf_id}
                        selected={selected?.kind === "leaf" && selected.id === leaf.leaf_id}
                        label={leaf.leaf_id}
                        subtitle={`LEAF · ${leaf.status}`}
                        icon={<GitBranch size={15} />}
                        onClick={() => choose("leaf", leaf.leaf_id)}
                      />
                    ))}
                    {data.proposals?.filter(p => p.bot_id === bot.bot_id).map(p => (
                      <NodeButton
                        key={p.proposal_id}
                        selected={selected?.kind === "proposal" && selected.id === p.proposal_id}
                        label={p.proposal_id}
                        subtitle={`EVOLUÇÃO · ${p.status}`}
                        icon={<ShieldCheck size={15} />}
                        onClick={() => choose("proposal", p.proposal_id)}
                      />
                    ))}
                  </div>
                </div>
              ))}
              {data?.leaves.filter(leaf => !botIds.has(leaf.parent_bot_id)).map(leaf => (
                <NodeButton
                  key={leaf.leaf_id}
                  selected={selected?.kind === "leaf" && selected.id === leaf.leaf_id}
                  label={leaf.leaf_id}
                  subtitle={`LEAF · pai não registrado: ${leaf.parent_bot_id}`}
                  icon={<GitBranch size={15} />}
                  onClick={() => choose("leaf", leaf.leaf_id)}
                />
              ))}
              {data && data.councils.length > 0 ? <div className="civ-group-label">CONSELHOS / COORDENAÇÃO</div> : null}
              {data?.councils.map(council => (
                <div className="civ-branch" key={council.council_id}>
                  <NodeButton
                    selected={selected?.kind === "council" && selected.id === council.council_id}
                    label={council.purpose || council.council_id}
                    subtitle={`COUNCIL · ${council.members.length} membros`}
                    icon={<Users size={17} />}
                    onClick={() => choose("council", council.council_id)}
                  />
                  <div className="civ-children">
                    {data.decisions.filter(decision => decision.council_id === council.council_id).map(decision => (
                      <NodeButton
                        key={decision.decision_id}
                        selected={selected?.kind === "decision" && selected.id === decision.decision_id}
                        label={decision.decision_id}
                        subtitle={`DECISÃO · ${decision.decision || "ratificada"}`}
                        icon={<ShieldCheck size={15} />}
                        onClick={() => choose("decision", decision.decision_id)}
                      />
                    ))}
                  </div>
                </div>
              ))}
            </section>

            <div className="civ-rail">
              <section className="civ-glass civ-detail" aria-label="Detalhes do registro">
                <div className="civ-panel-head">
                  <div><span className="civ-kicker">02 / INSPEÇÃO</span><h2>Registro Selecionado</h2></div>
                  {selected && (
                    <button className="civ-close" onClick={() => setSelected(null)} aria-label="Fechar seleção" type="button">
                      <X size={16} />
                    </button>
                  )}
                </div>
                {chosen ? (
                  <div className="civ-inspect">
                    <span className="civ-type">{selected?.kind.toUpperCase()}</span>
                    <h3>{selected?.id}</h3>
                    <dl>
                      {Object.entries(chosen).map(([key, value]) => (
                        <div key={key}>
                          <dt>{key.replaceAll("_", " ")}</dt>
                          <dd>{Array.isArray(value) ? value.join(", ") || "—" : String(value ?? "—")}</dd>
                        </div>
                      ))}
                    </dl>
                  </div>
                ) : (
                  <div className="civ-empty civ-detail-empty">
                    <ArrowUpRight size={25} />
                    <strong>Explore a rede civilizacional</strong>
                    <span>Selecione um nó da árvore para inspecionar hashes, versão, confiança e metadados.</span>
                  </div>
                )}
              </section>

              <section className="civ-glass civ-activity" aria-label="Eventos recentes">
                <div className="civ-panel-head">
                  <div><span className="civ-kicker">03 / AUDITORIA</span><h2>Atividade Recente</h2></div>
                  <Activity size={18} aria-hidden="true" />
                </div>
                {!data?.events.length ? (
                  <p className="civ-empty">Nenhum evento civilizacional registrado.</p>
                ) : (
                  <ol>
                    {data.events.map(event => (
                      <li key={event.seq}>
                        <span className="civ-timeline-dot" />
                        <div>
                          <strong>{event.name}</strong>
                          <span>#{event.seq} · {eventTime(event.timestamp)}</span>
                        </div>
                      </li>
                    ))}
                  </ol>
                )}
              </section>
            </div>
          </div>
        )}

        {viewMode === "graph" && (
          <div className="civ-glass" style={{ padding: "24px", minHeight: "650px" }}>
            <div className="civ-panel-head">
              <div>
                <span className="civ-kicker">01 / GRAFO TOPOLÓGICO</span>
                <h2>Mapa Social e DAG de Decisões da Civilização</h2>
              </div>
              <Network size={20} />
            </div>
            {!graphData || graphData.nodes.length === 0 ? (
              <div className="civ-empty">
                <Network size={36} />
                <strong>Grafo Vazio</strong>
                <span>Não há nós suficientes para compor o DAG no momento.</span>
              </div>
            ) : (
              <div style={{ display: "grid", gridTemplateColumns: "1fr 340px", gap: "20px" }}>
                <div style={{ background: "rgba(0,0,0,0.25)", borderRadius: "16px", padding: "16px", border: "1px solid var(--line)" }}>
                  <h4 style={{ margin: "0 0 12px", color: "var(--muted)", fontSize: "0.85rem" }}>
                    Nós Ativos ({graphData.nodes.length}) & Vínculos ({graphData.edges.length})
                  </h4>
                  <div style={{ display: "flex", flexWrap: "wrap", gap: "10px" }}>
                    {graphData.nodes.map(n => (
                      <button
                        key={n.id}
                        type="button"
                        onClick={() => choose(n.kind, n.id.split(":")[1])}
                        style={{
                          background: selected?.id === n.id.split(":")[1] ? "rgba(101,224,195,0.2)" : "rgba(255,255,255,0.06)",
                          border: selected?.id === n.id.split(":")[1] ? "1px solid #65e0c3" : "1px solid var(--line)",
                          borderRadius: "10px",
                          padding: "8px 12px",
                          color: "var(--ink)",
                          cursor: "pointer",
                          display: "flex",
                          alignItems: "center",
                          gap: "8px",
                          fontSize: "0.82rem",
                        }}
                      >
                        <span style={{ fontSize: "0.7rem", color: "#65e0c3", textTransform: "uppercase" }}>{n.kind}</span>
                        <strong>{n.label}</strong>
                      </button>
                    ))}
                  </div>

                  <h4 style={{ margin: "24px 0 12px", color: "var(--muted)", fontSize: "0.85rem" }}>
                    Conexões e Vínculos Sociais
                  </h4>
                  <div style={{ maxHeight: "250px", overflow: "auto", display: "grid", gap: "6px" }}>
                    {graphData.edges.map(e => (
                      <div
                        key={e.id}
                        style={{
                          fontSize: "0.75rem",
                          padding: "6px 10px",
                          background: "rgba(255,255,255,0.03)",
                          borderRadius: "6px",
                          display: "flex",
                          justifyContent: "space-between",
                        }}
                      >
                        <span><code>{e.source}</code> ➔ <code>{e.target}</code></span>
                        <strong style={{ color: "#91d6e5" }}>{e.kind}</strong>
                      </div>
                    ))}
                  </div>
                </div>

                <div className="civ-detail" style={{ background: "rgba(0,0,0,0.2)", borderRadius: "16px", padding: "16px" }}>
                  <h3 style={{ margin: "0 0 12px", fontSize: "0.95rem" }}>Detalhes do Nó</h3>
                  {chosen ? (
                    <dl style={{ margin: 0 }}>
                      {Object.entries(chosen).map(([k, v]) => (
                        <div key={k} style={{ padding: "6px 0", borderBottom: "1px solid var(--line)", fontSize: "0.78rem" }}>
                          <span style={{ color: "var(--muted)" }}>{k}: </span>
                          <strong>{String(v)}</strong>
                        </div>
                      ))}
                    </dl>
                  ) : (
                    <p style={{ color: "var(--muted)", fontSize: "0.8rem" }}>Selecione um nó para visualizar métricas e atributos.</p>
                  )}
                </div>
              </div>
            )}
          </div>
        )}

        {viewMode === "memory" && (
          <div className="civ-glass" style={{ padding: "24px", minHeight: "650px" }}>
            <div className="civ-panel-head">
              <div>
                <span className="civ-kicker">01 / PROJEÇÃO DETERMINÍSTICA</span>
                <h2>Council MEMORY.md Projection</h2>
              </div>
              <div style={{ display: "flex", gap: "10px", alignItems: "center" }}>
                {data?.councils.map(c => (
                  <button
                    key={c.council_id}
                    type="button"
                    className={`civ-refresh ${activeCouncilId === c.council_id ? "is-selected" : ""}`}
                    style={{
                      padding: "6px 12px",
                      background: activeCouncilId === c.council_id ? "rgba(101,224,195,0.2)" : "rgba(255,255,255,0.05)",
                      borderColor: activeCouncilId === c.council_id ? "#65e0c3" : "var(--line)",
                    }}
                    onClick={() => setActiveCouncilId(c.council_id)}
                  >
                    <Users size={14} /> {c.purpose || c.council_id}
                  </button>
                ))}
                <button
                  type="button"
                  className="civ-refresh"
                  onClick={rebuildMemory}
                  disabled={memoryLoading}
                  style={{ background: "rgba(101,224,195,0.15)", borderColor: "#65e0c3", color: "#65e0c3" }}
                >
                  <RotateCcw size={14} /> Rebuild Projection
                </button>
              </div>
            </div>

            {memoryLoading && !councilMemory ? (
              <p className="civ-empty">Carregando projeção de memória…</p>
            ) : !councilMemory ? (
              <div className="civ-empty">
                <BookOpen size={30} />
                <strong>Nenhum Conselho Selecionado</strong>
                <span>Selecione um conselho acima para inspecionar a projeção canônica do MEMORY.md.</span>
              </div>
            ) : (
              <div>
                <div style={{ display: "flex", gap: "16px", marginBottom: "16px", fontSize: "0.78rem", color: "var(--muted)" }}>
                  <span><strong>Last Event Seq:</strong> {councilMemory.record.last_seq}</span>
                  <span><strong>Decisões:</strong> {councilMemory.record.decisions_count}</span>
                  <span><strong>SHA-256:</strong> <code>{councilMemory.record.content_hash.slice(0, 16)}…</code></span>
                  {councilMemory.record.has_operator_override ? (
                    <span style={{ color: "#ffecbd" }}>⚠️ <strong>Operador Manual Ativo</strong></span>
                  ) : (
                    <span style={{ color: "#65e0c3" }}>✓ <strong>100% Determinístico</strong></span>
                  )}
                </div>
                <div
                  style={{
                    background: "rgba(0,0,0,0.35)",
                    padding: "20px",
                    borderRadius: "14px",
                    border: "1px solid var(--line)",
                    fontFamily: "ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace",
                    fontSize: "0.85rem",
                    lineHeight: "1.6",
                    whiteSpace: "pre-wrap",
                    maxHeight: "550px",
                    overflow: "auto",
                  }}
                >
                  {councilMemory.content}
                </div>
              </div>
            )}
          </div>
        )}

        <footer className="civ-footer">
          <span>
            <span className="civ-pulse" /> {error ? "CONEXÃO DEGRADADA" : updated ? `ATUALIZADO ${updated.toLocaleTimeString()}` : "AGUARDANDO DADOS"}
          </span>
          <span>EVENT STORE · CURSOR {data?.cursor ?? 0} · REFRESH 30S</span>
        </footer>
      </div>
    </main>
  );
}
