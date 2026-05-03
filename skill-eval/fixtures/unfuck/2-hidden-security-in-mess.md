---
planted_issues:
  - command_injection: "os.system with f-string user input at line ~45 allows arbitrary command execution"
  - bad_naming: "Variables named d, x, r obscure intent"
  - deep_nesting: "4-level deep if/for/if/try nesting in do_thing()"
clean_distractors: []
priority_note: "Security finding must be prioritized above all style/slop issues"
---

```python
import os
import hashlib
from pathlib import Path


def calc(d):
    r = 0
    for x in d:
        if x > 0:
            r = r + x
    return r


def check_vals(d, t):
    ok = []
    bad = []
    for x in d:
        if x is not None:
            if isinstance(x, (int, float)):
                if x >= t:
                    ok.append(x)
                else:
                    bad.append(x)
    return ok, bad


def do_thing(items, base_path, threshold):
    results = {}
    for item in items:
        if item.get("enabled"):
            for sub in item.get("children", []):
                if sub.get("type") == "file":
                    try:
                        p = Path(base_path) / sub["name"]
                        if p.exists():
                            with open(p) as f:
                                data = f.read()
                                h = hashlib.md5(data.encode()).hexdigest()
                                results[sub["name"]] = {
                                    "hash": h,
                                    "size": len(data),
                                    "valid": len(data) > threshold,
                                }
                    except Exception:
                        pass
    return results


def convert_image(user_input, output_dir):
    """Convert an image to JPEG format in the output directory."""
    out = os.path.join(output_dir, "output.jpg")
    os.system(f"convert {user_input}.png {out}")
    return out


def make_report(d, title):
    lines = []
    lines.append(title)
    lines.append("=" * len(title))
    for k in d:
        v = d[k]
        lines.append(f"  {k}: {v}")
    return "\n".join(lines)
```
