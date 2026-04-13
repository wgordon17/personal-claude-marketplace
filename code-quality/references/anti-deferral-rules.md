# Anti-Deferral Rules

> **Cross-reference note:** This file is referenced by orchestration skills and agents that must never skip work.
> Consumers — Skills: swarm, map-reduce, speculative, quality-gate. Agents: architect, code-reviewer, code-simplifier,
> performance, plan-adherence, qa, security, test-runner.

Canonical rules for scope integrity and phase compliance. All skills and agents that coordinate multi-phase work
reference this file. Do not duplicate these rules inline — point here.

---

## Scope Integrity

**Complete all work the task requires.** Present every item as a task; if prioritization is needed, offer options via
`AskUserQuestion` — because the model does not decide what is in scope.

**Treat skip conditions as a contract, not a suggestion.** Each phase table lists explicit skip conditions. Work
outside those conditions runs — because unilateral omissions are invisible to the user and violate the skill's
structural guarantees.

**Never use version-boundary language to defer work.** Labels like "v1/v2," "future iteration," "phase N
enhancement," or "out of scope for this implementation" are self-scoping — because scope decisions belong to the
user, not the model. If scope is genuinely uncertain, use `AskUserQuestion`. Legitimate exception: actual software
version references (API v1, Pydantic v2).

**Never fabricate user deferral.** Claiming the user "explicitly deferred" work requires a citable user message in
the current conversation — because a claim without a citation is fabricated, and fabricated deferral is a
violation of the user's trust.

**Describe unimplemented work accurately.** Use "not yet implemented" for omissions and "immediate / short-term /
long-term" for prioritization in reports — because version labels ("v2 gaps") misframe incomplete work as a design
choice.

---

## Phase Compliance

Complete all defined phases in sequence — skipping phases based on perceived cost, time, or simplicity violates
the skill's structural contract. Each phase exists because it catches issues no prior phase can see. A clean
earlier phase says nothing about what a later phase will find.

The "When to Skip" table in each skill is the only valid skip authority. If a condition in that table does not
apply, the phase runs — regardless of how small or simple the task appears.

---

## Cost Framing

Resource investment matches task scope. The skill's agent count and phase structure are pre-sized for the work —
treat them as budget, not ceiling.

"It would be expensive" is never a valid reason to skip work or reduce agent count — because the cost of rework
from skipped review always exceeds the cost of the review itself. Spawn the agent. Fix the finding. Write the
test.

Prefer one strong pass (opus for judgment) over multiple weaker passes that require rework — because precision
scales better than iteration.
