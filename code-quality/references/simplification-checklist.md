# Simplification Checklist

Canonical checklist for code simplification. Referenced by the `code-simplifier` agent,
quality-gate Round 4 (Simplicity lens), swarm Code-Simplifier phase, and unfuck implementation
agents.

## Core Principles

**Removal bias:** The burden of proof is on code that exists, not code that's removed. When in
doubt, delete. Every line of code is a liability -- it must justify its existence through clear,
current value. "Might need it later" is not justification; version control exists for recovery.

**Rule of Three:** Do not abstract until the third occurrence. Three similar lines of code is
better than a premature abstraction. Duplicate twice, extract on the third -- *if the
duplication represents the same concept*, not just similar syntax.

**Simplest thing that works:** Prefer the dumbest possible implementation that satisfies
current requirements. Do not build for hypothetical future needs (YAGNI). Do not add
extensibility points, plugin architectures, or configuration for scenarios that don't exist.

**Prefer well-maintained dependencies over custom code:** Before building custom
implementations, check whether a well-maintained library solves the problem. See
`code-quality/references/dependency-evaluation.md` for evaluation criteria. Custom code is
only justified when it provides genuine differentiation or when available libraries introduce
more complexity than they remove.

## What to Simplify

### Unnecessary Abstractions
- Wrapper classes that add zero behavior (just call one method on the wrapped object)
- Interface/protocol/ABC with only one implementation and no plan for a second
- Factory functions that always return the same type
- "Strategy" or "plugin" patterns with exactly one strategy/plugin

### Over-Parameterization
- Functions with 4+ parameters where 2-3 always have the same value
- Config objects passed through 3+ layers only to use 1 field
- Dependency injection for objects that are never swapped in tests
- Generic type parameters that are always instantiated with the same concrete type

### Wrapper Functions
- Functions that do nothing but call another function with the same args
- Middleware that passes through without transformation
- Layers added "for future extensibility" that add no current value
- "Manager" or "Service" classes that just delegate to another class

### Defensive Error Handling
- Try/except blocks that catch exceptions that cannot be raised in that context
- Null checks for values guaranteed non-null by the calling code
- Empty except blocks or `pass` on caught exceptions
- Defensive copies of immutable data

### Ceremonial Code
- Verbose docstrings for trivially obvious functions
- Type annotations so complex they require a comment to understand
- Comments explaining what the code obviously does ("# increment counter")
- Filler comments, AI-generated hedging language, restating-the-obvious comments
- Logging that duplicates information already captured by the framework

### Dead Code
- Commented-out code blocks (if it matters, it's in version control)
- Unused imports, variables, functions, classes, files
- Unreachable branches (after unconditional return/raise/break)
- Feature flags for features that shipped months ago
- TODO comments older than 6 months with no associated ticket

## What NOT to Simplify

- Error handling that IS necessary (boundary cases, external service calls)
- Abstractions used in tests (testability is a valid reason for indirection)
- Patterns present throughout the rest of the codebase (match existing style)
- Anything a reviewer praised as a good design choice
- Code outside the recently modified scope (unless explicitly instructed)
- Security-critical validation (input sanitization, auth checks, rate limiting)
- Observability instrumentation (logging, metrics, tracing) at system boundaries
