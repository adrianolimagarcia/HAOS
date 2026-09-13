import os
import pathlib
import tempfile
import unittest

from hermes.platform.auth.vault import SecretBroker, OAuthVault


def _with_temp_hermes_home(fn):
    """Decorator: roda o teste com HERMES_HOME isolado (nunca ~/.hermes)."""
    def wrapper(self, *args, **kwargs):
        old_home = os.environ.get("HERMES_HOME")
        with tempfile.TemporaryDirectory(prefix="haos-auth-") as tmp:
            os.environ["HERMES_HOME"] = tmp
            try:
                return fn(self, tmp, *args, **kwargs)
            finally:
                if old_home is None:
                    os.environ.pop("HERMES_HOME", None)
                else:
                    os.environ["HERMES_HOME"] = old_home
    return wrapper


class TestSecretBrokerRealVault(unittest.TestCase):
    """K4 — SecretBroker persiste no vault REAL do kernel (auth.json), não em
    memória: outro processo/instância enxerga o que foi gravado."""

    @_with_temp_hermes_home
    def test_roundtrip_persists_to_real_auth_file(self, home):
        sb = SecretBroker()
        sb.store_secret("openai:primary", "sk-test-12345")

        auth_file = pathlib.Path(home) / "auth.json"
        self.assertTrue(auth_file.exists())
        self.assertEqual(oct(auth_file.stat().st_mode & 0o777), "0o600")

        # Segunda instância (simula outro processo) lê do disco, não de memória.
        sb2 = SecretBroker()
        self.assertEqual(sb2.resolve_credential("openai:primary"), "sk-test-12345")

    @_with_temp_hermes_home
    def test_delete_and_list(self, home):
        sb = SecretBroker()
        sb.store_secret("a:b", "v1")
        sb.store_secret("c:d", "v2")
        self.assertEqual(sb.list_keys(), ["a:b", "c:d"])
        sb.delete_secret("a:b")
        self.assertIsNone(sb.resolve_credential("a:b"))
        self.assertEqual(sb.list_keys(), ["c:d"])

    @_with_temp_hermes_home
    def test_grants_gate_per_scope(self, home):
        sb = SecretBroker()
        sb.store_secret("db:master", "s3cr3t")
        sb.grant("compiler", "db:master")
        self.assertEqual(sb.resolve_credential_for("compiler", "db:master"), "s3cr3t")
        self.assertIsNone(sb.resolve_credential_for("vision-worker", "db:master"))
        sb.revoke("compiler", "db:master")
        self.assertIsNone(sb.resolve_credential_for("compiler", "db:master"))
        # Grant é decisão (gate); o segredo continua no vault.
        self.assertEqual(sb.resolve_credential("db:master"), "s3cr3t")

    @_with_temp_hermes_home
    def test_redaction_reuses_canonical_redactor(self, home):
        sb = SecretBroker()
        sb.store_secret("openai:primary", "sk-test-12345")
        text = "Use a chave sk-test-12345 para chamar a API."
        redacted = sb.redact(text)
        self.assertNotIn("sk-test-12345", redacted)
        # O vault ainda guarda o segredo intacto.
        self.assertEqual(sb.resolve_credential("openai:primary"), "sk-test-12345")


class TestOAuthVaultRealVault(unittest.TestCase):
    @_with_temp_hermes_home
    def test_profile_persists_and_expiry(self, home):
        vault = OAuthVault()
        vault.register_profile("github-oauth", {
            "access_token": "gho_secret_99",
            "expires_at": "2099-01-01T00:00:00Z",
        })
        self.assertEqual(vault.get_valid_token("github-oauth"), "gho_secret_99")

        # Segunda instância lê do disco.
        vault2 = OAuthVault()
        self.assertEqual(vault2.get_valid_token("github-oauth"), "gho_secret_99")

        # Token expirado não é devolvido.
        vault2.register_profile("expired-oauth", {
            "access_token": "old_token",
            "expires_at": "2000-01-01T00:00:00Z",
        })
        self.assertIsNone(vault2.get_valid_token("expired-oauth"))

        vault2.remove_profile("github-oauth")
        self.assertIsNone(vault2.get_valid_token("github-oauth"))


if __name__ == "__main__":
    unittest.main()
