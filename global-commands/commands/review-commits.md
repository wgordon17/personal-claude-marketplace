---
description: AI-assisted commit review for PR readiness
---

# Review Commits for PR Readiness

Perform a careful, thorough analysis of commits on the current branch to prepare for creating a pull request.

**When to use:** Before creating a PR, not during active development.

## Your Task

### Step 0: Understand Project Conventions

**Before reviewing commits, understand what this project expects:**

1. **Search for CONTRIBUTING.md** anywhere in the project:
   ```bash
   # Find CONTRIBUTING.md (prioritize shallower paths)
   find . -name "CONTRIBUTING.md" -not -path "*/node_modules/*" -not -path "*/.git/*" | head -1
   ```
2. **If present, read it carefully:**
   - What commit types are used?
   - What scopes are defined?
   - Are there deprecated scopes with recommended alternatives?
   - Any project-specific commit guidelines?
3. **Use these conventions** when evaluating commit messages

This ensures your review aligns with the project's established standards.

### Step 0.5: Check git-branchless Availability

Before reviewing commits, verify git-branchless is available for reliable history manipulation:

```bash
# Check if git-branchless is installed
if ! command -v git >/dev/null || ! git branchless --version >/dev/null 2>&1; then
  echo "ERROR: git-branchless not installed"
  echo "Install: brew install git-branchless"
  echo "Initialize: git branchless init (in each repo)"
  exit 1
fi

# Check if initialized in current repo
if ! git sl >/dev/null 2>&1; then
  echo "ERROR: git-branchless not initialized in this repo"
  echo "Run: git branchless init"
  exit 1
fi
```

**Why this matters:** git-branchless provides reliable non-interactive commands for rewording, moving, and splitting commits.

### Step 1: Understand the Branch

1. **Identify the base branch** (usually `origin/main` or tracking branch)
2. **List all commits** since divergence
3. **Show commit count** to set expectations

```bash
git log --oneline <base>..<current-branch>
```

### Step 2: Careful Analysis of Each Commit

**CRITICAL:** Do NOT use scripts or automation. You MUST manually review each commit individually.

For each commit:

1. **Read the full commit message** (subject + body)
2. **Check against CONTRIBUTING.md conventions:**
   - Does it use valid types and scopes?
   - Does it follow the project's format?
   - Are deprecated scopes being used?
3. **Review the actual changes:**
   ```bash
   git show <commit-sha> --stat
   git show <commit-sha>
   ```
4. **Analyze:**
   - What does this commit accomplish?
   - Is the message clear and accurate?
   - Does it follow Conventional Commits format?
   - Are the changes cohesive (one logical unit)?
   - Does it overlap with adjacent commits?

### Step 2.5: Detect and Flag AI Slop Commit Messages

**CRITICAL:** These patterns indicate AI-generated meta-commentary instead of meaningful descriptions:

**FORBIDDEN PATTERNS (flag immediately):**
- "addresses PR review feedback"
- "addresses review feedback"
- "addresses feedback"
- "cleanup and improvements"
- "session cleanup"
- "session review"
- "fixes issues"
- "various fixes"
- "miscellaneous changes"

**WHY THESE ARE BAD:**
- They describe the PROCESS (reviewing, cleaning up) not the CHANGES
- They're meta-commentary about development workflow
- They provide zero information about what actually changed
- They make git history useless for understanding changes
- They sound like AI trying to be helpful but failing

**CORRECT APPROACH:**
Look at the actual diff and describe what changed:
- ❌ "addresses PR review feedback"
- ✅ "removes unused config files and updates hook versions"

- ❌ "cleanup and improvements from session review"
- ✅ "adds .yml blocker hook and consolidates test ignores"

- ❌ "fixes issues"
- ✅ "fixes pyright error in subscription_tier return type"

**When you find these patterns:**
1. Read the actual `git show <sha> --stat` to see what changed
2. Write a proper commit message describing those specific changes
3. Flag it clearly in your review: "AI SLOP DETECTED - needs reword"
4. Provide the corrected message

### Step 3: Identify Squash Opportunities

**Look for patterns indicating commits should be combined:**

- **Iterative fixes:** Multiple commits making small adjustments to the same feature
  - Example: "fix: try this", "fix: actually this", "fix(api): handles edge case"
  - → Squash into one commit describing the complete solution

- **WIP/debugging commits:** Temporary commits made during development
  - Example: "wip", "debug logging", "remove debug"
  - → Squash with the actual implementation commit

- **Same scope, related changes:** Multiple commits to the same component
  - Example: "feat(auth): add login", "feat(auth): add logout", "feat(auth): add session"
  - → **Evaluate carefully:** Are these distinct features or one auth system?

- **Fix-ups to earlier commits:** Commits that fix mistakes in the same branch
  - Example: "feat(cli): add command", "fix(cli): typo in help text"
  - → Squash the fix into the original commit

**Do NOT suggest squashing if:**
- Commits represent distinct, independent features
- Each commit tells part of a logical story a reviewer should see
- Commits touch different parts of the codebase for different reasons

### Step 3.5: Identify Split Opportunities

**Look for commits that should be split:**

- **Mixed concerns:** Single commit with unrelated changes
  - Example: Feature code + documentation + test changes in one commit
  - → Split into separate feature, docs, and test commits

- **Excessive scope:** Commit touching >10 files or >500 lines
  - → Consider if this represents multiple logical changes

- **Multiple file groups:** Backend + frontend changes together
  - → Split into backend and frontend commits

**When detecting splits, provide commands:**

```bash
# Manual non-interactive split workflow (works for any commit):
git checkout <sha-to-split>       # Checkout the commit to split
git reset --soft HEAD~1           # Uncommit but keep changes staged
git reset HEAD                    # Unstage all files
git add src/feature.py && git commit -m "feat(api): adds feature"
git add tests/test_feature.py && git commit -m "test(api): adds feature tests"
git add . && git commit -m "docs(api): updates documentation"  # Remaining files
git restack                       # Update git-branchless state
git checkout -                    # Return to original branch
```

**Important notes:**
- This workflow works for **any commit**, not just HEAD - checkout moves you to that commit first
- You can split into 2, 3, or more commits by repeating `git add && git commit`
- Use `git status` between commits to see what files remain
- File-level only - hunk-level splitting requires interactive selection

**Future:** `git split` command coming in v0.11+ will provide additional options. Revisit when released.

### Step 4: Group Commits Logically

Create a suggested grouping:

```
Group 1: Feature X implementation
  - abc1234 feat(api): add endpoint scaffolding
  - def5678 feat(api): implement validation
  - ghi9012 fix(api): handle edge case
  → Squash into: "feat(api): implements user creation endpoint"

Group 2: Feature Y (keep separate)
  - jkl3456 feat(cli): adds export command
  → Keep as-is (distinct feature)

Group 3: CI fixes (squash)
  - mno7890 fix(ci): try node 20
  - pqr2345 fix(ci): disable cache
  → Squash into: "fix(ci): configures node 20 and disables cache"
```

### Step 5: Provide Squashing Commands

For each suggested squash, provide non-interactive commands using git-branchless:

```bash
git branchless move --fixup -x <source-sha> -d <target-sha>
```

**Example for multiple squashes:**
```bash
# Squash def5678 and ghi9012 into abc1234
git branchless move --fixup -x def5678 -d abc1234
git branchless move --fixup -x ghi9012 -d abc1234
```

**Note:** The `--fixup` flag is experimental. If issues occur, use `git branchless undo --yes` to recover.

### Step 5.5: Provide Non-Interactive Rewording Commands

**When suggesting commit message rewording** (for length violations, scope fixes, or tense corrections), use git-branchless for reliable non-interactive rewording:

**For each commit needing rewording:**

```bash
# Reword any commit (not just HEAD) - NON-INTERACTIVE
git reword -m "feat(scope): new shorter message" <commit-sha>

# Multi-line message (subject + body)
git reword -m "Subject line" -m "Body explaining why" <commit-sha>
```

**Template for your output:**

```markdown
## Rewording Commands

To apply these message corrections:

**Commit abc1234:** `feat(api): old long message` → `feat(api): new shorter message`
```bash
git reword -m "feat(api): new shorter message" abc1234
```

**Commit def5678:** `fix(database): another issue` → `fix(db): corrects connection timeout`
```bash
git reword -m "fix(db): corrects connection timeout" def5678
```

**If any reword fails:**
```bash
git branchless undo --yes
```
```

**Key points for rewording:**
- Always use `-m` flag for non-interactive rewording
- Multiple `-m` flags create subject + body
- Commits can be reworded in any order (descendants auto-rebase)
- Use `git branchless undo --yes` to recover from mistakes

**If git-branchless is not installed:**

```bash
brew install git-branchless
git branchless init  # Run in repository
```

### Step 6: Explain Your Reasoning

For each suggestion:
- **Why** should these be squashed?
- **What** is the logical unit they represent?
- **How** does this improve the PR for reviewers?
- **Does it follow project conventions?** (from CONTRIBUTING.md)

### Step 6.5: Error Recovery

**If any git-branchless operation fails or produces unexpected results:**

```bash
# Undo last operation immediately (no confirmation)
git branchless undo --yes

# Check for stale rebase state
test -d .git/rebase-merge && git rebase --abort
test -d .git/rebase-apply && git am --abort

# View operation history
git branchless op log

# Restore to specific operation
git branchless op restore <op-id>
```

**Preventive checks before operations:**
```bash
# Verify clean state
git status

# View current commit graph
git sl
```

### Step 7: Ask for Confirmation

Present your analysis and ask:
- "Does this grouping make sense?"
- "Should I proceed with any adjustments?"
- "Are there commits you want to keep separate that I suggested squashing?"

## Important Notes

- **Check CONTRIBUTING.md first:** Understand project conventions before analyzing
- **No automation:** Every commit requires your thoughtful analysis
- **Context matters:** Read the actual code changes, not just messages
- **Explain reasoning:** Don't just say "squash these"—explain why
- **User decides:** You provide analysis, user makes final decision
- **Rewriting history is fine:** These are pre-PR commits on a feature branch

## Output Format

Provide a structured report:

1. **Tool availability:** git-branchless status (installed/initialized)
2. **Project conventions:** Summary from CONTRIBUTING.md (if present)
3. **Summary:** X commits reviewed, Y squash opportunities, Z split opportunities
4. **Detailed analysis:** Each commit or group with reasoning
5. **Recommended actions:**
   - Rewording commands using `git reword -m`
   - Squashing commands using `git branchless move --fixup -x`
   - Splitting commands using manual reset workflow
6. **Error recovery:** How to undo if needed (`git branchless undo --yes`)
7. **Next steps:** What the user should do

**Full command reference:** Run `/git-history` skill or query Context7 `/arxanas/git-branchless`

Be thorough. Be careful. Help the user create a clean, reviewable PR.

---

## Tool Reference

For comprehensive git-branchless command documentation:
- **Skill:** Run `/git-history` skill
- **Context7:** Query `/arxanas/git-branchless` (376 snippets)
- **Wiki:** https://github.com/arxanas/git-branchless/wiki

**Commands to AVOID (interactive/unreliable):**
- `git rebase -i` - opens editor
- `git add -p` - interactive hunk selection
- `git revise` - no `-m` flag
- `sed` with GIT_EDITOR - platform-dependent
