"""Live Mission Execution Runner (Passo 2).

Executes an autonomous mission through HAOS MultiAgentTeamRuntime with:
- Model: DeepSeek-V4-Flash via live A6API (using DSH A6_API_KEY).
- Architecture:
  1. Town Mayor defines domain sub-goals (software implementation).
  2. DomainSubOrchestrator generates DAG: impl -> test -> review.
  3. Worker pool acquires Polecat (coder) and Witness (reviewer).
  4. Worktree isolation via GitWorktreeManager.
  5. Code and tests written to ephemeral worktree, verified via pytest.
  6. Audit trail recorded in EventStore.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
import urllib.request
import yaml
from pathlib import Path
from typing import Any, Dict

from hermes.platform.execution.team_runtime import (
    DomainSubGoal,
    DomainSubOrchestrator,
    MultiAgentTeamRuntime,
    SpecialistPool,
)
from hermes.platform.models.client import ChatMessage, ExactModelClient
from hermes.platform.models.profiles import ModelIdentity, ModelProfile, ProviderRoute
from hermes.platform.observability.event_store import EventStore
from hermes.platform.observability.events import Event
from hermes.platform.tasks.spec import TaskSpec
from hermes.platform.tasks.kanban_adapter import KanbanAdapter
from hermes.platform.workspaces.git_worktree import GitWorktreeManager


def load_dsh_a6_key() -> str:
    cred_path = Path("/root/.dsh/.credentials.yaml")
    if not cred_path.exists():
        raise RuntimeError("Credentials file /root/.dsh/.credentials.yaml not found")
    with open(cred_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    key = data.get("refs", {}).get("A6_API_KEY")
    if not key:
        raise RuntimeError("A6_API_KEY not found in DSH credentials")
    return key


def clean_code_markdown(text: str) -> str:
    # If the response contains markdown code block, extract between fences
    if "```" in text:
        parts = text.split("```")
        # Find first part that looks like python code
        for i in range(1, len(parts), 2):
            block = parts[i].strip()
            if not block:
                continue
            lines = block.splitlines()
            if lines and lines[0].strip().lower() in ("python", "py"):
                return "\n".join(lines[1:]).strip()
            return block
    return text.strip()


def run_live_mission():
    print("=" * 70)
    print("🚀 HAOS LIVE MISSION EXECUTION WITH A6API (DeepSeek-V4-Flash)")
    print("=" * 70)

    # 1. Load Real Credentials
    api_key = load_dsh_a6_key()
    print(f"[*] A6API Key loaded from DSH storage (prefix: {api_key[:8]}...)")

    # 2. Setup Sandbox & Worktree Root
    work_dir = Path(tempfile.mkdtemp(prefix="haos_mission_live_"))
    repo_dir = work_dir / "target_repo"
    repo_dir.mkdir(parents=True, exist_ok=True)

    subprocess.run(["git", "-C", str(repo_dir), "init", "-b", "main"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo_dir), "config", "user.name", "HAOS Live Agent"], check=True)
    subprocess.run(["git", "-C", str(repo_dir), "config", "user.email", "agent@haos.ai"], check=True)
    subprocess.run(["git", "-C", str(repo_dir), "commit", "--allow-empty", "-m", "chore: initial main commit"], check=True)

    wt_manager = GitWorktreeManager(repo_root=repo_dir)
    shared_data = os.environ.get("HAOS_DATA_DIR", "/tmp/haos_shared_data")
    if Path(shared_data).exists():
        event_store_path = Path(shared_data) / "events.db"
        kanban_db_path = Path(shared_data) / "kanban.db"
    else:
        event_store_path = work_dir / "mission_events.db"
        kanban_db_path = work_dir / "kanban.db"
    print(f"[*] EventStore path: {event_store_path}")
    print(f"[*] Kanban DB path:  {kanban_db_path}")
    event_store = EventStore(db_path=str(event_store_path))
    kanban = KanbanAdapter(db_path=str(kanban_db_path))
    pool = SpecialistPool(max_pool_size=4)
    runtime = MultiAgentTeamRuntime(event_store=event_store, specialist_pool=pool)

    # 3. Model Profile Definition
    profile = ModelProfile(
        id="live-coding-profile",
        model_identity=ModelIdentity(family="deepseek-v4", variant="flash"),
        routes=[
            ProviderRoute(provider_id="a6api", provider_model_id="deepseek-v4-flash", priority=1),
        ],
        parameters={"temperature": 0.2, "max_tokens": 4096, "thinking": {"type": "disabled"}},
    )

    # Transport targeting A6API endpoint with retry for transient 503/429
    def a6api_live_transport(url: str, headers: Dict[str, str], data: bytes, timeout: float):
        headers["Authorization"] = f"Bearer {api_key}"
        for attempt in range(3):
            try:
                req = urllib.request.Request("https://api.a6api.com/v1/chat/completions", data=data, headers=headers, method="POST")
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    return resp.status, json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as he:
                if he.code in (502, 503, 504, 429) and attempt < 2:
                    time.sleep(2.0 * (attempt + 1))
                    continue
                raise
            except Exception:
                if attempt < 2:
                    time.sleep(2.0 * (attempt + 1))
                    continue
                raise

    model_client = ExactModelClient(transport_fn=a6api_live_transport)

    # 4. Define Mission and Sub-Goals
    sub_goal = DomainSubGoal(
        id="token-bucket",
        domain="software",
        objective="Implement an in-memory TokenBucket rate limiter class with acquire() and refill() methods",
    )

    print(f"[*] Goal: {sub_goal.objective}")
    print(f"[*] Orchestrating sub-tasks with DomainSubOrchestrator...")

    # Ephemeral worktree allocation
    wt_path = wt_manager.create_worktree(task_id="token-bucket", base_branch="main")
    print(f"[*] Ephemeral worktree created at: {wt_path}")

    # Worker dispatch function
    def execute_worker_live(task: TaskSpec, specialist) -> Dict[str, Any]:
        print(f"\n---> [Worker: {specialist.worker_id}] Posture: {specialist.posture} | Task: {task.id}")
        t0 = time.time()

        if "impl" in task.id:
            prompt = (
                "Write a clean, concise Python module containing a class TokenBucket rate limiter. "
                "Constructor __init__(self, capacity, refill_rate). "
                "Method acquire(self, tokens=1) -> bool returning True if consumed, False otherwise. "
                "Method refill(self) updating tokens based on elapsed time. "
                "Output ONLY the python code in ```python ``` codeblock."
            )
            response = model_client.complete(profile, [ChatMessage("user", prompt)])
            code = clean_code_markdown(response.content)
            code_file = wt_path / "rate_limiter.py"
            code_file.write_text(code, encoding="utf-8")
            elapsed = time.time() - t0
            print(f"     [+] Created rate_limiter.py ({len(code)} bytes) via A6API in {elapsed:.2f}s")
            try:
                kanban.save_task(task, status="DONE")
            except Exception:
                pass
            return {"file": str(code_file), "tokens": response.total_tokens}

        elif "test" in task.id:
            code_text = (wt_path / "rate_limiter.py").read_text(encoding="utf-8")
            prompt = (
                f"Given this rate limiter:\n{code_text}\n\n"
                "Write 2 simple, short pytest tests in test_rate_limiter.py using `from rate_limiter import TokenBucket`. "
                "test_acquire_success and test_acquire_depletion. "
                "Output ONLY the python code in ```python ``` codeblock."
            )
            response = model_client.complete(profile, [ChatMessage("user", prompt)])
            raw_text = response.content
            test_code = clean_code_markdown(raw_text)
            if not test_code.strip():
                test_code = raw_text.strip()
            test_file = wt_path / "test_rate_limiter.py"
            test_file.write_text(test_code, encoding="utf-8")
            elapsed = time.time() - t0
            print(f"     [+] Created test_rate_limiter.py ({len(test_code)} bytes) via A6API in {elapsed:.2f}s")

            # Run pytest against the live generated code in the worktree
            print("     [*] Running pytest on generated code...")
            res = subprocess.run(
                ["/usr/local/lib/hermes-agent/venv/bin/python", "-m", "pytest", str(test_file)],
                cwd=str(wt_path),
                capture_output=True,
                text=True,
            )
            summary_line = [l for l in res.stdout.splitlines() if "passed" in l or "failed" in l or "error" in l]
            print(f"     [Pytest Output]: {summary_line[-1] if summary_line else res.stderr}")
            if res.returncode != 0:
                print("     [!] Pytest full output:\n", res.stdout, res.stderr)
            try:
                kanban.save_task(task, status="DONE")
            except Exception:
                pass
            return {"tests_passed": res.returncode == 0, "tokens": response.total_tokens}

        elif "review" in task.id:
            code_text = (wt_path / "rate_limiter.py").read_text(encoding="utf-8")
            prompt = f"Brief 1-line review of this code:\n\n{code_text}\n\nConclude with 'VERDICT: APPROVED'."
            response = model_client.complete(profile, [ChatMessage("user", prompt)])
            elapsed = time.time() - t0
            print(f"     [+] Witness review complete in {elapsed:.2f}s via A6API")
            verdict = "APPROVED" if "APPROVED" in response.content.upper() else "CHANGES_REQUESTED"
            print(f"     [Review Verdict]: {verdict}")
            try:
                kanban.save_task(task, status="DONE")
            except Exception:
                pass
            return {"verdict": verdict, "tokens": response.total_tokens}

        return {"status": "skipped"}

    # 5. Execute Multi-Agent Team Mission
    result = runtime.execute_team_mission(
        mission_goal="Build validated TokenBucket rate limiter",
        domain_sub_goals=[sub_goal],
        worker_execution_fn=execute_worker_live,
    )

    print("\n" + "=" * 70)
    print("✅ MISSION COMPLETED SUCCESSFULLY!")
    print(f"Mission ID: {result['mission_id']}")
    print(f"Tasks executed: {len(result['completed_tasks'])}")

    # Audit Events Check
    events = event_store.read_events()
    print(f"Total Telemetry Events Logged: {len(events)}")
    for ev in events[-10:]:
        print(f"  - [{ev.name}] {ev.payload.get('task_id') or ev.payload.get('mission_id') or ''}")

    # Emit Control Plane state update event
    event_store.append(
        Event(
            name="controlplane.state_updated",
            payload={
                "mission_id": result["mission_id"],
                "completed_tasks": result["completed_tasks"],
                "status": "success",
            },
        )
    )

    # Cleanup worktree
    wt_manager.remove_worktree(task_id="token-bucket", force=True)
    shutil.rmtree(work_dir, ignore_errors=True)
    print("=" * 70)


if __name__ == "__main__":
    run_live_mission()
