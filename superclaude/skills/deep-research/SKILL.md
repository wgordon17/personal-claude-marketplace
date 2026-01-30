---
name: deep-research
description: |
  Use when user requests deep research, comprehensive analysis, or thorough investigation.
  Triggers on: "research X thoroughly", "deep dive into", "comprehensive analysis of",
  "investigate X exhaustively", "compare X options", "evaluate alternatives for"
allowed-tools: [WebSearch, WebFetch, Read, Glob, Grep, Task]
---

# deep-research — 5-Hop Deep Research Mode

Comprehensive research methodology targeting 40+ sources with multi-hop exploration for thorough analysis of complex topics.

## When to Use

- User asks for "thorough research"
- User wants "comprehensive comparison"
- User needs "deep investigation"
- Technology selection decisions
- Understanding complex topics with nuance
- Evaluating multiple alternatives

## Research Methodology

### Phase 1: Scope Definition

Before starting research:

1. **Clarify the research question**
   - What specific question are we answering?
   - What decision will this inform?

2. **Identify key criteria for evaluation**
   - Performance, cost, complexity, security?
   - What matters most to the user?

3. **Define success metrics**
   - What does "good enough" research look like?
   - How will we know when to stop?

### Phase 2: Source Gathering (40+ Sources Target)

Organize sources into categories:

| Source Type | What to Look For | Priority |
|-------------|------------------|----------|
| **Primary sources** | Official documentation, specifications, papers | Highest |
| **Secondary sources** | Tutorials, blog posts, case studies | High |
| **Community sources** | GitHub issues, Stack Overflow, forums | Medium |
| **Comparative sources** | Benchmarks, comparisons, reviews | High |
| **Recent sources** | News, release notes, changelogs (2025-2026) | Critical |

### Phase 3: 5-Hop Exploration

Follow references 5 levels deep:

```
Topic
└── Primary Reference (hop 1)
    └── Referenced Work (hop 2)
        └── That Work's Reference (hop 3)
            └── Deeper Reference (hop 4)
                └── Final Source (hop 5)
```

**Why 5 hops?**
- Surface-level research stops at hop 1-2
- Expert-level insights often appear at hop 3-4
- Foundational context emerges at hop 4-5

### Phase 4: Multi-Perspective Analysis

Include viewpoints from:

| Stakeholder | What They Care About |
|-------------|---------------------|
| **Maintainers/creators** | Design decisions, roadmap |
| **Power users** | Advanced features, edge cases |
| **Critics** | Limitations, alternatives |
| **Enterprise users** | Scale, support, compliance |
| **Indie developers** | Simplicity, cost, DX |
| **Different tech stacks** | Integration, compatibility |

### Phase 5: Synthesis

1. **Create comparison tables**
   - Side-by-side feature comparison
   - Quantitative metrics where available

2. **Identify consensus opinions**
   - What do most sources agree on?
   - What's the "common wisdom"?

3. **Note controversial or debated points**
   - Where do experts disagree?
   - What's the nature of the disagreement?

4. **Highlight risks and trade-offs**
   - What could go wrong with each option?
   - Hidden costs or complexities?

5. **Provide actionable recommendations**
   - Clear, prioritized suggestions
   - Context-dependent guidance

## Output Format

### Research Report Structure

```markdown
# [Topic] Research Report

## Executive Summary
[2-3 paragraphs summarizing key findings. This should stand alone as a TL;DR.]

## Methodology
- Sources consulted: [count]
- Date range: [most recent to oldest]
- Key search queries used
- Hop depth achieved

## Detailed Findings

### [Category 1: e.g., Performance]
- **Finding 1**: [Description] (Source: [Link/Reference])
- **Finding 2**: [Description] (Source: [Link/Reference])
- **Consensus**: [What most sources agree on]
- **Debate**: [Where sources disagree]

### [Category 2: e.g., Developer Experience]
- **Finding 1**: [Description] (Source: [Link/Reference])
- ...

### [Category 3: e.g., Cost & Licensing]
- ...

## Comparison Table

| Criteria | Option A | Option B | Option C |
|----------|----------|----------|----------|
| Performance | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐ | ⭐⭐⭐ |
| Ease of Use | ⭐⭐⭐ | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐ |
| Community | ⭐⭐⭐⭐ | ⭐⭐⭐ | ⭐⭐⭐⭐⭐ |
| Cost | Free | $X/mo | Free |
| ... | ... | ... | ... |

## Risks & Considerations

### Option A Risks
- [Risk 1]: [Likelihood and impact]
- [Risk 2]: [Likelihood and impact]

### Option B Risks
- ...

### Common Pitfalls
- [Pitfall 1]: [How to avoid]
- [Pitfall 2]: [How to avoid]

## Recommendations

### Primary Recommendation
[Option X] is recommended because:
- [Reason 1]
- [Reason 2]
- [Reason 3]

### Alternative Recommendations
- **If [condition]**: Consider [Option Y] because [reason]
- **If [condition]**: Consider [Option Z] because [reason]

### Not Recommended
- [Option W] because [reason]

## Next Steps
1. [Immediate action]
2. [Short-term action]
3. [Evaluation checkpoint]

## Sources
1. [Source Title](URL) - [Brief description of what it contributed]
2. [Source Title](URL) - [Brief description]
...
[Aim for 40+ numbered sources]
```

## Quality Standards

- **Minimum 40 distinct sources consulted**
- **Include contrasting viewpoints** (not just the popular opinion)
- **Cite sources for factual claims** (inline references)
- **Distinguish facts from opinions** (use language like "according to X" vs "the documentation states")
- **Flag areas of uncertainty** (don't pretend to know what you don't)
- **Include recent (2025-2026) sources** where available
- **Cross-reference claims** (verify important claims with multiple sources)

## Research Anti-Patterns to Avoid

- **Confirmation bias**: Only finding sources that support one conclusion
- **Recency bias**: Ignoring older but still-relevant sources
- **Authority bias**: Taking official docs at face value without community validation
- **Shallow research**: Stopping at hop 1-2 when deeper exploration is needed
- **Scope creep**: Researching tangential topics instead of the core question

## When to Stop

Research is complete when:
- [ ] Core question can be answered confidently
- [ ] Major alternatives have been evaluated
- [ ] Consensus and debates are understood
- [ ] Risks are identified and documented
- [ ] Actionable recommendations can be made
- [ ] Source count target (40+) is met
