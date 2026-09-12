---
trigger: always_on
---

** Always move import to beginning of file
** Prioritize the simplest implementation
** Always use the Repository for the model for simple queries
** In all endpoint functions, if the caught error is an HTTPException, re-raise it. If it is any other Exception, print the stacktrace and error to the terminal via `AppErrorHandler.handleError(error)` and raise a new instance of `HTTPException`.