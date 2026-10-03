# HAOS deployment and cutover readiness runbook

Status: **READY WITH BLOCKERS**. This is a read-only topology audit; no deployment was run.

## Evidence-backed topology

- Acceptance VM: IP is read from `/tmp/haos-vm-ip.txt` (documented current value `192.168.122.130`); SSH user `haos`, key `/root/haos-vm/id_ed25519`; rootfs `/opt/haos`; state/config home `/home/haos/.haos`.
- Production appliance services are system units under `distro/haos-linux/config/includes.chroot/etc/systemd/system/`: `haos-gateway.service` and `haos-edge.service` (plus DNS, mesh, storage-init, STT, antigravity).
- `haos-gateway.service`: user/group `haos`, working directory `/opt/haos`, `HAOS_HOME=/home/haos/.haos`, `PYTHONPATH=/opt/haos`, executable `/opt/haos/venv/bin/python /opt/haos/gateway/run.py`, `Restart=always`, `RestartSec=3`.
- `haos-edge.service`: user/group `haos`, `HAOS_DATA_DIR=/var/lib/haos/edge`, upstream WebUI `127.0.0.1:8787`, gateway `127.0.0.1:9900`, binds `127.0.0.1:8788`, runs `/usr/local/bin/haos-edge server ...`, `Restart=always`, `RestartSec=3`.
- Edge routes `/health`; the source registers it in `packages/haos-edge/src/server.rs`. Public edge routes include `/health`, `/login`, `/api/login`, and static assets; control routes require the WebUI session. No TLS is provided by the unit; external exposure requires a reverse proxy.
- Gateway liveness is represented by `gateway_state.json`, including stamped `code_sha`/`code_version`; the update plan and verification compare every live gateway against the checkout SHA. `hermes update --plan` is documented as read-only.

## Preconditions and explicit blockers

1. **Do not cut over until a real staging VM run is complete.** The repository's acceptance plan says the Rust migration gate requires R1–R7 and R9, A→B→A isolation, and rollback back to Python. The current documented test state is not green: targeted tests were `23 passed, 3 failed`; `session_time_persistence_controls.py` failed `default_reset_policy`; `git diff --check` had existing trailing whitespace failures. These are evidence from `docs/haos/incremental-migration-acceptance-plan.md`, not a new test run here.
2. Confirm the VM IP with `cat /tmp/haos-vm-ip.txt`; do not rely on the documented example IP.
3. Confirm the builder image `haos-iso-builder:latest` and privileged Docker access before an ISO build. `lb` is only expected inside that container.
4. Confirm operator WebUI password is provisioned on the target using `HAOS_DATA_DIR=/var/lib/haos/edge haos-edge admin set-password`; the ISO pipeline deliberately scrubs `webui.passwd` and sessions.
5. For update/restart, run with `HOME=/root` or explicitly `HAOS_HOME=/root/.haos`; the documented workflow warns that a mismatched HOME can report no services and leave the active gateway on old code.
6. Do not claim performance improvement without the controlled baseline/candidate protocol in the acceptance plan (same fixture, A/B/A/B, at least five measured runs, RSS/PSS and p50/p95/p99).

## Staging and preflight

Run on the acceptance VM, after confirming the IP and SSH access:

```sh
IP="$(cat /tmp/haos-vm-ip.txt)"
SSH="ssh -i /root/haos-vm/id_ed25519 -o StrictHostKeyChecking=no haos@$IP"
$SSH 'cd /opt/haos && git rev-parse HEAD && systemctl is-system-running && systemctl --failed --no-legend && systemctl --no-pager --full status haos-gateway haos-edge'
$SSH 'curl --fail --silent --show-error http://127.0.0.1:8788/health'
$SSH 'HOME=/root HAOS_HOME=/home/haos/.haos haos update --plan'
```

The last command is the repository-documented read-only inventory. Record its expected SHA, PIDs, supervisors, and `code_sha` values. Capture baseline logs and service restart counters:

```sh
$SSH 'journalctl -u haos-gateway -u haos-edge --since "1 hour ago" --no-pager'
$SSH 'systemctl show haos-gateway haos-edge -p ActiveState -p SubState -p NRestarts -p ExecMainPID'
```

Run the directed acceptance commands from the repository (not stress tests):

```sh
$SSH 'cd /opt/haos && bash scripts/run_tests.sh <targeted-tests>'
cargo test --manifest-path packages/haos-edge/Cargo.toml --lib
python3 evals/gateway/session_time_persistence_ab.py .
python3 evals/gateway/session_time_persistence_controls.py .
```

Replace `<targeted-tests>` with the agreed test selection; the repository does not provide one universal cutover command.

## Backup and artifact preparation

- Before mutating the target, take the VM/libvirt snapshot using the existing harness under `/root/haos-vm/`; the exact snapshot command is not specified in this checkout and must be confirmed with the operator. Do not invent it.
- For a normal `haos update`, the updater creates per-profile `state-snapshots/` quick snapshots, but the docs explicitly classify these as file-loss recovery, **not code rollback insurance**. Use the updater's full `--backup` mode when its supported syntax is confirmed from `haos update --help`.
- For memory-fabric cutover specifically, create the documented backup directory `/root/haos-backup-memory-<timestamp>/` containing `config.yaml`, `obsidian_vault/`, and `memory/` before changing `memory.provider`.
- The ISO path is `distro/haos-linux/iso-from-vm.sh [IP_DA_VM] [TAG_DO_ISO]`. It rsyncs the VM rootfs, scrubs machine identity, SSH/admin/WebUI/session/config state, runs `lb binary` in `haos-iso-builder:latest`, validates the squashfs, and writes `distro/haos-linux/haos-linux-1.0-<tag>.iso`. Do not run it until staging is accepted; the task scope forbids deployment.
- Preserve pre-cutover SHA, dirty diff, config hash, service unit files, `gateway_state.json`, update receipt, and redacted journal output outside the checkout.

## Canary and cutover

There is no documented traffic-splitting or percentage-canary mechanism for the appliance units. Treat the acceptance VM as the canary, then perform a one-node cutover only after the staging gates pass. Do not represent a second production cohort as available.

1. Deploy the approved commit/assets to the VM according to `docs/haos/DEV_WORKFLOW_VM.md`'s repository flow (`git pull --ff-only origin main` at `/usr/local/lib/haos-agent` is the documented host path; `/opt/haos` is the VM runtime path). The exact production promotion authority is not specified; obtain operator approval.
2. Ensure appliance assets are synchronized: the documented update maintenance calls `sync_appliance_assets` (`hermes_cli/update_cmd_assets.py`) because `haos-edge` and installer scripts are outside the Python tree.
3. Run the update plan, snapshot/backup, apply, and restart through the supported `haos update` flow. Do not manually restart only one gateway profile: the update design restarts the fleet and verifies every live `code_sha`.
4. If changing memory fabric, use the reversible sequence documented in `DEV_WORKFLOW_VM.md`: `haos config unset memory.provider` is the rollback switch, then `haos gateway restart` because the provider is read at boot. Enable only a valid prefix of `memory.fabric.cutover`; malformed config retains defaults and logs an error.
5. After restart, verify all expected `gateway_state.json` entries are `state=current` with `code_sha` equal to the new checkout SHA, and inspect the machine-readable receipt under `~/.hermes/logs/update_receipts/latest.json` (or the active `$HAOS_HOME/logs/update_receipts/latest.json`).
6. Probe `curl --fail --silent --show-error http://127.0.0.1:8788/health`, then authenticated WebUI login/control-plane smoke tests, gateway chat/tool-call smoke, persistence/restart, and A→B→A profile isolation.

## Abort thresholds and observation window

Use measurable pre-cutover baselines; no fixed latency/error numbers are evidenced by this repository. Abort immediately on any of these objective conditions:

- any expected unit not `active/running`, any new failed unit, or increasing restart counter;
- edge `/health` fails, gateway `gateway_state.json` is missing/stale, or any live `code_sha` differs from the approved SHA;
- any R1–R7/R9 acceptance failure, profile data/auth bleed, 401/403 contract violation, duplicate/lost write, orphan worker, or persistence loss;
- any update receipt step reports failure, snapshot/backup verification fails, or service logs show an unbounded restart/error loop;
- performance regression beyond the pre-agreed tolerance, or any unsupported claim where baseline/PSS data is missing.

Observe continuously through the agreed canary window using `systemctl show`, `journalctl`, edge `/health`, gateway state/receipt, and the same functional probes. Because the repository does not define a numeric window or thresholds, the release owner must set them before approval; otherwise readiness is blocked.

## Rollback

1. Stop promotion and preserve logs/receipt/state before changing anything.
2. For memory fabric: `haos config unset memory.provider`; then `haos gateway restart`; if required, remove `HAOS_HOME/memory/fabric.db` with its `-wal`/`-shm` files. Restore the pre-cutover backup only after verifying the target paths.
3. For code/assets: use the recorded pre-cutover commit/ISO or VM snapshot. The exact appliance production rollback command is not specified in this checkout; confirm the operator's snapshot restore procedure rather than inventing a command.
4. Restart the full managed fleet using the supported update/gateway mechanism, not a single profile. Verify every gateway's `code_sha` returns to the rollback SHA and all units are active.
5. Re-run edge health, authenticated control-plane, gateway functional, persistence, and A→B→A probes. Do not declare rollback complete until these pass and the receipt/log evidence is saved.

## Readiness decision

**Not ready for production cutover from repository evidence alone.** The topology and rollback switch are documented, but traffic canary semantics, numeric abort window/tolerances, exact VM snapshot/restore command, and a green current acceptance run remain prerequisites. No deployment was performed in this audit.
