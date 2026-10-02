// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import MemoryGraphPage from "./MemoryGraphPage";

const api = vi.hoisted(() => ({ fetchJSON: vi.fn() }));
vi.mock("@/lib/api", () => ({ fetchJSON: api.fetchJSON }));
vi.mock("@/contexts/useProfileScope", () => ({ useProfileScope: () => ({ profile: "" }) }));

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
let root: Root | undefined;
let container: HTMLDivElement | undefined;

function stubContext() {
  const ctx = {
    setTransform: () => {},
    clearRect: () => {},
    fillRect: () => {},
    beginPath: () => {},
    moveTo: () => {},
    lineTo: () => {},
    closePath: () => {},
    stroke: () => {},
    fill: () => {},
    arc: () => {},
    save: () => {},
    restore: () => {},
    translate: () => {},
    rotate: () => {},
    scale: () => {},
    strokeText: () => {},
    fillText: () => {},
    measureText: () => ({ width: 40 }),
    createRadialGradient: () => ({ addColorStop: () => {} }),
  };
  HTMLCanvasElement.prototype.getContext = (() => ctx) as never;
}

beforeEach(() => {
  stubContext();
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue({
    width: 1200,
    height: 640,
    top: 0,
    left: 0,
    right: 1200,
    bottom: 640,
    x: 0,
    y: 0,
    toJSON: () => ({}),
  } as DOMRect);
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  );
});

async function renderPage() {
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  await act(async () => {
    root!.render(<MemoryGraphPage />);
  });
}

afterEach(async () => {
  if (root) {
    await act(async () => {
      root!.unmount();
    });
  }
  container?.remove();
  root = undefined;
  container = undefined;
  api.fetchJSON.mockReset();
  vi.unstubAllGlobals();
});

describe("MemoryGraphPage", () => {
  it("renders federated memory graph topology and stats cards", async () => {
    api.fetchJSON.mockImplementation(async (url: string) => {
      if (url.includes("/api/memory/graph/overview")) {
        return {
          active_provider: "holographic",
          fabric: { records: 53, projection_acks: 212, outbox_pending: 0, path: "/root/.haos/memory/fabric.db" },
          graphrag: { entities: 186, relations: 150, communities: 7, path: "/root/.haos/memory/graphrag.db" },
          obsidian: { total_notes: 95, folders: { "10-Memory": 40, "20-Architecture": 55 }, path: "/root/.haos/obsidian_vault" },
          decisions: { total: 17, path: "/root/.haos/memory/decisions.db" },
          graphify: { nodes: 1986, edges: 13543, file_count: 140, path: ".haos/graphify-out/graph.json" },
          providers: [{ name: "holographic", status: "ready" }, { name: "byterover", status: "ready" }],
        };
      }
      if (url.includes("/api/memory/graph")) {
        return {
          nodes: [
            {
              id: "canonical:fabric",
              label: "Canonical Journal",
              kind: "database",
              subsystem: "canonical",
              tier: "canonical",
              status: "ready",
              item_count: 53,
              path: "/root/.haos/memory/fabric.db",
              desc: "SQLite append-only ledger",
            },
            {
              id: "proj:obsidian",
              label: "Obsidian Vault",
              kind: "obsidian",
              subsystem: "obsidian",
              tier: "projection",
              status: "ready",
              item_count: 95,
              path: "/root/.haos/obsidian_vault",
              desc: "Markdown notes projection",
            },
          ],
          edges: [
            {
              source: "canonical:fabric",
              target: "proj:obsidian",
              kind: "projected_to",
              label: "syncs notes",
              desc: "Outbox projection",
            },
          ],
          stats: {
            canonical_records: 53,
            obsidian_notes: 95,
            decisions: 17,
            graphrag_entities: 186,
            graphrag_relations: 150,
            graphrag_communities: 7,
            graphify_nodes: 1986,
            graphify_edges: 13543,
            active_providers: ["holographic", "byterover"],
          },
          projections: [
            { name: "obsidian", status: "active", count: 95, path: "/root/.haos/obsidian_vault" },
            { name: "graphrag", status: "active", count: 186, path: "/root/.haos/memory/graphrag.db" },
          ],
          scopes: ["global", "project", "team", "private"],
          updated_at: 1790727000,
        };
      }
      return {};
    });

    await renderPage();

    expect(container!.textContent).toContain("Graph Memory");
    expect(container!.textContent).toContain("Topologia multi-camadas");
    expect(container!.textContent).toContain("CANONICAL SQLITE (FABRIC)");
    expect(container!.textContent).toContain("GRAPHRAG STORE");
    expect(container!.textContent).toContain("OBSIDIAN VAULT");
    expect(container!.textContent).toContain("GRAPHIFY (CODE KG)");
    expect(container!.textContent).toContain("53");
    expect(container!.textContent).toContain("186");
    expect(container!.textContent).toContain("95");
    expect(container!.textContent).toContain("1986");
    expect(container!.textContent).toContain("holographic");
  });

  it("renders Powerline Context HUD and Agent & Workflow Mesh tab", async () => {
    api.fetchJSON.mockImplementation(async (url: string) => {
      if (url.includes("/api/memory/graph/overview")) {
        return {
          active_provider: "holographic",
          fabric: { records: 50, projection_acks: 200, outbox_pending: 0, path: "" },
          graphrag: { entities: 100, relations: 80, communities: 5, path: "" },
          obsidian: { total_notes: 40, folders: {}, path: "" },
          decisions: { total: 10, path: "" },
          graphify: { nodes: 100, edges: 200, file_count: 10, path: "" },
          providers: [{ name: "holographic", status: "ready" }],
          hud: {
            memory_health_pct: 98.5,
            active_goal: "Parent-Child Chunking & Agent Mesh",
            avg_confidence: 0.88,
            active_agents: 4,
            dream_queue: 12,
            conflicts_count: 0,
            system_status: "optimal",
          },
        };
      }
      if (url.includes("/api/memory/graph")) {
        return {
          nodes: [
            {
              id: "agent:rust_edge",
              label: "Rust Edge Engine",
              kind: "agent",
              status: "running",
            },
          ],
          edges: [],
          stats: {},
        };
      }
      return {};
    });

    await renderPage();

    expect(container!.textContent).toContain("HAOS CORE");
    expect(container!.textContent).toContain("MEMORY:");
    expect(container!.textContent).toContain("99%");
    expect(container!.textContent).toContain("GOAL:");
    expect(container!.textContent).toContain("Active");
    expect(container!.textContent).toContain("CONF:");
    expect(container!.textContent).toContain("88%");
    expect(container!.textContent).toContain("AGENTS:");
    expect(container!.textContent).toContain("4");
    expect(container!.textContent).toContain("DREAM:");
    expect(container!.textContent).toContain("12");
    expect(container!.textContent).toContain("NO CONFLICTS");

    const goalSeg = container!.querySelector(".mem-hud-goal");
    expect(goalSeg?.getAttribute("title")).toContain("Parent-Child Chunking & Agent Mesh");

    // Check Agent & Workflow Mesh tab
    const tabs = Array.from(container!.querySelectorAll<HTMLButtonElement>("button.mem-tab"));
    const agentTab = tabs.find((t) => t.textContent?.includes("Agent & Workflow Mesh"));
    expect(agentTab).toBeDefined();

    await act(async () => {
      agentTab!.click();
    });

    expect(container!.textContent).toContain("Agent & Workflow Mesh (ADK / Ruflo / Herdr)");
  });

  it("handles loading error gracefully and allows retry", async () => {
    api.fetchJSON.mockRejectedValue(new Error("Network failed"));
    await renderPage();

    expect(container!.textContent).toContain("Falha ao carregar grafo de memória: Network failed");

    api.fetchJSON.mockResolvedValue({
      active_provider: "holographic",
      fabric: { records: 0, projection_acks: 0, outbox_pending: 0, path: "" },
      graphrag: { entities: 0, relations: 0, communities: 0, path: "" },
      obsidian: { total_notes: 0, folders: {}, path: "" },
      decisions: { total: 0, path: "" },
      graphify: { nodes: 0, edges: 0, file_count: 0, path: "" },
      providers: [],
      nodes: [],
      edges: [],
      stats: {
        canonical_records: 0,
        obsidian_notes: 0,
        decisions: 0,
        graphrag_entities: 0,
        graphrag_relations: 0,
        graphrag_communities: 0,
        graphify_nodes: 0,
        graphify_edges: 0,
        active_providers: [],
      },
      projections: [],
      scopes: [],
      updated_at: 1790727000,
    });

    const refreshBtn = container!.querySelector<HTMLButtonElement>("button.mem-refresh-btn");
    expect(refreshBtn).not.toBeNull();
    await act(async () => {
      refreshBtn!.click();
    });

    expect(container!.textContent).toContain("Graph Memory");
  });
});
