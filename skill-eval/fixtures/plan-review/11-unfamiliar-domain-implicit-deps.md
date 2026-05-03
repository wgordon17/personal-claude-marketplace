# Implementation Plan: ML Feature Pipeline

**Goal:** Build an end-to-end ML feature pipeline that ingests raw event data, computes features, stores them in a feature store, and serves them for real-time model inference.

**Workflow:** incremental

---

## File Structure

| File | Purpose |
|------|---------|
| `src/ingestion/event_consumer.py` | Kafka consumer for raw events |
| `src/ingestion/schema_validator.py` | Event schema validation |
| `src/features/transformers.py` | Feature computation transforms |
| `src/features/registry.py` | Feature metadata registry |
| `src/serving/feature_server.py` | Real-time feature serving API |
| `src/serving/cache_layer.py` | Redis cache for hot features |
| `tests/test_transformers.py` | Feature transform unit tests |
| `tests/test_integration.py` | End-to-end pipeline tests |

---

## Task 1: Event Ingestion Consumer
**Dependencies:** None
**Files:** `src/ingestion/event_consumer.py`, `src/ingestion/schema_validator.py`

Implement a Kafka consumer that reads from the `raw-events` topic, validates each event against the schema registry, and writes validated events to the `validated-events` topic. Use the confluent-kafka library with consumer group management. Dead-letter queue for schema validation failures.

The consumer must emit metrics (event count, validation error rate) using the Prometheus client. These metrics are consumed by the feature registry's health monitoring in Task 4.

---

## Task 2: Feature Transformers
**Dependencies:** Task 1
**Files:** `src/features/transformers.py`, `tests/test_transformers.py`

Implement stateless feature transforms: sliding window aggregations (count, sum, avg over 1h/24h/7d windows), entity-level features (user lifetime value, session frequency), and cross-entity features (user-product affinity scores).

Each transformer reads validated events from the consumer output (Task 1) and produces feature vectors. Transformers use the feature definitions registered in Task 4's registry to determine which computations to run and what output schema to produce.

Windowed aggregations use the tumbling window pattern from the Apache Beam SDK. This is a standard stream processing pattern, not a custom abstraction.

---

## Task 3: Feature Store Integration
**Dependencies:** Task 2
**Files:** `src/features/feature_store.py`

Write computed features to the feature store using the Feast SDK. Configure online (Redis) and offline (Parquet/S3) stores. Feature store entity definitions must match the schema produced by Task 2's transformers.

Point-in-time correctness is enforced by Feast's built-in timestamp tracking — the implementation must pass feature timestamps from the event consumer (Task 1) through the transformer (Task 2) to the feature store write call. This is a Feast requirement, not a custom implementation.

---

## Task 4: Feature Registry and Health Monitoring
**Dependencies:** Task 1
**Files:** `src/features/registry.py`

Build a feature metadata registry that tracks:
- Feature definitions (name, type, window, entity key)
- Feature freshness (last computed timestamp)
- Feature health (based on ingestion metrics from Task 1's Prometheus endpoint)

The registry reads Prometheus metrics emitted by the event consumer to determine pipeline health. Feature definitions in the registry are consumed by Task 2's transformers to determine which computations to run.

---

## Task 5: Real-Time Feature Serving
**Dependencies:** Task 3
**Files:** `src/serving/feature_server.py`, `src/serving/cache_layer.py`

Implement a FastAPI endpoint that serves features for real-time inference. The server reads from the Feast online store (populated by Task 3) with a Redis cache layer for frequently-accessed features.

The cache invalidation strategy uses the feature freshness timestamps from the registry (Task 4). When a feature's freshness drops below threshold, the cache entry is evicted and re-fetched from the online store.

---

## Task 6: Integration Testing
**Dependencies:** Task 1, Task 2, Task 3, Task 5
**Files:** `tests/test_integration.py`

End-to-end test: publish synthetic events to Kafka, verify they flow through ingestion → transformation → feature store → serving endpoint. Use testcontainers for Kafka and Redis.

---

## Non-scope

- Model training and evaluation (separate initiative)
- Feature monitoring dashboards (ops team responsibility)
- A/B testing infrastructure (depends on model serving, not feature serving)
