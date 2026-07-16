from ddt_mirror.core.xsy_parser import parse_xsy


def test_ddt_types_parsed(parsed):
    types, _ = parsed
    assert set(types) == {"CTRL_T", "PUMP_T"}
    pump = types["PUMP_T"]
    assert [m.name for m in pump.members] == ["Cmd", "Running", "Flow_PV", "Ctrl", "Log"]
    assert pump.members[0].comment == "Start command"
    assert pump.members[3].type_name == "CTRL_T"


def test_tags_parsed(parsed):
    _, tags = parsed
    by_name = {t.name: t for t in tags}
    assert by_name["Pump1"].type_name == "PUMP_T"
    assert by_name["Pump1"].comment == "Feed pump"
    assert by_name["Located_Speed"].address == "%MW50"
    assert by_name["E_Stop"].address == ""
    assert "Mixer" in by_name  # DFB instance present in tags; filtered later


def test_fbsource_locals_not_treated_as_globals():
    xml = """<?xml version="1.0"?>
<VariablesExchangeFile>
  <FBSource nameOfFBType="MY_DFB">
    <inputParameters><variables name="IN1" typeName="BOOL"/></inputParameters>
    <dataBlock><variables name="hidden_local" typeName="INT"/></dataBlock>
  </FBSource>
  <dataBlock><variables name="RealGlobal" typeName="INT"/></dataBlock>
</VariablesExchangeFile>"""
    _, tags = parse_xsy(xml)
    names = [t.name for t in tags]
    assert names == ["RealGlobal"]
