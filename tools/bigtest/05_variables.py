"""Phase 4: create all user variables (device-DDT instances are auto)."""

from common import ControlExpertBridge, WORK, kill_strays, log_weak, ref_json

kill_strays()
b = ControlExpertBridge()
b.open_project(WORK)

ref_vars = ref_json("variables.json")["variables"]
have = {v["name"] for v in b.list_variables(None, 2000)["variables"]}
todo = [v for v in ref_vars
        if not v.get("type", "").startswith("T_") and v["name"] not in have]
print(f"to create: {len(todo)} (ref total {len(ref_vars)}, already {len(have)})")

ok = 0
fails = []
for v in todo:
    try:
        b.create_variable(v["name"], v["type"], v.get("comment"),
                          v.get("address"), v.get("initial_value"))
        ok += 1
    except Exception as e:  # noqa: BLE001
        fails.append((v, str(e)[:140]))

print(f"created {ok}/{len(todo)}")
for v, err in fails:
    log_weak(f"create_variable {v['name']} ({v['type']}"
             f"{' @' + v['address'] if v.get('address') else ''}): {err}")

now = b.list_variables(None, 2000)
print(f"variables now: {now['total_variables']} (ref {len(ref_vars)})")
b.save_project(None)
b.close_project(False)
print("PHASE4 DONE")
