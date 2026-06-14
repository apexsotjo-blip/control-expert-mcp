"""Validate DDT + DFB creation via exchange XML and animation table tools."""

import json
import sys

sys.path.insert(0, r"..\src")

from control_expert_mcp.bridge import ControlExpertBridge, CEError  # noqa: E402

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
\t\t\t<variables name="Run" typeName="BOOL">
\t\t\t\t<comment>Pump running</comment>
\t\t\t</variables>
\t\t\t<variables name="Speed" typeName="REAL">
\t\t\t\t<comment>Speed feedback</comment>
\t\t\t</variables>
\t\t\t<variables name="Hours" typeName="DINT">
\t\t\t\t<comment>Running hours</comment>
\t\t\t</variables>
\t\t</structure>
\t</DDTSource>
</DDTExchangeFile>
"""

DFB_XML = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<FBExchangeFile>
\t{HDR.format(content="Function Block source file")}
\t<FBSource nameOfFBType="FB_SCALE" version="0.01">
\t\t<comment>Scale a raw analog input to engineering units</comment>
\t\t<inputParameters>
\t\t\t<variables name="RAW" typeName="INT">
\t\t\t\t<comment>Raw value</comment>
\t\t\t\t<attribute name="PositionPin" value="1"></attribute>
\t\t\t</variables>
\t\t\t<variables name="LORAW" typeName="INT">
\t\t\t\t<attribute name="PositionPin" value="2"></attribute>
\t\t\t</variables>
\t\t\t<variables name="HIRAW" typeName="INT">
\t\t\t\t<attribute name="PositionPin" value="3"></attribute>
\t\t\t</variables>
\t\t\t<variables name="LOEU" typeName="REAL">
\t\t\t\t<attribute name="PositionPin" value="4"></attribute>
\t\t\t</variables>
\t\t\t<variables name="HIEU" typeName="REAL">
\t\t\t\t<attribute name="PositionPin" value="5"></attribute>
\t\t\t</variables>
\t\t</inputParameters>
\t\t<outputParameters>
\t\t\t<variables name="PV" typeName="REAL">
\t\t\t\t<comment>Scaled value</comment>
\t\t\t\t<attribute name="PositionPin" value="1"></attribute>
\t\t\t</variables>
\t\t\t<variables name="ER" typeName="BOOL">
\t\t\t\t<comment>Range error</comment>
\t\t\t\t<attribute name="PositionPin" value="2"></attribute>
\t\t\t</variables>
\t\t</outputParameters>
\t\t<FBProgram name="FB_SCALE">
\t\t\t<STSource>IF HIRAW = LORAW THEN
\tER := TRUE;
\tPV := LOEU;
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

b = ControlExpertBridge()
b.new_project("BME P58 2040", "02.70", "TypesVal")

for label, xml, kind in (("DDT", DDT_XML, "ddt"), ("DFB", DFB_XML, "dfb")):
    try:
        r = b.import_xml(xml, None, kind, None, "overwrite")
        print(f"{label} import OK via {r.get('via')}")
    except CEError as e:
        print(f"{label} import FAIL: {str(e)[:300]}")

print("types now:", json.dumps(b.list_data_types(), default=str)[:300])

# use them: a DDT instance + a DFB instance called from ST
b.create_variable("Pump1", "T_PUMP", "pump struct", None, None)
print("write logic:", json.dumps(b.write_st_logic(
    "MAST", "UseTypes",
    "Scale1(RAW := RawAI, LORAW := 0, HIRAW := 27648, LOEU := 0.0, HIEU := 100.0);\n"
    "Pump1.Speed := Scale1.PV;\nPump1.Run := Scale1.PV > 5.0;\n",
    {"Scale1": "FB_SCALE", "RawAI": "INT"},
)))

r = b.build_project(True)
print("build:", r["build_state"])
if r["build_state"] != "built_ok":
    print(r.get("output", "")[-800:])

print("anim:", json.dumps(b.create_animation_table(
    "AT_Test", ["Pump1.Run", "Pump1.Speed", "RawAI", "Scale1.PV"]
)))
print("tables:", json.dumps(b.list_animation_tables()))
b.close_project(False)
print("TYPES+ANIM VALIDATION DONE")
