// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { dryRunFramework, loadFramework } from "./framework";
import { setManagementProfile } from "./api";

afterEach(() => { setManagementProfile(""); vi.unstubAllGlobals(); });

it("pins every dashboard read and dry-run to explicit profile with canonical auth", async () => {
  window.__HERMES_SESSION_TOKEN__ = "framework-test-token";
  setManagementProfile("other-profile");
  const fetcher = vi.fn(async () => new Response("{}", { headers: { "Content-Type": "application/json" } }));
  vi.stubGlobal("fetch", fetcher);
  const controller = new AbortController();
  await loadFramework("a", controller.signal);
  await dryRunFramework("a", controller.signal);
  expect(fetcher).toHaveBeenCalledTimes(6);
  for (const [url, init] of fetcher.mock.calls as unknown as Array<[string, RequestInit]>) {
    expect(url).toContain("profile=a");
    expect(new Headers(init.headers).get("X-Hermes-Session-Token")).toBe("framework-test-token");
    expect(init.signal).toBe(controller.signal);
  }
  const [url, init] = fetcher.mock.calls.at(-1) as unknown as [string, RequestInit];
  expect(url).toContain("/api/framework/run");
  expect(init.method).toBe("POST");
  expect(JSON.parse(String(init.body))).toEqual({ dry_run: true });
});

it("does not silently return a dashboard snapshot when an endpoint fails", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response("unavailable", { status: 503 })));
  await expect(loadFramework("b", new AbortController().signal)).rejects.toThrow();
});
