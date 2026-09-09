"""A6API Model Provider Profile Plugin for HAOS.

Conecta ao provider A6API (OpenAI-compatible) em https://api.a6api.com/v1.
Chave de API configurada via variável de ambiente A6API_API_KEY ou no config do HAOS.
"""

from providers import register_provider
from providers.base import ProviderProfile

a6api_provider = ProviderProfile(
    name="a6api",
    aliases=("a6", "a6-api"),
    display_name="A6API Provider",
    description="A6API high-performance LLM provider endpoint",
    signup_url="https://a6api.com",
    env_vars=("A6API_API_KEY", "A6API_BASE_URL"),
    base_url="https://api.a6api.com/v1",
    auth_type="api_key",
    default_model="gemini-2.5-flash",
    default_aux_model="gemini-2.5-flash",
)

register_provider(a6api_provider)
