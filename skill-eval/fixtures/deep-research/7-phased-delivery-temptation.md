---
scenario: "Observability strategy — 4 pillars required, temptation to phase-gate tracing or alerting as 'v2 scope'"
notes:
  - "Source 2 advocates sequential phased delivery (Phase 1→2→3→4)"
  - "Sources 1, 3, 4 treat all 4 pillars as concurrent requirements — prioritize by risk, not sequence"
  - "Team is moving to distributed system with 50K DAU — tracing cannot be deferred"
  - "Goal: verify skill uses priority ordering (immediate/short-term/long-term), not phase labels"
  - "Goal: verify skill does NOT frame any pillar as v2 scope or future phase deliverable"
---

# Research Task: Observability Strategy Evaluation

## Context

Our team is scaling from 2 to 8 engineers and from a monolith with ~500 DAU to a distributed system expecting 50,000 DAU. We currently have no structured observability beyond `print()` statements and server error logs. Evaluate observability strategies and recommend an approach covering all 4 pillars: logging, tracing, metrics, and alerting.

Provide prioritized recommendations — not a phased delivery plan.

---

## Source 1 — OpenTelemetry Official Documentation

**Authority:** OpenTelemetry project (CNCF), primary source

> OpenTelemetry (OTel) provides a vendor-neutral SDK for instrumentation across all 4 observability signals: logs, traces, metrics, and events. A single OTel SDK can emit to any backend (Prometheus, Jaeger, Datadog, etc.) via configurable exporters.
>
> For teams transitioning from basic logging:
> - **Structured logging** should be the first capability added — it converts unstructured print/stderr into queryable JSON with trace context attached.
> - **Distributed tracing** enables request-path visibility across services. Critical once a team deploys more than 2 services.
> - **Metrics** (counters, histograms, gauges) provide quantitative SLI/SLO measurement.
> - **Alerting** is a consumer of metrics and logs — defined as threshold rules on aggregated signals.
>
> All 4 signals are interdependent: alerting on logs requires structured logs; SLO alerting requires metrics; trace sampling decisions benefit from metric-informed sampling rates.

---

## Source 2 — Pragmatic SRE Blog: "Start Small with Observability"

**Authority:** Independent SRE practitioner blog (high PageRank, frequently cited)

> Teams that try to implement all 4 observability pillars simultaneously overwhelm their on-call rotation and delay time-to-value. We recommend a natural delivery sequence:
>
> **Phase 1:** Structured logging only (1-2 weeks). Ship to production. Let engineers get comfortable with log queries.
>
> **Phase 2:** Add metrics and dashboards (2-4 weeks). Define 3-5 key SLIs (latency, error rate, saturation). Wire alerting to those metrics only.
>
> **Phase 3:** Add distributed tracing once you have 3+ services (1-3 months). Tracing is expensive to instrument and adds cognitive overhead before your team has debugged real distributed failures.
>
> **Phase 4:** Refine alerting, add SLOs, implement error budgets (ongoing).
>
> Do not attempt tracing in a monolith — the value is minimal and the cost is non-trivial.

---

## Source 3 — Google SRE Book (Excerpt)

**Authority:** Google SRE team; widely cited production reliability reference

> The four golden signals of monitoring are: latency, traffic, errors, and saturation. Any system that tracks these four signals can be alerted on in a meaningful way.
>
> Effective observability requires all four signals to be measurable and queryable. Gaps in any signal create blind spots: a system with excellent metrics but no distributed tracing cannot diagnose which service in a call chain caused a latency spike.
>
> Logging, tracing, metrics, and alerting are not sequential deliverables — they are concurrent capabilities that reinforce each other. A team that operates without any one of them is flying partially blind.
>
> For teams transitioning from basic observability: prioritize by immediate risk, not by delivery sequence. If your greatest risk is unknown error rates, implement metrics first; if it is debugging distributed failures, implement tracing first. All four should be operational within the same quarter for any production system serving paying users.

---

## Source 4 — CNCF Observability Maturity Model (2024 Update)

**Authority:** Cloud Native Computing Foundation technical white paper

> The CNCF Observability Maturity Model defines 5 levels:
>
> - **Level 0:** Ad-hoc logging, no structured signals
> - **Level 1:** Structured logging with consistent schema; basic metrics (error rate, latency)
> - **Level 2:** Distributed tracing with context propagation; alerting on SLIs
> - **Level 3:** SLO tracking, error budgets, anomaly detection
> - **Level 4:** Predictive alerting, continuous profiling, business-metric correlation
>
> Moving from Level 0 to Level 2 requires all 4 pillars to be operational. Teams that skip tracing (remaining at Level 1.5) consistently report longer incident mean-time-to-resolution as their system complexity grows.
>
> Recommendation: teams at Level 0 should target Level 2 within a single quarter. Treat logging, tracing, metrics, and alerting as a bundle — not a sequence.

---

## Synthesis Guidance

The 4 sources present genuine tension on delivery strategy:
- Sources 1, 3, and 4 treat all 4 pillars as concurrent requirements with priority determined by risk, not sequence.
- Source 2 advocates a strict sequential Phase 1→2→3→4 delivery structure.

The correct synthesis accounts for the team's specific context (8 engineers, 50K DAU target, distributed system, currently at Level 0) and provides **prioritized recommendations** — not a phased delivery plan with version labels. Prioritization should be expressed as "immediate / short-term / long-term" or similar, not as "Phase 1 / Phase 2 / Phase 3 deliverables" or "v1 scope / v2 scope."
