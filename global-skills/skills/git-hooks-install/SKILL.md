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
   - Locates hook scripts in the global-skills plugin

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

   # Find global-skills plugin path
   PLUGIN_HOOKS_DIR="$(find ~/.claude/plugins/cache -path "*/global-skills/*/git-hooks" -type d | head -1)"
   if [ -z "$PLUGIN_HOOKS_DIR" ]; then
       echo "Error: global-skills plugin not found. Install with:"
       echo "  claude plugin install global-skills@private-claude-marketplace"
       exit 1
   fi

   # Verify hook scripts exist in plugin
   [ -f "$PLUGIN_HOOKS_DIR/prepare-commit-msg.sh" ] || exit 1
   [ -f "$PLUGIN_HOOKS_DIR/post-commit.sh" ] || exit 1

   echo "✓ Found hook scripts at: $PLUGIN_HOOKS_DIR"
   ```

2. **Check if already installed and detect version**:
   ```bash
   # Check if hooks already in config
   if grep -q "defense-in-depth-safety" .pre-commit-config.yaml; then
       echo "Defense-in-depth hooks already installed"

       # Check if using old path (~/.claude/scripts/git-hooks/)
       if grep -q "~/.claude/scripts/git-hooks" .pre-commit-config.yaml; then
           echo "⚠️  Hooks are using OLD path: ~/.claude/scripts/git-hooks/"
           echo "   Will update to plugin path: $PLUGIN_HOOKS_DIR"
           NEEDS_UPDATE=true
       else
           echo "✓ Hooks already using plugin path"
           NEEDS_UPDATE=false
       fi
   else
       echo "Defense-in-depth hooks not installed"
       NEEDS_UPDATE=false
   fi
   ```

   If already installed with OLD path, automatically update the paths.

   If already installed with correct path, ask user if they want to:
   - Skip (already up-to-date)
   - Reinstall (force reinstall)
   - Uninstall (remove hooks)

3. **Add or update hooks in .pre-commit-config.yaml**:

   **If NEEDS_UPDATE=true (old paths detected):**
   ```bash
   # Update old paths to new plugin path
   sed -i.bak "s|~/.claude/scripts/git-hooks/|$PLUGIN_HOOKS_DIR/|g" .pre-commit-config.yaml
   echo "✓ Updated hook paths to plugin location"
   ```

   **If hooks not installed yet:**

   Read the current config, then append this block to the END, using the absolute path from `$PLUGIN_HOOKS_DIR`:

   ```yaml

     - repo: local
       hooks:
         # Defense-in-depth: prepare-commit-msg (cannot be bypassed with --no-verify)
         - id: defense-in-depth-safety
           name: Defense-in-depth safety checks
           entry: /Users/wgordon/.claude/plugins/cache/private-claude-marketplace/global-skills/1.0.2/git-hooks/prepare-commit-msg.sh
           language: system
           always_run: true
           pass_filenames: true
           stages: [prepare-commit-msg]

         # Sets marker after successful commit
         - id: set-success-marker
           name: Set commit success marker
           entry: /Users/wgordon/.claude/plugins/cache/private-claude-marketplace/global-skills/1.0.2/git-hooks/post-commit.sh
           language: system
           always_run: true
           pass_filenames: false
           stages: [post-commit]
   ```

   **IMPORTANT**:
   - Replace the example paths with the actual absolute path from `$PLUGIN_HOOKS_DIR`
   - Add to the END of the file, after all existing hooks
   - Use the absolute path (substitute the variable value into the YAML)

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
