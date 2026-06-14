"""Proof: author a UF-unit Normal Operation sequence (per CP24-IWT-03) in SFC
through the same surfaces an AI client uses: get_language_reference shapes,
write_st_logic, import_xml, build_project."""

import json
import sys

sys.path.insert(0, r"..\src")

from control_expert_mcp.bridge import ControlExpertBridge  # noqa: E402

STEPS = [
    ("S_Service", "initialStep", "ServiceDone"),
    ("S_AirInlet", "step", "AirInletDone"),
    ("S_Draining", "step", "DrainDone"),
    ("S_BWBottom", "step", "BWBottomDone"),
    ("S_BWTop", "step", "BWTopDone"),
    ("S_FwdFlush", "step", "FwdFlushDone"),
]

chart = []
for i, (name, stype, _cond) in enumerate(STEPS):
    action = ""
    if i == 0:
        action = (
            "\n\t\t\t\t\t<action qualifier=\"NONE\">"
            "\n\t\t\t\t\t\t<actionName>\n\t\t\t\t\t\t\t<sectionName>A_Service</sectionName>\n\t\t\t\t\t\t</actionName>"
            "\n\t\t\t\t\t\t<tValue>\n\t\t\t\t\t\t\t<tLiteral></tLiteral>\n\t\t\t\t\t\t</tValue>"
            "\n\t\t\t\t\t</action>"
        )
    chart.append(
        f'\t\t\t\t<step stepType="{stype}" stepName="{name}">\n'
        f'\t\t\t\t\t<objPosition posX="0" posY="{i * 2}"></objPosition>{action}\n'
        f'\t\t\t\t\t<literals max="" min="" delay=""></literals>\n'
        f"\t\t\t\t</step>"
    )
for i, (_name, _stype, cond) in enumerate(STEPS):
    chart.append(
        f"\t\t\t\t<transition>\n"
        f'\t\t\t\t\t<objPosition posX="0" posY="{i * 2 + 1}"></objPosition>\n'
        f'\t\t\t\t\t<transitionCondition invertLogic="false">\n'
        f"\t\t\t\t\t\t<variableName>{cond}</variableName>\n"
        f"\t\t\t\t\t</transitionCondition>\n"
        f"\t\t\t\t</transition>"
    )
chart.append(
    f'\t\t\t\t<jumpSFC stepName="S_Service">\n'
    f'\t\t\t\t\t<objPosition posX="0" posY="{len(STEPS) * 2}"></objPosition>\n'
    f"\t\t\t\t</jumpSFC>"
)

SFC_XML = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    "<SFCExchangeFile>\n"
    '\t<fileHeader company="Schneider Automation" product="Control Expert V14.0 - 190112" '
    'dateTime="date_and_time#2026-6-11-12:0:0" content="SFC source file" DTDVersion="41"></fileHeader>\n'
    '\t<contentHeader name="Project" version="0.0.000"></contentHeader>\n'
    '\t<SFCProgram areaNum="0" operatorCtrl="0">\n'
    '\t\t<identProgram name="UF1_Sequence" type="section" task="MAST"></identProgram>\n'
    '\t\t<chartSource name="Chart">\n'
    "\t\t\t<networkSFC>\n" + "\n".join(chart) + "\n\t\t\t</networkSFC>\n"
    "\t\t</chartSource>\n"
    '\t\t<actionSource name="A_Service">\n'
    "\t\t\t<STSource>UF1_InService := TRUE;\n</STSource>\n"
    "\t\t</actionSource>\n"
    "\t</SFCProgram>\n"
    "\t<dataBlock>\n"
    + "".join(f'\t\t<variables name="{c}" typeName="BOOL"></variables>\n' for _, _, c in STEPS)
    + '\t\t<variables name="UF1_InService" typeName="BOOL"></variables>\n'
    "\t</dataBlock>\n"
    "</SFCExchangeFile>\n"
)

SUPERVISOR_ST = """(* UF unit 1 step supervisor: computes transition flags per CP24-IWT-03 *)
ServiceDone  := UF1_Start AND (S_Service.t  >= ServiceTime);
AirInletDone := S_AirInlet.t >= t#60s;
DrainDone    := S_Draining.t >= t#30s;
BWBottomDone := S_BWBottom.t >= t#30s;
BWTopDone    := S_BWTop.t    >= t#30s;
FwdFlushDone := S_FwdFlush.t >= t#60s;

(* outputs per step *)
FeedPump_Run  := S_Service.x OR S_FwdFlush.x;
BWPump_Run    := S_BWBottom.x OR S_BWTop.x;
Blower_Run    := S_AirInlet.x;
AirInletValve := S_AirInlet.x;
"""

b = ControlExpertBridge()
b.new_project("BMX P34 2020", "02.70", "UFProof")
print("import SFC:", json.dumps(b.import_xml(SFC_XML, None, "section", "MAST", "overwrite")))
print("write supervisor:", json.dumps(b.write_st_logic(
    "MAST", "UF1_Supervisor", SUPERVISOR_ST,
    {
        "UF1_Start": "BOOL", "ServiceTime": "TIME",
        "FeedPump_Run": "BOOL", "BWPump_Run": "BOOL",
        "Blower_Run": "BOOL", "AirInletValve": "BOOL",
    },
)))
r = b.build_project(True)
print("build_state:", r["build_state"])
print(r.get("output", "")[-500:])

if len(sys.argv) > 1 and sys.argv[1] == "--save":
    import os

    out_dir = os.path.abspath(os.path.join("..", "..", "demo-projects"))
    os.makedirs(out_dir, exist_ok=True)
    stu = os.path.join(out_dir, "UF_Sequence_Demo.stu")
    xef = os.path.join(out_dir, "UF_Sequence_Demo.xef")
    print("save:", json.dumps(b.save_project(stu)))
    print("export:", json.dumps(b.export_project(xef)))
b.close_project(False)
print("UF PROOF DONE")
