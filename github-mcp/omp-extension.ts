/**
 * github-mcp OMP bridge.
 *
 * The Claude Code hook's entire "logic" is a hardcoded string with no
 * backing script (hooks.json's SessionStart command is a literal `echo`).
 * Shelling out to `echo` for a fixed value would be a pointless subprocess,
 * so this injects the identical content directly.
 *
 * Uses pi.sendMessage(), not ctx.ui.notify(): notify() is a UI-facing,
 * transient notification ("Show a notification to the user" —
 * ExtensionUIContext.notify in OMP's shipped types), not something injected
 * into the model's own context — the model would never see it. sendMessage
 * is the mechanism that actually delivers content into the conversation —
 * live-confirmed end-to-end: the model quoted this exact string back
 * verbatim from its own context on session start.
 *
 * Kept byte-for-byte identical to the string in github-mcp/hooks/hooks.json
 * per the plan's instruction, even though the specific tool name it
 * references (mcp__github__*) is Claude-Code-shaped and won't exactly match
 * OMP's own re-encoded MCP tool names for this server (see dev-guard's
 * omp-extension.ts for the confirmed OMP naming convention) — this is
 * informational guidance text for the model, not a guard decision, so the
 * minor naming mismatch doesn't affect correctness of any enforcement.
 */
import type { ExtensionAPI } from "@oh-my-pi/pi-coding-agent";

const GITHUB_MCP_HINT =
	"You have access to a read-only GitHub MCP server (mcp__github__*). Use MCP tools for reading GitHub data (PRs, issues, actions, code security, notifications, discussions). Use the gh CLI for write operations (creating PRs, merging, creating issues). For raw.githubusercontent.com URLs, use the mcp__github__get_file_contents tool instead of WebFetch.";

export default function (pi: ExtensionAPI) {
	pi.on("session_start", async () => {
		// sendMessage's payload type is `string | Partial<CustomMessage>`
		// (dist/types/session/messages.d.ts) — pass a plain string, not a
		// `{type, text}` object.
		pi.sendMessage(GITHUB_MCP_HINT, { deliverAs: "nextTurn" });
	});
}
