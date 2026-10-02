import { useState } from "react";
import { FrameworkRecord } from "./FrameworkPanel";
import { frameworkRequest } from "@/lib/framework";

interface FrameworkPlanControlsProps {
  profile: string;
  record: Record<string, unknown>;
  signal: AbortSignal;
  onChange: () => void;
}
interface ArtifactStep { step_id: string; action_name: string; target: string; params: Record<string, unknown> }

export function FrameworkPlanControls({ profile, record, signal, onChange }: FrameworkPlanControlsProps) {
  const plan = record.plan as { plan_id?: string; steps?: ArtifactStep[] } | undefined;
  const step = plan?.steps?.find(item => item.action_name === "workspace_config_update");
  const [intent, setIntent] = useState<"grant" | "approve" | "apply" | null>(null);
  const [autonomous, setAutonomous] = useState(false);
  const [approvalIds, setApprovalIds] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  if (!step || !plan?.plan_id) return null;

  async function confirm() {
    if (!intent || !step || !plan?.plan_id) return;
    setBusy(true); setError("");
    const body = intent === "apply"
      ? { plan_id: plan.plan_id, approval_ids: approvalIds, mode: autonomous ? "autonomous" : "assisted", confirm: true }
      : { plan_id: plan.plan_id, step_id: step.step_id, confirm: true, ...(intent === "grant" ? { autonomous } : {}) };
    try {
      const response = await frameworkRequest<{ approval_id?: string }>(profile, intent, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body), signal,
      });
      if (signal.aborted) return;
      if (response.approval_id) setApprovalIds([response.approval_id]);
      setIntent(null); onChange();
    } catch (cause) { if (!signal.aborted) { setError(cause instanceof Error ? cause.message : "Operation refused"); onChange(); } }
    finally { if (!signal.aborted) setBusy(false); }
  }

  return <div className="my-3 rounded border border-border p-3 space-y-2">
    <p className="text-sm">Reversible framework JSON artifact only. Exact grant + assisted approval required; autonomous execution requires an explicit autonomous grant. Plans are one-use, including failed attempts.</p>
    <FrameworkRecord value={step} label="Exact action, target and parameters" />
    <label className="flex gap-2 text-sm"><input type="checkbox" checked={autonomous} onChange={event => { setAutonomous(event.target.checked); setIntent(null); }} />Explicit autonomous permission for this exact intent</label>
    <div className="flex flex-wrap gap-2">{(["grant", "approve", "apply"] as const).map(action => <button key={action} className="rounded border px-2 py-1" disabled={busy} onClick={() => setIntent(action)}>{action === "grant" ? "Grant exact policy" : action === "approve" ? "Approve for 5 minutes" : "Apply saved plan"}</button>)}</div>
    {approvalIds.length > 0 && <p className="text-sm">Operator approval issued (5-minute expiry).</p>}
    {intent && <div role="dialog" aria-label={`Confirm ${intent}`} className="border rounded p-3 space-y-2">
      <p>Confirm {intent} in profile {profile || "current"} for {plan.plan_id} / {step.step_id}?</p>
      <p className="text-sm break-all">{step.action_name} → {step.target}</p>
      <pre className="text-xs whitespace-pre-wrap break-all">{JSON.stringify(step.params, null, 2)}</pre>
      <p className="text-sm">Mode: {autonomous ? "autonomous exact grant" : "assisted"}. Rollback restores captured artifact bytes if verification fails; no host tuning.</p>
      <button disabled={busy} className="rounded border px-2 py-1" onClick={() => void confirm()}>Confirm {intent}</button>{" "}
      <button disabled={busy} onClick={() => setIntent(null)}>Cancel</button>
    </div>}
    {error && <p role="alert" className="text-destructive">{error}</p>}
  </div>;
}
