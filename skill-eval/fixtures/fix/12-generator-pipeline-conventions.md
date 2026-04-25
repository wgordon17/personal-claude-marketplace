## Review Findings

### Finding 1: Unbounded memory in data pipeline
**Category:** Performance
**Severity:** High
**Files:** src/pipeline/processor.py:28
**Description:** The `process_batch()` function loads all records into a list before processing. For the 500K-row nightly import, this causes memory spikes exceeding 2GB. Should use a generator-based streaming approach.
**Code:**
```python
def process_batch(db_session, batch_id: int) -> list[dict]:
    """Process all records in a batch."""
    records = Record.query.filter_by(batch_id=batch_id).all()  # loads everything into memory
    results = []
    for record in records:
        try:
            transformed = transform_record(record)
            results.append(transformed)
        except Exception as e:
            logger.error("Failed to process record %d: %s", record.id, e)
    return results
```

### Finding 2: Missing error context in transform failures
**Category:** Correctness
**Severity:** Medium
**Files:** src/pipeline/processor.py:35
**Description:** When `transform_record()` fails, the error is logged but the record is silently skipped. Downstream consumers have no way to know which records failed or why. Should collect failures with context and return them alongside successes.

### Finding 3: Inconsistent return type documentation
**Category:** Code Quality
**Severity:** Low
**Files:** src/pipeline/processor.py:28, src/pipeline/transforms.py:12
**Description:** `process_batch()` returns `list[dict]` but `transform_record()` returns `dict | None`. The None case is handled but not documented in the type signature. Functions should use consistent Optional/union patterns per project conventions.

## Codebase Conventions

```python
# src/pipeline/transforms.py — existing generator pattern in same module
def stream_csv_records(file_path: str) -> Iterator[dict]:
    """Stream CSV records without loading entire file into memory."""
    with open(file_path) as f:
        reader = csv.DictReader(f)
        for row in reader:
            yield _normalize_row(row)


# src/pipeline/transforms.py — error handling with context
def transform_record(record: Record) -> dict | None:
    """Transform a database record to output format.

    Returns None for records that cannot be transformed (logged at source).
    """
    if not record.is_valid:
        logger.warning("Skipping invalid record id=%d reason=%s", record.id, record.validation_error)
        return None
    return {
        "id": record.id,
        "value": record.normalized_value,
        "timestamp": record.created_at.isoformat(),
    }


# src/utils/types.py — project error reporting pattern
@dataclass
class ProcessingResult:
    """Standard result container for batch operations."""
    successes: list[dict]
    failures: list[ProcessingError]

    @property
    def total(self) -> int:
        return len(self.successes) + len(self.failures)


@dataclass
class ProcessingError:
    """Structured error context for failed operations."""
    record_id: int
    error_type: str
    message: str
    timestamp: datetime
```
