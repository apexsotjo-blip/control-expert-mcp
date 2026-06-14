"""Phase 6: build and report full diagnostics."""

from common import ControlExpertBridge, WORK, kill_strays

kill_strays()
b = ControlExpertBridge()
b.open_project(WORK)
r = b.build_project(True)
print("state:", r.get("build_state"))
out = r.get("output") or ""
for line in out.splitlines():
    t = line.strip()
    if t and "0 error(s), 0 warning(s)" not in t:
        print(" |", t[:220])
if r.get("build_state") == "built_ok":
    b.save_project(None)
    print("SAVED")
b.close_project(False)
print("PHASE6 DONE")
