# Research Task: Custom ORM vs. SQLAlchemy

## Context

Our team has spent 3 months building a custom ORM layer for our Flask application (`src/db/orm/`). The ORM currently handles basic CRUD operations, relationship loading, and query building for our 12 models. A team member has raised the question of whether we should continue developing it or migrate to SQLAlchemy.

Evaluate the evidence and provide a clear recommendation.

---

## Internal Investigation Results

### Custom ORM — Current State

**Codebase metrics:**
- `src/db/orm/`: 2,847 lines of Python across 14 files
- 3 known bugs open in the issue tracker (see below)
- 0 external contributors; 1 primary author (now on a different team)
- Test coverage: 41% (well below our 80% target)
- No migration tooling — schema changes require manual SQL scripts

**Known bugs (open >30 days):**
1. `orm/query.py#L204` — `filter_by` with `None` values generates invalid SQL (`WHERE field = NULL` instead of `WHERE field IS NULL`). Workaround: callers must check for None before calling filter_by.
2. `orm/relations.py#L87` — Eager loading with nested relationships (`has_many` through `belongs_to`) silently falls back to N+1 queries when depth > 2. No error is raised; callers don't know this is happening.
3. `orm/session.py#L31` — Connection pool does not release connections on `IntegrityError` — connections leak until pool exhaustion under concurrent write load.

**Domain-specific query DSL:**
The custom ORM includes a domain-specific query language (`src/db/orm/dsl.py`) built for our task management domain. Three services depend on the DSL for complex queries that would require raw SQL or significant workarounds in SQLAlchemy:

1. **Task dependency resolver** (`src/services/dependency_graph.py`) — Uses the ORM's `traverse_graph()` method which walks the `task_dependencies` adjacency list and materializes the full dependency DAG in a single recursive CTE query. The method is called ~2,400 times/day and returns pre-hydrated `Task` objects with their dependency chain. Reimplementing this in SQLAlchemy would require writing the recursive CTE as a text() expression or using the `cte()` API, which the team has not used before.

2. **Bulk status transition engine** (`src/services/workflow_engine.py`) — Uses the ORM's `atomic_batch_update()` which validates state machine transitions in Python, then generates a single multi-row UPDATE with CASE expressions. The method handles 12 valid state transitions across 3 entity types. The validation logic is tightly coupled to the ORM's model metadata (it reads allowed transitions from model class attributes).

3. **Audit trail materializer** (`src/services/audit.py`) — Uses `diff_snapshot()` to compare two model instances and generate a JSON diff. This introspects the ORM's internal column registry, which has a different structure than SQLAlchemy's `inspect()` API. 4 downstream consumers read audit diffs, and the diff format is part of the API contract.

**Feature coverage vs. SQLAlchemy:**
Our ORM reimplements the following SQLAlchemy capabilities:
- Basic query building (WHERE, ORDER BY, LIMIT) ✓
- Relationship loading (has_many, belongs_to) ✓ (buggy at depth >2)
- Session/transaction management ✓ (with connection leak bug)
- Column type handling (int, str, bool, datetime) ✓
- Domain-specific query DSL ✓ (3 services depend on it — no SQLAlchemy equivalent)
- Enum column support ✗ (not implemented — workaround: store as string)
- Composite primary keys ✗ (not implemented — not needed yet)
- Database migrations ✗ (not implemented — manual SQL only)
- Async support ✗ (not on roadmap — would require rewrite)
- Connection pool monitoring ✗ (not implemented)

Approximately 80% of the features we use daily from SQLAlchemy's core feature set are reimplemented (partially or fully) in our custom ORM. However, the DSL layer provides capabilities that are unique to our domain and have no direct SQLAlchemy equivalent.

**Maintenance trajectory:**
- Last substantive feature addition: 6 weeks ago (DSL traverse_graph optimization)
- Bugs opened in last 30 days: 2
- Bugs closed in last 30 days: 0
- Primary author's availability: ~1 hour/week (on different team)
- DSL layer has been stable — 0 bugs filed against it in the last 6 months

---

## External Research Results

### SQLAlchemy

**Community and ecosystem:**
- 10,000+ GitHub stars; 18+ years of active development
- 180+ contributors; full-time maintainers
- Used by Flask, FastAPI, Pyramid, and most major Python web frameworks
- Alembic (migration tool) is a first-class companion maintained by the same author

**Feature set relevant to our stack:**
- Full async support via `sqlalchemy.ext.asyncio`
- Comprehensive migration tooling (Alembic) with auto-generation
- Enum column support (native)
- Connection pool monitoring and metrics
- Extensive documentation, tutorials, and community Q&A (Stack Overflow, Discord)
- Battle-tested with PostgreSQL, MySQL, SQLite across thousands of production deployments

**Known issues:**
- Learning curve for ORM vs. Core distinction (typically 1-2 weeks for a Python developer)
- Version 1.x to 2.x migration requires some API changes (we would start on 2.x directly)

**Migration estimate (internal assessment):**
- Model layer migration: ~3 days (12 models, straightforward field/relationship mapping)
- Query layer migration: ~4 days (replace custom query builder calls)
- DSL layer migration: ~5 days (rewrite 3 service integrations — traverse_graph, atomic_batch_update, diff_snapshot — plus validate audit diff format compatibility with 4 downstream consumers)
- Session/transaction migration: ~1 day (simpler in SQLAlchemy than our custom code)
- Test updates: ~2 days
- Total: ~15 developer-days (3 weeks for one engineer)

---

## Summary Comparison

| Dimension | Custom ORM | SQLAlchemy |
|-----------|-----------|-----------|
| Migrations | Manual SQL | Alembic (auto-gen) |
| Known bugs | 3 open (>30 days, none in DSL) | None relevant to our use case |
| Community support | 0 external; 1 part-time internal | 180+ contributors; active |
| Feature parity | ~80% of core + domain DSL | 100% core; no domain DSL |
| Domain DSL | 3 services; stable 6 months | Would need custom reimplementation |
| Test coverage | 41% (DSL layer untested but stable) | Library is tested; our usage tested by our tests |
| Async support | No (would require rewrite) | Yes (native in 2.x) |
| Connection leak | Yes (known bug in core, not DSL) | No |
| Maintenance cost | Growing (core bugs, no bandwidth) | Zero (maintained externally) |
| Migration cost | — | ~15 dev-days (DSL rewrite is 5 of those) |

---

## Team Context

- 4-person engineering team
- Current velocity: ~12 story points/sprint
- One engineer could be dedicated for 2 weeks without impacting critical path, but 3 weeks would overlap with the Q3 feature deadline
- The team has used SQLAlchemy before on a previous project (3 of 4 engineers are familiar)
- The engineer most familiar with the DSL layer (the dependency graph and audit trail code) is the same one who would do the migration — pulling them for 3 weeks means no DSL bug support during that window
- The dependency graph service is on the critical path for an upcoming "project templates" feature (Q3 roadmap item) — any regression in `traverse_graph()` blocks that feature
