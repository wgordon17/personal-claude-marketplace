---
name: business-panel
description: |
  Use when user needs multi-stakeholder analysis, business impact assessment, or wants
  perspectives from different roles. Triggers on: "analyze from business perspective",
  "what would stakeholders think", "impact analysis", "business case for", "ROI of"
allowed-tools: [Read, Glob, Grep, WebSearch, WebFetch]
---

# business-panel — Multi-Stakeholder Analysis Mode

Simulates perspectives from different organizational roles to provide comprehensive business impact analysis.

## When to Use

- Evaluating business impact of technical decisions
- Making technology investment decisions
- Communicating technical concepts to stakeholders
- Risk assessment for projects
- ROI analysis for features/tools
- Go/no-go decisions on initiatives

## Panel Members

Simulate perspectives from these roles:

### 1. CTO / Technical Leadership
**Focus Areas:**
- Long-term technical strategy alignment
- Team capability requirements
- Technical debt implications
- Scalability and architecture concerns
- Build vs. buy decisions
- Vendor lock-in risks

**Key Questions:**
- Does this align with our technical roadmap?
- What skills do we need that we don't have?
- What technical debt does this create or resolve?
- How does this scale as we grow?

### 2. Product Manager
**Focus Areas:**
- Customer value delivered
- Time to market
- Feature trade-offs
- Competitive positioning
- User experience impact
- Product roadmap alignment

**Key Questions:**
- What customer problem does this solve?
- How does this affect our competitive position?
- What's the impact on our product roadmap?
- Can users tell this is an improvement?

### 3. Engineering Manager
**Focus Areas:**
- Team capacity and skills
- Hiring implications
- Developer experience
- Maintenance burden
- Team morale and growth
- On-call implications

**Key Questions:**
- Does my team have the skills for this?
- Will this create hiring needs?
- How does this affect developer productivity?
- What's the ongoing maintenance cost?

### 4. Finance / CFO
**Focus Areas:**
- Total cost of ownership (TCO)
- Build vs. buy analysis
- Licensing and subscription costs
- Resource allocation
- Budget impact
- Revenue implications

**Key Questions:**
- What's the all-in cost over 3 years?
- How does this affect our burn rate?
- What's the ROI and payback period?
- Are there hidden costs?

### 5. Security / Compliance
**Focus Areas:**
- Risk exposure
- Compliance requirements (SOC2, GDPR, HIPAA, etc.)
- Audit implications
- Data privacy concerns
- Vendor security posture
- Incident response impact

**Key Questions:**
- Does this introduce new security risks?
- How does this affect our compliance posture?
- What data privacy implications exist?
- What's the vendor's security track record?

### 6. Operations / SRE
**Focus Areas:**
- Operational complexity
- Monitoring and alerting needs
- On-call impact
- Disaster recovery
- Performance implications
- Infrastructure requirements

**Key Questions:**
- How does this affect system reliability?
- What new monitoring do we need?
- How does this change our on-call burden?
- What's the blast radius if this fails?

## Analysis Framework

### 1. Stakeholder Impact Matrix

```markdown
| Stakeholder | Impact Level | Key Concerns | Benefits | Risks |
|-------------|--------------|--------------|----------|-------|
| CTO | High | [List] | [List] | [List] |
| Product | Medium | [List] | [List] | [List] |
| Engineering | High | [List] | [List] | [List] |
| Finance | Medium | [List] | [List] | [List] |
| Security | Low | [List] | [List] | [List] |
| Operations | High | [List] | [List] | [List] |
```

### 2. Risk Assessment

| Category | Risk | Likelihood | Impact | Mitigation |
|----------|------|------------|--------|------------|
| **Technical** | [Risk] | H/M/L | H/M/L | [Action] |
| **Business** | [Risk] | H/M/L | H/M/L | [Action] |
| **Operational** | [Risk] | H/M/L | H/M/L | [Action] |
| **Security** | [Risk] | H/M/L | H/M/L | [Action] |
| **Compliance** | [Risk] | H/M/L | H/M/L | [Action] |

### 3. Cost-Benefit Analysis

```markdown
## Initial Investment
| Item | One-Time Cost | Notes |
|------|---------------|-------|
| Development | $X | [hours × rate] |
| Infrastructure | $X | [setup costs] |
| Training | $X | [team upskilling] |
| **Total Initial** | **$X** | |

## Ongoing Costs (Annual)
| Item | Annual Cost | Notes |
|------|-------------|-------|
| Maintenance | $X | [hours × rate] |
| Infrastructure | $X | [hosting, licenses] |
| Support | $X | [vendor, on-call] |
| **Total Annual** | **$X** | |

## Expected Benefits (Annual)
| Benefit | Annual Value | Notes |
|---------|--------------|-------|
| Time savings | $X | [hours saved × rate] |
| Revenue impact | $X | [new revenue or retained] |
| Risk reduction | $X | [avoided incident costs] |
| **Total Annual** | **$X** | |

## ROI Calculation
- **Payback Period**: X months
- **3-Year ROI**: X%
- **NPV (5% discount)**: $X
```

### 4. Decision Recommendation

```markdown
## Recommendation: [APPROVE / DEFER / REJECT]

### Summary
[2-3 sentence summary of recommendation and key reasoning]

### Key Factors For
1. [Factor 1 with supporting evidence]
2. [Factor 2 with supporting evidence]
3. [Factor 3 with supporting evidence]

### Key Factors Against
1. [Factor 1 with supporting evidence]
2. [Factor 2 with supporting evidence]

### Conditions for Success
- [Condition 1 that must be true for this to succeed]
- [Condition 2]

### Key Assumptions
- [Assumption 1 that the analysis depends on]
- [Assumption 2]

### Review Triggers
- Revisit this decision if:
  - [Trigger 1: e.g., costs exceed budget by 20%]
  - [Trigger 2: e.g., key team member leaves]
  - [Trigger 3: e.g., market conditions change]
```

## Output Format

### Business Panel Analysis Report

```markdown
# Business Panel Analysis: [Decision/Topic]

## Executive Summary
[3-4 sentence summary of the decision, key stakeholder perspectives, and recommendation]

## Decision Context
- **What we're deciding**: [Clear statement]
- **Why now**: [Urgency or trigger]
- **Alternatives considered**: [Brief list]

## Stakeholder Perspectives

### CTO View
**Position**: [Support/Oppose/Neutral]
**Key Points**:
- [Point 1]
- [Point 2]
**Concerns**: [What worries this stakeholder]

### Product View
**Position**: [Support/Oppose/Neutral]
**Key Points**:
- [Point 1]
- [Point 2]
**Concerns**: [What worries this stakeholder]

[...repeat for each stakeholder...]

## Impact Analysis

### Stakeholder Impact Matrix
[See template above]

### Risk Assessment
[See template above]

### Cost-Benefit Analysis
[See template above]

## Consensus & Disagreement

### Points of Agreement
- [What all stakeholders agree on]

### Points of Disagreement
- **Issue**: [Description]
  - **CTO view**: [Position]
  - **Finance view**: [Position]
  - **Resolution**: [How to resolve or who decides]

## Recommendation
[See template above]

## Next Steps
1. [Immediate action with owner]
2. [Short-term action with owner and deadline]
3. [Follow-up checkpoint with criteria]
```

## Using the Business Panel

### Step 1: Frame the Decision
- What exactly are we deciding?
- What are the options?
- What's the timeline?

### Step 2: Gather Context
- Read relevant documentation
- Understand current state
- Identify constraints

### Step 3: Simulate Each Perspective
- Put on each stakeholder's "hat"
- Consider their incentives and concerns
- Identify what would make them say yes/no

### Step 4: Synthesize
- Find common ground
- Surface conflicts
- Propose resolution

### Step 5: Recommend
- Make a clear recommendation
- State assumptions
- Define review triggers

## Anti-Patterns to Avoid

- **Analysis paralysis**: Spending too long analyzing when the answer is clear
- **False precision**: Fake numbers that create illusion of rigor
- **Missing stakeholders**: Forgetting someone who should have input
- **Groupthink**: All perspectives artificially agreeing
- **Present bias**: Underweighting future costs or benefits
