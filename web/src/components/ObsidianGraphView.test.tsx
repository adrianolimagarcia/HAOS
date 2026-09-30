// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ObsidianGraphView, type GraphEdge, type GraphNode } from "./ObsidianGraphView";

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
    measureText: () => ({ width: 50 }),
    createRadialGradient: () => ({ addColorStop: () => {} }),
  };
  HTMLCanvasElement.prototype.getContext = (() => ctx) as never;
}

beforeEach(() => {
  stubContext();
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue({
    width: 1000,
    height: 600,
    top: 0,
    left: 0,
    right: 1000,
    bottom: 600,
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

afterEach(async () => {
  if (root) {
    await act(async () => {
      root!.unmount();
    });
  }
  container?.remove();
  root = undefined;
  container = undefined;
  vi.unstubAllGlobals();
});

const SAMPLE_NODES: GraphNode[] = [
  { id: "note:1", kind: "note", label: "Note One", group: "Obsidian Notes" },
  { id: "entity:1", kind: "entity", label: "Entity One", group: "GraphRAG Knowledge" },
  { id: "fact:1", kind: "store", label: "Fact One", group: "Canonical DB" },
];

const SAMPLE_EDGES: GraphEdge[] = [
  { id: "e1", source: "note:1", target: "entity:1", kind: "references" },
];

describe("ObsidianGraphView", () => {
  it("renders canvas and obsidian-style control panel with Groups and Forces", async () => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);

    const onSelect = vi.fn();

    await act(async () => {
      root!.render(
        <ObsidianGraphView
          nodes={SAMPLE_NODES}
          edges={SAMPLE_EDGES}
          selectedId={null}
          onSelect={onSelect}
        />
      );
    });

    expect(container.querySelector("canvas")).not.toBeNull();
    expect(container.textContent).toContain("Graph view");
    expect(container.textContent).toContain("Groups");
    expect(container.textContent).toContain("Obsidian Notes");
    expect(container.textContent).toContain("GraphRAG Knowledge");
    expect(container.textContent).toContain("Display");
    expect(container.textContent).toContain("Forces");
    expect(container.textContent).toContain("Repel force (Distanciamento)");
    expect(container.textContent).toContain("Link distance (Comprimento)");
  });

  it("allows toggling group filters and controls", async () => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);

    await act(async () => {
      root!.render(
        <ObsidianGraphView
          nodes={SAMPLE_NODES}
          edges={SAMPLE_EDGES}
          selectedId={null}
          onSelect={vi.fn()}
        />
      );
    });

    const groupItem = container.querySelector<HTMLDivElement>(".obs-group-item");
    expect(groupItem).not.toBeNull();
    await act(async () => {
      groupItem!.click();
    });
    expect(groupItem!.classList.contains("is-disabled")).toBe(true);
  });

  it("handles pointer interaction and click without crashing", async () => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);

    const onSelect = vi.fn();

    await act(async () => {
      root!.render(
        <ObsidianGraphView
          nodes={SAMPLE_NODES}
          edges={SAMPLE_EDGES}
          selectedId={null}
          onSelect={onSelect}
        />
      );
    });

    const canvas = container.querySelector<HTMLCanvasElement>("canvas");
    expect(canvas).not.toBeNull();

    await act(async () => {
      canvas!.dispatchEvent(
        new PointerEvent("pointermove", { clientX: 500, clientY: 300, bubbles: true })
      );
      canvas!.dispatchEvent(
        new PointerEvent("pointerdown", { clientX: 500, clientY: 300, bubbles: true })
      );
      canvas!.dispatchEvent(
        new PointerEvent("pointerup", { clientX: 500, clientY: 300, bubbles: true })
      );
    });

    expect(canvas!.style.cursor).toBeDefined();
  });
});
