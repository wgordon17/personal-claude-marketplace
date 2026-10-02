import concurrent.futures
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

    with open(project_md_path) as f:
        content = f.read()

    novel_constraints = [c for c in constraints if c not in content]
    novel_rationale = [r for r in rationale if r not in content]

    if not novel_constraints and not novel_rationale:
        print(f"✅ Insights already present in {project_md_path}. Skipping append.")
        return

    additions = ""
    if "## Session Compaction Inbox" not in content:
        additions += "\n\n## Session Compaction Inbox\n"
        additions += (
            "> **Note to Agent:** Consolidate these raw insights into the main architecture "
            "sections and clear this inbox during the next `/session-end`.\n"
        )

    if novel_constraints:
        additions += (
            "\n### Unbreakable Constraints\n"
            + "\n".join([f"- {c}" for c in novel_constraints])
            + "\n"
        )
    if novel_rationale:
        additions += (
            "\n### Architectural Rationale\n"
            + "\n".join([f"- {r}" for r in novel_rationale])
            + "\n"
        )

    with open(project_md_path, "a") as f:
        f.write(additions)
    print(f"✅ Appended novel insights to {project_md_path} Inbox.")


def main():
    if len(sys.argv) < 2:
        print("Usage: cat <session.jsonl> | uv run python compactor.py <session_id>")
        sys.exit(1)

    session_id = sys.argv[1]

    try:
        history = []
        for line in sys.stdin:
            line = line.strip()
            if line:
                history.append(json.loads(line))
        if not history:
            raise ValueError("Empty input")
    except Exception as e:
        print(f"Error reading history (JSONL) from stdin: {e}")
        sys.exit(1)

    user_prompts = [msg.get("content", "") for msg in history if msg.get("role") == "user"][-3:]
    modified_files = set()
    for msg in history:
        for tool in msg.get("tool_calls", []):
            if tool.get("name") in ["edit", "write"]:
                try:
                    raw_args = tool.get("arguments", {})
                    args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
                    if isinstance(args, dict) and "path" in args:
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

    semantic_data = {
        "discovered_constraints": [],
        "architectural_decisions": [],
        "failed_paths_and_reasons": [],
        "active_errors_or_blockers": [],
        "current_exact_focus": "",
    }

    chunks = []
    current_chunk = []
    current_size = 0
    for msg in history:
        # Deep Slicing: Prevent Single-Message Overflow
        if isinstance(msg.get("content"), str) and len(msg["content"]) > 10000:
            msg["content"] = (
                msg["content"][:4000]
                + "\n...[TRUNCATED FOR COMPACTION]...\n"
                + msg["content"][-4000:]
            )

        msg_str = json.dumps(msg)
        if current_size + len(msg_str) > 15000 and current_chunk:
            chunks.append(current_chunk)
            current_chunk = []
            current_size = 0
        current_chunk.append(msg)
        current_size += len(msg_str)
    if current_chunk:
        chunks.append(current_chunk)

    print(f"📦 Splitting history into {len(chunks)} chunks and mapping in parallel...")

    def process_chunk(idx_chunk):
        i, chunk = idx_chunk
        payload = {
            "model": model,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": f"Extract state from history chunk {i + 1}/{len(chunks)}:\n"
                    + json.dumps(chunk),
                },
            ],
        }

        req = urllib.request.Request(
            api_base,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        )

        try:
            # Network Timeout Enforced
            with urllib.request.urlopen(req, timeout=45) as response:
                result = json.loads(response.read().decode("utf-8"))
                return json.loads(result["choices"][0]["message"]["content"]), i
        except Exception as e:
            print(f"⚠️ Chunk {i + 1} failed ({e}).")
            return None, i

    # Parallel Processing
    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        for res, i in executor.map(process_chunk, enumerate(chunks)):
            if res:
                results.append((i, res))

    results.sort(key=lambda x: x[0])

    for _, chunk_data in results:
        # Type Safety: Handle None lists
        semantic_data["discovered_constraints"].extend(
            chunk_data.get("discovered_constraints") or []
        )
        semantic_data["architectural_decisions"].extend(
            chunk_data.get("architectural_decisions") or []
        )
        semantic_data["failed_paths_and_reasons"].extend(
            chunk_data.get("failed_paths_and_reasons") or []
        )
        semantic_data["active_errors_or_blockers"].extend(
            chunk_data.get("active_errors_or_blockers") or []
        )
        if chunk_data.get("current_exact_focus"):
            semantic_data["current_exact_focus"] = chunk_data.get("current_exact_focus")

    if len(chunks) == 0 or not semantic_data["current_exact_focus"]:
        print("⚠️ Failed to extract full semantic state. Generating fallback structure.")
        semantic_data["current_exact_focus"] = "Implementing fallback mechanism"
        semantic_data["active_errors_or_blockers"].append("Context extraction partially failed.")

    mem_dir = detect_memory_dir()
    if mem_dir:
        update_project_memory(
            mem_dir,
            semantic_data.get("discovered_constraints", []),
            semantic_data.get("architectural_decisions", []),
        )
    else:
        print(
            "⚠️ No valid project memory directory (hack/) found or it failed "
            "the 2-stage check. Skipping memory sync."
        )

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

    artifact_dir = os.path.expanduser("~/.omp/artifacts")
    os.makedirs(artifact_dir, mode=0o700, exist_ok=True)
    real_path = os.path.join(artifact_dir, f"handoff-{session_id}.md")

    # Security: Create file with strict permissions (owner read/write only)
    fd = os.open(real_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(contract)

    print(f"✅ Compaction complete. Artifact saved to {real_path}")
    print(f'👉 To resume: omp @{real_path} "Execute the directives in the handoff document."')


if __name__ == "__main__":
    main()
