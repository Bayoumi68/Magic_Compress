"""Run long archive operations on a background thread with progress + cancel.

The UI thread must never block on I/O, so every extract/create/add/delete/test
runs inside a Task (a QThread). The target callable is handed a ``report``
progress callback and an ``is_cancelled`` probe, both wired to Qt signals.
"""

from __future__ import annotations

import traceback
from typing import Callable

from PySide6.QtCore import QThread, Signal

from .core.base import OperationCancelled


class Task(QThread):
    progress = Signal(int, int)      # current, total
    message = Signal(str)            # status text (e.g. current file)
    succeeded = Signal(object)       # result value
    failed = Signal(object, str)     # (exception, traceback string)

    def __init__(self, fn: Callable, *args, **kwargs):
        super().__init__()
        self._fn = fn
        self._args = args
        self._kwargs = kwargs
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def _is_cancelled(self) -> bool:
        return self._cancelled

    def run(self) -> None:  # executes on the worker thread
        def report(current: int, total: int, msg: str = "") -> None:
            self.progress.emit(int(current), int(total))
            if msg:
                self.message.emit(str(msg))

        try:
            result = self._fn(*self._args, report=report,
                              is_cancelled=self._is_cancelled, **self._kwargs)
            if self._cancelled:
                self.failed.emit(OperationCancelled("Operation cancelled."), "")
                return
            self.succeeded.emit(result)
        except OperationCancelled as exc:
            self.failed.emit(exc, "")
        except Exception as exc:  # noqa: BLE001 — surfaced to the user
            self.failed.emit(exc, traceback.format_exc())
