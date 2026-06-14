"""Phase 1: project + full hardware from the inventory (no reference files).

Order matters: the CRA comm head must be the FIRST module added on a remote
EIO rack (before PSU/IO), or every later AddChild fails with 'service has
failed'."""

from common import ControlExpertBridge, WORK, kill_strays, step

kill_strays()
b = ControlExpertBridge()

step("new project BME H58 2040 (HSBY)",
     lambda: {"cpu": b.new_project("BME H58 2040", "02.80", "DR001 Dabouq Reservoir")["project"]["cpu"]})

# new_project defaults: BME XBP 0800 rack + BMX CPS 4002 PSU; ref uses 0400 + 2010
step("replace local PSU -> CPS 2010",
     lambda: b.replace_io_module(-1, "", "", "BMX CPS 2010", "01.00", 0, None, None))
step("replace local rack -> XBP 0400",
     lambda: b.replace_rack(0, "", "", "BME XBP 0400", "01.00", None, None))

step("EIO drop topo 1", lambda: b.add_drop("EIO", 1, "M580 Drop for Ethernet", "01.00"))
step("EIO rack XBP 1200", lambda: b.add_rack("EIO", 1, 0, "BME XBP 1200", "01.00"))
step("CRA @0 (head first)", lambda: b.add_io_module("BME CRA 312 10.2", 0, "01.00", 0, 1, "EIO"))
for slot, pn, ver in ((1, "BMX AMI 0810", "01.00"), (2, "BMX AMI 0810", "01.00"),
                      (3, "BME AHI 0812", "01.00"), (4, "BME AHI 0812", "01.00"),
                      (6, "BMX AMO 0410", "01.00"), (8, "BMX DDI 6402K", "02.00"),
                      (9, "BMX DDI 1602", "02.00"), (10, "BMX DDO 3202K", "02.00")):
    step(f"EIO @{slot} {pn}", lambda pn=pn, slot=slot, ver=ver:
         b.add_io_module(pn, slot, ver, 0, 1, "EIO"))
step("EIO PSU", lambda: b.add_io_module("BMX CPS 2010", -1, "01.00", 0, 1, "EIO"))

step("save", lambda: b.save_project(WORK))

hw2 = b.get_hardware()
for bus in hw2["buses"]:
    print("BUS", bus["name"])
    for d in bus.get("drops", []):
        for r in d.get("racks", []):
            print(f"  DROP {d.get('toponumber')} RACK {r.get('partnumber')}")
            for m in r.get("modules", []):
                print(f"   [{m.get('toponumber')}] {m.get('partnumber')} v{m.get('version')}")
b.close_project(False)
print("PHASE1 DONE")
