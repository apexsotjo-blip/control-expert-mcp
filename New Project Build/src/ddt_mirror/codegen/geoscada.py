"""GeoSCADA-facing DNP3 point list.

GeoSCADA (Geo SCADA Expert) polls the SCADAPack as a DNP3 outstation; its
engineer needs, per point: the outstation's DNP3 address, the object
group/variation, the point number, and what to build it as. All of that
is decided by our RTU allocation, so we emit one CSV per generate — a
point list for bulk creation, NOT a native Geo SCADA import file (no
verified sample of that format exists yet; do not invent one).

Group -> Geo SCADA point class mapping (DNP3 semantics):
  g1  Binary Input   -> Binary Point (input)
  g20 Counter        -> Counter Point
  g30 Analog Input   -> Analog Point (current value)
  g40 Analog Output  -> Analog Point with Output/setpoint (SCADA writes)
"""

from __future__ import annotations

import csv
import io
import re

from ..core.rtu import RtuAssignment, group_space

_GROUP_INFO = {
    # space -> (geo scada class, direction seen from SCADA)
    "g1": ("Binary Input", "read"),
    "g20": ("Counter", "read"),
    "g30": ("Analog Input", "read"),
    "g40": ("Analog Output (setpoint)", "write"),
}

_VARIATION_RE = re.compile(r"^g(\d+)v(\d+)", re.IGNORECASE)

COLUMNS = ["PlcPath", "ObjectName", "OutstationDnp3Address", "Dnp3Group",
           "Variation", "PointNumber", "GeoScadaClass", "Direction",
           "CeType", "Comment"]


def generate_geoscada_csv(
    assignments: list[RtuAssignment],
    outstation_address: object = "",
) -> str:
    """One row per assignment that carries a DNP3 point.

    `outstation_address` is the RTU's DNP3 address (workbook Parameters
    sheet, 'SCADAPack DNP3 Address') — repeated on every row so the list
    is self-contained for the SCADA engineer."""
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(COLUMNS)
    for a in assignments:
        if a.entry.dnp3_point is None:
            continue
        m = _VARIATION_RE.match(a.entry.group)
        group_no = m.group(1) if m else group_space(a.entry.group).lstrip("g")
        variation = m.group(2) if m else ""
        cls, direction = _GROUP_INFO.get(
            group_space(a.entry.group), ("(unknown)", ""))
        writer.writerow([
            a.leaf.full_path,
            a.entry.name,
            outstation_address,
            group_no,
            variation,
            a.entry.dnp3_point,
            cls,
            direction,
            a.entry.ce_type,
            a.leaf.comment,
        ])
    return buf.getvalue()
