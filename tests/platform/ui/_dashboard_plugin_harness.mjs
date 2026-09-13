#!/usr/bin/env node
/**
 * HAOS dashboard-plugin bundle harness (delta 46 + delta 49 + regressão de
 * feedback de busy).
 *
 * EXECUTES hermes/platform/ui/dashboard_plugin/dist/index.js the way the
 * Web Dashboard host would — not text-inspection. Behavioural checks:
 *
 *   1. Fail-closed: with `window` but no SDK globals the bundle is inert
 *      (no throw, nothing registered).
 *   2. Registration: with the SDK + registry present, the bundle calls
 *      `register("haos", Component)`.
 *   3. Mount: rendering the registered component must call
 *      `SDK.fetchJSON("/api/plugins/haos/state")` and, once the backend
 *      payload resolves, render totals derived FROM that payload (nothing
 *      fabricated). A stub React (createElement + useState/useEffect on the
 *      first render only, matching the bundle's `[]` deps) drives the mount.
 *   4. Delta 49 actions: the Actions card renders operator/approver input and
 *      buttons that POST to this plugin's backend routes with a body DERIVED
 *      from the payload + the typed operator. The harness types an operator,
 *      clicks each button and asserts the exact POST url/method/body that
 *      SDK.fetchJSON received (dispatch, evolution approve/reject, grant
 *      approve/revoke, acp plan). No browser: the host click loop is
 *      simulated on the stub tree.
 *   5. Busy feedback (regressão #visual): a slow action (ACP spawning its
 *      peer) must render an immediate "running" notice while the POST is in
 *      flight — the harness HOLDS the acp POST promise, asserts the
 *      mid-flight notice, releases it, and asserts success replaces it.
 *
 * Emits one JSON line on stdout: {"ok":true/false, ...diagnostics}. Exit 0
 * only when all checks pass. Run via node (skip in the pytest wrapper when
 * node is unavailable).
 */

"use strict";

import { createRequire } from "node:module";
const require = createRequire(import.meta.url);
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const BUNDLE = process.argv[2];
if (!BUNDLE) {
  console.error("usage: node dashboard_plugin_harness.mjs <dist/index.js>");
  process.exit(2);
}
const code = fs.readFileSync(path.resolve(BUNDLE), "utf8");

// ---------------------------------------------------------------------------
// Minimal stub React: createElement + useState/useEffect (first render only).
// Enough to mount HaosPage; real host provides actual React via the SDK.
// Renders can be replayed (mountCtx.dirty) to simulate setState round trips.
// ---------------------------------------------------------------------------
let mountCtx = null;

function makeReactStub() {
  function createElement(type, props, ...children) {
    return { type, props: props || {}, children };
  }
  return { createElement };
}

function makeHooksStub() {
  function useState(initial) {
    if (!mountCtx) throw new Error("useState outside mount");
    const i = mountCtx.idx++;
    if (!(i in mountCtx.hooks)) mountCtx.hooks[i] = { value: initial };
    const slot = mountCtx.hooks[i];
    return [
      slot.value,
      function setValue(next) {
        slot.value = typeof next === "function" ? next(slot.value) : next;
        mountCtx.dirty = true;
      },
    ];
  }
  function useEffect(fn) {
    if (!mountCtx) throw new Error("useEffect outside mount");
    if (!mountCtx.effectsRun) mountCtx.effects.push(fn);
  }
  return { useState, useEffect };
}

// SDK UI components: pass-through wrappers so text/children survive render.
function makeComponentsStub(h) {
  function wrap(tag) {
    return function (props) {
      return h("div", { className: tag + (props.className ? " " + props.className : "") },
        props.children);
    };
  }
  return {
    Card: wrap("card"),
    CardHeader: wrap("card-header"),
    CardTitle: wrap("card-title"),
    CardContent: wrap("card-content"),
    Badge: wrap("badge"),
  };
}

// ---------------------------------------------------------------------------
// Tree walk: collect text leaves and fetch/action calls actually made.
// ---------------------------------------------------------------------------
function collectText(node, out) {
  if (node === null || node === undefined) return;
  if (typeof node === "string") { out.push(node); return; }
  if (Array.isArray(node)) {
    for (const child of node) collectText(child, out);
    return;
  }
  if (node && typeof node === "object" && "children" in node) {
    // Resolve function components (stub UI wrappers) before descending.
    if (typeof node.type === "function") {
      const resolved = node.type({ ...node.props, children: node.children });
      collectText(resolved, out);
      return;
    }
    collectText(node.children, out);
  }
}

/** Collect interactive nodes: elements tagged data-action (buttons) or
 *  data-field (inputs), resolving function components on the way down. */
function collectInteractive(node, out) {
  if (node === null || node === undefined) return;
  if (Array.isArray(node)) {
    for (const child of node) collectInteractive(child, out);
    return;
  }
  if (node && typeof node === "object" && "children" in node) {
    if (typeof node.type === "function") {
      const resolved = node.type({ ...node.props, children: node.children });
      collectInteractive(resolved, out);
      return;
    }
    const isElement = typeof node.type === "string";
    if (isElement && (node.props["data-action"] || node.props["data-field"])) {
      out.push(node);
    }
    collectInteractive(node.children, out);
  }
}

// ---------------------------------------------------------------------------
// Check 1 + 2: inert without SDK; registers with SDK.
// ---------------------------------------------------------------------------
const results = { ok: true, checks: {}, errors: [] };
function fail(msg) { results.ok = false; results.errors.push(msg); }

{
  // No SDK globals -> bundle must not throw and must not register anything.
  const sandbox1 = { window: {}, console };
  sandbox1.window.__HERMES_PLUGINS__ = {
    register() { sandbox1._registered = true; },
  };
  try {
    vm.runInNewContext(code, sandbox1, { filename: "haos-dist-inert.js" });
  } catch (err) {
    fail("bundle threw without SDK globals: " + err.message);
  }
  results.checks.inert_without_sdk = !sandbox1._registered;
  if (sandbox1._registered) fail("registered despite missing SDK");
}

const fetchCalls = []; // every SDK.fetchJSON invocation: {url, method, body}
let registeredName = null;
let registeredComponent = null;

// 4g: when set, the next POST /acp/plan is held pending so the harness can
// observe the mid-flight "running" notice before the promise settles.
let holdAcpPost = false;
const acpHolds = [];

const FAKE_PAYLOAD = {
  taskboard: { view: "taskboard", total: 2,
               columns: [{ status: "ready", count: 1 }, { status: "done", count: 1 }],
               recent: [
                 { id: "t-9f3a", title: "Wire the connector", status: "ready" },
                 { id: "t-7c21", title: "Ship the dashboard", status: "done" },
               ] },
  approvals: { view: "approvals", pending: [
                 { id: "t-7c21", title: "Ship the dashboard", status: "done", approved: false },
               ], decision: false },
  memory_graph: { view: "memory_graph",
                  nodes: [{ type: "task.created", count: 1 }, { type: "task.completed", count: 1 }],
                  trace_edges: 3, correlation_edges: 2 },
  evolution_pending: [{ proposal_id: "evo-abc123" }],
  grants_pending: [{ scope: "ci", credential_ref: "svc:ci", requester: "build-bot" }],
};

{
  const h = makeReactStub().createElement;
  const hooks = makeHooksStub();
  const sdk = {
    React: { createElement: h },
    hooks,
    components: makeComponentsStub(h),
    utils: { cn: (...xs) => xs.filter(Boolean).join(" ") },
    fetchJSON(url, init) {
      const method = (init && init.method) || "GET";
      let body = null;
      if (init && init.body !== undefined) {
        try { body = JSON.parse(init.body); } catch (_e) { body = init.body; }
      }
      fetchCalls.push({ url, method, body });
      // Backend replies: POSTs resolve to a small ack; reloads (GET) resolve
      // to the full fake payload so the derived view re-renders. The ACP plan
      // POST replies with a REAL session shape so the harness can prove the
      // view surfaces the produced plan (delta 51), not just "ok".
      if (method === "POST") {
        if (holdAcpPost && url === "/api/plugins/haos/acp/plan") {
          return new Promise((resolve) => { acpHolds.push(resolve); });
        }
        if (url === "/api/plugins/haos/acp/plan") {
          return Promise.resolve({
            status: "planned",
            session_id: "sess-harness-1",
            agent: { name: "mock-acp-agent" },
            result: { result: [{ type: "text", text: "plano do harness visivel" }] },
          });
        }
        return Promise.resolve({ status: "ok" });
      }
      return Promise.resolve(FAKE_PAYLOAD);
    },
  };
  const registry = {
    register(name, comp) {
      registeredName = name;
      registeredComponent = comp;
    },
  };
  const sandbox2 = {
    window: { __HERMES_PLUGIN_SDK__: sdk, __HERMES_PLUGINS__: registry },
    console,
    Promise,
    setTimeout,
  };
  try {
    vm.runInNewContext(code, sandbox2, { filename: "haos-dist-live.js" });
  } catch (err) {
    fail("bundle threw with SDK present: " + err.message);
  }
}
results.checks.registered_name = registeredName;
if (registeredName !== "haos") fail("expected register('haos', …), got " + registeredName);
if (typeof registeredComponent !== "function") fail("registered component is not a function");

// ---------------------------------------------------------------------------
// Mount driver: render -> run first-render effects once -> flush microtasks,
// re-rendering whenever state changed (setState round trip), up to N passes.
// ---------------------------------------------------------------------------
function renderTree() {
  mountCtx.idx = 0;
  mountCtx.lastTree = registeredComponent();
  return mountCtx.lastTree;
}

async function flushTicks(n) {
  for (let i = 0; i < n; i++) await new Promise((r) => setTimeout(r, 0));
}

/** Run effects once (first render), then re-render until the tree settles. */
async function settle(maxPasses) {
  if (!mountCtx.effectsRun) {
    mountCtx.effectsRun = true;
    const effects = mountCtx.effects.slice();
    mountCtx.effects = [];
    for (const fn of effects) fn();
    await flushTicks(4);
  }
  for (let pass = 0; pass < (maxPasses || 20); pass++) {
    if (!mountCtx.dirty) break;
    mountCtx.dirty = false;
    renderTree();
    await flushTicks(2);
  }
}

/** Fresh interactive-node scan of the last rendered tree. */
function interactiveNodes() {
  const out = [];
  collectInteractive(mountCtx.lastTree, out);
  return out;
}

function walkAction(action) {
  return interactiveNodes().find((n) => n.props["data-action"] === action)
      || null;
}

// ---------------------------------------------------------------------------
// Check 3: mount the component, flush the initial state fetch and assert the
// derived texts render (delta 46 contract).
// ---------------------------------------------------------------------------
async function runChecks() {
  mountCtx = { hooks: [], effects: [], effectsRun: false, idx: 0, dirty: false };
  renderTree();
  await settle();

  const text = [];
  collectText(mountCtx.lastTree || renderTree(), text);
  const joined = text.join(" | ");
  results.checks.fetch_url = (fetchCalls[0] && fetchCalls[0].url) || null;
  if (!fetchCalls[0] || fetchCalls[0].url !== "/api/plugins/haos/state") {
    fail("expected fetchJSON('/api/plugins/haos/state') first, got "
         + JSON.stringify(fetchCalls[0]));
  }
  const expected = ["Total: 2", "Awaiting approval", "Event node kinds: 2",
                    "Proposals awaiting decision: 1", "evo-abc123",
                    "Wire the connector", "Ship the dashboard",
                    "task.created", "task.completed",
                    "trace edges: 3", "correlation edges: 2"];
  results.checks.rendered_texts = expected.filter((s) => joined.includes(s));
  for (const s of expected) {
    if (!joined.includes(s)) fail("rendered tree missing derived text: " + s);
  }

  // -------------------------------------------------------------------------
  // Check 4 (delta 49): interactive actions POST the real backend routes.
  // Type an operator, click each button, assert the exact POST calls.
  // -------------------------------------------------------------------------
  const posted = [];
  function recordPostedCalls() {
    for (const call of fetchCalls) {
      if (call.method === "POST") {
        posted.push(JSON.stringify({ url: call.url, body: call.body }));
      }
    }
  }
  const hasPost = (url, body) => posted.includes(
    JSON.stringify({ url, body: body === undefined ? null : body }));

  // 4a. Operator input exists and typing enables the approve buttons.
  let nodes = [];
  collectInteractive(mountCtx.lastTree, nodes);
  const operatorInput = nodes.find((n) => n.props["data-field"] === "operator");
  if (!operatorInput) fail("Actions card missing operator/approver input");
  if (operatorInput && typeof operatorInput.props.onChange === "function") {
    operatorInput.props.onChange({ target: { value: "ops-49" } });
    mountCtx.dirty = true;
    await settle();
  }

  // Re-render then walk the fresh tree for enabled action buttons.
  nodes = [];
  collectInteractive(mountCtx.lastTree, nodes);
  const byAction = {};
  for (const n of nodes) {
    const action = n.props["data-action"];
    if (action) byAction[action] = n;
  }
  results.checks.action_buttons = Object.keys(byAction).sort();
  const wantedButtons = ["dispatch", "acp-plan", "evolution-approve",
                         "evolution-reject", "grant-approve", "grant-revoke",
                         "review-approve", "review-reject"];
  for (const b of wantedButtons) {
    if (!byAction[b]) fail("missing action button: " + b);
  }

  // 4b. review approve + reject (delta 52).
  if (byAction["review-approve"]
      && typeof byAction["review-approve"].props.onClick === "function") {
    byAction["review-approve"].props.onClick();
    await flushTicks(3);
    await settle();
  }
  nodes = [];
  collectInteractive(mountCtx.lastTree, nodes);
  const revReject = nodes.find((n) => n.props["data-action"] === "review-reject");
  if (revReject && typeof revReject.props.onClick === "function") {
    revReject.props.onClick();
    await flushTicks(3);
    await settle();
  }

  // 4c. dispatch -> POST /dispatch with no body.
  nodes = [];
  collectInteractive(mountCtx.lastTree, nodes);
  const dispBtn = nodes.find((n) => n.props["data-action"] === "dispatch");
  if (dispBtn && typeof dispBtn.props.onClick === "function") {
    dispBtn.props.onClick();
    await flushTicks(3);
    await settle();
  }
  // 4c. evolution approve -> POST /evolution/decide {verdict approved}.
  if (byAction["evolution-approve"]
      && typeof byAction["evolution-approve"].props.onClick === "function") {
    byAction["evolution-approve"].props.onClick();
    await flushTicks(3);
    await settle();
  }
  // 4d. evolution reject (re-walk: tree re-rendered after clicks).
  nodes = [];
  collectInteractive(mountCtx.lastTree, nodes);
  const reject = nodes.find((n) => n.props["data-action"] === "evolution-reject");
  if (reject && typeof reject.props.onClick === "function") {
    reject.props.onClick();
    await flushTicks(3);
    await settle();
  }
  // 4e. grant approve + revoke (re-walk each time; view re-renders).
  nodes = [];
  collectInteractive(mountCtx.lastTree, nodes);
  const grantApprove = nodes.find((n) => n.props["data-action"] === "grant-approve");
  if (grantApprove && typeof grantApprove.props.onClick === "function") {
    grantApprove.props.onClick();
    await flushTicks(3);
    await settle();
  }
  nodes = [];
  collectInteractive(mountCtx.lastTree, nodes);
  const grantRevoke = nodes.find((n) => n.props["data-action"] === "grant-revoke");
  if (grantRevoke && typeof grantRevoke.props.onClick === "function") {
    grantRevoke.props.onClick();
    await flushTicks(3);
    await settle();
  }
  // 4f. acp plan -> POST /acp/plan {instruction: operator}.
  nodes = [];
  collectInteractive(mountCtx.lastTree, nodes);
  const acpPlan = nodes.find((n) => n.props["data-action"] === "acp-plan");
  if (acpPlan && typeof acpPlan.props.onClick === "function") {
    acpPlan.props.onClick();
    await flushTicks(3);
    await settle();
  }

  // 4g. Busy feedback (delta 49 hardening, regression #visual): a slow action
  // (ACP spawns its peer) must show IMMEDIATE progress — a "running" notice —
  // instead of looking dead while the POST is in flight. Hold the POST
  // promise, click, assert the running notice rendered mid-flight, then
  // release it and assert the success notice replaces it.
  holdAcpPost = true;
  nodes = [];
  collectInteractive(mountCtx.lastTree, nodes);
  const acpHoldBtn = nodes.find((n) => n.props["data-action"] === "acp-plan");
  if (acpHoldBtn && typeof acpHoldBtn.props.onClick === "function") {
    acpHoldBtn.props.onClick();
    // setState is synchronous in the stub: the running notice must be
    // visible WITHOUT waiting for the held POST to settle.
    const busyText = [];
    collectText(renderTree(), busyText);
    const busyJoined = busyText.join(" | ");
    results.checks.running_notice_midflight = busyJoined.includes("acp plan…");
    if (!results.checks.running_notice_midflight) {
      fail("no visible running notice while the POST is in flight: " + busyJoined);
    }
    // Release the held POST and confirm success replaces the running notice.
    if (acpHolds.length > 0) acpHolds[acpHolds.length - 1]({ status: "ok" });
    await flushTicks(3);
    await settle();
    const doneText = [];
    collectText(mountCtx.lastTree, doneText);
    const doneJoined = doneText.join(" | ");
    results.checks.success_notice_after_release = doneJoined.includes("acp plan ok");
    if (!results.checks.success_notice_after_release) {
      fail("success notice missing after the held POST released: " + doneJoined);
    }
  } else {
    fail("acp-plan button unavailable for busy-feedback check");
  }
  holdAcpPost = false;
  acpHolds.length = 0;

  recordPostedCalls();
  results.checks.posted_calls = posted;

  const expectReviewApprove = hasPost("/api/plugins/haos/reviews/decide",
    { task_id: "t-7c21", verdict: "approved", approver: "ops-49" });
  const expectReviewReject = hasPost("/api/plugins/haos/reviews/decide",
    { task_id: "t-7c21", verdict: "changes_requested", approver: "ops-49" });
  const expectDispatch = hasPost("/api/plugins/haos/dispatch", undefined);
  const expectApprove = hasPost("/api/plugins/haos/evolution/decide",
    { proposal_id: "evo-abc123", verdict: "approved", approver: "ops-49" });
  const expectReject = hasPost("/api/plugins/haos/evolution/decide",
    { proposal_id: "evo-abc123", verdict: "rejected", approver: "ops-49" });
  const expectGrantApprove = hasPost("/api/plugins/haos/grants/approve",
    { scope: "ci", credential_ref: "svc:ci", approver: "ops-49" });
  const expectGrantRevoke = hasPost("/api/plugins/haos/grants/revoke",
    { scope: "ci", credential_ref: "svc:ci" });
  const expectAcpPlan = hasPost("/api/plugins/haos/acp/plan",
    { instruction: "ops-49" });

  if (!expectReviewApprove) fail("review approve did not POST reviews/decide: " + JSON.stringify(posted));
  if (!expectReviewReject) fail("review reject did not POST reviews/decide: " + JSON.stringify(posted));
  if (!expectDispatch) fail("dispatch did not POST /dispatch: " + JSON.stringify(posted));
  if (!expectApprove) fail("approve did not POST decide/approved: " + JSON.stringify(posted));
  if (!expectReject) fail("reject did not POST decide/rejected: " + JSON.stringify(posted));
  if (!expectGrantApprove) fail("grant approve did not POST grants/approve: " + JSON.stringify(posted));
  if (!expectGrantRevoke) fail("grant revoke did not POST grants/revoke: " + JSON.stringify(posted));
  if (!expectAcpPlan) fail("acp plan did not POST /acp/plan: " + JSON.stringify(posted));

  // 5. Delta 51 (transparência): a resposta do /acp/plan (sessão real) fica
  //    VISÍVEL na view — session id, agente e o texto do plano — em vez de
  //    ser descartada após o notice "acp plan ok".
  const acpTexts = [];
  collectText(mountCtx.lastTree || renderTree(), acpTexts);
  const acpJoined = acpTexts.join(" | ");
  results.checks.acp_plan_visible =
    acpJoined.includes("Last ACP plan") &&
    acpJoined.includes("sess-harness-1") &&
    acpJoined.includes("mock-acp-agent") &&
    acpJoined.includes("plano do harness visivel");
  if (!results.checks.acp_plan_visible) {
    fail("ACP plan produced by the backend is not visible in the view: " + acpJoined);
  }

  // eslint-disable-next-line no-console
  console.log(JSON.stringify(results));
  process.exit(results.ok ? 0 : 1);
}

runChecks().catch((err) => {
  fail("mount failed: " + (err && err.stack || err));
  // eslint-disable-next-line no-console
  console.log(JSON.stringify(results));
  process.exit(1);
});
