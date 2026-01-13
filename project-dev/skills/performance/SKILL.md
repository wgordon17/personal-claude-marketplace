---
name: performance
description: Query optimization, profiling guidance, and bottleneck identification. Use when investigating performance issues.
allowed-tools: [Read, Grep, Glob, Bash, LSP]
---

# Performance Analysis Skill

## Purpose

Identify and resolve performance bottlenecks in Project, with focus on database queries, view performance, and encryption overhead.

## When to Use

- Investigating slow page loads
- Optimizing database queries
- Profiling encryption operations
- User reports performance issues

## Required Skills

- `/uv-python` — For profiling commands
- `/lsp-navigation` — For tracing code paths

## Performance Analysis Workflow

### Step 1: Identify Slow Operations

```bash
# Enable query logging in Django settings
# LOGGING['loggers']['django.db.backends']['level'] = 'DEBUG'

# Or use Django Debug Toolbar in development
```

### Step 2: Analyze Query Patterns

Common issues:
1. **N+1 Queries** — Multiple queries in loop
2. **Missing indexes** — Full table scans
3. **Unnecessary fields** — SELECT * when only need ID
4. **Redundant queries** — Same query multiple times

### Step 3: Apply Optimizations

See `references/query-patterns.md` for common solutions.

## Common Performance Issues

### N+1 Query Problem

**Bad:**
```python
# This generates N+1 queries
for transaction in Transaction.objects.all():
    print(transaction.account.name)  # Each access hits DB
```

**Good:**
```python
# Use select_related for ForeignKey
for transaction in Transaction.objects.select_related('account'):
    print(transaction.account.name)  # No extra query

# Use prefetch_related for reverse FK or M2M
for user in User.objects.prefetch_related('transactions'):
    for t in user.transactions.all():  # Already fetched
        print(t.description)
```

### Missing Indexes

**Symptoms:**
- Slow filter/order operations
- `EXPLAIN` shows sequential scan

**Solution:**
```python
class Transaction(models.Model):
    date = models.DateField(db_index=True)  # Add index

    class Meta:
        indexes = [
            models.Index(fields=['date', 'account']),  # Composite index
        ]
```

### Unnecessary Data Loading

**Bad:**
```python
# Loads all fields
transactions = Transaction.objects.filter(user=user)
```

**Good:**
```python
# Only load what you need
transactions = Transaction.objects.filter(user=user).only('id', 'date', 'amount')

# Or defer heavy fields
transactions = Transaction.objects.filter(user=user).defer('encrypted_data')

# Use values() for dictionary output
transaction_ids = Transaction.objects.filter(user=user).values_list('id', flat=True)
```

## Project-Specific Optimizations

### Encryption Overhead

Encryption/decryption is CPU-intensive. Optimize by:

1. **Batch decryption** — Decrypt once per batch, not per transaction
2. **Lazy decryption** — Only decrypt when displaying
3. **Cache decrypted data** — In-request caching for repeated access

```python
# Bad: Decrypt each transaction separately
for t in transactions:
    data = decrypt(t.encrypted_data, dek)

# Good: Batch decryption
dek = get_dek_for_batch(batch)
for t in batch.transactions.all():
    data = decrypt(t.encrypted_data, dek)  # Same DEK reused
```

### Session Validation

IP validation and GeoIP lookups add overhead:

```python
# Already optimized: Single lookup per request in middleware
# apps/security/middleware/session.py
```

### Database Queries in Templates

**Bad:**
```html
{% for account in user.accounts.all %}  <!-- Query in template -->
```

**Good:**
```python
# In view
context['accounts'] = user.accounts.select_related('institution').all()
```

## Profiling Commands

```bash
# Profile Django request
uv run python -m cProfile -o output.prof manage.py runserver

# Analyze profile
uv run python -c "import pstats; p = pstats.Stats('output.prof'); p.sort_stats('cumulative').print_stats(20)"

# Django silk profiler (if installed)
# Visit /silk/ in browser
```

## Database Query Analysis

```sql
-- PostgreSQL: Analyze query plan
EXPLAIN ANALYZE SELECT * FROM transactions WHERE user_id = 1;

-- Check table sizes
SELECT relname, pg_size_pretty(pg_total_relation_size(relid))
FROM pg_catalog.pg_statio_user_tables
ORDER BY pg_total_relation_size(relid) DESC;

-- Find slow queries (if pg_stat_statements enabled)
SELECT query, calls, mean_time
FROM pg_stat_statements
ORDER BY mean_time DESC LIMIT 10;
```

## Performance Benchmarks

| Operation | Target | Unacceptable |
|-----------|--------|--------------|
| Dashboard load | < 500ms | > 2s |
| Transaction list | < 1s | > 3s |
| Login | < 1s | > 3s (Argon2 is slow by design) |
| Encryption (per tx) | < 1ms | > 10ms |
| Database query | < 50ms | > 500ms |

## Integration

### Called By
- `project-dev:orchestrator` — When investigating slowness
- `project-dev:code-quality` — Performance checks

### References
- Query patterns: `references/query-patterns.md`
