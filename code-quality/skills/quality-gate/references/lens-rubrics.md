# Lens Rubrics by Work Type

Detailed prompts for each lens, adapted per work type. The main SKILL.md defines the round
structure. This file provides the specific questions and techniques for each lens.

---

## Code Lenses

### Round 1: Correctness

- What inputs would make this produce wrong results?
- What assumptions are untested?
- What edge cases aren't handled?
- What happens with empty/null/zero/negative/max values?
- Are error paths correct (not just happy path)?
- **Tool:** Invoke `sc:analyze` on all modified code files.
- **Reasoning:** Use `sequential-thinking` MCP to decompose correctness check step-by-step.
- **First-principles:** "What are the fundamental invariants this code must maintain? Derive
  them from the requirements, not from what the code currently does."

### Round 2: Completeness

- Read the original request WORD BY WORD.
- Break it into atomic requirements. Check each independently.
- What was requested but not delivered?
- What was identified by you or a subagent but not actioned?
- What was deferred without the user explicitly requesting deferral?
- Are all code paths covered? All branches implemented?
- **Project rules:** Re-read CLAUDE.md/CONTRIBUTING.md. Check version bumps, manifest
  updates, deployment readiness. Will this change actually reach users after merge?
- **Reasoning:** Use `sequential-thinking` MCP to check each atomic requirement.
- **First-principles:** "Return to the original request. What are its fundamental parts?
  Is each one fully satisfied?"

### Round 3: Robustness

- How does this fail under load?
- What happens with bad input, missing dependencies, concurrent access?
- Where are silent failures (errors swallowed, exceptions caught and ignored)?
- Are resource leaks possible (file handles, connections, memory)?
- What happens when external services are unavailable?
- **First-principles:** "What are the fundamental ways this category of system fails?"

### Round 4: Simplicity

- What's over-engineered for the current requirement?
- What abstractions exist for a single consumer?
- What could be deleted and nothing would break?
- What's AI slop? (narrating obvious, sycophantic names, filler docstrings, hedge comments)
- Commented-out code? Unused imports? Dead branches?
- **Tool:** Invoke `sc:improve` for dead code, unused imports, over-abstraction.

### Round 5: Adversarial

- You are a senior engineer who rejected the last 3 PRs. This code has problems. Find them.
- Focus on: correctness bugs, security vulnerabilities, production failure modes.
- What would embarrass the author in code review?
- What will cause a 3am page?
- **First-principles:** "State the fundamental purpose in one sentence. Review against that
  purpose, not the structure you created."

---

## Research Lenses

### Round 1: Accuracy

- What claims are stated as fact without verification?
- What sources were cited vs assumed from training data?
- Are numbers, dates, versions correct?
- Would a domain expert disagree with any conclusion?
- **First-principles:** "What are the foundational facts this analysis rests on? Are they
  verified or assumed?"

### Round 2: Completeness

- What was the actual question asked?
- Did we answer that question, or an adjacent one?
- What perspectives, alternatives, or counterarguments are missing?
- What assumptions did we make about the user's context?
- **Reasoning:** Use `sequential-thinking` MCP to decompose the original question into parts.
- **First-principles:** "What are the fundamental aspects of this question? Did we address
  each one?"

### Round 3: Depth

- Did we stop at the surface, or trace to root causes?
- Are explanations mechanistic (how it works) or just descriptive (what it does)?
- Would someone with deep domain knowledge learn anything from this?
- Are edge cases and exceptions acknowledged?

### Round 4: Clarity

- Would someone unfamiliar with this topic follow the explanation?
- Are technical terms defined or assumed?
- Is the structure logical (not just chronological)?
- Is anything redundant or padded?

### Round 5: Adversarial

- A domain expert will read this. What would embarrass us?
- What's the strongest counter-argument to our recommendation?
- If we're wrong, what's the consequence?
- **First-principles:** "What is the fundamental question being answered? Does our answer
  hold up from first principles, or are we pattern-matching?"

---

## Planning Lenses

### Round 1: Feasibility

- Can each step actually be executed as described?
- Are dependencies between steps identified?
- What happens if step N fails — is there a recovery path?
- Are resource/time estimates realistic?
- **First-principles:** "What are the fundamental constraints this plan operates within?"

### Round 2: Completeness

- Walk through the plan step-by-step mentally. Where do you get stuck?
- What was in the original request that isn't in the plan?
- Are edge cases and failure modes addressed?
- Is scope explicitly bounded (what's in, what's out)?
- **Reasoning:** Use `sequential-thinking` MCP to walk through each step.
- **First-principles:** "What are the fundamental requirements? Is each one addressed by
  a concrete step?"

### Round 3: Robustness

- What if step 3 takes 3x longer than expected?
- What external dependencies could block progress?
- What if requirements change mid-execution?
- Are there single points of failure?

### Round 4: Simplicity

- What steps could be combined or eliminated?
- Is the plan over-specified where flexibility would serve better?
- Are there unnecessary phases or checkpoints?
- Could a simpler approach achieve the same outcome?

### Round 5: Adversarial

- A skeptical PM will review this plan. What's the weakest link?
- What's being hand-waved? Where are the vague verbs ("handle", "process", "manage")?
- Execute this plan mentally step-by-step. Where does it break?
- **First-principles:** "What is the fundamental goal? Does this plan achieve it, or does
  it achieve something adjacent?"

---

## Config/Artifact Lenses

### Round 1: Correctness

- Valid syntax? (parse/validate the file)
- Do values match the environment they target?
- Any typos in keys, paths, or identifiers?

### Round 2: Completeness

- All required fields present?
- No placeholder values left?
- Documentation/comments for non-obvious settings?
- **Project rules:** Re-read CLAUDE.md/CONTRIBUTING.md. Version bumped in ALL required
  files? Registry/marketplace entries updated? Will this change reach users after merge,
  or will caching/versioning prevent delivery?

### Round 3: Security

- No secrets, credentials, or tokens in the file?
- No overly permissive settings?
- Appropriate access controls?

### Round 4: Simplicity

- No commented-out blocks (dead config)?
- No redundant or conflicting settings?
- Consistent with existing project patterns?

### Round 5: Adversarial

- What breaks if this config is deployed to production as-is?
- What happens if someone copies this to a different environment?

---

## Question/Answer Lenses (Reduced: 3 Rounds)

For simple Q&A, use a reduced review:

### Round 1: Accuracy

- Is the answer correct? Verified against sources, not assumed?
- If uncertain, did we say "I don't know" rather than guessing?

### Round 2: Completeness

- Did we answer what was actually asked?
- Any important caveats or context missing?

### Round 3: Clarity

- Is the answer direct and actionable?
- Any AI slop (filler phrases, hedging, unnecessary elaboration)?
