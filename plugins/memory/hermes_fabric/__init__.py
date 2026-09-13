"""Plugin entrypoint wrapper para plugins/memory/hermes_fabric."""

from plugins.memory.hermes_fabric.provider import register

__all__ = ["register"]
