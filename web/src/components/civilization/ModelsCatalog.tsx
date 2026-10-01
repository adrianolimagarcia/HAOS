import { useMemo, useState } from "react";
import type { AvailableModel } from "@/lib/civilization-contracts";

export function catalogValue(value: number | null | undefined): string {
  return value == null || !Number.isFinite(value) ? "Unknown" : value.toLocaleString();
}
export function ModelsCatalog({ models }: { models: AvailableModel[] }) {
  const [search, setSearch] = useState("");
  const [provider, setProvider] = useState("");
  const [sort, setSort] = useState("name");
  const providers = [...new Set(models.map(model => model.provider).filter(Boolean))].sort();
  const filtered = useMemo(() => models.filter(model => (!provider || model.provider === provider) && `${model.id} ${model.name} ${model.provider}`.toLowerCase().includes(search.toLowerCase())).sort((a, b) => {
    if (sort === "context" || sort === "input" || sort === "output") {
      const key = { context: "context_length", input: "cost_per_1m_input_usd", output: "cost_per_1m_output_usd" }[sort] as "context_length" | "cost_per_1m_input_usd" | "cost_per_1m_output_usd";
      const av = a[key], bv = b[key];
      const aKnown = av != null && Number.isFinite(av), bKnown = bv != null && Number.isFinite(bv);
      if (aKnown !== bKnown) return aKnown ? -1 : 1;
      if (aKnown && bKnown && av !== bv) return av! - bv!;
    }
    return (sort === "provider" ? a.provider.localeCompare(b.provider) : 0) || (a.name || a.id).localeCompare(b.name || b.id) || a.id.localeCompare(b.id);
  }), [models, search, provider, sort]);
  return <section className="civ-glass" style={{ padding: 22 }} aria-label="Models catalog"><h2>Models</h2><p>Catalog metadata only — not live pricing. Unknown values are not zero.</p><div className="civ-btn-row"><label>Search models<input className="civ-input" value={search} onChange={event => setSearch(event.target.value)} /></label><label>Provider<select className="civ-select" value={provider} onChange={event => setProvider(event.target.value)}><option value="">All providers</option>{providers.map(value => <option key={value}>{value}</option>)}</select></label><label>Sort models<select className="civ-select" value={sort} onChange={event => setSort(event.target.value)}>{[["name", "Name"], ["provider", "Provider"], ["context", "Context length"], ["input", "Input cost"], ["output", "Output cost"]].map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label></div><div style={{ overflowX: "auto" }}><table><thead><tr>{["Model / ID", "Provider", "Context", "USD / 1M input", "USD / 1M output"].map(label => <th key={label}>{label}</th>)}</tr></thead><tbody>{filtered.map(model => <tr key={`${model.provider}:${model.id}`}><td>{model.name || model.id}<br /><code>{model.id}</code></td><td>{model.provider || "Unknown"}</td><td>{catalogValue(model.context_length)}</td><td>{catalogValue(model.cost_per_1m_input_usd)}</td><td>{catalogValue(model.cost_per_1m_output_usd)}</td></tr>)}</tbody></table></div>{!filtered.length && <p>No matching models.</p>}</section>;
}
