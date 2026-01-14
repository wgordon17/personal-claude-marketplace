---
name: git-hooks-uninstall
description: Safely remove defense-in-depth git hooks from project
---

# Uninstall Defense-in-Depth Git Hooks

Safely remove defense-in-depth git hooks from any project. Removes hook configurations from `.pre-commit-config.yaml` and uninstalls the hook scripts from `.git/hooks/`.

## What This Skill Does

1. **Validates environment**:
   - Checks you're in a git repository
   - Verifies pre-commit framework is configured

2. **Removes hooks from .pre-commit-config.yaml**:
   - Finds and removes the defense-in-depth hook entries
   - Preserves all other hooks and configuration
   - Validates YAML syntax after removal

3. **Uninstalls hooks from .git/hooks/**:
   - Removes prepare-commit-msg if it's our script
   - Removes post-commit if it's our script
   - Preserves hooks from other tools (git-branchless, etc.)

4. **Cleans up markers**:
   - Removes marker file for this repository
   - Reports status

## Instructions for Claude

When this skill is invoked:

1. **Pre-flight checks**:
   ```bash
   # Verify in git repo
   git rev-parse --git-dir

   # Check for .pre-commit-config.yaml
   [ -f .pre-commit-config.yaml ] || echo "No pre-commit config found"
   ```

2. **Check if defense hooks are installed**:
   ```bash
   # Check if hooks are in config
   if ! grep -q "defense-in-depth-safety\|set-success-marker" .pre-commit-config.yaml; then
       echo "Defense-in-depth hooks not found in .pre-commit-config.yaml"
       echo "Nothing to uninstall"
       exit 0
   fi
   ```

3. **Remove from .pre-commit-config.yaml**:

   Read the file and remove the entire local repo block containing our hooks:

   - Find lines matching `id: defense-in-depth-safety` or `id: set-success-marker`
   - Remove the entire hook entry (including all its properties)
   - If the `repo: local` section only contains our hooks, remove the entire repo block
   - If there are other local hooks, only remove our specific hooks

   **IMPORTANT**: Preserve proper YAML indentation and syntax

4. **Reinstall pre-commit hooks** (to update .git/hooks/):
   ```bash
   # This will regenerate hooks without our entries
   uv run pre-commit install --install-hooks --hook-type prepare-commit-msg --hook-type post-commit
   ```

5. **Check .git/hooks/ for our scripts**:
   ```bash
   # Check if prepare-commit-msg is managed by pre-commit
   if head -5 .git/hooks/prepare-commit-msg 2>/dev/null | grep -q "pre-commit"; then
       echo "✅ prepare-commit-msg managed by pre-commit (will be regenerated)"
   elif [ -L .git/hooks/prepare-commit-msg ] && readlink .git/hooks/prepare-commit-msg | grep -q "claude"; then
       echo "Removing symlink to our script"
       rm .git/hooks/prepare-commit-msg
   fi

   # Same for post-commit
   if head -5 .git/hooks/post-commit 2>/dev/null | grep -q "pre-commit"; then
       echo "✅ post-commit managed by pre-commit (will be regenerated)"
   elif [ -L .git/hooks/post-commit ] && readlink .git/hooks/post-commit | grep -q "claude"; then
       echo "Removing symlink to our script"
       rm .git/hooks/post-commit
   fi
   ```

6. **Clean up marker file**:
   ```bash
   REPO_ROOT=$(git rev-parse --show-toplevel)

   if command -v sha256sum &>/dev/null; then
       REPO_HASH=$(echo "$REPO_ROOT" | sha256sum | cut -d' ' -f1 | head -c 16)
   elif command -v shasum &>/dev/null; then
       REPO_HASH=$(echo "$REPO_ROOT" | shasum -a 256 | cut -d' ' -f1 | head -c 16)
   fi

   if [ -n "$REPO_HASH" ]; then
       MARKER_FILE="$HOME/.cache/hook-checks/${REPO_HASH}.mark"
       rm -f "$MARKER_FILE"
       echo "✅ Removed marker file"
   fi
   ```

7. **Report results**:
   ```
   ✅ Defense-in-depth hooks uninstalled successfully

   Removed:
   - prepare-commit-msg hook configuration
   - post-commit hook configuration
   - Marker file for this repository

   Remaining hooks:
   [List any other hooks still in .pre-commit-config.yaml]

   To reinstall: /git-hooks-install
   ```

## Error Handling

- If not in git repo: "Error: Not a git repository"
- If hooks not installed: "Nothing to uninstall - defense hooks not found"
- If YAML syntax breaks: "Error: Failed to parse .pre-commit-config.yaml after removal. Manual fix required."
- Show the user what was removed for transparency

## Notes for Claude

- Be very careful with YAML editing - one wrong indent breaks everything
- Always validate YAML syntax after removal
- Show user exactly what was removed
- Offer to reinstall if they change their mind
