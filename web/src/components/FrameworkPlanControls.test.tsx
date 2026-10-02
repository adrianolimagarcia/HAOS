// @vitest-environment jsdom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { expect, it, vi } from "vitest";
import { FrameworkPlanControls } from "./FrameworkPlanControls";
const request = vi.hoisted(() => vi.fn(async () => ({})));
vi.mock("@/lib/framework", () => ({ frameworkRequest: request }));
(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

it("requires explicit exact-intent confirmation before a saved-ID mutation", async () => {
  const container = document.createElement("div");
  const root = createRoot(container);
  const signal = new AbortController().signal;
  await act(async () => root.render(<FrameworkPlanControls profile="a" signal={signal} onChange={() => {}} record={{ plan: { plan_id: "plan_a", steps: [{ step_id: "step_a", action_name: "workspace_config_update", target: "/profile/a/agent/artifacts/operational-report.json", params: { content: { observed: true } } }] } }} />));
  const button = (label: string) => Array.from(container.querySelectorAll("button")).find(item => item.textContent === label)!;
  await act(async () => button("Grant exact policy").click());
  expect(request).not.toHaveBeenCalled();
  expect(container.querySelector('[role="dialog"]')?.textContent).toContain("/profile/a/agent/artifacts/operational-report.json");
  expect(container.querySelector('[role="dialog"]')?.textContent).toContain('"observed": true');
  await act(async () => button("Confirm grant").click());
  expect(request).toHaveBeenCalledTimes(1);
  const [profile, endpoint, init] = request.mock.calls[0] as unknown as [string, string, RequestInit];
  expect(profile).toBe("a"); expect(endpoint).toBe("grant");
  expect(JSON.parse(String(init.body))).toEqual({ plan_id: "plan_a", step_id: "step_a", confirm: true, autonomous: false });
  await act(async () => root.unmount());
});
