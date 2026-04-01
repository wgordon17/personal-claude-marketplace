# Dependency Evaluation Criteria

Use this checklist before implementing custom code. If a well-maintained library exists that
solves the problem, prefer it over custom implementation unless the library introduces more
complexity than it removes.

## When to Search

Search for existing libraries when:
- Building functionality that is not core product differentiation
- Implementing well-known algorithms, protocols, or patterns
- The implementation would exceed ~100 lines of non-trivial logic
- The domain has known edge cases that mature libraries handle (crypto, date/time, parsing)

## Evaluation Criteria

**CRITICAL: Use the actual current date for all recency checks.** The current date is available
from system context. Do NOT rely on training data for date comparisons -- training data creates
a false sense of recency. A library last updated in 2024 is NOT "recent" if today is 2026.
Always compare against today's actual date, not your training cutoff.

### Must-Have (all required)

| Criterion | Threshold | How to Check |
|---|---|---|
| **Recent commits** | Within last 6 months from TODAY | GitHub API / repo page, compare commit dates against current date |
| **Recent release** | Within last 12 months from TODAY | GitHub releases / package registry, compare against current date |
| **Compatible license** | MIT, Apache 2.0, BSD, ISC, or project-compatible | Package metadata, LICENSE file |
| **No critical CVEs** | Zero unpatched critical/high CVEs | `npm audit` / `pip audit` / GitHub security advisories |

### Should-Have (strong preference, not absolute)

| Criterion | Threshold | How to Check |
|---|---|---|
| **Popularity** | >500 GitHub stars OR >10K weekly downloads | GitHub stars, npm/PyPI download stats |
| **Maintenance signals** | Issues responded to within 30 days, PRs reviewed | GitHub Issues/PRs activity |
| **Documentation** | README with usage examples, API reference exists | Repo inspection |
| **Test coverage** | CI passing, test suite exists | GitHub Actions / CI badges |
| **Minimal dependencies** | Fewer transitive deps is better | Dependency tree inspection |
| **Security policy** | SECURITY.md or disclosure process exists | Repo inspection, GitHub community health score |
| **Release integrity** | Checksums, signatures, or CI-based releases | GitHub releases assets, registry attestations |

### Red Flags (prefer custom code if present)

- Single maintainer with no recent activity
- **High contributor concentration:** Top contributor >80% of commits AND no other contributor >10%
- Last release >2 years ago (abandoned)
- >100 open issues with no maintainer response
- Heavy transitive dependency tree (>20 deps for a focused library)
- Requires significant wrapper code to integrate (negates the benefit)
- Actively hostile to downstream users (frequent breaking changes without migration path)
- Manual releases from dev machines (no CI pipeline between maintainer and registry)
- Executes shell commands or arbitrary code with no sandboxing or allowlist boundary

## Supply Chain Assessment

For CLI tools, build tools, and dependencies that execute code or commands, evaluate these
additional dimensions beyond basic quality metrics.

### Maintainer Provenance

Investigate the primary maintainer(s) using publicly available information:

| Signal | Where to Check |
|---|---|
| Identity and real name | GitHub profile, package registry author field |
| Location / country of residence | GitHub profile, LinkedIn, personal site |
| Employment / affiliation | LinkedIn, GitHub org memberships, personal site |
| Commit history consistency | `git log --format='%an <%ae>'` for email domain patterns |
| Other projects | GitHub profile pinned repos — consistent technical focus vs one-off |

**When provenance matters:** Security-sensitive environments, government/defense contexts,
projects subject to export control or organizational software supply chain policies.
This is NOT about excluding contributors by nationality — it is about understanding the
trust chain when granting a tool access to your codebase and machines.

### Bus Factor

Go beyond the binary "single maintainer" check:

```
1. Count total commits and commits by top contributor
2. Calculate concentration: top_contributor_commits / total_commits
3. Check the gap: does contributor #2 have >10% of commits?
4. State explicitly: "Top contributor: [name] at [N]% ([X]/[Y] commits).
   Next: [name] at [M]%. Bus factor: [1 or 2+]."
```

A project with 33 contributors can still have a bus factor of 1 if the top contributor
holds 74% and the rest have 1-5 commits each.

### Release Integrity

| Signal | Trust Level | How to Check |
|---|---|---|
| CI-based releases (GitHub Actions → registry) | Higher | Check release workflow in `.github/workflows/` |
| Manual releases (dev machine → registry) | Lower | No release workflow, or `twine upload` / `npm publish` from local |
| Checksums published | Baseline | `checksums.txt` in GitHub release assets |
| Signatures (.sig, .asc, .pem) | Good | GitHub release assets |
| Trusted Publisher / attestations | Best | PyPI Trusted Publishers, npm provenance, sigstore |
| SBOM included | Bonus | Release assets or dedicated SBOM file |

**Distribution channel hierarchy** (higher = more trust):
1. Vendored/pinned source (you audit the code)
2. Curated package manager (Homebrew core, Debian main — third-party review)
3. CI-published registry package (GitHub Actions → npm/PyPI — auditable pipeline)
4. Manually-published registry package (dev machine → registry — single point of compromise)

### Code Execution Model

For tools that run commands or process input:

| Question | Why It Matters |
|---|---|
| Does it execute shell commands? | `shell=True` / `exec.Command("bash", "-c", ...)` = arbitrary execution |
| Does it accept arbitrary user input as commands? | Unbounded input → unbounded risk |
| Does it have a task/allowlist model? | Named tasks in config = auditable, bounded surface |
| Does it make network calls or phone home? | Telemetry, update checks = data exfiltration path |
| Does it read/write outside its working directory? | Global config files (`~/.config/`) = dotfile poisoning vector |

### AI Agent Compatibility

When the dependency will be invoked by an AI agent (not just a human):

| Question | What You Want |
|---|---|
| Can operations be bounded to a predefined set? | Named tasks, subcommands — not arbitrary input |
| Can dangerous subcommands be selectively blocked? | `tool exec` blocked, `tool run <task>` allowed |
| Is the operation set auditable from config? | YAML/JSON task definitions, not runtime-constructed commands |
| Does it amplify blast radius? | Single command affecting N repos/services = multiplier risk |

**Example:** A multi-repo tool with `run <named-task>` (bounded) vs `exec <arbitrary-cmd>`
(unbounded). Block the latter via command-guard rules; allow the former.

## Decision Framework

```
1. Does a library exist?
   NO  → Build custom (document why in commit message)
   YES → Continue

2. Does it pass all Must-Have criteria?
   NO  → Build custom (document which criterion failed)
   YES → Continue

3. Does integrating it add more complexity than building custom?
   YES → Build custom (document the complexity trade-off)
   NO  → Continue

4. Does it pass Supply Chain Assessment? (for tools/CLIs that execute code)
   FAIL → Build custom or mitigate (pin version, install from source, block subcommands)
   PASS → Use the library

5. Document the decision in the architect plan or commit message:
   "Using [library] because [reason]. Evaluated [N] alternatives."
   OR
   "Building custom because [reason]. No library met criteria: [specifics]."
```

## Date Verification Protocol

When evaluating a library's recency, follow this exact sequence:

1. Determine today's date from system context (e.g., `date +%Y-%m-%d`)
2. Find the library's most recent commit date and most recent release date
3. Calculate the actual difference in months/days
4. Compare against thresholds: commits within 6 months, releases within 12 months
5. State the comparison explicitly: "Last commit: [date]. Today: [date]. Gap: [N] months.
   Threshold: 6 months. Result: [PASS/FAIL]."

Do NOT say "recently updated" or "actively maintained" without performing this calculation.
