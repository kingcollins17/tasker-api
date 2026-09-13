# Workspace Rules

- Always move import to beginning of file
- Prioritize the simplest implementation
- Always use the Repository for the model for simple queries
- In all endpoint functions, if the caught error is an HTTPException, re-raise it. If it is any other Exception, print the stacktrace and error to the terminal via `AppErrorHandler.handleError(error)` and raise a new instance of `HTTPException`.
- Always name a service file after the name of the service contained in them; each service should be in its own service file (e.g., `admin_service.py`, `audit_service.py`).
- Every service must have a dependency function (`get_<service_name>`) that returns an instance of it.
- Never instantiate services directly inside endpoint functions; always obtain them using `Depends(get_<service_name>)`.
- Always use `str` (not `UUID`) for identifier field types across models, schemas, services, and endpoints.
- Do not run test commands unless explicitly requested by the user.

