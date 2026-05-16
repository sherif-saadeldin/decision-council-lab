from __future__ import annotations

from typing import Protocol

from rich.console import Console


class ProgressReporter(Protocol):
    def on_stage(self, stage: str) -> None: ...


class ConsoleProgressReporter:
    """Prints stage labels to the console unless quiet or disabled."""

    def __init__(self, console: Console, *, enabled: bool = True) -> None:
        self._console = console
        self._enabled = enabled

    def on_stage(self, stage: str) -> None:
        if not self._enabled:
            return
        self._console.print(f"[cyan]>[/cyan] {stage}", highlight=False)


class NullProgressReporter:
    def on_stage(self, stage: str) -> None:
        return


class StageTrackingProgress:
    """Tracks the latest stage while optionally delegating to another reporter."""

    def __init__(self, inner: ProgressReporter | None = None) -> None:
        self._inner = inner
        self.current_stage = "init"

    def on_stage(self, stage: str) -> None:
        self.current_stage = stage
        if self._inner is not None:
            self._inner.on_stage(stage)
