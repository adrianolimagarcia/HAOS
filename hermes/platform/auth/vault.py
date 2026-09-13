"""SecretBroker / OAuthVault — K4 (Emenda 13) + política de grant (delta 43)
sobre o vault REAL do Hermes.

O mock em memória acabou: segredos e perfis OAuth persistem no ``auth.json``
canônico do kernel (``$HERMES_HOME/auth.json``, 0o600, flock cross-process,
escrita atômica, corrupt-preservada) através das primitivas públicas
``hermes_cli.auth.write_credential_pool`` / ``read_credential_pool``.

Namespace HAOS: usamos um provider-id reservado (``haos`` / ``haos-oauth`` /
``haos-grants``) no ``credential_pool`` — o pool é só um mapa provider ->
lista de entradas e o kernel nunca consulta esses ids, então nada colide com
credenciais reais dos providers. Redação de segredos reutiliza o helper
canônico ``agent.redact.redact_sensitive_text`` (o mesmo usado nos
prompts/contexto).

Política de grant (delta 43, P1): o gate de grant também vira decisão
PERSISTIDA (provider ``haos-grants`` no mesmo auth.json) — antes vivia num
dict em memória e morria no restart do processo. Um grant pode ser
``policy="auto"`` (libera imediato, comportamento legado do K4) ou
``policy="requires_approval"`` (entra em ``pending_approvals()`` até
``approve_grant`` registrar aprovador + rationale). ``resolve_credential_for``
é fail-safe: sem grant ou grant pendente -> None, o segredo nunca vaza.

Regras v1.1: imports do kernel são LAZY (dentro dos métodos) — o módulo fica
stdlib-only em nível de módulo; sem segundo store, sem cripto inventada (o
vault do Hermes é 0o600 + dir 0o700 — aceitamos a semântica upstream).
"""

import time
from typing import Any, Dict, List, Optional

# Provider-ids reservados no credential_pool do vault real. O kernel nunca lê
# estes ids (ele consulta providers reais), então o namespace HAOS é aditivo.
_POOL_PROVIDER = "haos"          # segredos opacos (SecretBroker)
_OAUTH_POOL_PROVIDER = "haos-oauth"  # perfis OAuth genéricos (OAuthVault)
_GRANTS_POOL_PROVIDER = "haos-grants"  # política de grant persistida (delta 43)

# Campos aceitos como "secreto" numa entrada do pool (retornados por
# resolve_credential). O vault upstream não tem um campo "secret" genérico —
# usamos o campo real do pool quando presente, senão o nosso campo HAOS.
_SECRET_FIELDS = ("value", "secret", "access_token", "api_key")


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _import_auth():
    """Import lazy do facade de auth do kernel (evita acoplar o módulo)."""
    from hermes_cli import auth as _auth_api  # noqa: PLC0415 - lazy por design
    return _auth_api


class VaultError(RuntimeError):
    pass


class SecretBroker:
    """Secret Broker real sobre o vault do Hermes (sem mock em memória)."""

    def __init__(self):
        self._auth = None

    def _api(self):
        if self._auth is None:
            self._auth = _import_auth()
        return self._auth

    # ------------------------------------------------------------------ #
    # store
    # ------------------------------------------------------------------ #
    def store_secret(self, key: str, value: str) -> str:
        """Persiste um segredo no vault real sob o namespace HAOS.

        ``write_credential_pool`` faz lock + merge atômico: entradas de outras
        chaves que existem em disco são preservadas automaticamente.
        """
        if not isinstance(key, str) or not key or not isinstance(value, str):
            raise VaultError("SecretBroker.store_secret requer key e value strings não vazias")
        entry: Dict[str, Any] = {"id": key, "value": value, "stored_at": _now_iso()}
        self._api().write_credential_pool(_POOL_PROVIDER, [entry])
        return key

    def resolve_credential(self, credential_ref: str) -> Optional[str]:
        """Lê o segredo do vault real (cross-process: outro processo enxerga)."""
        pool = self._api().read_credential_pool(_POOL_PROVIDER)
        for entry in pool if isinstance(pool, list) else []:
            if isinstance(entry, dict) and entry.get("id") == credential_ref:
                for field in _SECRET_FIELDS:
                    if isinstance(entry.get(field), str) and entry[field]:
                        return entry[field]
                return None
        return None

    def delete_secret(self, key: str) -> bool:
        """Remove a entrada da chave (``removed_ids`` — merge não a recria)."""
        self._api().write_credential_pool(_POOL_PROVIDER, [], removed_ids=[key])
        return True

    def list_keys(self) -> List[str]:
        pool = self._api().read_credential_pool(_POOL_PROVIDER)
        return sorted(
            str(e["id"]) for e in pool if isinstance(e, dict) and e.get("id")
        )

    # ------------------------------------------------------------------ #
    # grants (política HAOS por agente/ferramenta — decisão persistida,
    # delta 43: provider haos-grants no MESMO auth.json, nunca em memória)
    # ------------------------------------------------------------------ #
    def redact(self, text: str) -> str:
        """Redige segredos no texto usando o helper canônico do kernel."""
        from agent.redact import redact_sensitive_text  # noqa: PLC0415 - lazy

        return redact_sensitive_text(text, force=True)

    def grant(
        self,
        scope: str,
        credential_ref: str,
        *,
        policy: str = "auto",
        requester: Optional[str] = None,
    ) -> str:
        """Concede (ou solicita) acesso de *scope* a um credential_ref.

        Política (delta 43):
        * ``policy="auto"`` (legado K4) — grant nasce aprovado; release
          imediato via ``resolve_credential_for``.
        * ``policy="requires_approval"`` — grant nasce pendente; entra em
          ``pending_approvals()`` até ``approve_grant`` registrar aprovador.

        O grant é uma decisão PERSISTIDA (provider ``haos-grants`` no mesmo
        auth.json); o segredo em si continua no vault sob ``haos``.
        """
        grant_id = self._grant_id(scope, credential_ref)
        self._write_grant({
            "id": grant_id,
            "scope": scope,
            "credential_ref": credential_ref,
            "policy": policy,
            "status": "approved" if policy != "requires_approval" else "pending",
            "requester": requester,
            "granted_at": _now_iso(),
        })
        return grant_id

    def approve_grant(
        self,
        scope: str,
        credential_ref: str,
        approver: str,
        rationale: Optional[str] = None,
    ) -> None:
        """Aprova um grant pendente (``requires_approval``).

        Exige grant pendente registrado — sem grant, não há o que aprovar
        (fail-closed: aprovar do nada lança ``VaultError`` em vez de criar
        acesso fantasma).
        """
        existing = self._find_grant(scope, credential_ref)
        if existing is None:
            raise VaultError(f"grant inexistente: {scope}::{credential_ref}")
        updated = dict(existing)
        updated["status"] = "approved"
        updated["approver"] = approver
        updated["rationale"] = rationale
        updated["approved_at"] = _now_iso()
        self._write_grant(updated)

    def pending_approvals(self) -> List[Dict[str, Any]]:
        """Grants com ``policy="requires_approval"`` ainda pendentes.

        Decisão fail-safe: sem dado (ou sem grants) a lista é vazia.
        """
        return [
            g for g in self._grants()
            if g.get("policy") == "requires_approval" and g.get("status") != "approved"
        ]

    def revoke(self, scope: str, credential_ref: str) -> None:
        self._api().write_credential_pool(
            _GRANTS_POOL_PROVIDER, [], removed_ids=[self._grant_id(scope, credential_ref)]
        )

    def check_grant(self, scope: str, credential_ref: str) -> bool:
        """Verdadeiro apenas quando o grant existe e está APROVADO."""
        grant = self._find_grant(scope, credential_ref)
        return bool(grant and grant.get("status") == "approved")

    def resolve_credential_for(self, scope: str, credential_ref: str) -> Optional[str]:
        """resolve_credential + gate de grant aprovado (None sem permissão)."""
        if not self.check_grant(scope, credential_ref):
            return None
        return self.resolve_credential(credential_ref)

    # -- helpers de persistência de grants ------------------------------- #
    def _grant_id(self, scope: str, credential_ref: str) -> str:
        return f"{scope}::{credential_ref}"

    def _grants(self) -> List[Dict[str, Any]]:
        pool = self._api().read_credential_pool(_GRANTS_POOL_PROVIDER)
        return [g for g in pool if isinstance(g, dict) and g.get("id")]

    def _find_grant(self, scope: str, credential_ref: str) -> Optional[Dict[str, Any]]:
        grant_id = self._grant_id(scope, credential_ref)
        for g in self._grants():
            if g.get("id") == grant_id:
                return g
        return None

    def _write_grant(self, grant: Dict[str, Any]) -> None:
        # Merge atômico do pool: reescreve o grant alterado preservando os
        # demais grants que existem em disco (concorrência cross-process).
        self._api().write_credential_pool(_GRANTS_POOL_PROVIDER, [grant])


class OAuthVault:
    """Perfis OAuth genéricos persistidos no vault real (namespace ``haos-oauth``).

    Para providers OAuth reais do Hermes (nous/openai-codex/xai-oauth/spotify),
    o caminho canônico é o fluxo upstream (``hermes auth login`` +
    ``OAUTH_PROVIDER_FLOWS``); esta classe guarda perfis adicionais/opacos com a
    mesma persistência do vault, sem tocar no estado do provider ativo.
    """

    def __init__(self):
        self._auth = None

    def _api(self):
        if self._auth is None:
            self._auth = _import_auth()
        return self._auth

    def register_profile(self, profile_id: str, tokens: Dict[str, Any]) -> str:
        if not isinstance(tokens, dict) or not isinstance(tokens.get("access_token"), str):
            raise VaultError(
                "OAuthVault.register_profile requer tokens dict com 'access_token' string"
            )
        entry: Dict[str, Any] = {
            "id": profile_id,
            "access_token": tokens["access_token"],
            "refresh_token": tokens.get("refresh_token"),
            "scope": tokens.get("scope"),
            "expires_at": tokens.get("expires_at"),
            "stored_at": _now_iso(),
        }
        self._api().write_credential_pool(_OAUTH_POOL_PROVIDER, [entry])
        return profile_id

    def get_valid_token(self, profile_id: str) -> Optional[str]:
        """Access token ainda válido (não expirado) para o perfil, ou None.

        Sem expiração registrada, considera válido (perfis opacos não têm
        refresh automático; providers reais usam o fluxo upstream).
        """
        pool = self._api().read_credential_pool(_OAUTH_POOL_PROVIDER)
        for entry in pool if isinstance(pool, list) else []:
            if isinstance(entry, dict) and entry.get("id") == profile_id:
                token = entry.get("access_token")
                if not isinstance(token, str) or not token:
                    return None
                expires = entry.get("expires_at")
                if expires and _is_expired_iso(expires):
                    return None
                return token
        return None

    def list_profiles(self) -> List[str]:
        pool = self._api().read_credential_pool(_OAUTH_POOL_PROVIDER)
        return sorted(
            str(e["id"]) for e in pool if isinstance(e, dict) and e.get("id")
        )

    def remove_profile(self, profile_id: str) -> bool:
        self._api().write_credential_pool(_OAUTH_POOL_PROVIDER, [], removed_ids=[profile_id])
        return True


def _is_expired_iso(expires_at: Any) -> bool:
    """True se *expires_at* ISO (com/sem offset) já passou."""
    import datetime as _dt

    if not isinstance(expires_at, str):
        return False
    raw = expires_at.strip()
    try:
        if raw.endswith("Z"):
            parsed = _dt.datetime.fromisoformat(raw[:-1] + "+00:00")
        else:
            parsed = _dt.datetime.fromisoformat(raw)
    except ValueError:
        return False
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_dt.timezone.utc)
    return parsed <= _dt.datetime.now(_dt.timezone.utc)
