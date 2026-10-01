import { fetchJSON } from "./api";

/** Explicit scope captures the owner's profile, including the process default. */
export function civRequest<T>(profile: string, path: string, init?: RequestInit): Promise<T> {
  const separator = path.includes("?") ? "&" : "?";
  return fetchJSON<T>(`/api/civilization/${path}${separator}profile=${encodeURIComponent(profile)}`, init);
}
export function jsonPost(body: unknown): RequestInit {
  return { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
}
export const MAX_AGENT_IMPORT_BYTES = 1024 * 1024;
export function parseAgentImport(text: string): Record<string, unknown> {
  const value: unknown = JSON.parse(text);
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("Agent JSON must be an object");
  const record = value as Record<string, unknown>;
  if (typeof record.id !== "string" || !record.id.trim()) throw new Error("Agent JSON requires an ID");
  return record;
}
export function downloadAgentJSON(id: string, value: unknown): void {
  const url = URL.createObjectURL(new Blob([JSON.stringify(value, null, 2) + "\n"], { type: "application/json" }));
  const link = document.createElement("a");
  link.href = url;
  link.download = `${id.replace(/[^a-zA-Z0-9_-]/g, "_")}.json`;
  document.body.append(link);
  try { link.click(); } finally { link.remove(); URL.revokeObjectURL(url); }
}
