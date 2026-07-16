"""COM work off the GUI thread.

ControlExpertBridge serializes everything on its own STA worker and its
methods block; this QObject lives on a QThread so those blocking calls never
freeze the UI. The GUI talks to it via queued signal/slot connections only.
"""

from __future__ import annotations

import traceback

from PySide6.QtCore import QObject, Signal, Slot

from ..core.engine import apply_plan, scan_project


class BridgeWorker(QObject):
    opened = Signal(object, str)   # (ProjectData, project_path)
    applied = Signal(object)       # ApplyReport
    transferred = Signal(object)   # TransferReport
    failed = Signal(str)
    progress = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self._bridge = None

    def _get_bridge(self):
        if self._bridge is None:
            self.progress.emit("Starting Control Expert automation session...")
            from control_expert_mcp.bridge import ControlExpertBridge

            self._bridge = ControlExpertBridge()
        return self._bridge

    @Slot(str)
    def open_project(self, path: str) -> None:
        try:
            bridge = self._get_bridge()
            self.progress.emit(f"Opening {path} ...")
            bridge.open_project(path)
            self.progress.emit("Reading variables and DDT definitions...")
            data = scan_project(bridge)
            self.opened.emit(data, path)
        except Exception:
            self.failed.emit(traceback.format_exc())

    @Slot(object, object, object, str)
    def apply(self, plan, new_alloc, state, project_path: str) -> None:
        try:
            bridge = self._get_bridge()
            report = apply_plan(bridge, plan, new_alloc, state, project_path,
                                progress=self.progress.emit)
            self.applied.emit(report)
        except Exception:
            self.failed.emit(traceback.format_exc())

    @Slot(object, object, str, str, str)
    def transfer(self, data, state, src_xls: str, out_dir: str,
                 project_path: str) -> None:
        try:
            import datetime as _dt

            from ..codegen.transfer import transfer_to_remoteconnect

            bridge = self._get_bridge()
            report = transfer_to_remoteconnect(
                bridge, data, state, src_xls, out_dir, project_path,
                timestamp=_dt.datetime.now().isoformat(timespec="seconds"),
                progress=self.progress.emit)
            self.transferred.emit(report)
        except Exception:
            self.failed.emit(traceback.format_exc())

    @Slot()
    def shutdown(self) -> None:
        if self._bridge is not None:
            try:
                self._bridge.shutdown()
            except Exception:
                pass
            self._bridge = None
