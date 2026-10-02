// @vitest-environment jsdom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, expect, it, vi } from "vitest";
import { AutonomyPanel } from "./AutonomyPanel";
const mocks = vi.hoisted(() => ({ load: vi.fn(), pause: vi.fn(async () => ({ paused: true })), submit: vi.fn() }));
vi.mock("@/lib/framework", () => ({ loadAutonomy: mocks.load, pauseAutonomy: mocks.pause, submitInvestigation: mocks.submit }));
(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
afterEach(() => { vi.useRealTimers(); vi.clearAllMocks(); });
const snapshot = { config: { enabled: true, auto_apply: false }, queue: { available: true, paused: false, counts: { running: 1 }, recent_jobs: [{ job_id: "j1", status: "uncertain", result: { verification: "unknown" } }] }, service: { status: "stale" } };

it("confirms pause and shows uncertainty without claiming cancellation or repair", async () => {
  mocks.load.mockResolvedValue(snapshot);
  const container = document.createElement("div"); const root = createRoot(container);
  await act(async () => root.render(<AutonomyPanel profile="a" />));
  const button = (label: string) => Array.from(container.querySelectorAll("button")).find(item => item.textContent === label)!;
  expect(container.textContent).toContain("Service: stale");
  expect(container.textContent).toContain("uncertain");
  await act(async () => button("Pause queue").click());
  expect(mocks.pause).not.toHaveBeenCalled();
  expect(container.querySelector('[role="dialog"]')?.textContent).toContain("will not be cancelled");
  await act(async () => button("Confirm pause").click());
  expect(mocks.pause).toHaveBeenCalledWith("a", true, expect.any(AbortSignal));
  await act(async () => root.unmount());
});

it("bounds polling after settlement and aborts reads and mutations on profile remount", async () => {
  vi.useFakeTimers(); mocks.load.mockResolvedValue(snapshot);
  const container = document.createElement("div"); const root = createRoot(container);
  await act(async () => root.render(<AutonomyPanel key="a" profile="a" />));
  const signal = mocks.load.mock.calls[0][1] as AbortSignal;
  await act(async () => vi.advanceTimersByTimeAsync(9999));
  expect(mocks.load).toHaveBeenCalledTimes(1);
  await act(async () => vi.advanceTimersByTimeAsync(1));
  expect(mocks.load).toHaveBeenCalledTimes(2);
  await act(async () => root.render(<AutonomyPanel key="b" profile="b" />));
  expect(signal.aborted).toBe(true);
  expect(mocks.load.mock.calls.at(-1)?.[0]).toBe("b");
  await act(async () => root.unmount());
  expect((mocks.load.mock.calls.at(-1)?.[1] as AbortSignal).aborted).toBe(true);
});
