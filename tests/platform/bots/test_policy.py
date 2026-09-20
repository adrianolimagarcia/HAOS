import pytest
from hermes.platform.bots.spec import BotSpec, BotPolicy
from hermes.platform.bots.manager import BotSpecManager
from hermes.platform.observability.event_store import EventStore

def manager(spec):
    m=BotSpecManager(EventStore()); m.register(spec); return m

def test_policy_roundtrip():
    p=BotPolicy(tools=["shell"], workspace="git_worktree", max_cost_usd=2, max_runtime_minutes=5)
    assert BotSpec.from_dict(BotSpec("b","B",policy=p).to_dict()).policy == p

def test_submission_denies_capability_and_limits():
    m=manager(BotSpec("b","B", policy=BotPolicy(tools=["shell"], max_cost_usd=1, max_runtime_minutes=5)))
    with pytest.raises(PermissionError): m.submit("b","x", required_capabilities=["network"])
    with pytest.raises(PermissionError): m.submit("b","x", max_cost_usd=2)
    with pytest.raises(PermissionError): m.submit("b","x", max_runtime_minutes=6)

def test_submission_allows_policy():
    m=manager(BotSpec("b","B", policy=BotPolicy(tools=["shell"], workspace="git_worktree", max_cost_usd=1, max_runtime_minutes=5)))
    task=m.submit("b","x", required_capabilities=["shell"], workspace_type="git_worktree")
    assert task.goal == "x"
