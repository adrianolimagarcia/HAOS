"""Antigravity Local Wrapper Model Provider Profile Plugin for HAOS.

Conecta ao wrapper local Antigravity em http://127.0.0.1:8790/v1 (ou porta configurada em ANTIGRAVITY_PROXY_PORT).
Disponibiliza os modelos Gemini com reasoning passback e thinking level dinâmico.
"""

import os
from providers import register_provider
from providers.base import ProviderProfile

_port = os.getenv("ANTIGRAVITY_PROXY_PORT", "8790")
_default_base = f"http://127.0.0.1:{_port}/v1"

antigravity_provider = ProviderProfile(
    name="antigravity",
    aliases=("agy", "antigravity-wrapper", "antigravity-local"),
    display_name="Antigravity Local Wrapper",
    description="Local Antigravity OAuth-bridged Gemini gateway (OpenAI-compatible)",
    env_vars=("ANTIGRAVITY_API_KEY", "ANTIGRAVITY_BASE_URL"),
    base_url=os.getenv("ANTIGRAVITY_BASE_URL", _default_base),
    auth_type="api_key",
    default_aux_model="gemini-3.7-flash-low",
    fallback_models=(
        "gemini-3.7-flash-low",
        "gemini-3.7-flash-medium",
        "gemini-3.7-flash-high",
        "gemini-3.7-pro-low",
        "gemini-3.7-pro-high",
        "gemini-3.8-flash-low",
        "gemini-3.8-flash-high",
        "gemini-2.5-flash",
        "gemini-2.5-pro",
    ),
)

register_provider(antigravity_provider)
