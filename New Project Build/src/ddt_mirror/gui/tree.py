"""Member-selection tree: QStandardItemModel helpers + the Access delegate.

Two view modes share the same delegate and check-propagation machinery:

- build_member_model  — grouped BY INSTANCE (detailed): every variable of
  every instance is its own row; per-variable exceptions are visible.
- build_type_model    — grouped BY TYPE (compact): each DDT member appears
  ONCE and edits are inherently type-level (they apply to every instance,
  including future ones); standalone tags group under their elementary
  type. Per-variable exceptions are preserved but not shown here.
"""

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


def build_type_model(
    leaves: list[FlatLeaf],
    deselected: set[str],
    overrides: dict[str, str],
    type_deselected: set[str] = frozenset(),
) -> QStandardItemModel:
    """Compact view: one row per DDT member (applies to ALL instances) and
    one row per standalone tag, grouped under its elementary type."""
    model = QStandardItemModel()
    model.setHorizontalHeaderLabels(HEADERS)
    root = model.invisibleRootItem()

    def type_access(leaf: FlatLeaf) -> Access:
        override = overrides.get(leaf.access_key)
        return Access(override) if override else guess_access(
            leaf.rel_path or leaf.instance)

    # DDT types: representative leaf per (type, rel_path), instances counted
    ddt_members: dict[str, dict[str, FlatLeaf]] = {}
    ddt_instances: dict[str, set[str]] = {}
    standalone: dict[str, list[FlatLeaf]] = {}
    order_ddt: list[str] = []
    order_elem: list[str] = []
    for leaf in leaves:
        if leaf.ddt_type:
            if leaf.ddt_type not in ddt_members:
                ddt_members[leaf.ddt_type] = {}
                ddt_instances[leaf.ddt_type] = set()
                order_ddt.append(leaf.ddt_type)
            ddt_members[leaf.ddt_type].setdefault(leaf.rel_path, leaf)
            ddt_instances[leaf.ddt_type].add(leaf.instance)
        else:
            if leaf.type_name not in standalone:
                standalone[leaf.type_name] = []
                order_elem.append(leaf.type_name)
            standalone[leaf.type_name].append(leaf)

    def leaf_row(leaf: FlatLeaf, label: str, off: bool) -> list[QStandardItem]:
        name = _item(label)
        name.setCheckable(True)
        name.setCheckState(Qt.Unchecked if off else Qt.Checked)
        name.setData(leaf, LEAF_ROLE)
        access = _item(ACCESS_LABELS[type_access(leaf)], editable=True)
        return [name, _item(leaf.type_name), access, _item(""),
                _item(leaf.comment)]

    for ddt in order_ddt:
        insts = sorted(ddt_instances[ddt])
        shown = ", ".join(insts[:4]) + (" ..." if len(insts) > 4 else "")
        parent = _item(f"{ddt}  ({len(insts)} instances: {shown})")
        parent.setCheckable(True)
        parent.setAutoTristate(True)
        font = parent.font()
        font.setBold(True)
        parent.setFont(font)
        for rel, leaf in ddt_members[ddt].items():
            off = leaf.access_key in type_deselected
            parent.appendRow(leaf_row(leaf, rel, off))
        root.appendRow([parent, _item(""), _item(""), _item(""), _item("")])
        _sync_parent_state(parent)

    for type_name in order_elem:
        tags = standalone[type_name]
        parent = _item(f"{type_name}  ({len(tags)} tags)")
        parent.setCheckable(True)
        parent.setAutoTristate(True)
        font = parent.font()
        font.setBold(True)
        parent.setFont(font)
        for leaf in tags:
            off = leaf.full_path in deselected
            parent.appendRow(leaf_row(leaf, leaf.instance, off))
        root.appendRow([parent, _item(""), _item(""), _item(""), _item("")])
        _sync_parent_state(parent)
    return model


def collect_type_view(
    model: QStandardItemModel,
    prior_per_var: list[str],
    leaves: list[FlatLeaf],
) -> tuple[list[str], list[str]]:
    """Commit the compact view: unchecked DDT members become type-level
    exclusions; RE-checking a member also clears any per-variable
    exclusions of that member (checked at type level = included
    everywhere). Standalone tags stay per-variable. Per-variable
    exclusions of DDT members not represented here are preserved."""
    type_members: list[str] = []
    per_var: list[str] = []
    included_keys: set[str] = set()
    seen_standalone: set[str] = set()

    for _parent, _r, item, leaf in iter_leaf_items(model):
        if leaf.ddt_type:
            if item.checkState() == Qt.Unchecked:
                type_members.append(leaf.access_key)
            else:
                included_keys.add(leaf.access_key)
        else:
            seen_standalone.add(leaf.full_path)
            if item.checkState() == Qt.Unchecked:
                per_var.append(leaf.full_path)

    key_of_path = {l.full_path: l.access_key for l in leaves if l.ddt_type}
    kept = [p for p in prior_per_var
            if p not in seen_standalone           # re-checked standalones drop
            and key_of_path.get(p) not in included_keys
            and p not in per_var]
    return kept + per_var, sorted(type_members)


# --------------------------------------------------------------- bulk edits

def visible_leaf_items(tree_view) -> list[tuple[QStandardItem, FlatLeaf]]:
    """(member item, leaf) for every leaf row not hidden by the filter."""
    model = tree_view.model()
    if model is None:
        return []
    root = model.invisibleRootItem()
    out: list[tuple[QStandardItem, FlatLeaf]] = []

    def walk(parent: QStandardItem, parent_index) -> None:
        for r in range(parent.rowCount()):
            if tree_view.isRowHidden(r, parent_index):
                continue
            child = parent.child(r, COL_MEMBER)
            leaf = child.data(LEAF_ROLE)
            if leaf is not None:
                out.append((child, leaf))
            if child.hasChildren():
                walk(child, child.index())

    walk(root, root.index())
    return out


def bulk_set_checked(items: list[QStandardItem], checked: bool) -> None:
    state = Qt.Checked if checked else Qt.Unchecked
    for item in items:
        if item.checkState() != state:
            item.setCheckState(state)  # propagation wiring does the rest


def bulk_set_access(
    model: QStandardItemModel,
    targets: list[tuple[QStandardItem, FlatLeaf]],
    label: str,
    overrides: dict[str, str],
    type_scope: bool,
) -> int:
    """Set access on every target row. type_scope=True writes type-level
    overrides (and clears stale per-variable ones); False writes
    per-variable overrides. Returns the number of rows updated."""
    value = LABEL_TO_ACCESS[label].value
    done: set[str] = set()
    n = 0
    for item, leaf in targets:
        if type_scope and leaf.ddt_type:
            if leaf.access_key in done:
                continue
            done.add(leaf.access_key)
            overrides[leaf.access_key] = value
            for parent, row, _i, other in iter_leaf_items(model):
                if other.access_key == leaf.access_key:
                    overrides.pop(other.instance_access_key, None)
                    sibling = parent.child(row, COL_ACCESS)
                    if sibling is not None:
                        sibling.setText(label)
                    n += 1
        else:
            overrides[leaf.instance_access_key] = value
            parent = item.parent() or model.invisibleRootItem()
            sibling = parent.child(item.row(), COL_ACCESS)
            if sibling is not None:
                sibling.setText(label)
            n += 1
    return n


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
