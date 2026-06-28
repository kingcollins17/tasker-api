import sys
import traceback


class AppErrorHandler:
    @staticmethod
    def handleError(error: Exception) -> None:
        """Prints the error and stacktrace to the terminal."""
        print(f"Error: {error}", file=sys.stderr)
        traceback.print_exception(
            type(error), error, error.__traceback__, file=sys.stderr)
