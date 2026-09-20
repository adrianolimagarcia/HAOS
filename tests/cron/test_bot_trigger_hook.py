"""Safe scheduler adapter hook for bot-chat cron triggers."""

from unittest.mock import patch


def test_due_bot_triggers_filters_without_claiming():
    from cron.scheduler_provider import InProcessCronScheduler

    jobs = [
        {"id": "bot", "deliver": "bot-chat"},
        {"id": "profiled", "deliver": ["telegram", "bot-chat:ops"]},
        {"id": "plain", "deliver": "telegram"},
    ]
    with patch("cron.jobs.get_due_jobs", return_value=jobs):
        with patch("cron.jobs.claim_job_for_fire") as claim:
            result = InProcessCronScheduler().get_due_bot_triggers()
    assert [job["id"] for job in result] == ["bot", "profiled"]
    claim.assert_not_called()
