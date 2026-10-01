import { useEffect, useRef, useState } from "react";
import { civRequest, jsonPost } from "@/lib/civilization-api";
import { errorMessage } from "@/lib/api-error";

interface Approval { approval_id: string; mission_id: string | null; agent_id: string | null; gate: string | null; risk_class: string | null; description: string | null; requested_at: number; status: string }
interface Approvals { approvals: Approval[]; quality: string; limitations: string[] }

function ApprovalDecision({ approval, profile, onChanged }: { approval: Approval; profile: string; onChanged: () => void }) {
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const generation = useRef(0);
  useEffect(() => { generation.current++; return () => { generation.current++; }; }, [profile, approval.approval_id]);
  async function decide(approved: boolean) {
    if (!window.confirm(`${approved ? "Approve" : "Reject"} ${approval.approval_id}? This records a decision only; it does not resume runtime.`)) return;
    const token = generation.current;
    setBusy(true); setError("");
    try {
      await civRequest(profile, `approvals/${encodeURIComponent(approval.approval_id)}/decision`, jsonPost({ approved, reason }));
      if (token === generation.current) onChanged();
    } catch (cause) { if (token === generation.current) setError(errorMessage(cause)); }
    finally { if (token === generation.current) setBusy(false); }
  }
  return <article className="civ-glass" style={{ padding: 18, marginTop: 12 }} aria-busy={busy}><h4>{approval.description || approval.approval_id}</h4><p>{approval.mission_id || "No mission"} · {approval.agent_id || "Unknown agent"} · {approval.gate || "Unknown gate"} · {approval.risk_class || "Unknown risk"}</p><label className="civ-form-group">Decision reason<input className="civ-input" value={reason} disabled={busy} onChange={event => setReason(event.target.value)} /></label><div className="civ-btn-row"><button className="civ-btn-primary" type="button" disabled={busy} onClick={() => void decide(true)}>Approve</button><button className="civ-btn-ghost" type="button" disabled={busy} onClick={() => void decide(false)}>Reject</button></div>{error && <p role="alert">{error}</p>}</article>;
}

/** Global by default: missionless requests must remain visible to the operator. */
export function ApprovalCenter({ profile, missionId }: { profile: string; missionId?: string }) {
  const [data, setData] = useState<Approvals | null>(null);
  const [error, setError] = useState("");
  const [tick, setTick] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setData(null); setError("");
    void civRequest<Approvals>(profile, `approvals${missionId ? `?mission_id=${encodeURIComponent(missionId)}` : ""}`, { signal: controller.signal })
      .then(value => { if (!controller.signal.aborted) setData(value); })
      .catch(cause => { if (!controller.signal.aborted) setError(errorMessage(cause)); });
    return () => controller.abort();
  }, [profile, missionId, tick]);
  return <section className="civ-glass" style={{ padding: 22 }} aria-label="Approval Center"><h2>Approval Center</h2><p className="civ-notice">Decisions are recorded only; no runtime is resumed.</p>{error && <p role="alert">{error}</p>}{data ? <><p>Quality: {data.quality}</p>{data.limitations.map(value => <p key={value}>{value}</p>)}{data.approvals.filter(item => item.status.toUpperCase() === "PENDING").map(approval => <ApprovalDecision key={`${profile}:${approval.approval_id}`} approval={approval} profile={profile} onChanged={() => setTick(value => value + 1)} />)}{!data.approvals.some(item => item.status.toUpperCase() === "PENDING") && <p>No pending approvals.</p>}</> : <p role="status">Approvals unavailable or loading.</p>}<button type="button" className="civ-btn-ghost" onClick={() => setTick(value => value + 1)}>Refresh approvals</button></section>;
}
