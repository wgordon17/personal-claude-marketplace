---
name: uv-python
description: PROACTIVE skill - Use for ANY task involving Python execution or bash/shell scripts that might run Python. Triggers include creating .py files, writing bash scripts, running terminal commands, automation scripts, CI/CD configs, Makefiles, dependency management, or ANY mention of python/pip/python3. Enforces uv CLI to replace ALL python/pip usage. CRITICAL - Activate BEFORE writing scripts or commands that could invoke Python.
allowed-tools: [Bash, Read, Write, Edit, Grep, Glob]
---

# uv-python

This skill ensures that all Python interactions utilize the `uv` CLI tool, a high-performance Python package and project manager that replaces pip, pipx, pyenv, and other tools.

## ⚠️ MANDATORY RULES - READ FIRST

**YOU MUST FOLLOW THESE RULES FOR ALL PYTHON OPERATIONS:**

1. **NEVER use `python`, `python3`, or `python3.x` directly** - Always use `uv run`
2. **NEVER use `pip install`** - Use `uv add` (in projects) or `uv pip install`
3. **NEVER use raw shebangs like `#!/usr/bin/env python3`** - Use `#!/usr/bin/env -S uv run` or omit shebang entirely (uv run handles it)
4. **NEVER pipe scripts to python** (e.g., `cat script.py | python3`) - Use `uv run script.py`
5. **ALWAYS check for uv availability first** - If not installed, offer installation once

These rules apply to:
- Writing new Python scripts
- Executing existing Python scripts
- Installing dependencies
- Running Python tools (black, ruff, pytest, mypy, etc.)
- Creating virtual environments
- Managing Python versions
- **Bash/shell scripts that execute Python**
- **Terminal commands that invoke Python**
- **CI/CD configurations (GitHub Actions, GitLab CI, etc.)**
- **Makefiles and build scripts**
- **Automation scripts in any language that shell out to Python**

## 🎯 Activation Checklist - When Does This Skill Apply?

This skill should be active if you are about to do ANY of the following:

- [ ] Write a `.py` file
- [ ] Execute a Python script
- [ ] Write a bash/shell script that might call `python`, `python3`, or `pip`
- [ ] Use the Bash tool with commands containing `python`, `pip`, or Python-related tools
- [ ] Create or modify CI/CD configs (GitHub Actions, GitLab CI, Jenkins, etc.)
- [ ] Write a Makefile with Python-related targets
- [ ] Install or manage Python packages
- [ ] Set up a Python development environment
- [ ] Run Python tools (pytest, black, ruff, mypy, etc.)
- [ ] Create automation/deployment scripts that use Python
- [ ] Write Git hooks that execute Python
- [ ] Create or modify any script that has a shebang like `#!/usr/bin/env python3`

**If ANY of the above apply, this skill MUST be active and its rules MUST be followed.**

## Objective

Always prefer `uv` over legacy Python commands to provide:

1. **10-100x faster** dependency resolution and installation
2. **Self-contained scripts** using PEP 723 inline dependency metadata
3. **Unified tooling** for packages, projects, Python versions, and tools
4. **Reproducible environments** with universal lockfiles
5. **Isolated execution** without polluting global Python installations

## Prerequisites

This skill requires `uv` to be installed on the system.

### First-Time uv Check

On the first Python-related operation in a session, check if `uv` is available:

```bash
which uv
```

**If uv IS installed**, proceed silently with uv-based commands.

**If uv is NOT installed**, follow the installation workflow below.

### uv Installation Workflow

When `uv` is not detected, offer installation ONCE per session:

1. **Detect the platform** to determine available installation methods:
   ```bash
   uname -s
   ```

2. **Present platform-appropriate installation options** using the AskUserQuestion tool

3. **Install using the selected method**

#### Platform Detection and Installation Options

**macOS (Darwin):**

Use AskUserQuestion to offer these options:

- **Homebrew** (Recommended if already using Homebrew)
  ```bash
  brew install uv
  ```

- **Official installer** (Standalone, no dependencies)
  ```bash
  curl -LsSf https://astral.sh/uv/install.sh | sh
  ```

- **pipx** (If pipx is installed)
  ```bash
  pipx install uv
  ```

- **Cargo** (If Rust toolchain is installed)
  ```bash
  cargo install uv
  ```

**Linux:**

Use AskUserQuestion to offer these options:

- **Official installer** (Recommended, works on all distributions)
  ```bash
  curl -LsSf https://astral.sh/uv/install.sh | sh
  ```

- **pipx** (If pipx is installed)
  ```bash
  pipx install uv
  ```

- **Cargo** (If Rust toolchain is installed)
  ```bash
  cargo install uv
  ```

**Windows:**

Use AskUserQuestion to offer these options:

- **Official installer** (Recommended, PowerShell)
  ```powershell
  powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
  ```

- **winget** (If Windows Package Manager is available)
  ```powershell
  winget install --id=astral-sh.uv -e
  ```

- **pipx** (If pipx is installed)
  ```powershell
  pipx install uv
  ```

- **Cargo** (If Rust toolchain is installed)
  ```powershell
  cargo install uv
  ```

#### Installation Prompt Template

When prompting the user, use this format:

> I notice `uv` is not installed. `uv` is a high-performance Python package manager (10-100x faster than pip) that this skill requires.
>
> I've detected you're on **[Platform]**. Which installation method would you prefer?

Then use AskUserQuestion with the platform-appropriate options.

#### AskUserQuestion Examples by Platform

**macOS Example:**
```
AskUserQuestion with:
- Question: "Which method would you like to use to install uv?"
- Header: "Install uv"
- Options:
  1. label: "Homebrew", description: "Install via Homebrew (brew install uv). Recommended if you already use Homebrew."
  2. label: "Official installer", description: "Standalone installer with no dependencies. Downloads and installs automatically."
  3. label: "pipx", description: "Install via pipx (requires pipx to be installed)."
  4. label: "Cargo", description: "Build from source using Cargo (requires Rust toolchain)."
```

**Linux Example:**
```
AskUserQuestion with:
- Question: "Which method would you like to use to install uv?"
- Header: "Install uv"
- Options:
  1. label: "Official installer", description: "Standalone installer that works on all distributions. Recommended for most users."
  2. label: "pipx", description: "Install via pipx (requires pipx to be installed)."
  3. label: "Cargo", description: "Build from source using Cargo (requires Rust toolchain)."
```

**Windows Example:**
```
AskUserQuestion with:
- Question: "Which method would you like to use to install uv?"
- Header: "Install uv"
- Options:
  1. label: "Official installer", description: "PowerShell installer script. Works on all Windows versions."
  2. label: "winget", description: "Install via Windows Package Manager (requires winget)."
  3. label: "pipx", description: "Install via pipx (requires pipx to be installed)."
  4. label: "Cargo", description: "Build from source using Cargo (requires Rust toolchain)."
```

#### After Installation

After successful installation, verify it worked:

```bash
uv --version
```

If the user declines installation, continue with legacy Python commands but note the limitation. Do NOT repeatedly prompt about installation in the same session.

#### Complete Installation Workflow Example

Here's how the complete workflow should look:

**Step 1: Check for uv**
```bash
which uv
```

**Step 2: If not found, detect platform**
```bash
uname -s
```

**Step 3: Present options based on platform**
- If `Darwin` (macOS): Use macOS options
- If `Linux`: Use Linux options
- If `MINGW*`, `MSYS*`, or `CYGWIN*`: Use Windows options

**Step 4: Use AskUserQuestion**
Present the appropriate options (see examples above)

**Step 5: Execute installation**
Run the command corresponding to the user's selection

**Step 6: Verify installation**
```bash
uv --version
```

**Step 7: Inform user of success**
"✓ uv has been successfully installed (version X.Y.Z). Proceeding with uv-based Python operations."

## Core Principles

### 1. Always Use uv for Python Execution

Replace all raw `python`/`python3` invocations with `uv run`:

**Legacy:**
```bash
python script.py
python3 -m module
```

**With uv:**
```bash
uv run script.py
uv run python -m module
```

### 2. Always Use uv for Dependency Management

Replace `pip` commands with `uv` equivalents:

**In projects (with pyproject.toml):**
```bash
# Legacy: pip install requests
uv add requests

# Legacy: pip install -r requirements.txt
uv sync
```

**Standalone installations:**
```bash
# Legacy: pip install package
uv pip install package
```

### 3. Use uvx for Tool Execution

Replace tool invocations with `uvx` for ephemeral execution:

**Legacy:**
```bash
black .
ruff check .
pytest
mypy src/
```

**With uv:**
```bash
uvx black .
uvx ruff check .
uvx pytest
uvx mypy src/
```

### 4. Prefer PEP 723 Inline Dependencies for Scripts

When creating Python scripts that require dependencies, use PEP 723 inline metadata to make them self-contained and portable:

```python
# /// script
# dependencies = [
#   "requests>=2.31.0",
#   "rich>=13.0.0"
# ]
# ///

import requests
from rich.pretty import pprint

# Script code here...
```

Running `uv run script.py` automatically creates an isolated environment with the declared dependencies.

**When to use PEP 723:**
- Any script that imports non-stdlib packages
- Scripts shared between team members
- Scripts that need reproducibility
- One-off utilities with specific dependency versions

**See `references/pep723-examples.md` for practical examples.**

### 5. Use uv for Python Version Management

Replace `pyenv` or manual Python installations:

```bash
# Install Python 3.12
uv python install 3.12

# List installed versions
uv python list

# Pin project to specific version
uv python pin 3.12
```

### 6. Initialize Projects with uv

When creating new Python projects:

```bash
# Legacy: mkdir project && cd project && python -m venv .venv
uv init my-project
cd my-project
```

This creates:
- `pyproject.toml` with project metadata
- `.python-version` pinning Python version
- Basic project structure

### 7. Bash Scripts and Terminal Commands

**CRITICAL**: When writing bash scripts, automation scripts, or any shell commands that execute Python, use `uv run`:

**❌ WRONG - Legacy bash script:**
```bash
#!/bin/bash
python3 script.py
python3 -c "print('hello')"
cat script.py | python3
./script.py  # with #!/usr/bin/env python3 shebang
pip install requests
```

**✅ CORRECT - With uv:**
```bash
#!/bin/bash
uv run script.py
uv run python -c "print('hello')"
uv run script.py
uv run script.py  # No shebang needed, or use #!/usr/bin/env -S uv run
uv pip install requests  # or uv add requests in projects
```

**Examples of contexts where this applies:**
- CI/CD workflows (GitHub Actions, GitLab CI)
- Makefiles
- Setup/deployment scripts
- Automation scripts
- Test runner scripts
- Build scripts
- Git hooks
- Cron jobs

**When writing these scripts, ALWAYS think:**
> "Am I about to type `python` or `pip`? Replace with `uv run` or `uv add`/`uv pip install`"

## Command Reference

For a comprehensive mapping of legacy commands to uv equivalents, consult `references/command-mappings.md`.

Quick reference:
- `python script.py` → `uv run script.py`
- `pip install package` → `uv add package` (projects) or `uv pip install package`
- `pip freeze > requirements.txt` → `uv lock` (generates `uv.lock`)
- `python -m venv .venv` → `uv venv`
- `pipx install tool` → `uv tool install tool`
- `pipx run tool` → `uvx tool`

## Workflow Examples

### Creating a One-Off Script

```bash
# Create script with inline dependencies
cat > analyze.py << 'EOF'
# /// script
# dependencies = ["pandas", "matplotlib"]
# ///

import pandas as pd
import matplotlib.pyplot as plt

# Analysis code...
EOF

# Run it (uv handles dependencies automatically)
uv run analyze.py
```

### Starting a New Project

```bash
# Initialize project
uv init data-pipeline

# Add dependencies
cd data-pipeline
uv add pandas numpy
uv add --dev pytest black

# Run the project
uv run python -m data_pipeline

# Run tests
uvx pytest
```

### Installing and Using Tools

```bash
# One-time tool execution (no installation)
uvx ruff check .

# Permanent tool installation
uv tool install black
black .  # Now available globally
```

### Test Execution (Pytest and Pre-commit)

**CRITICAL**: For comprehensive test execution guidance, see `~/.claude/skills/test-runner/SKILL.md`

**Quick reference for pytest with uv:**

```bash
# Run tests in project environment
uv run pytest

# Re-run only failures (efficient!)
uv run pytest --lf -vv

# Run specific test
uv run pytest path/to/test.py::test_function -vv

# Run with ephemeral execution (no project)
uvx pytest
```

**Quick reference for pre-commit with uv:**

```bash
# Run all hooks
uv run pre-commit run --all-files

# Run specific hook
uv run pre-commit run ruff-format

# Run on specific files
uv run pre-commit run --files file1.py file2.py
```

**Key principles**:
- Run test commands **sequentially**, not in parallel
- After failures, re-run **ONLY** the specific tests that failed
- Use `pytest --lf` for efficient failure re-runs
- Use `pytest -k "pattern"` for targeted test selection

See `~/.claude/skills/test-runner/SKILL.md` for detailed patterns and workflows.

## Error Handling

If `uv` commands fail:

1. **Verify uv is installed**: `uv --version`
2. **Check for updates**: `uv self update`
3. **Consult uv docs**: https://docs.astral.sh/uv/

If a specific operation is not supported by uv, fall back to legacy commands with a clear explanation to the user.

## Notes

- This skill applies **automatically** when working with Python, assuming `uv` is installed
- Existing projects using `requirements.txt` can be migrated: `uv add --requirements requirements.txt`
- `uv` respects existing virtual environments but creates isolated ones for scripts
- Tool execution via `uvx` caches tools for fast subsequent runs
- PEP 723 inline dependencies are cached per script hash for instant reruns

## Integration with LSP

The **pyright** language server for Python runs via `uvx`, using the same infrastructure as other Python tools. This powers Claude Code's LSP features for Python files.

**How it works:**
- Command: `uvx --from pyright pyright-langserver --stdio`
- No global installation needed
- Cache location: `~/.cache/uv/`
- Always gets latest version on first run

**Clear pyright cache if issues occur:**
```bash
uv cache clean pyright
```

**LSP Operations for Python:**
- `goToDefinition` - Jump to function/class definitions
- `findReferences` - Find all usages of a symbol
- `hover` - Get type info and docstrings
- `documentSymbol` - List all functions/classes in file
- `workspaceSymbol` - Search for symbols by name

**See the `lsp-navigation` skill** (`~/.claude/skills/lsp-navigation/`) for comprehensive LSP usage guidance.

## Additional Resources

- **Command Mappings**: `references/command-mappings.md` - Comprehensive legacy → uv command table
- **PEP 723 Examples**: `references/pep723-examples.md` - Practical inline dependency examples
- **LSP Navigation**: `~/.claude/skills/lsp-navigation/SKILL.md` - LSP tool usage guide
- **Official Docs**: https://docs.astral.sh/uv/
