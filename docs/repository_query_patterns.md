# Database Repository Query Patterns & MatchingEngine Guide

## 1. Repository.execute Statement Execution Rule

`Repository.execute(statement)` executes a custom SQLModel/SQLAlchemy statement directly through the repository's underlying session:

```python
async def execute(self, statement: Any) -> Any:
    return await self.session.exec(statement)
```

### CRITICAL RULE: No `.scalar()` or `.scalar_one_or_none()`

In SQLModel, `session.exec(statement)` returns a **`ScalarResult`** directly.

- ❌ **DO NOT** call `.scalar()`, `.scalar_one_or_none()`, or `res.scalar()` on the returned value of `Repository.execute(statement)`.
- ✅ **DO** call `.one_or_none()`, `.first()`, `.all()`, or `.one()` directly on the result object.

#### Correct Usage Example
```python
# Query single entity
stmt = select(UserLocation).where(UserLocation.user_id == provider_id)
res = await location_repo.execute(stmt)
loc: Optional[UserLocation] = res.one_or_none()

# Query joined entities with deduplication
stmt_eligibility = (
    select(User, ProviderProfile)
    .join(ProviderProfile, ProviderProfile.user_id == User.id)
)
res = await provider_profile_repo.execute(stmt_eligibility)
rows = res.unique().all()
```

---

## 2. MatchingEngine Lifecycle & Method Naming

The `MatchingEngine` service (`app/core/services/matching_engine.py`) is **ephemeral**:
- Instantiated per step with `session_id` and `db_session`.
- Executes step logic via **`await engine.run()`**.

```python
engine = MatchingEngine(session_id=session_id, db_session=session)
await engine.run()
```
