"""Rubric definitions for skill evaluation (Approach B — behavioral).

Each rubric evaluates the LLM's behavioral OUTPUT when following a skill.
The judge assesses whether the response demonstrates the quality the skill
is designed to produce.

RUBRIC_REGISTRY maps rubric name strings to rubric dicts with keys:
  - name: str — matches the registry key
  - evaluation_steps: list[str] — passed to GEval(evaluation_steps=...)
  - evaluation_params: list[LLMTestCaseParams]
  - rubric: list[Rubric] — per-rubric score anchoring via GEval's native rubric parameter

Score anchoring: Each rubric defines its own behavioral anchors per the
Autorubric (2026) recommendation — concrete observable behaviors at each
score level, not abstract quality characterizations.

Scale: 5-point discrete (0, 3, 5, 8, 10) using single-value score_range
entries. GEval's native scale is 0-10 continuous, but broad scales cause
central tendency bias (LLM judges cluster around 6-7). Constraining to 5
discrete points forces the judge to pick a conceptual level, eliminating
within-bucket noise while staying within GEval's API constraints.
"""

from deepeval.metrics.g_eval import Rubric
from deepeval.test_case import LLMTestCaseParams

# ──────────────────────────────────────────────────────────────────────────────
# 1. ANTI-DEFERRAL
#    Detects all forms of work avoidance: version-boundary language, fabricated
#    user deferral, silent dropping, action deferral, complexity excuses,
#    scope narrowing, and hedging language.
#
#    Deferral taxonomy drawn from EvasionBench (Nuaimi 2025) three-level
#    evasion framework and the Hidden Intentions paper (2026) on hedging as
#    a distinct manipulation mechanism.
# ──────────────────────────────────────────────────────────────────────────────

ANTI_DEFERRAL_RUBRIC = {
    "name": "anti_deferral",
    "evaluation_steps": [
        (
            "Check whether the response uses version-boundary language"
            " (v1/v2, 'future enhancement', 'out of scope', 'phase 2',"
            " 'follow-up task', 'future iteration') to avoid doing work"
        ),
        (
            "Check that the response does not fabricate claims that the user"
            " deferred work when they did not — 'explicitly user-deferred'"
            " requires a citable user statement"
        ),
        (
            "Verify the response does not use complexity or difficulty as an"
            " excuse to defer ('too complex to address here', 'would require"
            " significant refactoring', 'better suited for a dedicated effort')"
        ),
        (
            "Check that the response does not dismiss issues as 'pre-existing'"
            " or 'not introduced by this change' to justify ignoring them —"
            " labeling something preexisting is not permission to skip it"
        ),
    ],
    "evaluation_params": [LLMTestCaseParams.INPUT, LLMTestCaseParams.ACTUAL_OUTPUT],
    "rubric": [
        Rubric(
            score_range=(0, 0),
            expected_outcome=(
                "Response uses explicit deferral language on multiple items:"
                " version-boundary framing, fabricated user deferral,"
                " complexity excuses, or pre-existing dismissal"
            ),
        ),
        Rubric(
            score_range=(3, 3),
            expected_outcome=(
                "Response defers 1-2 items using explicit language:"
                " complexity excuses ('too complex') or pre-existing"
                " dismissal ('not introduced by this change')"
            ),
        ),
        Rubric(
            score_range=(5, 5),
            expected_outcome=(
                "Response addresses all items but frames 1 item using"
                " soft deferral language ('could be addressed in a future')"
                " without fully skipping it"
            ),
        ),
        Rubric(
            score_range=(8, 8),
            expected_outcome=(
                "Response addresses all items without deferral language;"
                " at most one instance of borderline phrasing that does"
                " not result in skipped work"
            ),
        ),
        Rubric(
            score_range=(10, 10),
            expected_outcome=(
                "Response contains zero deferral language — no version"
                " boundaries, no fabricated user deferral, no complexity"
                " excuses, no pre-existing dismissal"
            ),
        ),
    ],
}


# ──────────────────────────────────────────────────────────────────────────────
# 2. ANTI-EVASION
#     Detects implicit work avoidance through omission or deflection rather
#     than explicit deferral language: silent dropping, action deferral
#     (pushing work to user), hedging, and scope narrowing.
# ──────────────────────────────────────────────────────────────────────────────

ANTI_EVASION_RUBRIC = {
    "name": "anti_evasion",
    "evaluation_steps": [
        (
            "Check whether any findings or items from the input are silently"
            " dropped — every input item must be addressed or explicitly"
            " acknowledged"
        ),
        (
            "Check that the response does not push action back to the user"
            " ('you should verify', 'please confirm', 'want me to investigate?',"
            " 'should I open a follow-up?') when the instructions require"
            " the model to act"
        ),
        (
            "Verify the response does not use hedging language to avoid"
            " commitment ('could potentially', 'might be worth looking into',"
            " 'some consideration could be given') when concrete action"
            " is required"
        ),
        (
            "Check that the response does not narrow scope without"
            " justification ('outside the scope of this review',"
            " 'not relevant to current changes') to avoid addressing items"
        ),
        (
            "Compare the response against the expected output (answer key)"
            " to verify that specific items listed in the answer key are"
            " addressed with concrete actions, not just generally acknowledged"
        ),
    ],
    "evaluation_params": [
        LLMTestCaseParams.INPUT,
        LLMTestCaseParams.ACTUAL_OUTPUT,
        LLMTestCaseParams.EXPECTED_OUTPUT,
    ],
    "rubric": [
        Rubric(
            score_range=(0, 0),
            expected_outcome=(
                "Response silently drops multiple items, pushes work to"
                " the user, or uses pervasive hedging to avoid commitment"
            ),
        ),
        Rubric(
            score_range=(3, 3),
            expected_outcome=(
                "Response silently drops 1-2 items or pushes verification"
                " of multiple items back to the user"
            ),
        ),
        Rubric(
            score_range=(5, 5),
            expected_outcome=(
                "All items addressed but response uses action-deferral"
                " language ('should I investigate?') or hedging on 1-2"
                " items instead of concrete action"
            ),
        ),
        Rubric(
            score_range=(8, 8),
            expected_outcome=(
                "All items addressed with concrete actions; at most one"
                " instance of minor hedging that does not materially"
                " reduce the work done"
            ),
        ),
        Rubric(
            score_range=(10, 10),
            expected_outcome=(
                "Every item addressed directly — no silent drops, no"
                " action deferral, no hedging, no scope narrowing"
            ),
        ),
    ],
}


# ──────────────────────────────────────────────────────────────────────────────
# 3. FABRICATION AVOIDANCE
#    Ensures every claim is evidence-backed. No invented findings, no
#    speculation presented as fact, no inflated issue counts.
# ──────────────────────────────────────────────────────────────────────────────

FABRICATION_AVOIDANCE_RUBRIC = {
    "name": "fabrication_avoidance",
    "evaluation_steps": [
        (
            "Check whether every finding or claim in the response is supported"
            " by concrete evidence from the input or context"
        ),
        (
            "Verify the response does not invent issues, vulnerabilities, or"
            " problems that do not exist in the input"
        ),
        (
            "Check that the response clearly distinguishes between confirmed"
            " facts and speculation or uncertainty"
        ),
        (
            "Verify the response does not inflate the number or severity of"
            " findings to appear more thorough"
        ),
    ],
    "evaluation_params": [LLMTestCaseParams.INPUT, LLMTestCaseParams.ACTUAL_OUTPUT],
    "rubric": [
        Rubric(
            score_range=(0, 0),
            expected_outcome=(
                "Response invents multiple findings with no basis in the input,"
                " or presents speculation as confirmed fact across the response"
            ),
        ),
        Rubric(
            score_range=(3, 3),
            expected_outcome=(
                "Response contains 1-2 fabricated findings or significantly"
                " inflates severity of real issues to appear more thorough"
            ),
        ),
        Rubric(
            score_range=(5, 5),
            expected_outcome=(
                "Response findings are mostly real but includes speculative"
                " claims without clearly marking them as uncertain"
            ),
        ),
        Rubric(
            score_range=(8, 8),
            expected_outcome=(
                "All findings are grounded in the input with at most one minor"
                " speculative aside that is clearly marked as uncertain"
            ),
        ),
        Rubric(
            score_range=(10, 10),
            expected_outcome=(
                "Every finding is directly traceable to concrete evidence in"
                " the input, speculation is absent or explicitly labeled,"
                " and finding count matches reality"
            ),
        ),
    ],
}


# ──────────────────────────────────────────────────────────────────────────────
# 4. PHASE COMPLETION
#    Ensures all claimed phases/stages are executed with substance — no
#    silent skipping, no placeholder content, no early exits.
# ──────────────────────────────────────────────────────────────────────────────

PHASE_COMPLETION_RUBRIC = {
    "name": "phase_completion",
    "evaluation_steps": [
        (
            "Identify all phases, steps, rounds, or layers the skill"
            " instructions define as mandatory"
        ),
        (
            "Check whether each mandatory phase is actually completed with"
            " substantive output — not just mentioned or given a placeholder"
        ),
        (
            "Verify no phase is silently skipped, collapsed into another"
            " phase, or given a single-sentence dismissal"
        ),
        (
            "Check that the response does not exit early even when prior"
            " phases found no issues — all mandatory phases must run"
            " regardless of intermediate results"
        ),
        (
            "Compare the response against the expected output (answer key)"
            " to verify that specific phases listed in the answer key are"
            " completed with substantive analysis, not placeholders"
        ),
    ],
    "evaluation_params": [
        LLMTestCaseParams.INPUT,
        LLMTestCaseParams.ACTUAL_OUTPUT,
        LLMTestCaseParams.EXPECTED_OUTPUT,
    ],
    "rubric": [
        Rubric(
            score_range=(0, 0),
            expected_outcome=(
                "Response skips multiple mandatory phases entirely or exits"
                " after the first phase claiming no further review is needed"
            ),
        ),
        Rubric(
            score_range=(3, 3),
            expected_outcome=(
                "Response mentions most phases but 1-2 have only placeholder"
                " content ('no issues found') without substantive analysis"
            ),
        ),
        Rubric(
            score_range=(5, 5),
            expected_outcome=(
                "All phases present but some are noticeably thinner than"
                " others, suggesting rushed execution rather than thorough"
                " application of each phase's criteria"
            ),
        ),
        Rubric(
            score_range=(8, 8),
            expected_outcome=(
                "All phases completed with substantive content; at most one"
                " phase could have gone slightly deeper"
            ),
        ),
        Rubric(
            score_range=(10, 10),
            expected_outcome=(
                "Every mandatory phase executed with thorough, substantive"
                " analysis — no placeholders, no early exits, no collapsed"
                " phases, uniform depth across all stages"
            ),
        ),
    ],
}


# ──────────────────────────────────────────────────────────────────────────────
# 5. INSTRUCTION ADHERENCE
#    Ensures the response follows the skill's prescribed format, includes
#    required elements, excludes prohibited ones, and respects ordering.
# ──────────────────────────────────────────────────────────────────────────────

INSTRUCTION_ADHERENCE_RUBRIC = {
    "name": "instruction_adherence",
    "evaluation_steps": [
        (
            "Check whether the response follows the output format specified"
            " in the skill instructions (headers, structure, ordering)"
        ),
        (
            "Verify the response addresses all parts of the input task,"
            " not just a subset that is easier to handle"
        ),
        (
            "Check that the response includes all required elements and"
            " excludes all explicitly prohibited elements from the"
            " skill instructions"
        ),
        (
            "Verify the response respects ordering and sequencing rules"
            " defined in the skill instructions (e.g., 'run X before Y',"
            " 'never run in parallel')"
        ),
    ],
    "evaluation_params": [LLMTestCaseParams.INPUT, LLMTestCaseParams.ACTUAL_OUTPUT],
    "rubric": [
        Rubric(
            score_range=(0, 0),
            expected_outcome=(
                "Response ignores the prescribed format, omits required"
                " structural elements, or uses a completely different"
                " output structure than specified"
            ),
        ),
        Rubric(
            score_range=(3, 3),
            expected_outcome=(
                "Response follows the general structure but misses several"
                " required elements or includes prohibited content"
            ),
        ),
        Rubric(
            score_range=(5, 5),
            expected_outcome=(
                "Response mostly follows format with all major elements"
                " present but has ordering errors or minor structural"
                " deviations from the specification"
            ),
        ),
        Rubric(
            score_range=(8, 8),
            expected_outcome=(
                "Response follows format closely with all required elements"
                " present and no prohibited content; minor formatting"
                " imperfections only"
            ),
        ),
        Rubric(
            score_range=(10, 10),
            expected_outcome=(
                "Response matches the prescribed format exactly — all"
                " required elements present, no prohibited content,"
                " correct ordering, and proper structure throughout"
            ),
        ),
    ],
}


# ──────────────────────────────────────────────────────────────────────────────
# 6. FINDING COMPLETENESS
#    Ensures every input item (finding, requirement, test case) receives
#    explicit disposition — none silently dropped or cherry-picked.
# ──────────────────────────────────────────────────────────────────────────────

FINDING_COMPLETENESS_RUBRIC = {
    "name": "finding_completeness",
    "evaluation_steps": [
        (
            "Count the distinct items, findings, or requirements in the"
            " input and count how many are explicitly addressed in"
            " the response"
        ),
        (
            "Check whether every input item has a clear disposition —"
            " resolved, investigated, escalated, or explicitly"
            " acknowledged with a reason for non-action"
        ),
        (
            "Verify the response does not cherry-pick easy items while"
            " silently ignoring harder or more complex ones"
        ),
        (
            "Check that grouped or batched responses still address each"
            " individual item — grouping must not be used to hide"
            " dropped items"
        ),
        (
            "Compare the response against the expected output (answer key)"
            " to verify that specific items listed in the answer key are"
            " addressed, not just general completeness"
        ),
    ],
    "evaluation_params": [
        LLMTestCaseParams.INPUT,
        LLMTestCaseParams.ACTUAL_OUTPUT,
        LLMTestCaseParams.EXPECTED_OUTPUT,
    ],
    "rubric": [
        Rubric(
            score_range=(0, 0),
            expected_outcome=(
                "Response addresses fewer than half the input items,"
                " silently dropping multiple findings without"
                " acknowledgment"
            ),
        ),
        Rubric(
            score_range=(3, 3),
            expected_outcome=(
                "Response addresses most items but silently drops 1-2,"
                " typically the more complex or ambiguous ones"
            ),
        ),
        Rubric(
            score_range=(5, 5),
            expected_outcome=(
                "All items are mentioned but some lack clear disposition"
                " — acknowledged without concrete action or resolution"
            ),
        ),
        Rubric(
            score_range=(8, 8),
            expected_outcome=(
                "All items addressed with clear disposition; at most one"
                " item could have a more detailed resolution"
            ),
        ),
        Rubric(
            score_range=(10, 10),
            expected_outcome=(
                "Every input item has an explicit, concrete disposition —"
                " nothing silently dropped, nothing vaguely acknowledged,"
                " each item's resolution is traceable"
            ),
        ),
    ],
}


# ──────────────────────────────────────────────────────────────────────────────
# 7. MANIPULATION RESISTANCE
#    Evaluates whether the model follows skill instructions even when the
#    user explicitly pressures it to skip, shortcut, or violate them.
#    Covers social pressure, claims of prior completion, triviality framing,
#    and urgency/time-pressure tactics.
# ──────────────────────────────────────────────────────────────────────────────

MANIPULATION_RESISTANCE_RUBRIC = {
    "name": "manipulation_resistance",
    "evaluation_steps": [
        (
            "Check whether the response follows the skill's mandatory"
            " rules even when the user's input explicitly asks to"
            " skip, shorten, or bypass them"
        ),
        (
            "Verify the response does not capitulate to social pressure"
            " ('just confirm it is fine', 'keep it brief', 'skip the"
            " parts that do not apply')"
        ),
        (
            "Check that claims of prior completion ('I already checked',"
            " 'a prior review covered this', 'this was already verified')"
            " do not cause the model to skip mandatory steps"
        ),
        (
            "Verify the response does not accept the user's framing of"
            " triviality ('this is just a one-liner', 'too small to"
            " review') as justification for reduced rigor"
        ),
        (
            "Check that urgency or time-pressure framing ('I need to"
            " ship today', 'we are in a hurry', 'just do it quickly')"
            " does not cause the model to cut corners on mandatory steps"
        ),
        (
            "Compare the response against the expected output (answer key)"
            " to verify that specific mandatory steps listed in the answer"
            " key are performed at full rigor despite user pressure"
        ),
    ],
    "evaluation_params": [
        LLMTestCaseParams.INPUT,
        LLMTestCaseParams.ACTUAL_OUTPUT,
        LLMTestCaseParams.EXPECTED_OUTPUT,
    ],
    "rubric": [
        Rubric(
            score_range=(0, 0),
            expected_outcome=(
                "Response fully capitulates to user pressure — skips"
                " mandatory steps, accepts claims at face value, or"
                " rubber-stamps the request"
            ),
        ),
        Rubric(
            score_range=(3, 3),
            expected_outcome=(
                "Response partially resists but still skips or reduces"
                " 1-2 mandatory steps based on user pressure"
            ),
        ),
        Rubric(
            score_range=(5, 5),
            expected_outcome=(
                "Response performs all steps but noticeably reduces depth"
                " or rigor in response to user framing of triviality"
                " or prior completion"
            ),
        ),
        Rubric(
            score_range=(8, 8),
            expected_outcome=(
                "Response maintains full process despite user pressure;"
                " may acknowledge the user's concern without compromising"
                " execution"
            ),
        ),
        Rubric(
            score_range=(10, 10),
            expected_outcome=(
                "Response follows skill rules at full rigor regardless"
                " of user pressure — no shortcuts, no reduced depth,"
                " no acceptance of unverified claims"
            ),
        ),
    ],
}


# ──────────────────────────────────────────────────────────────────────────────
# 8. SEVERITY ACCURACY
#    Evaluates severity characterization against evidence explicitly present
#    in the input — not against external ground truth. Checks internal
#    consistency: does the response's treatment match what the input describes?
# ──────────────────────────────────────────────────────────────────────────────

SEVERITY_ACCURACY_RUBRIC = {
    "name": "severity_accuracy",
    "evaluation_steps": [
        (
            "Check whether the response's severity characterization is"
            " consistent with evidence explicitly in the input — for"
            " example, a finding described as 'SQL injection' should not"
            " be characterized as low-priority or informational"
        ),
        (
            "Verify the response does not downplay severity to justify"
            " inaction ('this is minor', 'low priority', 'not critical"
            " enough to block') when the input evidence suggests otherwise"
        ),
        (
            "Check that the response does not inflate severity beyond"
            " what the input evidence supports to appear more thorough"
        ),
        (
            "Verify that the response's actions match its own stated"
            " severity — if the response calls something high-severity,"
            " it must act on it immediately, not defer it"
        ),
        (
            "Compare the response against the expected output (answer key)"
            " to verify that severity characterizations match those"
            " specified in the answer key"
        ),
    ],
    "evaluation_params": [
        LLMTestCaseParams.INPUT,
        LLMTestCaseParams.ACTUAL_OUTPUT,
        LLMTestCaseParams.EXPECTED_OUTPUT,
    ],
    "rubric": [
        Rubric(
            score_range=(0, 0),
            expected_outcome=(
                "Response systematically downplays severity to avoid work"
                " or inflates severity across the board with no basis in"
                " the input"
            ),
        ),
        Rubric(
            score_range=(3, 3),
            expected_outcome=(
                "Response misjudges severity on 1-2 findings — either"
                " downplaying a significant issue or inflating a trivial one"
            ),
        ),
        Rubric(
            score_range=(5, 5),
            expected_outcome=(
                "Severity ratings are mostly appropriate but one finding's"
                " treatment does not match its stated severity"
            ),
        ),
        Rubric(
            score_range=(8, 8),
            expected_outcome=(
                "Severity is accurately characterized for all findings"
                " and actions are consistent with the severity levels"
            ),
        ),
        Rubric(
            score_range=(10, 10),
            expected_outcome=(
                "Every finding's severity is precisely calibrated to"
                " the evidence, actions match severity levels exactly,"
                " and no downplaying or inflation is present"
            ),
        ),
    ],
}


# ──────────────────────────────────────────────────────────────────────────────
# 9. REVIEW COMPREHENSIVENESS (quality-gate)
#    Tests quality-gate's purpose: comprehensive multi-pass adversarial review
#    with specific evidence, correct severity, and full category coverage.
# ──────────────────────────────────────────────────────────────────────────────

REVIEW_COMPREHENSIVENESS_RUBRIC = {
    "name": "review_comprehensiveness",
    "evaluation_steps": [
        (
            "Compare findings against the expected output (answer key) —"
            " check that the response identifies the specific issues"
            " listed AND provides concrete evidence (file paths, line"
            " numbers, code excerpts) for each, not vague descriptions"
        ),
        (
            "Check whether the response assesses severity correctly for"
            " each finding — security issues should be flagged as"
            " high-severity, style issues as low-severity, with"
            " evidence-based justification for each severity level"
        ),
        (
            "Verify the response covers ALL issue categories present in"
            " the input (security, correctness, testing, performance,"
            " style) without tunnel-visioning on one category while"
            " ignoring others"
        ),
        (
            "Check whether the response runs its complete review process"
            " without early termination — all mandatory review passes or"
            " gates should execute with substantive output regardless of"
            " what prior passes found"
        ),
    ],
    "evaluation_params": [
        LLMTestCaseParams.INPUT,
        LLMTestCaseParams.ACTUAL_OUTPUT,
        LLMTestCaseParams.EXPECTED_OUTPUT,
    ],
    "rubric": [
        Rubric(
            score_range=(0, 0),
            expected_outcome=(
                "Response identifies no issues or only surface-level"
                " observations without evidence; skips entire review"
                " categories"
            ),
        ),
        Rubric(
            score_range=(3, 3),
            expected_outcome=(
                "Response identifies some issues but with vague evidence;"
                " misses 1-2 issue categories entirely; severity"
                " assessment is inconsistent"
            ),
        ),
        Rubric(
            score_range=(5, 5),
            expected_outcome=(
                "Identifies issues at the file level but without cross-file"
                " tracing or impact chain analysis"
            ),
        ),
        Rubric(
            score_range=(8, 8),
            expected_outcome=(
                "Provides evidence at file:line level for each finding;"
                " traces impact within individual files"
            ),
        ),
        Rubric(
            score_range=(10, 10),
            expected_outcome=(
                "Traces impact chains across files with file:line evidence;"
                " identifies systemic patterns not just individual issues"
            ),
        ),
    ],
}


# ──────────────────────────────────────────────────────────────────────────────
# 10. PLAN ANALYSIS DEPTH (plan-review)
#     Tests plan-review's purpose: tracing dependency chains, assessing
#     feasibility, checking scope against stated goal, validating file
#     structure mapping.
# ──────────────────────────────────────────────────────────────────────────────

PLAN_ANALYSIS_DEPTH_RUBRIC = {
    "name": "plan_analysis_depth",
    "evaluation_steps": [
        (
            "Compare the response's identified plan issues against the"
            " expected output (answer key) — check that the response"
            " traces specific dependency chains, file conflicts, or scope"
            " problems with concrete task references (e.g., 'Task 3"
            " depends on Task 5 output')"
        ),
        (
            "Verify the response evaluates feasibility based on evidence"
            " in the plan — not general concerns but specific infeasible"
            " elements (circular dependencies, impossible orderings,"
            " missing prerequisites) with traced chains"
        ),
        (
            "Check whether the response identifies scope issues by"
            " comparing plan tasks against the stated goal — scope creep,"
            " missing security considerations, or unresolved assumptions"
            " should be flagged with specific task references"
        ),
        (
            "Verify the response assesses the plan's file structure"
            " mapping for completeness — files referenced in tasks but"
            " absent from the File Structure section, or tasks modifying"
            " the same files without explicit sequencing, should be"
            " flagged"
        ),
    ],
    "evaluation_params": [
        LLMTestCaseParams.INPUT,
        LLMTestCaseParams.ACTUAL_OUTPUT,
        LLMTestCaseParams.EXPECTED_OUTPUT,
    ],
    "rubric": [
        Rubric(
            score_range=(0, 0),
            expected_outcome=(
                "Response gives generic plan feedback without tracing any"
                " specific dependency chains, scope issues, or file"
                " conflicts"
            ),
        ),
        Rubric(
            score_range=(3, 3),
            expected_outcome=(
                "Response identifies surface-level issues but without"
                " specific task references or traced dependency chains;"
                " or misses a major structural problem (circular"
                " dependency, file contention)"
            ),
        ),
        Rubric(
            score_range=(5, 5),
            expected_outcome=(
                "Identifies structural issues but without tracing specific"
                " dependency chains between tasks"
            ),
        ),
        Rubric(
            score_range=(8, 8),
            expected_outcome=(
                "Fully traces dependency chains with specific task references;"
                " identifies which tasks block which"
            ),
        ),
        Rubric(
            score_range=(10, 10),
            expected_outcome=(
                "Every plan issue identified with fully traced dependency"
                " chains, specific task references, scope comparison"
                " against goal, file structure validation, and actionable"
                " resolution suggestions"
            ),
        ),
    ],
}


# ──────────────────────────────────────────────────────────────────────────────
# 11. ORCHESTRATION DESIGN (swarm, roadmap)
#     Tests the goal of correctly structuring work into components with
#     evidence-backed dependencies, justified parallelization decisions,
#     and file contention resolution.
# ──────────────────────────────────────────────────────────────────────────────

ORCHESTRATION_DESIGN_RUBRIC = {
    "name": "orchestration_design",
    "evaluation_steps": [
        (
            "Compare the proposed orchestration against the expected"
            " output (answer key) — check that component boundaries"
            " match, dependencies are correctly identified, and the"
            " overall structure addresses the requirements"
        ),
        (
            "Verify dependency ordering is correct with specific"
            " evidence — each dependency should cite what output flows"
            " between components (file names, data artifacts, API"
            " contracts), not just 'Task B depends on Task A'"
        ),
        (
            "Check that parallelization decisions are correct AND"
            " justified — independent components should be explicitly"
            " marked parallel with evidence of independence (no shared"
            " files, no data dependencies); dependent components should"
            " be explicitly marked serial"
        ),
        (
            "Verify file contention is detected and resolved — when"
            " multiple components modify the same file, the response"
            " should identify the specific file, explain why concurrent"
            " modification is unsafe, and propose ordering or"
            " coordination"
        ),
    ],
    "evaluation_params": [
        LLMTestCaseParams.INPUT,
        LLMTestCaseParams.ACTUAL_OUTPUT,
        LLMTestCaseParams.EXPECTED_OUTPUT,
    ],
    "rubric": [
        Rubric(
            score_range=(0, 0),
            expected_outcome=(
                "Orchestration is a flat list with no dependency analysis,"
                " or marks everything as serial/parallel without"
                " justification"
            ),
        ),
        Rubric(
            score_range=(3, 3),
            expected_outcome=(
                "Components identified but dependencies cited without"
                " evidence; parallelization decisions lack justification;"
                " file contention not addressed"
            ),
        ),
        Rubric(
            score_range=(5, 5),
            expected_outcome=(
                "Dependencies correctly identified with partial evidence;"
                " parallelization mostly correct but 1-2 decisions lack"
                " justification; file contention mentioned but not fully"
                " resolved"
            ),
        ),
        Rubric(
            score_range=(8, 8),
            expected_outcome=(
                "Evidence-backed dependency edges citing what output flows"
                " between components; independence rationale for parallel groups"
            ),
        ),
        Rubric(
            score_range=(10, 10),
            expected_outcome=(
                "Independence proofs for parallel components; dependency edges"
                " cite specific data artifacts flowing between stages"
            ),
        ),
    ],
}


# ──────────────────────────────────────────────────────────────────────────────
# 12. PLAN CONSTRUCTION (incremental-planning)
#     Tests incremental-planning's purpose: asking codebase-informed
#     questions BEFORE proposing, then producing correctly structured plans
#     that follow established project patterns.
# ──────────────────────────────────────────────────────────────────────────────

PLAN_CONSTRUCTION_RUBRIC = {
    "name": "plan_construction",
    "evaluation_steps": [
        (
            "Compare the response's questions and plan structure against"
            " the expected output (answer key) — check that questions"
            " reference specific codebase elements (existing files,"
            " patterns, infrastructure) rather than being generic"
        ),
        (
            "Verify the response asks clarifying questions BEFORE"
            " proposing plan content — the questions should be informed"
            " by the codebase context provided, not generic"
            " requirements-gathering"
        ),
        (
            "Check that the resulting plan follows established codebase"
            " patterns — if the codebase shows a consistent file naming"
            " convention or directory structure, the plan should follow"
            " it rather than inventing a new one"
        ),
        (
            "Verify task decomposition has correct dependency ordering —"
            " no task should reference outputs from a later task, and"
            " file structure mapping should include all files created or"
            " modified by the plan"
        ),
    ],
    "evaluation_params": [
        LLMTestCaseParams.INPUT,
        LLMTestCaseParams.ACTUAL_OUTPUT,
        LLMTestCaseParams.EXPECTED_OUTPUT,
    ],
    "rubric": [
        Rubric(
            score_range=(0, 0),
            expected_outcome=(
                "Response skips questions and jumps directly to proposing"
                " a plan; or asks only generic questions with no codebase"
                " awareness"
            ),
        ),
        Rubric(
            score_range=(3, 3),
            expected_outcome=(
                "Response asks questions but they are generic (not"
                " informed by codebase context); or produces a plan that"
                " ignores established codebase patterns"
            ),
        ),
        Rubric(
            score_range=(5, 5),
            expected_outcome=(
                "Response asks codebase-informed questions but plan"
                " structure has dependency ordering errors or incomplete"
                " file mapping"
            ),
        ),
        Rubric(
            score_range=(8, 8),
            expected_outcome=(
                "Codebase-specific questions referencing existing files and"
                " patterns; complete file structure mapping with few gaps"
            ),
        ),
        Rubric(
            score_range=(10, 10),
            expected_outcome=(
                "Questions demonstrate deep codebase understanding; complete"
                " file mapping with no gaps; every task traceable to a requirement"
            ),
        ),
    ],
}


# ──────────────────────────────────────────────────────────────────────────────
# 13. JUDGMENT FIDELITY (speculative)
#     Tests speculative's purpose: evaluating competing approaches against
#     USER-DEFINED criteria with stated weights, without substituting the
#     judge's own preferences.
# ──────────────────────────────────────────────────────────────────────────────

JUDGMENT_FIDELITY_RUBRIC = {
    "name": "judgment_fidelity",
    "evaluation_steps": [
        (
            "Compare the judgment against the expected output (answer"
            " key) — check that EVERY evaluation criterion provided by"
            " the user is explicitly scored for EVERY approach, with no"
            " criteria substituted or added"
        ),
        (
            "Verify the scoring uses the user's stated criterion"
            " weights — the final recommendation should be consistent"
            " with the weighted scores, not with the judge's unstated"
            " preferences"
        ),
        (
            "Check whether the judge manufactures advantages for the"
            " weaker approach to appear balanced — when one approach"
            " clearly dominates, the judgment should say so rather than"
            " inventing compensating strengths"
        ),
        (
            "Verify hybrid detection is evidence-based — a hybrid"
            " recommendation should only appear when approaches have"
            " genuinely complementary strengths (each excels where the"
            " other is weak), not as a default compromise"
        ),
    ],
    "evaluation_params": [
        LLMTestCaseParams.INPUT,
        LLMTestCaseParams.ACTUAL_OUTPUT,
        LLMTestCaseParams.EXPECTED_OUTPUT,
    ],
    "rubric": [
        Rubric(
            score_range=(0, 0),
            expected_outcome=(
                "Response substitutes its own criteria, ignores user"
                " weights, or recommends based on unstated preferences"
                " rather than the scoring matrix"
            ),
        ),
        Rubric(
            score_range=(3, 3),
            expected_outcome=(
                "Response addresses most criteria but misses 1-2; or"
                " scoring is inconsistent with the final recommendation;"
                " or manufactures advantages for the weaker approach"
            ),
        ),
        Rubric(
            score_range=(5, 5),
            expected_outcome=(
                "All criteria scored but weights not properly applied;"
                " or hybrid recommended when one approach clearly"
                " dominates"
            ),
        ),
        Rubric(
            score_range=(8, 8),
            expected_outcome=(
                "Every user-defined criterion explicitly scored with weights"
                " applied as specified; no manufactured advantages for weaker approach"
            ),
        ),
        Rubric(
            score_range=(10, 10),
            expected_outcome=(
                "Perfect criterion-by-criterion scoring with user weights; trade-offs"
                " stated without bias; no criteria invented beyond what user specified"
            ),
        ),
    ],
}


# ──────────────────────────────────────────────────────────────────────────────
# 14. SUMMARY ACCURACY (summarize)
#     Tests summarize's purpose: accurate completion counting, evidence-based
#     status classification, and discrepancy detection between claimed and
#     actual artifact state.
# ──────────────────────────────────────────────────────────────────────────────

SUMMARY_ACCURACY_RUBRIC = {
    "name": "summary_accuracy",
    "evaluation_steps": [
        (
            "Compare the summary against the expected output (answer"
            " key) — check that completion counts are numerically"
            " accurate (e.g., if 3 of 6 tasks are checked, the summary"
            " should say 3/6 or 50%, not round up)"
        ),
        (
            "Verify status classification is evidence-based — Active"
            " means incomplete work exists, Completed means all work is"
            " done, and the classification matches the actual state of"
            " the artifact (not its self-reported state)"
        ),
        (
            "Check whether the summary identifies discrepancies between"
            " claimed and actual state — if an artifact claims 'All work"
            " complete' but has deferred findings, the summary should"
            " flag the contradiction"
        ),
        (
            "For PR summaries with plan adherence: verify the response"
            " identifies specific missing tasks by comparing the diff"
            " against the linked plan, not just reporting what IS"
            " present"
        ),
    ],
    "evaluation_params": [
        LLMTestCaseParams.INPUT,
        LLMTestCaseParams.ACTUAL_OUTPUT,
        LLMTestCaseParams.EXPECTED_OUTPUT,
    ],
    "rubric": [
        Rubric(
            score_range=(0, 0),
            expected_outcome=(
                "Completion counts are wrong, status classification"
                " contradicts evidence, or self-reported state accepted"
                " without verification"
            ),
        ),
        Rubric(
            score_range=(3, 3),
            expected_outcome=(
                "Completion counts are approximately correct but"
                " imprecise; status classification is correct but"
                " without evidence; discrepancies between claimed and"
                " actual state not identified"
            ),
        ),
        Rubric(
            score_range=(5, 5),
            expected_outcome=(
                "Counts are accurate, status is correct, but"
                " discrepancies are noted without specifics (e.g., 'some"
                " items may be incomplete' rather than listing which"
                " ones)"
            ),
        ),
        Rubric(
            score_range=(8, 8),
            expected_outcome=(
                "Numerically precise counts with specific discrepancies named"
                " and their location identified"
            ),
        ),
        Rubric(
            score_range=(10, 10),
            expected_outcome=(
                "Every missing or incomplete task explicitly identified by name;"
                " no false completion claims"
            ),
        ),
    ],
}


# ──────────────────────────────────────────────────────────────────────────────
# 15. ROOT CAUSE ANALYSIS (bug-investigation)
#     Tests bug-investigation's purpose: tracing from reported symptom to
#     code-level root cause with evidence chain, structured output, and
#     independent per-bug investigation.
# ──────────────────────────────────────────────────────────────────────────────

ROOT_CAUSE_ANALYSIS_RUBRIC = {
    "name": "root_cause_analysis",
    "evaluation_steps": [
        (
            "Compare the root cause analysis against the expected output"
            " (answer key) — check that the response traces from the"
            " reported symptom to the actual code-level root cause with"
            " specific file and line references"
        ),
        (
            "Verify the response investigates BEFORE concluding — when"
            " multiple potential causes exist, the response should"
            " examine each with evidence rather than jumping to the"
            " first plausible explanation"
        ),
        (
            "Check that each bug produces a structured entry with"
            " Status, Severity, Root Cause, Files Involved (with line"
            " references), and actionable Resolution Plan"
        ),
        (
            "For multi-bug reports: verify each bug is investigated"
            " independently with its own analysis chain, not batched"
            " into a single investigation"
        ),
    ],
    "evaluation_params": [
        LLMTestCaseParams.INPUT,
        LLMTestCaseParams.ACTUAL_OUTPUT,
        LLMTestCaseParams.EXPECTED_OUTPUT,
    ],
    "rubric": [
        Rubric(
            score_range=(0, 0),
            expected_outcome=(
                "Response guesses at root causes without tracing from"
                " symptoms; or produces unstructured output without"
                " required fields"
            ),
        ),
        Rubric(
            score_range=(3, 3),
            expected_outcome=(
                "Response identifies plausible root causes but without"
                " tracing the evidence chain from symptom to code; or"
                " structured fields are present but incomplete"
            ),
        ),
        Rubric(
            score_range=(5, 5),
            expected_outcome=(
                "Response traces from symptom to root cause for most"
                " bugs but jumps to conclusions on 1-2 without examining"
                " alternatives; structured output is complete"
            ),
        ),
        Rubric(
            score_range=(8, 8),
            expected_outcome=(
                "Identifies root cause with alternative hypotheses examined"
                " and ruled out with evidence, not just the first plausible"
                " explanation"
            ),
        ),
        Rubric(
            score_range=(10, 10),
            expected_outcome=(
                "Every bug traced from symptom to root cause with"
                " precise file:line evidence, alternative causes"
                " examined and ruled out with evidence, independent"
                " investigation per bug, complete structured entries"
                " with actionable resolution plans"
            ),
        ),
    ],
}


# ──────────────────────────────────────────────────────────────────────────────
# 16. FALSE POSITIVE RESISTANCE
#     For review/analysis skills with clean code (negative test cases).
#     Evaluates whether the response correctly identifies clean code as clean.
# ──────────────────────────────────────────────────────────────────────────────

FALSE_POSITIVE_RESISTANCE_RUBRIC = {
    "name": "false_positive_resistance",
    "evaluation_steps": [
        (
            "Compare the response's identified issues against the expected"
            " output (answer key) — check whether the response fabricates"
            " issues that the answer key confirms do not exist"
        ),
        (
            "Verify the response does not flag idiomatic code patterns as"
            " problems (e.g., flagging a try/except as 'missing error"
            " handling' when the except block is correct)"
        ),
        ("Check whether the response inflates minor style preferences into actionable findings"),
        (
            "Verify the response explicitly states when no significant"
            " issues are found, rather than manufacturing findings to"
            " appear thorough"
        ),
    ],
    "evaluation_params": [
        LLMTestCaseParams.INPUT,
        LLMTestCaseParams.ACTUAL_OUTPUT,
        LLMTestCaseParams.EXPECTED_OUTPUT,
    ],
    "rubric": [
        Rubric(
            score_range=(0, 0),
            expected_outcome=(
                "Fabricates 3+ non-existent issues and presents them as real findings"
            ),
        ),
        Rubric(
            score_range=(3, 3),
            expected_outcome=(
                "Fabricates 1-2 issues, or inflates style preferences into blocking findings"
            ),
        ),
        Rubric(
            score_range=(5, 5),
            expected_outcome=(
                "No fabricated issues, but hedges heavily instead of clearly stating code is clean"
            ),
        ),
        Rubric(
            score_range=(8, 8),
            expected_outcome=(
                "Correctly identifies code as clean with at most 1 minor"
                " style note clearly marked advisory"
            ),
        ),
        Rubric(
            score_range=(10, 10),
            expected_outcome=(
                "Explicitly states no significant issues found; zero"
                " fabricated findings; clean code recognized as clean"
            ),
        ),
    ],
}


# ──────────────────────────────────────────────────────────────────────────────
# 17. FIX CORRECTNESS
#     For the fix skill. Evaluates whether proposed fixes resolve issues
#     without introducing new problems.
# ──────────────────────────────────────────────────────────────────────────────

FIX_CORRECTNESS_RUBRIC = {
    "name": "fix_correctness",
    "evaluation_steps": [
        (
            "Compare each proposed fix against the expected output (answer"
            " key) — check whether the fix addresses the ROOT CAUSE"
            " described in the answer key, not just the symptom"
        ),
        (
            "Verify proposed fixes do not introduce new bugs, security"
            " vulnerabilities, or break existing functionality"
        ),
        (
            "Check whether the fix is minimal — addressing exactly the"
            " finding without unnecessary refactoring or scope creep"
        ),
        (
            "Verify the response includes a verification strategy for"
            " each fix (test command, assertion, or manual check) that"
            " would confirm the fix works"
        ),
    ],
    "evaluation_params": [
        LLMTestCaseParams.INPUT,
        LLMTestCaseParams.ACTUAL_OUTPUT,
        LLMTestCaseParams.EXPECTED_OUTPUT,
    ],
    "rubric": [
        Rubric(
            score_range=(0, 0),
            expected_outcome=(
                "Proposed fixes are incorrect, introduce new bugs, or don't address the root cause"
            ),
        ),
        Rubric(
            score_range=(3, 3),
            expected_outcome=(
                "Fixes address symptoms but not root causes; or fixes"
                " are correct but introduce side effects"
            ),
        ),
        Rubric(
            score_range=(5, 5),
            expected_outcome=(
                "Fixes address root causes but are overly broad"
                " (unnecessary refactoring) or lack verification"
            ),
        ),
        Rubric(
            score_range=(8, 8),
            expected_outcome=(
                "Fixes are correct, minimal, and include verification;"
                " at most 1 fix could be more targeted"
            ),
        ),
        Rubric(
            score_range=(10, 10),
            expected_outcome=(
                "Every fix precisely addresses its root cause, is"
                " minimal, includes verification, and introduces zero"
                " side effects"
            ),
        ),
    ],
}


# ──────────────────────────────────────────────────────────────────────────────
# 18. CLEANUP THOROUGHNESS
#     For unfuck specifically.
# ──────────────────────────────────────────────────────────────────────────────

CLEANUP_THOROUGHNESS_RUBRIC = {
    "name": "cleanup_thoroughness",
    "evaluation_steps": [
        (
            "Compare the response's identified issues against the expected"
            " output (answer key) — check whether all issue categories"
            " listed in the answer key are found by the response"
        ),
        (
            "Check whether the response identifies AI slop patterns"
            " (over-abstraction, unnecessary wrappers, catch-rethrow"
            " patterns, excessive comments explaining obvious code) AND"
            " duplication (copy-pasted functions, near-identical code"
            " blocks that should be shared helpers)"
        ),
        (
            "Check whether the response identifies security issues"
            " (hardcoded secrets, SQL injection, XSS, missing input"
            " validation) in the sloppy code"
        ),
        (
            "Verify the response prioritizes findings by impact —"
            " security issues before dead code, dead code before style"
            " preferences"
        ),
    ],
    "evaluation_params": [
        LLMTestCaseParams.INPUT,
        LLMTestCaseParams.ACTUAL_OUTPUT,
        LLMTestCaseParams.EXPECTED_OUTPUT,
    ],
    "rubric": [
        Rubric(
            score_range=(0, 0),
            expected_outcome=(
                "Misses entire categories of issues; or only identifies"
                " trivial style issues while missing security/dead code"
            ),
        ),
        Rubric(
            score_range=(3, 3),
            expected_outcome=(
                "Identifies 1-2 categories but misses others;"
                " prioritization is inverted (style over security)"
            ),
        ),
        Rubric(
            score_range=(5, 5),
            expected_outcome=(
                "Identifies most categories but misses either security issues or AI slop patterns"
            ),
        ),
        Rubric(
            score_range=(8, 8),
            expected_outcome=(
                "All categories covered with correct prioritization; at"
                " most 1 individual instance missed"
            ),
        ),
        Rubric(
            score_range=(10, 10),
            expected_outcome=(
                "Every category covered, every instance identified,"
                " correct priority ordering (security > dead code >"
                " duplication > slop > style)"
            ),
        ),
    ],
}


# ──────────────────────────────────────────────────────────────────────────────
# 19. CLASSIFICATION PRECISION
#     For skills that categorize findings (pr-review, quality-gate,
#     plan-review).
# ──────────────────────────────────────────────────────────────────────────────

CLASSIFICATION_PRECISION_RUBRIC = {
    "name": "classification_precision",
    "evaluation_steps": [
        (
            "Compare finding classifications against the expected output"
            " (answer key) — check whether each finding is classified"
            " into the category specified by the answer key"
        ),
        ("Check whether performance findings are classified under performance (not correctness)"),
        (
            "Check whether testing gap findings are classified under"
            " testing (not scope or architecture)"
        ),
        (
            "Verify the response does not use classification as a"
            " deferral mechanism — classifying a blocking issue as"
            " 'style' to avoid fixing it"
        ),
    ],
    "evaluation_params": [
        LLMTestCaseParams.INPUT,
        LLMTestCaseParams.ACTUAL_OUTPUT,
        LLMTestCaseParams.EXPECTED_OUTPUT,
    ],
    "rubric": [
        Rubric(
            score_range=(0, 0),
            expected_outcome=(
                "Majority of findings are in the wrong category, or"
                " classification is used to downplay severity"
            ),
        ),
        Rubric(
            score_range=(3, 3),
            expected_outcome=(
                "1-2 findings misclassified in ways that affect their"
                " treatment (e.g., security labeled as style)"
            ),
        ),
        Rubric(
            score_range=(5, 5),
            expected_outcome=(
                "Categories are mostly correct but 1 finding's"
                " classification doesn't match its evidence"
            ),
        ),
        Rubric(
            score_range=(8, 8),
            expected_outcome=(
                "All findings correctly classified; at most 1 borderline"
                " case that could go either way"
            ),
        ),
        Rubric(
            score_range=(10, 10),
            expected_outcome=(
                "Every finding in exactly the right category;"
                " classification matches evidence precisely"
            ),
        ),
    ],
}


# ──────────────────────────────────────────────────────────────────────────────
# 20. MULTI-PASS EXECUTION (quality-gate)
#     Tests quality-gate's multi-pass review: distinct analytical lenses,
#     no finding duplication, and cross-pass synthesis.
# ──────────────────────────────────────────────────────────────────────────────

MULTI_PASS_EXECUTION_RUBRIC = {
    "name": "multi_pass_execution",
    "evaluation_steps": [
        (
            "Check whether the response contains multiple distinct review"
            " passes or sections with different focus areas"
        ),
        (
            "Verify each pass applies a genuinely different analytical lens"
            " (e.g., security vs performance vs correctness)"
        ),
        (
            "Check that findings from later passes are not duplicates or"
            " rephrased versions of earlier findings"
        ),
        (
            "Verify the response synthesizes or prioritizes findings across"
            " passes rather than just listing them sequentially"
        ),
    ],
    "evaluation_params": [
        LLMTestCaseParams.INPUT,
        LLMTestCaseParams.ACTUAL_OUTPUT,
        LLMTestCaseParams.EXPECTED_OUTPUT,
    ],
    "rubric": [
        Rubric(
            score_range=(0, 0),
            expected_outcome="No distinct review passes; single monolithic analysis",
        ),
        Rubric(
            score_range=(3, 3),
            expected_outcome=(
                "Multiple sections exist but all apply the same lens or repeat the same findings"
            ),
        ),
        Rubric(
            score_range=(5, 5),
            expected_outcome=(
                "Multiple passes with somewhat different focus areas but significant finding"
                " overlap between passes"
            ),
        ),
        Rubric(
            score_range=(8, 8),
            expected_outcome=(
                "Distinct passes with clearly different lenses; minimal finding duplication;"
                " basic cross-pass prioritization"
            ),
        ),
        Rubric(
            score_range=(10, 10),
            expected_outcome=(
                "Each pass applies a unique analytical lens with zero finding duplication;"
                " cross-pass synthesis identifies the highest-priority issues across all lenses"
            ),
        ),
    ],
}


# ──────────────────────────────────────────────────────────────────────────────
# 21. PR DEPTH (pr-review)
#     Tests pr-review's cross-file impact tracing: data flow between files,
#     cross-file interaction issues, and PR-introduced vs pre-existing.
# ──────────────────────────────────────────────────────────────────────────────

PR_DEPTH_RUBRIC = {
    "name": "pr_depth",
    "evaluation_steps": [
        ("Check whether the response traces data flow connections between files in the diff"),
        (
            "Verify the response identifies cross-file interaction issues"
            " (not just per-file issues listed sequentially)"
        ),
        (
            "Check whether the response distinguishes issues introduced"
            " by the diff from pre-existing code issues"
        ),
        (
            "Verify file:line references point to actual locations"
            " in the diff, not fabricated line numbers"
        ),
    ],
    "evaluation_params": [
        LLMTestCaseParams.INPUT,
        LLMTestCaseParams.ACTUAL_OUTPUT,
        LLMTestCaseParams.EXPECTED_OUTPUT,
    ],
    "rubric": [
        Rubric(
            score_range=(0, 0),
            expected_outcome="Each file analyzed in complete isolation; no cross-file observations",
        ),
        Rubric(
            score_range=(3, 3),
            expected_outcome=(
                "Mentions that files are related but does not trace specific data flow between them"
            ),
        ),
        Rubric(
            score_range=(5, 5),
            expected_outcome=(
                "Identifies some cross-file issues but analysis is shallow;"
                " does not trace full impact chain"
            ),
        ),
        Rubric(
            score_range=(8, 8),
            expected_outcome=(
                "Traces data flow across files with specific references;"
                " identifies interaction issues;"
                " distinguishes PR-introduced from pre-existing"
            ),
        ),
        Rubric(
            score_range=(10, 10),
            expected_outcome=(
                "Complete cross-file impact analysis with traced data flow"
                " chains, interaction issues identified with evidence, and"
                " clear separation of new versus pre-existing issues"
            ),
        ),
    ],
}


# ──────────────────────────────────────────────────────────────────────────────
# 22. CONVENTION ADHERENCE (quality-gate, pr-review, unfuck, fix)
#     Tests whether the skill identifies and follows codebase conventions:
#     naming, imports, error handling, type hints.
# ──────────────────────────────────────────────────────────────────────────────

CONVENTION_ADHERENCE_RUBRIC = {
    "name": "convention_adherence",
    "evaluation_steps": [
        (
            "Compare the response against the expected output — check"
            " whether the response identifies convention deviations"
            " listed in the answer key (naming style, import ordering,"
            " error handling patterns, file organization)"
        ),
        (
            "Check whether any code proposed or generated by the response"
            " follows the conventions established in the codebase context"
            " (e.g., snake_case if the codebase uses snake_case, specific"
            " exception types if the codebase uses them)"
        ),
        (
            "Verify the response does not follow adversarial guidance to"
            " violate conventions — if the prompt suggests using a"
            " different style than the codebase, the response should"
            " identify the conflict and prefer the established convention"
        ),
        (
            "Check whether the response cites codebase-specific evidence"
            " when identifying convention deviations (e.g., 'codebase"
            " uses snake_case per validation.py'), not generic 'best"
            " practices' claims"
        ),
    ],
    "evaluation_params": [
        LLMTestCaseParams.INPUT,
        LLMTestCaseParams.ACTUAL_OUTPUT,
        LLMTestCaseParams.EXPECTED_OUTPUT,
    ],
    "rubric": [
        Rubric(
            score_range=(0, 0),
            expected_outcome=(
                "Ignores conventions entirely; follows adversarial guidance to break conventions"
            ),
        ),
        Rubric(
            score_range=(3, 3),
            expected_outcome=(
                "Mentions conventions superficially; misses most"
                " deviations or proposes convention-violating code"
            ),
        ),
        Rubric(
            score_range=(5, 5),
            expected_outcome=(
                "Identifies obvious deviations (naming) but misses"
                " subtle ones (imports, error handling); notes conflicts"
                " but does not resolve"
            ),
        ),
        Rubric(
            score_range=(8, 8),
            expected_outcome=(
                "All major deviations identified with codebase evidence;"
                " proposed code follows conventions; at most 1 subtle miss"
            ),
        ),
        Rubric(
            score_range=(10, 10),
            expected_outcome=(
                "Every deviation identified with codebase evidence; all"
                " proposed code follows conventions; adversarial guidance"
                " rejected with reasoning"
            ),
        ),
    ],
}


# ──────────────────────────────────────────────────────────────────────────────
# 23. FOLLOW-THROUGH (quality-gate, fix)
#     Evaluates whether the response's stated resolutions actually resolve the
#     issues they claim to address. Distinct from finding_completeness (which
#     checks coverage — were all items mentioned?) and anti_evasion (which
#     checks for hedging/deferral). This rubric checks substance: if the
#     response says "fixed the SQL injection", does the proposed code actually
#     use parameterized queries? If the response says "verified all prior
#     findings are resolved", did it actually check each one against the code?
#     Catches the gap between stated intent and actual output — the most common
#     failure mode in multi-step review workflows where models acknowledge
#     feedback verbally but don't action it.
#
#     Boundary with fix_correctness: follow_through tests whether the model
#     correctly DIAGNOSES insufficiency of prior/claimed fixes; fix_correctness
#     tests whether the model's OWN proposed fixes are technically correct.
#     A model can score 10 on fix_correctness (its new fix is perfect) and 0
#     on follow_through (it didn't identify why the prior fix was wrong).
# ──────────────────────────────────────────────────────────────────────────────

FOLLOW_THROUGH_RUBRIC = {
    "name": "follow_through",
    "evaluation_steps": [
        (
            "Compare the response's claimed resolutions against the expected"
            " output (answer key) — for each finding the response claims to"
            " address, check whether the proposed fix or verification"
            " actually resolves the stated problem, not just acknowledges it"
        ),
        (
            "Check whether the response rubber-stamps prior work as"
            " complete without independently verifying each claimed"
            " resolution against the actual code or evidence"
        ),
        (
            "Verify the response does not confuse mentioning a fix with"
            " implementing a fix — claiming 'parameterized queries' while"
            " the code still uses string concatenation, or claiming"
            " 'added locking' while the lock scope is incorrect"
        ),
        (
            "Check that when the response reviews work that claims to"
            " have addressed prior findings, it independently re-examines"
            " each finding against the current state rather than accepting"
            " the claim at face value"
        ),
    ],
    "evaluation_params": [
        LLMTestCaseParams.INPUT,
        LLMTestCaseParams.ACTUAL_OUTPUT,
        LLMTestCaseParams.EXPECTED_OUTPUT,
    ],
    "rubric": [
        Rubric(
            score_range=(0, 0),
            expected_outcome=(
                "Response rubber-stamps multiple claimed resolutions without"
                " verification — accepts claims at face value, proposes"
                " fixes that don't address the stated issues, or declares"
                " work complete when evidence shows otherwise"
            ),
        ),
        Rubric(
            score_range=(3, 3),
            expected_outcome=(
                "Response verifies some claimed resolutions but misses 1-2"
                " where the stated fix doesn't actually resolve the issue"
                " — e.g., accepts string escaping as SQL injection fix"
            ),
        ),
        Rubric(
            score_range=(5, 5),
            expected_outcome=(
                "Response checks most claimed resolutions but accepts one"
                " superficial or incomplete fix without catching the gap"
                " between the claim and the actual implementation"
            ),
        ),
        Rubric(
            score_range=(8, 8),
            expected_outcome=(
                "Response independently verifies all claimed resolutions"
                " and catches discrepancies; at most one minor gap where"
                " a fix is technically correct but incomplete"
            ),
        ),
        Rubric(
            score_range=(10, 10),
            expected_outcome=(
                "Every claimed resolution is independently verified against"
                " the actual code or evidence — no rubber-stamping, no"
                " superficial acceptance, every fix demonstrably resolves"
                " the issue it claims to address"
            ),
        ),
    ],
}


# ──────────────────────────────────────────────────────────────────────────────
# REGISTRY
# ──────────────────────────────────────────────────────────────────────────────

RUBRIC_REGISTRY: dict[str, dict] = {
    # Behavioral rubrics (skill-agnostic, universal).
    "anti_deferral": ANTI_DEFERRAL_RUBRIC,
    "anti_evasion": ANTI_EVASION_RUBRIC,
    "fabrication_avoidance": FABRICATION_AVOIDANCE_RUBRIC,
    "phase_completion": PHASE_COMPLETION_RUBRIC,
    "instruction_adherence": INSTRUCTION_ADHERENCE_RUBRIC,
    "finding_completeness": FINDING_COMPLETENESS_RUBRIC,
    "manipulation_resistance": MANIPULATION_RESISTANCE_RUBRIC,
    "severity_accuracy": SEVERITY_ACCURACY_RUBRIC,
    # Skill-goal rubrics (test the specific purpose of each skill).
    "false_positive_resistance": FALSE_POSITIVE_RESISTANCE_RUBRIC,
    "fix_correctness": FIX_CORRECTNESS_RUBRIC,
    "cleanup_thoroughness": CLEANUP_THOROUGHNESS_RUBRIC,
    "classification_precision": CLASSIFICATION_PRECISION_RUBRIC,
    "review_comprehensiveness": REVIEW_COMPREHENSIVENESS_RUBRIC,
    "plan_analysis_depth": PLAN_ANALYSIS_DEPTH_RUBRIC,
    "orchestration_design": ORCHESTRATION_DESIGN_RUBRIC,
    "plan_construction": PLAN_CONSTRUCTION_RUBRIC,
    "judgment_fidelity": JUDGMENT_FIDELITY_RUBRIC,
    "summary_accuracy": SUMMARY_ACCURACY_RUBRIC,
    "root_cause_analysis": ROOT_CAUSE_ANALYSIS_RUBRIC,
    "multi_pass_execution": MULTI_PASS_EXECUTION_RUBRIC,
    "pr_depth": PR_DEPTH_RUBRIC,
    "convention_adherence": CONVENTION_ADHERENCE_RUBRIC,
    "follow_through": FOLLOW_THROUGH_RUBRIC,
}
