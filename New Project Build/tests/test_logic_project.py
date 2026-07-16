import os

from ddt_mirror.codegen.logic_project import (
    _section_task, device_type_to_platform, generate_logic_project,
)


def test_device_type_to_platform():
    assert device_type_to_platform("SCADAPack 474 <61>") == "SCADAPack47x"
    assert device_type_to_platform("SCADAPack 470") == "SCADAPack47x"
    assert device_type_to_platform("SCADAPack 535E <12>") == "SCADAPack57x"
    assert device_type_to_platform("SCADAPack 575") == "SCADAPack57x"
    assert device_type_to_platform("SCADAPack 100 <3>") == "SCADAPack1070"
    assert device_type_to_platform("SCADAPack 1070") == "SCADAPack1070"
    assert device_type_to_platform("") == "SCADAPack57x"     # safe default
    assert device_type_to_platform("mystery box") == "SCADAPack57x"


def test_section_task_extracts_task_attribute():
    xml = ('<?xml version="1.0"?><PGMExchangeFile><program>'
           '<identProgram name="Main" type="section" task="FAST">'
           '</identProgram></program></PGMExchangeFile>')
    assert _section_task(xml) == "FAST"
    assert _section_task("<no ident here/>") == "MAST"


class FakeBridge:
    """Records the driven calls; no COM."""

    def __init__(self, fail_on=()):
        self.calls = []
        self.fail_on = set(fail_on)

    def new_logic_editor_project(self, platform, xpdf, xso):
        self.calls.append(("new", platform))
        return {"created": True, "platform": platform}

    def import_xml(self, xml, path, kind, task, mode):
        tag = f"{kind}:{os.path.basename(path) if path else ''}"
        if tag in self.fail_on:
            raise RuntimeError("boom")
        self.calls.append(("import", kind, os.path.basename(path or ""), task))

    def write_st_logic(self, task, section, text, declare):
        self.calls.append(("st", task, section))

    def save_project(self, path):
        self.calls.append(("save", path))
        return {"saved_as": path}


def _bundle(tmp_path):
    xsy = tmp_path / "p_RTU_variables.xsy"
    xsy.write_text("<VariablesExchangeFile/>", encoding="utf-8")
    sdir = tmp_path / "secs"
    sdir.mkdir()
    s1 = sdir / "01_MAST_Main.xst"
    s1.write_text('<x><identProgram task="MAST"/></x>', encoding="utf-8")
    s2 = sdir / "02_FAST_Ramp.xst"
    s2.write_text('<x><identProgram task="FAST"/></x>', encoding="utf-8")
    return str(xsy), [str(s1), str(s2)]


def test_generate_logic_project_drives_bridge(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "ddt_mirror.codegen.logic_project.extract_dtm_resources",
        lambda: ("<xpdf/>", "<xso/>"))
    xsy, sections = _bundle(tmp_path)
    bridge = FakeBridge()
    out = str(tmp_path / "out.stu")
    rep = generate_logic_project(
        bridge, "SCADAPack47x", xsy, sections,
        "Pump1.Cmd := Pump1_Cmd.value;", "RTU_MIRROR", out)
    assert rep.ok and rep.stu_path == out
    assert rep.platform == "SCADAPack47x"
    assert rep.imported_variables is True
    assert rep.imported_sections == ["01_MAST_Main.xst", "02_FAST_Ramp.xst"]
    kinds = [c for c in bridge.calls]
    assert ("new", "SCADAPack47x") == kinds[0]
    assert ("st", "MAST", "RTU_MIRROR") in kinds
    assert kinds[-1] == ("save", out)
    # per-section task routing honored
    assert ("import", "section", "02_FAST_Ramp.xst", "FAST") in kinds


def test_generate_logic_project_partial_failures_warn_not_abort(
        tmp_path, monkeypatch):
    monkeypatch.setattr(
        "ddt_mirror.codegen.logic_project.extract_dtm_resources",
        lambda: ("<xpdf/>", "<xso/>"))
    xsy, sections = _bundle(tmp_path)
    bridge = FakeBridge(fail_on={"variables:p_RTU_variables.xsy",
                                 "section:01_MAST_Main.xst"})
    rep = generate_logic_project(
        bridge, "SCADAPack57x", xsy, sections, "x := y.value;",
        "RTU_MIRROR", str(tmp_path / "out.stu"))
    assert rep.ok                              # still saved
    assert rep.imported_variables is False
    assert rep.imported_sections == ["02_FAST_Ramp.xst"]
    assert any("variable import failed" in w for w in rep.warnings)
    assert any("01_MAST_Main.xst" in w for w in rep.warnings)
