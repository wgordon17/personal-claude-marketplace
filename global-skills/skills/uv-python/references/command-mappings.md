# Command Mappings: Legacy → uv

This reference provides a comprehensive mapping of legacy Python commands to their `uv` equivalents.

## Installation

### macOS (Darwin)

| Method | Command | Notes |
|--------|---------|-------|
| Homebrew | `brew install uv` | Recommended if using Homebrew |
| Official installer | `curl -LsSf https://astral.sh/uv/install.sh \| sh` | Standalone, no dependencies |
| pipx | `pipx install uv` | Requires pipx |
| Cargo | `cargo install uv` | Requires Rust toolchain |

### Linux

| Method | Command | Notes |
|--------|---------|-------|
| Official installer | `curl -LsSf https://astral.sh/uv/install.sh \| sh` | Recommended for all distros |
| pipx | `pipx install uv` | Requires pipx |
| Cargo | `cargo install uv` | Requires Rust toolchain |

### Windows

| Method | Command | Notes |
|--------|---------|-------|
| Official installer | `powershell -c "irm https://astral.sh/uv/install.ps1 \| iex"` | PowerShell script |
| winget | `winget install --id=astral-sh.uv -e` | Requires Windows Package Manager |
| pipx | `pipx install uv` | Requires pipx |
| Cargo | `cargo install uv` | Requires Rust toolchain |

## Script Execution

| Legacy Command | uv Equivalent | Notes |
|----------------|---------------|-------|
| `python script.py` | `uv run script.py` | Runs script in project environment or creates isolated env |
| `python3 script.py` | `uv run script.py` | uv automatically uses appropriate Python version |
| `python -m module` | `uv run python -m module` | Run module as script |
| `python -c "code"` | `uv run python -c "code"` | Execute inline Python code |
| `./script.py` (with shebang) | `uv run script.py` | uv respects PEP 723 dependencies |
| `cat script.py \| python3` | `uv run script.py` | Don't pipe to python, use uv run |

## Bash Scripts and Shebangs

| Legacy Approach | uv Approach | Notes |
|----------------|---------------|-------|
| `#!/usr/bin/env python3` | `#!/usr/bin/env -S uv run` | Or omit shebang, use `uv run script.py` |
| `#!/usr/bin/python3` | `#!/usr/bin/env -S uv run` | Or omit shebang, use `uv run script.py` |
| Bash script with `python3 foo.py` | Bash script with `uv run foo.py` | Replace ALL python invocations |
| Bash script with `pip install` | Bash script with `uv pip install` or `uv add` | Depends on context |

### Example: Legacy Bash Script → uv Bash Script

**❌ Legacy:**
```bash
#!/bin/bash
set -e
pip install -r requirements.txt
python3 -m pytest
python3 deploy.py --env prod
```

**✅ With uv:**
```bash
#!/bin/bash
set -e
uv add --requirements requirements.txt  # or uv sync if using pyproject.toml
uvx pytest
uv run deploy.py --env prod
```

## Package Management (Project Context)

| Legacy Command | uv Equivalent | Notes |
|----------------|---------------|-------|
| `pip install package` | `uv add package` | Adds to pyproject.toml and installs |
| `pip install package==1.2.3` | `uv add package==1.2.3` | Specify exact version |
| `pip install package>=1.0` | `uv add "package>=1.0"` | Note: quote version constraints |
| `pip install -r requirements.txt` | `uv add --requirements requirements.txt` | Migrate requirements.txt to pyproject.toml |
| `pip install -e .` | `uv sync` | Installs project in editable mode |
| `pip install --dev package` | `uv add --dev package` | Add development dependency |
| `pip uninstall package` | `uv remove package` | Remove dependency |
| `pip list` | `uv pip list` | List installed packages |
| `pip freeze` | `uv pip freeze` | Export installed packages |
| `pip freeze > requirements.txt` | `uv lock` | Creates uv.lock (more reliable) |
| `pip show package` | `uv pip show package` | Show package info |

## Package Management (Standalone/Global)

| Legacy Command | uv Equivalent | Notes |
|----------------|---------------|-------|
| `pip install package` | `uv pip install package` | Outside project context |
| `pip install --user package` | `uv pip install package` | uv handles user installs automatically |
| `python -m pip install --upgrade pip` | `uv self update` | Update uv itself |

## Virtual Environments

| Legacy Command | uv Equivalent | Notes |
|----------------|---------------|-------|
| `python -m venv .venv` | `uv venv` | Creates virtual environment |
| `python -m venv .venv --python=3.12` | `uv venv --python 3.12` | Specify Python version |
| `source .venv/bin/activate` | N/A | uv commands automatically use project venv |
| `deactivate` | N/A | Not needed with uv workflow |

## Python Version Management

| Legacy Command | uv Equivalent | Notes |
|----------------|---------------|-------|
| `pyenv install 3.12` | `uv python install 3.12` | Install Python version |
| `pyenv install 3.12.1` | `uv python install 3.12.1` | Install specific patch version |
| `pyenv global 3.12` | N/A | Use `uv python pin` in project |
| `pyenv local 3.12` | `uv python pin 3.12` | Pin project to Python version |
| `pyenv versions` | `uv python list` | List installed Python versions |
| `python --version` | `uv run python --version` | Check Python version in project context |

## Tool Management

| Legacy Command | uv Equivalent | Notes |
|----------------|---------------|-------|
| `pipx install black` | `uv tool install black` | Install tool globally |
| `pipx run black .` | `uvx black .` | Run tool without installing |
| `pipx run --spec black==23.0.0 black .` | `uvx --from black==23.0.0 black .` | Run specific tool version |
| `pipx upgrade black` | `uv tool install --upgrade black` | Upgrade tool |
| `pipx uninstall black` | `uv tool uninstall black` | Remove tool |
| `pipx list` | `uv tool list` | List installed tools |

## Common Development Tools

| Legacy Command | uv Equivalent | Notes |
|----------------|---------------|-------|
| `black .` | `uvx black .` | Code formatting |
| `ruff check .` | `uvx ruff check .` | Linting |
| `ruff format .` | `uvx ruff format .` | Fast formatting |
| `mypy src/` | `uvx mypy src/` | Type checking |
| `pytest` | `uvx pytest` | Run tests |
| `pytest tests/` | `uvx pytest tests/` | Run specific test directory |
| `isort .` | `uvx isort .` | Import sorting |
| `flake8` | `uvx flake8` | Linting (legacy) |
| `pylint src/` | `uvx pylint src/` | Linting |
| `bandit -r src/` | `uvx bandit -r src/` | Security linting |

## Project Initialization

| Legacy Command | uv Equivalent | Notes |
|----------------|---------------|-------|
| `mkdir project && cd project` | `uv init project && cd project` | Creates project structure |
| `poetry new project` | `uv init project` | Similar to poetry init |
| `poetry init` | `uv init` | Initialize in existing directory |
| N/A | `uv init --lib` | Initialize library project |
| N/A | `uv init --app` | Initialize application project |

## Dependency Locking

| Legacy Command | uv Equivalent | Notes |
|----------------|---------------|-------|
| `pip freeze > requirements.txt` | `uv lock` | Generates uv.lock |
| `pip-compile requirements.in` | `uv lock` | uv uses pyproject.toml |
| `pip-sync` | `uv sync` | Sync environment with lock |
| `poetry lock` | `uv lock` | Generate lockfile |
| `poetry install` | `uv sync` | Install from lockfile |

## Build and Publish

| Legacy Command | uv Equivalent | Notes |
|----------------|---------------|-------|
| `python setup.py sdist` | `uv build` | Build source distribution |
| `python setup.py bdist_wheel` | `uv build` | Build wheel (uv builds both) |
| `python -m build` | `uv build` | Modern build command replacement |
| `twine upload dist/*` | `uv publish` | Publish to PyPI |

## Script Dependencies (PEP 723)

| Legacy Approach | uv Approach | Notes |
|-----------------|-------------|-------|
| Separate requirements.txt | Inline dependencies in script | See pep723-examples.md |
| Manual venv creation | Automatic isolated environment | uv creates env on first run |
| README with setup instructions | Self-documenting script | Dependencies visible in script |

## Environment Inspection

| Legacy Command | uv Equivalent | Notes |
|----------------|---------------|-------|
| `pip list` | `uv pip list` | List packages in environment |
| `pip show package` | `uv pip show package` | Package details |
| `which python` | `uv run which python` | Python path in project |
| `python -c "import sys; print(sys.executable)"` | `uv run python -c "import sys; print(sys.executable)"` | Python executable path |

## Cache Management

| Legacy Command | uv Equivalent | Notes |
|----------------|---------------|-------|
| `pip cache purge` | `uv cache clean` | Clear package cache |
| N/A | `uv cache prune` | Remove outdated cached files |

## CI/CD Workflows

### GitHub Actions Example

**❌ Legacy:**
```yaml
- name: Install dependencies
  run: |
    python -m pip install --upgrade pip
    pip install -r requirements.txt

- name: Run tests
  run: python -m pytest
```

**✅ With uv:**
```yaml
- name: Install uv
  run: curl -LsSf https://astral.sh/uv/install.sh | sh

- name: Install dependencies
  run: uv sync

- name: Run tests
  run: uvx pytest
```

### GitLab CI Example

**❌ Legacy:**
```yaml
script:
  - pip install -r requirements.txt
  - python -m pytest
  - python build.py
```

**✅ With uv:**
```yaml
before_script:
  - curl -LsSf https://astral.sh/uv/install.sh | sh

script:
  - uv sync
  - uvx pytest
  - uv run build.py
```

### Makefile Example

**❌ Legacy:**
```makefile
install:
	pip install -r requirements.txt

test:
	python -m pytest

lint:
	black .
	ruff check .
```

**✅ With uv:**
```makefile
install:
	uv sync

test:
	uvx pytest

lint:
	uvx black .
	uvx ruff check .
```

## Notes

1. **Quote version specifiers**: When using version constraints with shell special characters, quote them:
   ```bash
   uv add "package>=1.0,<2.0"
   ```

2. **Multiple packages**: Add multiple packages in one command:
   ```bash
   uv add requests pandas numpy
   ```

3. **Platform-specific dependencies**: uv handles platform markers automatically:
   ```bash
   uv add "package; sys_platform == 'win32'"
   ```

4. **Optional dependencies**: Install package extras:
   ```bash
   uv add "package[extra1,extra2]"
   ```

5. **Development dependencies**: Separate production from dev dependencies:
   ```bash
   uv add --dev pytest black mypy
   ```

6. **Upgrade dependencies**:
   ```bash
   uv lock --upgrade           # Upgrade all
   uv lock --upgrade-package requests  # Upgrade specific package
   ```

7. **Workspace support**: For monorepo projects with multiple packages:
   ```bash
   uv workspace add ./packages/subproject
   ```

## Migration Quick Start

To migrate an existing project to uv:

```bash
# 1. Install uv
curl -LsSf https://astral.sh/uv/install.sh | sh

# 2. Initialize uv in project
cd your-project
uv init

# 3. Import existing dependencies
uv add --requirements requirements.txt

# 4. Create lockfile
uv lock

# 5. Sync environment
uv sync
```

## Further Reading

- Official uv docs: https://docs.astral.sh/uv/
- PEP 723 spec: https://peps.python.org/pep-0723/
- uv GitHub: https://github.com/astral-sh/uv
