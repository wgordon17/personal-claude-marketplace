# Generate or Update CONTRIBUTING.md

Create or update a CONTRIBUTING.md file with consistent Conventional Commits standards.

## Your Task

### Step 1: Analyze the Project

1. **Check for existing CONTRIBUTING.md**—if present, this is an UPDATE operation
2. **Scan git history** to discover actual usage patterns:
   ```bash
   git log --oneline -100
   ```
3. **Analyze codebase structure** to identify logical component scopes:
   - Look at top-level directories
   - Identify major subsystems/modules
   - Note language-specific conventions (Go packages, Python modules, etc.)

### Step 2: Determine Best Types and Scopes

**For NEW files:**
- Propose scopes based on codebase architecture
- Group related components (e.g., individual handlers → `api`)
- Include both component scopes and infrastructure scopes

**For UPDATES:**
- Extract types/scopes from git history
- Identify inconsistencies (same component, different scope names)
- Recommend consolidations (e.g., `supervisor`, `interviewer` → `agents`)
- Mark deprecated scopes with recommended alternatives

### Step 3: Generate CONTRIBUTING.md

Follow this structure:

```markdown
# Contributing to [Project Name]

## Commit Message Guidelines

[Project] follows [Conventional Commits](https://www.conventionalcommits.org/).

### Format

\`\`\`
<type>(<scope>): <description>

[optional body]

[optional footer]
\`\`\`

### Types

| Type | Purpose |
|------|---------|
| feat | Adds new feature |
| fix | Fixes a bug |
| docs | Updates documentation |
| chore | Maintenance (dependencies, configs) |
| refactor | Refactors code (no behavior change) |
| test | Adds or modifies tests |
| perf | Improves performance |
| style | Formatting, whitespace (no code change) |

### Scopes

Project-specific scopes for [Project]:

**Scope Rules:**
- One scope per commit—no commas (e.g., `fix(api):` not `fix(api,auth):`)
- Only lowercase letters, numbers, hyphens, underscores
- If a change spans multiple components, use the primary one or omit scope

**Component Scopes:**
- **scope1**: Description
- **scope2**: Description

**Infrastructure Scopes:**
- **ci**: CI/CD pipelines
- **deps**: Dependency updates
- **build**: Build system
- **repo**: Repository structure

**Deprecated Scopes** (use alternatives):
- `old-scope` → use `new-scope`

### Examples

**Good:**
\`\`\`
feat(auth): adds password reset flow

Users need ability to reset passwords without admin intervention.
\`\`\`

\`\`\`
fix(api): prevents null pointer in handler

Requests with missing auth headers were causing crashes.
\`\`\`

\`\`\`
chore(deps): updates package to v2.0
\`\`\`
*No body needed—change is obvious*

**Bad:**
\`\`\`
🎉 ADDED FEATURE!!!
\`\`\`
*Emojis, ALL CAPS, no type/scope*

\`\`\`
fix(api,auth): updates handlers and tokens
\`\`\`
*Multiple scopes—use one scope or omit if cross-cutting*

\`\`\`
feat(cli): adds new commands

Added three new commands: init, build, and deploy. Each command
has its own handler and validation logic. Updated help text to
include all new commands and their options.
\`\`\`
*Body describes "what" changed—diff shows this; explain "why" instead*

\`\`\`
chore(repo): updates configuration

Changes:
- config.yaml: Updated settings
- README.md: Updated docs

Benefits:
- Better performance
- Clearer documentation
\`\`\`
*Lists files, documents "benefits"—too verbose*

**Better:**
\`\`\`
chore(repo): updates configuration for new environment

New staging environment requires different defaults.
\`\`\`
*Concise, explains why, no file listing*

### Guidelines

**DO:**
- Use present indicative tense ("adds" not "add" or "added")
- Keep subject line ≤50 characters
- Explain WHY in body, not WHAT (diff shows what)
- Keep body to 2-3 lines maximum, each ≤72 characters
- Reference issues: "Closes #123"

**Body structure:**
1. **First line:** Why this change was needed (problem/motivation)
2. **Second line (optional):** Essential technical context if non-obvious
3. **That's it.**

No body needed if the change is obvious from the subject line.

**DON'T:**
- Use emojis or ALL CAPS
- List changed files (git shows this)
- Include statistics (lines changed)
- Add meta-commentary ("Generated with...", "Co-Authored-By...")
- Write verbose explanations or "benefits"
- Describe what changed (the diff shows that)

### Breaking Changes

Add ! after type/scope:

\`\`\`
feat(api)!: changes response structure

BREAKING CHANGE: Responses now wrapped in {data, meta}.
\`\`\`

## Development Workflow

### Git Workflow

**IMPORTANT: Always include git workflow section in generated CONTRIBUTING.md:**

```markdown
### Git Workflow

Use feature branches for all work. Don't commit directly to main.

**One-time setup:**
\`\`\`bash
git config --global push.autoSetupRemote true
\`\`\`

**Feature workflow:**
\`\`\`bash
# Create feature branch from latest upstream
git fetch upstream
git switch -c feature/your-feature upstream/main

# Work and commit
git commit -am "feat(scope): description"
git push  # Auto-sets up tracking

# Rebase before PR
git fetch upstream
git rebase upstream/main

# Create PR
gh pr create
\`\`\`

After PR merges, delete the branch and sync main.
```

### Commit Workflow

**During Development:**
- Commit freely and often—don't worry about perfection
- WIP commits, debugging attempts, and iterations are all fine
- Focus on making progress, not perfect history

**Before PR/Merge:**
- Review your commits—look at the full diff and commit history
- Group related changes—combine commits that belong together logically
- Use interactive rebase to reorganize, squash, and reword

**Decision criteria for squashing:**
- Do these commits represent one logical change?
- Would a reviewer want to see these as separate steps?
- Does each commit add value independently?

If commits are just iterations toward a solution, squash them.
If commits represent distinct logical changes, keep them separate.

**Commit cleanup with git-branchless:**
\`\`\`bash
# Review your commits
git log --oneline -10
git sl  # Visual commit graph

# Reword a commit message
git reword -m "fix(ci): configure node 20 and disable cache" jkl3456

# Squash commits together
git branchless move --fixup -x def5678 -d abc1234
git branchless move --fixup -x ghi9012 -d abc1234

# View the result
git sl
\`\`\`

**You decide** what makes sense for your change—there's no formula.

**Setup:** \`brew install git-branchless && git branchless init\`

[Add project-specific workflow based on repo analysis]
```

## Pull Request Description Guidelines

CRITICAL: Always include this section in CONTRIBUTING.md:

```markdown
### PR Description Guidelines

**Keep PR descriptions brief:**
- State what changed and why
- Use bullet points for multiple changes
- Reference related issues/PRs if applicable
- **NO verification sections, file lists, or test result summaries**

The diff shows what changed. CI shows test results. Don't repeat information that's already visible.

**Good example:**
```
Fixes authentication redirect loop - users were being sent to login after successful signup. Updated redirect logic in EncryptedSignupView.
```

**Bad example:**
```
## Summary
Fixed authentication issues

## Changes Made
- Updated EncryptedSignupView.py line 45
- Modified redirect logic
- Added tests

## Test Results
✅ 189 tests passing
✅ Coverage: 71%

## Files Changed
- apps/accounts/views.py
- apps/accounts/tests/test_views.py
```

The bad example repeats information visible in the PR diff and CI checks.
```

## Key Principles

1. **Scopes should map to architecture** - Not arbitrary, based on actual codebase structure
2. **Consolidate related scopes** - `supervisor`, `interviewer`, `panel` → `agents`
3. **Deprecate, don't delete** - Document old scopes with recommended alternatives
4. **Present indicative tense** - "adds", "fixes", "updates" (not imperative "add")
5. **Concise bodies** - Explain why, not what; 3-4 lines max
6. **No meta-content** - No file lists, statistics, benefits sections, or checklists
7. **Brief PR descriptions** - Just what changed and why, no checklists or test results

## Output

Write the CONTRIBUTING.md file directly to the project root. If updating, preserve any project-specific sections (development workflow, code style) while standardizing the commit message section AND adding PR description guidelines.
