"""Member-selection tree: QStandardItemModel helpers + the Access delegate."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QStandardItem, QStandardItemModel
from PySide6.QtWidgets import QComboBox, QStyledItemDelegate

from ..core.access import guess_access
from ..core.model import Access, FlatLeaf

COL_MEMBER, COL_TYPE, COL_ACCESS, COL_ADDRESS, COL_COMMENT = range(5)
HEADERS = ["Member", "Type", "Access", "Address", "Comment"]

LEAF_ROLE = Qt.UserRole + 1  # FlatLeaf stored on the member column item

ACCESS_LABELS = {Access.READ: "Read", Access.READ_WRITE: "Read/Write"}
LABEL_TO_ACCESS = {v: k for k, v in ACCESS_LABELS.items()}


def _item(text: str, editable: bool = False) -> QStandardItem:
    it = QStandardItem(text)
    it.setEditable(editable)
    return it


def build_member_model(
    leaves: list[FlatLeaf],
    deselected: set[str],
    overrides: dict[str, str],
    alloc_leaves: dict,
    type_deselected: set[str] = frozenset(),
) -> QStandardItemModel:
    """Group leaves under one row per instance; standalone tags are top-level."""
    model = QStandardItemModel()
    model.setHorizontalHeaderLabels(HEADERS)
    root = model.invisibleRootItem()

    def current_access(leaf: FlatLeaf) -> Access:
        override = (overrides.get(leaf.instance_access_key)
                    or overrides.get(leaf.access_key))
        return Access(override) if override else guess_access(
            leaf.rel_path or leaf.instance)

    def address_text(leaf: FlatLeaf) -> str:
        from ..core.allocator import premapped_address

        pre = premapped_address(leaf)
        if pre:
            return f"{pre} (already located)"
        entry = alloc_leaves.get(leaf.full_path)
        return entry.address if entry else "(new)"

    def leaf_row(leaf: FlatLeaf, label: str) -> list[QStandardItem]:
        name = _item(label)
        name.setCheckable(True)
        off = (leaf.full_path in deselected
               or (leaf.ddt_type and leaf.access_key in type_deselected))
        name.setCheckState(Qt.Unchecked if off else Qt.Checked)
        name.setData(leaf, LEAF_ROLE)
        access = _item(ACCESS_LABELS[current_access(leaf)], editable=True)
        return [name, _item(leaf.type_name), access,
                _item(address_text(leaf)), _item(leaf.comment)]

    by_instance: dict[str, list[FlatLeaf]] = {}
    order: list[str] = []
    for leaf in leaves:
        if leaf.instance not in by_instance:
            by_instance[leaf.instance] = []
            order.append(leaf.instance)
        by_instance[leaf.instance].append(leaf)

    for instance in order:
        group = by_instance[instance]
        if len(group) == 1 and not group[0].rel_path:  # standalone tag
            root.appendRow(leaf_row(group[0], instance))
            continue
        parent = _item(f"{instance}  ({group[0].ddt_type})")
        parent.setCheckable(True)
        parent.setAutoTristate(True)
        parent.setCheckState(Qt.Checked)
        font = parent.font()
        font.setBold(True)
        parent.setFont(font)
        for leaf in group:
            parent.appendRow(leaf_row(leaf, leaf.rel_path))
        root.appendRow([parent, _item(""), _item(""), _item(""), _item("")])
        _sync_parent_state(parent)
    return model


def _sync_parent_state(parent: QStandardItem) -> None:
    states = {parent.child(r, COL_MEMBER).checkState()
              for r in range(parent.rowCount())}
    if states == {Qt.Checked}:
        parent.setCheckState(Qt.Checked)
    elif states == {Qt.Unchecked}:
        parent.setCheckState(Qt.Unchecked)
    else:
        parent.setCheckState(Qt.PartiallyChecked)


def iter_leaf_items(model: QStandardItemModel):
    """Yield every (parent, row, member item, FlatLeaf) in the model."""
    root = model.invisibleRootItem()

    def walk(parent: QStandardItem):
        for r in range(parent.rowCount()):
            child = parent.child(r, COL_MEMBER)
            leaf = child.data(LEAF_ROLE)
            if leaf is not None:
                yield parent, r, child, leaf
            if child.hasChildren():
                yield from walk(child)

    yield from walk(root)


def wire_check_propagation(model: QStandardItemModel, type_mode=lambda: True) -> None:
    """Parent checkbox drives children; children roll up to the parent.

    When type_mode() is True, (un)checking a DDT member applies to the same
    member of EVERY instance of that DDT type. Instance-level (parent)
    checkboxes always stay instance-scoped.
    """
    guard = {"busy": False}

    def on_changed(item: QStandardItem) -> None:
        if guard["busy"] or item.column() != COL_MEMBER:
            return
        guard["busy"] = True
        try:
            if item.hasChildren():
                if item.checkState() != Qt.PartiallyChecked:
                    for r in range(item.rowCount()):
                        item.child(r, COL_MEMBER).setCheckState(item.checkState())
                return
            leaf = item.data(LEAF_ROLE)
            parents: dict[int, QStandardItem] = {}
            if leaf is not None and leaf.ddt_type and type_mode():
                root = model.invisibleRootItem()
                for parent, _r, other, other_leaf in iter_leaf_items(model):
                    if (other_leaf.ddt_type == leaf.ddt_type
                            and other_leaf.rel_path == leaf.rel_path):
                        if other is not item:
                            other.setCheckState(item.checkState())
                        if parent is not root:
                            parents[id(parent)] = parent
            if item.parent() is not None:
                parents[id(item.parent())] = item.parent()
            for parent in parents.values():
                _sync_parent_state(parent)
        finally:
            guard["busy"] = False

    model.itemChanged.connect(on_changed)


def collect_deselected(model: QStandardItemModel) -> tuple[list[str], list[str]]:
    """Classify unchecked leaves into (per-variable paths, type-level members).

    A DDT member unchecked across EVERY instance of its type is recorded as a
    type-level exclusion ("TYPE|rel.path") so it also applies to instances
    added to the project later; partial unchecks stay per-variable.
    """
    totals: dict[str, list[int]] = {}   # access_key -> [instances, unchecked]
    unchecked_ddt: list = []
    per_var: list[str] = []

    for _parent, _r, item, leaf in iter_leaf_items(model):
        if leaf.ddt_type:
            t = totals.setdefault(leaf.access_key, [0, 0])
            t[0] += 1
            if item.checkState() == Qt.Unchecked:
                t[1] += 1
                unchecked_ddt.append(leaf)
        elif item.checkState() == Qt.Unchecked:
            per_var.append(leaf.full_path)

    type_members = sorted(
        key for key, (total, off) in totals.items() if off and off == total)
    type_set = set(type_members)
    per_var.extend(l.full_path for l in unchecked_ddt
                   if l.access_key not in type_set)
    return per_var, type_members


class AccessDelegate(QStyledItemDelegate):
    """Combobox editor for the Access column.

    In type mode an edit applies to the whole DDT type (every instance's
    matching row updates live, and stale per-variable overrides are cleared).
    In manual mode it stores a per-variable override for this row only.
    """

    def __init__(self, overrides: dict[str, str], type_mode=lambda: True,
                 parent=None) -> None:
        super().__init__(parent)
        self._overrides = overrides
        self._type_mode = type_mode

    def createEditor(self, parent, option, index):
        leaf = self._leaf_for(index)
        if leaf is None:
            return None
        box = QComboBox(parent)
        box.addItems(list(LABEL_TO_ACCESS))
        return box

    def setEditorData(self, editor, index):
        editor.setCurrentText(index.data() or "Read")

    def setModelData(self, editor, model, index):
        leaf = self._leaf_for(index)
        if leaf is None:
            return
        label = editor.currentText()
        value = LABEL_TO_ACCESS[label].value
        if leaf.ddt_type and self._type_mode():
            self._overrides[leaf.access_key] = value
            self._apply_to_matches(model, leaf.access_key, label)
        else:
            self._overrides[leaf.instance_access_key] = value
            model.setData(index, label)

    @staticmethod
    def _leaf_for(index):
        member_index = index.siblingAtColumn(COL_MEMBER)
        return member_index.data(LEAF_ROLE)

    def _apply_to_matches(self, model, access_key: str, label: str) -> None:
        for parent, row, _item, leaf in iter_leaf_items(model):
            if leaf.access_key == access_key:
                # the type-level choice supersedes any per-variable override
                self._overrides.pop(leaf.instance_access_key, None)
                sibling = parent.child(row, COL_ACCESS)
                if sibling is not None:
                    sibling.setText(label)
