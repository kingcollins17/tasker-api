---
name: sqlmodel-queries
description: Usage guide and code snippets for querying data with SQLModel using AsyncSession — select(), session.exec(), col() column wrapping, filtering, joins, eager loading, insert, update, delete, and bulk operations, all async. Use this whenever the user is writing, debugging, or reviewing SQLModel query code, working with an async session.exec()/session.get(), filtering or joining SQLModel tables, or running inserts/updates/deletes against a SQLModel database.
---

# SQLModel Query Usage Guide (AsyncSession)

Assumes SQLModel table models already exist and a `session: AsyncSession` is obtained the way it typically is in a FastAPI app — via an `async_sessionmaker` and a `get_session()` dependency, then injected into repository/service functions rather than opened inline. Every snippet below takes `session` as a given. Covers running queries: insert, select, update, delete — all via `session.exec()`, not `session.execute()`.

> [!IMPORTANT]
> **Always wrap column usages in `col()`**: All model column attribute references in queries (such as in `.where()`, `.order_by()`, `.group_by()`, `.in_()`, `.like()`, `.join()`) MUST always be wrapped in `col()` from `sqlmodel` (e.g. `col(Hero.name) == "Deadpond"`, `col(Hero.age) >= 18`). This avoids IDE/type-checker warnings and expression evaluation errors.

```python
from sqlmodel import select, col
from sqlmodel.ext.asyncio.session import AsyncSession
```

## Insert

Use `session.add()` for creating one record at a time. Only reach for the `insert()` query construct when creating many rows in a single round trip.

```python
async def create_hero(session: AsyncSession, name: str, secret_name: str) -> Hero:
    hero = Hero(name=name, secret_name=secret_name)
    session.add(hero)
    await session.commit()
    await session.refresh(hero)   # reload from DB to pick up generated fields like id
    return hero
```

Multiple rows in one commit:

```python
session.add_all([hero_1, hero_2, hero_3])
await session.commit()
```

Only for many rows at once — bulk insert without per-object overhead (list of dicts, via SQLAlchemy's `insert()`). Note `params` is keyword-only on `session.exec()`:

```python
from sqlalchemy import insert

async def bulk_create_heroes(session: AsyncSession, rows: list[dict]) -> None:
    await session.exec(insert(Hero), params=rows)
    await session.commit()
```

## Select

```python
from sqlmodel import select, col

async def get_hero(session: AsyncSession, hero_id: int) -> Hero | None:
    return await session.get(Hero, hero_id)                       # by primary key

async def list_heroes(session: AsyncSession) -> list[Hero]:
    result = await session.exec(select(Hero))
    return result.all()

async def get_hero_by_name(session: AsyncSession, name: str) -> Hero | None:
    result = await session.exec(select(Hero).where(col(Hero.name) == name))
    return result.first()                                          # first match or None
```

`session.exec()` runs a `select()` statement and returns `Hero` objects directly — always call `.all()` / `.first()` / `.one()` / `.one_or_none()` on the result to actually pull the data.

### Filtering

Column usages should **always** be wrapped in `col()` from `sqlmodel`:

```python
from sqlmodel import select, col, and_, or_

select(Hero).where(col(Hero.age) >= 35)
select(Hero).where(col(Hero.name) == "Deadpond", col(Hero.age) != None)      # multiple args = AND
select(Hero).where(and_(col(Hero.age) >= 35, col(Hero.age) <= 40))
select(Hero).where(or_(col(Hero.name) == "Deadpond", col(Hero.name) == "Spider-Boy"))
select(Hero).where(col(Hero.name).in_(["Deadpond", "Rusty-Man"]))
select(Hero).where(col(Hero.name).like("%Man%"))
select(Hero).filter_by(name="Deadpond")                            # keyword shorthand
select(Hero).where(col(Hero.age) >= 18)                            # col() avoids type-checker/caching issues
```

Statements are **immutable** — reassign rather than expecting in-place mutation:

```python
statement = select(Hero)
statement = statement.where(col(Hero.age) >= 18)   # must reassign
```

### Joins

A join that fans out — e.g. joining a one-to-many relationship, or joining onto a table that has multiple matching rows per parent — returns the parent entity once per matching row. Call `.unique()` before `.all()` whenever that's possible, so duplicates are collapsed based on identity:

```python
async def get_heroes_in_team(session: AsyncSession, team_name: str) -> list[tuple[Hero, Team]]:
    statement = select(Hero, Team).join(Team).where(col(Team.name) == team_name)
    result = await session.exec(statement)
    return result.unique().all()   # dedupe in case the join produced repeat rows

async def get_teams_with_multiple_heroes(session: AsyncSession) -> list[Team]:
    statement = select(Team).join(Team.heroes)   # one-to-many join -> Team can repeat
    result = await session.exec(statement)
    return result.unique().all()

select(Hero).join(Team, isouter=True)   # outer join
```

### Eager loading relationships (avoid N+1 queries)

Accessing a relationship attribute in a loop triggers one query per item unless eager-loaded — and lazy loading doesn't work mid-await in async code at all, so eager-load anything you'll need. Loader options come from `sqlalchemy.orm`:

```python
from sqlalchemy.orm import selectinload, joinedload

async def list_heroes_with_team(session: AsyncSession) -> list[Hero]:
    statement = select(Hero).options(selectinload(Hero.team))   # best for collections
    result = await session.exec(statement)
    return result.all()   # separate query under the hood, no duplicate rows, .unique() not needed

async def list_teams_with_heroes(session: AsyncSession) -> list[Team]:
    statement = select(Team).options(joinedload(Team.heroes))   # joinedload on a collection
    result = await session.exec(statement)
    return result.unique().all()   # required: joinedload on a *collection* relationship
    # produces one row per hero, so the same Team repeats — SQLAlchemy raises
    # InvalidRequestError here if .unique() is omitted
```

`joinedload()` on a to-one relationship (like `Hero.team` above) doesn't duplicate anything, so `.unique()` is optional there — but `joinedload()` on a *collection* relationship always needs it. `selectinload()` never needs it, since it runs as a separate follow-up query rather than a SQL join.

### Aggregates, ordering, pagination

```python
from sqlalchemy import func
from sqlmodel import col, select

async def count_heroes(session: AsyncSession) -> int:
    result = await session.exec(select(func.count()).select_from(Hero))
    return result.one()

statement = (
    select(col(Hero.team_id), func.count(col(Hero.id)))
    .group_by(col(Hero.team_id))
    .having(func.count(col(Hero.id)) > 1)
)

statement = select(Hero).order_by(col(Hero.name)).limit(20).offset(40)
```

## Update

Use fetch + modify + `session.add()` for updating one record at a time. Only reach for the `update()` query construct when updating many rows in a single round trip.

```python
async def update_hero_age(session: AsyncSession, hero_id: int, age: int) -> Hero | None:
    hero = await session.get(Hero, hero_id)
    if hero is None:
        return None
    hero.age = age
    session.add(hero)
    await session.commit()
    await session.refresh(hero)
    return hero
```

Only for many rows at once — bulk update without loading rows individually:

```python
from sqlalchemy import update
from sqlmodel import col

async def bump_team_ages(session: AsyncSession, team_id: int) -> None:
    await session.exec(update(Hero).where(col(Hero.team_id) == team_id).values(age=col(Hero.age) + 1))
    await session.commit()
```

`RETURNING` the updated rows:

```python
statement = update(Hero).where(col(Hero.team_id) == 1).values(age=col(Hero.age) + 1).returning(Hero)
result = await session.exec(statement, execution_options={"synchronize_session": "fetch"})
updated = result.scalars().all()
```

## Delete

```python
async def delete_hero(session: AsyncSession, hero_id: int) -> bool:
    hero = await session.get(Hero, hero_id)
    if hero is None:
        return False
    await session.delete(hero)          # await — delete() may cascade and load relationships
    await session.commit()
    return True
```

Bulk delete without loading each row:

```python
from sqlalchemy import delete
from sqlmodel import col

async def delete_underage_heroes(session: AsyncSession) -> None:
    await session.exec(delete(Hero).where(col(Hero.age) < 18))
    await session.commit()
```

## Gotchas

- **Always wrap column attributes in `col()`**: Column attributes in query constructs (`.where()`, `.order_by()`, `.group_by()`, `.in_()`, `.like()`, etc.) MUST always be wrapped in `col()` from `sqlmodel` (e.g. `col(Hero.age) >= 18`, `col(Hero.name).in_(...)`). This prevents type-checker errors and expression caching issues.
- **Joins that can duplicate rows:** call `.unique()` before `.all()`/`.first()` whenever a join could return the same entity more than once — a join fanning out over a one-to-many, or `joinedload()` on a collection relationship. SQLAlchemy will raise `InvalidRequestError` if you use `joinedload()` on a collection without `.unique()`.
- **`session.add()` vs `insert()`/`update()` queries:** default to `session.add()` for single-record create/update — it keeps the object tracked in the identity map, runs defaults/validators, and picks up generated values on refresh. Only drop to the `insert()`/`update()` query constructs when affecting many rows at once.
- **`select` import:** use `from sqlmodel import select`, not `from sqlalchemy import select` — that's what lets `session.exec()` return model instances directly instead of row tuples.
- **Always `session.exec()`, not `session.execute()`:** `session.exec()` works for `select()`, `insert()`, `update()`, and `delete()` statements alike; `params=` is keyword-only on it (unlike `execute()`).
- **Forgetting to await + unwrap:** `session.exec(statement)` returns a coroutine — `await` it first, then call `.all()` / `.first()` / `.one()` on the result.
- **N+1 queries:** touching a relationship in a loop issues one query per iteration unless eager-loaded — and in async code, an un-eager-loaded relationship access will fail outright, not just be slow.
- **Bulk update/delete bypasses the session's in-memory objects** unless you set `synchronize_session` — use `"fetch"` if already-loaded objects need to reflect the change.
- **`session.delete()` must be awaited** — it can cascade and load relationships to do so.
- **One session per request:** if `get_session()` is a shared FastAPI dependency, don't open a second `AsyncSession` inside a repository/service — accept `session` as a parameter and reuse the one already bound to the request.