import functools
import inspect
import logging
import traceback
from typing import Any, Callable, Tuple, Type, Union

# Set up the default application logger
logger = logging.getLogger("tasker_api")

def log_error(
    exceptions: Union[Type[BaseException], Tuple[Type[BaseException], ...]] = Exception,
    message: str = "An error occurred in",
    level: int = logging.ERROR,
    reraise: bool = True,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """A decorator to log exceptions raised by synchronous or asynchronous functions.

    Args:
        exceptions: The exception type or a tuple of exception types to log. Defaults to Exception.
        message: Custom log prefix message.
        level: The log level to write the entry at (e.g., logging.ERROR).
        reraise: If True, the exception will be re-raised after logging. If False,
                 the function will return None after logging.
    """
    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        
        def _log_exception(exc: BaseException) -> None:
            func_name = func.__name__
            module_name = func.__module__
            full_message = (
                f"{message} {module_name}.{func_name}: {str(exc)}\n"
                f"Traceback:\n{traceback.format_exc()}"
            )
            logger.log(level, full_message)

        @functools.wraps(func)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                return await func(*args, **kwargs)
            except exceptions as e:
                _log_exception(e)
                if reraise:
                    raise
                return None

        @functools.wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                return func(*args, **kwargs)
            except exceptions as e:
                _log_exception(e)
                if reraise:
                    raise
                return None

        if inspect.iscoroutinefunction(func):
            return async_wrapper
        return sync_wrapper

    return decorator
