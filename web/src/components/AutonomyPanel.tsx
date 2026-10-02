import { useEffect, useState } from "react";
import { FrameworkPanel, FrameworkRecord } from "./FrameworkPanel";
import { loadAutonomy, pauseAutonomy, submitInvestigation, type AutonomySnapshot } from "@/lib/framework";

export interface AutonomyPanelProps { profile: string }

export function AutonomyPanel({ profile }: AutonomyPanelProps) {
  const [snapshot, setSnapshot] = useState<AutonomySnapshot | null>(null);
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(0);
  const [confirm, setConfirm] = useState(false);
  const [busy, setBusy] = useState(false);
  const [summary, setSummary] = useState("");
  const [receipt, setReceipt] = useState<Record<string, unknown> | null>(null);
  const [controller] = useState(() => new AbortController());
  useEffect(() => () => controller.abort(), [controller]);
  useEffect(() => {
    const request = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    // Schedule after settlement, not setInterval: slow requests cannot overlap.
    async function refresh() {
      try {
        const result = await loadAutonomy(profile, request.signal);
        if (!request.signal.aborted) { setSnapshot(result); setError(""); }
      } catch (cause) {
        if (!request.signal.aborted) setError(cause instanceof Error ? cause.message : "Autonomy unavailable");
      } finally {
        if (!request.signal.aborted) timer = setTimeout(() => void refresh(), 10000);
      }
    }
    void refresh();
    return () => { request.abort(); clearTimeout(timer); };
  }, [profile, revision]);

  async function mutate(operation: () => Promise<Record<string, unknown>>) {
    setBusy(true); setError("");
    try {
      const result = await operation();
      if (!controller.signal.aborted) { setReceipt(result); setConfirm(false); setRevision(value => value + 1); }
    } catch (cause) {
      if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : "Operation failed");
    } finally { if (!controller.signal.aborted) setBusy(false); }
  }

  return <FrameworkPanel title="Event-driven autonomy">
    <p className="text-sm">Investigations run in the background service, never in this web request. Adjustments require an existing exact authorization and are limited to the framework report artifact, not OS repairs.</p>
    {error && <p role="alert" className="text-destructive">{error} · displayed data may be stale</p>}
    {!snapshot && !error && <p role="status">Loading autonomy…</p>}
    {snapshot && <div className="space-y-3">
      <p>Enabled: {snapshot.config.enabled ? "yes" : "no"} · Authorized auto-apply: {snapshot.config.auto_apply ? "enabled" : "disabled"} · Service: {snapshot.service.status}</p>
      <p>Queue: {snapshot.queue.available ? (snapshot.queue.paused ? "paused" : "accepting new jobs") : "not initialized"}</p>
      <p>Running: {snapshot.queue.counts.running ?? 0} · Pending: {snapshot.queue.counts.pending ?? 0}</p>
      <p className="text-sm">Pause stops new job claims only. Running investigations continue separately. Heartbeat absence or staleness is unknown, not proof of a healthy worker.</p>
      <button className="rounded border px-3 py-2" disabled={busy} onClick={() => setConfirm(true)}>{snapshot.queue.paused ? "Resume queue" : "Pause queue"}</button>
      {confirm && <div role="dialog" aria-label="Confirm queue pause change" className="rounded border p-3">
        <p>{snapshot.queue.paused ? "Resume new job claims?" : "Pause new job claims? Running jobs will not be cancelled."}</p>
        <button disabled={busy} onClick={() => void mutate(() => pauseAutonomy(profile, !snapshot.queue.paused, controller.signal))}>Confirm {snapshot.queue.paused ? "resume" : "pause"}</button>
        <button disabled={busy} onClick={() => setConfirm(false)}>Cancel</button>
      </div>}
      <form onSubmit={event => { event.preventDefault(); void mutate(() => submitInvestigation(profile, summary, controller.signal)); }}>
        <label className="block">Investigation summary (untrusted operator observation, not commands)
          <textarea aria-label="Investigation summary" className="block w-full rounded border bg-background p-2" value={summary} maxLength={2000} onChange={event => setSummary(event.target.value)} />
        </label>
        <button className="rounded border px-3 py-2" disabled={busy || !summary.trim()}>Queue investigation</button>
      </form>
      <FrameworkRecord value={snapshot.service} label="Service heartbeat evidence" />
      <FrameworkRecord value={snapshot.queue.counts} label="Queue counts" />
      <p className="text-sm">Diagnosis is a finding, not a verified repair. Inspect recorded investigation, action outcome, pending authorization and verification evidence; missing evidence remains unknown.</p>
      {snapshot.queue.recent_jobs.length === 0 && <p>No recorded jobs.</p>}
      {snapshot.queue.recent_jobs.map((job, index) => <FrameworkRecord key={String(job.job_id ?? index)} value={job} label={`${String(job.job_id ?? "Job")}: ${String(job.status ?? "unknown")}`} />)}
    </div>}
    {receipt && <FrameworkRecord value={receipt} label="Latest operator submission receipt" />}
  </FrameworkPanel>;
}
