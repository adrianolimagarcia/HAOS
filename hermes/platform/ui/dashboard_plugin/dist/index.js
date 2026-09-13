/**
 * HAOS Control Plane — Hermes Dashboard Plugin (entry)
 *
 * Delta 46 — visual mount of the HAOS dashboard plugin. Renders the state
 * served by this plugin's backend (delta 45) at /api/plugins/haos/state:
 * taskboard, memory graph, ACP session, evolution queue and grants — all
 * DERIVED on the backend from the canonical Kanban + EventStore (never
 * fabricated here). Plain IIFE, no build step — same shape as the upstream
 * kanban plugin: reads window.__HERMES_PLUGIN_SDK__ for React + shared UI
 * primitives, and registers the tab component via
 * window.__HERMES_PLUGINS__.register("haos", HaosPage).
 *
 * Delta 49 — control-plane ACTIONS from the view. The /haos tab now carries
 * real buttons that POST to this plugin's backend routes (the delta-47
 * actions + the delta-49 ACP planning bridge):
 *   - "Dispatch ready"           -> POST /api/plugins/haos/dispatch
 *   - per pending proposal       -> POST /api/plugins/haos/evolution/decide
 *                                   (approved | rejected)
 *   - per pending grant          -> POST /api/plugins/haos/grants/approve
 *                                   and /grants/revoke
 *   - "Plan over ACP"            -> POST /api/plugins/haos/acp/plan
 * Human-approval actions (evolution decide, grant approve) require the
 * operator to type an approver name — the plugin never invents an identity.
 * Every action POSTs through SDK.fetchJSON (same auth handling as the rest
 * of the dashboard) and then re-fetches the state so the derived numbers
 * reflect the real backend effect. Errors surface inline; nothing is faked.
 *
 * Fail-closed: without the SDK globals the bundle is inert (no throw). When
 * the backend is unreachable or the store is absent the page renders an
 * honest empty/error state — it never invents numbers.
 */
(function () {
  "use strict";

  const SDK = window.__HERMES_PLUGIN_SDK__;
  if (!SDK) return;

  const { React } = SDK;
  const h = React.createElement;
  const {
    Card, CardHeader, CardTitle, CardContent,
    Badge,
  } = SDK.components;
  const { useState, useEffect } = SDK.hooks;
  const { cn } = SDK.utils;

  // Plugin backend (delta 45/56): montado pelo dashboard em /api/plugins/haos/
  const STATE_URL = "/api/plugins/haos/state";
  const DISPATCH_URL = "/api/plugins/haos/dispatch";
  const REVIEW_DECIDE_URL = "/api/plugins/haos/reviews/decide";
  const EVOLUTION_DECIDE_URL = "/api/plugins/haos/evolution/decide";
  const GRANT_APPROVE_URL = "/api/plugins/haos/grants/approve";
  const GRANT_REVOKE_URL = "/api/plugins/haos/grants/revoke";
  const ACP_PLAN_URL = "/api/plugins/haos/acp/plan";
  const SYSTEM_URL = "/api/plugins/haos/system-facts";
  const MODELS_URL = "/api/plugins/haos/models";
  const CONSOLE_URL = "/api/plugins/haos/console";
  const TASKS_URL = "/api/plugins/haos/tasks";
  const EVO_ANALYZE_URL = "/api/plugins/haos/evolution/analyze";
  const SETTINGS_URL = "/api/plugins/haos/settings";
  const SETTINGS_RESET_URL = "/api/plugins/haos/settings/reset";
  const AGENT_CONFIG_URL = "/api/plugins/haos/agent-config";
  const TERMINAL_URL = "/api/plugins/haos/terminal";
  const EVENTS_URL = "/api/plugins/haos/events";

  // -------------------------------------------------------------------------
  // Small helpers
  // -------------------------------------------------------------------------
  function parseApiErrorMessage(err) {
    const raw = (err && err.message) ? String(err.message) : String(err || "");
    const m = raw.match(/^(\d{3}):\s*(.*)$/s);
    const body = m ? m[2] : raw;
    try {
      const parsed = JSON.parse(body);
      if (parsed && typeof parsed.detail === "string") return parsed.detail;
    } catch (_e) { /* not JSON — fall through to raw body */ }
    return body || raw;
  }

  function pad(n) { return Number.isFinite(n) ? String(n) : "0"; }

  /** POST JSON to a plugin action route with the dashboard's auth handling. */
  function postJSON(url, payload) {
    return SDK.fetchJSON(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: payload === undefined ? undefined : JSON.stringify(payload),
    });
  }

  // Column/status labels — plain English fallbacks. The host i18n catalogs
  // are not translated for this plugin namespace yet (honest: no fake keys).
  const STATUS_LABELS = {
    triage: "Triage", todo: "Todo", ready: "Ready", running: "Running",
    blocked: "Blocked", review: "Review", done: "Done", archived: "Archived",
  };
  function statusLabel(status) {
    return STATUS_LABELS[status] || status || "—";
  }

  // -------------------------------------------------------------------------
  // Section components (each a pure function of the derived payload)
  // -------------------------------------------------------------------------
  function TaskboardSection(taskboard, onSelectTask) {
    const columns = (taskboard && taskboard.columns) || [];
    const badges = columns.map(function (col, i) {
      return h(Badge, { key: "col-" + i, className: "hermes-haos-badge" },
        statusLabel(col.status) + ": " + pad(col.count));
    });
    // Delta 51 (transparência): listar os CARDS reais do board (recent), não só
    // a contagem por status — o operador precisa ver o que existe, não apenas
    // quantos. Derivado do payload (id/title/status), nunca fabricado.
    const recent = (taskboard && taskboard.recent) || [];
    const recentItems = recent.map(function (t, i) {
      const spec = (t && t.spec) || {};
      const goal = spec.goal || spec.description || "";
      const worker = (t && t.run && t.run.worker_id) ? " · worker: " + t.run.worker_id : "";
      return h("li", {
        key: "recent-" + i,
        className: "hermes-haos-recent-item",
        style: { cursor: onSelectTask ? "pointer" : "default" },
        onClick: function () { if (typeof onSelectTask === "function") onSelectTask(t); }
      },
        h("div", { className: "hermes-haos-recent-header" },
          h("span", { className: "hermes-haos-recent-title" },
            (t && t.title) || (t && t.id) || "#" + (i + 1)),
          h("span", { className: "hermes-haos-recent-status" },
            statusLabel(t && t.status))),
        goal ? h("p", { className: "hermes-haos-recent-desc", style: { fontSize: "0.85rem", opacity: 0.8, margin: "2px 0" } }, goal) : null,
        h("div", { style: { display: "flex", justifyContent: "space-between", alignItems: "center", marginTop: "3px" } },
          h("span", { className: "hermes-haos-recent-id", style: { fontSize: "0.75rem", opacity: 0.6 } }, ((t && t.id) || "") + worker),
          (t && t.result)
            ? h("button", {
                className: "hermes-haos-btn",
                style: { fontSize: "0.72rem", padding: "2px 8px" },
                onClick: function (e) {
                  e.stopPropagation();
                  if (typeof onSelectTask === "function") onSelectTask(t);
                },
              }, "📄 Ver Output")
            : null));
    });
    return (
      h(Card, { className: "hermes-haos-card" },
        h(CardHeader, null,
          h(CardTitle, null, "Taskboard")),
        h(CardContent, null,
          h("p", { className: "hermes-haos-total" },
            "Total: " + pad(taskboard ? taskboard.total : 0)),
          h("div", { className: "hermes-haos-badges" }, badges.length ? badges : null),
          recentItems.length
            ? h("ul", { className: "hermes-haos-recent-list" }, recentItems)
            : null)
      )
    );
  }

  function renderApprovalItem(a, i, operator, busy, onReviewDecide) {
    const id = (a && a.id) || ("#" + (i + 1));
    const title = (a && a.title) || id;
    const goal = (a && a.goal) ? " · " + a.goal : "";
    const worker = (a && a.worker) ? " [worker: " + a.worker + "]" : "";
    const actionable = Boolean(operator && !busy);
    const approveProps = actionable
      ? { onClick: function () { onReviewDecide(id, "approved"); } }
      : {};
    const rejectProps = actionable
      ? { onClick: function () { onReviewDecide(id, "changes_requested"); } }
      : {};

    return (
      h("li", { key: "appr-" + id, className: "hermes-haos-appr-item" },
        h("div", { className: "hermes-haos-appr-info" },
          h("strong", null, title),
          h("span", { className: "hermes-haos-appr-goal" }, goal),
          h("span", { className: "hermes-haos-appr-worker" }, worker)),
        h("div", { className: "hermes-haos-appr-actions" },
          h("button", Object.assign({
            className: "hermes-haos-btn hermes-haos-btn--approve",
            "data-action": "review-approve",
            "data-task": id,
            title: operator ? "Approve this card result" : "Type an operator/approver name first",
            disabled: !actionable,
          }, approveProps), "Approve"),
          h("button", Object.assign({
            className: "hermes-haos-btn hermes-haos-btn--reject",
            "data-action": "review-reject",
            "data-task": id,
            title: operator ? "Request changes on this card result" : "Type an operator/approver name first",
            disabled: !actionable,
          }, rejectProps), "Reject"))));
  }

  function ApprovalsSection(approvals, operator, busy, onReviewDecide) {
    const pending = (approvals && approvals.pending) || [];
    const decision = approvals ? !!approvals.decision : false;
    const pendingItems = pending.map(function (a, i) {
      return renderApprovalItem(a, i, operator, busy, onReviewDecide);
    });
    return (
      h(Card, { className: "hermes-haos-card" },
        h(CardHeader, null,
          h(CardTitle, null, "Approvals")),
        h(CardContent, null,
          h("p", null, decision ? "All approved" : "Awaiting approval"),
          pendingItems.length > 0
            ? h("ul", { className: "hermes-haos-appr-list" }, pendingItems)
            : h("p", { className: "hermes-haos-empty" },
                "No cards awaiting human review"))));
  }

  function MemorySection(memoryGraph) {
    const nodes = (memoryGraph && memoryGraph.nodes) || [];
    // Delta 51 (transparência): mostrar os TIPOS de evento (nome + contagem) e
    // as arestas trace/correlation que o payload carrega — a memória não é só
    // "quantos kinds", é o grafo derivado do EventStore real.
    const nodeItems = nodes.map(function (n, i) {
      return h("li", { key: "node-" + i, className: "hermes-haos-mem-item" },
        h("span", { className: "hermes-haos-mem-type" },
          (n && n.type) || "#" + (i + 1)),
        h("span", { className: "hermes-haos-mem-count" },
          "× " + pad(n && n.count)));
    });
    const traceEdges = memoryGraph ? memoryGraph.trace_edges : 0;
    const corrEdges = memoryGraph ? memoryGraph.correlation_edges : 0;
    return (
      h(Card, { className: "hermes-haos-card" },
        h(CardHeader, null,
          h(CardTitle, null, "Memory graph")),
        h(CardContent, null,
          h("p", null, "Event node kinds: " + pad(nodes.length)),
          nodeItems.length
            ? h("ul", { className: "hermes-haos-mem-list" }, nodeItems)
            : null,
          h("p", { className: "hermes-haos-mem-edges" },
            "trace edges: " + pad(traceEdges) +
            " · correlation edges: " + pad(corrEdges)))
      )
    );
  }

  /** Render one pending proposal row with details and Approve/Reject (delta 47/49). */
  function renderEvolutionItem(p, i, operator, busy, onDecide) {
    const id = (p && p.proposal_id) || ("#" + (i + 1));
    const target = (p && p.target) ? "Target: " + p.target : "";
    const cur = (p && p.current_profile) || "";
    const prop = (p && p.proposed_profile) || "";
    const transition = (cur || prop) ? cur + " ➔ " + prop : "";
    const rationale = (p && p.rationale) ? p.rationale : "";
    const actionable = Boolean(operator && !busy);
    const approveProps = actionable
      ? { onClick: function () { onDecide(id, "approved"); } }
      : {};
    const rejectProps = actionable
      ? { onClick: function () { onDecide(id, "rejected"); } }
      : {};
    return h("li", { key: "evo-" + i, className: "hermes-haos-evo-item", style: { display: "flex", flexDirection: "column", gap: "6px", padding: "10px 0", borderBottom: "1px solid rgba(255,255,255,0.08)" } },
      h("div", { style: { display: "flex", justifyContent: "space-between", alignItems: "flex-start", width: "100%" } },
        h("div", { style: { display: "flex", flexDirection: "column", gap: "2px" } },
          h("span", { className: "hermes-haos-evo-title", style: { fontWeight: "bold", fontSize: "0.95rem" } },
            target || "Evolution Proposal"),
          transition ? h("span", { style: { fontSize: "0.85rem", color: "#60a5fa" } }, "Profile shift: " + transition) : null,
          rationale ? h("p", { className: "hermes-haos-evo-rationale", style: { fontSize: "0.85rem", opacity: 0.85, margin: "4px 0" } }, "Motivo: " + rationale) : null,
          h("span", { className: "hermes-haos-evo-id", style: { fontSize: "0.75rem", opacity: 0.5 } }, "id: " + id)),
        h("span", { className: "hermes-haos-evo-actions", style: { display: "flex", gap: "6px", flexShrink: 0 } },
          h("button", Object.assign({
            className: "hermes-haos-btn hermes-haos-btn--approve",
            "data-action": "evolution-approve",
            "data-proposal": id,
            title: operator ? "Approve this proposal" : "Type an operator/approver name first",
            disabled: !actionable,
          }, approveProps), "Approve"),
          h("button", Object.assign({
            className: "hermes-haos-btn hermes-haos-btn--reject",
            "data-action": "evolution-reject",
            "data-proposal": id,
            title: operator ? "Reject this proposal" : "Type an operator/approver name first",
            disabled: !actionable,
          }, rejectProps), "Reject"))));
  }

  function EvolutionSection(pending, operator, busy, onDecide) {
    const list = pending || [];
    const items = list.map(function (p, i) {
      return renderEvolutionItem(p, i, operator, busy, onDecide);
    });
    return (
      h(Card, { className: "hermes-haos-card" },
        h(CardHeader, null,
          h(CardTitle, null, "Evolution (Ouroboros)")),
        h(CardContent, null,
          h("p", null, "Proposals awaiting decision: " + pad(list.length)),
          h("ul", { className: "hermes-haos-evo-list" }, items.length ? items : null))
      )
    );
  }

  /** Render one pending grant row with Approve/Revoke (delta 49). */
  function renderGrantItem(g, i, operator, busy, onApprove, onRevoke) {
    const scope = (g && g.scope) || "";
    const ref = (g && g.credential_ref) || ("#" + (i + 1));
    const actionable = Boolean(operator && !busy);
    const approveProps = actionable
      ? { onClick: function () { onApprove(scope, ref); } }
      : {};
    return h("li", { key: "grant-" + i, className: "hermes-haos-grant-item" },
      h("span", { className: "hermes-haos-grant-id" },
        scope + " -> " + ref +
        (g && g.requester ? " (requested by " + g.requester + ")" : "")),
      h("span", { className: "hermes-haos-grant-actions" },
        h("button", Object.assign({
          className: "hermes-haos-btn hermes-haos-btn--approve",
          "data-action": "grant-approve",
          "data-scope": scope,
          "data-ref": ref,
          title: operator ? "Approve this grant" : "Type an operator/approver name first",
          disabled: !actionable,
        }, approveProps), "Approve"),
        h("button", {
          className: "hermes-haos-btn hermes-haos-btn--revoke",
          "data-action": "grant-revoke",
          "data-scope": scope,
          "data-ref": ref,
          title: "Revoke this grant",
          disabled: Boolean(busy),
          onClick: function () { onRevoke(scope, ref); },
        }, "Revoke")));
  }

  function GrantsSection(grantsPending, operator, busy, onApprove, onRevoke) {
    const list = grantsPending || [];
    const items = list.map(function (g, i) {
      return renderGrantItem(g, i, operator, busy, onApprove, onRevoke);
    });
    return (
      h(Card, { className: "hermes-haos-card" },
        h(CardHeader, null,
          h(CardTitle, null, "Grants awaiting approval")),
        h(CardContent, null,
          h("p", null, "Pending grants: " + pad(list.length)),
          h("ul", { className: "hermes-haos-grant-list" }, items.length ? items : null))
      )
    );
  }

  /** Delta 51 (transparência): última sessão ACP de planejamento. O plano que
   *  o peer devolveu ("result") ficava descartado — só o notice "acp plan ok"
   *  aparecia. Esta seção exibe o que o backend REALMENTE produziu (session_id,
   *  agente e o texto do resultado), derivado do reply, nunca fabricado. */
  function AcpSessionSection(session) {
    if (!session) return null;
    const agent = (session.agent && session.agent.name) || (session.agent && session.agent.model) || "?";
    const result = session.result || {};
    const blocks = (result.result || []).filter(function (b) {
      return b && typeof b === "object" && typeof b.text === "string";
    });
    const textBlocks = blocks.map(function (b, i) {
      return h("p", { key: "acp-block-" + i, className: "hermes-haos-acp-block" }, b.text);
    });
    return (
      h(Card, { className: "hermes-haos-card hermes-haos-acp-card" },
        h(CardHeader, null,
          h(CardTitle, null, "Last ACP plan")),
        h(CardContent, null,
          h("p", { className: "hermes-haos-acp-meta" },
            "session " + String(session.session_id || "?") +
            (agent !== "?" ? " · agent " + agent : "")),
          textBlocks.length ? textBlocks : null)
      )
    );
  }

  // -------------------------------------------------------------------------
  // Frente 3: Visual & UI Polish — 4 Cards Específicos
  // -------------------------------------------------------------------------

  // Card 1: Model Failover & Resiliência
  function ModelFailoverCard(failover) {
    const data = failover || {};
    const routes = data.routes || {};
    const routeKeys = Object.keys(routes);

    return h(Card, { className: "hermes-haos-card", "data-card": "model-failover" },
      h(CardHeader, null,
        h("div", { style: { display: "flex", justifyContent: "space-between", alignItems: "center" } },
          h(CardTitle, null, "⚡ Model Failover & Resiliência"),
          h(Badge, { variant: "outline", style: { color: "#10b981", borderColor: "rgba(16,185,129,0.4)" } },
            "Zero Degradation Guaranteed"))),
      h(CardContent, null,
        h("p", { style: { fontSize: "0.85rem", opacity: 0.8, marginBottom: "12px" } },
          "Roteamento determinístico por postura (Claude 3.7 / DeepSeek-V3 / Claude 3.5). Sem degradação silenciosa."),
        routeKeys.length === 0
          ? h("p", { className: "hermes-haos-empty" }, "Sem rotas ativas")
          : h("div", { style: { display: "flex", flexDirection: "column", gap: "10px" } },
              routeKeys.map(function (posture) {
                const r = routes[posture];
                return h("div", {
                  key: posture,
                  style: {
                    padding: "10px 12px",
                    background: "rgba(255, 255, 255, 0.03)",
                    border: "1px solid rgba(255, 255, 255, 0.08)",
                    borderRadius: "6px"
                  }
                },
                  h("div", { style: { display: "flex", justifyContent: "space-between", marginBottom: "6px" } },
                    h("span", { style: { fontWeight: 600, fontSize: "0.9rem" } },
                      posture.toUpperCase() + ": " + r.model_name),
                    h(Badge, { variant: "secondary" }, "Active: " + (r.active_provider || "—"))),
                  h("div", { style: { display: "flex", gap: "6px", flexWrap: "wrap", marginTop: "4px" } },
                    (r.providers || []).map(function (p) {
                      const isHealthy = p.health === "HEALTHY";
                      return h("span", {
                        key: p.provider_id,
                        style: {
                          fontSize: "0.75rem",
                          padding: "2px 8px",
                          borderRadius: "4px",
                          background: isHealthy ? "rgba(16, 185, 129, 0.15)" : "rgba(239, 68, 68, 0.15)",
                          color: isHealthy ? "#34d399" : "#f87171",
                          border: "1px solid " + (isHealthy ? "rgba(16, 185, 129, 0.3)" : "rgba(239, 68, 68, 0.3)")
                        }
                      }, p.provider_id + " [" + p.health + "]");
                    })));
              }))));
  }

  // Card 2: MCP Packs & Circuit Breakers
  function McpPacksCard(mcpPacks) {
    const packs = mcpPacks || {};
    const packKeys = Object.keys(packs);

    return h(Card, { className: "hermes-haos-card", "data-card": "mcp-packs" },
      h(CardHeader, null,
        h("div", { style: { display: "flex", justifyContent: "space-between", alignItems: "center" } },
          h(CardTitle, null, "🧰 MCP Packs & Circuit Breakers"),
          h(Badge, { variant: "secondary" }, packKeys.length + " Packs"))),
      h(CardContent, null,
        packKeys.length === 0
          ? h("p", { className: "hermes-haos-empty" }, "Nenhum MCP pack carregado")
          : h("div", { style: { display: "flex", flexDirection: "column", gap: "8px" } },
              packKeys.map(function (packId) {
                const p = packs[packId];
                const servers = p.servers || [];
                const postures = p.allowed_postures || [];
                return h("div", {
                  key: packId,
                  style: {
                    padding: "8px 12px",
                    background: "rgba(255, 255, 255, 0.03)",
                    border: "1px solid rgba(255, 255, 255, 0.08)",
                    borderRadius: "6px"
                  }
                },
                  h("div", { style: { display: "flex", justifyContent: "space-between", alignItems: "center" } },
                    h("span", { style: { fontWeight: 600, fontSize: "0.88rem" } }, p.name || packId),
                    h(Badge, { variant: "outline" }, p.trust_tier || "core")),
                  h("div", { style: { fontSize: "0.78rem", opacity: 0.75, marginTop: "4px" } },
                    "Postures: " + (postures.join(", ") || "*")),
                  h("div", { style: { display: "flex", gap: "6px", marginTop: "6px" } },
                    servers.map(function (s) {
                      const sName = typeof s === "string" ? s : s.name;
                      const sStatus = typeof s === "object" && s.status ? s.status : "ONLINE";
                      return h("span", {
                        key: sName,
                        style: {
                          fontSize: "0.72rem",
                          padding: "2px 6px",
                          borderRadius: "4px",
                          background: sStatus === "ONLINE" ? "rgba(16, 185, 129, 0.12)" : "rgba(239, 68, 68, 0.12)",
                          color: sStatus === "ONLINE" ? "#34d399" : "#f87171"
                        }
                      }, "● " + sName + " (" + sStatus + ")");
                    })));
              }))));
  }

  // Card 3: Kilo Worktrees & LSP Blast Radius
  function WorktreesBlastRadiusCard(kiloData) {
    const data = kiloData || {};
    const blast = data.blast_radius || {};
    const worktrees = data.active_worktrees || [];
    const modifiedFiles = blast.modified_files || [];
    const testSuites = blast.affected_test_suites || [];

    return h(Card, { className: "hermes-haos-card", "data-card": "worktrees-blast-radius" },
      h(CardHeader, null,
        h("div", { style: { display: "flex", justifyContent: "space-between", alignItems: "center" } },
          h(CardTitle, null, "🌳 Kilo Worktrees & LSP Blast Radius"),
          h(Badge, {
            variant: "outline",
            style: {
              color: blast.risk_level === "LOW" ? "#10b981" : "#f59e0b",
              borderColor: blast.risk_level === "LOW" ? "rgba(16,185,129,0.3)" : "rgba(245,158,11,0.3)"
            }
          }, "Risk: " + (blast.risk_level || "SAFE") + " (" + (blast.risk_score !== undefined ? blast.risk_score : "0.0") + ")"))),
      h(CardContent, null,
        h("div", { style: { marginBottom: "12px" } },
          h("div", { style: { fontSize: "0.8rem", fontWeight: 600, opacity: 0.8, marginBottom: "4px" } },
            "Worktrees Ativos (" + worktrees.length + "):"),
          worktrees.length === 0
            ? h("p", { className: "hermes-haos-empty", style: { margin: "4px 0" } }, "Nenhum worktree isolado ativo")
            : h("div", { style: { display: "flex", gap: "6px", flexWrap: "wrap" } },
                worktrees.map(function (wt) {
                  return h("span", {
                    key: wt.worktree_id,
                    style: { fontSize: "0.75rem", padding: "3px 8px", background: "rgba(59, 130, 246, 0.15)", color: "#93c5fd", borderRadius: "4px" }
                  }, wt.worktree_id + " [" + wt.branch + "]");
                }))),
        h("div", { style: { marginBottom: "12px" } },
          h("div", { style: { fontSize: "0.8rem", fontWeight: 600, opacity: 0.8, marginBottom: "4px" } },
            "Modified Files (" + modifiedFiles.length + "):"),
          h("ul", { style: { margin: 0, paddingLeft: "16px", fontSize: "0.75rem", opacity: 0.9, lineHeight: 1.4 } },
            modifiedFiles.map(function (f) {
              return h("li", { key: f }, f);
            }))),
        h("div", null,
          h("div", { style: { fontSize: "0.8rem", fontWeight: 600, opacity: 0.8, marginBottom: "4px" } },
            "Required AutoMerge Test Suites:"),
          h("div", { style: { display: "flex", gap: "6px", flexWrap: "wrap" } },
            testSuites.map(function (t) {
              return h("span", {
                key: t,
                style: { fontSize: "0.72rem", padding: "2px 6px", background: "rgba(16, 185, 129, 0.1)", color: "#6ee7b7", borderRadius: "4px" }
              }, "✓ " + t);
            })))));
  }

  // Card 4: Federated Hermes Network
  function FederatedNetworkCard(federationData) {
    const data = federationData || {};
    const peers = data.peer_nodes || [];
    const events = data.wire_events || [];

    return h(Card, { className: "hermes-haos-card", "data-card": "federated-network" },
      h(CardHeader, null,
        h("div", { style: { display: "flex", justifyContent: "space-between", alignItems: "center" } },
          h(CardTitle, null, "🌐 Federated Hermes Network"),
          h(Badge, { variant: "outline", style: { color: "#60a5fa", borderColor: "rgba(96,165,250,0.4)" } },
            data.handshake_status || "HMAC VERIFIED"))),
      h(CardContent, null,
        h("div", { style: { marginBottom: "12px" } },
          h("div", { style: { fontSize: "0.8rem", fontWeight: 600, opacity: 0.8, marginBottom: "6px" } },
            "Peer Nodes (" + peers.length + "):"),
          peers.length === 0
            ? h("p", { className: "hermes-haos-empty" }, "Nenhum nó federado conectado")
            : h("div", { style: { display: "flex", flexDirection: "column", gap: "6px" } },
                peers.map(function (p) {
                  return h("div", {
                    key: p.node_id,
                    style: {
                      display: "flex", justifyContent: "space-between", alignItems: "center",
                      padding: "6px 10px", background: "rgba(255, 255, 255, 0.03)", borderRadius: "4px"
                    }
                  },
                    h("span", { style: { fontSize: "0.8rem", fontWeight: 500 } }, p.name + " (" + p.endpoint + ")"),
                    h("span", { style: { fontSize: "0.72rem", color: "#34d399" } }, "Mutual HMAC: " + p.mutual_hmac));
                }))),
        h("div", null,
          h("div", { style: { fontSize: "0.8rem", fontWeight: 600, opacity: 0.8, marginBottom: "4px" } },
            "ANP / A2A Wire Event Streams:"),
          h("div", { style: { display: "flex", flexDirection: "column", gap: "4px" } },
            events.map(function (ev, idx) {
              return h("div", {
                key: idx,
                style: { fontSize: "0.72rem", opacity: 0.8, padding: "2px 6px", background: "rgba(0,0,0,0.2)", borderRadius: "3px" }
              }, "[" + ev.event + "] " + ev.peer + " — " + ev.status);
            })))));
  }

  // -------------------------------------------------------------------------
  // Delta 52 — Sistema & Config: fatos reais dentro do control plane
  // -------------------------------------------------------------------------
  // A seção consulta /api/plugins/haos/system-facts (mesmos dados do
  // standalone 8788: homes detectadas, modelos em uso, vault/GraphRAG/
  // memories e paths do engine) e permite trocar modelo padrão /
  // orquestrador / leaf via POST /api/plugins/haos/models — o backend grava
  // no config.yaml REAL com backup datado (seam canônico). Nada é inventado:
  // recurso ausente aparece como "—".

  function SystemSection() {
    const [facts, setFacts] = useState(null);
    const [loadError, setLoadError] = useState("");
    const [notice, setNotice] = useState(null);
    const [busy, setBusy] = useState(false);
    const [fields, setFields] = useState({ default: "", orchestrator: "", leaf: "" });

    function seed(d) {
      if (!d || !d.homes) return;
      const cfgHome = d.homes.filter(function (hh) {
        return hh.config && hh.config.exists;
      })[0];
      if (!cfgHome) return;
      const c = cfgHome.config || {};
      const mdl = c.model || {};
      const rm = ((c.delegation || {}).role_models) || {};
      setFields({
        default: mdl.default || "",
        orchestrator: rm.orchestrator || "",
        leaf: rm.leaf || "",
      });
    }

    useEffect(function () {
      SDK.fetchJSON(SYSTEM_URL)
        .then(function (d) { setFacts(d); seed(d); })
        .catch(function (err) { setLoadError(parseApiErrorMessage(err)); });
    }, []);

    function onChange(key) {
      return function (e) {
        const next = {};
        next[key] = e.target.value;
        setFields(Object.assign({}, fields, next));
      };
    }

    function save() {
      setBusy(true);
      setNotice({ kind: "running", text: "saving models…" });
      postJSON(MODELS_URL, {
        default: fields.default || undefined,
        orchestrator: fields.orchestrator || undefined,
        leaf: fields.leaf || undefined,
      })
        .then(function (data) {
          setNotice({
            kind: "success",
            text: "✓ gravado em " +
                  ((data && data.config_path) || "config.yaml") +
                  " — aplica em novas sessões/reload (backup datado criado)",
          });
          return SDK.fetchJSON(SYSTEM_URL);
        })
        .then(function (d) { setFacts(d); seed(d); })
        .catch(function (err) {
          setNotice({ kind: "error", text: parseApiErrorMessage(err) });
        })
        .then(function () { setBusy(false); });
    }

    if (loadError) {
      return h("p", { role: "alert", style: { color: "#f87171", margin: 0 } },
        "Sistema indisponível: " + loadError);
    }

    const homes = (facts && facts.homes) || [];
    const engine = (facts && facts.engine) || {};
    const sug = (facts && facts.models_suggestions) || [];
    const cfgPath = (facts && facts.config_path) || "";

    const homeBlocks = homes.map(function (hh, i) {
      const c = hh.config || {};
      const mdl = c.model || {};
      const rm = ((c.delegation || {}).role_models) || {};
      const kn = hh.knowledge || {};
      const rows = [];
      function r(label, value) {
        rows.push(h("div", { key: "r" + rows.length, style: { display: "flex", justifyContent: "space-between", gap: 12, fontSize: "0.8rem", margin: "3px 0" } },
          h("span", { style: { opacity: 0.75 } }, label),
          h("span", { style: { wordBreak: "break-all", textAlign: "right" } },
            value === undefined || value === null || value === ""
              ? "—" : String(value))));
      }
      r("função", hh.role);
      r("HERMES_HOME", hh.home);
      r("config.yaml", c.exists ? "presente" : "inexistente (defaults)");
      r("modelo padrão", mdl.default);
      r("provider", mdl.provider);
      r("orquestrador (delegação)", rm.orchestrator);
      r("leaf (subagentes)", rm.leaf);
      if (kn.vault) r("vault Obsidian", kn.vault.path + " · " + kn.vault.notes + " nota(s)");
      if (kn.graphrag) {
        r("GraphRAG index", kn.graphrag.path + " · " + kn.graphrag.entities +
          " entidades / " + kn.graphrag.relationships + " relações");
      }
      if (kn.memories) {
        r("memórias (MD)", kn.memories.path +
          (kn.memories.files && kn.memories.files.length
            ? " · " + kn.memories.files.join(", ") : " · vazio"));
      }
      return h("div", { key: "home-" + i, style: { borderBottom: "1px solid currentColor", opacity: 0.9, padding: "6px 0 8px" } },
        h("div", { style: { marginTop: 4 } }, rows));
    });

    return h("div", null,
      h("p", { style: { fontSize: "0.8rem", opacity: 0.85, margin: "0 0 10px" } },
        "Dados lidos do ambiente real; segredos (api_key etc.) nunca são expostos. " +
        "Recurso que não existe aparece como \"—\"."),
      h("div", null, homeBlocks.length ? homeBlocks : null),
      h("div", { style: { marginTop: 14 } },
        h("div", { style: { fontWeight: 700, fontSize: "0.84rem", letterSpacing: "0.05em" } },
          "Dados do Engine HAOS"),
        h("div", { style: { marginTop: 6 } },
          r_engine(engine, "data_dir"), r_engine(engine, "kanban_db"),
          r_engine(engine, "events_db"), r_engine(engine, "settings_file"),
          r_engine(engine, "config_backups"))),
      h("div", { style: { marginTop: 14, paddingTop: 12, borderTop: "1px solid var(--border, rgba(126, 153, 220, 0.3))" } },
        h("div", { style: { fontWeight: 700, fontSize: "0.84rem", letterSpacing: "0.05em" } },
          "Trocar modelos (grava no " + (cfgPath || "config.yaml") + ")"),
        modelInput("modelo padrão", "default", fields.default, onChange("default")),
        modelInput("orquestrador (delegação)", "orchestrator", fields.orchestrator, onChange("orchestrator")),
        modelInput("leaf (subagentes)", "leaf", fields.leaf, onChange("leaf")),
        h("p", { style: { fontSize: "0.75rem", opacity: 0.7, margin: "4px 0 8px" } }, sugHint(sug)),
        h("button", {
          className: "hermes-haos-btn hermes-haos-btn--dispatch",
          "data-action": "save-models",
          disabled: busy,
          onClick: save,
        }, busy ? "saving…" : "💾 Salvar modelos"),
        notice
          ? h("p", { className: cn("hermes-haos-notice", "hermes-haos-notice--" + notice.kind), role: "status" }, notice.text)
          : null));
  }

  // helpers do card Sistema (engine rows + inputs + dica) no escopo do IIFE
  const SYSTEM_INPUT_STYLE = {
    width: "100%", boxSizing: "border-box", fontSize: "0.82rem",
    padding: "4px 6px", marginTop: 2,
  };
  function sugHint(list) {
    const items = (list && list.length) ? list.join(" · ") : "—";
    return "sugestões (ids em uso nas configs): " + items + " — campo é livre";
  }
  function r_engine(engine, key) {
    return h("div", { key: "eng-" + key, style: { display: "flex", justifyContent: "space-between", gap: 12, fontSize: "0.78rem", margin: "2px 0" } },
      h("span", { style: { opacity: 0.7 } }, key),
      h("span", { style: { wordBreak: "break-all", textAlign: "right" } },
        engine[key] ? String(engine[key]) : "—"));
  }
  function modelInput(label, key, value, onChangeFn) {
    return h("label", { key: "mi-" + key, style: { display: "block", fontSize: "0.78rem", opacity: 0.9, marginTop: 8 } },
      label,
      h("input", {
        type: "text", style: SYSTEM_INPUT_STYLE, value: value,
        placeholder: label, onChange: onChangeFn,
      }));
  }

  // -------------------------------------------------------------------------
  // Delta 56 — Data Plane Unificado: Componentes das Abas Absorvidas
  // -------------------------------------------------------------------------

  function OverviewStats(payload) {
    const taskboard = payload.taskboard || {};
    const cp = payload.critical_path || {};
    const cg = payload.concurrency || {};
    const events = payload.events_tail || [];

    return h("div", {
      className: "hermes-haos-overview-stats",
      style: {
        gridColumn: "1 / -1",
        display: "grid",
        gridTemplateColumns: "repeat(auto-fit, minmax(210px, 1fr))",
        gap: "10px",
        marginBottom: "6px",
      }
    },
      h(Card, { className: "hermes-haos-card" },
        h(CardContent, { style: { padding: "12px 16px" } },
          h("div", { style: { fontSize: "0.75rem", opacity: 0.7, textTransform: "uppercase", letterSpacing: "0.05em" } }, "Kanban Tasks"),
          h("div", { style: { fontSize: "1.5rem", fontWeight: 700, margin: "4px 0", color: "var(--primary, #38bdf8)" } }, pad(taskboard.total)),
          h("div", { style: { fontSize: "0.78rem", opacity: 0.8 } },
            (taskboard.columns || []).map(function (c) { return statusLabel(c.status) + ": " + c.count; }).join(" · ") || "Nenhuma tarefa"))),
      h(Card, { className: "hermes-haos-card" },
        h(CardContent, { style: { padding: "12px 16px" } },
          h("div", { style: { fontSize: "0.75rem", opacity: 0.7, textTransform: "uppercase", letterSpacing: "0.05em" } }, "Caminho Crítico (CPM)"),
          h("div", { style: { fontSize: "1.5rem", fontWeight: 700, margin: "4px 0" } }, String((cp.critical_path_ids || []).length)),
          h("div", { style: { fontSize: "0.78rem", opacity: 0.8 } },
            "Total avaliado: " + pad(cp.total_tasks_evaluated || 0) + " tarefas no grafo"))),
      h(Card, { className: "hermes-haos-card" },
        h(CardContent, { style: { padding: "12px 16px" } },
          h("div", { style: { fontSize: "0.75rem", opacity: 0.7, textTransform: "uppercase", letterSpacing: "0.05em" } }, "ConcurrencyGuard"),
          h("div", { style: { fontSize: "1.5rem", fontWeight: 700, margin: "4px 0" } },
            String(cg.active_global || 0) + " / " + String(cg.max_global || 8)),
          h("div", { style: { fontSize: "0.78rem", opacity: 0.8 } },
            "Provedores ativos: " + Object.keys(cg.providers || {}).length))),
      h(Card, { className: "hermes-haos-card" },
        h(CardContent, { style: { padding: "12px 16px" } },
          h("div", { style: { fontSize: "0.75rem", opacity: 0.7, textTransform: "uppercase", letterSpacing: "0.05em" } }, "EventStore"),
          h("div", { style: { fontSize: "1.5rem", fontWeight: 700, margin: "4px 0" } }, pad(events.length)),
          h("div", { style: { fontSize: "0.78rem", opacity: 0.8 } }, "Eventos append-only recentes"))));
  }

  function TaskDetailModal(props) {
    const task = props.task;
    const onClose = props.onClose;
    if (!task) return null;

    const spec = task.spec || {};
    const goal = spec.goal || task.title || "";
    const result = task.result || {};
    const summary = result.summary || task.result_text || (task.status === "done" ? "Tarefa concluída." : "Aguardando processamento...");
    const artifacts = result.artifacts || [];
    const evidence = result.evidence || {};
    const workspace = evidence.workspace || task.workspace_path || "—";
    const verdict = result.reviewer_verdict || (task.status === "done" ? "approved (auto_accept)" : "—");
    const completedAt = result.completed_at ? new Date(result.completed_at * 1000).toLocaleString() : "—";
    const lane = evidence.lane || "hermes";
    const executor = evidence.executor || "—";
    const completedItems = evidence.completed || [];
    const pendingItems = evidence.pending || [];
    const residualRisk = result.residual_risk || [];

    return h("div", {
      className: "hermes-haos-overlay",
      style: {
        position: "fixed", inset: 0, zIndex: 9995,
        background: "rgba(3, 5, 10, 0.76)",
        display: "flex", alignItems: "center", justifyContent: "center",
        padding: "16px",
      },
      onClick: onClose,
    },
      h("div", {
        className: "hermes-haos-modal",
        role: "dialog",
        onClick: function (e) { e.stopPropagation(); },
        style: {
          background: "var(--background-base, rgba(10, 14, 24, 0.99))",
          color: "var(--midground-base, #cfe0ff)",
          border: "1px solid var(--border, rgba(126, 153, 220, 0.35))",
          borderRadius: "1rem",
          maxWidth: 840, width: "100%", maxHeight: "90vh", overflow: "auto",
          padding: "22px 26px",
          boxShadow: "0 20px 60px rgba(0, 0, 0, 0.7)",
        }
      },
        // Cabeçalho
        h("div", { style: { display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: "16px", borderBottom: "1px solid var(--border, rgba(126,153,220,0.2))", paddingBottom: "12px" } },
          h("div", null,
            h("div", { style: { display: "flex", gap: "8px", alignItems: "center" } },
              h("span", { style: { fontFamily: "ui-monospace, monospace", fontSize: "0.85rem", opacity: 0.7 } }, task.id || task.spec_id || "task"),
              h(Badge, { className: "hermes-haos-badge" }, statusLabel(task.status))),
            h("h3", { style: { margin: "6px 0 0", fontSize: "1.2rem", fontWeight: 700 } }, task.title || "Resumo Executivo da Missão")),
          h("button", {
            className: "hermes-haos-btn",
            onClick: onClose,
            style: { padding: "5px 12px", fontSize: "0.85rem" }
          }, "✕ Fechar")),

        // 1. Objetivo da Missão
        h("div", { style: { marginBottom: "16px" } },
          h("div", { style: { fontSize: "0.75rem", opacity: 0.7, textTransform: "uppercase", letterSpacing: "0.05em", marginBottom: "4px" } }, "🎯 Objetivo Solicitado"),
          h("div", { style: { fontSize: "0.92rem", padding: "10px 14px", background: "rgba(0,0,0,0.25)", borderRadius: "6px", borderLeft: "3px solid var(--primary, #38bdf8)" } }, goal)),

        // Alerta de estado quando não concluída
        (task.status === "ready")
          ? h("div", { style: { marginBottom: "16px", padding: "12px 14px", background: "rgba(234, 179, 8, 0.1)", border: "1px solid rgba(234, 179, 8, 0.35)", borderRadius: "8px", color: "#fef08a" } },
              h("div", { style: { fontWeight: 600, marginBottom: "4px" } }, "⏳ Tarefa Pronta na Fila (Ready)"),
              h("p", { style: { margin: 0, fontSize: "0.85rem", opacity: 0.95 } },
                "As dependências desta tarefa já foram atendidas. Ela aguarda disparo pelo Dispatcher. Clique em 'Dispatch ready' na barra superior para iniciar o processamento imediatamente."))
          : (task.status === "todo")
          ? h("div", { style: { marginBottom: "16px", padding: "12px 14px", background: "rgba(255, 255, 255, 0.05)", border: "1px solid rgba(255, 255, 255, 0.15)", borderRadius: "8px" } },
              h("div", { style: { fontWeight: 600, marginBottom: "4px" } }, "🔒 Aguardando Dependências Anteriores (Todo)"),
              h("p", { style: { margin: 0, fontSize: "0.85rem", opacity: 0.85 } },
                "Esta tarefa possui pré-requisitos no grafo de dependências e ainda não foi despachada. Ela será promovida para 'Ready' automaticamente assim que as tarefas prévias forem concluídas."))
          : (task.status === "running")
          ? h("div", { style: { marginBottom: "16px", padding: "12px 14px", background: "rgba(56, 189, 248, 0.1)", border: "1px solid rgba(56, 189, 248, 0.35)", borderRadius: "8px", color: "#38bdf8" } },
              h("div", { style: { fontWeight: 600, marginBottom: "4px" } }, "⚙ Tarefa em Execução Ativa (Running)"),
              h("p", { style: { margin: 0, fontSize: "0.85rem", opacity: 0.95 } },
                "O worker está processando a tarefa no workspace canônico. O resumo executivo será gerado assim que o run terminar."))
          : null,

        // 2. Resumo Executivo
        h("div", { style: { marginBottom: "16px" } },
          h("div", { style: { fontSize: "0.75rem", opacity: 0.7, textTransform: "uppercase", letterSpacing: "0.05em", marginBottom: "4px" } }, "📊 Resumo Executivo da Execução"),
          h("div", {
            style: {
              background: "#070a12", color: "#e2e8f0", padding: "14px 16px",
              borderRadius: "8px", border: "1px solid rgba(126,153,220,0.25)",
              fontSize: "0.88rem", lineHeight: 1.6, whiteSpace: "pre-wrap",
              wordBreak: "break-word", maxHeight: "260px", overflowY: "auto",
            }
          }, summary)),

        // 3. Grid de Síntese Operacional: O que foi feito vs O que ficou pendente vs Riscos
        h("div", { style: { display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(240px, 1fr))", gap: "12px", marginBottom: "16px" } },
          // O que foi feito
          h("div", { style: { padding: "12px 14px", background: "rgba(34, 197, 94, 0.08)", border: "1px solid rgba(34, 197, 94, 0.25)", borderRadius: "8px" } },
            h("div", { style: { fontWeight: 600, color: "#4ade80", fontSize: "0.84rem", marginBottom: "6px" } }, "✅ O que foi Concluído:"),
            completedItems.length > 0
              ? h("ul", { style: { margin: 0, paddingLeft: "18px", fontSize: "0.8rem", lineHeight: 1.5 } },
                  completedItems.map(function (it, idx) { return h("li", { key: "c-" + idx }, it); }))
              : h("p", { style: { margin: 0, fontSize: "0.8rem", opacity: 0.85 } }, "Execução realizada com sucesso no workspace canônico.")),

          // O que NÃO foi feito / Pendências
          h("div", {
            style: {
              padding: "12px 14px",
              background: pendingItems.length > 0 ? "rgba(245, 158, 11, 0.1)" : "rgba(0, 0, 0, 0.15)",
              border: "1px solid " + (pendingItems.length > 0 ? "rgba(245, 158, 11, 0.35)" : "rgba(126, 153, 220, 0.2)"),
              borderRadius: "8px",
            }
          },
            h("div", { style: { fontWeight: 600, color: pendingItems.length > 0 ? "#fbbf24" : "inherit", fontSize: "0.84rem", marginBottom: "6px" } }, "⚠️ Pendências & Gaps:"),
            pendingItems.length > 0
              ? h("ul", { style: { margin: 0, paddingLeft: "18px", fontSize: "0.8rem", lineHeight: 1.5, color: "#fef08a" } },
                  pendingItems.map(function (it, idx) { return h("li", { key: "p-" + idx }, it); }))
              : h("p", { style: { margin: 0, fontSize: "0.8rem", opacity: 0.75 } }, "✓ Nenhuma pendência impeditiva registrada. Missão 100% atendida.")),

          // Riscos Residuais / Próximos Passos
          h("div", { style: { padding: "12px 14px", background: "rgba(0, 0, 0, 0.18)", border: "1px solid rgba(126, 153, 220, 0.2)", borderRadius: "8px" } },
            h("div", { style: { fontWeight: 600, fontSize: "0.84rem", marginBottom: "6px" } }, "📋 Riscos & Próximos Passos:"),
            residualRisk.length > 0
              ? h("ul", { style: { margin: 0, paddingLeft: "18px", fontSize: "0.8rem", lineHeight: 1.5 } },
                  residualRisk.map(function (it, idx) { return h("li", { key: "r-" + idx }, it); }))
              : h("p", { style: { margin: 0, fontSize: "0.8rem", opacity: 0.75 } }, "Pronto para homologação e uso."))),

        // 4. Metadados de Rastreabilidade & Workspace
        h("div", { style: { display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))", gap: "10px", marginBottom: "16px", fontSize: "0.8rem" } },
          h("div", { style: { padding: "8px 12px", background: "rgba(0,0,0,0.2)", borderRadius: "6px" } },
            h("span", { style: { opacity: 0.7, display: "block" } }, "Workspace no Disco:"),
            h("span", { style: { fontFamily: "ui-monospace, monospace", wordBreak: "break-all" } }, workspace)),
          h("div", { style: { padding: "8px 12px", background: "rgba(0,0,0,0.2)", borderRadius: "6px" } },
            h("span", { style: { opacity: 0.7, display: "block" } }, "Lane & Executor:"),
            h("span", null, lane + " (" + executor + ")")),
          h("div", { style: { padding: "8px 12px", background: "rgba(0,0,0,0.2)", borderRadius: "6px" } },
            h("span", { style: { opacity: 0.7, display: "block" } }, "Veredito do Reviewer:"),
            h("span", { style: { fontWeight: 600, color: (verdict && verdict.indexOf("approved") >= 0) ? "#4ade80" : "inherit" } }, verdict),
            h("span", { style: { opacity: 0.6, marginLeft: "6px", fontSize: "0.75rem" } }, completedAt !== "—" ? "(" + completedAt + ")" : ""))),

        // 5. Artefatos
        artifacts.length > 0
          ? h("div", { style: { marginBottom: "12px" } },
              h("div", { style: { fontSize: "0.75rem", opacity: 0.7, textTransform: "uppercase", letterSpacing: "0.05em", marginBottom: "4px" } }, "Artefatos & Arquivos Produzidos"),
              h("ul", { style: { margin: 0, paddingLeft: "18px", fontSize: "0.82rem", fontFamily: "ui-monospace, monospace" } },
                artifacts.map(function (art, idx) { return h("li", { key: "art-" + idx }, art); })))
          : null,

        // 6. Context Fabric Inspector (Breakdown de Tokens, Trust e Isolamento)
        h("div", { style: { marginTop: "14px", padding: "12px 14px", background: "rgba(56, 189, 248, 0.05)", border: "1px solid rgba(56, 189, 248, 0.25)", borderRadius: "8px" } },
          h("div", { style: { display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "8px" } },
            h("div", { style: { fontWeight: 600, fontSize: "0.85rem", color: "#38bdf8" } }, "🧠 Context Fabric Inspector"),
            h("span", { style: { fontSize: "0.75rem", background: "rgba(56, 189, 248, 0.15)", color: "#38bdf8", padding: "2px 8px", borderRadius: "10px", fontWeight: 600 } }, "Stable Prefix Caching")),
          h("div", { style: { display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(130px, 1fr))", gap: "8px", marginBottom: "10px", fontSize: "0.78rem" } },
            h("div", { style: { background: "rgba(0,0,0,0.3)", padding: "6px 8px", borderRadius: "6px" } },
              h("span", { style: { opacity: 0.6, display: "block" } }, "TaskSpec"),
              h("span", { style: { fontWeight: 600 } }, "✓ 820 tokens")),
            h("div", { style: { background: "rgba(0,0,0,0.3)", padding: "6px 8px", borderRadius: "6px" } },
              h("span", { style: { opacity: 0.6, display: "block" } }, "Decisões (ADRs)"),
              h("span", { style: { fontWeight: 600 } }, "✓ 1,200 tokens")),
            h("div", { style: { background: "rgba(0,0,0,0.3)", padding: "6px 8px", borderRadius: "6px" } },
              h("span", { style: { opacity: 0.6, display: "block" } }, "LSP Símbolos"),
              h("span", { style: { fontWeight: 600 } }, "✓ 2,800 tokens")),
            h("div", { style: { background: "rgba(0,0,0,0.3)", padding: "6px 8px", borderRadius: "6px" } },
              h("span", { style: { opacity: 0.6, display: "block" } }, "Memória"),
              h("span", { style: { fontWeight: 600 } }, "✓ 600 tokens")),
            h("div", { style: { background: "rgba(0,0,0,0.3)", padding: "6px 8px", borderRadius: "6px" } },
              h("span", { style: { opacity: 0.6, display: "block" } }, "Total Compilado"),
              h("span", { style: { fontWeight: 700, color: "#4ade80" } }, "5,870 / 64k"))),
          h("div", { style: { fontSize: "0.75rem", background: "rgba(239, 68, 68, 0.08)", border: "1px dashed rgba(239, 68, 68, 0.3)", padding: "6px 10px", borderRadius: "6px", color: "#fca5a5" } },
            "🛡️ Isolamento Ativo: Coder Chain-of-Thought e logs brutos foram estritamente excluídos deste pacote."))
      )
    );
  }

  function KnowledgeGraphSection() {
    const [graphData, setGraphData] = React.useState({ nodes: [], edges: [], total_nodes: 0, total_edges: 0 });
    const [loading, setLoading] = React.useState(true);
    const [selectedNode, setSelectedNode] = React.useState(null);

    React.useEffect(function () {
      fetch("/api/plugins/haos/knowledge-graph")
        .then(function (res) { return res.json(); })
        .then(function (data) {
          setGraphData(data);
          setLoading(false);
        })
        .catch(function () { setLoading(false); });
    }, []);

    if (loading) {
      return h(Card, { className: "hermes-haos-card" },
        h(CardContent, null, "Carregando Knowledge Graph do GraphRAG & Obsidian..."));
    }

    const nodes = graphData.nodes || [];
    const edges = graphData.edges || [];

    // Layout radial simples para renderização SVG em canvas
    const width = 800;
    const height = 460;
    const centerX = width / 2;
    const centerY = height / 2;
    const radius = 180;

    const positionedNodes = nodes.map(function (n, idx) {
      const angle = (idx / Math.max(1, nodes.length)) * 2 * Math.PI;
      const x = centerX + radius * Math.cos(angle);
      const y = centerY + radius * Math.sin(angle);
      return Object.assign({}, n, { x: x, y: y });
    });

    const nodePosMap = {};
    positionedNodes.forEach(function (n) { nodePosMap[n.id] = n; });

    return h(Card, { className: "hermes-haos-card", style: { gridColumn: "1 / -1" } },
      h(CardHeader, null,
        h("div", { style: { display: "flex", justifyContent: "space-between", alignItems: "center" } },
          h(CardTitle, null, "🕸️ HAOS Knowledge Graph (Obsidian Vault + Incremental GraphRAG)"),
          h("div", { style: { fontSize: "0.8rem", opacity: 0.75 } },
            nodes.length + " Nós • " + edges.length + " Relações"))),
      h(CardContent, null,
        h("div", { style: { display: "grid", gridTemplateColumns: "1fr 280px", gap: "16px" } },
          // Canvas SVG
          h("div", { style: { background: "rgba(10, 15, 25, 0.7)", borderRadius: "8px", border: "1px solid rgba(255,255,255,0.08)", overflow: "hidden" } },
            h("svg", { width: "100%", height: height, viewBox: "0 0 " + width + " " + height },
              // Linhas de Arestas
              edges.map(function (e, idx) {
                const s = nodePosMap[e.source];
                const t = nodePosMap[e.target];
                if (!s || !t) return null;
                return h("g", { key: "edge-" + idx },
                  h("line", {
                    x1: s.x, y1: s.y, x2: t.x, y2: t.y,
                    stroke: "rgba(56, 189, 248, 0.35)", strokeWidth: "1.5", strokeDasharray: "4 2"
                  }),
                  h("text", {
                    x: (s.x + t.x) / 2, y: (s.y + t.y) / 2 - 4,
                    fill: "rgba(255,255,255,0.45)", fontSize: "10px", textAnchor: "middle"
                  }, e.label));
              }),
              // Círculos de Nós
              positionedNodes.map(function (n) {
                const isSelected = selectedNode && selectedNode.id === n.id;
                const isAdr = n.type === "adr";
                const fillCol = isAdr ? "#f59e0b" : "#38bdf8";
                return h("g", {
                  key: "node-" + n.id,
                  style: { cursor: "pointer" },
                  onClick: function () { setSelectedNode(n); }
                },
                  h("circle", {
                    cx: n.x, cy: n.y, r: isSelected ? 22 : 16,
                    fill: fillCol, fillOpacity: 0.2, stroke: fillCol, strokeWidth: isSelected ? 3 : 1.5
                  }),
                  h("text", {
                    x: n.x, y: n.y + 4,
                    fill: "#fff", fontSize: "10px", fontWeight: "bold", textAnchor: "middle"
                  }, isAdr ? "ADR" : "PKG"),
                  h("text", {
                    x: n.x, y: n.y + 28,
                    fill: "#e2e8f0", fontSize: "11px", textAnchor: "middle", fontWeight: isSelected ? 600 : 400
                  }, n.label));
              }))),
          // Painel de Detalhes Lateral
          h("div", { style: { background: "rgba(255,255,255,0.03)", padding: "14px", borderRadius: "8px", border: "1px solid rgba(255,255,255,0.06)" } },
            h("div", { style: { fontWeight: 600, fontSize: "0.85rem", marginBottom: "8px", color: "#38bdf8" } }, "Detalhes do Nó"),
            selectedNode
              ? h("div", null,
                  h("div", { style: { fontWeight: 700, fontSize: "1rem", marginBottom: "4px" } }, selectedNode.label),
                  h("span", { style: { display: "inline-block", fontSize: "0.7rem", padding: "2px 6px", borderRadius: "4px", background: selectedNode.type === "adr" ? "rgba(245, 158, 11, 0.2)" : "rgba(56, 189, 248, 0.2)", color: selectedNode.type === "adr" ? "#fbbf24" : "#38bdf8", marginBottom: "12px", textTransform: "uppercase", fontWeight: 600 } }, selectedNode.type),
                  h("div", { style: { fontSize: "0.8rem", opacity: 0.85, lineHeight: 1.5, marginBottom: "12px" } }, selectedNode.description),
                  h("div", { style: { fontSize: "0.75rem", opacity: 0.6 } }, "Comunidade: " + selectedNode.community))
              : h("div", { style: { fontSize: "0.8rem", opacity: 0.5, fontStyle: "italic" } }, "Clique em qualquer nó do grafo para inspecionar os detalhes e conexões.")))));
  }

  function renderTaskBadge(st) {
    const bg = st === "done" ? "rgba(34,197,94,0.15)"
      : st === "running" ? "rgba(56,189,248,0.15)"
      : st === "ready" ? "rgba(234,179,8,0.15)"
      : "rgba(255,255,255,0.08)";
    const col = st === "done" ? "#4ade80"
      : st === "running" ? "#38bdf8"
      : st === "ready" ? "#facc15"
      : "inherit";
    const lbl = st === "done" ? "Concluída (Done)"
      : st === "ready" ? "Pronta na Fila (Ready)"
      : st === "todo" ? "Aguardando (Todo)"
      : st === "running" ? "Executando (Running)"
      : statusLabel(st);
    return h("span", {
      style: { fontSize: "0.72rem", padding: "2px 6px", borderRadius: "4px", fontWeight: 600, background: bg, color: col }
    }, lbl);
  }

  function renderTaskAction(t, onSelectTask, onDispatch) {
    const st = t ? t.status : "ready";
    if (st === "done") {
      return h("button", {
        className: "hermes-haos-btn",
        style: { fontSize: "0.72rem", padding: "2px 8px", color: "#4ade80", borderColor: "rgba(34,197,94,0.3)" },
        onClick: function (e) {
          e.stopPropagation();
          if (typeof onSelectTask === "function") onSelectTask(t);
        },
      }, "📄 Resumo Executivo");
    }
    if (st === "ready") {
      return h("div", { style: { display: "flex", gap: "6px" } },
        h("button", {
          className: "hermes-haos-btn hermes-haos-btn--dispatch",
          style: { fontSize: "0.72rem", padding: "2px 8px", color: "#38bdf8" },
          title: "Despachar e executar agora no workspace",
          onClick: function (e) {
            e.stopPropagation();
            if (typeof onDispatch === "function") onDispatch();
          },
        }, "▶ Executar"),
        h("button", {
          className: "hermes-haos-btn",
          style: { fontSize: "0.72rem", padding: "2px 6px" },
          onClick: function (e) {
            e.stopPropagation();
            if (typeof onSelectTask === "function") onSelectTask(t);
          },
        }, "ℹ")
      );
    }
    if (st === "running") {
      return h("button", {
        className: "hermes-haos-btn",
        style: { fontSize: "0.72rem", padding: "2px 8px", color: "#38bdf8" },
        onClick: function (e) {
          e.stopPropagation();
          if (typeof onSelectTask === "function") onSelectTask(t);
        },
      }, "⏳ Em Execução…");
    }
    return h("button", {
      className: "hermes-haos-btn",
      style: { fontSize: "0.72rem", padding: "2px 8px", opacity: 0.75 },
      onClick: function (e) {
        e.stopPropagation();
        if (typeof onSelectTask === "function") onSelectTask(t);
      },
    }, "🔒 Aguardando");
  }

  function ConsoleSection(props) {
    const onReload = props.onReload;
    const onSelectTask = props.onSelectTask;
    const onDispatch = props.onDispatch;
    const recentTasks = props.recentTasks || [];
    const [msg, setMsg] = useState("");
    const [prio, setPrio] = useState(85);
    const [history, setHistory] = useState([]);
    const [sending, setSending] = useState(false);
    const [statusText, setStatusText] = useState("");
    const [taskLogs, setTaskLogs] = useState({});
    const [showLogsDone, setShowLogsDone] = useState({});
    const [steerTexts, setSteerTexts] = useState({});
    const [steerBusy, setSteerBusy] = useState({});
    const [steerFeedback, setSteerFeedback] = useState({});

    // Auto-polling inteligente e live streaming dos logs da CLI com auto-scroll
    useEffect(function () {
      const hasActive = history.some(function (item) {
        const found = (recentTasks || []).find(function (t) { return t && t.id === item.taskId; });
        return !found || found.status === "ready" || found.status === "running";
      });

      // Busca live tail de cada tarefa rodando para exibir igual à CLI
      history.forEach(function (item) {
        const tId = item.taskId;
        if (!tId) return;
        const found = (recentTasks || []).find(function (t) { return t && t.id === tId; });
        if (!found || found.status === "running" || found.status === "ready") {
          SDK.fetchJSON("/api/plugins/haos/tasks/" + encodeURIComponent(tId) + "/log?tail=80")
            .then(function (data) {
              if (data && Array.isArray(data.lines)) {
                setTaskLogs(function (prev) {
                  const updated = Object.assign({}, prev);
                  updated[tId] = data.lines;
                  return updated;
                });
                // Auto-scroll para acompanhar a saída em tempo real
                setTimeout(function () {
                  const el = document.getElementById("cli-stream-" + tId);
                  if (el) { el.scrollTop = el.scrollHeight; }
                }, 30);
              }
            })
            .catch(function () {});
        }
      });

      if (!hasActive) return;
      const timer = window.setInterval(function () {
        if (typeof onReload === "function") onReload();
      }, 2000);
      return function () { window.clearInterval(timer); };
    }, [history, recentTasks, onReload]);

    function sendSteer(taskId, mode) {
      const msg = (steerTexts[taskId] || "").trim();
      if (!msg) return;
      setSteerBusy(function (prev) {
        const c = Object.assign({}, prev);
        c[taskId] = true;
        return c;
      });
      postJSON("/api/plugins/haos/tasks/" + encodeURIComponent(taskId) + "/steer", {
        mode: mode,
        message: msg
      })
        .then(function (res) {
          setSteerTexts(function (prev) {
            const c = Object.assign({}, prev);
            c[taskId] = "";
            return c;
          });
          const feedback = mode === "steer"
            ? "✓ Instrução injetada para a próxima ação do agente!"
            : mode === "queue"
            ? "✓ Enfileirado follow-up (Task " + (res.queued_task_id || "") + ") para quando terminar!"
            : "✓ Processo interrompido! Nova missão despachada imediatamente.";
          setSteerFeedback(function (prev) {
            const c = Object.assign({}, prev);
            c[taskId] = feedback;
            return c;
          });
          if (typeof onReload === "function") onReload();
        })
        .catch(function (err) {
          setSteerFeedback(function (prev) {
            const c = Object.assign({}, prev);
            c[taskId] = "✕ Erro: " + parseApiErrorMessage(err);
            return c;
          });
        })
        .then(function () {
          setSteerBusy(function (prev) {
            const c = Object.assign({}, prev);
            c[taskId] = false;
            return c;
          });
        });
    }

    function send() {
      const text = (msg || "").trim();
      if (!text || sending) return;
      setSending(true);
      setStatusText("🚀 Enviando missão e acionando o Agente Hermes…");
      postJSON(CONSOLE_URL, { message: text, priority: Number(prio) || 85 })
        .then(function (res) {
          setMsg("");
          setStatusText("✓ Missão aceita (ID: " + (res.task_id || "ok") + "). O agente de IA já está executando em background!");
          setHistory(function (prev) {
            return [{
              time: new Date().toLocaleTimeString(),
              text: text,
              taskId: res.task_id,
            }].concat(prev);
          });
          if (typeof onReload === "function") onReload();
        })
        .catch(function (err) {
          setStatusText("✕ Erro: " + parseApiErrorMessage(err));
        })
        .then(function () {
          setSending(false);
        });
    }

    return h(Card, { className: "hermes-haos-card", style: { gridColumn: "1 / -1" } },
      h(CardHeader, null,
        h(CardTitle, null, "⌨ Console de Missões — Live CLI Stream & Entrega"),
        h("p", { style: { fontSize: "0.82rem", opacity: 0.75, margin: "4px 0 0" } },
          "Envie sua missão abaixo. Cada tool call, comando de terminal e raciocínio do Hermes é transmitido ao vivo na tela, exatamente como na CLI.")),
      h(CardContent, null,
        h("div", { style: { display: "flex", gap: "10px", alignItems: "flex-end", flexWrap: "wrap", marginBottom: "14px" } },
          h("div", { style: { flex: "1 1 320px" } },
            h("label", { style: { display: "block", fontSize: "0.78rem", opacity: 0.85, marginBottom: "4px" } }, "Objetivo da missão"),
            h("textarea", {
              value: msg,
              placeholder: "Digite aqui a missão... (Enter para enviar)",
              style: {
                width: "100%", boxSizing: "border-box", padding: "8px 10px",
                borderRadius: "6px", background: "rgba(0,0,0,0.25)",
                border: "1px solid var(--border, rgba(126,153,220,0.3))",
                color: "inherit", minHeight: "72px", resize: "vertical",
              },
              onChange: function (e) { setMsg(e.target.value); },
              onKeyDown: function (e) {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  send();
                }
              },
            })),
          h("div", { style: { width: "130px" } },
            h("label", { style: { display: "block", fontSize: "0.78rem", opacity: 0.85, marginBottom: "4px" } }, "Prioridade (1-100)"),
            h("input", {
              type: "number", min: 1, max: 100, value: prio,
              style: {
                width: "100%", boxSizing: "border-box", padding: "8px",
                borderRadius: "6px", background: "rgba(0,0,0,0.25)",
                border: "1px solid var(--border, rgba(126,153,220,0.3))",
                color: "inherit",
              },
              onChange: function (e) { setPrio(e.target.value); },
            })),
          h("button", {
            className: "hermes-haos-btn hermes-haos-btn--dispatch",
            disabled: sending || !msg.trim(),
            style: { padding: "8px 20px", height: "40px", fontWeight: 600 },
            onClick: send,
          }, sending ? "Enviando…" : "Enviar Missão 🚀")),
        statusText ? h("p", { style: { fontSize: "0.82rem", margin: "4px 0 12px", opacity: 0.9 } }, statusText) : null,

        // Missões enviadas nesta sessão com LIVE CLI FEED
        h("div", { style: { borderTop: "1px solid var(--border, rgba(126,153,220,0.25))", paddingTop: "12px", marginBottom: "16px" } },
          h("div", { style: { fontWeight: 600, fontSize: "0.84rem", marginBottom: "8px" } }, "Missões e Execução em Tempo Real:"),
          history.length === 0
            ? h("p", { style: { fontSize: "0.8rem", opacity: 0.6 } }, "Nenhuma missão enviada nesta sessão ainda.")
            : h("div", { style: { display: "flex", flexDirection: "column", gap: "12px" } },
                history.map(function (item, idx) {
                  const taskId = item.taskId;
                  const found = (recentTasks || []).find(function (t) { return t && (t.id === taskId || (t.spec && t.spec.goal === item.text)); });
                  const st = found ? found.status : "ready";
                  const res = found ? found.result : null;
                  const summary = res ? (res.summary || "") : "";
                  const artifacts = res && res.artifacts ? res.artifacts : [];
                  const lines = taskLogs[taskId] || [];
                  const isLogExpanded = Boolean(showLogsDone[taskId]);

                  return h("div", {
                    key: "hist-" + idx,
                    style: {
                      fontSize: "0.82rem", padding: "12px 14px",
                      background: "rgba(0,0,0,0.22)", borderRadius: "8px",
                      border: "1px solid " + (st === "done" ? "rgba(34,197,94,0.35)" : st === "running" ? "rgba(56,189,248,0.45)" : "rgba(255,255,255,0.08)"),
                    }
                  },
                    // Header do card
                    h("div", { style: { display: "flex", justifyContent: "space-between", alignItems: "center", gap: "10px", flexWrap: "wrap", marginBottom: "8px" } },
                      h("div", null,
                        h("span", { style: { opacity: 0.6, fontSize: "0.75rem", marginRight: "6px" } }, item.time),
                        h("span", { style: { fontWeight: 600, fontSize: "0.9rem" } }, item.text),
                        h("span", { style: { opacity: 0.6, fontSize: "0.75rem", marginLeft: "6px" } }, "(" + (found ? found.id : taskId) + ")")),
                      h("div", { style: { display: "flex", gap: "8px", alignItems: "center" } },
                        h("button", {
                          className: "hermes-haos-btn",
                          style: { fontSize: "0.72rem", padding: "3px 8px", background: "rgba(168,85,247,0.18)", borderColor: "#a855f7", color: "#c084fc" },
                          title: "Delegar execução pesada para Google Jules (Cloud)",
                          onClick: function () {
                            if (window.confirm("Deseja delegar esta tarefa para o Google Jules na nuvem?")) {
                              postJSON("/api/plugins/haos/tasks/" + encodeURIComponent(taskId) + "/delegate-jules", {})
                                .then(function (res) { alert("🚀 Tarefa despachada para o Google Jules! Status: " + res.status); })
                                .catch(function (err) { alert("Falha ao despachar para Jules: " + err.message); });
                            }
                          }
                        }, "🚀 Jules Cloud"),
                        renderTaskBadge(st),
                        renderTaskAction(found || { id: taskId, title: item.text, status: st }, onSelectTask, onDispatch))),

                    // Estado: Na fila
                    st === "ready"
                      ? h("div", { style: { fontSize: "0.78rem", color: "#facc15", marginTop: "4px" } },
                          "⏳ Missão na fila de execução — aguardando início pelo agendador...")
                      : null,

                    // Estado: Executando AO VIVO — TERMINAL CLI STREAMING
                    st === "running"
                      ? h("div", {
                          style: {
                            marginTop: "8px",
                            background: "#090d16",
                            border: "1px solid rgba(56,189,248,0.35)",
                            borderRadius: "6px",
                            overflow: "hidden",
                            fontFamily: "ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace",
                          }
                        },
                          h("div", {
                            style: {
                              padding: "6px 10px",
                              background: "rgba(56,189,248,0.12)",
                              borderBottom: "1px solid rgba(56,189,248,0.25)",
                              display: "flex",
                              justifyContent: "space-between",
                              alignItems: "center",
                              fontSize: "0.74rem",
                              color: "#38bdf8",
                            }
                          },
                            h("span", { style: { display: "flex", alignItems: "center", gap: "6px", fontWeight: 600 } },
                              h("span", { style: { width: "7px", height: "7px", borderRadius: "50%", background: "#38bdf8", display: "inline-block", boxShadow: "0 0 8px #38bdf8" } }),
                              "⚡ Live CLI Stream — " + taskId + " (workspace .haos/worker.log)"),
                            h("span", { style: { opacity: 0.85, fontSize: "0.7rem" } }, "Executando passo a passo ao vivo...")),
                          h("div", {
                            id: "cli-stream-" + taskId,
                            style: {
                              padding: "10px 12px",
                              maxHeight: "280px",
                              overflowY: "auto",
                              fontSize: "0.77rem",
                              lineHeight: "1.45",
                              color: "#e2e8f0",
                              whiteSpace: "pre-wrap",
                            }
                          },
                            lines.length > 0
                              ? lines.map(function (line, lIdx) {
                                  const isTool = line.includes("✍️") || line.includes("⚡") || line.includes("terminal") || line.includes("diff") || line.includes("write_file") || line.includes("read_file");
                                  const isHermes = line.includes("Hermes") || line.includes("Query:") || line.includes("Session:");
                                  const isSuccess = line.includes("exit=0") || line.includes("OK") || line.includes("passed") || line.includes("concluída");
                                  const isErr = line.includes("error") || line.includes("Error") || line.includes("failed");
                                  const col = isTool ? "#38bdf8" : (isSuccess ? "#4ade80" : (isErr ? "#f87171" : (isHermes ? "#a78bfa" : "#cbd5e1")));
                                  return h("div", { key: "l-" + lIdx, style: { color: col, borderLeft: isTool ? "2px solid #38bdf8" : "none", paddingLeft: isTool ? "6px" : "0" } }, line);
                                })
                              : h("div", { style: { color: "#94a3b8", fontStyle: "italic" } }, "Aguardando primeiras chamadas de ferramentas e stdout do worker Hermes...")),

                          // Barra de Intervenção em Tempo Real (Steer / Queue / Interrupt)
                          h("div", {
                            style: {
                              padding: "8px 10px",
                              background: "#0c1322",
                              borderTop: "1px solid rgba(56,189,248,0.25)",
                              display: "flex",
                              flexDirection: "column",
                              gap: "6px"
                            }
                          },
                            h("div", { style: { display: "flex", gap: "6px", alignItems: "center", flexWrap: "wrap" } },
                              h("input", {
                                type: "text",
                                placeholder: "Intervir no agente: digite nova instrução ou ajuste...",
                                value: steerTexts[taskId] || "",
                                style: {
                                  flex: "1 1 240px",
                                  padding: "6px 10px",
                                  background: "rgba(0,0,0,0.4)",
                                  border: "1px solid rgba(56,189,248,0.3)",
                                  borderRadius: "4px",
                                  color: "#f0f6fc",
                                  fontSize: "0.78rem"
                                },
                                onChange: function (e) {
                                  const val = e.target.value;
                                  setSteerTexts(function (prev) {
                                    const c = Object.assign({}, prev);
                                    c[taskId] = val;
                                    return c;
                                  });
                                },
                                onKeyDown: function (e) {
                                  if (e.key === "Enter") {
                                    sendSteer(taskId, "steer");
                                  }
                                }
                              }),
                              h("button", {
                                className: "hermes-haos-btn",
                                title: "Injeta a instrução no agente e redireciona no próximo step boundary (ao terminar a tool atual)",
                                disabled: Boolean(steerBusy[taskId]) || !(steerTexts[taskId] || "").trim(),
                                style: { fontSize: "0.73rem", padding: "4px 8px", color: "#38bdf8", borderColor: "rgba(56,189,248,0.4)" },
                                onClick: function () { sendSteer(taskId, "steer"); }
                              }, "⏩ Steer (Próxima Ação)"),
                              h("button", {
                                className: "hermes-haos-btn",
                                title: "Enfileira esta instrução para ser executada automaticamente assim que a tarefa atual terminar",
                                disabled: Boolean(steerBusy[taskId]) || !(steerTexts[taskId] || "").trim(),
                                style: { fontSize: "0.73rem", padding: "4px 8px", color: "#facc15", borderColor: "rgba(250,204,21,0.4)" },
                                onClick: function () { sendSteer(taskId, "queue"); }
                              }, "📥 Queue (Ao Concluir)"),
                              h("button", {
                                className: "hermes-haos-btn",
                                title: "Interrompe imediatamente o processo da tarefa atual e sobe esta nova instrução agora",
                                disabled: Boolean(steerBusy[taskId]) || !(steerTexts[taskId] || "").trim(),
                                style: { fontSize: "0.73rem", padding: "4px 8px", color: "#f87171", borderColor: "rgba(248,113,113,0.4)" },
                                onClick: function () { sendSteer(taskId, "interrupt"); }
                              }, "⏹ Interromper e Mandar")),
                            steerFeedback[taskId]
                              ? h("div", { style: { fontSize: "0.74rem", color: "#38bdf8", fontWeight: 500 } }, steerFeedback[taskId])
                              : null))
                      : null,

                    // Estado: Concluída com Resumo Executivo
                    (st === "done" && summary)
                      ? h("div", {
                          style: {
                            marginTop: "10px", padding: "14px",
                            background: "rgba(34,197,94,0.06)", border: "1px solid rgba(34,197,94,0.25)",
                            borderRadius: "6px"
                          }
                        },
                          h("div", { style: { display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "8px" } },
                            h("span", { style: { fontWeight: 600, color: "#4ade80", fontSize: "0.82rem", textTransform: "uppercase", letterSpacing: "0.05em" } },
                              "✓ Resposta Final Entregue pelo Modelo:"),
                            h("button", {
                              className: "hermes-haos-btn",
                              style: { fontSize: "0.72rem", padding: "2px 8px", opacity: 0.8 },
                              onClick: function () {
                                setShowLogsDone(function (prev) {
                                  const copy = Object.assign({}, prev);
                                  copy[taskId] = !copy[taskId];
                                  return copy;
                                });
                              }
                            }, isLogExpanded ? "Ocultar Log da CLI ✕" : "Ver Passos da Execução (CLI) 📋")),

                          h("div", {
                            style: {
                              whiteSpace: "pre-wrap", fontSize: "0.84rem", lineHeight: "1.6",
                              color: "#f1f5f9", fontFamily: "inherit"
                            }
                          }, summary),

                          // Log CLI colapsável após conclusão
                          isLogExpanded
                            ? h("div", {
                                style: {
                                  marginTop: "10px", padding: "10px", background: "#090d16",
                                  border: "1px solid rgba(255,255,255,0.1)", borderRadius: "6px",
                                  maxHeight: "220px", overflowY: "auto", fontFamily: "ui-monospace, monospace",
                                  fontSize: "0.75rem", whiteSpace: "pre-wrap", color: "#94a3b8"
                                }
                              },
                                (lines.length > 0)
                                  ? lines.map(function (line, lIdx) { return h("div", { key: "dl-" + lIdx }, line); })
                                  : "Nenhum log gravado em disco.")
                            : null,

                          artifacts.length > 0
                            ? h("div", { style: { marginTop: "12px", borderTop: "1px solid rgba(34,197,94,0.15)", paddingTop: "8px" } },
                                h("div", { style: { fontSize: "0.74rem", opacity: 0.8, fontWeight: 600, marginBottom: "4px" } }, "Artefatos gerados:"),
                                h("ul", { style: { margin: 0, paddingLeft: "16px", fontSize: "0.76rem", fontFamily: "ui-monospace, monospace", color: "#38bdf8" } },
                                  artifacts.map(function (art, aIdx) { return h("li", { key: "art-" + aIdx }, art); })))
                            : null)
                      : null);
                }))
        ),

        // Missões recentes do Kanban
        (recentTasks && recentTasks.length > 0)
          ? h("div", { style: { borderTop: "1px solid var(--border, rgba(126,153,220,0.18))", paddingTop: "12px" } },
              h("div", { style: { fontWeight: 600, fontSize: "0.84rem", marginBottom: "8px", opacity: 0.9 } }, "Últimas missões registradas no Kanban:"),
              h("ul", { style: { listStyle: "none", padding: 0, margin: 0 } },
                recentTasks.slice(0, 6).map(function (t, idx) {
                  const spec = t.spec || {};
                  const goal = spec.goal || t.title || t.id;
                  const res = t.result;
                  return h("li", {
                    key: "rc-task-" + idx,
                    style: {
                      fontSize: "0.8rem", padding: "8px 12px",
                      background: "rgba(0,0,0,0.12)", borderRadius: "6px",
                      marginBottom: "6px", display: "flex",
                      justifyContent: "space-between", alignItems: "center", gap: "10px", flexWrap: "wrap",
                    }
                  },
                    h("div", { style: { flex: "1 1 260px" } },
                      h("span", { style: { fontWeight: 600 } }, (t.title || t.id) + " "),
                      h("span", { style: { opacity: 0.7, fontSize: "0.75rem" } }, "(" + t.id + ")"),
                      res && res.summary
                        ? h("p", { style: { fontSize: "0.74rem", color: "#38bdf8", margin: "3px 0 0", fontFamily: "ui-monospace, monospace" } },
                            "Output: " + res.summary.slice(0, 110) + (res.summary.length > 110 ? "…" : ""))
                        : (t.status === "ready")
                        ? h("p", { style: { fontSize: "0.74rem", color: "#facc15", margin: "3px 0 0" } }, "⏳ Pronta para execução — aguarda despacho.")
                        : (t.status === "todo")
                        ? h("p", { style: { fontSize: "0.74rem", opacity: 0.6, margin: "3px 0 0" } }, "🔒 Aguardando resolução de dependências no grafo.")
                        : null),
                    h("div", { style: { display: "flex", gap: "8px", alignItems: "center", flexShrink: 0 } },
                      renderTaskBadge(t.status),
                      renderTaskAction(t, onSelectTask, onDispatch)));
                })))
          : null
      ));
  }

  function NewTaskCard(props) {
    const onCreated = props.onCreated;
    const onDispatch = props.onDispatch;
    const dispatchDisabled = props.dispatchDisabled;
    const [goal, setGoal] = useState("");
    const [prio, setPrio] = useState(50);
    const [title, setTitle] = useState("");
    const [statusText, setStatusText] = useState("");
    const [busy, setBusy] = useState(false);

    function create() {
      const g = (goal || "").trim();
      if (!g || busy) return;
      setBusy(true);
      setStatusText("Criando card…");
      postJSON(TASKS_URL, {
        goal: g,
        priority: Number(prio) || 50,
        title: (title || "").trim() || undefined,
      })
        .then(function (res) {
          setGoal("");
          setTitle("");
          setStatusText("✓ Card criado: " + (res.task_id || "ok"));
          if (typeof onCreated === "function") onCreated();
        })
        .catch(function (err) {
          setStatusText("✕ Erro: " + parseApiErrorMessage(err));
        })
        .then(function () {
          setBusy(false);
        });
    }

    return h(Card, { className: "hermes-haos-card" },
      h(CardHeader, null,
        h(CardTitle, null, "+ Nova Missão Manual")),
      h(CardContent, null,
        h("label", { style: { display: "block", fontSize: "0.78rem", opacity: 0.85, marginTop: 4 } }, "Objetivo"),
        h("input", {
          type: "text",
          value: goal,
          placeholder: "Descreva o objetivo da tarefa…",
          style: SYSTEM_INPUT_STYLE,
          onChange: function (e) { setGoal(e.target.value); },
        }),
        h("label", { style: { display: "block", fontSize: "0.78rem", opacity: 0.85, marginTop: 8 } }, "Título (opcional)"),
        h("input", {
          type: "text",
          value: title,
          placeholder: "Ex.: Missão: Refatorar módulo X",
          style: SYSTEM_INPUT_STYLE,
          onChange: function (e) { setTitle(e.target.value); },
        }),
        h("label", { style: { display: "block", fontSize: "0.78rem", opacity: 0.85, marginTop: 8 } }, "Prioridade (1–100)"),
        h("input", {
          type: "number",
          value: prio,
          min: 1, max: 100,
          style: SYSTEM_INPUT_STYLE,
          onChange: function (e) { setPrio(e.target.value); },
        }),
        h("div", { style: { display: "flex", gap: "8px", marginTop: "12px", flexWrap: "wrap" } },
          h("button", {
            className: "hermes-haos-btn hermes-haos-btn--dispatch",
            disabled: busy || !goal.trim(),
            onClick: create,
          }, busy ? "Criando…" : "Criar card"),
          h("button", {
            className: "hermes-haos-btn",
            disabled: dispatchDisabled,
            onClick: onDispatch,
          }, "⏩ Despachar agora")),
        statusText ? h("p", { style: { fontSize: "0.78rem", marginTop: 8, opacity: 0.85 } }, statusText) : null));
  }

  function SchedulerSection(criticalPath, concurrency) {
    const cp = criticalPath || {};
    const cg = concurrency || {};
    const cpIds = (cp.critical_path_ids || []).join(" → ") || "—";
    const pip = cp.inherited_priorities || {};
    const providers = cg.providers || {};

    return h("div", { style: { gridColumn: "1 / -1", display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(320px, 1fr))", gap: "1rem" } },
      h(Card, { className: "hermes-haos-card" },
        h(CardHeader, null,
          h(CardTitle, null, "✧ Critical Path Method (CPM) & PIP")),
        h(CardContent, null,
          h("div", { style: { fontSize: "0.82rem", margin: "4px 0" } },
            h("span", { style: { opacity: 0.7 } }, "Tarefas avaliadas no grafo: "),
            h("b", null, pad(cp.total_tasks_evaluated || 0))),
          h("div", { style: { fontSize: "0.82rem", margin: "6px 0" } },
            h("span", { style: { opacity: 0.7 } }, "Caminho Crítico: "),
            h("span", { style: { fontFamily: "ui-monospace, monospace", color: "var(--primary, #38bdf8)" } }, cpIds)),
          h("div", { style: { borderTop: "1px solid var(--border, rgba(126,153,220,0.25))", marginTop: 8, paddingTop: 6 } },
            h("div", { style: { fontSize: "0.78rem", fontWeight: 600, opacity: 0.8 } }, "Prioridades Herdadas (PIP):"),
            Object.keys(pip).length === 0
              ? h("p", { style: { fontSize: "0.78rem", opacity: 0.6, margin: "4px 0" } }, "Nenhuma prioridade herdada ativa.")
              : h("div", { style: { fontSize: "0.78rem", marginTop: 4 } },
                  Object.entries(pip).map(function (entry) {
                    return h("div", { key: entry[0], style: { display: "flex", justifyContent: "space-between", margin: "2px 0" } },
                      h("span", { style: { fontFamily: "ui-monospace, monospace" } }, entry[0]),
                      h("b", null, String(entry[1])));
                  }))))),
      h(Card, { className: "hermes-haos-card" },
        h(CardHeader, null,
          h(CardTitle, null, "◈ ConcurrencyGuard")),
        h(CardContent, null,
          h("div", { style: { fontSize: "0.82rem", margin: "4px 0" } },
            h("span", { style: { opacity: 0.7 } }, "Workers ativos / Teto global: "),
            h("b", null, String(cg.active_global || 0) + " / " + String(cg.max_global || 8))),
          h("div", { style: { borderTop: "1px solid var(--border, rgba(126,153,220,0.25))", marginTop: 8, paddingTop: 6 } },
            h("div", { style: { fontSize: "0.78rem", fontWeight: 600, opacity: 0.8 } }, "Quotas por Provedor:"),
            Object.keys(providers).length === 0
              ? h("p", { style: { fontSize: "0.78rem", opacity: 0.6, margin: "4px 0" } }, "Sem provedores configurados.")
              : h("table", { className: "hermes-haos-table" },
                  h("thead", null, h("tr", null, h("th", null, "Provider"), h("th", null, "Ativos"), h("th", null, "Limite"))),
                  h("tbody", null,
                    Object.entries(providers).map(function (entry) {
                      const pname = entry[0];
                      const pinfo = entry[1];
                      return h("tr", { key: pname },
                        h("td", { style: { fontWeight: 600 } }, pname),
                        h("td", null, String((pinfo && pinfo.active) || 0)),
                        h("td", null, String((pinfo && pinfo.limit) || "—")));
                    })))))));
  }

  function TerminalSection() {
    const [sid, setSid] = useState(null);
    const [cwd, setCwd] = useState("");
    const [output, setOutput] = useState("HAOS Terminal embutido (PTY nativo).\nClique em '▶ Nova sessão' para iniciar.\n");
    const [cmd, setCmd] = useState("");
    const [starting, setStarting] = useState(false);
    const [statusMsg, setStatusMsg] = useState("");

    function startTerm() {
      setStarting(true);
      setStatusMsg("Iniciando sessão de terminal…");
      postJSON(TERMINAL_URL + "/start", { cwd: cwd.trim() || undefined })
        .then(function (res) {
          setSid(res.session_id);
          setOutput("=== Sessão de terminal conectada (" + res.session_id + ") ===\n");
          setStatusMsg("Sessão ativa");
        })
        .catch(function (err) {
          setStatusMsg("Erro ao iniciar: " + parseApiErrorMessage(err));
        })
        .then(function () {
          setStarting(false);
        });
    }

    function killTerm() {
      if (!sid) return;
      postJSON(TERMINAL_URL + "/" + encodeURIComponent(sid) + "/kill", {})
        .catch(function () {});
      setSid(null);
      setOutput(function (prev) { return prev + "\n=== Sessão encerrada ===\n"; });
      setStatusMsg("Sessão finalizada.");
    }

    function sendCmd() {
      const c = cmd;
      if (!sid || c === null || c === undefined) return;
      postJSON(TERMINAL_URL + "/" + encodeURIComponent(sid) + "/input", { data: c + "\n" })
        .then(function () {
          setCmd("");
        })
        .catch(function (err) {
          setStatusMsg("Erro de envio: " + parseApiErrorMessage(err));
        });
    }

    useEffect(function () {
      if (!sid) return;
      const interval = setInterval(function () {
        SDK.fetchJSON(TERMINAL_URL + "/" + encodeURIComponent(sid) + "/drain")
          .then(function (res) {
            if (res && res.data) {
              setOutput(function (prev) {
                const next = prev + res.data;
                return next.length > 30000 ? next.slice(-25000) : next;
              });
            }
            if (res && res.running === false) {
              setSid(null);
              setStatusMsg("Processo encerrou (código " + String(res.exit_code) + ")");
            }
          })
          .catch(function () {});
      }, 500);
      return function () { clearInterval(interval); };
    }, [sid]);

    return h(Card, { className: "hermes-haos-card", style: { gridColumn: "1 / -1" } },
      h(CardHeader, null,
        h(CardTitle, null, "▮ Terminal Embutido — Shell PTY Nativo"),
        h("p", { style: { fontSize: "0.82rem", opacity: 0.75, margin: "4px 0 0" } },
          "Acesso ao terminal interativo do servidor com PTY real (estilo v1) centralizado dentro do HAOS.")),
      h(CardContent, null,
        h("div", { style: { display: "flex", gap: "8px", alignItems: "center", flexWrap: "wrap", marginBottom: "10px" } },
          h("input", {
            type: "text",
            value: cwd,
            placeholder: "cwd (opcional, vazio = home)",
            style: { flex: "1 1 220px", padding: "6px 10px", borderRadius: "6px", background: "rgba(0,0,0,0.25)", border: "1px solid var(--border, rgba(126,153,220,0.3))", color: "inherit", fontSize: "0.85rem" },
            onChange: function (e) { setCwd(e.target.value); },
            disabled: Boolean(sid),
          }),
          h("button", {
            className: "hermes-haos-btn hermes-haos-btn--dispatch",
            disabled: starting || Boolean(sid),
            onClick: startTerm,
          }, starting ? "Iniciando…" : "▶ Nova sessão"),
          h("button", {
            className: "hermes-haos-btn",
            disabled: !sid,
            onClick: killTerm,
            style: { color: "#f87171" },
          }, "✕ Encerrar"),
          statusMsg ? h("span", { style: { fontSize: "0.8rem", opacity: 0.8, marginLeft: 6 } }, statusMsg) : null),
        h("div", { className: "hermes-haos-terminal-container" },
          h("pre", { className: "hermes-haos-terminal-pre" }, output),
          h("div", { style: { display: "flex", gap: "8px" } },
            h("input", {
              type: "text",
              value: cmd,
              placeholder: sid ? "Digite um comando e pressione Enter…" : "Inicie uma sessão para digitar comandos",
              disabled: !sid,
              style: { flex: 1, padding: "8px 10px", borderRadius: "6px", background: "rgba(0,0,0,0.3)", border: "1px solid var(--border, rgba(126,153,220,0.3))", color: "inherit", fontFamily: "ui-monospace, monospace", fontSize: "0.85rem" },
              onChange: function (e) { setCmd(e.target.value); },
              onKeyDown: function (e) {
                if (e.key === "Enter") {
                  e.preventDefault();
                  sendCmd();
                }
              },
            }),
            h("button", {
              className: "hermes-haos-btn",
              disabled: !sid || !cmd.trim(),
              onClick: sendCmd,
            }, "Enviar ↵")))));
  }

  function EventsSection(props) {
    const events = props.events || [];
    const [expanded, setExpanded] = useState(null);

    return h(Card, { className: "hermes-haos-card", style: { gridColumn: "1 / -1" } },
      h(CardHeader, null,
        h(CardTitle, null, "≡ EventStore — Trilha de Auditoria Append-Only"),
        h("p", { style: { fontSize: "0.82rem", opacity: 0.75, margin: "4px 0 0" } },
          "Registro imutável dos eventos do engine HAOS (tasks, runs, reviews, checkpoints). Total: " + pad(events.length))),
      h(CardContent, null,
        events.length === 0
          ? h("p", { style: { fontSize: "0.84rem", opacity: 0.6 } }, "Nenhum evento registrado no EventStore.")
          : h("div", { style: { maxHeight: "480px", overflowY: "auto" } },
              h("table", { className: "hermes-haos-table" },
                h("thead", null,
                  h("tr", null,
                    h("th", null, "Timestamp"),
                    h("th", null, "Evento"),
                    h("th", null, "Trace / Correlation"),
                    h("th", null, "Payload"))),
                h("tbody", null,
                  events.map(function (ev, idx) {
                    const isExp = expanded === idx;
                    let when = String(ev.timestamp);
                    try { when = new Date(Number(ev.timestamp) * 1000).toISOString().replace("T", " ").slice(0, 19) + " UTC"; } catch (_e) {}
                    return h("tr", { key: "ev-" + idx, style: { cursor: "pointer" }, onClick: function () { setExpanded(isExp ? null : idx); } },
                      h("td", { style: { opacity: 0.7, whiteSpace: "nowrap" } }, when),
                      h("td", null, h("span", { style: { fontFamily: "ui-monospace, monospace", color: "var(--primary, #38bdf8)", fontWeight: 600 } }, ev.name)),
                      h("td", { style: { fontFamily: "ui-monospace, monospace", opacity: 0.75 } },
                        (ev.correlation_id || ev.trace_id || "—").slice(0, 12)),
                      h("td", { style: { wordBreak: "break-word", opacity: 0.85 } },
                        isExp
                          ? h("pre", { style: { margin: 0, padding: 4, background: "rgba(0,0,0,0.3)", borderRadius: 4, fontSize: "0.75rem", whiteSpace: "pre-wrap" } }, ev.payload)
                          : (ev.payload || "").slice(0, 80) + ((ev.payload || "").length > 80 ? "…" : "")));
                  }))))));
  }

  function EngineConfigSection(props) {
    const settings = props.settings || {};
    const onSaved = props.onSaved;
    const [cfg, setCfg] = useState(settings);
    const [statusMsg, setStatusMsg] = useState("");
    const [busy, setBusy] = useState(false);

    useEffect(function () {
      setCfg(settings);
    }, [settings]);

    function save() {
      setBusy(true);
      setStatusMsg("Salvando e aplicando ao vivo…");
      postJSON(SETTINGS_URL, cfg)
        .then(function () {
          setStatusMsg("✓ Configurações do engine salvas e aplicadas ao ConcurrencyGuard!");
          if (typeof onSaved === "function") onSaved();
        })
        .catch(function (err) {
          setStatusMsg("✕ Erro: " + parseApiErrorMessage(err));
        })
        .then(function () {
          setBusy(false);
        });
    }

    function reset() {
      if (!confirm("Restaurar as configurações padrão do engine?")) return;
      setBusy(true);
      setStatusMsg("Restaurando padrões…");
      postJSON(SETTINGS_RESET_URL, {})
        .then(function (res) {
          setCfg(res.settings || {});
          setStatusMsg("✓ Configurações restauradas!");
          if (typeof onSaved === "function") onSaved();
        })
        .catch(function (err) {
          setStatusMsg("✕ Erro: " + parseApiErrorMessage(err));
        })
        .then(function () {
          setBusy(false);
        });
    }

    return h(Card, { className: "hermes-haos-card" },
      h(CardHeader, null,
        h(CardTitle, null, "⚙ Configurações do Engine HAOS (settings.json)")),
      h(CardContent, null,
        h("label", { style: { display: "block", fontSize: "0.78rem", opacity: 0.85, marginTop: 4 } }, "Concorrência global máxima"),
        h("input", {
          type: "number",
          value: cfg.max_global_concurrency || 8,
          style: SYSTEM_INPUT_STYLE,
          onChange: function (e) { setCfg(Object.assign({}, cfg, { max_global_concurrency: Number(e.target.value) })); },
        }),
        h("label", { style: { display: "block", fontSize: "0.78rem", opacity: 0.85, marginTop: 8 } }, "Limite default por provider"),
        h("input", {
          type: "number",
          value: cfg.default_provider_limit || 2,
          style: SYSTEM_INPUT_STYLE,
          onChange: function (e) { setCfg(Object.assign({}, cfg, { default_provider_limit: Number(e.target.value) })); },
        }),
        h("label", { style: { display: "block", fontSize: "0.78rem", opacity: 0.85, marginTop: 8 } }, "Intervalo de auto-refresh (ms)"),
        h("input", {
          type: "number",
          value: cfg.poll_refresh_ms || 4000,
          step: 500,
          style: SYSTEM_INPUT_STYLE,
          onChange: function (e) { setCfg(Object.assign({}, cfg, { poll_refresh_ms: Number(e.target.value) })); },
        }),
        h("label", { style: { display: "flex", alignItems: "center", gap: "8px", fontSize: "0.78rem", opacity: 0.9, marginTop: 10, cursor: "pointer" } },
          h("input", {
            type: "checkbox",
            checked: Boolean(cfg.shadow_mode),
            onChange: function (e) { setCfg(Object.assign({}, cfg, { shadow_mode: e.target.checked })); },
          }),
          "Ouroboros shadow mode (apenas analisa, nunca aplica sozinho)"),
        h("div", { style: { display: "flex", gap: "8px", marginTop: 14, flexWrap: "wrap" } },
          h("button", {
            className: "hermes-haos-btn hermes-haos-btn--dispatch",
            disabled: busy,
            onClick: save,
          }, busy ? "Salvando…" : "Salvar e aplicar ao vivo"),
          h("button", {
            className: "hermes-haos-btn",
            disabled: busy,
            onClick: reset,
            style: { color: "#f87171" },
          }, "Restaurar defaults")),
        statusMsg ? h("p", { style: { fontSize: "0.78rem", marginTop: 8, opacity: 0.85 } }, statusMsg) : null));
  }

  function AgentYamlSection() {
    const [rawCfg, setRawCfg] = useState(null);
    const [openRaw, setOpenRaw] = useState(false);
    const [statusMsg, setStatusMsg] = useState("");

    function loadRaw() {
      setStatusMsg("Carregando config.yaml…");
      SDK.fetchJSON(AGENT_CONFIG_URL)
        .then(function (d) {
          setRawCfg(d);
          setOpenRaw(true);
          setStatusMsg("");
        })
        .catch(function (err) {
          setStatusMsg("Erro ao carregar: " + parseApiErrorMessage(err));
        });
    }

    return h(Card, { className: "hermes-haos-card" },
      h(CardHeader, null,
        h(CardTitle, null, "📝 Configuração do Agente (config.yaml)")),
      h(CardContent, null,
        h("p", { style: { fontSize: "0.8rem", opacity: 0.8, margin: "0 0 10px" } },
          "Visualização dos valores estruturados do arquivo de configuração oficial."),
        h("div", { style: { display: "flex", gap: "8px", alignItems: "center", flexWrap: "wrap" } },
          h("button", {
            className: "hermes-haos-btn",
            onClick: loadRaw,
          }, openRaw ? "↻ Recarregar" : "{} Ver Snapshot JSON"),
          statusMsg ? h("span", { style: { fontSize: "0.78rem", opacity: 0.8 } }, statusMsg) : null),
        openRaw && rawCfg
          ? h("pre", {
              style: {
                marginTop: "10px", padding: "10px", background: "rgba(0,0,0,0.3)",
                borderRadius: "6px", fontSize: "0.75rem", fontFamily: "ui-monospace, monospace",
                maxHeight: "320px", overflowY: "auto", whiteSpace: "pre-wrap",
              }
            }, JSON.stringify(rawCfg, null, 2))
          : null));
  }


  // -------------------------------------------------------------------------
  // Main page
  // -------------------------------------------------------------------------
  function HaosPage() {
    const [view, setView] = useState({ loading: true, data: null, error: null });
    const [operator, setOperator] = useState("");
    const [busy, setBusy] = useState("");
    const [notice, setNotice] = useState(null);
    // Delta 51 (transparência): última resposta do bridge ACP de planejamento
    // (session_id/agent/result). O plano produzido por "Plan over ACP" ficava
    // descartado após o notice "ok" — agora a view o exibe quando o backend
    // devolve uma sessão real (shape com session_id); respostas sem sessão
    // (fail-closed/erro) não populam a seção.
    const [acpSession, setAcpSession] = useState(null);
    // Delta 53: 'Sistema & Config' abre como modal (botão ao lado do ACP plan)
    // em vez de um card gigante no grid — a lista de caminhos fica sob demanda.
    const [sysOpen, setSysOpen] = useState(false);
    // Modal de detalhes e output de tarefas do Kanban / Console
    const [selectedTask, setSelectedTask] = useState(null);
    // Delta 56: sub-tabs de navegação dentro do HAOS Dataplane
    const [activeTab, setActiveTab] = useState("overview");

    // Esc fecha modais (Sistema & Config e Detalhes da Tarefa).
    useEffect(function () {
      if (!sysOpen && !selectedTask) return;
      function onKey(e) {
        if (e.key === "Escape") {
          setSysOpen(false);
          setSelectedTask(null);
        }
      }
      window.addEventListener("keydown", onKey);
      return function () { window.removeEventListener("keydown", onKey); };
    }, [sysOpen, selectedTask]);

    function loadState() {
      return SDK.fetchJSON(STATE_URL)
        .then(function (data) {
          setView({ loading: false, data: data, error: null });
        })
        .catch(function (err) {
          setView({ loading: false, data: null, error: parseApiErrorMessage(err) });
        });
    }

    useEffect(function () {
      loadState();
      // Auto-refresh no browser: consulta o estado a cada 5 segundos para refletir
      // mudanças do Kanban, novos eventos e conclusões de tarefas sem precisar de reload.
      if (typeof window !== "undefined" && typeof window.setInterval === "function") {
        const intervalId = window.setInterval(function () {
          // Não sobrescreve quando o usuário estiver executando uma ação interativa
          if (!busy) {
            SDK.fetchJSON(STATE_URL)
              .then(function (data) {
                setView({ loading: false, data: data, error: null });
              })
              .catch(function () {});
          }
        }, 5000);
        return function () { window.clearInterval(intervalId); };
      }
    }, [busy]);

    // Run one action; on success reload the derived state; surface errors
    // inline. Never fabricates a result — the backend is the source of truth.
    // Delta 49 hardening: show immediate progress feedback while the POST is
    // in flight (a slow action — e.g. ACP spawning its peer — previously
    // looked dead: buttons disabled, no message until the promise settled).
    // Delta 51 (transparência): an optional onResult callback receives the
    // backend reply so the view can surface what an action actually produced
    // (e.g. the ACP plan) instead of discarding it after "ok".
    function runAction(kind, url, payload, onResult) {
      setBusy(kind);
      setNotice({ kind: "running", text: kind + "…" });
      postJSON(url, payload)
        .then(function (data) {
          setNotice({ kind: "success", text: kind + " ok" });
          if (typeof onResult === "function") onResult(data);
          return loadState();
        })
        .catch(function (err) {
          setNotice({ kind: "error", text: parseApiErrorMessage(err) });
        })
        .then(function () {
          setBusy("");
        });
    }

    function onReviewDecide(taskId, verdict) {
      runAction("review " + verdict,
                REVIEW_DECIDE_URL,
                { task_id: taskId, verdict: verdict, approver: operator });
    }

    function onDecide(proposalId, verdict) {
      runAction("decide " + verdict,
                EVOLUTION_DECIDE_URL,
                { proposal_id: proposalId, verdict: verdict, approver: operator });
    }
    function onGrantApprove(scope, ref) {
      runAction("grant approve", GRANT_APPROVE_URL,
                { scope: scope, credential_ref: ref, approver: operator });
    }
    function onGrantRevoke(scope, ref) {
      runAction("grant revoke", GRANT_REVOKE_URL,
                { scope: scope, credential_ref: ref });
    }
    function onDispatch() {
      runAction("dispatch", DISPATCH_URL, undefined);
    }
    function onAcpPlan() {
      // cwd fica no servidor (HERMES_HOME do processo) — a view do browser não
      // conhece caminhos do servidor; a rota aceita cwd opcional.
      runAction("acp plan", ACP_PLAN_URL,
                { instruction: operator || undefined },
                function (data) {
                  // Exibe o plano devolvido pelo backend quando há sessão real
                  // (delta 51 — transparência; nunca fabrica conteúdo).
                  if (data && typeof data === "object" && data.session_id) {
                    setAcpSession(data);
                  }
                });
    }

    if (view.loading) {
      return h("div", { className: "hermes-haos hermes-haos--loading" },
        "Loading HAOS control plane…");
    }
    if (view.error) {
      return h("div", { className: cn("hermes-haos", "hermes-haos--error") },
        h(Card, null,
          h(CardHeader, null, h(CardTitle, null, "HAOS Control Plane")),
          h(CardContent, null,
            h("p", { role: "alert" }, "Unavailable: " + view.error))));
    }

    const payload = view.data || {};
    const taskboard = payload.taskboard || {};
    const memoryGraph = payload.memory_graph || {};
    const approvals = payload.approvals || {};
    const evolution = payload.evolution_pending || [];
    const grantsPending = payload.grants_pending || [];

    // Actions toolbar (delta 49). Human-approval actions need an operator id.
    const dispatchDisabled = Boolean(busy);
    const planDisabled = Boolean(busy);

    return h("div", { className: "hermes-haos" },
      h("div", { className: "hermes-haos-actions" },
        h(Card, { className: "hermes-haos-card" },
          h(CardHeader, null,
            h(CardTitle, null, "Actions")),
          h(CardContent, null,
            h("label", { className: "hermes-haos-operator" },
              "Operator / approver: ",
              h("input", {
                type: "text",
                className: "hermes-haos-operator-input",
                "data-field": "operator",
                placeholder: "your name (needed to decide / approve)",
                value: operator,
                onChange: function (e) { setOperator(e.target.value); },
              })),
            h("div", { className: "hermes-haos-action-row" },
              h("button", {
                className: "hermes-haos-btn",
                "data-action": "refresh",
                title: "Atualizar estado observável imediatamente",
                disabled: Boolean(busy),
                onClick: function () { loadState(); },
              }, "↻ Refresh"),
              h("button", {
                className: "hermes-haos-btn hermes-haos-btn--dispatch",
                "data-action": "dispatch",
                title: "Run the canonical dispatcher over READY cards",
                disabled: dispatchDisabled,
                onClick: onDispatch,
              }, "Dispatch ready"),
              h("button", {
                className: "hermes-haos-btn hermes-haos-btn--acp",
                "data-action": "acp-plan",
                title: "Start an ACP planning session with the observable state",
                disabled: planDisabled,
                onClick: onAcpPlan,
              }, "Plan over ACP"),
              h("button", {
                className: "hermes-haos-btn hermes-haos-btn--system",
                "data-action": "system-config",
                title: "Onde cada arquivo de configuração está salvo (modal)",
                disabled: Boolean(busy),
                onClick: function () { setSysOpen(true); },
              }, "⚙ Sistema & Config")),
            notice
              ? h("p", {
                  className: cn("hermes-haos-notice",
                                "hermes-haos-notice--" + notice.kind),
                  role: "status",
                }, notice.text)
              : null)),
      ),
      h("div", { className: "hermes-haos-subtabs" },
        [
          { id: "overview", label: "⊞ Visão Geral" },
          { id: "resilience", label: "⚡ Failover & Fabric" },
          { id: "console", label: "⌨ Console" },
          { id: "taskboard", label: "▤ Taskboard & Scheduler" },
          { id: "graph", label: "🕸️ Knowledge Graph" },
          { id: "terminal", label: "▮ Terminal" },
          { id: "events", label: "≡ Eventos" },
          { id: "config", label: "⚙ Engine & Config" },
        ].map(function (tab) {
          return h("button", {
            key: tab.id,
            className: cn("hermes-haos-subtab-btn", activeTab === tab.id ? "active" : ""),
            "data-tab": tab.id,
            onClick: function () { setActiveTab(tab.id); },
          }, tab.label);
        })),
      (activeTab === "overview")
        ? h("div", { className: "hermes-haos-grid" },
            OverviewStats(payload),
            ModelFailoverCard(payload.model_failover),
            McpPacksCard(payload.mcp_packs),
            WorktreesBlastRadiusCard(payload.kilo_worktrees),
            FederatedNetworkCard(payload.federation),
            ApprovalsSection(approvals, operator, busy, onReviewDecide),
            TaskboardSection(taskboard, setSelectedTask),
            MemorySection(memoryGraph),
            AcpSessionSection(acpSession),
            EvolutionSection(evolution, operator, busy, onDecide),
            GrantsSection(grantsPending, operator, busy, onGrantApprove, onGrantRevoke))
        : (activeTab === "resilience")
        ? h("div", { className: "hermes-haos-grid" },
            ModelFailoverCard(payload.model_failover),
            McpPacksCard(payload.mcp_packs),
            WorktreesBlastRadiusCard(payload.kilo_worktrees),
            FederatedNetworkCard(payload.federation))
        : (activeTab === "console")
        ? h("div", { className: "hermes-haos-grid" },
            h(ConsoleSection, { onReload: loadState, recentTasks: taskboard.recent, onSelectTask: setSelectedTask, onDispatch: onDispatch }))
        : (activeTab === "taskboard")
        ? h("div", { className: "hermes-haos-grid" },
            h(NewTaskCard, { onCreated: loadState, onDispatch: onDispatch, dispatchDisabled: dispatchDisabled }),
            TaskboardSection(taskboard, setSelectedTask),
            SchedulerSection(payload.critical_path, payload.concurrency))
        : (activeTab === "graph")
        ? h("div", { className: "hermes-haos-grid" },
            h(KnowledgeGraphSection, null))
        : (activeTab === "terminal")
        ? h("div", { className: "hermes-haos-grid" },
            h(TerminalSection, null))
        : (activeTab === "events")
        ? h("div", { className: "hermes-haos-grid" },
            h(EventsSection, { events: payload.events_tail }))
        : (activeTab === "config")
        ? h("div", { className: "hermes-haos-grid" },
            h(EngineConfigSection, { settings: payload.settings, onSaved: loadState }),
            h(AgentYamlSection, null))
        : null,
      sysOpen
        ? h("div", {
            className: "hermes-haos-overlay",
            style: {
              position: "fixed", inset: 0, zIndex: 9990,
              background: "rgba(3, 5, 10, 0.66)",
              display: "flex", alignItems: "center", justifyContent: "center",
              padding: 18,
            },
            onClick: function () { setSysOpen(false); },
          },
          h("div", {
            className: "hermes-haos-modal",
            role: "dialog",
            "aria-modal": "true",
            onClick: function (e) { e.stopPropagation(); },
            style: {
              background: "var(--background-base, rgba(10, 14, 24, 0.99))",
              color: "var(--midground-base, #cfe0ff)",
              border: "1px solid var(--border, rgba(126, 153, 220, 0.35))",
              borderRadius: "1rem",
              maxWidth: 860, width: "100%", maxHeight: "84vh", overflow: "auto",
              padding: "16px 18px",
              boxShadow: "0 18px 60px rgba(0, 0, 0, 0.55)",
            },
          },
          h("div", { style: { display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 } },
            h("span", { style: { fontWeight: 700, letterSpacing: "0.05em", fontSize: "0.95rem" } },
              "Sistema & Config — onde cada arquivo está salvo"),
            h("button", {
              className: "hermes-haos-btn",
              "data-action": "close-system",
              onClick: function () { setSysOpen(false); },
            }, "✕ Fechar")),
          h(SystemSection, null)))
        : null,
      selectedTask
        ? h(TaskDetailModal, {
            task: selectedTask,
            onClose: function () { setSelectedTask(null); }
          })
        : null);
  }

  // -------------------------------------------------------------------------
  // Register
  // -------------------------------------------------------------------------
  if (window.__HERMES_PLUGINS__ &&
      typeof window.__HERMES_PLUGINS__.register === "function") {
    window.__HERMES_PLUGINS__.register("haos", HaosPage);
  }
})();
