"""Phase 6b: apply project settings (multiAssign, dynamicArray, timezone),
re-assert CPU ethernet (EIP on for HSBY), rebuild, separate errors/warnings."""

import json
import re

from common import ControlExpertBridge, WORK, kill_strays, step

kill_strays()
b = ControlExpertBridge()
b.open_project(WORK)

with open("settings_to_apply.json", encoding="utf-8") as f:
    to_apply = json.load(f)
print("applying settings:", to_apply)
step("set_project_settings", lambda: b.set_project_settings(to_apply))
# after a ZEF reload the project path is intact; save to persist
step("save after settings", lambda: b.save_project(WORK), weak_on_fail=False)


def build_report(label):
    r = b.build_project(True)
    out = r.get("output") or ""
    errors = [ln.strip() for ln in out.splitlines()
              if re.search(r"\bE\d{3,4}\b|[1-9]\d* [Ee]rror|not mapped on a device|"
                           r"must be checked|Process failed", ln)]
    print(f"\n{label}: {r.get('build_state')}")
    seen = set()
    for e in errors:
        key = re.sub(r"\d+", "#", e)[:80]
        if key not in seen:
            seen.add(key)
            print("  ERR:", e[:200])
    return r


build_report("build after settings")
b.save_project(WORK)
b.close_project(False)
print("PHASE6b DONE")
