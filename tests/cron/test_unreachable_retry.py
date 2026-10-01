"""Cowork-inspired bounded automatic re-runs for cron fires that never reached the model.

Contract (cron/unreachable_retry.py): a recurring job whose run fails with a transient
network/DNS error before ANY model call gets its ``next_run_at`` pulled earlier along a
bounded ladder (5/15/30 min); a run that reaches the model resets the ladder, and the
ladder never fires past its last rung.
"""

from datetime import datetime, timedelta, timezone

import pytest

from cron import unreachable_retry as ur
from cron.jobs import create_job, get_job, mark_job_run


@pytest.fixture
def tmp_cron_home(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    return home


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def test_unreachable_failure_pulls_next_run_earlier_then_ladder_exhausts(tmp_cron_home):
    """Failed-unreachable runs re-fire on the 5/15/30-minute ladder instead of waiting a
    full period, and the ladder stops after its last rung (falls back to the schedule)."""
    # Interval, not a cron expression: the natural next fire is always a full day out. A
    # fixed clock time ("0 3 * * *") makes the 30-minute rung land past the natural fire
    # in the half hour before it, and plan_retry rightly yields to the schedule (CI red).
    job = create_job("nightly report", "every 24h")
    job_id = job["id"]

    now = datetime.now(timezone.utc)
    for i, delay in enumerate(ur.RETRY_DELAYS_SECONDS):
        assert mark_job_run(job_id, False, "ConnectError: dns", model_unreachable=True)
        j = get_job(job_id)
        nxt = datetime.fromisoformat(j["next_run_at"])
        # Pulled to roughly now + ladder delay, far before the daily occurrence.
        assert timedelta(0) < nxt - now <= timedelta(seconds=delay + 120), (
            f"attempt {i}: expected retry ~{delay}s out, got {nxt - now}")
        assert j[ur.STATE_KEY]["attempt"] == i + 1

    # Ladder exhausted: the next unreachable failure keeps the natural schedule.
    assert mark_job_run(job_id, False, "ConnectError: dns", model_unreachable=True)
    j = get_job(job_id)
    assert j.get(ur.STATE_KEY) is None
    assert datetime.fromisoformat(j["next_run_at"]) - now > timedelta(hours=1)


def test_reaching_the_model_resets_ladder_and_oneshots_never_retry(tmp_cron_home):
    """Any run that reached the model clears retry state; one-shots (pre-claimed
    dispatch, at-most-times #38758) never enter the ladder."""
    job = create_job("hourly sync", "every 12h")
    job_id = job["id"]
    assert mark_job_run(job_id, False, "ConnectError: dns", model_unreachable=True)
    assert get_job(job_id)[ur.STATE_KEY]["attempt"] == 1

    # A normal failed run (model reached) resets the ladder and stays on schedule.
    assert mark_job_run(job_id, False, "agent error")
    j = get_job(job_id)
    assert j.get(ur.STATE_KEY) is None
    now = datetime.now(timezone.utc)
    assert datetime.fromisoformat(j["next_run_at"]) - now > timedelta(hours=11)

    # One-shot: flag is ignored, no retry state, no resurrection.
    once = create_job("one shot", _iso(datetime.now(timezone.utc) + timedelta(minutes=1)))
    assert mark_job_run(once["id"], False, "ConnectError: dns", model_unreachable=True)
    remaining = get_job(once["id"])
    assert remaining is None or remaining.get(ur.STATE_KEY) is None


@pytest.mark.parametrize("rung_tail", ["finish", "crash"])
def test_ladder_reruns_do_not_spend_extra_repeat_budget(tmp_cron_home, monkeypatch, rung_tail):
    """A ladder re-run repeats an occurrence that already counted toward ``repeat``, so it must
    not count again. Otherwise an outage that outlasts the ladder retires a finite job with zero
    model calls, where the same outage with the ladder off costs it one run (#109990 fixed only
    the final-run notice). Drives the tick's claim hand-off and bookkeeping tail: the claimed
    snapshot's ``next_run_at`` has already moved to the natural slot when the run is recorded."""
    clock = [datetime.now(timezone.utc)]
    monkeypatch.setattr("cron.jobs._hermes_now", lambda: clock[0])
    monkeypatch.setattr(ur, "_hermes_now", lambda: clock[0])
    monkeypatch.setattr(sched, "finish_execution", lambda *_a, **_kw: None)
    runs = []
    monkeypatch.setattr(sched, "run_one_job", lambda job, **_kw: runs.append(job) or True)
    # "crash": a rung whose run raises leaves through the crash tail, which must not count it either.
    monkeypatch.setattr(sched, "run_job", lambda *_a, **_kw: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(sched, "mark_execution_running", lambda *_a, **_kw: {})
    monkeypatch.setattr(sched, "_deliver_crash_failure", lambda *_a, **_kw: (None, "suppressed"))
    job_id = create_job("digest", "every 24h", repeat=2)["id"]

    held = []
    for _ in range(1 + len(ur.RETRY_DELAYS_SECONDS)):  # the occurrence, then every rung
        clock[0] = datetime.fromisoformat(get_job(job_id)["next_run_at"]) + timedelta(seconds=1)
        due = next(d for d in get_due_jobs() if d["id"] == job_id)
        assert sched._process_due_job(dict(due, execution_id="exec"), None, None, False)
        run = runs[-1]
        if rung_tail == "crash" and held:
            assert sched._run_one_job_body(run) is False
            assert get_job(job_id)["repeat"]["completed"] == 1, "a crashed rung must not count"
            return
        run["_model_unreachable"] = True
        held.append(ur.will_retry(run))
        assert sched._finish_completed_run(
            sched._RunDelivery(job=run, success=False, error="ConnectError: dns"),
            run["fire_claim"]["by"], "exec")
        assert get_job(job_id)["repeat"]["completed"] == 1, "only the occurrence itself counts"

    j = get_job(job_id)
    assert j["state"] == "scheduled" and j.get(ur.STATE_KEY) is None
    assert datetime.fromisoformat(j["next_run_at"]) - clock[0] > timedelta(hours=23)
    # A rung on the last slot no longer completes the job, so its notice is held as well.
    assert held == [True, True, True, False]
    # But a rung whose limit was edited down to the count does retire the job: send its notice.
    assert not ur.will_retry(dict(runs[1], repeat={"times": 1, "completed": 1}))
