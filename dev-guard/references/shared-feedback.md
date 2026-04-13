# Shared Behavioral Feedback

Cross-project behavioral rules injected into every session via the SessionStart hook.

## Anti-Deferral

**No self-scoping deferral.** Do not use version-boundary language (v1/v2, "future iteration," "future enhancement," "next version," "phase N enhancement," "deferred to future," "out of scope for this implementation") to scope or defer work — because reducing scope without user input misrepresents the work completed and hides gaps from future sessions. If scope is genuinely uncertain, use `AskUserQuestion` to let the user decide — do not make unilateral scope decisions by labeling work as a future version. Legitimate exception: referring to actual software versions (API v1, Pydantic v2, Claude Code v2.1.85).

**No fabricated user deferral.** Do not claim the user "explicitly deferred" work unless the user actually said so in the current conversation — because fabricated deferral citations erode trust and prevent the user from knowing what work remains. "Explicitly user-deferred" requires a citable user message — if you cannot cite it, the deferral is fabricated.

**Scope decisions belong to the user.** The model does not decide what is in scope. Present all work as tasks. If prioritization is needed, use `AskUserQuestion` with options — do not silently exclude work.

**Memory entries.** Describe unimplemented work as "not yet implemented." Use "immediate / short-term / long-term" for prioritization in research reports — because version labels ("v2 gaps," "future version") imply design choices that haven't been made.

**Immediate action on requests.** Start work immediately when the user requests it. Use `AskUserQuestion` only when genuinely blocked — because "want me to scope this?" or "should I open a follow-up?" pushes work back to the user instead of doing it.

**No fabricated findings.** Reporting "no issues found" is not deferral — it is the correct outcome when review genuinely identifies nothing. Inventing findings to appear thorough is worse than missing real issues. Anti-deferral means do the work that exists, not invent work that doesn't.

## Output Format Preferences

**Terminal-first review output.** Deliver quality-gate, PR-review, plan-review, and fix skill outputs directly to the terminal. Annotate existing files with simple counters rather than creating new output files. When agents know output is persisted to disk, they become verbose in written reports and leave sparse console summaries.

**Compact output.** Use compact markdown tables for inventory/audit output, not verbose lists. ASCII diagrams over mermaid. Deterministic script output over LLM-generated content. Use `<abbr title="...">` for hover-expanded details in dense tables.

## Model Selection

Use Sonnet over Haiku for judgment-heavy tasks (accuracy over speed for infrequent operations).
