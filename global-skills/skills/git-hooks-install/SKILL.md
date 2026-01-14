---
name: git-hooks-install
description: Install defense-in-depth git hooks using pre-commit framework
---

# Install Defense-in-Depth Git Hooks

Automatically install and validate defense-in-depth git hooks in any project using pre-commit framework. This creates an unbypassable safety layer using `prepare-commit-msg` hook to catch `--no-verify` bypass attempts.

## What This Skill Does

1. **Validates environment**:
   - Checks you're in a git repository
   - Verifies pre-commit framework is configured
   - Confirms hook scripts exist at `~/.claude/scripts/git-hooks/`

2. **Updates .pre-commit-config.yaml**:
   - Adds defense-in-depth hooks to local repo configuration
   - Configures prepare-commit-msg (unbypassable) and post-commit (marker setter)
   - Preserves existing hooks and configuration

3. **Installs hooks**:
   - Runs `uv run pre-commit install` with required hook types
   - Installs prepare-commit-msg and post-commit hooks

4. **Tests and validates**:
   - Creates test commits to verify hook ordering
   - Tests marker lifecycle (create, check, consume)
   - Tests --no-verify detection (if not blocked by deny list)
   - Tests branch protection (blocks commits to main/master)
   - Tests merge conflict detection
   - Reports results

## Instructions for Claude

When this skill is invoked:

1. **Pre-flight checks**:
   ```bash
   # Verify in git repo
   git rev-parse --git-dir

   # Check for .pre-commit-config.yaml
   [ -f .pre-commit-config.yaml ] || exit 1

   # Verify hook scripts exist
   [ -f ~/.claude/scripts/git-hooks/prepare-commit-msg.sh ] || exit 1
   [ -f ~/.claude/scripts/git-hooks/post-commit.sh ] || exit 1
   ```

2. **Check if already installed**:
   ```bash
   # Check if hooks already in config
   grep -q "defense-in-depth-safety" .pre-commit-config.yaml
   ```

   If already installed, ask user if they want to:
   - Skip (already installed)
   - Reinstall (update configuration)
   - Uninstall (remove hooks)

3. **Add hooks to .pre-commit-config.yaml**:

   Read the current config, then append this block to the END:

   ```yaml

     - repo: local
       hooks:
         # Defense-in-depth: prepare-commit-msg (cannot be bypassed with --no-verify)
         - id: defense-in-depth-safety
           name: Defense-in-depth safety checks
           entry: ~/.claude/scripts/git-hooks/prepare-commit-msg.sh
           language: system
           always_run: true
           pass_filenames: true
           stages: [prepare-commit-msg]

         # Sets marker after successful commit
         - id: set-success-marker
           name: Set commit success marker
           entry: ~/.claude/scripts/git-hooks/post-commit.sh
           language: system
           always_run: true
           pass_filenames: false
           stages: [post-commit]
   ```

   **IMPORTANT**: Add to the END of the file, after all existing hooks

4. **Install hook types**:
   ```bash
   uv run pre-commit install --install-hooks --hook-type prepare-commit-msg --hook-type post-commit
   ```

5. **Run validation tests**:

   **Test 1: Normal commit flow**
   ```bash
   # Clear any stale markers
   rm -f ~/.cache/hook-checks/*.mark

   # Commit on feature branch
   git commit --allow-empty -m "test: validate defense hooks"

   # Check marker was created
   [ -f ~/.cache/hook-checks/*.mark ] && echo "✅ Marker created" || echo "❌ No marker"
   ```

   **Test 2: Marker consumption**
   ```bash
   # Second commit should consume marker
   git commit --allow-empty -m "test: marker consumption"

   # Marker should be recreated (not the same timestamp)
   ```

   **Test 3: Branch protection** (if not on main):
   ```bash
   CURRENT_BRANCH=$(git rev-parse --abbrev-ref HEAD)
   if [[ "$CURRENT_BRANCH" != "main" && "$CURRENT_BRANCH" != "master" ]]; then
       git switch main 2>/dev/null || git switch -c test-main
       git commit --allow-empty -m "test: should block" 2>&1 | grep -q "BLOCKED" && echo "✅ Blocked" || echo "❌ Not blocked"
       git switch "$CURRENT_BRANCH"
   fi
   ```

6. **Report results**:

   Show summary:
   ```
   ✅ Defense-in-depth hooks installed successfully

   Hooks configured:
   - prepare-commit-msg: Critical safety checks (unbypassable)
   - post-commit: Success marker setter

   What's protected:
   - Commits to main/master branches
   - Merge conflicts in staged files
   - Invalid commit message format (when pre-commit bypassed)
   - --no-verify bypass detection

   Test results:
   ✅ Hook ordering correct
   ✅ Marker lifecycle working
   ✅ Branch protection active

   Documentation: ~/.claude/docs/git-hooks-defense-in-depth.md
   ```

## Error Handling

- If not in git repo: "Error: Not a git repository"
- If no .pre-commit-config.yaml: "Error: Pre-commit not configured. Run: uv run pre-commit sample-config > .pre-commit-config.yaml"
- If hook scripts missing: "Error: Install hook scripts first. See ~/.claude/docs/git-hooks-defense-in-depth.md"
- If installation fails: Show error and suggest manual installation

## Notes for Claude

- This skill should be PROACTIVE - if you notice a project needs git hook safety, suggest running this command
- Always test after installation to verify hooks work
- Be careful not to break existing .pre-commit-config.yaml syntax
- Use YAML-safe editing (proper indentation, no syntax errors)
