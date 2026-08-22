/**
 * git-tools OMP bridge.
 *
 * Single SessionStart hook, no enforcement logic: shells out to the
 * existing scripts/git-instructions.sh unchanged and injects its dynamic
 * git-workflow instructions into context. The script reads no session-derived
 * input and makes no trust/permission decision — it only inspects the
 * current git repo via `git rev-parse`/`git symbolic-ref` in its cwd — so
 * dev-guard's bridge's ctx-only session-identity sourcing rule (which exists
 * to prevent trust-store impersonation) has no equivalent risk to guard
 * against here.
 *
 * Ground truth for the subprocess approach comes from the same Task 1 spike
 * as dev-guard's bridge: hack/research/omp-spike-findings.md (gitignored,
 * local-only). Bun.spawn() is used directly rather than pi.exec() for
 * consistency with that bridge's shared pattern, even though this call
 * needs no stdin.
 */
import type { ExtensionAPI } from "@oh-my-pi/pi-coding-agent";

const PLUGIN_ROOT = new URL(".", import.meta.url).pathname.replace(/\/$/, "");

export default function (pi: ExtensionAPI) {
	pi.on("session_start", async (_event, ctx) => {
		let proc: ReturnType<typeof Bun.spawn>;
		try {
			proc = Bun.spawn([`${PLUGIN_ROOT}/scripts/git-instructions.sh`], {
				stdin: "ignore",
				stdout: "pipe",
				stderr: "pipe",
				cwd: ctx.cwd,
				env: { ...process.env, CLAUDE_PLUGIN_ROOT: PLUGIN_ROOT },
			});
		} catch {
			return;
		}

		const [stdout] = await Promise.all([new Response(proc.stdout).text(), proc.exited]);

		// sendMessage's payload type is `string | Partial<CustomMessage>`
		// (dist/types/session/messages.d.ts) — a plain string is the valid
		// form, NOT a `{type, text}` object (caught during live testing).
		//
		// `deliverAs: "nextTurn"` is the closest documented analog to
		// Claude Code's SessionStart hook, which injects once, before the
		// first assistant turn, persisting in the transcript for the rest
		// of the session — live-confirmed end-to-end against a real git
		// repo: the model correctly quoted git-instructions.sh's dynamic
		// mainline-detection and workflow guidance verbatim from context.
		if (stdout.trim()) {
			pi.sendMessage(stdout, { deliverAs: "nextTurn" });
		}
	});
}
