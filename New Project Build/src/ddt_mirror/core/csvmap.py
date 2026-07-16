"""Address-map CSV: the HMI-side deliverable (and Phase-3 Vijeo input)."""

from __future__ import annotations

import csv
import io

from .model import Assignment

COLUMNS = ["Tag", "PlcPath", "Type", "Access", "Address", "Registers",
           "MirrorVariable", "Premapped", "Comment"]


def generate_csv(assignments: list[Assignment]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(COLUMNS)
    for a in assignments:
        leaf = a.leaf
        tag_name = a.mirror_var or leaf.full_path.replace(".", "_")
        writer.writerow([
            tag_name,
            leaf.full_path,
            leaf.type_name,
            "R/W" if leaf.access.value == "read_write" else "R",
            a.address,
            a.registers,
            a.mirror_var,
            "yes" if a.premapped else "",
            leaf.comment,
        ])
    return buf.getvalue()
