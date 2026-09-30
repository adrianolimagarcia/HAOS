import { useCallback, useEffect, useMemo, useState } from "react";
import { Activity, ArrowUpRight, BookOpen, Bot, ChevronRight, Cpu, GitBranch, Network, Play, RefreshCw, RotateCcw, Save, Settings2, ShieldCheck, SlidersHorizontal, Users, X } from "lucide-react";
import { fetchJSON } from "@/lib/api";
import { CivConstellation } from "@/components/CivConstellation";
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

interface ModelConfig { inherit: boolean; provider?: string | null; model_name?: string | null; temperature?: number; max_tokens?: number }
interface AgentConfig extends BotNode {
  id: string;
  role: string;
  domain: string;
  description: string;
  model: ModelConfig;
  capabilities: { allowed_tools: string[]; max_risk_tier: string; allowed_write_paths: string[] };
  memory: { working: boolean; session: boolean; project: boolean; domain: boolean; global_civ: boolean };
  budget: { max_tokens: number; timeout_seconds: number; max_cost_usd: number; max_iterations: number };
  policies: { require_human_approval: string[]; risk_tolerance: string };
  effective_model?: string;
  model_source?: string;
}
interface AvailableModel { id: string; provider: string; label: string; context_window?: number; cost_per_million_tokens?: number }
interface MissionEvent { time: number; type: string; agent_id: string; description: string; status: string }
interface MissionNode { id: string; agent_id: string; role: string; action: string; status: string }
interface Mission {
  id: string;
  title: string;
  goal: string;
  status: string;
  agents: string[];
  workflow: { nodes: MissionNode[]; edges: { from_node: string; to_node: string }[] };
  budget: { max_cost_usd: number; max_tokens: number; timeout_seconds: number };
  policies: Record<string, boolean>;
  simulation?: Record<string, unknown> | null;
  events: MissionEvent[];
  progress_pct: number;
}

type StudioTab = "observatory" | "agents" | "missions" | "office";

type AgentDraft = Omit<AgentConfig, "bot_id" | "version" | "bundle_hash">;

function emptyAgentDraft(): AgentDraft {
  return {
    id: "agent-new",
    name: "New Agent",
    status: "active",
    role: "builder",
    domain: "Software Engineering",
    description: "",
    model: { inherit: true, provider: null, model_name: null, temperature: 0.2, max_tokens: 4096 },
    capabilities: { allowed_tools: ["read_file", "terminal", "git"], max_risk_tier: "MEDIUM", allowed_write_paths: [] },
    memory: { working: true, session: true, project: true, domain: false, global_civ: false },
    budget: { max_tokens: 150000, timeout_seconds: 1800, max_cost_usd: 5, max_iterations: 25 },
    policies: { require_human_approval: ["merge", "deploy"], risk_tolerance: "CONSERVATIVE" },
  };
}

type ViewMode = "tree" | "graph" | "memory";
/**
 * Kept open on purpose: the graph API emits kinds like "constitution" and may
 * add more, and the selection inspector must survive an unknown one.
 */
type Selection = { kind: string; id: string };

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
  const [studioTab, setStudioTab] = useState<StudioTab>("observatory");
  const [agents, setAgents] = useState<AgentConfig[]>([]);
  const [availableModels, setAvailableModels] = useState<AvailableModel[]>([]);
  const [sessionModel, setSessionModel] = useState("");
  const [missions, setMissions] = useState<Mission[]>([]);
  const [agentDraft, setAgentDraft] = useState<AgentDraft | null>(null);
  const [agentEditorTab, setAgentEditorTab] = useState("identity");
  const [agentSaving, setAgentSaving] = useState(false);
  const [missionSaving, setMissionSaving] = useState(false);
  const [missionGoal, setMissionGoal] = useState("");
  const [missionTitle, setMissionTitle] = useState("");
  const [missionAgentIds, setMissionAgentIds] = useState<string[]>([]);
  const [selectedMissionId, setSelectedMissionId] = useState<string | null>(null);

  const loadControlPlane = useCallback(async () => {
    try {
      const [agentResponse, modelResponse, missionResponse] = await Promise.all([
        fetchJSON<{ agents: AgentConfig[]; session_model: string }>("/api/civilization/agents"),
        fetchJSON<{ models: AvailableModel[]; session_model: string }>("/api/civilization/models/available"),
        fetchJSON<{ missions: Mission[] }>("/api/civilization/missions"),
      ]);
      setAgents(agentResponse.agents ?? []);
      setSessionModel(agentResponse.session_model || modelResponse.session_model || "");
      setAvailableModels(modelResponse.models ?? []);
      setMissions(missionResponse.missions ?? []);
    } catch (cause) {
      setError(errorMessage(cause));
    }
  }, []);

  useEffect(() => {
    if (studioTab === "observatory") return;
    void loadControlPlane();
  }, [loadControlPlane, profile, studioTab, tick]);

  const selectedMission = missions.find(mission => mission.id === selectedMissionId) ?? missions[0] ?? null;

  const openAgentEditor = useCallback((agent?: AgentConfig) => {
    setAgentDraft(agent ? { ...agent, model: { ...agent.model }, capabilities: { ...agent.capabilities }, memory: { ...agent.memory }, budget: { ...agent.budget }, policies: { ...agent.policies } } : emptyAgentDraft());
    setAgentEditorTab("identity");
  }, []);

  const saveAgent = useCallback(async () => {
    if (!agentDraft) return;
    setAgentSaving(true);
    try {
      const isExisting = agents.some(agent => agent.id === agentDraft.id);
      const response = await fetchJSON<AgentConfig>(`/api/civilization/agents${isExisting ? `/${encodeURIComponent(agentDraft.id)}` : ""}`, {
        method: isExisting ? "PUT" : "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(agentDraft),
      });
      setAgents(current => isExisting ? current.map(agent => agent.id === response.id ? response : agent) : [...current, response]);
      setAgentDraft(null);
    } catch (cause) {
      setError(errorMessage(cause));
    } finally {
      setAgentSaving(false);
    }
  }, [agentDraft, agents]);

  const duplicateAgent = useCallback(async (agent: AgentConfig) => {
    try {
      const response = await fetchJSON<AgentConfig>(`/api/civilization/agents/${encodeURIComponent(agent.id)}/duplicate`, { method: "POST" });
      setAgents(current => [...current, response]);
    } catch (cause) {
      setError(errorMessage(cause));
    }
  }, []);

  const createMission = useCallback(async () => {
    if (!missionTitle.trim() || !missionGoal.trim()) return;
    setMissionSaving(true);
    try {
      const response = await fetchJSON<Mission>("/api/civilization/missions", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title: missionTitle, goal: missionGoal, agents: missionAgentIds }),
      });
      setMissions(current => [...current, response]);
      setSelectedMissionId(response.id);
      setMissionTitle("");
      setMissionGoal("");
      setMissionAgentIds([]);
    } catch (cause) {
      setError(errorMessage(cause));
    } finally {
      setMissionSaving(false);
    }
  }, [missionAgentIds, missionGoal, missionTitle]);

  const missionAction = useCallback(async (mission: Mission, action: "simulate" | "start" | "pause" | "resume") => {
    try {
      const response = await fetchJSON<Mission | Record<string, unknown>>(`/api/civilization/missions/${encodeURIComponent(mission.id)}/${action}`, { method: "POST" });
      if ("id" in response) setMissions(current => current.map(item => item.id === mission.id ? response as Mission : item));
    } catch (cause) {
      setError(errorMessage(cause));
    }
  }, []);
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

  // Graph nodes are keyed "kind:id"; the details panel selects by the bare id.
  const chosenId = selected ? `${selected.kind}:${selected.id}` : null;

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
            {studioTab === "observatory" && (
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
            )}
            <button className="civ-refresh" onClick={refresh} type="button" disabled={loading} aria-label="Atualizar observatório">
              <RefreshCw size={16} /> Atualizar
            </button>
          </div>
        </header>

        <nav className="civ-nav-tabs" aria-label="Civilization workspace">
          {([
            ["observatory", "Observatory", Activity],
            ["agents", "Agent Studio", Bot],
            ["missions", "Mission Center", GitBranch],
            ["office", "2D Office", Cpu],
          ] as const).map(([tab, label, Icon]) => (
            <button key={tab} type="button" className={`civ-tab-btn ${studioTab === tab ? "is-active" : ""}`} onClick={() => setStudioTab(tab)}>
              <Icon size={15} /> {label}
            </button>
          ))}
        </nav>

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

        {studioTab === "agents" && (
          <section aria-label="Agent Studio">
            <div className="civ-agent-header">
              <div><span className="civ-kicker">CONTROL PLANE / AGENT REGISTRY</span><h2>Agent Studio</h2><p style={{ color: "var(--muted)", margin: "5px 0 0", fontSize: "0.82rem" }}>Configure bots declaratively. The kernel validates every capability and memory boundary.</p></div>
              <button type="button" className="civ-btn-primary" onClick={() => openAgentEditor()}><Bot size={16} /> New agent</button>
            </div>
            <div className="civ-agent-grid">
              {agents.map(agent => (
                <article className="civ-glass civ-agent-card" key={agent.id}>
                  <div>
                    <div className="civ-agent-top"><div className="civ-agent-title"><span className="civ-node-icon"><Bot size={18} /></span><div><strong>{agent.name}</strong><div style={{ color: "var(--muted)", fontSize: "0.7rem" }}>{agent.id}</div></div></div><span className={`civ-role-badge civ-role-${agent.role.toLowerCase()}`}>{agent.role}</span></div>
                    <p style={{ color: "var(--muted)", minHeight: "38px", fontSize: "0.78rem", margin: "8px 0" }}>{agent.description || "No description"}</p>
                    <div style={{ display: "flex", flexWrap: "wrap", gap: "7px" }}><span className={`civ-model-pill ${agent.model_source === "AGENT_OVERRIDE" ? "is-override" : ""}`}><Cpu size={12} /> {agent.effective_model || sessionModel || "Inherited"}</span><span className="civ-model-pill"><ShieldCheck size={12} /> {agent.capabilities.max_risk_tier}</span></div>
                    <div className="civ-cascade-box" style={{ marginTop: "14px" }}><div className="civ-cascade-row"><span style={{ color: "var(--muted)" }}>Effective model</span><strong>{agent.effective_model || sessionModel}</strong></div><div className="civ-cascade-row"><span style={{ color: "var(--muted)" }}>Resolution source</span><span style={{ color: "#a2f2eb", fontSize: "0.7rem" }}>{agent.model_source || "SESSION_MODEL"}</span></div></div>
                  </div>
                  <div className="civ-agent-footer"><small style={{ color: "var(--muted)" }}>{agent.memory.project ? "Project memory" : "Session only"} · {agent.budget.max_tokens.toLocaleString()} tokens</small><div className="civ-btn-row"><button type="button" className="civ-btn-ghost" onClick={() => void duplicateAgent(agent)} title="Duplicate"><GitBranch size={13} /></button><button type="button" className="civ-btn-ghost" onClick={() => openAgentEditor(agent)}><Settings2 size={13} /> Edit</button></div></div>
                </article>
              ))}
            </div>
            {!agents.length && <div className="civ-glass civ-empty"><Bot size={30} /><strong>No configured agents</strong><span>Create the first agent to assemble your civilization.</span></div>}
          </section>
        )}

        {studioTab === "missions" && (
          <section aria-label="Mission Center">
            <div className="civ-agent-header"><div><span className="civ-kicker">MISSION CENTER / ORCHESTRATION</span><h2>Mission Designer</h2></div><span className="civ-model-pill"><Cpu size={13} /> Session model: {sessionModel || "—"}</span></div>

                    <div className="civ-columns"><section className="civ-glass" style={{ padding: "22px" }}><div className="civ-panel-head"><div><span className="civ-kicker">NEW MISSION</span><h2>Intent and crew</h2></div><GitBranch size={18} /></div><div className="civ-form-group"><label htmlFor="mission-title">Mission title</label><input id="mission-title" className="civ-input" value={missionTitle} onChange={event => setMissionTitle(event.target.value)} placeholder="Build authentication system" /></div><div className="civ-form-group"><label htmlFor="mission-goal">Human goal</label><textarea id="mission-goal" className="civ-textarea" rows={4} value={missionGoal} onChange={event => setMissionGoal(event.target.value)} placeholder="Implement OAuth with secure token rotation" /></div><div className="civ-form-group"><label>Required agents</label><div style={{ display: "grid", gap: "7px" }}>{agents.map(agent => <label key={agent.id} style={{ display: "flex", gap: "8px", alignItems: "center", color: "var(--muted)", fontSize: "0.78rem" }}><input type="checkbox" checked={missionAgentIds.includes(agent.id)} onChange={event => setMissionAgentIds(current => event.target.checked ? [...current, agent.id] : current.filter(id => id !== agent.id))} /> {agent.name} <span style={{ color: "#91d6e5" }}>({agent.role})</span></label>)}</div></div><button type="button" className="civ-btn-primary" onClick={() => void createMission()} disabled={missionSaving || !missionTitle.trim() || !missionGoal.trim()}><Save size={15} /> {missionSaving ? "Creating…" : "Create mission"}</button></section><section className="civ-glass" style={{ padding: "22px" }}><div className="civ-panel-head"><div><span className="civ-kicker">ACTIVE MISSIONS</span><h2>Execution board</h2></div><Play size={18} /></div>{missions.map(mission => <button type="button" key={mission.id} onClick={() => setSelectedMissionId(mission.id)} style={{ width: "100%", textAlign: "left", marginBottom: "10px", padding: "14px", borderRadius: "14px", border: selectedMission?.id === mission.id ? "1px solid #65e0c3" : "1px solid var(--line)", background: selectedMission?.id === mission.id ? "rgba(101,224,195,0.12)" : "rgba(255,255,255,0.04)", color: "var(--ink)", cursor: "pointer" }}><strong>{mission.title}</strong><div style={{ color: "var(--muted)", fontSize: "0.72rem", marginTop: "5px" }}>{mission.status} · {mission.agents.length} agents · {mission.progress_pct}%</div><div className="civ-progress-bar"><div className="civ-progress-fill" style={{ width: `${mission.progress_pct}%` }} /></div></button>)}{!missions.length && <div className="civ-empty">No missions created.</div>}</section></div>
            {selectedMission && <section className="civ-glass" style={{ marginTop: "16px", padding: "22px" }}><div className="civ-panel-head"><div><span className="civ-kicker">MISSION / {selectedMission.id}</span><h2>{selectedMission.title}</h2></div><div className="civ-btn-row"><button type="button" className="civ-btn-ghost" onClick={() => void missionAction(selectedMission, "simulate")}><SlidersHorizontal size={14} /> Simulate</button><button type="button" className="civ-btn-primary" onClick={() => void missionAction(selectedMission, selectedMission.status === "PAUSED" ? "resume" : "start")}><Play size={14} /> {selectedMission.status === "PAUSED" ? "Resume" : "Start"}</button></div></div><p style={{ color: "var(--muted)", fontSize: "0.82rem" }}>{selectedMission.goal}</p><div className="civ-dag-container">{selectedMission.workflow.nodes.map((node, index) => <div key={node.id} style={{ display: "flex", alignItems: "center", gap: "16px" }}><div className={`civ-dag-step ${node.status === "COMPLETED" ? "is-done" : ""}`}><strong>{node.action}</strong><small style={{ color: "var(--muted)" }}>{node.agent_id} · {node.status}</small></div>{index < selectedMission.workflow.nodes.length - 1 && <ChevronRight className="civ-dag-arrow" size={18} />}</div>)}</div><ol className="civ-activity" style={{ padding: 0, margin: 0 }}>{selectedMission.events.map((event, index) => <li key={`${event.time}-${index}`}><span className="civ-timeline-dot" /><div><strong>{event.description}</strong><span>{event.type} · {event.agent_id}</span></div></li>)}</ol></section>}
          </section>
        )}
         {studioTab === "observatory" && viewMode === "tree" && (
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

         {studioTab === "observatory" && viewMode === "graph" && (
          <div className="civ-glass" style={{ padding: "24px", minHeight: "650px" }}>
            <div className="civ-panel-head">
              <div>
                <span className="civ-kicker">01 / GRAFO TOPOLÓGICO</span>
                <h2>Mapa Social e DAG de Decisões da Civilização</h2>
              </div>
              <Network size={20} />
            </div>
            {!graphData || !Array.isArray(graphData.nodes) || graphData.nodes.length === 0 ? (
              <div className="civ-empty">
                <Network size={36} />
                <strong>Grafo Vazio</strong>
                <span>Não há nós suficientes para compor o DAG no momento.</span>
              </div>
            ) : (
              <div className="civ-graph-layout">
                <div className="civ-graph-stage">
                  <CivConstellation
                    nodes={graphData.nodes}
                    edges={graphData.edges}
                    selectedId={chosenId}
                    onSelect={node => choose(node.kind, node.id.split(":")[1])}
                  />
                  <h4 className="civ-graph-links-title">
                    Conexões e Vínculos Sociais ({graphData.edges.length})
                  </h4>
                  <div className="civ-graph-links">
                    {graphData.edges.map(e => (
                      <div key={e.id} className="civ-graph-link">
                        <span><code>{e.source}</code> ➔ <code>{e.target}</code></span>
                        <strong>{e.kind}</strong>
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

         {studioTab === "office" && (
          <section aria-label="2D robot office">
            <div className="civ-agent-header"><div><span className="civ-kicker">LIVING CIVILIZATION / EVENT STREAM</span><h2>HAOS Office Floor</h2><p style={{ color: "var(--muted)", margin: "5px 0 0", fontSize: "0.82rem" }}>A visual, non-authoritative projection of agent activity. Kernel state remains the source of truth.</p></div><span className="civ-model-pill"><Activity size={13} /> Live event projection</span></div>
            <div className="civ-office-floor"><div className="civ-office-grid-overlay" aria-hidden="true" /><div className="civ-office-stations">{(agents.length ? agents : data?.bots.map(bot => ({ ...emptyAgentDraft(), ...bot, id: bot.bot_id, role: "builder", domain: "Unknown", description: "", model: { inherit: true }, capabilities: { allowed_tools: [], max_risk_tier: "READ", allowed_write_paths: [] }, memory: { working: true, session: true, project: false, domain: false, global_civ: false }, budget: { max_tokens: 0, timeout_seconds: 0, max_cost_usd: 0, max_iterations: 0 }, policies: { require_human_approval: [], risk_tolerance: "CONSERVATIVE" } } as AgentConfig)) ?? []).map((agent, index) => <div className="civ-station-desk" key={agent.id}><div className={`civ-robot-figure ${agent.status === "active" || index % 2 === 0 ? "is-working" : ""}`}><Bot size={29} /><span className="civ-robot-bubble">{agent.status === "active" ? "working" : agent.status}</span></div><strong>{agent.name}</strong><span className={`civ-role-badge civ-role-${agent.role.toLowerCase()}`}>{agent.role}</span><small style={{ color: "var(--muted)" }}>{agent.status === "active" ? "Processing mission events…" : "Awaiting assignment"}</small><div className="civ-model-pill"><Cpu size={11} /> {agent.effective_model || sessionModel || "Inherited"}</div></div>)}{!agents.length && !data?.bots.length && <div className="civ-empty"><Bot size={34} /><strong>The office is empty</strong><span>Create agents in Agent Studio to populate the floor.</span></div>}</div></div>
          </section>
        )}

        {studioTab === "observatory" && viewMode === "memory" && (
          <div className="civ-glass" style={{ padding: "24px", minHeight: "650px" }}>
            <div className="civ-panel-head"><div><span className="civ-kicker">01 / PROJEÇÃO DETERMINÍSTICA</span><h2>Council MEMORY.md Projection</h2></div><div style={{ display: "flex", gap: "10px", alignItems: "center" }}>{data?.councils.map(c => <button key={c.council_id} type="button" className={`civ-refresh ${activeCouncilId === c.council_id ? "is-selected" : ""}`} style={{ padding: "6px 12px", background: activeCouncilId === c.council_id ? "rgba(101,224,195,0.2)" : "rgba(255,255,255,0.05)", borderColor: activeCouncilId === c.council_id ? "#65e0c3" : "var(--line)" }} onClick={() => setActiveCouncilId(c.council_id)}><Users size={14} /> {c.purpose || c.council_id}</button>)}<button type="button" className="civ-refresh" onClick={rebuildMemory} disabled={memoryLoading}><RotateCcw size={14} /> Rebuild Projection</button></div></div>
            {memoryLoading && !councilMemory ? <p className="civ-empty">Carregando projeção de memória…</p> : !councilMemory ? <div className="civ-empty"><BookOpen size={30} /><strong>Nenhum Conselho Selecionado</strong><span>Selecione um conselho acima para inspecionar a projeção canônica do MEMORY.md.</span></div> : <div><div style={{ display: "flex", gap: "16px", marginBottom: "16px", fontSize: "0.78rem", color: "var(--muted)" }}><span><strong>Last Event Seq:</strong> {councilMemory.record.last_seq}</span><span><strong>Decisões:</strong> {councilMemory.record.decisions_count}</span><span><strong>SHA-256:</strong> <code>{councilMemory.record.content_hash.slice(0, 16)}…</code></span></div><div style={{ background: "rgba(0,0,0,0.35)", padding: "20px", borderRadius: "14px", border: "1px solid var(--line)", fontFamily: "ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace", fontSize: "0.85rem", lineHeight: "1.6", whiteSpace: "pre-wrap", maxHeight: "550px", overflow: "auto" }}>{councilMemory.content}</div></div>}
          </div>
        )}

        {agentDraft && (
          <div className="civ-modal-backdrop" role="presentation" onMouseDown={event => { if (event.target === event.currentTarget) setAgentDraft(null); }}>
            <section className="civ-modal-dialog" role="dialog" aria-modal="true" aria-labelledby="agent-editor-title">
              <div className="civ-modal-head"><div><span className="civ-kicker">AGENT DESIGNER</span><h2 id="agent-editor-title" style={{ margin: "6px 0 0" }}>{agents.some(agent => agent.id === agentDraft.id) ? "Edit agent" : "Create agent"}</h2></div><button type="button" className="civ-close" onClick={() => setAgentDraft(null)} aria-label="Close editor"><X size={16} /></button></div>
              <div className="civ-modal-tabs">{[["identity", "Identity"], ["model", "Model"], ["capabilities", "Capabilities"], ["memory", "Memory & budget"], ["policies", "Policies"]].map(([tab, label]) => <button type="button" key={tab} className={`civ-modal-tab ${agentEditorTab === tab ? "is-active" : ""}`} onClick={() => setAgentEditorTab(tab)}>{label}</button>)}</div>
              <div className="civ-modal-body">
                {agentEditorTab === "identity" && <><div className="civ-form-group"><label htmlFor="agent-id">Immutable ID</label><input id="agent-id" className="civ-input" value={agentDraft.id} onChange={event => setAgentDraft({ ...agentDraft, id: event.target.value })} disabled={agents.some(agent => agent.id === agentDraft.id)} /></div><div className="civ-form-group"><label htmlFor="agent-name">Display name</label><input id="agent-name" className="civ-input" value={agentDraft.name} onChange={event => setAgentDraft({ ...agentDraft, name: event.target.value })} /></div><div className="civ-form-group"><label htmlFor="agent-role">Constitutional role</label><select id="agent-role" className="civ-select" value={agentDraft.role} onChange={event => setAgentDraft({ ...agentDraft, role: event.target.value })}>{["planner", "builder", "critic", "validator", "promoter", "security"].map(role => <option key={role}>{role}</option>)}</select></div><div className="civ-form-group"><label htmlFor="agent-domain">Domain</label><input id="agent-domain" className="civ-input" value={agentDraft.domain} onChange={event => setAgentDraft({ ...agentDraft, domain: event.target.value })} /></div><div className="civ-form-group"><label htmlFor="agent-description">Description</label><textarea id="agent-description" className="civ-textarea" rows={3} value={agentDraft.description} onChange={event => setAgentDraft({ ...agentDraft, description: event.target.value })} /></div></>}
                {agentEditorTab === "model" && <><div className="civ-form-group"><label><input type="checkbox" checked={agentDraft.model.inherit} onChange={event => setAgentDraft({ ...agentDraft, model: { ...agentDraft.model, inherit: event.target.checked } })} /> Inherit session model</label><div className="civ-cascade-box"><div className="civ-cascade-row"><span style={{ color: "var(--muted)" }}>Session model</span><strong>{sessionModel || "—"}</strong></div><div className="civ-cascade-row"><span style={{ color: "var(--muted)" }}>Effective model</span><strong>{agentDraft.model.inherit ? sessionModel || "global default" : agentDraft.model.model_name || "select an override"}</strong></div></div></div><div className="civ-form-group"><label htmlFor="agent-model">Specific model override</label><select id="agent-model" className="civ-select" value={agentDraft.model.model_name || ""} disabled={agentDraft.model.inherit} onChange={event => { const model = availableModels.find(item => item.id === event.target.value); setAgentDraft({ ...agentDraft, model: { ...agentDraft.model, inherit: false, model_name: model?.id || event.target.value, provider: model?.provider || null } }); }}><option value="">Select model</option>{availableModels.map(model => <option key={model.id} value={model.id}>{model.label || model.id} · {model.provider}</option>)}</select></div><div className="civ-form-group"><label htmlFor="agent-temperature">Temperature</label><input id="agent-temperature" className="civ-input" type="number" min="0" max="2" step="0.1" value={agentDraft.model.temperature ?? 0.2} onChange={event => setAgentDraft({ ...agentDraft, model: { ...agentDraft.model, temperature: Number(event.target.value) } })} /></div></>}
                {agentEditorTab === "capabilities" && <><div className="civ-form-group"><label>Allowed tools</label><div style={{ display: "grid", gridTemplateColumns: "repeat(2, minmax(0, 1fr))", gap: "8px" }}>{["read_file", "terminal", "git", "filesystem", "network", "deployment"].map(tool => <label key={tool} style={{ fontSize: "0.78rem", color: "var(--muted)" }}><input type="checkbox" checked={agentDraft.capabilities.allowed_tools.includes(tool)} onChange={event => setAgentDraft({ ...agentDraft, capabilities: { ...agentDraft.capabilities, allowed_tools: event.target.checked ? [...agentDraft.capabilities.allowed_tools, tool] : agentDraft.capabilities.allowed_tools.filter(item => item !== tool) } })} /> {tool}</label>)}</div></div><div className="civ-form-group"><label htmlFor="agent-risk">Maximum risk tier</label><select id="agent-risk" className="civ-select" value={agentDraft.capabilities.max_risk_tier} onChange={event => setAgentDraft({ ...agentDraft, capabilities: { ...agentDraft.capabilities, max_risk_tier: event.target.value } })}>{["READ", "LOW", "MEDIUM", "HIGH", "CRITICAL"].map(risk => <option key={risk}>{risk}</option>)}</select></div></>}
                {agentEditorTab === "memory" && <><div className="civ-form-group"><label>Readable memory scopes</label>{(["working", "session", "project", "domain", "global_civ"] as const).map(scope => <label key={scope} style={{ fontSize: "0.78rem", color: "var(--muted)" }}><input type="checkbox" checked={agentDraft.memory[scope]} disabled={scope === "global_civ" && agentDraft.role !== "promoter"} onChange={event => setAgentDraft({ ...agentDraft, memory: { ...agentDraft.memory, [scope]: event.target.checked } })} /> {scope === "global_civ" ? "Global civilization memory (PROMOTER only)" : scope}</label>)}</div><div className="civ-form-group"><label htmlFor="agent-tokens">Token limit</label><input id="agent-tokens" className="civ-input" type="number" value={agentDraft.budget.max_tokens} onChange={event => setAgentDraft({ ...agentDraft, budget: { ...agentDraft.budget, max_tokens: Number(event.target.value) } })} /></div><div className="civ-form-group"><label htmlFor="agent-cost">Cost limit (USD)</label><input id="agent-cost" className="civ-input" type="number" step="0.01" value={agentDraft.budget.max_cost_usd} onChange={event => setAgentDraft({ ...agentDraft, budget: { ...agentDraft.budget, max_cost_usd: Number(event.target.value) } })} /></div></>}
                {agentEditorTab === "policies" && <><div className="civ-form-group"><label htmlFor="agent-tolerance">Risk tolerance</label><select id="agent-tolerance" className="civ-select" value={agentDraft.policies.risk_tolerance} onChange={event => setAgentDraft({ ...agentDraft, policies: { ...agentDraft.policies, risk_tolerance: event.target.value } })}><option>CONSERVATIVE</option><option>BALANCED</option><option>EXPERIMENTAL</option></select></div><div className="civ-form-group"><label>Human approval gates</label>{["merge", "deploy", "memory_promotion"].map(gate => <label key={gate} style={{ fontSize: "0.78rem", color: "var(--muted)" }}><input type="checkbox" checked={agentDraft.policies.require_human_approval.includes(gate)} onChange={event => setAgentDraft({ ...agentDraft, policies: { ...agentDraft.policies, require_human_approval: event.target.checked ? [...agentDraft.policies.require_human_approval, gate] : agentDraft.policies.require_human_approval.filter(item => item !== gate) } })} /> {gate}</label>)}</div></>}
              </div>
              <div className="civ-modal-head"><span style={{ color: "var(--muted)", fontSize: "0.72rem" }}>Kernel validates constitutional invariants on save.</span><button type="button" className="civ-btn-primary" onClick={() => void saveAgent()} disabled={agentSaving}><Save size={15} /> {agentSaving ? "Saving…" : "Save configuration"}</button></div>
            </section>
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
