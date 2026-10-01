// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";
import CivilizationPage from "./CivilizationPage";

const api = vi.hoisted(() => ({ fetchJSON: vi.fn() }));
vi.mock("@/lib/api", () => ({ fetchJSON: api.fetchJSON }));
vi.mock("@/contexts/useProfileScope", () => ({ useProfileScope: () => ({ profile: "" }) }));

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
let root: Root | undefined;
let container: HTMLDivElement | undefined;

async function renderPage() {
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  await act(async () => { root!.render(<CivilizationPage/>); });
}

afterEach(async () => {
  if (root) await act(async () => { root!.unmount(); });
  container?.remove();
  root = undefined;
  container = undefined;
  api.fetchJSON.mockReset();
});

describe("Civilization observatory", () => {
  it("renders only explicit parent and council relationships", async () => {
    api.fetchJSON.mockResolvedValue({
      bots: [{ bot_id: "bot-a", name: "Architect", status: "active", version: 1, bundle_hash: "hash" }],
      councils: [{ council_id: "council-a", purpose: "Review", members: ["bot-a"], version: 1 }],
      leaves: [{ leaf_id: "leaf-a", parent_bot_id: "bot-a", council_id: null, council_session_id: null, status: "active" },
        { leaf_id: "orphan", parent_bot_id: "unknown", council_id: null, council_session_id: null, status: "active" }],
      decisions: [{ decision_id: "decision-a", council_id: "council-a", council_session_id: "session-a", created_at: "2026-01-01" }],
      events: [{ seq: 1, name: "bot.created", timestamp: "2026-01-01T00:00:00Z" }], cursor: 1,
    });
    await renderPage();
    expect(container!.querySelector(".civ-branch .civ-children")!.textContent).toContain("leaf-a");
    expect(container!.textContent).toContain("pai não registrado: unknown");
    expect(container!.textContent).toContain("decision-a");
    expect(container!.textContent).toContain("bot.created");
    expect(api.fetchJSON).toHaveBeenCalledWith("/api/civilization/overview?profile=", expect.objectContaining({ signal: expect.any(AbortSignal) }));
  });

  it("separates an API failure from a genuinely empty event store", async () => {
    api.fetchJSON.mockRejectedValueOnce(new Error("backend offline"));
    await renderPage();
    expect(container!.textContent).toContain("Não foi possível atualizar");
    expect(container!.textContent).toContain("Nenhum estado foi carregado");
    expect(container!.textContent).not.toContain("Nenhuma identidade registrada");
    api.fetchJSON.mockResolvedValueOnce({ bots: [], councils: [], leaves: [], decisions: [], events: [], cursor: 0 });
    await act(async () => { container!.querySelector<HTMLButtonElement>("button[aria-label='Atualizar observatório']")!.click(); });
    expect(container!.textContent).toContain("Nenhuma identidade registrada");
  });
});
