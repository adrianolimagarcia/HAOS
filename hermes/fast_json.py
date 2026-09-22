"""Módulo utilitário de serialização e desserialização JSON ultra-rápido.

Utiliza `orjson` (implementação nativa em Rust) com fallback transparente
para a biblioteca padrão `json` do CPython.
"""
from typing import Any, Callable, Optional, Union

try:
    import orjson as _orjson

    def dumps(obj: Any, *, default: Optional[Callable[[Any], Any]] = None, indent: Optional[int] = None) -> str:
        """Serializa um objeto Python em string JSON via motor Rust `orjson`.
        
        Suporta formatação com indentação via `orjson.OPT_INDENT_2`.
        """
        option = _orjson.OPT_INDENT_2 if indent else 0
        return _orjson.dumps(obj, default=default, option=option).decode("utf-8")

    def dumps_bytes(obj: Any, *, default: Optional[Callable[[Any], Any]] = None, indent: Optional[int] = None) -> bytes:
        """Serializa um objeto Python diretamente em bytes UTF-8 via motor Rust `orjson`."""
        option = _orjson.OPT_INDENT_2 if indent else 0
        return _orjson.dumps(obj, default=default, option=option)

    def loads(s: Union[str, bytes, bytearray, memoryview]) -> Any:
        """Desserializa uma string ou buffer binário JSON via motor Rust `orjson`."""
        return _orjson.loads(s)

    RUST_JSON_ACTIVE: bool = True

except ImportError:  # pragma: no cover
    import json as _std_json

    def dumps(obj: Any, *, default: Optional[Callable[[Any], Any]] = None, indent: Optional[int] = None) -> str:
        return _std_json.dumps(obj, default=default, indent=indent)

    def dumps_bytes(obj: Any, *, default: Optional[Callable[[Any], Any]] = None, indent: Optional[int] = None) -> bytes:
        return _std_json.dumps(obj, default=default, indent=indent).encode("utf-8")

    def loads(s: Union[str, bytes, bytearray, memoryview]) -> Any:
        return _std_json.loads(s)

    RUST_JSON_ACTIVE: bool = False
