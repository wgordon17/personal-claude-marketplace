/**
 * dev-guard OMP bridge.
 *
 * Translates OMP's `pi.on(...)` event shapes into the Claude-Code-shaped
 * stdin JSON the existing Python/shell scripts in hooks/ already expect, and
 * translates their stdout-JSON/exit-code responses back into OMP's return
 * shapes. Zero reimplementation of guard logic in TypeScript — every
 * decision is made by shelling out to the unchanged scripts.
 *
 * Ground truth for every design decision below comes from a live OMP v17.4.2
 * verification spike: hack/research/omp-spike-findings.md (gitignored,
 * local-only — read it for the full evidence trail behind each comment
 * here).
 */
import type {
	ExtensionAPI,
	ExtensionAskDialogQuestion,
	ExtensionAskDialogResultItem,
	ExtensionContext,
	ToolCallEvent,
	ToolCallEventResult,
	ToolResultEvent,
	ToolResultEventResult,
} from "@oh-my-pi/pi-coding-agent";

// ─────────────────────────────────────────────────────────────────────────
// Plugin root resolution
//
// OMP has no CLAUDE_PLUGIN_ROOT equivalent (confirmed null, no `ctx` field
// either — Task 1 spike Step 6.5). inject-reference.sh, stop-hook.py, and
// subagent-stop-hook.py all read it directly and silently no-op (not error)
// if unset — a total, invisible failure if this bridge didn't inject it.
// This file lives at the plugin root next to package.json (same placement
// rationale as the plan's File Design Notes), so its own directory IS the
// plugin root.
// ─────────────────────────────────────────────────────────────────────────
const PLUGIN_ROOT = new URL(".", import.meta.url).pathname.replace(/\/$/, "");
const HOOKS_DIR = `${PLUGIN_ROOT}/hooks`;

// ─────────────────────────────────────────────────────────────────────────
// Fail-open / fail-closed matcher classification — explicit typed constants,
// not inferred from tool-call content (security constraint: an implicit
// classification risks misclassifying a new matcher and silently changing
// security posture). This governs ONLY the narrow window where the bridge's
// own guard subprocess call itself fails or times out (spawnFailed /
// timedOut) — the `result.code === 2` hard-block check always runs first,
// before FailPolicy is ever consulted, so normal-operation blocking is
// unaffected either way.
//
// Fail closed: Bash and MCP — these dispatch to guard functions that can
// hard-block (GIT_DENY_RULES, the fetchaller mutating-call gate).
//
// Fail open: Write, Edit, Read, WebSearch — NOT because these only reach
// advisory-only checks. Write/Edit/NotebookEdit both have live hard-block
// paths of their own (_guard_tmp_path, _guard_comment_narration), and
// WebFetch/WebSearch/read-as-URL route through _check_url_rules, whose
// BLOCKED_URL_RULES entries default to a "block" action (~17 of 26; the
// rest override to "ask") — a real hard block is reachable here during
// normal operation too. Only a plain Read of a file path is genuinely
// advisory-only (its one guard, _guard_claire_typo, only ever corrects the
// path or allows, never blocks). Fail-open is accepted specifically for the
// subprocess-failure/timeout window on these four matchers, not a claim
// about what they do the rest of the time.
// ─────────────────────────────────────────────────────────────────────────
type FailPolicy = "fail-closed" | "fail-open";

const FAIL_CLOSED_TOOL_NAMES: ReadonlySet<string> = new Set(["bash"]);
const FAIL_OPEN_TOOL_NAMES: ReadonlySet<string> = new Set(["write", "edit", "read", "web_search"]);
// No OMP tool-call event exists for a NotebookEdit or EnterPlanMode
// equivalent: OMP's plan mode is a slash-command modality (docs/extensions.md's
// plan-mode.ts example), not a gated tool call, and no separate
// notebook-editing entry appears in the live ToolCallEvent union (Task 1
// spike). These two Claude Code matchers have no OMP dispatch path at all —
// a documented gap, not a silent misclassification. See OMP-COMPAT.md.

export function isMcpTool(toolName: string): boolean {
	return toolName.startsWith("mcp__");
}

export function getFailPolicy(toolName: string): FailPolicy | undefined {
	if (FAIL_CLOSED_TOOL_NAMES.has(toolName) || isMcpTool(toolName)) return "fail-closed";
	if (FAIL_OPEN_TOOL_NAMES.has(toolName)) return "fail-open";
	return undefined;
}

// ─────────────────────────────────────────────────────────────────────────
// MCP server re-encoding
//
// tool-selection-guard.py's mcp_key() (mcp_constants.py) expects the
// Claude-Code-shaped `mcp__<server>__<tool>` identity (double underscore
// both places). OMP's live convention is `mcp__<sanitized_server>_<tool>`
// (single underscore before the tool name, "plugin_" prefix dropped) — but
// this is NOT a pure mechanical formula: live-verified against a real OMP
// install, `context7` sanitizes to `context` (the trailing digit is
// dropped), not `context7`. A bridge that assumes a fixed hyphen->underscore
// transform gets this one wrong, and OMP's own tool metadata
// (`pi.getAllTools()`'s `sourceInfo.path`) only echoes the already-sanitized
// OMP name back — it does not expose the original un-sanitized identity — so
// there is no way to recover the exact original from OMP's own runtime
// introspection. Instead: hard-code the OMP-sanitized form for every server
// dev-guard's own MCP_READ_ONLY allowlist (mcp_constants.py) actually cares
// about, since those are the only servers whose exact key matters — unknown
// servers already pass through to settings.json in
// tool-selection-guard.py's _handle_mcp_tool() regardless of key accuracy.
//
// Live-verified against a real OMP v17.4.2 install (Task 1 spike):
//   github, claude-mem, serena, sequential-thinking, fetchaller, context7
// NOT live-verified — derived by applying the same "plugin_ prefix stripped,
// hyphens -> underscores" pattern the verified servers all followed. If
// wrong, the effect is that this dev-guard installation's own allowlist
// entries for the affected server won't auto-approve under OMP (falls
// through to settings.json passthrough) — degraded convenience, not a
// security hole, since it's the strictly narrower outcome vs. mis-approving
// something. Re-verify against a live install before relying on these:
//   playwright, plugin_jira_mcp-atlassian-prod, metadata-service
// ─────────────────────────────────────────────────────────────────────────
export const OMP_TO_CLAUDE_CODE_MCP_SERVER: Readonly<Record<string, string>> = {
	github_mcp_github: "plugin_github-mcp_github",
	claude_mem_mcp_search: "plugin_claude-mem_mcp-search",
	serena: "serena",
	sequential_thinking: "sequential-thinking",
	fetchaller_mcp_fetchaller: "plugin_fetchaller-mcp_fetchaller",
	context: "context7", // NOT a formula match — OMP drops the trailing digit.
	// Best-effort, unverified against a live install — see comment above.
	playwright: "playwright",
	jira_mcp_atlassian_prod: "plugin_jira_mcp-atlassian-prod",
	metadata_service: "metadata-service",
};

const KNOWN_OMP_MCP_SERVER_PREFIXES = Object.keys(OMP_TO_CLAUDE_CODE_MCP_SERVER).sort(
	(a, b) => b.length - a.length,
);

// OMP's tool-name sanitization (hyphens -> underscores, same as server
// names) is lossy in the same way: "resolve_library_id" could originally
// have been "resolve-library-id" or always underscored — there is no way to
// tell from the OMP side alone. Scanning mcp_constants.py's full
// MCP_READ_ONLY allowlist, every server's tool names are already
// underscore-native (Python-style) or camelCase (Jira) EXCEPT context7,
// whose tool names use npm-idiomatic hyphens (`resolve-library-id`,
// `query-docs`) — matched here as a small per-server override table rather
// than mirroring all ~150 allowlist entries into TypeScript. A tool not
// listed here is passed through with its OMP underscore form unchanged
// (correct for every non-context7 server verified so far).
const OMP_TO_CLAUDE_CODE_MCP_TOOL: Readonly<Record<string, Readonly<Record<string, string>>>> = {
	context7: {
		resolve_library_id: "resolve-library-id",
		query_docs: "query-docs",
	},
};

/** Re-encode an OMP MCP tool name into the mcp__<server>__<tool> shape mcp_key() expects. */
export function reencodeMcpToolName(ompToolName: string): string {
	const rest = ompToolName.slice("mcp__".length);
	for (const ompServer of KNOWN_OMP_MCP_SERVER_PREFIXES) {
		if (rest === ompServer || rest.startsWith(`${ompServer}_`)) {
			const claudeCodeServer = OMP_TO_CLAUDE_CODE_MCP_SERVER[ompServer];
			const ompTool = rest.slice(ompServer.length + 1);
			const toolOverrides = OMP_TO_CLAUDE_CODE_MCP_TOOL[claudeCodeServer];
			const claudeCodeTool = toolOverrides?.[ompTool] ?? ompTool;
			return `mcp__${claudeCodeServer}__${claudeCodeTool}`;
		}
	}
	// Unknown server: best-effort single->double underscore at the first
	// boundary. May be wrong for multi-segment server names, but unknown
	// servers pass through to settings.json regardless of exact key.
	const firstUnderscore = rest.indexOf("_");
	if (firstUnderscore === -1) return ompToolName;
	return `mcp__${rest.slice(0, firstUnderscore)}__${rest.slice(firstUnderscore + 1)}`;
}

/**
 * Map an OMP tool name to the Claude Code tool name tool-selection-guard.py
 * branches on, or undefined if there's no guard-relevant equivalent.
 * mcp__ tools pass through their OMP name (re-encoded separately in
 * translateToolInputForGuard's caller path via reencodeMcpToolName).
 */
export function translateToolNameForGuard(ompToolName: string): string | undefined {
	if (isMcpTool(ompToolName)) return reencodeMcpToolName(ompToolName);
	switch (ompToolName) {
		case "bash":
			return "Bash";
		case "write":
			return "Write";
		case "edit":
			return "Edit";
		case "web_search":
			return "WebSearch";
		default:
			return undefined;
	}
}

/**
 * Translate OMP's tool_call input field names into the Claude-Code-shaped
 * fields tool-selection-guard.py reads. Field names confirmed in Task 1
 * spike, Step 4, EXCEPT web_search's fields (query/allowed_domains),
 * which were not live-verified and are a best-effort guess based on the
 * OMP CLI's own flag naming.
 */
export function translateToolInputForGuard(ompToolName: string, input: Record<string, unknown>): Record<string, unknown> {
	if (isMcpTool(ompToolName)) return input;
	switch (ompToolName) {
		case "bash":
			return { command: input.command };
		case "write":
		case "edit":
			return input; // edit's OMP input is untyped/hashline-based — pass through unchanged, no known field rename
		case "web_search":
			return { query: input.query, allowed_domains: input.allowed_domains, blocked_domains: input.blocked_domains };
		default:
			return input;
	}
}

/**
 * Classify OMP's single "read" tool as Claude Code's Read or WebFetch based
 * on input content, since OMP has no standalone URL-fetch tool (Task 1
 * spike, Step 4) — the Claude Code tool name depends on the input, not a
 * pure function of the OMP tool name alone, so this can't go through
 * translateToolNameForGuard above. Shared by both the tool_call and
 * tool_result dispatchers below (previously duplicated inline in each).
 */
export function classifyReadTool(input: Record<string, unknown>): { toolName: "Read" | "WebFetch"; toolInput: Record<string, unknown> } {
	const path = String(input.path ?? "");
	const isUrl = /^https?:\/\//i.test(path);
	return isUrl ? { toolName: "WebFetch", toolInput: { url: path } } : { toolName: "Read", toolInput: { file_path: path } };
}

/** Best-effort Bash tool_response shape for _extract_response_text's stdout/stderr concat. */
export function bashToolResponseForGuard(event: ToolResultEvent): Record<string, unknown> {
	const text = event.content
		.filter((c): c is { type: "text"; text: string } => c.type === "text")
		.map((c) => c.text)
		.join("\n");
	return { stdout: text, stderr: "" };
}

/** Best-effort Read tool_response shape for _extract_response_text's content field. */
export function readToolResponseForGuard(event: ToolResultEvent): Record<string, unknown> {
	const text = event.content
		.filter((c): c is { type: "text"; text: string } => c.type === "text")
		.map((c) => c.text)
		.join("\n");
	return { content: text };
}

// ─────────────────────────────────────────────────────────────────────────
// Shared subprocess helper
//
// Uses Bun.spawn() directly, NOT pi.exec(). Confirmed empirically (Task 1
// spike, Step 2): pi.exec()'s ExecOptions has no stdin field at all — both a
// `stdin` and an `input` option name silently no-op (child sees immediate
// EOF, not the payload). Extensions run in-process in the same Bun runtime
// (docs/extensions.md: "Extensions run in-process with no isolation"), so
// Bun.spawn is reachable as a Bun global and correctly pipes stdin — verified
// live with a marker round-tripped through `cat`.
//
// Every call injects CLAUDE_PLUGIN_ROOT (see above) and sets the child's cwd
// explicitly, since tool-selection-guard.py's git-safety checks operate on
// the process's actual working directory, not a JSON field.
// ─────────────────────────────────────────────────────────────────────────
interface RunScriptResult {
	code: number;
	stdout: string;
	stderr: string;
	timedOut: boolean;
	/** True when the subprocess itself could not be spawned/run at all (distinct from a normal non-zero exit). */
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

function runGuard(
	extraArgs: string[],
	stdinPayload: unknown,
	cwd: string,
	timeoutMs?: number,
): Promise<RunScriptResult> {
	return runScript("uv", ["run", `${HOOKS_DIR}/tool-selection-guard.py`, ...extraArgs], {
		stdin: JSON.stringify(stdinPayload),
		cwd,
		timeoutMs,
	});
}

function runDecisionPersistence(stdinPayload: unknown, cwd: string): Promise<RunScriptResult> {
	return runScript("uv", ["run", `${HOOKS_DIR}/decision-persistence.py`], {
		stdin: JSON.stringify(stdinPayload),
		cwd,
	});
}

/** Parse tool-selection-guard.py's hookSpecificOutput JSON from stdout, if present. */
export function parseHookOutput(stdout: string): { permissionDecision?: string; permissionDecisionReason?: string; updatedInput?: Record<string, unknown>; additionalContext?: string } | undefined {
	const trimmed = stdout.trim();
	if (!trimmed) return undefined;
	try {
		const parsed = JSON.parse(trimmed);
		return parsed?.hookSpecificOutput;
	} catch {
		return undefined;
	}
}

/**
 * Parse stop-hook.py / subagent-stop-hook.py's top-level
 * `{"decision": "block", "reason": "..."}` JSON from stdout, if present.
 *
 * Distinct shape from parseHookOutput() above: those two scripts' block
 * signal is NOT nested under a `hookSpecificOutput` key (that convention
 * belongs to tool-selection-guard.py's PreToolUse/PostToolUse output).
 */
export function parseStopDecision(stdout: string): { decision?: string; reason?: string } | undefined {
	const trimmed = stdout.trim();
	if (!trimmed) return undefined;
	try {
		return JSON.parse(trimmed);
	} catch {
		return undefined;
	}
}

/** Find the most recent assistant message's text content, walking backward through `messages` (string or content-block-array shapes); "" if none. */
export function lastAssistantText(messages: { role?: string; content?: unknown }[]): string {
	for (let i = messages.length - 1; i >= 0; i--) {
		const msg = messages[i] as { role?: string; content?: unknown };
		if (msg.role !== "assistant") continue;
		const content = msg.content;
		if (typeof content === "string") return content;
		if (Array.isArray(content)) {
			return content
				.filter((c): c is { type: "text"; text: string } => c && c.type === "text")
				.map((c) => c.text)
				.join("\n");
		}
	}
	return "";
}

// ─────────────────────────────────────────────────────────────────────────
// Session identity — sourced ONLY from ctx, never from event.input
//
// Security constraint: event.input is LLM-controlled for custom tools
// (AskUserQuestion). _check_trust (tool-selection-guard.py) compares
// session_id against stored trust grants — a spoofed session_id would
// inherit another session's trust rules, bypassing ask-type guards.
// ─────────────────────────────────────────────────────────────────────────
function getSessionId(ctx: ExtensionContext): string {
	const mgr = ctx.sessionManager as unknown as { getSessionId?: () => string | undefined };
	return mgr.getSessionId?.() ?? "";
}

export default function (pi: ExtensionAPI) {
	const z = pi.zod;

	// ─── Session lifecycle ──────────────────────────────────────────────
	pi.on("session_start", async (_event, ctx) => {
		const sessionId = getSessionId(ctx);
		const validatePayload = { session_id: sessionId, cwd: ctx.cwd };
		const referenceFiles = ["shared-feedback.md", "token-efficiency.md"];

		// hooks.json's SessionStart matcher runs --validate and both
		// inject-reference.sh calls as independent, parallel-executing
		// hooks — mirrored here via Promise.all rather than sequential
		// awaits.
		const [validateResult, ...referenceResults] = await Promise.all([
			runGuard(["--validate"], validatePayload, ctx.cwd),
			...referenceFiles.map((referenceFile) =>
				runScript(`${HOOKS_DIR}/inject-reference.sh`, [referenceFile], { cwd: ctx.cwd }),
			),
		]);

		if (validateResult.code !== 0 || validateResult.spawnFailed) {
			// Persistent (not one-shot) warning — Task 1 spike confirmed
			// ctx.ui.setStatus(key, text) is the persistent-surface method
			// (interactive mode wires it to the footer/status bar; a log
			// line alone would be silent in the common case of no one
			// watching stderr).
			ctx.ui.setStatus(
				"dev-guard-validate",
				"dev-guard: guard config validation failed — see stderr for details",
			);
			ctx.ui.notify(
				`dev-guard --validate failed (exit ${validateResult.code}): ${validateResult.stderr.slice(0, 400)}`,
				"error",
			);
		}

		// sendMessage's payload type is `string | Partial<CustomMessage>`
		// (dist/types/session/messages.d.ts) — pass a plain string, not a
		// `{type, text}` object.
		//
		// `deliverAs: "nextTurn"` is chosen because the SDK docs describe
		// it as "stored and injected on the next user prompt" — the
		// closest documented analog to Claude Code's SessionStart hook,
		// which injects once, before the first assistant turn, and the
		// content then persists in the transcript for the rest of the
		// session. Live-confirmed end-to-end: the model correctly quotes
		// both shared-feedback.md's and token-efficiency.md's content back
		// from its own context. Results stay in `referenceFiles` order
		// (Promise.all preserves input order regardless of completion
		// order), so messages are still delivered shared-feedback-first.
		for (const result of referenceResults) {
			if (result.stdout.trim()) {
				pi.sendMessage(result.stdout, { deliverAs: "nextTurn" });
			}
		}
	});

	// ─── AskUserQuestion custom tool ────────────────────────────────────
	// Literal name "AskUserQuestion" confirmed permitted with no collision
	// against the built-in "ask" tool (Task 1 spike, Step 9) — so
	// decision-persistence.py's hardcoded tool_name == "AskUserQuestion"
	// string-equality check is satisfied without any name translation.
	const askQuestionSchema = z.object({
		questions: z.array(
			z.object({
				question: z.string(),
				header: z.string().optional(),
				options: z.array(
					z.object({
						label: z.string(),
						description: z.string().optional(),
					}),
				),
				multiSelect: z.boolean().optional(),
			}),
		),
	});

	pi.registerTool({
		name: "AskUserQuestion",
		label: "Ask User Question",
		description: "Ask the user one or more multiple-choice questions and wait for their answer.",
		approval: "read",
		parameters: askQuestionSchema,
		async execute(_toolCallId, params, signal, _onUpdate, ctx) {
			const questions = params.questions;

			// decision-persistence.py PreToolUse: check for a prior stored
			// decision before bothering the user at all.
			const sessionId = getSessionId(ctx);
			const preResult = await runDecisionPersistence(
				{
					session_id: sessionId,
					hook_event_name: "PreToolUse",
					tool_name: "AskUserQuestion",
					tool_input: { questions },
				},
				ctx.cwd,
			);
			const preOutput = parseHookOutput(preResult.stdout);
			let answers: Record<string, string> | undefined;
			if (preOutput?.permissionDecision === "allow" && preOutput.updatedInput?.answers) {
				answers = preOutput.updatedInput.answers as Record<string, string>;
			}

			if (!answers) {
				answers = await collectAnswers(ctx, questions, signal);
			}

			// decision-persistence.py PostToolUse: capture new decisions.
			await runDecisionPersistence(
				{
					session_id: sessionId,
					hook_event_name: "PostToolUse",
					tool_name: "AskUserQuestion",
					tool_input: { questions },
					tool_response: { answers },
				},
				ctx.cwd,
			);

			return {
				content: [{ type: "text", text: JSON.stringify(answers) }],
				details: { answers },
			};
		},
	});

	/**
	 * Races collectAnswersFromUi() against `signal`: neither askDialog() nor
	 * select() (OMP's shipped UI primitives, per the Task 1 spike) expose a
	 * cancellation parameter, so an aborted tool call resolves to an empty
	 * answer set here instead of leaving the tool call hung on a UI response
	 * that will never come.
	 */
	async function collectAnswers(
		ctx: ExtensionContext,
		questions: { question: string; header?: string; options: { label: string; description?: string }[]; multiSelect?: boolean }[],
		signal: AbortSignal,
	): Promise<Record<string, string>> {
		if (signal.aborted) return {};

		let onAbort: (() => void) | undefined;
		const aborted = new Promise<Record<string, string>>((resolve) => {
			onAbort = () => resolve({});
			signal.addEventListener("abort", onAbort, { once: true });
		});
		try {
			return await Promise.race([collectAnswersFromUi(ctx, questions), aborted]);
		} finally {
			// Removes the listener when collectAnswersFromUi() wins the race —
			// { once: true } only detaches it after it FIRES, not after the
			// race resolves the other way, so without this the listener lingers
			// on `signal` for the lifetime of the underlying AbortController.
			if (onAbort) signal.removeEventListener("abort", onAbort);
		}
	}

	/**
	 * Collect answers via ctx.ui.askDialog() when reachable, falling back to
	 * ctx.ui.select() per question when it isn't (print/RPC/subagent modes —
	 * confirmed live in Task 1 spike: askDialog is undefined outside TUI
	 * mode). The select() fallback is single-answer-only even for
	 * multiSelect questions — a documented degradation, not a bug: there is
	 * no clean multi-select equivalent among the always-available UI
	 * primitives.
	 *
	 * Matches by question TEXT, not array position (a two-question batch
	 * answered in reverse order must still map correctly).
	 */
	async function collectAnswersFromUi(
		ctx: ExtensionContext,
		questions: { question: string; header?: string; options: { label: string; description?: string }[]; multiSelect?: boolean }[],
	): Promise<Record<string, string>> {
		const answers: Record<string, string> = {};

		if (typeof ctx.ui.askDialog === "function") {
			const dialogQuestions: ExtensionAskDialogQuestion[] = questions.map((q, i) => ({
				id: `q${i}`,
				question: q.question,
				header: q.header,
				options: q.options,
				multi: q.multiSelect,
			}));
			const result = await ctx.ui.askDialog(dialogQuestions);
			if (result && result.kind === "submit") {
				for (const item of result.results as ExtensionAskDialogResultItem[]) {
					const matched = questions.find((q) => q.question === item.question);
					if (!matched) continue;
					answers[matched.question] = item.customInput || item.selectedOptions.join(", ");
				}
			}
			return answers;
		}

		for (const q of questions) {
			const label = await ctx.ui.select(
				q.question,
				q.options.map((o) => ({ label: o.label, description: o.description })),
			);
			if (label !== undefined) {
				answers[q.question] = label;
			}
		}
		return answers;
	}

	// ─── tool_call dispatch (PreToolUse) ────────────────────────────────
	pi.on("tool_call", async (event: ToolCallEvent, ctx): Promise<ToolCallEventResult | void> => {
		if (event.toolName === "AskUserQuestion") return; // handled by the custom tool itself

		const sessionId = getSessionId(ctx);

		// "read" doubles as Claude Code's Read AND WebFetch under OMP (no
		// standalone URL-fetch tool — Task 1 spike, Step 4) — the Claude
		// Code tool name itself depends on the input content (URL vs file
		// path), so it can't go through the generic name/input translation
		// functions below (which assume the name is a pure function of the
		// OMP tool name alone). Mirrors the same detection already used in
		// the tool_result handler for this tool.
		let claudeCodeToolName: string | undefined;
		let toolInput: Record<string, unknown>;
		if (event.toolName === "read") {
			const classified = classifyReadTool(event.input as Record<string, unknown>);
			claudeCodeToolName = classified.toolName;
			toolInput = classified.toolInput;
		} else {
			claudeCodeToolName = translateToolNameForGuard(event.toolName);
			toolInput = translateToolInputForGuard(event.toolName, event.input as Record<string, unknown>);
		}
		if (!claudeCodeToolName) return; // no guard-relevant equivalent for this tool

		const failPolicy = getFailPolicy(event.toolName);

		const result = await runGuard(
			[],
			{
				session_id: sessionId,
				tool_use_id: event.toolCallId,
				hook_event_name: "PreToolUse",
				tool_name: claudeCodeToolName,
				tool_input: toolInput,
			},
			ctx.cwd,
		);

		// Dual block-signal handling: exit code 2 is an independent hard
		// block, checked BEFORE stdout JSON — a bridge that only reads
		// stdout JSON silently turns every hard git-deny block into
		// passthrough allow (security constraint).
		if (result.code === 2) {
			return { block: true, reason: result.stderr.trim() || "Blocked by dev-guard." };
		}

		if (result.spawnFailed || result.timedOut) {
			if (failPolicy === "fail-closed") {
				return { block: true, reason: "dev-guard bridge subprocess failed — failing closed for this matcher." };
			}
			return; // fail-open: no opinion, let the call proceed
		}

		const output = parseHookOutput(result.stdout);
		if (output?.permissionDecision === "ask" || output?.permissionDecision === "deny") {
			return { block: true, reason: output.permissionDecisionReason ?? "Blocked by dev-guard." };
		}

		// Auto-correction forwarding (e.g. _guard_claire_typo's .claire ->
		// .claude rewrite): tool-selection-guard.py signals this as an
		// "allow" decision with `updatedInput` carrying the corrected
		// Claude-Code-shaped fields. ToolCallEventResult.input is OMP's
		// equivalent replacement-input mechanism. Only translated for
		// "read" here, where the OMP field name (`path`) is confirmed —
		// Write/Edit's exact OMP field names were not verified in the
		// Task 1 spike (translateToolInputForGuard passes their input
		// through unchanged rather than guessing), so a correction to
		// those tools' file_path is not forwarded and is a known,
		// documented gap rather than a guessed-and-possibly-wrong mapping.
		if (output?.permissionDecision === "allow" && output.updatedInput && claudeCodeToolName === "Read") {
			const correctedPath = output.updatedInput.file_path;
			if (typeof correctedPath === "string") {
				return { input: { path: correctedPath } };
			}
		}
		return; // allow / no opinion
	});

	// ─── tool_result dispatch (PostToolUse) ─────────────────────────────
	pi.on("tool_result", async (event: ToolResultEvent, ctx): Promise<ToolResultEventResult | void> => {
		if (event.toolName === "AskUserQuestion") return; // handled by the custom tool itself

		const sessionId = getSessionId(ctx);

		if (event.toolName === "bash") {
			// hooks.json's PostToolUse Bash matcher runs both hooks
			// unconditionally — validate-commit-message.sh's exit code never
			// gates whether tool-selection-guard.py also runs, and vice
			// versa. Mirrored here via Promise.all instead of a sequential
			// await-then-early-return, which previously skipped the guard
			// call entirely whenever commit validation blocked.
			const bashResponse = bashToolResponseForGuard(event);
			const [commitResult, guardResult] = await Promise.all([
				runScript(`${HOOKS_DIR}/validate-commit-message.sh`, [], {
					stdin: JSON.stringify({ tool_input: event.input }),
					cwd: ctx.cwd,
				}),
				runGuard(
					[],
					{
						session_id: sessionId,
						tool_use_id: event.toolCallId,
						hook_event_name: "PostToolUse",
						tool_name: "Bash",
						tool_input: event.input,
						tool_response: bashResponse,
					},
					ctx.cwd,
				),
			]);

			if (commitResult.code === 2) {
				return { isError: true, content: [{ type: "text", text: commitResult.stderr.trim() }] };
			}
			if (guardResult.code === 2) {
				const output = parseHookOutput(guardResult.stdout);
				return {
					isError: true,
					content: [
						{
							type: "text",
							text: guardResult.stderr.trim() || output?.permissionDecisionReason || "Blocked by dev-guard.",
						},
					],
				};
			}
			return;
		}

		// "read" doubles as Claude Code's Read AND WebFetch (OMP has no
		// standalone URL-fetch tool — `omp read <url>` handles URLs
		// directly through the same "read" tool; Task 1 spike, Step 4).
		// Translate to whichever Claude Code tool name the guard's
		// PostToolUse dispatch actually branches on for this input shape,
		// so the URL/auth-guard checks (WebFetch) and rtk-tee tracking
		// (Read) both still fire.
		if (event.toolName === "read") {
			const classified = classifyReadTool(event.input as Record<string, unknown>);
			await runGuard(
				[],
				{
					session_id: sessionId,
					tool_use_id: event.toolCallId,
					hook_event_name: "PostToolUse",
					tool_name: classified.toolName,
					tool_input: classified.toolInput,
					tool_response: readToolResponseForGuard(event),
				},
				ctx.cwd,
			);
			return;
		}
	});

	// ─── Stop / SubagentStop mapping ────────────────────────────────────
	// agent_end is OMP's closest analog to both Claude Code's Stop and
	// SubagentStop events — there is no confirmed OMP event that
	// distinguishes a subagent's agent_end from the main session's (Task 1
	// spike; docs/extensions.md's full event-surface list has no such
	// event). Both scripts are dispatched on every agent_end as a
	// documented best-effort approximation. This mirrors the plan's own
	// accepted trade-off for this exact gap: a quality/completeness
	// control, not a security gate, so a wrong or silent guess degrades
	// usefulness, not safety.
	//
	// Separately: stop-hook.py's _parse_transcript() expects a Claude Code
	// JSONL transcript shape (`{"type": "user"|"assistant"|"tool_use",
	// "message": {...}}` per line). OMP's own session file
	// (ctx.sessionManager.getSessionFile()) is not guaranteed to match that
	// format — this was not verified in the Task 1 spike and is out of
	// scope for this bridge to reconcile (would require synthesizing a
	// translated transcript file, which edges toward reimplementing
	// transcript-parsing logic the plan explicitly declines to duplicate).
	// The real session file path is passed through as the best available,
	// trusted (ctx-sourced) value; _parse_transcript() already fails safe
	// (returns empty results, not a crash) on an unparseable file, so the
	// degraded case is "no signals detected" — the same safe-by-default
	// direction as every other best-effort path here. See OMP-COMPAT.md.
	let agentEndWithSubagentSignal = 0;
	let agentEndWithoutSubagentSignal = 0;

	pi.on("agent_end", async (event, ctx) => {
		const sessionId = getSessionId(ctx);
		const transcriptPath = ctx.sessionManager.getSessionFile?.() ?? "";
		const lastMessage = lastAssistantText(event.messages);

		// Telemetry only — not a gate. Best-effort "subagent-context signal"
		// proxy: a transcript path segment pattern distinct from a top-level
		// session file naming convention isn't independently confirmed, so
		// this currently only distinguishes "transcript path present" vs
		// not, pending a clearer live-confirmed signal.
		if (transcriptPath) {
			agentEndWithSubagentSignal++;
		} else {
			agentEndWithoutSubagentSignal++;
		}

		// Neither script depends on the other's result — hooks.json's Stop
		// and SubagentStop are separate matchers with no ordering
		// relationship, so both fire together here via Promise.all.
		const [stopResult, subagentResult] = await Promise.all([
			runScript("uv", ["run", `${HOOKS_DIR}/stop-hook.py`], {
				stdin: JSON.stringify({
					session_id: sessionId,
					transcript_path: transcriptPath,
					cwd: ctx.cwd,
					last_assistant_message: lastMessage,
					stop_hook_active: false,
				}),
				cwd: ctx.cwd,
				timeoutMs: 60_000,
			}),
			runScript("uv", ["run", `${HOOKS_DIR}/subagent-stop-hook.py`], {
				stdin: JSON.stringify({ session_id: sessionId, transcript_path: transcriptPath }),
				cwd: ctx.cwd,
				timeoutMs: 30_000,
			}),
		]);

		// stop-hook.py and subagent-stop-hook.py never signal block/warning
		// via exit code — both scripts' own docstrings state "Exit codes: 0
		// -- always" and their _exit_block() helpers deliberately always
		// `sys.exit(0)`, encoding the decision as `{"decision": "block",
		// "reason": "..."}` JSON on stdout instead. This is specifically to
		// avoid a misleading "Stop hook error" label in Claude Code's own UI
		// (anthropics/claude-code#34600, cited in stop-hook.py's
		// _exit_block() docstring) — a workaround for a Claude-Code-specific
		// display quirk that has nothing to do with OMP, but the resulting
		// contract (exit 0 always, decision on stdout) is what this bridge
		// must honor regardless of which host is asking. A `code === 2`
		// check here — as used elsewhere in this file for
		// tool-selection-guard.py, which DOES use exit code 2 as a real
		// hard-block signal (see the PreToolUse dispatch above) — would
		// never fire for these two scripts and silently drop every
		// block/warning. No defensive fallback on the exit code is kept:
		// unlike a timeout or spawn failure, a future change to these two
		// scripts' documented contract is not a runtime condition this
		// bridge needs to degrade gracefully under, and a dead check would
		// only mislead future maintainers into thinking exit 2 is reachable
		// here.
		const stopOutput = parseStopDecision(stopResult.stdout);
		if (stopOutput?.decision === "block") {
			ctx.ui.notify(stopOutput.reason?.trim() || "dev-guard stop-hook: incomplete work detected.", "warning");
		}
		const subagentOutput = parseStopDecision(subagentResult.stdout);
		if (subagentOutput?.decision === "block") {
			ctx.ui.notify(subagentOutput.reason?.trim() || "dev-guard subagent-stop-hook: FixSummary validation failed.", "warning");
		}
	});

	pi.on("session_shutdown", async (_event, ctx) => {
		const sessionId = getSessionId(ctx);
		await runGuard(["--session-end"], { session_id: sessionId }, ctx.cwd);
		pi.logger.debug("dev-guard agent_end telemetry", {
			withSubagentSignal: agentEndWithSubagentSignal,
			withoutSubagentSignal: agentEndWithoutSubagentSignal,
		});
	});
}
