"""Contract tests for HAOS Dynamic Forms & Schema-driven Operator Interventions."""

import json
from hermes.platform.forms.dynamic import (
    DynamicFormSpec,
    FormFieldSpec,
    extract_dynamic_form,
)
from tools.haos_form_tool import request_operator_form_tool


def test_dynamic_form_validation_success():
    spec = DynamicFormSpec(
        form_id="env_keys",
        title="Configuration Form",
        description="Provide required environment keys",
        fields=[
            FormFieldSpec(name="api_key", label="API Key", type="text", required=True),
            FormFieldSpec(name="port", label="Port", type="number", required=True),
            FormFieldSpec(name="env", label="Environment", type="select", options=["dev", "prod"]),
            FormFieldSpec(name="enable_tls", label="Enable TLS", type="boolean", required=False),
        ]
    )

    valid_payload = {
        "api_key": "sk-123456",
        "port": 8080,
        "env": "prod",
        "enable_tls": True,
    }
    is_valid, errors = spec.validate_submission(valid_payload)
    assert is_valid is True
    assert len(errors) == 0


def test_dynamic_form_validation_failures():
    spec = DynamicFormSpec(
        form_id="deploy_gate",
        title="Deploy Confirmation",
        description="Confirm parameters",
        fields=[
            FormFieldSpec(name="cluster", label="Cluster", type="select", options=["us-east", "eu-west"], required=True),
            FormFieldSpec(name="replicas", label="Replicas", type="number", required=True),
        ]
    )

    # Missing required cluster, invalid replicas string
    invalid_payload = {
        "cluster": "asia-south",
        "replicas": "not-a-number",
    }
    is_valid, errors = spec.validate_submission(invalid_payload)
    assert is_valid is False
    assert "cluster" in errors
    assert "replicas" in errors


def test_extract_dynamic_form_marker():
    spec = DynamicFormSpec(
        form_id="migration_form",
        title="Migration Plan",
        description="Pick schema strategy",
        fields=[FormFieldSpec(name="dry_run", label="Dry Run", type="boolean")]
    )
    marker = spec.to_kanban_marker()
    extracted = extract_dynamic_form(f"Pre-text before marker\n{marker}\nPost-text")

    assert extracted is not None
    assert extracted.form_id == "migration_form"
    assert extracted.title == "Migration Plan"
    assert len(extracted.fields) == 1
    assert extracted.fields[0].name == "dry_run"


def test_request_operator_form_tool():
    res_raw = request_operator_form_tool(
        form_id="test_form",
        title="Test Title",
        description="Testing description",
        fields=[{"name": "username", "label": "Username", "type": "text"}],
        task_id="TSK-999"
    )
    res = json.loads(res_raw)
    assert res["success"] is True
    assert "DYNAMIC_FORM:" in res["marker"]
    assert res["form"]["form_id"] == "test_form"
    assert res["form"]["task_id"] == "TSK-999"
