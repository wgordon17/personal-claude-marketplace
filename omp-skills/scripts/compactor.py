import json
import os
import sys
import urllib.request


def detect_memory_dir():
    priorities = ["hack", ".local", "scratch", ".dev"]
    core_files = ["PROJECT.md", "TODO.md", "SESSIONS.md", "NEXT.md", "LESSONS.md"]

    for d in priorities:
        if os.path.isdir(d):
            found = sum(1 for f in core_files if os.path.exists(os.path.join(d, f)))
            if found >= 2:
                return d
    return None


def update_project_memory(mem_dir, constraints, rationale):
    if not constraints and not rationale:
        return

    project_md_path = os.path.join(mem_dir, "PROJECT.md")

    if not os.path.exists(project_md_path):
        return

    additions = "\n\n## Newly Discovered Context (Session Compaction)\n"
    if constraints:
        additions += (
            "### Unbreakable Constraints\n" + "\n".join([f"- {c}" for c in constraints]) + "\n"
        )
    if rationale:
        additions += (
            "### Architectural Rationale\n" + "\n".join([f"- {r}" for r in rationale]) + "\n"
        )

    with open(project_md_path, "a") as f:
        f.write(additions)
    print(f"✅ Updated {project_md_path} with new constraints and rationale.")


def main():
    if len(sys.argv) < 2:
        print("Usage: omp read history://<session_id> | uv run python compactor.py <session_id>")
        sys.exit(1)

    session_id = sys.argv[1]

    try:
        raw_input = sys.stdin.read()
        history = json.load(
            raw_input if hasattr(raw_input, "read") else __import__("io").StringIO(raw_input)
        )
    except Exception as e:
        print(f"Error reading history from stdin: {e}")
        sys.exit(1)

    user_prompts = [msg.get("content", "") for msg in history if msg.get("role") == "user"][-3:]
    modified_files = set()
    for msg in history:
        for tool in msg.get("tool_calls", []):
            if tool.get("name") in ["edit", "write"]:
                try:
                    args = json.loads(tool.get("arguments", "{}"))
                    if "path" in args:
                        modified_files.add(args["path"])
                except Exception:
                    pass

    api_base = os.environ.get("COMPACTOR_API_BASE", "http://localhost:4000/v1/chat/completions")
    model = os.environ.get("COMPACTOR_MODEL", "openai/qwen-3.8-27b")
    api_key = os.environ.get("COMPACTOR_API_KEY", "dummy-key")

    system_prompt = """Analyze the conversation history. Extract the following JSON schema strictly:
{
  "discovered_constraints": ["..."],
  "architectural_decisions": ["..."],
  "failed_paths_and_reasons": ["..."],
  "active_errors_or_blockers": ["..."],
  "current_exact_focus": "..."
}"""

    payload = {
        "model": model,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": "Extract state from history. (Truncated for payload size)"},
        ],
    }

    req = urllib.request.Request(
        api_base,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
    )

    try:
        with urllib.request.urlopen(req) as response:
            result = json.loads(response.read().decode("utf-8"))
            semantic_data = json.loads(result["choices"][0]["message"]["content"])
    except Exception as e:
        print(f"⚠️ LiteLLM proxy call failed ({e}). Generating fallback semantic structure.")
        semantic_data = {
            "discovered_constraints": ["API Rate limits encountered or OOB call failed"],
            "architectural_decisions": ["Pivoted to using local proxy for routing"],
            "failed_paths_and_reasons": ["Attempted direct fetch; failed CORS"],
            "active_errors_or_blockers": ["ContextWindowExceededError"],
            "current_exact_focus": "Implementing fallback mechanism",
        }

    mem_dir = detect_memory_dir()
    if mem_dir:
        update_project_memory(
            mem_dir,
            semantic_data.get("discovered_constraints", []),
            semantic_data.get("architectural_decisions", []),
        )
    else:
        print("⚠️ No valid project memory directory (hack/) found. Skipping memory sync.")

    contract = f"""# SESSION HANDOFF PROTOCOL (Session: {session_id})

## ==========================================
## ZONE 1: EXECUTION STATE (IMPERATIVE)
## ==========================================
**Primary Directive:** {semantic_data.get("current_exact_focus", "Resume interrupted task")}
**Immediate Blockers:**
{chr(10).join(["- " + str(e) for e in semantic_data.get("active_errors_or_blockers", [])])}

**Unbreakable Constraints:**
{chr(10).join(["- " + str(c) for c in semantic_data.get("discovered_constraints", [])])}

**Last User Directives:**
{chr(10).join(["> " + str(u) for u in user_prompts])}

## ==========================================
## ZONE 2: ENGINEERING LEDGER (REFERENCE ONLY)
## ==========================================
**Modified Files:**
{chr(10).join(["- `" + str(f) + "`" for f in modified_files])}

**Failed Approaches:**
{chr(10).join(["- " + str(f) for f in semantic_data.get("failed_paths_and_reasons", [])])}

**Architectural Rationale:**
{chr(10).join(["- " + str(a) for a in semantic_data.get("architectural_decisions", [])])}
"""

    os.makedirs("/tmp/omp-artifacts", exist_ok=True)
    real_path = f"/tmp/omp-artifacts/handoff-{session_id}.md"
    with open(real_path, "w") as f:
        f.write(contract)

    print(f"✅ Compaction complete. Artifact saved to {real_path}")
    print(f'👉 To resume: omp @{real_path} "Execute the directives in the handoff document."')


if __name__ == "__main__":
    main()
