"""Background workers.

Every periodic job is a :class:`Worker`: it calls a ``step`` function which
returns the delay until the next call (``None`` = stop).  Exceptions are
logged and the worker keeps running with a back-off, so no job can die
silently.  Under gevent (production) the threads are greenlets.
"""

from __future__ import annotations

import logging
import threading
from typing import Callable

logger = logging.getLogger(__name__)

Step = Callable[[], "float | None"]


class Worker:
    ERROR_BACKOFF_S = 5.0

    def __init__(self, name: str, step: Step, *, initial_delay: float = 0.0,
                 on_error: Callable[[Exception], None] | None = None):
        self.name = name
        self._step = step
        self._initial_delay = initial_delay
        self._on_error = on_error
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.errors = 0

    def start(self) -> "Worker":
        self._thread = threading.Thread(target=self._run, name=self.name, daemon=True)
        self._thread.start()
        return self

    def stop(self, timeout: float | None = 2.0) -> None:
        self._stop.set()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout)

    @property
    def alive(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def run_once(self) -> float | None:
        """Run one step with the worker's error handling (used by tests)."""
        try:
            return self._step()
        except Exception as e:
            self.errors += 1
            logger.exception("Worker %s failed", self.name)
            if self._on_error:
                try:
                    self._on_error(e)
                except Exception:
                    logger.exception("Worker %s error handler failed", self.name)
            return self.ERROR_BACKOFF_S

    def _run(self) -> None:
        if self._initial_delay and self._stop.wait(self._initial_delay):
            return
        logger.debug("Worker %s started", self.name)
        while not self._stop.is_set():
            delay = self.run_once()
            if delay is None:
                break
            if self._stop.wait(max(0.0, delay)):
                break
        logger.debug("Worker %s stopped", self.name)


def once(fn: Callable[[], None]) -> Step:
    """Adapt a one-shot function to a worker step."""
    def step():
        fn()
        return None
    return step
