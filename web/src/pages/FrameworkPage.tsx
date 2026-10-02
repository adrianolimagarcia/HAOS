import { useEffect, useState } from "react";
import { useProfileScope } from "@/contexts/useProfileScope";
import { FrameworkPlanControls } from "@/components/FrameworkPlanControls";
import { FrameworkPanel, FrameworkRecord } from "@/components/FrameworkPanel";
import { dryRunFramework, loadFramework, type FrameworkSnapshot } from "@/lib/framework";

function ScopedFramework({ profile }: { profile: string }) {
  const [snapshot, setSnapshot] = useState<FrameworkSnapshot | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [revision, setRevision] = useState(0);
  const [receipt, setReceipt] = useState<Record<string, unknown> | null>(null);
  const [controller] = useState(() => new AbortController());
  useEffect(() => () => controller.abort(), [controller]);
  useEffect(() => {
    const request = new AbortController();
    setError("");
    void loadFramework(profile, request.signal).then(setSnapshot).catch(cause => {
      if (!request.signal.aborted) setError(cause instanceof Error ? cause.message : "Framework unavailable");
    });
    return () => request.abort();
  }, [profile, revision]);

  async function run(createReport = false) {
    setBusy(true); setError("");
    try {
      const result = await dryRunFramework(profile, controller.signal, createReport);
      if (!controller.signal.aborted) { setReceipt(result); setRevision(value => value + 1); }
    } catch (cause) {
      if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : "Dry run failed");
    } finally { if (!controller.signal.aborted) setBusy(false); }
  }

  return <div className="p-6 space-y-5">
    <header className="flex flex-wrap items-center justify-between gap-3">
      <div><h1 className="text-2xl font-semibold">HAOS Operational Framework</h1>
        <p className="text-sm text-muted-foreground">Profile: {profile || "current"} · Operator-driven observation and audit</p></div>
      <div className="flex gap-2">
        <button className="rounded border px-3 py-2" onClick={() => setRevision(value => value + 1)}>Refresh</button>
        <button className="rounded border px-3 py-2" disabled={busy || !snapshot?.status.capabilities.dry_run} onClick={() => void run(true)}>Preview reversible report plan</button>
        <button className="rounded border px-3 py-2" disabled={busy || !snapshot?.status.capabilities.dry_run} onClick={() => void run()}>{busy ? "Running…" : "Run dry-run cycle"}</button>
      </div>
    </header>
    <p className="rounded border border-border p-3 text-sm">Dry run stores plan/audit artifacts only. Explicitly confirmed grant/approval/apply is limited to the reversible framework report artifact — never host remediation. Warnings are hypotheses, not proven causes; dry-run outcomes never prove a repair.</p>
    {error && <p role="alert" className="text-destructive">{error}</p>}
    {!snapshot && !error && <p role="status">Loading framework…</p>}
    {receipt && <FrameworkPanel title="Latest dry-run receipt"><FrameworkRecord value={receipt} label="Inspect outcome and verification" /></FrameworkPanel>}
    {snapshot && <div className="grid gap-4 lg:grid-cols-2">
      <FrameworkPanel title="Status"><p>{snapshot.status.initialized ? "Recorded framework state available" : "Not initialized — no recorded cycle"}</p><FrameworkRecord value={snapshot.status.state} /></FrameworkPanel>
      <FrameworkPanel title="Telemetry"><p className="text-sm text-muted-foreground">{snapshot.telemetry.scope}. Unavailable sources remain unknown.</p><FrameworkRecord value={snapshot.telemetry.telemetry} label="Metrics, sources and collection errors" /></FrameworkPanel>
      <FrameworkPanel title="Plans"><p>{snapshot.plans.plans.length} recent recorded plans</p>{snapshot.plans.plans.map((plan, index) => <div key={String(plan.cycle_id ?? index)}><FrameworkRecord value={plan} label={String((plan.plan as Record<string, unknown> | undefined)?.title ?? plan.cycle_id ?? "Plan")} /><FrameworkPlanControls profile={profile} record={plan} signal={controller.signal} onChange={() => setRevision(value => value + 1)} /></div>)}</FrameworkPanel>
      <FrameworkPanel title="Hierarchy"><p>{snapshot.hierarchy.supervisor} → bounded domain workers · {snapshot.hierarchy.result.status}</p><ul className="my-2 space-y-1">{snapshot.hierarchy.result.task_results.map(task => <li key={task.task_id}>{task.domain}: {task.status}{task.error ? ` — ${task.error}` : ""}</li>)}</ul><FrameworkRecord value={snapshot.hierarchy} label="Task ownership, dependencies and findings" /></FrameworkPanel>
      <FrameworkPanel title="Action receipts"><p>{snapshot.actions.receipts.length} recorded outcomes · verification and rollback evidence</p>{snapshot.actions.receipts.map((item, index) => <FrameworkRecord key={String(item.cycle_id ?? index)} value={item} label={`${String(item.plan_id ?? "Receipt")}: ${String(item.phase)}`} />)}</FrameworkPanel>
    </div>}
  </div>;
}

export default function FrameworkPage() {
  const { profile } = useProfileScope();
  // Remount on scope change: old receipts/data never flash in another profile.
  return <ScopedFramework key={profile} profile={profile} />;
}
