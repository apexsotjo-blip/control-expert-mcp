"""List device types and protocols from the Control Expert DTM catalog cache."""

import re
import sys

PATH = (
    sys.argv[1]
    if len(sys.argv) > 1
    else r"C:\ProgramData\Schneider Electric\Control Expert 14.0\DTMCatalog\DTMCatalog.xml"
)

data = open(PATH, "r", encoding="utf-8", errors="replace").read()
for m in re.finditer(r'<DeviceType\b[^>]*d2p1:Name="([^"]+)"[^>]*d2p1:Type="([^"]+)"[^>]*>', data):
    name, typ = m.group(1), m.group(2)
    chunk = data[m.start():m.start() + 8000]
    protos = sorted(set(re.findall(r'protocolId="([^"]+)"', chunk)))
    print(f"{typ:14s} {name:34s} protocols={protos}")
