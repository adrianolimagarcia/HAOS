import os
import pathlib
import tempfile
import unittest

from hermes.platform.auth.device_flow import (
    DeviceAuthorizationDenied,
    DeviceAuthorizationExpired,
    DeviceFlowClient,
    DeviceFlowError,
    format_user_hint,
)
from hermes.platform.auth.vault import OAuthVault, SecretBroker, VaultError


def _with_temp_hermes_home(fn):
    """Decorator: roda o teste com HERMES_HOME isolado (nunca ~/.hermes)."""
    def wrapper(self, *args, **kwargs):
        old_home = os.environ.get("HERMES_HOME")
        with tempfile.TemporaryDirectory(prefix="haos-auth43-") as tmp:
            os.environ["HERMES_HOME"] = tmp
            try:
                return fn(self, tmp, *args, **kwargs)
            finally:
                if old_home is None:
                    os.environ.pop("HERMES_HOME", None)
                else:
                    os.environ["HERMES_HOME"] = old_home
    return wrapper


class TestGrantPolicyPersisted(unittest.TestCase):
    """Delta 43 — grants são decisão PERSISTIDA (mesmo auth.json, provider
    haos-grants), não dict em memória: outra instância/processo enxerga a
    decisão. Auto aprova na hora (legado K4); requires_approval fica pendente
    até approve_grant; sem grant/pendente resolve_credential_for é None."""

    @_with_temp_hermes_home
    def test_grant_survives_new_instance(self, home):
        sb = SecretBroker()
        sb.store_secret("db:master", "s3cr3t")
        sb.grant("compiler", "db:master")

        auth_file = pathlib.Path(home) / "auth.json"
        self.assertTrue(auth_file.exists())

        # Segunda instância (simula outro processo) vê o grant do disco.
        sb2 = SecretBroker()
        self.assertTrue(sb2.check_grant("compiler", "db:master"))
        self.assertEqual(sb2.resolve_credential_for("compiler", "db:master"), "s3cr3t")

    @_with_temp_hermes_home
    def test_requires_approval_pending_then_approved(self, home):
        sb = SecretBroker()
        sb.store_secret("prod:db", "top-secret")
        sb.grant("compiler", "prod:db", policy="requires_approval", requester="build-bot")

        # Pendente: gate NÃO libera (fail-safe) e aparece na fila.
        self.assertFalse(sb.check_grant("compiler", "prod:db"))
        self.assertIsNone(sb.resolve_credential_for("compiler", "prod:db"))
        pending = sb.pending_approvals()
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["credential_ref"], "prod:db")
        self.assertEqual(pending[0]["scope"], "compiler")
        self.assertEqual(pending[0]["status"], "pending")

        # Aprovação registra aprovador/rationale e libera (cross-instance).
        sb.approve_grant("compiler", "prod:db", approver="lead", rationale="necessário p/ build")
        sb2 = SecretBroker()
        self.assertTrue(sb2.check_grant("compiler", "prod:db"))
        self.assertEqual(sb2.resolve_credential_for("compiler", "prod:db"), "top-secret")
        self.assertEqual(sb2.pending_approvals(), [])

    @_with_temp_hermes_home
    def test_approve_unknown_grant_fails_closed(self, home):
        sb = SecretBroker()
        # Aprovar um grant que nunca foi solicitado NÃO cria acesso fantasma.
        with self.assertRaises(VaultError):
            sb.approve_grant("compiler", "ghost:ref", approver="lead")
        self.assertFalse(sb.check_grant("compiler", "ghost:ref"))

    @_with_temp_hermes_home
    def test_revoke_persists_and_clears_pending(self, home):
        sb = SecretBroker()
        sb.store_secret("api:key", "k-123")
        sb.grant("worker", "api:key", policy="requires_approval")
        sb.approve_grant("worker", "api:key", approver="ops")
        self.assertEqual(sb.resolve_credential_for("worker", "api:key"), "k-123")

        sb.revoke("worker", "api:key")
        sb2 = SecretBroker()  # cross-instance: revogação no disco
        self.assertFalse(sb2.check_grant("worker", "api:key"))
        self.assertIsNone(sb2.resolve_credential_for("worker", "api:key"))
        # Segredo continua no vault (revogar é decisão, não apagar credencial).
        self.assertEqual(sb2.resolve_credential("api:key"), "k-123")


class TestDeviceFlow(unittest.TestCase):
    """Delta 43 — device authorization (RFC 8628) sobre transporte INJETADO
    (forma B): o módulo nunca abre rede; peer fake fala o contrato."""

    def test_start_returns_authorization_fields(self):
        transport = lambda url, payload: {  # noqa: E731
            "device_code": "dc-1",
            "user_code": "ABCD-EFGH",
            "verification_uri": "https://provider.dev/device",
            "verification_uri_complete": "https://provider.dev/device?code=ABCD-EFGH",
            "expires_in": 600,
            "interval": 5,
        }
        flow = DeviceFlowClient(transport)
        auth = flow.start(
            "https://provider.dev/device_authorization", client_id="cli-agent", scope="repo write",
        )
        self.assertEqual(auth["user_code"], "ABCD-EFGH")
        hint = format_user_hint(auth)
        self.assertIn("https://provider.dev/device", hint)
        self.assertIn("ABCD-EFGH", hint)
        self.assertIn("https://provider.dev/device?code=ABCD-EFGH", hint)

    def test_poll_waits_pending_then_returns_token(self):
        calls = {"n": 0}

        def transport(url, payload):
            calls["n"] += 1
            if calls["n"] < 3:
                return {"error": "authorization_pending"}
            return {"access_token": "tok-999", "refresh_token": "ref-1", "expires_in": 3600}

        flow = DeviceFlowClient(transport)
        sleeps = []
        result = flow.poll(
            "https://provider.dev/token", client_id="cli-agent", device_code="dc-1",
            max_polls=5, base_interval_s=1.0,
            sleep_fn=lambda s: sleeps.append(s),
        )
        self.assertEqual(result["access_token"], "tok-999")
        self.assertEqual(calls["n"], 3)
        self.assertEqual(len(sleeps), 2)  # duas authorization_pending

    def test_poll_max_polls_exhausted_raises(self):
        transport = lambda url, payload: {"error": "authorization_pending"}  # noqa: E731
        flow = DeviceFlowClient(transport)
        with self.assertRaises(DeviceFlowError):
            flow.poll(
                "https://provider.dev/token", client_id="c", device_code="dc",
                max_polls=2, base_interval_s=1.0, sleep_fn=lambda s: None,
            )

    def test_poll_terminal_errors_typed(self):
        flow = DeviceFlowClient(lambda u, p: {"error": "access_denied"})
        with self.assertRaises(DeviceAuthorizationDenied):
            flow.poll("https://x/token", "c", "dc", sleep_fn=lambda s: None)

        flow2 = DeviceFlowClient(lambda u, p: {"error": "expired_token"})
        with self.assertRaises(DeviceAuthorizationExpired):
            flow2.poll("https://x/token", "c", "dc", sleep_fn=lambda s: None)

    def test_no_transport_fails_closed(self):
        with self.assertRaises(DeviceFlowError):
            DeviceFlowClient(http_post=None)  # type: ignore[arg-type]

    def test_slow_down_increases_interval(self):
        calls = {"n": 0}

        def transport(url, payload):
            calls["n"] += 1
            if calls["n"] == 1:
                return {"error": "slow_down"}
            return {"access_token": "t"}

        flow = DeviceFlowClient(transport)
        sleeps = []
        flow.poll("https://x/token", "c", "dc", base_interval_s=2.0,
                  sleep_fn=lambda s: sleeps.append(s))
        # slow_down soma 5s ao intervalo base de 2s.
        self.assertGreater(sleeps[0], 2.0)

    @_with_temp_hermes_home
    def test_success_persists_profile_in_real_vault(self, home):
        transport = lambda url, payload: {  # noqa: E731
            "access_token": "device-tok", "refresh_token": "device-ref", "expires_in": 3600,
        }
        flow = DeviceFlowClient(transport)
        tokens = flow.poll("https://x/token", "c", "dc", sleep_fn=lambda s: None)
        vault = OAuthVault()
        vault.register_profile("opaque-provider", {
            "access_token": tokens["access_token"],
            "refresh_token": tokens.get("refresh_token"),
            "expires_at": "2099-01-01T00:00:00Z",
        })
        vault2 = OAuthVault()
        self.assertEqual(vault2.get_valid_token("opaque-provider"), "device-tok")


if __name__ == "__main__":
    unittest.main()
