<!-- Vendored from https://github.com/JetBrains/benjamin-plus-skill (MIT) at commit b9c4ba62df2e1d946218932ef8016bced0d972b1. See UPSTREAM.md in this directory's parent, and token-efficiency.LICENSE alongside this file, for attribution and sync details. Rules 1-3's examples are locally patched on every sync to avoid recommending shell tools this repo's own dev-guard/hooks/tool-selection-guard.py blocks (head, tail, cat, bare python) in favor of the Read tool / uv run — see .github/scripts/patch-token-efficiency.sh. -->

# Benjamin-Plus

Every request you send re-reads the whole conversation so far. The bill is
steps × context, not words. Save by taking fewer steps and keeping bulky tool
output out of the transcript — never by skimping on the work itself. Solve the
task exactly as you otherwise would; these rules change how you look things
up, not what you build.

**1. Recon in one pass.**
Before changing anything, collect every independent fact in a single step:
chain probes with `;` and label the sections (e.g., `ls -la; wc -l
requirements.txt`), or issue several tool calls in one message. A second
lookup round is for questions the first round's answers created. Copying a
convention (a DSL, schema, or file format)? Sample two existing examples of
the exact construct you will write, not one.

**2. Look through a keyhole.**
A command that only inspects ends with a limiter: Read tool with
offset/limit, `grep -m 20`, or `wc -l` before contents. Size unknown?
Measure first, then read the slice you need. Read a file whole only when
you are about to edit it or copy from it verbatim — truncating data you
will transform corrupts output, so keyhole rules apply to inspection,
never to ingestion. If a peek was too narrow, take exactly one wider look.

**3. Probe the environment once.**
Before running code with several dependencies, test them in one probe
(`uv run python3 -c "import x, y, z"`; `command -v tool1 tool2`), and
install everything missing in one command — not one traceback at a time.

**4. Green means the task's own check.**
If the task names verification commands, those are the check: run them
exactly as written, and green means exit status zero. A failure you judge
environmental (missing package, compiler, or tool) is still your failure —
fix the environment and re-run; "unrelated to my change" is not a green
check. The same check failing twice on the same approach means the approach
is wrong: name one alternative and try it before patching the next symptom.
When the check passes, stop: no victory laps, no re-reading files you just
wrote. Close with at most two lines.

**5. Polling is a step.**
A running command that hasn't finished is not new information — but every
status check re-reads the whole conversation. If your harness returns while a
command is still running, wait in large slices (30 seconds or more; minutes
for builds and test suites) before checking again. Never re-poll at
one-second intervals, and never send empty input just to peek. Where
execution blocks until completion, this rule costs nothing.

Never build a verification harness, test suite, or checker the task didn't
ask for — verify stated properties with the shortest command that measures
them, and spend the saved steps on the task itself. If saving a step risks a
wrong result, spend the step: efficiency never outranks correctness, a
failing check, or anything the task explicitly asks you to produce.
