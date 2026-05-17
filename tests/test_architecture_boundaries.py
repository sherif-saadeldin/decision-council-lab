from __future__ import annotations

from pathlib import Path


def test_services_do_not_import_rich_directly() -> None:
    service_dir = Path("council/services")
    offenders: list[str] = []
    for path in service_dir.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "from rich" in text or "import rich" in text:
            offenders.append(str(path))

    assert offenders == []


def test_storage_boundary_module_exists() -> None:
    assert Path("council/storage/run_store.py").is_file()
    assert Path("council/services/council_service.py").is_file()
    assert Path("council/rendering/review_renderer.py").is_file()
