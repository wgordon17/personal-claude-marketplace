# Implementation Plan: CI/CD Pipeline

**Goal:** Implement a CI/CD pipeline for the project with automated testing, security scanning, deployment gating, and rollback capability.

**Tracker:** None
**Workflow:** incremental

---

## Key Decisions

- GitHub Actions as CI/CD platform (team already uses GitHub, no additional tooling needed)
- Docker-based deployments with container registry (matches existing infrastructure)
- Blue-green deployment to production (standard pattern for zero-downtime releases)
- Dependabot for dependency security scanning (native GitHub integration, zero setup)

## Non-scope

- Advanced deployment strategies (canary releases, feature flags) — blue-green covers the rollback requirement for the initial release
- Custom security rule authoring — out of scope for pipeline setup

## File Structure

| File | Purpose |
|------|---------|
| `.github/workflows/ci.yml` | Lint, unit test, integration test workflow |
| `.github/workflows/deploy-staging.yml` | Staging deployment with smoke tests |
| `.github/workflows/deploy-prod.yml` | Production blue-green deployment |
| `docker/Dockerfile` | Multi-stage build for the application |
| `docker/docker-compose.test.yml` | Integration test environment |
| `scripts/smoke-test.sh` | Post-deployment smoke test suite |
| `scripts/deploy-blue-green.sh` | Blue-green deployment orchestration |
| `docs/runbook-rollback.md` | Manual rollback procedure documentation |
| `.github/dependabot.yml` | Dependency update and vulnerability scanning config |

---

## Tasks

### Task 1: CI Workflow — Lint and Unit Tests
**Dependencies:** None
**Files:** `.github/workflows/ci.yml`

Set up GitHub Actions workflow triggered on push and PR:
- Python linting with ruff
- Type checking with pyright
- Unit tests with pytest and coverage reporting
- Fail-fast on any check failure

### Task 2: Integration Test Suite
**Dependencies:** Task 1
**Files:** `docker/docker-compose.test.yml`, `.github/workflows/ci.yml`

Add Docker Compose environment for integration tests:
- PostgreSQL and Redis containers matching production versions
- Run integration test suite against containerized services
- Add integration test step to CI workflow after unit tests pass

### Task 3: Container Build and Registry Push
**Dependencies:** Task 2
**Files:** `docker/Dockerfile`, `.github/workflows/ci.yml`

Multi-stage Docker build optimized for production:
- Build stage with all dependencies
- Runtime stage with minimal base image
- Push to GitHub Container Registry on main branch merges
- Tag with commit SHA and `latest`

### Task 4: Staging Deployment with Smoke Tests
**Dependencies:** Task 3
**Files:** `.github/workflows/deploy-staging.yml`, `scripts/smoke-test.sh`

Automated deployment to staging environment:
- Triggered on successful main branch CI
- Deploy container to staging Kubernetes namespace
- Run smoke test suite (health checks, critical path verification)
- Block production deployment if smoke tests fail

### Task 5: Production Deployment — Blue-Green
**Dependencies:** Task 4
**Files:** `.github/workflows/deploy-prod.yml`, `scripts/deploy-blue-green.sh`

Blue-green deployment to production:
- Manual trigger with approval gate (requires team lead approval)
- Deploy to inactive (green) environment
- Run smoke tests against green
- Switch load balancer to green on success
- Keep blue running for 30 minutes as fallback

### Task 6: Post-Deployment Monitoring
**Dependencies:** Task 5
**Files:** `docs/runbook-rollback.md`

Add deployment observability and rollback procedures:
- Document manual rollback procedure: switch load balancer back to blue environment, verify health, investigate root cause
- Add Prometheus metrics endpoint for deployment version tracking
- Configure Grafana dashboard for error rate and latency post-deploy

Note: Automated rollback on error rate spike is captured as a monitoring alert that pages the on-call engineer. Manual rollback via the documented runbook keeps the blast radius predictable and avoids false-positive automated rollbacks during the initial deployment stabilization period.

### Task 7: Security Integration
**Dependencies:** Task 1
**Files:** `.github/dependabot.yml`

Add security scanning to the pipeline:
- Configure Dependabot for weekly dependency vulnerability scanning
- Auto-create PRs for vulnerable dependency updates
- Add security advisory notifications to the team Slack channel

Note: Static Application Security Testing (SAST) via SonarQube and Dynamic Application Security Testing (DAST) via OWASP ZAP require dedicated infrastructure (SonarQube server, ZAP runner with browser automation) and are better addressed as a parallel initiative by the security team who can configure the scanning rules and triage the results. Dependabot covers the most common vulnerability vector (known CVEs in dependencies) with zero infrastructure overhead.
