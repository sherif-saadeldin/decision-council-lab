from __future__ import annotations

from council.intake_normalizer import (
    normalize_constraint_fragments,
    normalize_context_text,
    normalize_risk_fragments,
)


def test_constraint_normalizer_handles_shorthand_and_dedupes() -> None:
    items = normalize_constraint_fragments("time money legal, legal")
    assert "Limited time" in items
    assert "Financial pressure" in items
    assert "Legal constraints" in items
    assert items.count("Legal constraints") == 1


def test_risk_normalizer_handles_keywords() -> None:
    items = normalize_risk_fragments("burnout, adoption, execution")
    assert "Burnout risk" in items
    assert "Adoption risk" in items
    assert "Execution risk" in items


def test_context_normalizer_drops_weak_values() -> None:
    assert normalize_context_text("  ") == ""
    assert normalize_context_text("ok") == ""
    assert normalize_context_text("seed stage team of 3") == "seed stage team of 3"
