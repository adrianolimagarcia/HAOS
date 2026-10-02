import type { ReactNode } from "react";

interface FrameworkPanelProps { title: string; children: ReactNode }
export function FrameworkPanel({ title, children }: FrameworkPanelProps) {
  return <section className="rounded-lg border border-border bg-card p-4 min-w-0">
    <h2 className="text-lg font-semibold mb-3">{title}</h2>{children}
  </section>;
}

interface FrameworkRecordProps { value: unknown; label?: string }
export function FrameworkRecord({ value, label = "Structured evidence" }: FrameworkRecordProps) {
  return <details><summary className="cursor-pointer text-sm text-muted-foreground">{label}</summary>
    <pre className="mt-2 max-h-80 overflow-auto whitespace-pre-wrap break-all text-xs">{JSON.stringify(value, null, 2)}</pre>
  </details>;
}
