"""Craft + validate minimal LD / FBD / SFC authoring templates (import + build)."""

import json
import sys

sys.path.insert(0, r"..\src")

from control_expert_mcp.bridge import ControlExpertBridge, CEError  # noqa: E402

HDR = (
    '<fileHeader company="Schneider Automation" product="Control Expert V14.0 - 190112" '
    'dateTime="date_and_time#2026-6-11-12:0:0" content="{content}" DTDVersion="41"></fileHeader>\n'
    '\t<contentHeader name="Project" version="0.0.000"></contentHeader>'
)

# --- LD: rung 1 = Start AND NOT Stop -> set Motor + reset Idle (fan-out via VLink)
#     rung 2 = open contact Motor -> coil RunLamp (serial + plain HLink test)
LD_XML = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<LDExchangeFile>
\t{HDR.format(content="Ladder source file")}
\t<program>
\t\t<identProgram name="LD_Demo" type="section" task="MAST"></identProgram>
\t\t<LDSource nbColumns="11">
\t\t\t<networkLD>
\t\t\t\t<typeLine>
\t\t\t\t\t<contact typeContact="openContact" contactVariableName="StartPB"></contact>
\t\t\t\t\t<contact typeContact="closedContact" contactVariableName="StopPB"></contact>
\t\t\t\t\t<shortCircuit>
\t\t\t\t\t\t<VLink></VLink>
\t\t\t\t\t\t<HLink nbCells="8"></HLink>
\t\t\t\t\t</shortCircuit>
\t\t\t\t\t<coil typeCoil="setCoil" coilVariableName="Motor"></coil>
\t\t\t\t</typeLine>
\t\t\t\t<typeLine>
\t\t\t\t\t<emptyCell nbCells="10"></emptyCell>
\t\t\t\t\t<coil typeCoil="resetCoil" coilVariableName="IdleLamp"></coil>
\t\t\t\t</typeLine>
\t\t\t\t<typeLine>
\t\t\t\t\t<emptyLine nbRows="1"></emptyLine>
\t\t\t\t</typeLine>
\t\t\t\t<typeLine>
\t\t\t\t\t<contact typeContact="openContact" contactVariableName="Motor"></contact>
\t\t\t\t\t<HLink nbCells="9"></HLink>
\t\t\t\t\t<coil typeCoil="coil" coilVariableName="RunLamp"></coil>
\t\t\t\t</typeLine>
\t\t\t</networkLD>
\t\t</LDSource>
\t</program>
\t<dataBlock>
\t\t<variables name="StartPB" typeName="BOOL"></variables>
\t\t<variables name="StopPB" typeName="BOOL"></variables>
\t\t<variables name="Motor" typeName="BOOL"></variables>
\t\t<variables name="IdleLamp" typeName="BOOL"></variables>
\t\t<variables name="RunLamp" typeName="BOOL"></variables>
\t</dataBlock>
</LDExchangeFile>
"""

# --- FBD: AND block -> TON timer -> RS flipflop
FBD_XML = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<FBDExchangeFile>
\t{HDR.format(content="Derived Function Block source file")}
\t<program>
\t\t<identProgram name="FBD_Demo" type="section" task="MAST"></identProgram>
\t\t<FBDSource nbRows="24" nbColumns="36">
\t\t\t<networkFBD>
\t\t\t\t<FFBBlock instanceName=".1" typeName="AND_BOOL" additionnalPinNumber="0" enEnO="false" width="8" height="6">
\t\t\t\t\t<objPosition posX="3" posY="3"></objPosition>
\t\t\t\t\t<descriptionFFB execAfter="">
\t\t\t\t\t\t<inputVariable invertedPin="false" formalParameter="EN"></inputVariable>
\t\t\t\t\t\t<inputVariable invertedPin="false" formalParameter="IN1" effectiveParameter="PumpReady"></inputVariable>
\t\t\t\t\t\t<inputVariable invertedPin="true" formalParameter="IN2" effectiveParameter="FaultActive"></inputVariable>
\t\t\t\t\t\t<outputVariable invertedPin="false" formalParameter="ENO"></outputVariable>
\t\t\t\t\t\t<outputVariable invertedPin="false" formalParameter="OUT"></outputVariable>
\t\t\t\t\t</descriptionFFB>
\t\t\t\t</FFBBlock>
\t\t\t\t<FFBBlock instanceName="StartDelay" typeName="TON" additionnalPinNumber="0" enEnO="false" width="8" height="6">
\t\t\t\t\t<objPosition posX="14" posY="3"></objPosition>
\t\t\t\t\t<descriptionFFB execAfter="">
\t\t\t\t\t\t<inputVariable invertedPin="false" formalParameter="EN"></inputVariable>
\t\t\t\t\t\t<inputVariable invertedPin="false" formalParameter="IN"></inputVariable>
\t\t\t\t\t\t<inputVariable invertedPin="false" formalParameter="PT" effectiveParameter="t#3s"></inputVariable>
\t\t\t\t\t\t<outputVariable invertedPin="false" formalParameter="ENO"></outputVariable>
\t\t\t\t\t\t<outputVariable invertedPin="false" formalParameter="Q" effectiveParameter="PumpRun"></outputVariable>
\t\t\t\t\t\t<outputVariable invertedPin="false" formalParameter="ET"></outputVariable>
\t\t\t\t\t</descriptionFFB>
\t\t\t\t</FFBBlock>
\t\t\t\t<linkFB>
\t\t\t\t\t<linkSource parentObjectName=".1" pinName="OUT">
\t\t\t\t\t\t<objPosition posX="10" posY="7"></objPosition>
\t\t\t\t\t</linkSource>
\t\t\t\t\t<linkDestination parentObjectName="StartDelay" pinName="IN">
\t\t\t\t\t\t<objPosition posX="14" posY="7"></objPosition>
\t\t\t\t\t</linkDestination>
\t\t\t\t</linkFB>
\t\t\t</networkFBD>
\t\t</FBDSource>
\t</program>
\t<dataBlock>
\t\t<variables name="PumpReady" typeName="BOOL"></variables>
\t\t<variables name="FaultActive" typeName="BOOL"></variables>
\t\t<variables name="PumpRun" typeName="BOOL"></variables>
\t\t<variables name="StartDelay" typeName="TON"></variables>
\t</dataBlock>
</FBDExchangeFile>
"""

# --- SFC: Init -> Fill -> Drain -> jump Init, ST actions, variable transitions
SFC_XML = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<SFCExchangeFile>
\t{HDR.format(content="SFC source file")}
\t<SFCProgram areaNum="0" operatorCtrl="0">
\t\t<identProgram name="SFC_Demo" type="section" task="MAST"></identProgram>
\t\t<chartSource name="Chart">
\t\t\t<networkSFC>
\t\t\t\t<step stepType="initialStep" stepName="S_Init">
\t\t\t\t\t<objPosition posX="0" posY="0"></objPosition>
\t\t\t\t\t<action qualifier="NONE">
\t\t\t\t\t\t<actionName>
\t\t\t\t\t\t\t<sectionName>A_Init</sectionName>
\t\t\t\t\t\t</actionName>
\t\t\t\t\t\t<tValue>
\t\t\t\t\t\t\t<tLiteral></tLiteral>
\t\t\t\t\t\t</tValue>
\t\t\t\t\t</action>
\t\t\t\t\t<literals max="" min="" delay=""></literals>
\t\t\t\t</step>
\t\t\t\t<step stepType="step" stepName="S_Fill">
\t\t\t\t\t<objPosition posX="0" posY="2"></objPosition>
\t\t\t\t\t<action qualifier="NONE">
\t\t\t\t\t\t<actionName>
\t\t\t\t\t\t\t<sectionName>A_Fill</sectionName>
\t\t\t\t\t\t</actionName>
\t\t\t\t\t\t<tValue>
\t\t\t\t\t\t\t<tLiteral></tLiteral>
\t\t\t\t\t\t</tValue>
\t\t\t\t\t</action>
\t\t\t\t\t<literals max="" min="t#1s" delay=""></literals>
\t\t\t\t</step>
\t\t\t\t<step stepType="step" stepName="S_Drain">
\t\t\t\t\t<objPosition posX="0" posY="4"></objPosition>
\t\t\t\t\t<literals max="" min="t#1s" delay=""></literals>
\t\t\t\t</step>
\t\t\t\t<transition>
\t\t\t\t\t<objPosition posX="0" posY="1"></objPosition>
\t\t\t\t\t<transitionCondition invertLogic="false">
\t\t\t\t\t\t<variableName>SeqStart</variableName>
\t\t\t\t\t</transitionCondition>
\t\t\t\t</transition>
\t\t\t\t<transition>
\t\t\t\t\t<objPosition posX="0" posY="3"></objPosition>
\t\t\t\t\t<transitionCondition invertLogic="false">
\t\t\t\t\t\t<variableName>FillDone</variableName>
\t\t\t\t\t</transitionCondition>
\t\t\t\t</transition>
\t\t\t\t<transition>
\t\t\t\t\t<objPosition posX="0" posY="5"></objPosition>
\t\t\t\t\t<transitionCondition invertLogic="false">
\t\t\t\t\t\t<variableName>DrainDone</variableName>
\t\t\t\t\t</transitionCondition>
\t\t\t\t</transition>
\t\t\t\t<jumpSFC stepName="S_Init">
\t\t\t\t\t<objPosition posX="0" posY="6"></objPosition>
\t\t\t\t</jumpSFC>
\t\t\t</networkSFC>
\t\t</chartSource>
\t\t<actionSource name="A_Init">
\t\t\t<STSource>FillDone := FALSE;
DrainDone := FALSE;
</STSource>
\t\t</actionSource>
\t\t<actionSource name="A_Fill">
\t\t\t<STSource>IF S_Fill.t &gt;= t#5s THEN
\tFillDone := TRUE;
END_IF;
IF S_Drain.t &gt;= t#3s THEN
\tDrainDone := TRUE;
END_IF;
</STSource>
\t\t</actionSource>
\t</SFCProgram>
\t<dataBlock>
\t\t<variables name="SeqStart" typeName="BOOL"></variables>
\t\t<variables name="FillDone" typeName="BOOL"></variables>
\t\t<variables name="DrainDone" typeName="BOOL"></variables>
\t</dataBlock>
</SFCExchangeFile>
"""

b = ControlExpertBridge()
b.new_project("BMX P34 2020", "02.70", "TplVal")

for label, xml in (("LD", LD_XML), ("FBD", FBD_XML), ("SFC", SFC_XML)):
    try:
        r = b.import_xml(xml, None, "section", "MAST", "overwrite")
        print(f"{label} import OK via {r.get('via')}")
    except CEError as e:
        print(f"{label} import FAIL: {str(e)[:300]}")

r = b.build_project(True)
print("build:", json.dumps(r))
print("structure:", json.dumps(b.get_project_structure(), default=str)[:600])
b.close_project(False)
print("TEMPLATE VALIDATION DONE")
