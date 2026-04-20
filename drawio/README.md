# drawio

Vendored [draw.io](https://github.com/jgraph/drawio-mcp) diagram generation
skill for Claude Code. Creates native `.drawio` files with optional export to
PNG, SVG, or PDF via the draw.io Desktop CLI.

## Prerequisites

- **draw.io Desktop** — required for editing diagrams and CLI export
  - macOS: `brew install --cask drawio`
  - Linux: available via snap/apt/flatpak
  - Windows: installer from [draw.io releases](https://github.com/jgraph/drawio-desktop/releases)
- **@drawio/postprocess** (optional) — `npx @drawio/postprocess` for edge
  routing optimization. Skipped silently if unavailable.

## Usage

Invoke the skill with `/drawio` followed by a diagram description:

- `/drawio flowchart for user login` → `user-login.drawio`
- `/drawio png architecture overview` → `architecture-overview.drawio.png`
- `/drawio svg ER diagram for users` → `users-er.drawio.svg`

## Supported Formats

| Format | Embed XML | Notes |
|--------|-----------|-------|
| `.drawio` | N/A | Native — always generated |
| `.drawio.png` | Yes | Viewable everywhere, editable in draw.io |
| `.drawio.svg` | Yes | Scalable, editable in draw.io |
| `.drawio.pdf` | Yes | Printable, editable in draw.io |

## Upstream Tracking

The vendored SKILL.md is synced weekly from
[jgraph/drawio-mcp](https://github.com/jgraph/drawio-mcp) via the
`sync-drawio-skill` GitHub Actions workflow. Changes arrive as PRs.

The skill also fetches
[`shared/xml-reference.md`](https://github.com/jgraph/drawio-mcp/blob/main/shared/xml-reference.md)
from upstream at runtime via GitHub raw URL. This reference is intentionally
NOT vendored — it stays fresh automatically without sync PRs.

See [UPSTREAM.md](UPSTREAM.md) for the current vendored commit SHA.

## License

The vendored skill content is licensed under Apache 2.0 by JGraph Ltd.
See [LICENSE](LICENSE).
