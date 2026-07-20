# PyInstaller spec: bundles the web UI (launcher.py) into a windowless,
# no-Python-required app folder. Build with:
#   .venv\Scripts\pyinstaller installer\ddtmirror.spec --noconfirm
#
# Deliberately excludes PySide6 (the desktop Qt UI) - web/server.py never
# imports gui/, so leaving it out keeps the bundle small and the build
# fast. Add the gui package + PySide6 back here if a desktop build is
# ever wanted from the same tooling.

import os

block_cipher = None
ROOT = os.path.abspath(os.path.join(os.path.dirname(SPEC), ".."))
STATIC = os.path.join(ROOT, "src", "ddt_mirror", "web", "static")

a = Analysis(
    [os.path.join(ROOT, "installer", "launcher.py")],
    pathex=[
        os.path.join(ROOT, "src"),
        os.path.join(os.path.dirname(ROOT), "src"),  # parent control_expert_mcp
    ],
    binaries=[],
    datas=[(STATIC, os.path.join("ddt_mirror", "web", "static"))],
    hiddenimports=[
        "ddt_mirror.web.server",
        "ddt_mirror.core.adopt", "ddt_mirror.core.allocator",
        "ddt_mirror.core.access", "ddt_mirror.core.csvmap",
        "ddt_mirror.core.ddt_library", "ddt_mirror.core.engine",
        "ddt_mirror.core.flatten", "ddt_mirror.core.model",
        "ddt_mirror.core.persist", "ddt_mirror.core.rtu",
        "ddt_mirror.core.stgen", "ddt_mirror.core.xsy_parser",
        "ddt_mirror.codegen.logic_project", "ddt_mirror.codegen.remoteconnect",
        "ddt_mirror.codegen.scanner", "ddt_mirror.codegen.transfer",
        "ddt_mirror.codegen.vijeo", "ddt_mirror.codegen.geoscada",
        "control_expert_mcp.bridge", "control_expert_mcp.lang_reference",
        "win32com.client", "win32com", "win32timezone", "pythoncom",
        "pywintypes",
        "uvicorn.logging", "uvicorn.loops", "uvicorn.loops.auto",
        "uvicorn.protocols", "uvicorn.protocols.http",
        "uvicorn.protocols.http.auto", "uvicorn.protocols.websockets",
        "uvicorn.protocols.websockets.auto", "uvicorn.lifespan",
        "uvicorn.lifespan.on",
        "lxml.etree", "xlrd", "xlwt",
    ],
    hookspath=[],
    excludes=["PySide6", "shiboken6", "PyQt5", "PyQt6", "matplotlib",
             "numpy", "tkinter"],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="DDTMirror",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
)
coll = COLLECT(
    exe, a.binaries, a.zipfiles, a.datas,
    strip=False,
    upx=False,
    name="DDTMirror",
)
