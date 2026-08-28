/**
 * github-mcp's bridge has one piece of state worth guarding: GITHUB_MCP_HINT
 * is required to stay byte-for-byte identical to the string in
 * hooks/hooks.json's SessionStart `echo` command (see the comment in
 * omp-extension.ts) — the two are otherwise free to drift silently since
 * nothing else compares them. This test parses the literal string out of
 * hooks.json and asserts equality, so an edit to one without the other
 * fails loudly instead of silently diverging between the Claude Code and
 * OMP session-start experiences.
 *
 * The remaining logic (a single session_start handler with no branching)
 * has nothing else pure to unit-test; the module-import smoke test below
 * guards the same "does importing this crash" risk covered for the other
 * two bridges.
 */
import { expect, test } from "bun:test";
import ompExtension, { GITHUB_MCP_HINT } from "../omp-extension.ts";

test("module imports without crashing and exports an activation function", () => {
	expect(typeof ompExtension).toBe("function");
});

test("GITHUB_MCP_HINT matches hooks.json's SessionStart echo string", async () => {
	const hooksJsonSource = await Bun.file(new URL("../hooks/hooks.json", import.meta.url)).text();
	const echoMatch = hooksJsonSource.match(/"command":\s*"echo '([^]*?)'"/);
	expect(echoMatch).not.toBeNull();
	expect(GITHUB_MCP_HINT).toBe(echoMatch![1]);
});
