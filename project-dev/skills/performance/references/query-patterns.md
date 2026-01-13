# Query Optimization Patterns

Quick reference for common Django query optimizations.

## select_related vs prefetch_related

| Method | Use For | SQL Result |
|--------|---------|------------|
| `select_related` | ForeignKey, OneToOne | JOIN |
| `prefetch_related` | ManyToMany, reverse FK | Separate query + Python join |

### Examples

```python
# ForeignKey: Transaction.account
Transaction.objects.select_related('account')

# Nested ForeignKey: Transaction.account.institution
Transaction.objects.select_related('account__institution')

# Reverse FK: User.transactions (Transaction has FK to User)
User.objects.prefetch_related('transactions')

# ManyToMany
Transaction.objects.prefetch_related('categories')
```

## Limiting Fields

```python
# Only specific fields
Transaction.objects.only('id', 'date', 'amount')

# All except heavy fields
Transaction.objects.defer('encrypted_data')

# Just values (dict output)
Transaction.objects.values('id', 'date')

# Just IDs
Transaction.objects.values_list('id', flat=True)
```

## Aggregation

```python
from django.db.models import Count, Sum, Avg

# Count
Transaction.objects.filter(user=user).count()

# Sum
Transaction.objects.filter(user=user).aggregate(total=Sum('amount'))

# Group by
Transaction.objects.values('category').annotate(total=Sum('amount'))
```

## Bulk Operations

```python
# Bulk create (single INSERT)
Transaction.objects.bulk_create([
    Transaction(field=value),
    Transaction(field=value),
])

# Bulk update (single UPDATE)
Transaction.objects.filter(user=user).update(is_hidden=True)

# Delete without loading
Transaction.objects.filter(user=user).delete()
```

## Avoiding Repeated Queries

```python
# Bad: Multiple queries for same data
user = User.objects.get(id=1)
user = User.objects.get(id=1)  # Query again!

# Good: Reuse queryset
user = User.objects.get(id=1)
# ... use user ...

# Good: Cache in view
@cached_property
def user_transactions(self):
    return list(self.request.user.transactions.all())
```

## Exists vs Count

```python
# When you just need to know if any exist
if Transaction.objects.filter(user=user).exists():  # Faster
    pass

# Not this
if Transaction.objects.filter(user=user).count() > 0:  # Slower
    pass
```

## Indexing Strategy

```python
class Transaction(models.Model):
    # Single-column index
    date = models.DateField(db_index=True)

    # Composite index for common query pattern
    class Meta:
        indexes = [
            models.Index(fields=['user', 'date']),  # For: .filter(user=x).order_by('date')
            models.Index(fields=['account', '-date']),  # For: .filter(account=x).order_by('-date')
        ]
```

## Query Debugging

```python
# Print SQL for queryset
print(Transaction.objects.filter(user=user).query)

# Count queries in test
from django.test.utils import CaptureQueriesContext
from django.db import connection

with CaptureQueriesContext(connection) as context:
    list(Transaction.objects.filter(user=user))

print(f"Queries: {len(context.captured_queries)}")
```

## Project-Specific Patterns

### Transaction List with Decryption

```python
# Get transactions with related data in one query
transactions = (
    Transaction.objects
    .filter(batch__in=user_batches)
    .select_related('batch', 'account')
    .order_by('-date')
)

# Get all DEKs upfront
dek_grants = {
    dg.transaction_batch_id: dg
    for dg in DEKGrant.objects.filter(user=user, transaction_batch__in=batches)
}

# Now decrypt without additional queries
for t in transactions:
    dek_grant = dek_grants[t.batch_id]
    # ... decrypt ...
```

### Session Validation

```python
# Already optimized in middleware
# Single SessionGrant lookup per request
# Cached on request object
```

### User with Related Data

```python
# Dashboard context
user = (
    User.objects
    .select_related('subscription')
    .prefetch_related('financialinstitution_set__account_set')
    .get(id=user_id)
)
```
