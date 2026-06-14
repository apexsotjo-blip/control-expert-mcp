"""Shared helpers for the big recreation test."""

import json
import os
import subprocess
import sys
import time

sys.path.insert(0, r"..\..\src")

from control_expert_mcp.bridge import ControlExpertBridge, CEError  # noqa: E402,F401

HERE = os.path.dirname(os.path.abspath(__file__))
REF_DIR = os.path.join(HERE, "ref")
WORK = os.path.join(HERE, "DR001_RECREATE.stu")
WEAK_LOG = os.path.join(HERE, "weaknesses.jsonl")


def log_weak(msg: str) -> None:
    print("  WEAK:", msg[:220])
    with open(WEAK_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(msg) + "\n")


def kill_strays() -> None:
    """Kill leftover CE automation servers and remove stale write tokens."""
    for name in ("ControlExpert.exe", "psbroker.exe"):
        subprocess.run(["taskkill", "/IM", name, "/F"], capture_output=True)
    for folder in (HERE,):
        for fn in os.listdir(folder):
            if fn.lower().endswith(".ztx"):
                try:
                    os.remove(os.path.join(folder, fn))
                except OSError:
                    pass
    time.sleep(2)


def ref_json(name: str):
    with open(os.path.join(REF_DIR, name), encoding="utf-8") as f:
        return json.load(f)


def ref_text(*parts: str) -> str:
    with open(os.path.join(REF_DIR, *parts), encoding="utf-8") as f:
        return f.read()


def step(label, fn, weak_on_fail=True):
    try:
        r = fn()
        out = ""
        if isinstance(r, dict):
            out = " -> " + json.dumps(r, default=str)[:140]
        print(f"OK   {label}{out}")
        return r
    except CEError as e:
        msg = f"{label}: {str(e)[:250]}"
        if weak_on_fail:
            log_weak(msg)
        else:
            print("FAIL", msg)
        return None
