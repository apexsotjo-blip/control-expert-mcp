"""Live GUI smoke test: the real wizard flow against real Control Expert.

Uses the scratch project produced by verify_e2e.py. Drives the exact worker
signal path the GUI uses (queued cross-thread calls), skipping only the
confirmation dialog. Run verify_e2e.py first.
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication

from ddt_mirror.gui.app import MainWindow, PAGE_GENERATE, PAGE_TYPES

PROJECT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..",
                                       "_e2e_work", "mirror_e2e.stu"))


def wait_for(signal, timeout_ms: int):
    loop = QEventLoop()
    result = []
    signal.connect(lambda *args: (result.append(args), loop.quit()))
    QTimer.singleShot(timeout_ms, loop.quit)
    loop.exec()
    return result[0] if result else None


def check(cond, msg):
    if not cond:
        print(f"FAIL: {msg}")
        sys.exit(1)
    print(f"  OK: {msg}")


def main() -> None:
    if not os.path.isfile(PROJECT):
        print(f"Run tools/verify_e2e.py first (missing {PROJECT})")
        sys.exit(2)

    app = QApplication(sys.argv)
    win = MainWindow()

    print("==> Opening real project through the GUI worker...")
    win.path_edit.setText(PROJECT)
    win._open_clicked()
    got = wait_for(win._worker.opened, 240_000)
    check(got is not None, "worker opened the project and scanned variables")
    app.processEvents()
    check(win.stack.currentIndex() == PAGE_TYPES, "GUI advanced to types page")

    win._types_next()
    win._members_next()
    check(win.stack.currentIndex() == PAGE_GENERATE, "reached generate page")
    check("HMI_Pump1_Man_SP" in win.preview_st.toPlainText(),
          "preview regenerated from live project data")

    print("==> Applying through the GUI worker (create/write/build/save)...")
    win.request_apply.emit(win.plan, win.new_alloc, win.state, win.project_path)
    got = wait_for(win._worker.applied, 600_000)
    check(got is not None, "worker finished apply")
    report = got[0]
    check(report.ok, f"apply succeeded (build={report.build_state})")
    check(os.path.isfile(report.csv_path), "address-map CSV written")
    check(os.path.isfile(report.sidecar_path), "sidecar JSON written")

    win.request_shutdown.emit()
    win._thread.quit()
    win._thread.wait(15_000)
    print("\nLIVE GUI SMOKE TEST PASSED")


if __name__ == "__main__":
    main()
