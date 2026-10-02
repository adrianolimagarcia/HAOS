import { fetchJSON } from "@/lib/api";

export interface FrameworkSnapshot {
  status: { initialized: boolean; state: Record<string, unknown>; capabilities: { dry_run: boolean; apply: boolean; approvals: boolean } };
  telemetry: { telemetry: Record<string, unknown>; scope: string };
  plans: { plans: Array<Record<string, unknown>> };
  hierarchy: { supervisor: string; tasks: Array<{ id: string; parent_id: string; posture: string }>; result: { status: string; task_results: Array<{ task_id: string; domain: string; status: string; error?: string }> } };
  actions: { receipts: Array<Record<string, unknown>>; cycles: Array<Record<string, unknown>> };
}

export function frameworkRequest<T>(profile: string, endpoint: string, init?: RequestInit): Promise<T> {
  // Explicit scope pins in-flight mutations even if the switcher changes meanwhile.
  return fetchJSON<T>(`/api/framework/${endpoint}?profile=${encodeURIComponent(profile || "current")}`, init);
}

export async function loadFramework(profile: string, signal: AbortSignal): Promise<FrameworkSnapshot> {
  const [status, telemetry, plans, hierarchy, actions] = await Promise.all([
    frameworkRequest<FrameworkSnapshot["status"]>(profile, "status", { signal }),
    frameworkRequest<FrameworkSnapshot["telemetry"]>(profile, "telemetry", { signal }),
    frameworkRequest<FrameworkSnapshot["plans"]>(profile, "plans", { signal }),
    frameworkRequest<FrameworkSnapshot["hierarchy"]>(profile, "hierarchy", { signal }),
    frameworkRequest<FrameworkSnapshot["actions"]>(profile, "actions", { signal }),
  ]);
  return { status, telemetry, plans, hierarchy, actions };
}

export function dryRunFramework(profile: string, signal: AbortSignal, createReport = false) {
  return frameworkRequest<Record<string, unknown>>(profile, "run", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(createReport ? { dry_run: true, create_report: true } : { dry_run: true }), signal,
  });
}
