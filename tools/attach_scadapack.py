"""Attach to a running RemoteConnect logic session through the PSBroker.

Run this WHILE a RemoteConnect project's logic editor is open:

    ..\\.venv\\Scripts\\python.exe -u attach_scadapack.py

It asks the broker which applications it is currently serving, attaches to
the served application as a second client, and reads project info through the
same IApplication interface the MCP server uses.
"""

import pythoncom
import pywintypes
import win32com.client
from win32com.client import VARIANT

pythoncom.CoInitialize()

IID_IPSBBrokerInfo = pywintypes.IID("{352686EA-EF6E-11D3-A1CC-000629A2F326}")


def qi(obj, iid):
    return win32com.client.Dispatch(
        obj._oleobj_.QueryInterface(iid, pythoncom.IID_IDispatch)
    )


def invoke_out(obj, name, in_args=(), vt=pythoncom.VT_VARIANT):
    out = VARIANT(pythoncom.VT_BYREF | vt, None)
    dispid = obj._oleobj_.GetIDsOfNames(0, name)
    obj._oleobj_.Invoke(
        dispid, 0, pythoncom.DISPATCH_PROPERTYGET | pythoncom.DISPATCH_METHOD, 1,
        *in_args, out,
    )
    return out.value


print("Creating broker...")
broker = win32com.client.Dispatch("PSBroker.PServerBroker.1")
print("InterfaceVersion:", broker.InterfaceVersion)

print("\nQuerying served applications (IPSBBrokerInfo.GetApplicationNames)...")
names = None
try:
    info = qi(broker, IID_IPSBBrokerInfo)
    for vt in (pythoncom.VT_VARIANT, pythoncom.VT_ARRAY | pythoncom.VT_BSTR, pythoncom.VT_BSTR):
        try:
            names = invoke_out(info, "GetApplicationNames", (), vt)
            break
        except Exception as e:
            print(f"  (vt={vt:#x} failed: {str(e)[:90]})")
except Exception as e:
    print("QI IPSBBrokerInfo failed:", str(e)[:140])

print("Served applications:", names)

if not names:
    print("\nNo served applications — is the RemoteConnect logic editor open right now?")
    raise SystemExit(1)

if isinstance(names, (str, bytes)):
    names = [names]

for name in names:
    print(f"\n=== Attaching to {name!r} ===")
    try:
        app = broker.OpenApplication(str(name))
        print("Version:", app.Version)
        print("InstallationPath:", app.InstallationPath)
        print("IsProjectOpen:", int(app.IsProjectOpen))
        if int(app.IsProjectOpen):
            ole = app._oleobj_
            dispid = ole.GetIDsOfNames(0, "Project")
            disp = ole.Invoke(dispid, 0, pythoncom.DISPATCH_PROPERTYGET, 1, 0)
            proj = win32com.client.Dispatch(disp)
            print("Project name:", proj.Name)
            print("Project file:", proj.ProjectFileName)
            try:
                cpu = proj.Configuration.Cpu
                print("CPU:", cpu.PartNumber, cpu.Version, "| family:", cpu.Family)
            except Exception as e:
                print("CPU read failed:", str(e)[:100])
            try:
                prog = proj.Program
                tasks = prog.Tasks
                cnt = tasks.Count
                if callable(cnt):
                    cnt = cnt()
                print("Tasks:", cnt)
                for t in tasks:
                    td = win32com.client.Dispatch(t)
                    secs = [win32com.client.Dispatch(s).Name for s in td.Sections]
                    print(f"  task {td.Name}: sections={secs}")
            except Exception as e:
                print("Program read failed:", str(e)[:100])
            print("\nSUCCESS — the MCP bridge can drive this RemoteConnect logic session.")
        del app
    except Exception as e:
        print("Attach failed:", str(e)[:160])
