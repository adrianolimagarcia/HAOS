# Operational framework deployment — 0.21.93

## Observed release
- Commit: `53175916da5ebf9de49d18adfeb923052fa07458`; tested tree: `2f5111fe268059824ac066fe381062eeb449f194`.
- Atomic non-force publication verified: remote main and haos-standalone both equal release commit.
- Installed `/usr/local/lib/haos-agent` equals release commit; source and importlib metadata both 0.21.93.
- Original dirty developer workspace was not reset or blanket-staged. Release committed in `.worktrees/operational-framework-deploy`.

## Tests actually executed
- Exact release targeted Python: 82 passed; with existing memory graph regression: 85 passed, zero failures.
- Frontend targeted native Vitest: 11 passed; TypeScript/Vite production build passed.
- VM exact archive acceptance: 82 passed, zero failures, plus real CLI plan/grant/approve/apply, hierarchy and authenticated canonical HTTP/assets.
- Installed environment targeted Python: 82 passed, zero failures.
- Policy symlink escape discovered in review, corrected, and real two-profile regression passed. Malformed/expired/tampered approvals, replay, policy refusal, rollback, cancellation, partial receipt and profile separation covered.

## Live deployment verified
- Actual unit: `haos-dashboard.service`, fresh PID3232435, active; `/proc/PID/cwd` is installed tree.
- Drop-in `/etc/systemd/system/haos-dashboard.service.d/20-verified-install.conf` switches WorkingDirectory from dirty workspace to installed release and explicitly binds root home.
- Canonical URL: http://127.0.0.1:9119/framework . Refresh and use configured dashboard login.
- Unauthenticated framework API401. Local-root signed short-lived configured-provider session smoke (not a password-login test) verified status/telemetry/plans/actions/hierarchy200; capabilities apply/approvals/artifact_only true.
- Authenticated memory graph overview200, SPA200, nine referenced assets and exact FrameworkPage-DphqeXNz.js chunk byte-identical to release artifacts.
- Initial memory smoke used nonexistent `/api/memory-graph/status` and returned404; corrected canonical `/api/memory/graph/overview` returned200.
- Custom8787 and edge8788 are separate UIs and were not rebuilt/restarted. Remote public ingress and rendered browser interaction were not independently verified.

## Preservation and rollback evidence
- Backup: `/root/haos-framework-deploy-backup-20261002T145456Z` contains prior unit, old dist, tracked patch (empty at deployment observation), untracked archive and inventory.
- Prior install SHA91887dfe951b21c42ef3c7b495ed518f4f717252.
- Stash preserved: `framework-deploy-preserve-20261002T145456Z`; seven noncolliding files restored,28 overlapping paths retained in archive/stash, not blanket stash-popped onto new tracked code.
- Code rollback must preserve current changes first, restore prior installation revision in an isolated/safely preserved tree, refresh its editable metadata, restore archived dist and prior unit/drop-in, then restart exact unit and verify HTTP. No blanket reset of dirty developer workspace.
- VM evidence: `/tmp/haos-framework-acceptance.kspQp5/`; archive SHA256b59c0023c8a7871b889cb9f880496067e2afb9c59d7f3a7b598a1bba020d123e.
- Concurrent external VM checkout change observed f5ca8a2475→d34c790368 while acceptance ran; this task used a separate exact validation tree, did not mutate `/opt/haos`. Global VM baseline preservation cannot be certified.

## Capability boundary
Autonomy is opt-in exact-intent execution of a fixed profile-owned reversible report JSON artifact (64KiB limit). No privileged host repair, shell execution, disk/firewall/kernel actions or autonomous scheduling daemon. Hierarchy is bounded trusted in-process async storage/memory/system diagnosis, not autonomous LLM fleet agents. Rollback restores supported artifact pre-state with policy checks; interrupted/partial receipts block new mutations pending operator recovery. No automatic SIGKILL recovery. No stress test or ISO produced.
