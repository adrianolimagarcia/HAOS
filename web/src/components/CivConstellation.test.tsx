// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { CivConstellation, colorOf, radiusOf, type StarEdge, type StarNode } from "./CivConstellation";

/**
 * Exercises the real component against a stubbed 2D context, so a regression
 * that paints an empty frame — the failure mode that shipped twice — fails
 * here instead of only in the browser.
 */

const NODES: StarNode[] = [
  { id: "council:arch", kind: "council", label: "arch", status: "active" },
  { id: "bot:architect-bot", kind: "bot", label: "architect-bot", status: "active" },
  { id: "bot:security-bot", kind: "bot", label: "security-bot", status: "idle" },
  { id: "decision:dec-1", kind: "decision", label: "dec-1", status: "done" },
];

const EDGES: StarEdge[] = [
  { id: "e1", source: "council:arch", target: "bot:architect-bot", kind: "has_member", directed: true },
  { id: "e2", source: "council:arch", target: "bot:security-bot", kind: "has_member", directed: true },
  { id: "e3", source: "council:arch", target: "decision:dec-1", kind: "produced", directed: true },
];

/**
 * The exact node kinds emitted by GET /api/civilization/graph against the
 * production event store. "constitution" is deliberate: it is a real kind the
 * API returns, and leaving it unhandled made every node's radius NaN, which
 * painted a completely blank canvas with no visible error.
 */
const REAL_PAYLOAD_NODES: StarNode[] = (
  ["bot", "bot", "constitution", "council", "decision", "proposal"] as StarNode["kind"][]
).map((kind, i) => ({ id: `${kind}:n${i}`, kind, label: `${kind} ${i}`, status: "active" }));

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
let root: Root | undefined;
let container: HTMLDivElement | undefined;

function stubContext() {
  const calls: Record<string, number> = {};
  const bump = (name: string) => { calls[name] = (calls[name] ?? 0) + 1; };
  const ctx = {
    setTransform: () => bump("setTransform"),
    clearRect: () => bump("clearRect"),
    fillRect: () => bump("fillRect"),
    beginPath: () => bump("beginPath"),
    moveTo: () => bump("moveTo"),
    lineTo: () => bump("lineTo"),
    closePath: () => bump("closePath"),
    stroke: () => bump("stroke"),
    fill: () => bump("fill"),
    arc: () => bump("arc"),
    save: () => bump("save"),
    restore: () => bump("restore"),
    translate: () => bump("translate"),
    rotate: () => bump("rotate"),
    strokeText: () => bump("strokeText"),
    fillText: () => bump("fillText"),
    createRadialGradient: () => ({ addColorStop: () => { bump("addColorStop"); } }),
  };
  HTMLCanvasElement.prototype.getContext = (() => ctx) as never;
  return calls;
}

beforeEach(() => {
  container = document.createElement("div");
  document.body.appendChild(container);
  // jsdom reports a 0x0 box for everything; a real 1200x640 box is required or
  // the renderer legitimately bails out before drawing.
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue({
    width: 1200, height: 640, top: 0, left: 0, right: 1200, bottom: 640, x: 0, y: 0,
    toJSON: () => ({}),
  } as DOMRect);
  vi.stubGlobal("ResizeObserver", class {
    observe() {}
    unobserve() {}
    disconnect() {}
  });
});

afterEach(() => {
  act(() => { root?.unmount(); });
  root = undefined;
  container?.remove();
  container = undefined;
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

function renderConstellation() {
  act(() => {
    root = createRoot(container as HTMLDivElement);
    root.render(
      <CivConstellation nodes={NODES} edges={EDGES} selectedId={null} onSelect={() => {}} />,
    );
  });
}

describe("CivConstellation", () => {
  it("paints a non-empty frame with every node", async () => {
    const calls = stubContext();
    renderConstellation();

    // Drive one real animation frame; the render loop paints on RAF.
    await act(async () => {
      await new Promise<void>(resolve => requestAnimationFrame(() => resolve()));
    });

    // The backdrop alone would only set fillRect; the node pass must also run.
    expect(calls.fillRect ?? 0).toBeGreaterThan(0);
    expect(calls.arc ?? 0).toBeGreaterThanOrEqual(NODES.length);
    expect(calls.strokeText ?? 0).toBeGreaterThan(0);
  });

  it("sizes the canvas to the real box, not 0x0", () => {
    stubContext();
    renderConstellation();
    const canvas = container?.querySelector("canvas") as HTMLCanvasElement;
    expect(canvas).toBeTruthy();
    expect(canvas.width).toBeGreaterThan(0);
    expect(canvas.height).toBeGreaterThan(0);
  });

  it("paints every node of a real API payload, including unknown kinds", async () => {
    const calls = stubContext();
    act(() => {
      root = createRoot(container as HTMLDivElement);
      root.render(
        <CivConstellation
          nodes={REAL_PAYLOAD_NODES}
          edges={[]}
          selectedId={null}
          onSelect={() => {}}
        />,
      );
    });

    await act(async () => {
      await new Promise<void>(resolve => requestAnimationFrame(() => resolve()));
    });

    // Backdrop + starfield + rings + core + one bloom/core arc per node.
    // Before the fix this was 0 for the constitution-bearing payload.
    expect(calls.arc ?? 0).toBeGreaterThan(REAL_PAYLOAD_NODES.length);
    expect(calls.strokeText ?? 0).toBeGreaterThan(0);
  });

  it("gives every API kind a finite radius and a colour", () => {
    // This is the actual guarantee that was violated: an unknown kind used to
    // yield undefined, producing NaN coordinates and a blank canvas.
    for (const node of REAL_PAYLOAD_NODES) {
      expect(Number.isFinite(radiusOf(node.kind))).toBe(true);
      expect(radiusOf(node.kind)).toBeGreaterThan(0);
      expect(colorOf(node.kind)).toHaveLength(3);
    }
    // Including a kind that does not exist at all.
    expect(Number.isFinite(radiusOf("some_future_kind"))).toBe(true);
    expect(radiusOf("some_future_kind")).toBeGreaterThan(0);
    expect(colorOf("some_future_kind")).toHaveLength(3);
  });
});
