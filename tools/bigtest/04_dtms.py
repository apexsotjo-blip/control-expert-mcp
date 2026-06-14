"""Phase 3: Modbus slave DTMs, addresses, gateways, scan lines, CPU Ethernet."""

import re
import uuid

from common import ControlExpertBridge, WORK, kill_strays, log_weak, ref_text, step

kill_strays()
b = ControlExpertBridge()
b.open_project(WORK)

ref_ds = ref_text("master_dataset.xml")

# -- parse reference slave devices (tag -> device name, addr, gw, subnet, order)
slaves = {}
for m in re.finditer(r'<SlaveDevice deviceTag="([^"]+)".*?</SlaveDevice>', ref_ds, re.S):
    blk = m.group(0)
    tag = m.group(1)
    devname = re.search(r'DeviceName="([^"]*)"', blk).group(1)
    num = int(re.search(r'DeviceNum="(\d+)"', blk).group(1))
    p = re.search(
        r'slaveDeviceAddressID="([^"]*)"[^>]*slaveDeviceSubnetID="([^"]*)"'
        r'[^>]*slaveDeviceGatewayID="([^"]*)"', blk)
    reqs = re.search(r"<ManagedModbusRequestList>.*?</ManagedModbusRequestList>", blk, re.S)
    slaves[tag] = {
        "device": devname, "num": num,
        "addr": p.group(1) if p else None,
        "subnet": p.group(2) if p else None,
        "gw": p.group(3) if p else None,
        "requests": reqs.group(0) if reqs else None,
    }

dtms_now = b.list_dtms()
have = {c.get("name") for c in (dtms_now.get("dtms") or [{}])[0].get("children", []) or []}
print("DTM children already present:", sorted(have))

todo = sorted(
    (t for t, s in slaves.items()
     if s["device"] in ("Modbus Device", "STB NIP2x1x") and t not in have),
    key=lambda t: slaves[t]["num"])
print(f"slaves to add ({len(todo)}):", todo)

for tag in todo:
    s = slaves[tag]
    step(f"add {tag} ({s['device']})",
         lambda tag=tag, s=s: b.add_dtm(s["device"], tag, "BMEP58_ECPU_EXT", "Modbus", "", ""))
    if s["addr"]:
        step(f"  addr {s['addr']}", lambda tag=tag, s=s: b.set_dtm_address(tag, s["addr"], None, None))

# -- one dataset round-trip: gateways/subnets + scan lines for every slave
md = b.get_master_dtm_dataset(None)
ds = md.get("xml") or open(md["file"], encoding="utf-8").read()


def patch_slave(m):
    blk = m.group(0)
    tag = m.group(1)
    s = slaves.get(tag)
    if not s:
        return blk
    if s["gw"]:
        blk = re.sub(r'(slaveDeviceGatewayID=")[^"]*(")', rf"\g<1>{s['gw']}\g<2>", blk)
    if s["subnet"]:
        blk = re.sub(r'(slaveDeviceSubnetID=")[^"]*(")', rf"\g<1>{s['subnet']}\g<2>", blk)
    if s["requests"]:
        fresh = re.sub(r'(requestUniqueID=")[0-9a-fA-F-]+(")',
                       lambda _m: f"{_m.group(1)}{uuid.uuid4()}{_m.group(2)}", s["requests"])
        if "<ManagedModbusRequestList>" in blk:
            blk = re.sub(r"<ManagedModbusRequestList>.*?</ManagedModbusRequestList>",
                         lambda _m: fresh, blk, flags=re.S)
        else:
            blk = blk.replace("</ModbusTCP>", fresh + "</ModbusTCP>")
    return blk


new_ds, n = re.subn(r'<SlaveDevice deviceTag="([^"]+)".*?</SlaveDevice>', patch_slave, ds, flags=re.S)
print(f"dataset: patched {n} slave blocks")
step("write master dataset (gateways + scan lines)",
     lambda: {k: v for k, v in b.set_master_dtm_dataset(new_ds, None).items() if k != "note"})

# -- CPU Ethernet (ref TcpSettings + all security services on)
step("configure_cpu_ethernet", lambda: {
    "changed": b.configure_cpu_ethernet(
        "20.10.21.16", "255.255.255.224", "20.10.21.30", "20.10.21.18",
        {"tftp": True, "eipServer": True, "dhcp_bootp": True, "ftp": True,
         "webServer": True, "snmp": True},
        ip_b="20.10.21.19", ip_d="20.10.21.17")["changed"]})

r = step("save", lambda: b.save_project(WORK))
if r is None:
    # 'Cannot access file' after archive reimports — save under a temp name,
    # then swap files after close.
    alt = WORK.replace(".stu", "_tmp.stu")
    r = step("save (alt path)", lambda: b.save_project(alt), weak_on_fail=False)
    if r:
        import os
        b.close_project(False)
        os.replace(alt, WORK)
        print("swapped temp save into", WORK)
        b.open_project(WORK)

after = b.list_dtms()
kids = (after.get("dtms") or [{}])[0].get("children", []) or []
print(f"DTM children now: {len(kids)} (ref: 19)")
dt = b.list_data_types()
print(f"types now: {len(dt.get('ddts', []))} DDTs (ref 86)")
b.close_project(False)
print("PHASE3 DONE")
