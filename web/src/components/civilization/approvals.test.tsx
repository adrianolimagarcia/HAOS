// @vitest-environment jsdom
// Plan: exercise real scoped API paths, deferred A→B→A responses, and rendered analytics/replay.
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fetchJSON } from "../../lib/api";
import { ApprovalCenter } from "./ApprovalCenter";
import { MissionInsights } from "./MissionInsights";

vi.mock("../../lib/api", () => ({ fetchJSON: vi.fn() }));
(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let container: HTMLDivElement;
let root: Root;
const request = vi.mocked(fetchJSON);

beforeEach(() => {
  request.mockReset();
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
});
afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
  vi.restoreAllMocks();
});

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: Error) => void;
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
function button(label: string): HTMLButtonElement {
  const found = Array.from(container.querySelectorAll("button")).find(item => item.textContent === label);
  expect(found, `button ${label}`).toBeDefined();
  return found!;
}
async function click(label: string) {
  await act(async () => button(label).click());
}
async function reason(value: string) {
  await act(async () => {
    const input = container.querySelector("input")!;
    // A browser edit bypasses React's programmatic value tracker.
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(input, value);
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
}
function approvals(description: string, status = "PENDING") {
  return {
    approvals: [{ approval_id: "gate/a b", mission_id: null, agent_id: null, gate: null,
      risk_class: null, description, requested_at: 0, status }],
    quality: "recorded", limitations: ["Decision records do not imply execution."],
  };
}
function analytics(tokens: number | null) {
  return { mission_id: "mission/a b", duration_seconds: null, tokens, failures: 0,
    retries: null, event_count: 0, quality: "partial", limitations: ["Duration is not recorded."] };
}
const events = { events: [{ time: 0, type: "mission_status", agent_id: "agent-a",
  description: "Recorded endpoint event", status: "RUNNING" }] };

describe("approval center", () => {
  it("GETs global approvals without mission_id and displays missionless pending records only", async () => {
    const pending = approvals("Missionless pending", "pending");
    request.mockResolvedValue({ ...pending, approvals: [...pending.approvals,
      { ...pending.approvals[0], approval_id: "done", description: "Already decided", status: "APPROVED" }] });
    await act(async () => root.render(<ApprovalCenter profile="owner & one" />));
    expect(request).toHaveBeenCalledExactlyOnceWith("/api/civilization/approvals?profile=owner%20%26%20one", {
      signal: expect.any(AbortSignal),
    });
    expect(container.textContent).toContain("Missionless pending");
    expect(container.textContent).toContain("No mission");
    expect(container.textContent).not.toContain("Already decided");
    expect(container.textContent).toContain("no runtime is resumed");
  });

  it.each([{ label: "Approve", approved: true }, { label: "Reject", approved: false }])(
    "confirms $label and posts a boolean decision and reason without claiming runtime resumed",
    async ({ label, approved }) => {
      request.mockResolvedValueOnce(approvals("Review this gate"));
      const decision = deferred<unknown>();
      const refresh = deferred<unknown>();
      request.mockReturnValueOnce(decision.promise).mockReturnValueOnce(refresh.promise);
      const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
      await act(async () => root.render(<ApprovalCenter profile="owner/a" />));
      await reason("Operator reviewed risk");
      await click(label);
      expect(confirm).toHaveBeenCalledWith(expect.stringContaining("does not resume runtime"));
      expect(request).toHaveBeenCalledTimes(1);
      confirm.mockReturnValue(true);
      await click(label);
      expect(request.mock.calls[1]).toEqual(["/api/civilization/approvals/gate%2Fa%20b/decision?profile=owner%2Fa", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ approved, reason: "Operator reviewed risk" }),
      }]);
      expect(container.querySelector('[aria-busy="true"]')).not.toBeNull();
      expect(button("Approve").disabled).toBe(true);
      expect(button("Reject").disabled).toBe(true);
      expect(container.querySelector("input")!.disabled).toBe(true);
      await click(label);
      expect(request).toHaveBeenCalledTimes(2);
      await act(async () => decision.resolve({ runtime_resumed: false }));
      expect(request).toHaveBeenCalledTimes(3);
      expect(request.mock.calls[2][0]).toBe("/api/civilization/approvals?profile=owner%2Fa");
      await act(async () => refresh.resolve({ ...approvals("unused"), approvals: [] }));
      expect(container.textContent).toContain("No pending approvals.");
      expect(container.textContent).toContain("Decisions are recorded only; no runtime is resumed.");
      expect(container.textContent).not.toMatch(/runtime (?:has been |was )?resumed/i);
    },
  );

  it.each(["success", "error"])("ignores obsolete scoped GET %s after A→B→A", async outcome => {
    const first = deferred<unknown>();
    const second = deferred<unknown>();
    const current = deferred<unknown>();
    request.mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise).mockReturnValueOnce(current.promise);
    await act(async () => root.render(<ApprovalCenter profile="A" missionId="mission/a b" />));
    await act(async () => root.render(<ApprovalCenter profile="B" missionId="mission-b" />));
    await act(async () => root.render(<ApprovalCenter profile="A" missionId="mission/a b" />));
    expect(request.mock.calls.map(([url]) => url)).toEqual([
      "/api/civilization/approvals?mission_id=mission%2Fa%20b&profile=A",
      "/api/civilization/approvals?mission_id=mission-b&profile=B",
      "/api/civilization/approvals?mission_id=mission%2Fa%20b&profile=A",
    ]);
    expect((request.mock.calls[0][1]!.signal as AbortSignal).aborted).toBe(true);
    expect((request.mock.calls[1][1]!.signal as AbortSignal).aborted).toBe(true);
    await act(async () => current.resolve(approvals("Current A")));
    // The transport mock deliberately ignores abort, exercising the UI's stale-response guard.
    await act(async () => {
      if (outcome === "success") { first.resolve(approvals("Obsolete A")); second.resolve(approvals("Obsolete B")); }
      else { first.reject(new Error("Obsolete A failure")); second.reject(new Error("Obsolete B failure")); }
    });
    expect(container.textContent).toContain("Current A");
    expect(container.textContent).not.toContain("Obsolete");
    expect(container.querySelector('[role="alert"]')).toBeNull();
  });

  it.each(["success", "error"])("ignores obsolete decision %s after A→B→A", async outcome => {
    const oldDecision = deferred<unknown>();
    request.mockResolvedValue(approvals("Current gate"));
    vi.spyOn(window, "confirm").mockReturnValue(true);
    await act(async () => root.render(<ApprovalCenter profile="A" />));
    request.mockReturnValueOnce(oldDecision.promise);
    await click("Approve");
    await act(async () => root.render(<ApprovalCenter profile="B" />));
    await act(async () => root.render(<ApprovalCenter profile="A" />));
    const callsBeforeSettlement = request.mock.calls.length;
    await act(async () => {
      if (outcome === "success") oldDecision.resolve({});
      else oldDecision.reject(new Error("Obsolete decision failure"));
    });
    expect(request).toHaveBeenCalledTimes(callsBeforeSettlement);
    expect(container.querySelector('[role="alert"]')).toBeNull();
    expect(button("Approve").disabled).toBe(false);
    expect(container.textContent).toContain("Current gate");
  });
});

describe("mission insights", () => {
  it("renders null analytics as Unknown, preserves zero, and replays events from the events endpoint", async () => {
    request.mockResolvedValueOnce(analytics(0)).mockResolvedValueOnce(events);
    await act(async () => root.render(<MissionInsights profile="owner & one" missionId="mission/a b" />));
    expect(request.mock.calls.map(([url]) => url)).toEqual([
      "/api/civilization/missions/mission%2Fa%20b/analytics?profile=owner%20%26%20one",
      "/api/civilization/missions/mission%2Fa%20b/events?profile=owner%20%26%20one",
    ]);
    const values = Object.fromEntries(Array.from(container.querySelectorAll("dl > div"))
      .map(row => [row.querySelector("dt")!.textContent, row.querySelector("dd")!.textContent]));
    expect(values).toEqual({ "Total duration (seconds)": "Unknown", tokens: "0", failures: "0", retries: "Unknown", "event count": "0" });
    expect(container.textContent).toContain("Duration is not recorded.");
    expect(container.querySelector("output")!.textContent).toBe("0 / 1");
    await click("End");
    expect(container.querySelector('[aria-label="Replayed events"]')!.textContent).toContain("Recorded endpoint event");
  });

  it.each(["success", "error"])("suppresses stale analytics and event %s after A→B→A", async outcome => {
    const obsolete = Array.from({ length: 4 }, () => deferred<unknown>());
    for (const item of obsolete) request.mockReturnValueOnce(item.promise);
    request.mockResolvedValueOnce(analytics(0)).mockResolvedValueOnce(events);
    await act(async () => root.render(<MissionInsights profile="A" missionId="mission-a" />));
    await act(async () => root.render(<MissionInsights profile="B" missionId="mission-b" />));
    await act(async () => root.render(<MissionInsights profile="A" missionId="mission-a" />));
    expect(request.mock.calls.map(([url]) => url)).toEqual([
      "/api/civilization/missions/mission-a/analytics?profile=A", "/api/civilization/missions/mission-a/events?profile=A",
      "/api/civilization/missions/mission-b/analytics?profile=B", "/api/civilization/missions/mission-b/events?profile=B",
      "/api/civilization/missions/mission-a/analytics?profile=A", "/api/civilization/missions/mission-a/events?profile=A",
    ]);
    await act(async () => obsolete.forEach((item, index) => {
      if (outcome === "error") item.reject(new Error("Obsolete insights failure"));
      else item.resolve(index % 2 === 0 ? analytics(999) : { events: [] });
    }));
    expect(container.querySelector('[role="alert"]')).toBeNull();
    expect(container.textContent).not.toContain("999");
    expect(container.querySelector("output")!.textContent).toBe("0 / 1");
    await click("End");
    expect(container.textContent).toContain("Recorded endpoint event");
  });
});
