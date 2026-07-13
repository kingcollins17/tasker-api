import asyncio
from typing import Coroutine, Any, TypeVar

T = TypeVar("T")

def run_async(coro: Coroutine[Any, Any, T]) -> T:
    """Run an async coroutine in a synchronous Celery worker context."""
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)
