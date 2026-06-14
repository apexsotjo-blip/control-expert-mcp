"""THE BIG TEST: recreate a plant-style project from scratch exercising one of
everything, then commission it on the PLC simulator.

Covers: M580 project, local + remote (EIO/CRA) hardware, Modbus DTM with IP +
scan line, DDT + DFB types, logic in ST/LD/FBD/SFC/IL, animation table,
build, save, simulator download + RUN.
"""

import json
import sys
import uuid

sys.path.insert(0, r"..\src")

import win32com.client  # noqa: E402

from control_expert_mcp.bridge import ControlExpertBridge, CEError  # noqa: E402
from control_expert_mcp.lang_reference import REFERENCES  # noqa: E402

HDR = (
    '<fileHeader company="Schneider Automation" product="Control Expert V14.0 - 190112" '
    'dateTime="date_and_time#2026-6-11-13:0:0" content="{content}" DTDVersion="41"></fileHeader>\n'
    '\t<contentHeader name="Project" version="0.0.000"></contentHeader>'
)

DDT_XML = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<DDTExchangeFile>
\t{HDR.format(content="DDT source file")}
\t<DDTSource DDTName="T_PUMP" version="0.01">
\t\t<structure>
\t\t\t<variables name="Run" typeName="BOOL"></variables>
\t\t\t<variables name="Speed" typeName="REAL"></variables>
\t\t\t<variables name="Hours" typeName="DINT"></variables>
\t\t</structure>
\t</DDTSource>
</DDTExchangeFile>
"""

DFB_XML = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<FBExchangeFile>
\t{HDR.format(content="Function Block source file")}
\t<FBSource nameOfFBType="FB_SCALE" version="0.01">
\t\t<inputParameters>
\t\t\t<variables name="RAW" typeName="INT"><attribute name="PositionPin" value="1"></attribute></variables>
\t\t\t<variables name="LORAW" typeName="INT"><attribute name="PositionPin" value="2"></attribute></variables>
\t\t\t<variables name="HIRAW" typeName="INT"><attribute name="PositionPin" value="3"></attribute></variables>
\t\t\t<variables name="LOEU" typeName="REAL"><attribute name="PositionPin" value="4"></attribute></variables>
\t\t\t<variables name="HIEU" typeName="REAL"><attribute name="PositionPin" value="5"></attribute></variables>
\t\t</inputParameters>
\t\t<outputParameters>
\t\t\t<variables name="PV" typeName="REAL"><attribute name="PositionPin" value="1"></attribute></variables>
\t\t\t<variables name="ER" typeName="BOOL"><attribute name="PositionPin" value="2"></attribute></variables>
\t\t</outputParameters>
\t\t<FBProgram name="FB_SCALE">
\t\t\t<STSource>IF HIRAW = LORAW THEN
\tER := TRUE; PV := LOEU;
ELSE
\tER := FALSE;
\tPV := (HIEU - LOEU) / INT_TO_REAL(HIRAW - LORAW) * INT_TO_REAL(RAW - LORAW) + LOEU;
\tIF PV &lt; LOEU THEN PV := LOEU; END_IF;
\tIF PV &gt; HIEU THEN PV := HIEU; END_IF;
END_IF;
</STSource>
\t\t</FBProgram>
\t</FBSource>
</FBExchangeFile>
"""

IL_XML = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<ILExchangeFile>
\t{HDR.format(content="IL source file")}
\t<program>
\t\t<identProgram name="IL_Demo" type="section" task="MAST"></identProgram>
\t\t<ILSource>(* lamp test in IL *)
LD LampTest
ST TestLamp
</ILSource>
\t</program>
\t<dataBlock>
\t\t<variables name="LampTest" typeName="BOOL"></variables>
\t\t<variables name="TestLamp" typeName="BOOL"></variables>
\t</dataBlock>
</ILExchangeFile>
"""

MB_REQUEST = (
    '<ManagedModbusRequest Index="0" State="0" FromExtTool="1">\n'
    '\t\t\t\t\t\t\t\t\t<Parameters requestSettingBit="1" requestSettingUnitID="255" '
    'requestSettingTimeout="1500" requestSettingRepetitiveRate="60" '
    'requestSettingRDAddress="0" requestSettingRDLength="10" '
    'requestSettingLastValue="0" requestSettingWRAddress="0" '
    'requestSettingWRLength="10" '
    f'requestUniqueID="{uuid.uuid4()}" '
    'requestInputObjID="1024" requestOutputObjID="1025" '
    'requestReservedInputObjID="65535" requestReservedOutputObjID="1025"/>\n'
    "\t\t\t\t\t\t\t\t</ManagedModbusRequest>"
)

b = ControlExpertBridge()
report: list[str] = []


def step(label, fn):
    try:
        r = fn()
        msg = f"OK   {label}"
        if isinstance(r, dict):
            msg += f" -> {json.dumps(r, default=str)[:160]}"
        print(msg)
        report.append(msg)
        return r
    except CEError as e:
        msg = f"FAIL {label}: {str(e)[:220]}"
        print(msg)
        report.append(msg)
        return None


print("=== 1. project ===")
step("new_project M580", lambda: {"cpu": b.new_project("BME P58 2040", "02.70", "GMA_Recreate")["project"]["cpu"]})

print("\n=== 2. hardware ===")
step("local AMI 0810 @1", lambda: b.add_io_module("BMX AMI 0810", 1, "01.00", 0, None, None))
step("local DDI 1602 @2", lambda: b.add_io_module("BMX DDI 1602", 2, "02.00", 0, None, None))


def add_eio_drop():
    def inner():
        buses = b._buses()
        eio = None
        for bus in b._iter_qi(buses, "IBus"):
            nm = str(bus.Name)
            if "EIO" in nm or "RIO" in nm:
                eio = bus
                break
        if eio is None:
            raise CEError("no EIO bus found on this CPU")
        drops = b._wrap(b._get_prop(eio, "Drops"))
        drop = b._wrap(b._get_prop(drops, "AddChild", 1, 0, "M580 Drop for Ethernet", "01.00"))
        drop = b._qi(drop, "IDrop")
        racks = b._wrap(b._get_prop(drop, "Racks"))
        rack = b._wrap(b._get_prop(racks, "AddChild", 0, 0, "BME XBP 1200", "01.00"))
        rack = b._qi(rack, "IRack")
        mods = b._wrap(b._get_prop(rack, "Modules"))
        b._get_prop(mods, "AddChild", 0, 0, "BME CRA 312 10.2", "01.00")
        b._get_prop(mods, "AddChild", 1, 0, "BMX AMI 0810", "01.00")
        return {"drop": str(drop.Name), "rack": str(rack.Name)}

    return b._run(inner)


step("EIO drop + CRA + AMI", add_eio_drop)

print("\n=== 3. Modbus DTM ===")
step("add Modbus Device", lambda: b.add_dtm("Modbus Device", "Pump_1", "BMEP58_ECPU_EXT", "", "", ""))
step("set IP", lambda: b.set_dtm_address("Pump_1", "20.10.21.2"))


def add_scanline():
    ds = b.get_master_dtm_dataset(None)
    xml = ds["xml"]
    marker = "<ManagedModbusRequestList>"
    i = xml.find(marker)
    if i < 0:
        raise CEError("no request list in dataset")
    new = xml[: i + len(marker)] + "\n\t\t\t\t\t\t\t\t\t" + MB_REQUEST + xml[i + len(marker):]
    return b.set_master_dtm_dataset(new, None)


step("Modbus scan line", add_scanline)

print("\n=== 4. types ===")
step("DDT T_PUMP", lambda: b.import_xml(DDT_XML, None, "ddt", None, "overwrite"))
step("DFB FB_SCALE", lambda: b.import_xml(DFB_XML, None, "dfb", None, "overwrite"))

print("\n=== 5. variables ===")
for name, typ in (
    ("Pump1", "T_PUMP"), ("Scale1", "FB_SCALE"), ("RawAI", "INT"),
    ("StartPB", "BOOL"), ("StopPB", "BOOL"), ("Motor", "BOOL"),
    ("IdleLamp", "BOOL"), ("RunLamp", "BOOL"),
    ("PumpReady", "BOOL"), ("FaultActive", "BOOL"), ("PumpRun", "BOOL"),
    ("SeqStart", "BOOL"), ("FillDone", "BOOL"), ("DrainDone", "BOOL"),
):
    step(f"var {name}", lambda n=name, t=typ: b.create_variable(n, t, None, None, None))

print("\n=== 6. logic in all 5 languages ===")
step("ST supervisor", lambda: b.write_st_logic(
    "MAST", "Supervisor",
    "Scale1(RAW := RawAI, LORAW := 0, HIRAW := 27648, LOEU := 0.0, HIEU := 100.0);\n"
    "Pump1.Speed := Scale1.PV;\n"
    "Pump1.Run := Motor;\n"
    "PumpReady := NOT Scale1.ER;\n"
    "IF Pump1.Run THEN Pump1.Hours := Pump1.Hours + 1; END_IF;\n",
    None,
))
step("LD section", lambda: b.import_xml(REFERENCES["LD"]["example"], None, "section", "MAST", "overwrite"))
step("FBD section", lambda: b.import_xml(REFERENCES["FBD"]["example"], None, "section", "MAST", "overwrite"))
step("SFC section", lambda: b.import_xml(REFERENCES["SFC"]["example"], None, "section", "MAST", "overwrite"))
step("IL section", lambda: b.import_xml(IL_XML, None, "section", "MAST", "overwrite"))

print("\n=== 7. animation table ===")
step("AT_Commission", lambda: b.create_animation_table(
    "AT_Commission",
    ["StartPB", "StopPB", "Motor", "RunLamp", "RawAI", "Scale1.PV",
     "Pump1.Run", "Pump1.Speed", "SeqStart", "FillDone", "DrainDone", "PumpRun"],
))

print("\n=== 8. build + save ===")
r = step("build", lambda: b.build_project(True))
if r and r.get("build_state") != "built_ok":
    print((r.get("output") or "")[-1200:])
step("save", lambda: b.save_project(r"C:\Users\TOJG\Desktop\MCP server\demo-projects\GMA_Recreate.stu"))

print("\n=== 9. simulator ===")
step("setup sim", lambda: b.plc_setup_connection("simulator", "127.0.0.1", None))
step("connect sim", lambda: b.plc_connect("simulator", "primary"))
step("download", lambda: b.plc_transfer("pc_to_plc"))
step("RUN", lambda: b.plc_command("run"))
step("state", lambda: b.plc_state())

print("\n=== SUMMARY ===")
ok = sum(1 for line in report if line.startswith("OK"))
print(f"{ok}/{len(report)} steps OK")
for line in report:
    if line.startswith("FAIL"):
        print(line)
print("ONE-OF-EVERYTHING DONE")
