import re
from collections import Counter

from common import ControlExpertBridge, WORK, kill_strays

kill_strays()
b = ControlExpertBridge()
b.open_project(WORK)
r = b.build_project(True)
out = r.get("output") or ""
b.close_project(False)

codes = Counter(re.findall(r"\bE\d{3,4}\b", out))
print("error codes:", dict(codes))

# group error lines by the section/object that owns them
sections = Counter()
for ln in out.splitlines():
    m = re.match(r"\{([^:]+?)(?: <[^>]+>)? : \[[^\]]+\]\}", ln.strip())
    if m and re.search(r"E\d{3,4}|error", ln):
        sections[m.group(1).strip()] += 1
print("\nerrors by section (top 12):")
for s, c in sections.most_common(12):
    print(f"  {s}: {c}")

print("\nsample distinct error lines:")
seen = set()
for ln in out.splitlines():
    t = ln.strip()
    if re.search(r"E\d{3,4}", t):
        key = re.sub(r"\d+", "#", t)
        if key not in seen:
            seen.add(key)
            print("  |", t[:160])
        if len(seen) > 18:
            break
