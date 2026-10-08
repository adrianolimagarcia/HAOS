"""Behavior tests for the opt-in capability registry."""
import time

from tools.capability_registry_v2 import CapabilityOperation, CapabilityRegistryV2, is_v2_enabled


def _operation(**overrides):
    values = dict(
        id="search.docs", tool_ref="docs_search", name="Search documentation",
        description="Search documentation pages by query", parameters_compact={"query": "string"},
        parameters_schema={"type": "object", "required": ["query"], "properties": {
            "query": {"type": "string", "description": "Text to search"},
        }},
    )
    values.update(overrides)
    return CapabilityOperation(**values)


def test_register_export_compact_manifest_and_full_schema_reduction():
    registry = CapabilityRegistryV2()
    operation = _operation(description="A long description. " * 40)
    registry.register(operation)
    compact = registry.export_compact_manifest()
    assert registry.get_operation(operation.id) is operation
    assert compact[0]["parameters"]["required"] == ["query"]
    assert compact[0]["parameters"]["properties"]["query"]["type"] == "string"
    assert len(str(compact)) < len(str(operation.parameters_schema)) + len(operation.description)
    assert "risk_level" not in compact[0]


def test_search_matches_names_description_and_required_role():
    registry = CapabilityRegistryV2()
    operation = _operation(roles=["operator"])
    registry.register(operation)
    assert registry.search_capabilities("documentation search", role="operator") == [operation]
    assert registry.search_capabilities("documentation search", role="default") == []
    assert registry.search_capabilities("unrelated", role="operator") == []


def test_execution_validates_required_types_and_unexpected_fields():
    registry = CapabilityRegistryV2()
    registry.register(_operation(), lambda args: {"query": args["query"]})
    for args, message in [({}, "Missing required"), ({"query": 3}, "must be string"), ({"query": "x", "extra": 1}, "Unexpected")]:
        result = registry.execute("search.docs", args)
        assert result["error"]["status"] == 400
        assert message in result["error"]["message"]
        assert result["trace"]["verdict"] == "deny"


def test_role_denial_is_structured_and_audited():
    registry = CapabilityRegistryV2()
    registry.register(_operation(roles=["operator"]))
    result = registry.execute("search.docs", {"query": "x"}, caller_role="default")
    assert result["error"]["status"] == 403
    assert result["error"]["code"] == "forbidden"
    assert result["trace"]["op_id"] == "search.docs"
    assert result["trace"]["risk"] == "low"
    assert "timestamp" in registry.audit_traces[-1]


def test_read_only_restriction_and_permitted_mutator_role():
    registry = CapabilityRegistryV2()
    registry.register(_operation(read_only=False, roles=["default", "admin"]), lambda _args: "changed")
    denied = registry.execute("search.docs", {"query": "x"}, caller_role="default")
    allowed = registry.execute("search.docs", {"query": "x"}, caller_role="admin")
    assert denied["error"]["code"] == "read_only_enforced"
    assert allowed["result"] == "changed"


def test_timeout_and_handler_exception_are_structured():
    registry = CapabilityRegistryV2()
    operation = _operation(timeout_seconds=1)
    registry.register(operation, lambda _args: time.sleep(2))
    timed_out = registry.execute(operation.id, {"query": "x"})
    assert timed_out["error"]["code"] == "timeout"
    registry.register(operation, lambda _args: (_ for _ in ()).throw(RuntimeError("boom")))
    failed = registry.execute(operation.id, {"query": "x"})
    assert failed["error"]["code"] == "execution_error"


def test_fallback_runs_only_for_authorized_valid_operation():
    registry = CapabilityRegistryV2()
    registry.register(_operation())
    fallback = lambda args: {"found": args["query"]}
    assert registry.execute("search.docs", {"query": "x"}, fallback_handler=fallback)["result"] == {"found": "x"}
    denied = registry.execute("search.docs", {}, fallback_handler=fallback)
    assert denied["error"]["status"] == 400


def test_feature_flag_env_override_and_config_default(monkeypatch):
    monkeypatch.setenv("HAOS_MCP_CAPABILITY_REGISTRY_V2", "true")
    assert is_v2_enabled() is True
    monkeypatch.setenv("HAOS_MCP_CAPABILITY_REGISTRY_V2", "off")
    assert is_v2_enabled() is False
