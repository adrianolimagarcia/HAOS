import { useEffect, useState } from "react";
import { civRequest } from "@/lib/civilization-api";
import type { MissionEvent } from "@/lib/civilization-contracts";
import { errorMessage } from "@/lib/api-error";
import { MissionReplay } from "./MissionReplay";

interface Analytics { mission_id: string; duration_seconds: number | null; tokens: number | null; failures: number | null; retries: number | null; event_count: number; quality: string; limitations: string[] }
export function MissionInsights({ missionId, profile }: { missionId: string; profile: string }) {
  const [analytics, setAnalytics] = useState<Analytics | null>(null);
  const [events, setEvents] = useState<MissionEvent[]>([]);
  const [error, setError] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    setAnalytics(null); setEvents([]); setError("");
    const init = { signal: controller.signal };
    // Independent panels remain useful if another endpoint is unavailable.
    const load = async <T,>(path: string, apply: (value: T) => void) => {
      try { const value = await civRequest<T>(profile, path, init); if (!controller.signal.aborted) apply(value); }
      catch (cause) { if (!controller.signal.aborted) setError(current => [current, errorMessage(cause)].filter(Boolean).join("; ")); }
    };
    void load<Analytics>(`missions/${encodeURIComponent(missionId)}/analytics`, setAnalytics);
    void load<{ events: MissionEvent[] }>(`missions/${encodeURIComponent(missionId)}/events`, value => setEvents(value.events ?? []));
    return () => controller.abort();
  }, [profile, missionId]);
  return <section aria-label="Mission insights">{error && <p role="alert">{error}</p>}<div className="civ-glass" style={{ padding: 18, marginTop: 16 }}><h3>Analytics</h3>{analytics ? <><p>Quality: {analytics.quality}</p><dl className="civ-inspect">{(["duration_seconds", "tokens", "failures", "retries", "event_count"] as const).map(key => <div className="civ-cascade-row" key={key}><dt>{key === "duration_seconds" ? "Total duration (seconds)" : key.replaceAll("_", " ")}</dt><dd>{analytics[key] == null ? "Unknown" : analytics[key]}</dd></div>)}</dl>{analytics.limitations.map(value => <p key={value}>{value}</p>)}</> : <p>Analytics unavailable</p>}</div><MissionReplay events={events} missionId={missionId} /></section>;
}
