import { useEffect, useRef, useState } from "react";
import { civRequest, downloadAgentJSON, jsonPost, MAX_AGENT_IMPORT_BYTES, parseAgentImport } from "@/lib/civilization-api";
import { errorMessage } from "@/lib/api-error";

interface AgentIdentity { id: string; status: string }
export function AgentLifecycle({ agent, profile, onChanged }: { agent: AgentIdentity; profile: string; onChanged: () => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const generation = useRef(0);
  // Setup restores liveness under StrictMode; cleanup invalidates prior completions.
  useEffect(() => {
    generation.current += 1;
    setBusy(false); setError("");
    return () => { generation.current += 1; };
  }, [profile, agent.id]);
  async function lifecycle() {
    const enabled = agent.status === "disabled";
    if (!window.confirm(`${enabled ? "Enable" : "Disable"} agent ${agent.id}? This changes declarative availability, not runtime execution.`)) return;
    const ownerGeneration = generation.current;
    setBusy(true); setError("");
    try { await civRequest(profile, `agents/${encodeURIComponent(agent.id)}/lifecycle`, jsonPost({ enabled })); if (ownerGeneration === generation.current) onChanged(); }
    catch (cause) { if (ownerGeneration === generation.current) setError(errorMessage(cause)); }
    finally { if (ownerGeneration === generation.current) setBusy(false); }
  }
  async function exportAgent() {
    const ownerGeneration = generation.current;
    setBusy(true); setError("");
    try { const value = await civRequest(profile, `agents/${encodeURIComponent(agent.id)}/export`, { method: "POST" }); if (ownerGeneration === generation.current) downloadAgentJSON(agent.id, value); }
    catch (cause) { if (ownerGeneration === generation.current) setError(errorMessage(cause)); }
    finally { if (ownerGeneration === generation.current) setBusy(false); }
  }
  return <div aria-busy={busy}><span className="civ-model-pill">{agent.status}</span><button className="civ-btn-ghost" type="button" disabled={busy} onClick={() => void lifecycle()}>{busy ? "Working…" : agent.status === "disabled" ? "Enable" : "Disable"}</button><button className="civ-btn-ghost" type="button" disabled={busy} onClick={() => void exportAgent()}>Export JSON</button>{error && <p role="alert">{error}</p>}</div>;
}
export function AgentImport({ profile, onChanged }: { profile: string; onChanged: () => void }) {
  const [preview, setPreview] = useState<Record<string, unknown> | null>(null);
  const [confirmed, setConfirmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const generation = useRef(0);
  useEffect(() => {
    generation.current += 1;
    setPreview(null); setConfirmed(false); setBusy(false); setError("");
    return () => { generation.current += 1; };
  }, [profile]);
  async function readFile(file?: File) {
    generation.current += 1;
    const ownerGeneration = generation.current;
    setPreview(null); setConfirmed(false); setError("");
    if (!file) return;
    if (file.size > MAX_AGENT_IMPORT_BYTES) { setError("File exceeds the 1 MiB import limit"); return; }
    try { const value = parseAgentImport(await file.text()); if (ownerGeneration === generation.current) setPreview(value); }
    catch (cause) { if (ownerGeneration === generation.current) setError(errorMessage(cause)); }
  }
  async function importAgent() {
    if (!preview || !confirmed) return;
    const ownerGeneration = generation.current;
    setBusy(true); setError("");
    try { await civRequest(profile, "agents/import", jsonPost(preview)); if (ownerGeneration === generation.current) { setPreview(null); setConfirmed(false); onChanged(); } }
    catch (cause) { if (ownerGeneration === generation.current) setError(errorMessage(cause)); }
    finally { if (ownerGeneration === generation.current) setBusy(false); }
  }
  return <section className="civ-glass" style={{ padding: 18, marginBottom: 16 }} aria-label="Import agent"><label>Import agent JSON (max 1 MiB)<input type="file" accept="application/json,.json" disabled={busy} onChange={event => { void readFile(event.target.files?.[0]); event.target.value = ""; }} /></label>{error && <p role="alert">{error}</p>}{preview && <div style={{ padding: 16 }}><h3>Preview JSON</h3><pre style={{ maxHeight: 250, overflow: "auto", whiteSpace: "pre-wrap" }}>{JSON.stringify(preview, null, 2)}</pre><p role="alert">Warning: import replaces the existing agent with ID {String(preview.id)}. This is not a merge.</p><label><input type="checkbox" checked={confirmed} onChange={event => setConfirmed(event.target.checked)} /> I understand this ID may be overwritten</label><div className="civ-btn-row"><button type="button" className="civ-btn-primary" disabled={!confirmed || busy} onClick={() => void importAgent()}>{busy ? "Importing…" : "Confirm import"}</button><button type="button" className="civ-btn-ghost" disabled={busy} onClick={() => setPreview(null)}>Cancel</button></div></div>}</section>;
}
