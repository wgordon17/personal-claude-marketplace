# Documentation Taxonomy

Canonical definitions for documentation triggers and surfaces. All documentation-aware skills
in this plugin reference this file. Do not maintain ad-hoc lists elsewhere — if a skill needs
to check documentation, it points here.

---

## Documentation Triggers

A change requires documentation updates when it modifies the **public contract** of the project.
The public contract is what users, consumers, or integrators rely on.

### Changes that ALWAYS require documentation updates

| Category | Examples |
|----------|----------|
| **New public API** | New exported function, class, module, struct, trait, endpoint, route, handler, view |
| **New CLI command** | New subcommand, flag, argument, entry point, make target, script in `bin/` |
| **New UI surface** | New page, route, component exposed in a component library |
| **New configuration** | New config option, environment variable, feature flag, default value |
| **New dependency** | New runtime dependency that users must install or configure |
| **New component** | New skill, agent, command, hook, plugin, package, crate, module |
| **Removed any of the above** | Deletion of anything users could depend on |
| **Renamed/moved any of the above** | Path changes, name changes, namespace changes |
| **Changed behavior** | Changed function signature, return type, error behavior, default values, API response format, config semantics |
| **Changed requirements** | New minimum version, new system dependency, dropped platform support |

### Changes that DO NOT require documentation updates

| Category | Examples |
|----------|----------|
| **Internal refactoring** | Restructuring private code, renaming private variables, extracting helpers |
| **Test changes** | Adding, modifying, or removing tests (unless test instructions are documented) |
| **Performance improvements** | Optimizations that don't change behavior or API |
| **Code style** | Formatting, linting fixes, import reordering |
| **Build internals** | CI config changes, build script internals (unless user-facing build commands change) |
| **Comments on internal code** | Adding docstrings to private functions |

### Gray areas — use judgment

| Category | When docs ARE needed | When docs are NOT needed |
|----------|---------------------|------------------------|
| **Bug fixes** | If the fix changes documented behavior | If the fix aligns behavior with existing docs |
| **Dependency updates** | If minimum version changes or new install steps needed | If it's a compatible patch update |
| **Error messages** | If error handling strategy is documented | If error messages are implementation detail |
| **Logging changes** | If log format/level is part of the public contract | If logs are internal observability |

---

## Documentation Surfaces

Where documentation lives. Organized by type, with detection patterns for automated discovery.

### Human-readable documentation

| Surface | Detection | What it contains |
|---------|-----------|-----------------|
| **README.md** | `Glob("**/README.md")` | Project overview, installation, usage, component lists, feature descriptions |
| **CONTRIBUTING.md** | `Glob("**/CONTRIBUTING.md")` | Development workflow, coding standards, PR process |
| **Changelog** | `Glob("**/CHANGELOG.md")` OR `Glob("**/HISTORY.md")` OR `Glob("**/CHANGES.md")` | Version history, breaking changes, migration notes |
| **Documentation directory** | `Glob("**/docs/**/*.md")` | Guides, tutorials, architecture docs, API references |
| **Examples** | `Glob("**/examples/**/*")` | Usage examples, sample code, demo projects |

### Package manifests (machine-readable metadata)

| Surface | Detection | Key fields |
|---------|-----------|------------|
| **package.json** | `Glob("**/package.json")` | `description`, `bin`, `exports`, `main`, `scripts`, `keywords` |
| **pyproject.toml** | `Glob("**/pyproject.toml")` | `[project] description`, `[project.scripts]`, `[project.optional-dependencies]` |
| **Cargo.toml** | `Glob("**/Cargo.toml")` | `[package] description`, `[[bin]]`, `[features]`, `[dependencies]` |
| **go.mod** | `Glob("**/go.mod")` | Module path, Go version, dependencies |
| **plugin.json** | `Glob("**/plugin.json")` | `name`, `description`, `version` |
| **marketplace.json** | `Glob("**/marketplace.json")` | Plugin entries with `name`, `version`, `description`, `tags` |
| ***.cabal** | `Glob("**/*.cabal")` | Package description, exposed modules, executables |

### Inline documentation

| Surface | Detection | Scope |
|---------|-----------|-------|
| **Python docstrings** | `Grep(pattern="\"\"\"\\|'''", type="py")` | Public functions, classes, modules |
| **JSDoc/TSDoc** | `Grep(pattern="/\\*\\*", type="ts")` or `type="js"` | Exported functions, classes, interfaces |
| **Rustdoc** | `Grep(pattern="///\\|//!", type="rust")` | Public items (`pub fn`, `pub struct`, etc.) |
| **GoDoc** | `Grep(pattern="^// [A-Z]", type="go")` | Exported identifiers |

### Structural documentation (inside README/docs)

These are not separate files but specific structures WITHIN documentation files that need
updating when components change.

| Structure | What to check |
|-----------|--------------|
| **Component tables** | Rows listing skills, agents, commands, endpoints, etc. Row count must match on-disk component count. |
| **Component counts in headings** | `## Skills (9)` — the number must match the actual count |
| **Dependency matrices** | Tables showing plugin/package dependencies — must reflect current state |
| **Feature lists** | Bullet lists of features — must include all current features, exclude removed ones |
| **Installation instructions** | Commands, package names, version numbers — must work |
| **Usage examples** | Code snippets — must reference existing functions, correct signatures |
| **Configuration tables** | Config keys, default values, types — must match actual schema |

---

## Cross-Surface Consistency Rules

When multiple surfaces document the same thing, they must agree:

1. **Manifest ↔ README** — Package manifest description must be consistent with README overview
2. **Plugin ↔ Registry** — plugin.json description must match marketplace.json entry
3. **README ↔ README** — Root README component counts must match plugin/package README counts
4. **Code ↔ Docs** — Inline docstrings must match README/API doc descriptions
5. **Count ↔ Reality** — Any stated count (skills, endpoints, commands) must match Glob results

---

## Ecosystem-Specific Component Discovery

Patterns for discovering registrable components per ecosystem. Use to verify documentation
completeness (component exists on disk → should be documented).

### Python
```
Grep(pattern="def\\s+\\w+", path="src/", type="py")                    # Public functions
Grep(pattern="@click\\.command|@click\\.group", ...)                    # CLI (Click)
Grep(pattern="@app\\.command|def\\s+\\w+.*typer", ...)                  # CLI (Typer)
Grep(pattern="argparse\\.ArgumentParser|add_subparsers", ...)           # CLI (argparse)
Grep(pattern="@app\\.route|@router\\.", ...)                            # API (Flask/FastAPI)
Grep(pattern="class.*APIView|class.*ViewSet", ...)                      # API (Django REST)
Grep(pattern="\\[project\\.scripts\\]|\\[tool\\.poetry\\.scripts\\]", path="pyproject.toml")
```

### Rust
```
Grep(pattern="^pub fn|^pub struct|^pub enum|^pub trait", type="rust")   # Public API
Grep(pattern="\\[\\[bin\\]\\]|name\\s*=", path="Cargo.toml")           # Binary targets
Grep(pattern="#\\[command|#\\[clap", type="rust")                        # CLI (clap)
Grep(pattern="pub mod", path="src/lib.rs")                               # Module exports
```

### Node/TypeScript
```
Grep(pattern="\"bin\":|\"exports\":|\"main\":", path="package.json")    # Entry points
Grep(pattern="export (default |const |function |class )", type="ts")    # Public exports
Grep(pattern="app\\.(get|post|put|delete|patch)\\(|router\\.", ...)     # API (Express)
Grep(pattern="@Controller|@Get|@Post", type="ts")                       # API (NestJS)
Grep(pattern="\\.command\\(|program\\.", ...)                            # CLI (Commander)
```

### Go
```
Grep(pattern="^func [A-Z]", type="go")                                  # Exported functions
Grep(pattern="http\\.Handle|mux\\.", type="go")                         # HTTP handlers
Grep(pattern="cobra\\.Command|flag\\.", type="go")                      # CLI commands
```

### Frontend
```
Glob("**/components/**/*.{tsx,vue,svelte}")                              # UI components
Glob("**/pages/**/*.{tsx,vue,svelte}")                                   # Pages/routes
Grep(pattern="export default|export const", glob="*.tsx")                # Exported components
```

### Shell/CLI
```
Glob("bin/*")                                                            # Executable scripts
Grep(pattern="^[a-z_-]+:", path="Makefile")                              # Make targets
Grep(pattern="function\\s+\\w+|\\w+\\(\\)", glob="*.sh")                # Shell functions
Glob("*.nix")                                                            # Nix expressions
```

### Claude Code Plugins
```
Glob("**/skills/*/SKILL.md")                                             # Skills
Glob("**/agents/*.md")                                                   # Agents
Glob("**/commands/*.md")                                                 # Commands
Glob("**/hooks/hooks.json")                                              # Hooks
```

### Generic (any project)
```
Glob("**/*.proto")                                                       # Protobuf schemas
Glob("**/*.graphql|**/*.gql")                                            # GraphQL schemas
Glob("**/migrations/**/*")                                               # DB migrations
Glob("**/examples/**/*")                                                 # Examples
```
