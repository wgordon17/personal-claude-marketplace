/**
 * git-tools' bridge has no pure logic to unit-test: its only body is a
 * single session_start handler that shells out to scripts/git-instructions.sh
 * and forwards its stdout verbatim — no translation, parsing, or branching
 * logic lives in this file. What IS worth a regression test is the same
 * risk flagged for dev-guard's larger bridge: that importing the module at
 * all doesn't crash by eagerly invoking `pi.on(...)` at module scope
 * (`import type` erases the `@oh-my-pi/pi-coding-agent` dependency at
 * runtime, and the real registration only happens inside the un-invoked
 * default-exported activation function).
 */
import { expect, test } from "bun:test";
import ompExtension from "../omp-extension.ts";

test("module imports without crashing and exports an activation function", () => {
	expect(typeof ompExtension).toBe("function");
});
