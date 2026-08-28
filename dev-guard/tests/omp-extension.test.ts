/**
 * Coverage for omp-extension.ts's pure translation/parsing logic — the
 * bridge's own code, not the Python guard scripts it shells out to (those
 * are covered by dev-guard/tests/test_omp_bridge_contract.py). This file
 * previously had no automated coverage at all: a Stop/SubagentStop
 * block-decision parsing bug shipped through review undetected because
 * nothing exercised the TypeScript side directly.
 *
 * `import type` in omp-extension.ts means this file has zero runtime
 * dependency on the real `@oh-my-pi/pi-coding-agent` package — the type
 * import is erased by Bun's transpiler, and none of the `pi.on(...)`
 * registrations execute merely from importing the module (they're all
 * inside the un-invoked default-exported activation function). No mock
 * `pi`/`ctx` global is needed for the functions tested here.
 */
import { describe, expect, test } from "bun:test";
import {
	bashToolResponseForGuard,
	classifyReadTool,
	getFailPolicy,
	isMcpTool,
	lastAssistantText,
	OMP_TO_CLAUDE_CODE_MCP_SERVER,
	parseHookOutput,
	parseStopDecision,
	readToolResponseForGuard,
	reencodeMcpToolName,
	translateToolInputForGuard,
	translateToolNameForGuard,
} from "../omp-extension.ts";

describe("translateToolNameForGuard", () => {
	test("known OMP tool names map to their Claude Code equivalents", () => {
		expect(translateToolNameForGuard("bash")).toBe("Bash");
		expect(translateToolNameForGuard("write")).toBe("Write");
		expect(translateToolNameForGuard("edit")).toBe("Edit");
		expect(translateToolNameForGuard("web_search")).toBe("WebSearch");
	});

	test("mcp__ tools are re-encoded via reencodeMcpToolName, not the switch", () => {
		expect(translateToolNameForGuard("mcp__context_resolve_library_id")).toBe(
			"mcp__context7__resolve-library-id",
		);
	});

	test("unknown OMP tool names have no guard-relevant equivalent", () => {
		expect(translateToolNameForGuard("some_future_omp_tool")).toBeUndefined();
	});
});

describe("translateToolInputForGuard", () => {
	test("bash: only the command field is forwarded", () => {
		expect(translateToolInputForGuard("bash", { command: "ls", timeout: 5000 })).toEqual({
			command: "ls",
		});
	});

	test("write/edit: input is passed through unchanged", () => {
		const input = { path: "foo.ts", content: "x" };
		expect(translateToolInputForGuard("write", input)).toBe(input);
		expect(translateToolInputForGuard("edit", input)).toBe(input);
	});

	test("web_search: query/allowed_domains/blocked_domains are forwarded", () => {
		expect(
			translateToolInputForGuard("web_search", {
				query: "test",
				allowed_domains: ["a.com"],
				blocked_domains: ["b.com"],
				extra: "dropped",
			}),
		).toEqual({ query: "test", allowed_domains: ["a.com"], blocked_domains: ["b.com"] });
	});

	test("mcp__ tools: input is passed through unchanged", () => {
		const input = { owner: "x" };
		expect(translateToolInputForGuard("mcp__github_mcp_github_get_me", input)).toBe(input);
	});
});

describe("classifyReadTool (Read-vs-WebFetch URL-detection branch)", () => {
	test("http(s) URL path classifies as WebFetch", () => {
		expect(classifyReadTool({ path: "https://example.com/page" })).toEqual({
			toolName: "WebFetch",
			toolInput: { url: "https://example.com/page" },
		});
		expect(classifyReadTool({ path: "http://example.com" })).toEqual({
			toolName: "WebFetch",
			toolInput: { url: "http://example.com" },
		});
	});

	test("scheme match is case-insensitive", () => {
		expect(classifyReadTool({ path: "HTTPS://example.com" }).toolName).toBe("WebFetch");
	});

	test("non-URL path classifies as Read", () => {
		expect(classifyReadTool({ path: "/some/file.ts" })).toEqual({
			toolName: "Read",
			toolInput: { file_path: "/some/file.ts" },
		});
	});

	test("missing path defaults to an empty-string Read, not a throw", () => {
		expect(classifyReadTool({})).toEqual({ toolName: "Read", toolInput: { file_path: "" } });
	});
});

describe("bashToolResponseForGuard", () => {
	test("text content blocks are joined into stdout, stderr always empty", () => {
		const event = {
			content: [
				{ type: "text", text: "line one" },
				{ type: "text", text: "line two" },
			],
		} as unknown as Parameters<typeof bashToolResponseForGuard>[0];
		expect(bashToolResponseForGuard(event)).toEqual({ stdout: "line one\nline two", stderr: "" });
	});

	test("non-text content blocks are filtered out", () => {
		const event = {
			content: [
				{ type: "text", text: "kept" },
				{ type: "image", data: "..." },
			],
		} as unknown as Parameters<typeof bashToolResponseForGuard>[0];
		expect(bashToolResponseForGuard(event)).toEqual({ stdout: "kept", stderr: "" });
	});

	test("empty content array produces an empty stdout", () => {
		const event = { content: [] } as unknown as Parameters<typeof bashToolResponseForGuard>[0];
		expect(bashToolResponseForGuard(event)).toEqual({ stdout: "", stderr: "" });
	});
});

describe("readToolResponseForGuard", () => {
	test("text content blocks are joined into content", () => {
		const event = {
			content: [
				{ type: "text", text: "line one" },
				{ type: "text", text: "line two" },
			],
		} as unknown as Parameters<typeof readToolResponseForGuard>[0];
		expect(readToolResponseForGuard(event)).toEqual({ content: "line one\nline two" });
	});

	test("non-text content blocks are filtered out", () => {
		const event = {
			content: [
				{ type: "text", text: "kept" },
				{ type: "image", data: "..." },
			],
		} as unknown as Parameters<typeof readToolResponseForGuard>[0];
		expect(readToolResponseForGuard(event)).toEqual({ content: "kept" });
	});

	test("empty content array produces empty content", () => {
		const event = { content: [] } as unknown as Parameters<typeof readToolResponseForGuard>[0];
		expect(readToolResponseForGuard(event)).toEqual({ content: "" });
	});
});

describe("reencodeMcpToolName", () => {
	test("context7: trailing digit is dropped and hyphenated tool names are restored", () => {
		expect(reencodeMcpToolName("mcp__context_resolve_library_id")).toBe(
			"mcp__context7__resolve-library-id",
		);
		expect(reencodeMcpToolName("mcp__context_query_docs")).toBe("mcp__context7__query-docs");
	});

	test("github: plugin_ prefix and hyphenated server segment are restored", () => {
		expect(reencodeMcpToolName("mcp__github_mcp_github_get_me")).toBe(
			"mcp__plugin_github-mcp_github__get_me",
		);
	});

	test("a known server with no tool-name override passes the tool name through unchanged", () => {
		expect(reencodeMcpToolName("mcp__serena_find_symbol")).toBe("mcp__serena__find_symbol");
	});

	test("unknown server: best-effort single->double underscore at the first boundary", () => {
		expect(reencodeMcpToolName("mcp__unknownserver_sometool")).toBe(
			"mcp__unknownserver__sometool",
		);
	});

	test("unknown server with no underscore at all is returned unchanged", () => {
		expect(reencodeMcpToolName("mcp__onlyname")).toBe("mcp__onlyname");
	});
});

describe("isMcpTool", () => {
	test("mcp__ prefix is detected", () => {
		expect(isMcpTool("mcp__github_mcp_github_get_me")).toBe(true);
		expect(isMcpTool("bash")).toBe(false);
	});
});

describe("getFailPolicy", () => {
	test("bash fails closed", () => {
		expect(getFailPolicy("bash")).toBe("fail-closed");
	});

	test("mcp__ tools fail closed", () => {
		expect(getFailPolicy("mcp__github_mcp_github_get_me")).toBe("fail-closed");
	});

	test("write, edit, read, and web_search fail open", () => {
		expect(getFailPolicy("write")).toBe("fail-open");
		expect(getFailPolicy("edit")).toBe("fail-open");
		expect(getFailPolicy("read")).toBe("fail-open");
		// web_search is explicitly fail-open, matching OMP-COMPAT.md's
		// "Fail-open / fail-closed policy" section.
		expect(getFailPolicy("web_search")).toBe("fail-open");
	});

	test("a matcher with no classification returns undefined", () => {
		expect(getFailPolicy("notebook_edit")).toBeUndefined();
	});
});

describe("parseHookOutput", () => {
	test("valid JSON with hookSpecificOutput parses correctly", () => {
		const stdout = JSON.stringify({
			hookSpecificOutput: { permissionDecision: "allow", additionalContext: "note" },
		});
		expect(parseHookOutput(stdout)).toEqual({
			permissionDecision: "allow",
			additionalContext: "note",
		});
	});

	test("empty stdout returns undefined without throwing", () => {
		expect(parseHookOutput("")).toBeUndefined();
		expect(parseHookOutput("   \n")).toBeUndefined();
	});

	test("malformed JSON returns undefined without throwing", () => {
		expect(parseHookOutput("{not json")).toBeUndefined();
	});

	test("valid JSON with no hookSpecificOutput key returns undefined", () => {
		expect(parseHookOutput(JSON.stringify({ other: 1 }))).toBeUndefined();
	});
});

describe("parseStopDecision", () => {
	test("valid JSON parses correctly", () => {
		const stdout = JSON.stringify({ decision: "block", reason: "incomplete work" });
		expect(parseStopDecision(stdout)).toEqual({ decision: "block", reason: "incomplete work" });
	});

	test("empty stdout returns undefined without throwing", () => {
		expect(parseStopDecision("")).toBeUndefined();
		expect(parseStopDecision("  ")).toBeUndefined();
	});

	test("malformed JSON returns undefined without throwing, not null", () => {
		expect(parseStopDecision("{decision: block")).toBeUndefined();
	});
});

describe("lastAssistantText", () => {
	test("walks backward to find the most recent assistant message", () => {
		const messages = [
			{ role: "assistant", content: "first" },
			{ role: "user", content: "question" },
			{ role: "assistant", content: "second" },
		];
		expect(lastAssistantText(messages)).toBe("second");
	});

	test("skips trailing non-assistant messages to find an earlier assistant message", () => {
		const messages = [
			{ role: "user", content: "question" },
			{ role: "assistant", content: "answer" },
			{ role: "tool_result", content: "tool output" },
			{ role: "user", content: "follow-up" },
		];
		expect(lastAssistantText(messages)).toBe("answer");
	});

	test("string content is returned as-is", () => {
		expect(lastAssistantText([{ role: "assistant", content: "plain string" }])).toBe(
			"plain string",
		);
	});

	test("array content with mixed block types joins only text blocks", () => {
		const messages = [
			{
				role: "assistant",
				content: [
					{ type: "text", text: "part one" },
					{ type: "tool_use", id: "x" },
					{ type: "text", text: "part two" },
				],
			},
		];
		expect(lastAssistantText(messages)).toBe("part one\npart two");
	});

	test("assistant message with no text blocks returns an empty string", () => {
		const messages = [{ role: "assistant", content: [{ type: "tool_use", id: "x" }] }];
		expect(lastAssistantText(messages)).toBe("");
	});

	test("no assistant message returns an empty string, not a throw", () => {
		expect(lastAssistantText([{ role: "user", content: "hi" }])).toBe("");
		expect(lastAssistantText([])).toBe("");
	});
});

describe("MCP server table internal consistency (drift check)", () => {
	/**
	 * SCOPE: this only catches drift between the two SIDES of the mapping
	 * that already live in this repo — mcp_constants.py's MCP_READ_ONLY
	 * server set vs. omp-extension.ts's OMP_TO_CLAUDE_CODE_MCP_SERVER
	 * table — if one is updated without the other. It does NOT and CANNOT
	 * detect drift against a live OMP install's actual naming convention;
	 * bun test has no OMP runtime available. Mirrors the Python-side
	 * TestMcpServerSyncContract in test_omp_bridge_contract.py, which
	 * performs the same check in the opposite direction with a hardcoded
	 * server set. Both should be updated together when either constants
	 * file changes.
	 */
	test("every mcp_constants.py MCP_READ_ONLY server has a bridge-table entry", async () => {
		const constantsSource = await Bun.file(
			new URL("../hooks/mcp_constants.py", import.meta.url),
		).text();
		const serversInPython = new Set<string>();
		for (const match of constantsSource.matchAll(/_qualify\(\s*"([^"]+)"/g)) {
			serversInPython.add(match[1]);
		}
		// Sanity check on the extraction itself, so a regex that silently
		// stops matching (e.g. after a mcp_constants.py reformat) fails
		// loudly here instead of passing with an empty set.
		expect(serversInPython.size).toBeGreaterThan(0);

		const serversInBridge = new Set(Object.values(OMP_TO_CLAUDE_CODE_MCP_SERVER));
		const unmapped = [...serversInPython].filter((server) => !serversInBridge.has(server));
		expect(unmapped).toEqual([]);
	});
});
