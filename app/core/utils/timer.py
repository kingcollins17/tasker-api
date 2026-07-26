import time
from typing import Optional

class Timer:
    """
    A utility class to measure execution duration.
    
    Can be used as a context manager (both sync and async) or with explicit
    start() and stop() methods. By default, duration is measured using
    time.perf_counter() for high resolution, and elapsed time is provided
    in milliseconds.
    
    Usage as context manager:
        async with Timer() as timer:
            await do_something()
        print(f"Took {timer.elapsed_ms} ms")
        
    Usage with explicit calls:
        timer = Timer()
        timer.start()
        # do something
        duration_ms = timer.stop()
    """

    def __init__(self):
        self._start: Optional[float] = None
        self.elapsed: float = 0.0

    def start(self):
        """Starts the timer."""
        self._start = time.perf_counter()

    def stop(self) -> int:
        """
        Stops the timer and returns the elapsed time in milliseconds.
        
        Raises:
            RuntimeError: If the timer was not started.
        """
        if self._start is None:
            raise RuntimeError("Timer was not started.")
        self.elapsed = time.perf_counter() - self._start
        self._start = None
        return self.elapsed_ms

    @property
    def elapsed_ms(self) -> int:
        """Returns the elapsed time in milliseconds."""
        return int(self.elapsed * 1000)

    # Sync context manager
    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.stop()

    # Async context manager compatibility
    async def __aenter__(self):
        self.start()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        self.stop()
