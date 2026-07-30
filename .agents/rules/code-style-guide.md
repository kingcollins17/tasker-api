---
trigger: always_on
---

** Always move import to beginning of file
** Prioritize the simplest implementation
** Do not access the database session directly from outside the repository class; execute custom queries/statements using `Repository.execute(statement)` and refreshes using `Repository.refresh(entity)`
** Do not call `.scalar()` or `.scalar_one_or_none()` on results of `Repository.execute(statement)`; `Repository.execute` already returns a SQLModel `ScalarResult`, so call `.one_or_none()`, `.first()`, or `.all()` directly.
** Always wrap endpoints in try-except blocks
** All response model fields should always be nullable using Optional from typing
** Always use the Repository for the model for simple queries
** In all endpoint functions, if the caught error is an HTTPException, re-raise it. If it is any other Exception, print the stacktrace and error to the terminal via `AppErrorHandler.handleError(error)` and raise a new instance of `HTTPException`.