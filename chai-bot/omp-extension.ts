/**
 * chai-bot OMP bridge.
 *
 * Translates OMP's `pi.on(...)` event shapes into the Claude-Code-shaped
 * stdin JSON chai-bot's existing hooks/session-start.sh and hooks/metrics.py
 * already expect, following dev-guard's shell-out pattern
 * (dev-guard/omp-extension.ts) rather than github-mcp's inline-static-string
 * pattern -- chai-bot's hooks have real logic (a repo-gate + curl probe, and
 * SQLite metrics writes), so this bridge shells out to the unchanged scripts
 * instead of reimplementing their behavior in TypeScript.
 *
 * See dev-guard/OMP-COMPAT.md for the confirmed OMP behaviors this bridge
 * relies on (stdin via Bun.spawn, MCP tool-name re-encoding, event mapping).
 * metrics.py always exits 0, but on PreToolUse it can emit a hookSpecificOutput
 * `permissionDecision: "deny"` (when CHAI_BOT_BASE_URL is not https/loopback-safe,
 * to keep the Bearer CHAI_TOKEN off cleartext transport) -- the tool_call handler
 * below translates that into an OMP block, mirroring dev-guard's
 * deny -> { block: true } handling (and failing closed if metrics.py can't run).
 */
import type {
	ExtensionAPI,
	ExtensionContext,
	ToolCallEvent,
	ToolCallEventResult,
	ToolResultEvent,
	ToolResultEventResult,
} from "@oh-my-pi/pi-coding-agent";

// ─────────────────────────────────────────────────────────────────────────
// Plugin root resolution -- OMP has no CLAUDE_PLUGIN_ROOT equivalent
// (confirmed absent, dev-guard/OMP-COMPAT.md's "CLAUDE_PLUGIN_ROOT"
// section). session-start.sh and metrics.py both read it directly (the
// former via inject-reference.sh's pattern, the latter for consistency with
// dev-guard's convention) and would otherwise fail closed/no-op invisibly.
// This file lives at the plugin root next to package.json, so its own
// directory IS the plugin root.
// ─────────────────────────────────────────────────────────────────────────
const PLUGIN_ROOT = new URL(".", import.meta.url).pathname.replace(/\/$/, "");
const HOOKS_DIR = `${PLUGIN_ROOT}/hooks`;

// ─────────────────────────────────────────────────────────────────────────
// MCP server re-encoding
//
// OMP's live MCP tool-name convention (confirmed against 6 other servers in
// dev-guard/OMP-COMPAT.md) is `mcp__<sanitized_server>_<tool>` -- single
// underscore before the tool name, "plugin_" prefix dropped, hyphens
// replaced with underscores. chai-bot's server key is "ship-help" registered
// as plugin "chai-bot", so applying that same mechanical transform to
// "plugin_chai-bot_ship-help" gives "chai_bot_ship_help". The Claude-Code
// target form below IS repo-verified (hooks.json's mcp__plugin_chai-bot_.*
// matcher + metrics.py parsing); only OMP's live emission of the input form
// is unconfirmed. The one documented deviation from the mechanical rule --
// context7's dropped trailing digit -- provably cannot apply here (neither
// "chai-bot" nor "ship-help" contains a digit). STILL NOT LIVE-VERIFIED
// against a real OMP install (chai-bot is a new server, not one of the 6
// dev-guard's spike covered) -- if wrong, the effect is degraded convenience
// only: this bridge's dispatch for chai-bot's MCP tools simply never fires
// under OMP, falling through to no metrics/no advisory under that harness
// specifically (Claude Code is unaffected). This mirrors the same accepted
// risk dev-guard's own bridge documents for its own unverified servers
// (playwright, jira, metadata-service).
// ─────────────────────────────────────────────────────────────────────────
const CHAI_BOT_OMP_SERVER = "chai_bot_ship_help";
const CHAI_BOT_CLAUDE_CODE_SERVER = "plugin_chai-bot_ship-help";

function isMcpTool(toolName: string): boolean {
	return toolName.startsWith("mcp__");
}

/** True if this OMP tool name belongs to chai-bot's MCP server (best-effort, see comment above). */
function isChaiBotMcpTool(ompToolName: string): boolean {
	if (!isMcpTool(ompToolName)) return false;
	const rest = ompToolName.slice("mcp__".length);
	return rest === CHAI_BOT_OMP_SERVER || rest.startsWith(`${CHAI_BOT_OMP_SERVER}_`);
}

/** Re-encode an OMP chai-bot MCP tool name into the mcp__<server>__<tool> shape metrics.py's tool_name parsing expects. */
function reencodeMcpToolName(ompToolName: string): string {
	const rest = ompToolName.slice("mcp__".length);
	const tool = rest.slice(CHAI_BOT_OMP_SERVER.length + 1);
	return `mcp__${CHAI_BOT_CLAUDE_CODE_SERVER}__${tool}`;
}

// ─────────────────────────────────────────────────────────────────────────
// Shared subprocess helper -- Bun.spawn(), not pi.exec()
//
// pi.exec()'s ExecOptions has no stdin field at all (dev-guard/OMP-COMPAT.md,
// "Subprocess execution"); Bun.spawn() is a Bun global reachable directly
// since OMP extensions run in-process in the same Bun runtime, and correctly
// pipes stdin. Every call injects CLAUDE_PLUGIN_ROOT.
// ─────────────────────────────────────────────────────────────────────────
interface RunScriptResult {
	code: number;
	stdout: string;
	stderr: string;
	timedOut: boolean;
	spawnFailed: boolean;
}

async function runScript(
	command: string,
	args: string[],
	opts: { stdin?: string; cwd: string; timeoutMs?: number } = { cwd: PLUGIN_ROOT },
): Promise<RunScriptResult> {
	let proc: ReturnType<typeof Bun.spawn>;
	try {
		proc = Bun.spawn([command, ...args], {
			stdin: "pipe",
			stdout: "pipe",
			stderr: "pipe",
			cwd: opts.cwd,
			env: { ...process.env, CLAUDE_PLUGIN_ROOT: PLUGIN_ROOT },
		});
	} catch {
		return { code: -1, stdout: "", stderr: "", timedOut: false, spawnFailed: true };
	}

	if (opts.stdin !== undefined) {
		try {
			await proc.stdin.write(opts.stdin);
		} catch {
			proc.kill();
			return { code: -1, stdout: "", stderr: "", timedOut: false, spawnFailed: true };
		}
	}
	proc.stdin.end();

	let timedOut = false;
	const timer = setTimeout(() => {
		timedOut = true;
		proc.kill();
	}, opts.timeoutMs ?? 10_000);

	try {
		const [stdout, stderr, code] = await Promise.all([
			new Response(proc.stdout).text(),
			new Response(proc.stderr).text(),
			proc.exited,
		]);
		return { code, stdout, stderr, timedOut, spawnFailed: false };
	} catch {
		return { code: -1, stdout: "", stderr: "", timedOut, spawnFailed: true };
	} finally {
		clearTimeout(timer);
	}
}

function runMetrics(stdinPayload: unknown, cwd: string): Promise<RunScriptResult> {
	return runScript("uv", ["run", `${HOOKS_DIR}/metrics.py`], {
		stdin: JSON.stringify(stdinPayload),
		cwd,
	});
}

/** Parse metrics.py's hookSpecificOutput JSON from stdout, if present. */
function parseHookOutput(
	stdout: string,
): { permissionDecision?: string; permissionDecisionReason?: string; additionalContext?: string } | undefined {
	const trimmed = stdout.trim();
	if (!trimmed) return undefined;
	try {
		const parsed = JSON.parse(trimmed);
		return parsed?.hookSpecificOutput;
	} catch {
		return undefined;
	}
}

function getSessionId(ctx: ExtensionContext): string {
	const mgr = ctx.sessionManager as unknown as { getSessionId?: () => string | undefined };
	return mgr.getSessionId?.() ?? "";
}

export default function (pi: ExtensionAPI) {
	// ─── Session lifecycle ──────────────────────────────────────────────
	pi.on("session_start", async (_event: unknown, ctx: ExtensionContext) => {
		const result = await runScript(`${HOOKS_DIR}/session-start.sh`, [], { cwd: ctx.cwd });
		if (result.stdout.trim()) {
			// deliverAs: "nextTurn" is the closest documented analog to Claude
			// Code's SessionStart hook (injects once, persists in transcript
			// for the rest of the session) -- same choice dev-guard's and
			// github-mcp's bridges make for their own SessionStart content.
			pi.sendMessage(result.stdout, { deliverAs: "nextTurn" });
		}
	});

	// ─── tool_call dispatch (PreToolUse) ────────────────────────────────
	pi.on(
		"tool_call",
		async (event: ToolCallEvent, ctx: ExtensionContext): Promise<ToolCallEventResult | void> => {
			if (!isChaiBotMcpTool(event.toolName)) return;

			const sessionId = getSessionId(ctx);
			const claudeCodeToolName = reencodeMcpToolName(event.toolName);

			const result = await runMetrics(
				{
					session_id: sessionId,
					tool_use_id: event.toolCallId,
					hook_event_name: "PreToolUse",
					tool_name: claudeCodeToolName,
					tool_input: event.input,
				},
				ctx.cwd,
			);

			// Fail CLOSED if metrics.py can't run: we then cannot confirm
			// CHAI_BOT_BASE_URL is https/loopback-safe, so block the chai-bot tool
			// rather than risk a cleartext CHAI_TOKEN leak. Mirrors dev-guard's
			// fail-closed policy for its security matchers (this call is always a
			// chai-bot MCP tool -- isChaiBotMcpTool gated it above).
			if (result.spawnFailed || result.timedOut) {
				return {
					block: true,
					reason:
						"chai-bot bridge subprocess failed -- failing closed (cannot confirm CHAI_BOT_BASE_URL is https://).",
				};
			}
			const output = parseHookOutput(result.stdout);

			// metrics.py exits 0 always; its one block signal is a PreToolUse
			// "deny", emitted when CHAI_BOT_BASE_URL is not https/loopback-safe
			// (the Bearer CHAI_TOKEN would otherwise go cleartext). Honor it under
			// OMP by blocking the tool call -- mirrors dev-guard/omp-extension.ts's
			// deny -> { block: true } translation.
			if (output?.permissionDecision === "deny") {
				return {
					block: true,
					reason:
						output.permissionDecisionReason ??
						"chai-bot: blocked (CHAI_BOT_BASE_URL is not https://).",
				};
			}

			// Otherwise the only action is forwarding the ask_persona advisory
			// reminder, if present, into the model's context. Claude Code delivers
			// this via hookSpecificOutput.additionalContext bundled with the tool's
			// own PreToolUse response; OMP's ToolCallEventResult has no equivalent
			// per-call context field (dev-guard/OMP-COMPAT.md documents this gap for
			// other advisory content too), so pi.sendMessage(..., {deliverAs:
			// "nextTurn"}) is the closest available mechanism. Best-effort
			// translation, not live-verified under OMP.
			if (output?.permissionDecision === "allow" && output.additionalContext) {
				pi.sendMessage(output.additionalContext, { deliverAs: "nextTurn" });
			}
		},
	);

	// ─── tool_result dispatch (PostToolUse) ─────────────────────────────
	pi.on(
		"tool_result",
		async (event: ToolResultEvent, ctx: ExtensionContext): Promise<ToolResultEventResult | void> => {
			if (!isChaiBotMcpTool(event.toolName)) return;

			const sessionId = getSessionId(ctx);
			const claudeCodeToolName = reencodeMcpToolName(event.toolName);

			await runMetrics(
				{
					session_id: sessionId,
					tool_use_id: event.toolCallId,
					hook_event_name: "PostToolUse",
					tool_name: claudeCodeToolName,
					tool_input: event.input,
					tool_response: toolResponseForMetrics(event),
				},
				ctx.cwd,
			);
			// metrics.py never signals anything back to the caller on
			// PostToolUse (pure side-effect logging) -- nothing to forward.
		},
	);

	/**
	 * Build a Claude-Code-shaped tool_response for metrics.py's
	 * _extract_response_size(): an MCP CallToolResult-shaped
	 * {"content": [{"type": "text", "text": ...}]}, mirroring
	 * dev-guard/omp-extension.ts's bashToolResponseForGuard/
	 * readToolResponseForGuard pattern for translating OMP's
	 * `event.content` text blocks into the shape the Python script expects.
	 */
	function toolResponseForMetrics(event: ToolResultEvent): Record<string, unknown> {
		const textBlocks = event.content
			.filter(
				(c: { type: string; text?: string }): c is { type: "text"; text: string } =>
					c.type === "text",
			)
			.map((c: { type: string; text?: string }) => ({ type: "text", text: c.text }));
		return { content: textBlocks, isError: event.isError ?? false };
	}
}
