from hermes.platform.context.memory.access import MemoryAccessContext

def test_private_team_project_and_global_acl():
    access = MemoryAccessContext("alice", frozenset({"team-a"}), frozenset({"project-a"}))
    assert access.can_read("private", {"owner_id": "alice"})
    assert not access.can_read("private", {"owner_id": "bob"})
    assert access.can_read("team", {"team_ids": ["team-a"]})
    assert not access.can_read("team", {"team_ids": ["team-b"]})
    assert access.can_read("project", {"project_ids": ["project-a"]})
    assert access.can_read("global", {})
