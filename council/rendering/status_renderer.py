from __future__ import annotations


def health_style(health: str) -> str:
    return {
        "healthy": "green",
        "warning": "yellow",
        "failed": "red",
    }.get(health, "dim")
