// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { MissionEvent } from "../../lib/civilization-contracts";
import { MissionReplay } from "./MissionReplay";
import { initialReplayState, reconstructReplay, replayEventReducer } from "./replay";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const events: MissionEvent[] = [
  { time: 20, seq: 8, type: "node_started", agent_id: "agent-a", description: "First", status: "running", node_id: "node-a", mission_status: "running" },
  { time: 10, seq: 2, type: "node_completed", agent_id: "node-b", description: "Second", status: "done" },
  { time: 10, seq: 2, type: "node_completed", agent_id: "agent-a", description: "Third", status: "done", node_id: "node-a", mission_status: "completed" },
];
let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  vi.useFakeTimers();
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
  vi.useRealTimers();
});

async function render(input: readonly MissionEvent[] = events, missionId = "a") {
  await act(async () => root.render(<MissionReplay events={input} missionId={missionId} />));
}

async function click(label: string) {
  const button = Array.from(container.querySelectorAll("button")).find((item) => item.textContent === label);
  expect(button).toBeDefined();
  await act(async () => button!.click());
}

function position() {
  return container.querySelector("output")?.textContent;
}

async function advance(milliseconds: number) {
  await act(async () => vi.advanceTimersByTime(milliseconds));
}

async function speed(value: string) {
  await act(async () => {
    const select = container.querySelector("select")!;
    select.value = value;
    select.dispatchEvent(new Event("change", { bubbles: true }));
  });
}

describe("mission replay", () => {
  it("reduces immutable, ordered prefixes using only explicit historical fields", () => {
    const initial = initialReplayState();
    const first = replayEventReducer(initial, events[0]);
    expect(initial.nodeStatuses).toEqual({});
    expect(initial.missionStatus).toBeNull();
    expect(first.nodeStatuses).toEqual({ "node-a": "running" });
    expect(reconstructReplay(events, 2)).toEqual(first);
    expect(reconstructReplay(events, 3)).toEqual({ nodeStatuses: { "node-a": "done" }, missionStatus: "completed" });
    expect(reconstructReplay(events, 1)).toEqual(first);
    expect(reconstructReplay(events, -1)).toEqual(initial);
    expect(reconstructReplay(events, 999)).toEqual(reconstructReplay(events, 3));
    const ambiguous = { ...events[0], node_id: undefined, mission_status: undefined };
    expect(reconstructReplay([ambiguous], 1)).toEqual(initial);
    expect(reconstructReplay([{ ...events[0], status: "", mission_status: undefined }], 1)).toEqual(initial);
    expect(events.map((event) => event.seq)).toEqual([8, 2, 2]);
  });

  it("seeks prefixes, preserves event order, and never infers missing node state", async () => {
    await render();
    expect(position()).toBe("0 / 3");
    expect(container.textContent).toContain("Historical node state unavailable");
    await click("Next");
    expect(position()).toBe("1 / 3");
    expect(container.querySelector('[aria-label="Historical node states"]')?.textContent).toBe("node-a: running");
    await click("End");
    expect(position()).toBe("3 / 3");
    expect(Array.from(container.querySelectorAll('[aria-label="Replayed events"] li p')).map((item) => item.textContent)).toEqual(["First", "Second", "Third"]);
    expect(container.querySelector('[aria-label="Historical node states"]')?.textContent).toBe("node-a: done");
    await click("Previous");
    expect(container.textContent).toContain("Historical mission status: running");
    await act(async () => {
      const slider = container.querySelector("input")!;
      // React's native setter bypasses its value tracker, matching a user interaction.
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(slider, "1");
      slider.dispatchEvent(new Event("input", { bubbles: true }));
      slider.dispatchEvent(new Event("change", { bubbles: true }));
    });
    expect(position()).toBe("1 / 3");
    await click("Start");
    expect(position()).toBe("0 / 3");
    expect(container.textContent).toContain("Historical node state unavailable");
  });

  it("plays at 1/2/10x, pauses, stops at end and cleans up mission switches and unmount", async () => {
    await render();
    await click("Play");
    await advance(999);
    expect(position()).toBe("0 / 3");
    await advance(1);
    expect(position()).toBe("1 / 3");
    await click("Pause");
    await advance(5000);
    expect(position()).toBe("1 / 3");
    await speed("2");
    await click("Play");
    await advance(500);
    expect(position()).toBe("2 / 3");
    await speed("10");
    await advance(100);
    expect(position()).toBe("3 / 3");
    expect(vi.getTimerCount()).toBe(0);
    await click("Start");
    await click("Play");
    await render(events, "b");
    expect(position()).toBe("0 / 3");
    expect(vi.getTimerCount()).toBe(0);
    await click("Play");
    await act(async () => root.unmount());
    expect(vi.getTimerCount()).toBe(0);
    root = createRoot(container);
    await render([]);
    expect(container.textContent).toContain("No mission events available");
    expect(container.querySelector("input")?.disabled).toBe(true);
    expect(Array.from(container.querySelectorAll("button")).every((button) => button.disabled)).toBe(true);
  });
});
