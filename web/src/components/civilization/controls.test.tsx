// @vitest-environment jsdom
import { act, StrictMode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fetchJSON } from "../../lib/api";
import { MAX_AGENT_IMPORT_BYTES } from "../../lib/civilization-api";
import type { AvailableModel } from "../../lib/civilization-contracts";
import { AgentImport, AgentLifecycle } from "./AgentControls";
import { ModelsCatalog } from "./ModelsCatalog";

vi.mock("../../lib/api", () => ({ fetchJSON: vi.fn() }));
(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let container: HTMLDivElement;
let root: Root;
const request = vi.mocked(fetchJSON);

beforeEach(() => {
  request.mockReset();
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
});
afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

function button(label: string): HTMLButtonElement {
  const found = Array.from(container.querySelectorAll("button")).find(item => item.textContent === label);
  expect(found, `button ${label}`).toBeDefined();
  return found!;
}
async function click(label: string) {
  await act(async () => button(label).click());
}
function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: Error) => void;
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
async function selectFile(text: string, size?: number) {
  const file = new File([text], "agent.json", { type: "application/json" });
  // jsdom's File lacks text(); retain the real File size and file-input event path.
  const read = vi.fn().mockResolvedValue(text);
  Object.defineProperty(file, "text", { value: read });
  if (size !== undefined) Object.defineProperty(file, "size", { value: size });
  await act(async () => {
    const input = container.querySelector<HTMLInputElement>('input[type="file"]')!;
    Object.defineProperty(input, "files", { configurable: true, value: [file] });
    input.dispatchEvent(new Event("change", { bubbles: true }));
  });
  return read;
}
async function inputValue(input: HTMLInputElement, value: string) {
  await act(async () => {
    // Bypass React's value tracker, as a real browser edit does.
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(input, value);
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
}
async function selectValue(select: HTMLSelectElement, value: string) {
  await act(async () => {
    select.value = value;
    select.dispatchEvent(new Event("change", { bubbles: true }));
  });
}

describe("agent lifecycle", () => {
  it.each([
    { status: "active", label: "Disable", enabled: false },
    { status: "disabled", label: "Enable", enabled: true },
  ])("requires confirmation and posts scoped $label availability while busy", async ({ status, label, enabled }) => {
    const changed = vi.fn();
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    const pending = deferred<unknown>();
    request.mockReturnValue(pending.promise);
    await act(async () => root.render(<AgentLifecycle agent={{ id: "agent/a b", status }} profile="owner & one" onChanged={changed} />));
    await click(label);
    expect(confirm).toHaveBeenCalledWith(expect.stringContaining("not runtime execution"));
    expect(request).not.toHaveBeenCalled();
    expect(changed).not.toHaveBeenCalled();
    confirm.mockReturnValue(true);
    await click(label);
    expect(request).toHaveBeenCalledExactlyOnceWith(
      "/api/civilization/agents/agent%2Fa%20b/lifecycle?profile=owner%20%26%20one",
      { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ enabled }) },
    );
    expect(container.querySelector('[aria-busy="true"]')).not.toBeNull();
    expect(button("Working…").disabled).toBe(true);
    expect(button("Export JSON").disabled).toBe(true);
    await click("Working…");
    await click("Export JSON");
    expect(request).toHaveBeenCalledTimes(1);
    expect(changed).not.toHaveBeenCalled();
    await act(async () => pending.resolve({}));
    expect(changed).toHaveBeenCalledTimes(1);
    expect(button(label).disabled).toBe(false);
    expect(container.querySelector('[aria-busy="false"]')).not.toBeNull();
  });

  it.each(["success", "error"])("keeps StrictMode live but ignores stale A→B→A %s completions", async outcome => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const old = deferred<unknown>();
    const current = deferred<unknown>();
    request.mockReturnValueOnce(old.promise).mockReturnValueOnce(current.promise);
    const changed = vi.fn();
    const render = async (profile: string) => act(async () => root.render(<StrictMode><AgentLifecycle agent={{ id: "a", status: "active" }} profile={profile} onChanged={changed} /></StrictMode>));
    await render("A");
    await click("Disable");
    await render("B");
    await render("A");
    await click("Disable");
    await act(async () => { if (outcome === "success") old.resolve({}); else old.reject(new Error("old profile error")); });
    expect(changed).not.toHaveBeenCalled();
    expect(container.querySelector('[role="alert"]')).toBeNull();
    expect(button("Working…").disabled).toBe(true);
    await act(async () => current.resolve({}));
    expect(changed).toHaveBeenCalledOnce();
    expect(button("Disable").disabled).toBe(false);
  });

  it("shows request failures without reporting a change and permits retry", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    request.mockRejectedValueOnce(new Error("availability rejected"));
    const changed = vi.fn();
    await act(async () => root.render(<AgentLifecycle agent={{ id: "a", status: "active" }} profile="" onChanged={changed} />));
    await click("Disable");
    expect(container.querySelector('[role="alert"]')?.textContent).toContain("availability rejected");
    expect(request.mock.calls[0][0]).toBe("/api/civilization/agents/a/lifecycle?profile=");
    expect(changed).not.toHaveBeenCalled();
    expect(button("Disable").disabled).toBe(false);
    request.mockResolvedValueOnce({});
    await click("Disable");
    expect(changed).toHaveBeenCalledTimes(1);
    expect(container.querySelector('[role="alert"]')).toBeNull();
  });
});

describe("agent JSON import", () => {
  it("rejects oversized files before reading and previews the limit-sized object without posting", async () => {
    await act(async () => root.render(<AgentImport profile="p" onChanged={vi.fn()} />));
    const oversizedRead = await selectFile('{"id":"large"}', MAX_AGENT_IMPORT_BYTES + 1);
    expect(oversizedRead).not.toHaveBeenCalled();
    expect(container.querySelector('[role="alert"]')?.textContent).toContain("1 MiB");
    expect(container.querySelector("pre")).toBeNull();
    const value = { id: "candidate", model: { provider: "test", temperature: 0 } };
    const validRead = await selectFile(JSON.stringify(value), MAX_AGENT_IMPORT_BYTES);
    expect(validRead).toHaveBeenCalledOnce();
    expect(JSON.parse(container.querySelector("pre")!.textContent!)).toEqual(value);
    expect(container.textContent).toContain("not a merge");
    expect(button("Confirm import").disabled).toBe(true);
    await click("Confirm import");
    expect(request).not.toHaveBeenCalled();
  });

  it("requires explicit overwrite acknowledgment for each preview and posts the complete JSON to its profile", async () => {
    const changed = vi.fn();
    const pending = deferred<unknown>();
    request.mockReturnValue(pending.promise);
    await act(async () => root.render(<AgentImport profile="team/a" onChanged={changed} />));
    await selectFile('{"id":"old"}');
    await act(async () => container.querySelector<HTMLInputElement>('input[type="checkbox"]')!.click());
    expect(button("Confirm import").disabled).toBe(false);
    const value = { id: "replacement", extension: { untouched: [1, 2] } };
    await selectFile(JSON.stringify(value));
    expect(container.querySelector<HTMLInputElement>('input[type="checkbox"]')!.checked).toBe(false);
    expect(button("Confirm import").disabled).toBe(true);
    expect(container.textContent).toContain("replacement");
    await act(async () => container.querySelector<HTMLInputElement>('input[type="checkbox"]')!.click());
    await click("Confirm import");
    expect(request).toHaveBeenCalledExactlyOnceWith("/api/civilization/agents/import?profile=team%2Fa", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(value),
    });
    expect(button("Importing…").disabled).toBe(true);
    expect(button("Cancel").disabled).toBe(true);
    expect(container.querySelector<HTMLInputElement>('input[type="file"]')!.disabled).toBe(true);
    expect(changed).not.toHaveBeenCalled();
    await act(async () => pending.resolve({}));
    expect(changed).toHaveBeenCalledTimes(1);
    expect(container.querySelector("pre")).toBeNull();
    expect(container.querySelector<HTMLInputElement>('input[type="file"]')!.disabled).toBe(false);
  });

  it.each(["[]", "null", '{"id":" "}', "not json"])("does not preview or post invalid agent JSON %s", async text => {
    await act(async () => root.render(<AgentImport profile="p" onChanged={vi.fn()} />));
    await selectFile(text);
    expect(container.querySelector('[role="alert"]')).not.toBeNull();
    expect(container.querySelector("pre")).toBeNull();
    expect(request).not.toHaveBeenCalled();
  });
});

describe("models catalog", () => {
  const models: AvailableModel[] = [
    { id: "unknown-id", name: "Alpha", provider: "remote", context_length: null, cost_per_1m_input_usd: null },
    { id: "free-id", name: "Beta", provider: "local", context_length: 0, cost_per_1m_input_usd: 0, cost_per_1m_output_usd: 0 },
    { id: "priced-id", name: "Gamma", provider: "remote", context_length: 2000, cost_per_1m_input_usd: 2, cost_per_1m_output_usd: 3 },
  ];
  const rows = () => Array.from(container.querySelectorAll("tbody tr"));
  it("searches name, ID and provider case-insensitively and combines search with the provider filter", async () => {
    await act(async () => root.render(<ModelsCatalog models={models} />));
    const search = container.querySelector("input")!;
    const provider = container.querySelectorAll("select")[0];
    for (const term of ["BETA", "FREE-ID", "LOCAL"]) {
      await inputValue(search, term);
      expect(rows().map(row => row.querySelector("code")!.textContent)).toEqual(["free-id"]);
    }
    await selectValue(provider, "remote");
    expect(rows()).toHaveLength(0);
    expect(container.textContent).toContain("No matching models");
    await inputValue(search, "");
    expect(rows().map(row => row.querySelector("code")!.textContent)).toEqual(["unknown-id", "priced-id"]);
    await selectValue(provider, "");
    expect(rows()).toHaveLength(models.length);
  });
  it("renders null and absent metadata as Unknown, preserves zero, and sorts known costs before unknown", async () => {
    await act(async () => root.render(<ModelsCatalog models={models} />));
    const cells = (id: string) => rows().find(row => row.querySelector("code")!.textContent === id)!.querySelectorAll("td");
    expect(Array.from(cells("unknown-id")).slice(2).map(cell => cell.textContent)).toEqual(["Unknown", "Unknown", "Unknown"]);
    expect(Array.from(cells("free-id")).slice(2).map(cell => cell.textContent)).toEqual(["0", "0", "0"]);
    for (const sort of ["input", "output", "context"]) {
      await selectValue(container.querySelectorAll("select")[1], sort);
      expect(rows().map(row => row.querySelector("code")!.textContent)).toEqual(["free-id", "priced-id", "unknown-id"]);
    }
  });
});

describe("agent JSON export", () => {
  it("downloads the canonical response as JSON and removes the temporary link and object URL", async () => {
    const canonical = { id: "a/b", model: { provider: "canonical" }, extension: true };
    request.mockResolvedValue(canonical);
    const createObjectURL = vi.fn().mockReturnValue("blob:agent-json");
    const revokeObjectURL = vi.fn();
    const NativeURL = URL;
    vi.stubGlobal("URL", class extends NativeURL {
      static createObjectURL = createObjectURL;
      static revokeObjectURL = revokeObjectURL;
    });
    let clicked: HTMLAnchorElement | undefined;
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) {
      clicked = document.querySelector<HTMLAnchorElement>('a[download="a_b.json"]')!;
      expect(this.isConnected).toBe(true);
      expect(this.href).toBe("blob:agent-json");
      expect(this.download).toBe("a_b.json");
    });
    await act(async () => root.render(<AgentLifecycle agent={{ id: "a/b", status: "active" }} profile="exports" onChanged={vi.fn()} />));
    await click("Export JSON");
    expect(request).toHaveBeenCalledExactlyOnceWith("/api/civilization/agents/a%2Fb/export?profile=exports", { method: "POST" });
    expect(createObjectURL).toHaveBeenCalledOnce();
    const blob = createObjectURL.mock.calls[0][0] as Blob;
    expect(blob.type).toBe("application/json");
    const text = await new Promise<string>((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(reader.result as string);
      reader.onerror = () => reject(reader.error);
      reader.readAsText(blob);
    });
    expect(text).toBe(JSON.stringify(canonical, null, 2) + "\n");
    expect(clicked).toBeDefined();
    expect(clicked!.isConnected).toBe(false);
    expect(revokeObjectURL).toHaveBeenCalledExactlyOnceWith("blob:agent-json");
    expect(button("Export JSON").disabled).toBe(false);
  });
});
