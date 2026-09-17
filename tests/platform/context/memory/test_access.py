from hermes.platform.context.memory.access import MemoryAccessContext

def test_private_team_project_and_global_acl():
    access = MemoryAccessContext("alice", frozenset({"team-a"}), frozenset({"project-a"}))
    assert access.can_read("private", {"owner_id": "alice"})
    assert not access.can_read("private", {"owner_id": "bob"})
    assert access.can_read("team", {"team_ids": ["team-a"]})
    assert not access.can_read("team", {"team_ids": ["team-b"]})
    assert access.can_read("project", {"project_ids": ["project-a"]})
    assert access.can_read("global", {})


def test_write_requires_principal_membership():
    access = MemoryAccessContext("alice", frozenset({"team-a"}), frozenset({"project-a"}))
    access.require_write("private", {"owner_id": "alice"})
    access.require_write("team", {"team_ids": ["team-a"]})
    try:
        access.require_write("project", {"project_ids": ["project-b"]})
    except PermissionError:
        pass
    else:
        raise AssertionError("cross-project write was accepted")
