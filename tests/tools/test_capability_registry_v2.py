"""Behavior tests for the opt-in capability registry."""
import concurrent.futures
import time
import pytest

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


def test_feature_flag_default_is_disabled(monkeypatch):
    monkeypatch.delenv("HAOS_MCP_CAPABILITY_REGISTRY_V2", raising=False)
    # Default without env var should be false
    assert is_v2_enabled() is False


def test_server_side_type_safety_and_nested_validation():
    registry = CapabilityRegistryV2()
    op = _operation(
        id="cluster.configure",
        parameters_schema={
            "type": "object",
            "required": ["cluster_id", "replicas", "config"],
            "properties": {
                "cluster_id": {"type": "string"},
                "replicas": {"type": "integer"},
                "tags": {"type": "array", "items": {"type": "string"}},
                "config": {
                    "type": "object",
                    "required": ["endpoint"],
                    "properties": {
                        "endpoint": {"type": "string"},
                        "secure": {"type": "boolean"},
                    },
                },
            },
        },
    )
    registry.register(op, lambda args: {"status": "ok"})

    # Missing nested required
    res = registry.execute("cluster.configure", {"cluster_id": "c1", "replicas": 3, "config": {}})
    assert res["error"]["status"] == 400
    assert "Missing required fields: endpoint" in res["error"]["message"]

    # Boolean passed where integer expected (in Python bool is subclass of int)
    res = registry.execute("cluster.configure", {"cluster_id": "c1", "replicas": True, "config": {"endpoint": "https://api"}})
    assert res["error"]["status"] == 400
    assert "must be integer" in res["error"]["message"]

    # Array items invalid type
    res = registry.execute("cluster.configure", {
        "cluster_id": "c1", "replicas": 2, "tags": ["valid", 123],
        "config": {"endpoint": "https://api"}
    })
    assert res["error"]["status"] == 400
    assert "tags[1] must be string" in res["error"]["message"]

    # Spurious nested argument
    res = registry.execute("cluster.configure", {
        "cluster_id": "c1", "replicas": 2,
        "config": {"endpoint": "https://api", "spurious": "bad"}
    })
    assert res["error"]["status"] == 400
    assert "Unexpected argument: spurious" in res["error"]["message"]


def test_adversarial_role_bypass_and_injection():
    registry = CapabilityRegistryV2()
    registry.register(_operation(roles=["secops", "cluster-admin"]))

    # Role spoofing / injection strings
    for bad_role in ["", "   ", "default", "secops;admin", "*", "root", None]:
        res = registry.execute("search.docs", {"query": "test"}, caller_role=bad_role)
        assert res["ok"] is False
        assert res["error"]["status"] == 403
        assert res["trace"]["verdict"] == "deny"
        assert res["trace"]["status"] == 403


def test_adversarial_read_only_mutation_prevention():
    registry = CapabilityRegistryV2()
    executed = []
    registry.register(
        _operation(id="db.drop", read_only=False, roles=["operator", "dba"]),
        lambda args: executed.append(args) or "dropped"
    )

    # Restricted and default callers cannot mutate even if role matches
    for restricted in ["default", "restricted", "read_only"]:
        res = registry.execute("db.drop", {"query": "drop all"}, caller_role=restricted)
        assert res["ok"] is False
        assert res["error"]["status"] == 403
        assert res["error"]["code"] in {"forbidden", "read_only_enforced"}

    assert len(executed) == 0

    # Authorized non-restricted role succeeds
    res = registry.execute("db.drop", {"query": "drop table"}, caller_role="dba")
    assert res["ok"] is True
    assert res["result"] == "dropped"
    assert len(executed) == 1


def test_timeout_returns_504_and_records_trace():
    registry = CapabilityRegistryV2()
    registry.register(_operation(id="slow.op", timeout_seconds=1), lambda _args: time.sleep(1.5))
    res = registry.execute("slow.op", {"query": "x"})
    assert res["ok"] is False
    assert res["error"]["status"] == 504
    assert res["error"]["code"] == "timeout"
    assert res["trace"]["status"] == 504
    assert res["trace"]["verdict"] == "error"
    assert res["trace"]["duration_ms"] >= 900


def test_audit_trace_isolation_and_caller_metadata():
    registry = CapabilityRegistryV2()
    registry.register(_operation(id="query.audit", risk_level="medium"), lambda args: "done")

    res = registry.execute("query.audit", {"query": "whoami"}, caller_role="default", caller_profile="agent-alpha")
    assert res["ok"] is True
    trace = res["trace"]
    assert trace["caller_role"] == "default"
    assert trace["caller_profile"] == "agent-alpha"
    assert trace["risk"] == "medium"
    assert trace["status"] == 200
    assert "timestamp" in trace

    traces = registry.get_audit_traces()
    assert len(traces) == 1
    assert traces[0] == trace


def test_registration_validation_rules():
    registry = CapabilityRegistryV2()

    # Invalid risk level
    with pytest.raises(ValueError, match="risk_level"):
        registry.register(_operation(risk_level="extreme"))

    # Invalid cost tier
    with pytest.raises(ValueError, match="cost_tier"):
        registry.register(_operation(cost_tier="expensive"))

    # Missing tool_ref
    with pytest.raises(ValueError, match="tool_ref"):
        registry.register(_operation(tool_ref=""))


def test_concurrent_execution_thread_safety():
    registry = CapabilityRegistryV2()
    registry.register(_operation(id="op.concurrent"), lambda args: args["query"])

    def run_worker(idx):
        return registry.execute("op.concurrent", {"query": f"worker_{idx}"})

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(run_worker, range(20)))

    for r in results:
        assert r["ok"] is True

    assert len(registry.audit_traces) == 20


def test_auth_role_spoofing_fail_closed():
    from tools.capability_registry_v2 import (
        CapabilityRegistryV2,
        create_trusted_security_context,
        create_anonymous_security_context,
        SecurityContext,
    )
    registry = CapabilityRegistryV2()
    op = _operation(id="admin.drop_db", roles=["admin"], read_only=False)
    registry.register(op, lambda args: {"dropped": True})

    # 1. Tentativa de spoofing via caller_role sem contexto
    res = registry.execute("admin.drop_db", {"query": "drop"}, caller_role="admin")
    assert res["ok"] is False
    assert res["error"]["status"] == 403
    assert res["error"]["code"] == "role_spoofing_prevented"

    # 2. Contexto anônimo tentando role admin
    anon_ctx = create_anonymous_security_context()
    res = registry.execute("admin.drop_db", {"query": "drop"}, security_context=anon_ctx)
    assert res["ok"] is False
    assert res["error"]["status"] == 403
    assert res["error"]["code"] == "forbidden"

    # 3. Contexto fraudulento não autenticado com role="admin"
    forged_ctx = SecurityContext(principal_id="attacker", role="admin", authenticated=False)
    res = registry.execute("admin.drop_db", {"query": "drop"}, security_context=forged_ctx)
    assert res["ok"] is False
    assert res["error"]["status"] == 403
    assert res["error"]["code"] == "authentication_required"

    # 4. Contexto confiável e autenticado
    trusted_ctx = create_trusted_security_context(principal_id="sys_admin", role="admin")
    res = registry.execute("admin.drop_db", {"query": "drop"}, security_context=trusted_ctx)
    assert res["ok"] is True
    assert res["result"] == {"dropped": True}
    assert res["trace"]["caller_role"] == "admin"


def test_timeout_worker_active_termination_no_residual_side_effects(tmp_path):
    import time
    marker_file = tmp_path / "zombie_marker.txt"

    def slow_mutating_worker(args):
        time.sleep(0.4)
        marker_file.write_text("zombie_executed")
        return {"status": "ok"}

    registry = CapabilityRegistryV2()
    # Registra com timeout de 1 segundo, mas vamos testar diretamente o _run_with_timeout com timeout curto
    from tools.capability_registry_v2 import _run_with_timeout

    with pytest.raises(TimeoutError):
        _run_with_timeout(slow_mutating_worker, {}, 0.1)

    # Espera tempo suficiente para o worker ter executado se ainda estivesse vivo
    time.sleep(0.5)

    # O arquivo NÃO deve existir porque o worker foi terminado ativamente pelo SO
    assert not marker_file.exists(), "Worker zumbi continuou vivo após o timeout e executou mutação!"


