import csv
import io

from ddt_mirror.codegen.geoscada import generate_geoscada_csv
from ddt_mirror.core.model import Access, FlatLeaf, LeafKind
from ddt_mirror.core.rtu import (
    DT_ANALOG, DT_DIGITAL, GROUP_AI, GROUP_AO, GROUP_BI, GROUP_COUNTER,
    LVT_BOOL, LVT_INT, MB_DISCRETE, MB_UINT, RtuAssignment, RtuEntry,
    RtuSpec,
)


def _assign(path, group, point, access="read", ce_type="INT",
            comment="") -> RtuAssignment:
    leaf = FlatLeaf(instance=path.split(".")[0],
                    rel_path=".".join(path.split(".")[1:]),
                    type_name=ce_type, kind=LeafKind.WORD1,
                    comment=comment)
    leaf.access = Access(access)
    entry = RtuEntry(name=path.replace(".", "_"), data_type=DT_ANALOG,
                     logic_type=LVT_INT, group=group, access=access,
                     kind="word1", ce_type=ce_type, dnp3_point=point)
    spec = RtuSpec(DT_ANALOG, LVT_INT, group, MB_UINT, "holding")
    return RtuAssignment(leaf=leaf, entry=entry, spec=spec)


def test_geoscada_csv_maps_groups_to_classes():
    rows = [
        _assign("Pump1.Mode", GROUP_AI, 2306),
        _assign("Pump1.SP", GROUP_AO, 10, access="read_write"),
        _assign("Pump1.Run", GROUP_BI, 5, ce_type="BOOL"),
        _assign("Pump1.Cnt", GROUP_COUNTER, 7, ce_type="UDINT"),
        _assign("Pump1.NoPoint", GROUP_AI, None),
    ]
    text = generate_geoscada_csv(rows, outstation_address=1024)
    parsed = list(csv.DictReader(io.StringIO(text)))
    assert len(parsed) == 4                     # point-less row dropped
    by_path = {r["PlcPath"]: r for r in parsed}

    ai = by_path["Pump1.Mode"]
    assert ai["Dnp3Group"] == "30" and ai["Variation"] == "1"
    assert ai["PointNumber"] == "2306"
    assert ai["GeoScadaClass"] == "Analog Input"
    assert ai["OutstationDnp3Address"] == "1024"

    ao = by_path["Pump1.SP"]
    assert ao["Dnp3Group"] == "40"
    assert ao["Direction"] == "write"

    assert by_path["Pump1.Run"]["GeoScadaClass"] == "Binary Input"
    assert by_path["Pump1.Cnt"]["GeoScadaClass"] == "Counter"


def test_geoscada_written_by_rtu_export(parsed, tmp_path):
    """export_remoteconnect emits the point list next to the bundle."""
    import xlwt

    from ddt_mirror.codegen.remoteconnect import (
        OBJECTS_SHEET, PARAMETERS_SHEET, export_remoteconnect,
    )
    from ddt_mirror.core.engine import ProjectData
    from ddt_mirror.core.flatten import flatten_tags
    from ddt_mirror.core.persist import SidecarState

    wb = xlwt.Workbook()
    ws = wb.add_sheet(OBJECTS_SHEET)
    ws.write(0, 3, "Name"); ws.write(1, 3, "ObjName")
    ws = wb.add_sheet(PARAMETERS_SHEET)
    ws.write(2, 0, "RtuSettingsBasicGeneralRtuDnpAddress")
    ws.write(2, 1, "SCADAPack DNP3 Address")
    ws.write(2, 2, 77)
    src = tmp_path / "rtu.xls"
    wb.save(str(src))

    types, tags = parsed
    leaves, warnings = flatten_tags(tags, types)
    data = ProjectData(types=types, tags=tags, leaves=leaves,
                       warnings=warnings)
    state = SidecarState()
    state.selected_types = ["PUMP_T", "INT", "BOOL", "REAL"]

    report = export_remoteconnect(data, state, str(src), str(tmp_path),
                                  str(tmp_path / "p.stu"), timestamp="t0")
    assert report.geoscada_path
    with open(report.geoscada_path, encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh))
    assert rows and all(r["OutstationDnp3Address"] == "77" for r in rows)
    assert all(r["PointNumber"] for r in rows)
