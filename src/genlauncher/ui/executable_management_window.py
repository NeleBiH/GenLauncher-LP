"""Executable management — list, register and pick a launch executable.

Port of Rewritten's ``LauncherExecutableManagementWindow`` (plus its
``LauncherExecutableEditorWindow`` as a pop-up). Rewritten opens it from two
buttons in the settings window; this port opens it from the same two buttons
in Options.

The window owns three things: what is listed (built-in names for the active
game plus the user's registrations), the registrations themselves, and which
entry is in use. ``custom`` and ``selected_path`` are read back by the caller,
which persists them.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..core import executables as exe
from ..core.executables import ExecutableEntry, ExecutableError
from .info_dialog import InfoDialog
from .theme import DEFAULT_COLORS, ui_font

TARGET_TITLES = {
    "game": "Manage game clients",
    "world_builder": "Manage World Builders",
}

IN_USE = "In use"
BUILT_IN = "Built-in"
CUSTOM = "Custom"
MISSING = "The selected executable is missing or cannot be used."


class ExecutableManagementWindow(QDialog):
    """List every executable of one target and let the user change the list."""

    def __init__(
        self,
        game_dir: Path | str,
        mode: str,
        target: str,
        custom: list[dict] | None = None,
        selected_path: str = "",
        parent=None,
    ):
        super().__init__(parent)
        self._game_dir = Path(game_dir)
        self._mode = mode
        self._target = target
        self._custom: list[dict] = [dict(item) for item in (custom or [])]
        self._selected_path = selected_path

        self.setWindowTitle(TARGET_TITLES.get(target, "Executables"))
        self.setFixedSize(720, 560)
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.WindowCloseButtonHint)
        self._build_ui()
        self._refresh()

    # ---- public results -----------------------------------------------------

    @property
    def custom(self) -> list[dict]:
        """The registrations after the window closed."""
        return self._custom

    @property
    def selected_path(self) -> str:
        """The stored value the launcher should use; "" means undecided."""
        return self._selected_path

    # ---- construction -------------------------------------------------------

    def _build_ui(self) -> None:
        c = DEFAULT_COLORS
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(8)

        title = QLabel(TARGET_TITLES.get(self._target, "Executables"), self)
        title.setFont(ui_font(13, bold=True))
        title.setStyleSheet(f"color: {c.text};")
        layout.addWidget(title)

        tools = QWidget(self)
        tools_row = QHBoxLayout(tools)
        tools_row.setContentsMargins(0, 0, 0, 0)
        tools_row.setSpacing(8)

        self._search = QLineEdit(tools)
        self._search.setPlaceholderText("Search by name or path")
        self._search.setFont(ui_font(10))
        self._search.textChanged.connect(lambda _t: self._refresh())
        tools_row.addWidget(self._search, 1)

        add_button = QPushButton("Add executable", tools)
        add_button.setFont(ui_font(10))
        add_button.clicked.connect(self._on_add)
        tools_row.addWidget(add_button)

        layout.addWidget(tools)

        self._tree = QTreeWidget(self)
        self._tree.setColumnCount(3)
        self._tree.setHeaderLabels(["Executable", "Path", ""])
        self._tree.setColumnWidth(0, 230)
        self._tree.setColumnWidth(1, 300)
        self._tree.setRootIsDecorated(False)
        self._tree.setAlternatingRowColors(True)
        self._tree.setFont(ui_font(10))
        self._tree.itemSelectionChanged.connect(self._update_buttons)
        self._tree.itemDoubleClicked.connect(lambda *_: self._on_edit())
        layout.addWidget(self._tree, 1)

        location = QLabel(f"Game directory: {self._game_dir}", self)
        location.setFont(ui_font(8))
        location.setStyleSheet(f"color: {c.inactive_border};")
        location.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(location)

        footer = QWidget(self)
        row = QHBoxLayout(footer)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)

        self._use_button = QPushButton("Use selected", footer)
        self._edit_button = QPushButton("Edit…", footer)
        self._remove_button = QPushButton("Remove", footer)
        for button in (self._use_button, self._edit_button, self._remove_button):
            button.setFont(ui_font(10))
            row.addWidget(button)

        self._use_button.clicked.connect(self._on_use)
        self._edit_button.clicked.connect(self._on_edit)
        self._remove_button.clicked.connect(self._on_remove)

        row.addStretch(1)

        close_button = QPushButton("Close", footer)
        close_button.setFont(ui_font(10, bold=True))
        close_button.clicked.connect(self.accept)
        row.addWidget(close_button)

        layout.addWidget(footer)

        self._use_button.setEnabled(False)
        self._edit_button.setEnabled(False)
        self._remove_button.setEnabled(False)

    # ---- list ---------------------------------------------------------------

    def _entries(self) -> list[ExecutableEntry]:
        return exe.discover(
            self._game_dir, self._mode, self._target, self._custom
        )

    def _refresh(self, keep_path: str = "") -> None:
        """Rebuild the rows. ``keep_path`` keeps a row selected across edits."""
        selected = keep_path or self._current_path()
        entries = self._entries()
        needle = self._search.text().strip().casefold()

        self._tree.clear()
        for entry in entries:
            if needle and needle not in entry.display_name.casefold() and needle not in entry.path.casefold():
                continue
            item = QTreeWidgetItem(self._tree)
            marker = "✕ " if not entry.available else ""
            item.setText(0, f"{marker}{entry.display_name}")
            item.setText(1, entry.path)
            item.setText(2, IN_USE if entry.path == self._selected_path else BUILT_IN if entry.built_in else CUSTOM)
            item.setData(0, Qt.ItemDataRole.UserRole, entry.path)
            item.setData(0, Qt.ItemDataRole.UserRole + 1, entry.kind)
            if not entry.available:
                item.setToolTip(0, MISSING)
                item.setForeground(0, c_brush(DEFAULT_COLORS.error))
            elif entry.path == self._selected_path:
                item.setForeground(0, c_brush(DEFAULT_COLORS.active))
            self._tree.addTopLevelItem(item)

        self._restore_selection(selected)
        self._update_buttons()

    def _restore_selection(self, path: str) -> None:
        if not path:
            return
        for index in range(self._tree.topLevelItemCount()):
            item = self._tree.topLevelItem(index)
            if item.data(0, Qt.ItemDataRole.UserRole) == path:
                self._tree.setCurrentItem(item)
                return

    def _current_path(self) -> str:
        item = self._tree.currentItem()
        return item.data(0, Qt.ItemDataRole.UserRole) if item else ""

    def _current_entry(self) -> ExecutableEntry | None:
        wanted = self._current_path()
        if not wanted:
            return None
        for entry in self._entries():
            if entry.path == wanted:
                return entry
        return None

    def _update_buttons(self) -> None:
        entry = self._current_entry()
        usable = entry is not None and entry.available
        self._use_button.setEnabled(bool(usable and entry.path != self._selected_path))
        self._edit_button.setEnabled(bool(entry and entry.can_edit))
        self._remove_button.setEnabled(bool(entry and entry.can_remove))

    # ---- actions ------------------------------------------------------------

    def _on_use(self) -> None:
        entry = self._current_entry()
        if entry is None or not entry.available:
            return
        self._selected_path = entry.path
        self._refresh(keep_path=entry.path)

    def _on_add(self) -> None:
        draft = self._edit_dialog("", "", "Add executable")
        if draft is None:
            return
        name, path = draft
        self._custom.append({"display_name": name, "path": path})
        if not self._selected_path:
            self._selected_path = path
        self._refresh(keep_path=path)

    def _on_edit(self) -> None:
        entry = self._current_entry()
        if entry is None or not entry.can_edit:
            return
        draft = self._edit_dialog(
            entry.display_name, entry.path, "Edit executable"
        )
        if draft is None:
            return
        name, path = draft
        was_selected = self._selected_path == entry.path
        self._custom = [
            {"display_name": name, "path": path}
            if item.get("path", "").casefold() == entry.path.casefold()
            else item
            for item in self._custom
        ]
        if was_selected:
            self._selected_path = path
        self._refresh(keep_path=path)

    def _on_remove(self) -> None:
        entry = self._current_entry()
        if entry is None or not entry.can_remove:
            return
        if not InfoDialog.question(
            self,
            "Remove executable",
            f"Remove “{entry.display_name}” from the list?\n"
            "The file itself is not deleted.",
            continue_text="Remove",
            default_button=False,
        ):
            return
        self._custom = [
            item
            for item in self._custom
            if item.get("path", "").casefold() != entry.path.casefold()
        ]
        if self._selected_path.casefold() == entry.path.casefold():
            self._selected_path = ""
        self._refresh()

    def _edit_dialog(
        self, display_name: str, path: str, title: str
    ) -> tuple[str, str] | None:
        """Ask for a name and a file. Returns the validated pair, or None."""
        from PySide6.QtWidgets import QFileDialog, QFormLayout

        editor = QDialog(self)
        editor.setWindowTitle(title)
        editor.setFixedWidth(520)
        body = QVBoxLayout(editor)
        form = QFormLayout()
        form.setSpacing(10)

        path_edit = QLineEdit(path, editor)
        path_edit.setPlaceholderText("generalszh.exe or /full/path/to/file.exe")
        path_edit.setFont(ui_font(10))
        browse = QPushButton("Browse…", editor)
        browse.setFont(ui_font(10))
        path_row = QWidget(editor)
        path_row_layout = QHBoxLayout(path_row)
        path_row_layout.setContentsMargins(0, 0, 0, 0)
        path_row_layout.setSpacing(6)
        path_row_layout.addWidget(path_edit, 1)
        path_row_layout.addWidget(browse)

        name_edit = QLineEdit(display_name, editor)
        name_edit.setPlaceholderText("Display name")
        name_edit.setFont(ui_font(10))

        message = QLabel("", editor)
        message.setFont(ui_font(9))
        message.setStyleSheet(f"color: {DEFAULT_COLORS.error};")
        message.setWordWrap(True)

        form.addRow("Executable file", path_row)
        form.addRow("Display name", name_edit)
        form.addRow("", message)
        body.addLayout(form)

        def on_browse() -> None:
            chosen, _ = QFileDialog.getOpenFileName(
                editor,
                "Select executable",
                str(self._game_dir),
                "Executables (*.exe);;All files (*)",
            )
            if chosen:
                path_edit.setText(chosen)
                if not name_edit.text().strip():
                    name_edit.setText(Path(chosen).stem)

        browse.clicked.connect(on_browse)

        footer = QHBoxLayout()
        footer.addStretch(1)
        cancel = QPushButton("Cancel", editor)
        cancel.clicked.connect(editor.reject)
        save = QPushButton("Save", editor)
        save.setDefault(True)
        footer.addWidget(cancel)
        footer.addWidget(save)
        body.addLayout(footer)

        draft: list[tuple[str, str]] = []

        def on_save() -> None:
            try:
                name, stored = exe.validate_registration(
                    self._game_dir,
                    name_edit.text(),
                    path_edit.text(),
                    self._entries(),
                    editing=path,
                )
            except ExecutableError as exc:
                message.setText(str(exc))
                return
            draft.append((name, stored))
            editor.accept()

        save.clicked.connect(on_save)

        if editor.exec() != int(QDialog.DialogCode.Accepted) or not draft:
            return None
        return draft[0]


def c_brush(color: str):
    """A QBrush for a theme colour (kept in one place for the row painting)."""
    from PySide6.QtGui import QBrush, QColor

    return QBrush(QColor(color))
