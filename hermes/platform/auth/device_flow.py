"""Device Authorization Flow (RFC 8628) — delta 43 (P1, padrões OpenClaw).

Fluxo de autorização para clients sem browser/redirect (CLI, agente, worker):
o client pede um ``device_code`` + ``user_code`` + ``verification_uri``,
mostra o código ao operador humano, e POLLA o endpoint de token até o
operador aprovar no browser. Depois troca o ``device_code`` por tokens.

Forma (B): transporte como SEAM injetado. Este módulo é stdlib-only e NUNCA
abre rede sozinho — ``DeviceFlowClient`` recebe um ``http_post`` callable
(p.ex. um peer out-of-process, no estilo do fetcher do delta 39) e monta o
protocolo sobre ele. Sem transporte injetado, o client falha fechado
(``DeviceFlowError``): quem integra decide onde/como a rede acontece.

Contrato do transporte (quem injeta implementa):
    http_post(url, payload: dict) -> dict   # resposta JSON do endpoint

O fluxo segue o RFC 8628:
* start: POST no device_authorization_endpoint -> device_code/user_code/
  verification_uri/expires_in/interval.
* poll: POST no token_endpoint com grant_type
  urn:ietf:params:oauth:grant-type:device_code; respostas de erro
  authorization_pending/slow_down são esperadas (continua polling),
  access_denied/expired_token encerram com erro tipado.
* Sucesso: access_token (+ refresh_token/expires_in) devolvido — o chamador
  persiste via ``OAuthVault.register_profile`` (mesmo vault real).
"""

import time
from typing import Any, Callable, Dict, Optional


class DeviceFlowError(RuntimeError):
    """Erro de protocolo do device flow (fail-closed)."""


class DeviceAuthorizationDenied(DeviceFlowError):
    """O operador negou a autorização (access_denied)."""


class DeviceAuthorizationExpired(DeviceFlowError):
    """O device_code expirou sem aprovação (expired_token)."""


# Erros esperados durante o polling — não são falhas do client.
_POLLING_ERRORS = ("authorization_pending", "slow_down")


class DeviceFlowClient:
    """Client do device authorization flow sobre um transporte injetado.

    ``http_post`` é OBRIGATÓRIO (fail-closed: sem transporte não há rede e o
    client recusa operar). Endpoints são fornecidos pelo chamador a partir da
    descoberta do provider (RFC 8414) ou da config do provider.
    """

    def __init__(self, http_post: Callable[[str, Dict[str, Any]], Dict[str, Any]]):
        if not callable(http_post):
            raise DeviceFlowError("DeviceFlowClient requer http_post injetado (sem rede em processo)")
        self._http_post = http_post

    # ------------------------------------------------------------------ #
    # start
    # ------------------------------------------------------------------ #
    def start(
        self,
        device_authorization_endpoint: str,
        client_id: str,
        scope: Optional[str] = None,
        *,
        extra_params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Inicia o fluxo: devolve os campos do RFC 8628 (dict com
        ``device_code``, ``user_code``, ``verification_uri``, ``expires_in``,
        ``interval``) + qualquer campo extra do provider."""
        payload: Dict[str, Any] = {"client_id": client_id}
        if scope:
            payload["scope"] = scope
        if extra_params:
            payload.update(extra_params)
        response = self._post(device_authorization_endpoint, payload)
        if not isinstance(response.get("device_code"), str) or not response["device_code"]:
            raise DeviceFlowError("device authorization sem device_code na resposta")
        if not isinstance(response.get("user_code"), str) or not response["user_code"]:
            raise DeviceFlowError("device authorization sem user_code na resposta")
        if not isinstance(response.get("verification_uri"), str) or not response["verification_uri"]:
            raise DeviceFlowError("device authorization sem verification_uri na resposta")
        return dict(response)

    # ------------------------------------------------------------------ #
    # poll
    # ------------------------------------------------------------------ #
    def poll(
        self,
        token_endpoint: str,
        client_id: str,
        device_code: str,
        *,
        max_polls: Optional[int] = None,
        base_interval_s: float = 5.0,
        sleep_fn: Callable[[float], None] = time.sleep,
    ) -> Dict[str, Any]:
        """Polling do token (RFC 8628 §3.5) até aprovação ou esgotamento.

        Devolve o payload de tokens (access_token presente) quando o operador
        aprova. Lança tipado em access_denied/expired_token. Para erros de
        polling (authorization_pending/slow_down) itera respeitando o
        ``interval`` (ou ``base_interval_s``); sem ``max_polls`` só encerra em
        erro terminal/sucesso. ``sleep_fn`` é seam de teste (default time.sleep).
        """
        if not isinstance(device_code, str) or not device_code:
            raise DeviceFlowError("poll requer device_code string")
        polls = 0
        interval_s = max(float(base_interval_s), 1.0)
        while max_polls is None or polls < max_polls:
            payload = {
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                "client_id": client_id,
                "device_code": device_code,
            }
            response = self._post(token_endpoint, payload)
            error = response.get("error")
            if error in _POLLING_ERRORS:
                polls += 1
                if error == "slow_down":
                    interval_s += 5.0  # RFC 8628 §3.5
                sleep_fn(interval_s)
                continue
            if error == "access_denied":
                raise DeviceAuthorizationDenied(
                    str(response.get("error_description") or "operador negou a autorização")
                )
            if error == "expired_token":
                raise DeviceAuthorizationExpired(
                    str(response.get("error_description") or "device_code expirou")
                )
            if error:
                raise DeviceFlowError(
                    f"erro de token: {error}: {response.get('error_description') or ''}".strip()
                )
            if not isinstance(response.get("access_token"), str) or not response["access_token"]:
                raise DeviceFlowError("token endpoint respondeu sem access_token")
            return dict(response)
        raise DeviceFlowError(
            f"polling esgotou após {polls} tentativa(s) sem aprovação"
        )

    # ------------------------------------------------------------------ #
    # helpers
    # ------------------------------------------------------------------ #
    def _post(self, url: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(url, str) or not url:
            raise DeviceFlowError("url vazia no device flow")
        try:
            response = self._http_post(url, payload)
        except DeviceFlowError:
            raise
        except Exception as exc:  # noqa: BLE001 - transporte quebrou => fail-closed
            raise DeviceFlowError(f"transporte do device flow falhou: {exc!r}") from exc
        if not isinstance(response, dict):
            raise DeviceFlowError("transporte do device flow devolveu não-dict")
        return response


def format_user_hint(authorization: Dict[str, Any]) -> str:
    """Mensagem pronta p/ exibir ao operador (CLI/UI): URL + código."""
    uri = authorization.get("verification_uri")
    code = authorization.get("user_code")
    complete = authorization.get("verification_uri_complete")
    if not uri or not code:
        return ""
    base = f"Autorize em {uri} com o código: {code}"
    if isinstance(complete, str) and complete:
        return f"{base}\n(ou abra direto: {complete})"
    return base


__all__ = [
    "DeviceFlowClient",
    "DeviceFlowError",
    "DeviceAuthorizationDenied",
    "DeviceAuthorizationExpired",
    "format_user_hint",
]
