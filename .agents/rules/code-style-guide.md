---
trigger: always_on
---

** Always move import to beginning of file
** Prioritize the simplest implementation
** Do not access the database session directly from outside the repository class; execute custom queries/statements using `Repository.execute(statement)`
** Always wrap endpoints in try-except blocks
** All response model fields should always be nullable using Optional from typing
** Always use the Repository for the model for simple queries