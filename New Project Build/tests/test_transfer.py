import os

from ddt_mirror.codegen.transfer import (
    SECTION_EXT, export_sections, filter_xsy,
)

XSY = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<VariablesExchangeFile>
  <DDTSource DDTName="PUMP_T">
    <structure>
      <variables name="Cmd" typeName="BOOL"></variables>
    </structure>
  </DDTSource>
  <dataBlock>
    <variables name="Pump1" typeName="PUMP_T"></variables>
    <variables name="HMI_Pump1_Flow_PV" typeName="REAL" topologicalAddress="%MW1000">
      <comment>HMI mirror of Pump1.Flow_PV</comment>
    </variables>
    <variables name="Line_Speed" typeName="INT">
      <comment>HMI mirror lookalike is kept: comment must START with marker</comment>
    </variables>
    <variables name="Located_Speed" typeName="INT" topologicalAddress="%MW50"></variables>
  </dataBlock>
</VariablesExchangeFile>
"""


def test_filter_xsy_removes_only_mirror_variables():
    filtered, removed = filter_xsy(XSY)
    assert removed == ["HMI_Pump1_Flow_PV"]
    assert "HMI_Pump1_Flow_PV" not in filtered
    assert "Pump1" in filtered and "Line_Speed" in filtered
    assert 'DDTName="PUMP_T"' in filtered            # types untouched
    assert 'topologicalAddress="%MW50"' in filtered  # premapped tag kept
    # still well-formed XML with declaration
    assert filtered.lstrip().startswith("<?xml")


class FakeBridge:
    """get_project_structure/read_section stub for the sections export."""

    def get_project_structure(self):
        return {"tasks": [{"name": "MAST", "sections": [
            {"name": "Main", "language": "ST"},
            {"name": "HMI_MIRROR", "language": "ST"},
            {"name": "Interlocks", "language": "LD"},
            {"name": "Weird", "language": "LL984"},
        ]}]}

    def read_section(self, task, section):
        return {"task": task, "section": section, "language": "ST",
                "xml": f"<xml for {section}/>"}


def test_export_sections_excludes_mirror_and_types_files(tmp_path):
    files, warnings = export_sections(FakeBridge(), "HMI_MIRROR",
                                      str(tmp_path))
    names = [os.path.basename(f) for f in files]
    assert names == ["01_MAST_Main.xst", "02_MAST_Interlocks.xld",
                     "03_MAST_Weird.xpg.xml"]
    assert not any("HMI_MIRROR" in n for n in names)
    assert any("unknown exchange extension" in w for w in warnings)
    with open(files[0], encoding="utf-8") as fh:
        assert fh.read() == "<xml for Main/>"


def test_section_ext_covers_ce_languages():
    assert SECTION_EXT["ST"] == ".xst"
    assert SECTION_EXT["LD"] == ".xld"
