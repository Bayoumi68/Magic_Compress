"""Reusable dialogs: progress, password prompt, create-archive and extract."""

from __future__ import annotations

import os
from dataclasses import dataclass

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..core.model import CREATABLE_FORMATS, ArchiveFormat, CompressionLevel
from ..core.registry import default_extension
from .prefs import Prefs, apply_runtime_prefs


# ---------------------------------------------------------------------------
# Progress
# ---------------------------------------------------------------------------
class ProgressDialog(QDialog):
    """Modal progress with an indeterminate-until-known bar and a Cancel button."""

    cancel_requested = Signal()

    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.setMinimumWidth(460)
        self.setWindowFlag(Qt.WindowContextHelpButtonHint, False)
        self._cancelled = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(12)

        self._heading = QLabel(title)
        self._heading.setStyleSheet("font-size: 14px; font-weight: 600;")
        layout.addWidget(self._heading)

        self._bar = QProgressBar()
        self._bar.setRange(0, 0)  # indeterminate until first concrete progress
        layout.addWidget(self._bar)

        self._file = QLabel("Starting…")
        self._file.setStyleSheet("color: #6a7280;")
        layout.addWidget(self._file)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self._cancel_btn = QPushButton("Cancel")
        self._cancel_btn.clicked.connect(self._on_cancel)
        buttons.addWidget(self._cancel_btn)
        layout.addLayout(buttons)

    def on_progress(self, current: int, total: int) -> None:
        if total <= 0:
            self._bar.setRange(0, 0)
        else:
            self._bar.setRange(0, total)
            self._bar.setValue(current)

    def on_message(self, message: str) -> None:
        metrics = self._file.fontMetrics()
        self._file.setText(metrics.elidedText(message, Qt.ElideMiddle, 400))

    def _on_cancel(self) -> None:
        self._cancelled = True
        self._cancel_btn.setEnabled(False)
        self._file.setText("Cancelling…")
        self.cancel_requested.emit()

    @property
    def cancelled(self) -> bool:
        return self._cancelled


# ---------------------------------------------------------------------------
# Password
# ---------------------------------------------------------------------------
def ask_password(parent, subtitle: str = "This archive is encrypted.") -> str | None:
    dlg = QDialog(parent)
    dlg.setWindowTitle("Password required")
    dlg.setModal(True)
    dlg.setMinimumWidth(380)
    dlg.setWindowFlag(Qt.WindowContextHelpButtonHint, False)

    layout = QVBoxLayout(dlg)
    layout.setContentsMargins(20, 18, 20, 18)
    layout.setSpacing(10)

    label = QLabel(subtitle)
    label.setWordWrap(True)
    layout.addWidget(label)

    field = QLineEdit()
    field.setEchoMode(QLineEdit.Password)
    field.setPlaceholderText("Enter password")
    layout.addWidget(field)

    show = QCheckBox("Show password")
    show.toggled.connect(lambda on: field.setEchoMode(QLineEdit.Normal if on else QLineEdit.Password))
    layout.addWidget(show)

    box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
    box.accepted.connect(dlg.accept)
    box.rejected.connect(dlg.reject)
    layout.addWidget(box)

    field.returnPressed.connect(dlg.accept)
    field.setFocus()

    if dlg.exec() == QDialog.Accepted:
        return field.text()
    return None


# ---------------------------------------------------------------------------
# Comment
# ---------------------------------------------------------------------------
def show_comment_dialog(parent, comment: str, editable: bool) -> str | None:
    """Show the archive comment. Returns the new text if edited & saved, else None."""
    dlg = QDialog(parent)
    dlg.setWindowTitle("Archive comment")
    dlg.setModal(True)
    dlg.setMinimumSize(460, 300)
    dlg.setWindowFlag(Qt.WindowContextHelpButtonHint, False)

    layout = QVBoxLayout(dlg)
    layout.setContentsMargins(18, 16, 18, 16)
    layout.setSpacing(10)

    heading = QLabel("Comment" if editable else "Comment (read-only)")
    heading.setStyleSheet("font-weight: 600;")
    layout.addWidget(heading)

    editor = QPlainTextEdit()
    editor.setPlainText(comment)
    editor.setReadOnly(not editable)
    if not comment:
        editor.setPlaceholderText("This archive has no comment." if not editable
                                  else "Type a comment to store in the archive…")
    layout.addWidget(editor, 1)

    if editable:
        box = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        box.button(QDialogButtonBox.Save).setObjectName("Primary")
        box.accepted.connect(dlg.accept)
        box.rejected.connect(dlg.reject)
    else:
        box = QDialogButtonBox(QDialogButtonBox.Close)
        box.rejected.connect(dlg.reject)
        box.clicked.connect(dlg.reject)
    layout.addWidget(box)

    if dlg.exec() == QDialog.Accepted and editable:
        return editor.toPlainText()
    return None


# ---------------------------------------------------------------------------
# Create archive
# ---------------------------------------------------------------------------
_UNIT_BYTES = {"KB": 1024, "MB": 1024 ** 2, "GB": 1024 ** 3}


@dataclass
class CreateConfig:
    dest: str
    format: ArchiveFormat
    level: CompressionLevel
    password: str | None
    encrypt_names: bool
    sources: list[str]
    volume_size: int | None = None


class CreateArchiveDialog(QDialog):
    def __init__(self, parent=None, initial_sources: list[str] | None = None,
                 default_format: ArchiveFormat | None = None,
                 default_level: CompressionLevel | None = None):
        super().__init__(parent)
        self.setWindowTitle("Create archive")
        self.setModal(True)
        self.setMinimumWidth(560)
        self.setWindowFlag(Qt.WindowContextHelpButtonHint, False)

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(12)

        # --- sources -----------------------------------------------------
        src_group = QGroupBox("Files and folders to compress")
        src_layout = QHBoxLayout(src_group)
        self._sources = QListWidget()
        self._sources.setSelectionMode(QListWidget.ExtendedSelection)
        src_layout.addWidget(self._sources, 1)

        src_buttons = QVBoxLayout()
        add_files = QPushButton("Add Files…")
        add_folder = QPushButton("Add Folder…")
        remove = QPushButton("Remove")
        add_files.clicked.connect(self._add_files)
        add_folder.clicked.connect(self._add_folder)
        remove.clicked.connect(self._remove_selected)
        for b in (add_files, add_folder, remove):
            src_buttons.addWidget(b)
        src_buttons.addStretch(1)
        src_layout.addLayout(src_buttons)
        root.addWidget(src_group, 1)

        # --- options -----------------------------------------------------
        opt_group = QGroupBox("Options")
        form = QFormLayout(opt_group)
        form.setSpacing(9)

        self._format = QComboBox()
        for i, fmt in enumerate(CREATABLE_FORMATS):
            self._format.addItem(fmt.label, fmt)
            if default_format is not None and fmt == default_format:
                self._format.setCurrentIndex(i)
        self._format.currentIndexChanged.connect(self._on_format_changed)
        form.addRow("Format:", self._format)

        self._level = QComboBox()
        for lvl in CompressionLevel:
            self._level.addItem(lvl.label, lvl)
        self._level.setCurrentIndex(
            list(CompressionLevel).index(default_level or CompressionLevel.NORMAL))
        form.addRow("Compression:", self._level)

        dest_row = QHBoxLayout()
        self._dest = QLineEdit()
        self._dest.setPlaceholderText("Choose where to save the archive")
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse_dest)
        dest_row.addWidget(self._dest, 1)
        dest_row.addWidget(browse)
        dest_widget = QWidget()
        dest_widget.setLayout(dest_row)
        form.addRow("Save as:", dest_widget)

        self._password = QLineEdit()
        self._password.setEchoMode(QLineEdit.Password)
        self._password.setPlaceholderText("Leave empty for no encryption")
        show_pw = QCheckBox("Show")
        show_pw.toggled.connect(
            lambda on: self._password.setEchoMode(QLineEdit.Normal if on else QLineEdit.Password))
        pw_row = QHBoxLayout()
        pw_row.addWidget(self._password, 1)
        pw_row.addWidget(show_pw)
        pw_widget = QWidget()
        pw_widget.setLayout(pw_row)
        self._pw_label = QLabel("Password:")
        form.addRow(self._pw_label, pw_widget)

        self._encrypt_names = QCheckBox("Also encrypt file names (7-Zip only)")
        form.addRow("", self._encrypt_names)

        split_row = QHBoxLayout()
        self._split_check = QCheckBox("Split into volumes of")
        self._split_size = QSpinBox()
        self._split_size.setRange(1, 1024 * 1024)
        self._split_size.setValue(100)
        self._split_unit = QComboBox()
        self._split_unit.addItems(list(_UNIT_BYTES.keys()))
        self._split_unit.setCurrentText("MB")
        self._split_size.setEnabled(False)
        self._split_unit.setEnabled(False)
        self._split_check.toggled.connect(self._update_split_enabled)
        split_row.addWidget(self._split_check)
        split_row.addWidget(self._split_size)
        split_row.addWidget(self._split_unit)
        split_row.addStretch(1)
        split_widget = QWidget()
        split_widget.setLayout(split_row)
        self._split_label = QLabel("Split (7-Zip):")
        form.addRow(self._split_label, split_widget)

        root.addWidget(opt_group)

        # --- buttons -----------------------------------------------------
        box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        box.button(QDialogButtonBox.Ok).setText("Create")
        box.button(QDialogButtonBox.Ok).setObjectName("Primary")
        box.accepted.connect(self._on_accept)
        box.rejected.connect(self.reject)
        root.addWidget(box)

        for path in initial_sources or []:
            self._append_source(path)
        self._on_format_changed()
        self._maybe_suggest_dest()

    # -- source management ----------------------------------------------
    def _append_source(self, path: str) -> None:
        path = os.path.abspath(path)
        existing = {self._sources.item(i).text() for i in range(self._sources.count())}
        if path not in existing and os.path.exists(path):
            self._sources.addItem(path)

    def add_sources(self, paths: list[str]) -> None:
        """Append more sources to an already-open dialog (multi-select coalescing)."""
        for p in paths:
            self._append_source(p)
        self._maybe_suggest_dest()
        self.raise_()
        self.activateWindow()

    def _add_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, "Add files")
        for p in paths:
            self._append_source(p)
        self._maybe_suggest_dest()

    def _add_folder(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Add folder")
        if path:
            self._append_source(path)
            self._maybe_suggest_dest()

    def _remove_selected(self) -> None:
        for item in self._sources.selectedItems():
            self._sources.takeItem(self._sources.row(item))

    def _source_paths(self) -> list[str]:
        return [self._sources.item(i).text() for i in range(self._sources.count())]

    # -- dest / format --------------------------------------------------
    def _current_format(self) -> ArchiveFormat:
        data = self._format.currentData()
        return data if isinstance(data, ArchiveFormat) else ArchiveFormat(data)

    def _maybe_suggest_dest(self) -> None:
        if self._dest.text().strip():
            return
        paths = self._source_paths()
        if not paths:
            return
        first = paths[0]
        base_dir = os.path.dirname(first.rstrip("\\/"))
        stem = os.path.basename(first.rstrip("\\/")) or "archive"
        stem = os.path.splitext(stem)[0] or "archive"
        ext = default_extension(self._current_format())
        self._dest.setText(os.path.join(base_dir, stem + ext))

    def _on_format_changed(self) -> None:
        fmt = self._current_format()
        can_encrypt = fmt in (ArchiveFormat.ZIP, ArchiveFormat.SEVENZIP)
        self._password.setEnabled(can_encrypt)
        self._pw_label.setEnabled(can_encrypt)
        if not can_encrypt:
            self._password.clear()
        self._encrypt_names.setEnabled(fmt == ArchiveFormat.SEVENZIP)
        if fmt != ArchiveFormat.SEVENZIP:
            self._encrypt_names.setChecked(False)
        # Splitting into volumes is a 7-Zip-only capability.
        is_7z = fmt == ArchiveFormat.SEVENZIP
        self._split_label.setEnabled(is_7z)
        self._split_check.setEnabled(is_7z)
        if not is_7z:
            self._split_check.setChecked(False)
        self._update_split_enabled()
        # Swap the extension on the suggested destination.
        dest = self._dest.text().strip()
        if dest:
            base = dest
            for known in (".tar.gz", ".tar.bz2", ".tar.xz", ".tar", ".zip", ".7z"):
                if base.lower().endswith(known):
                    base = base[: -len(known)]
                    break
            else:
                base = os.path.splitext(base)[0]
            self._dest.setText(base + default_extension(fmt))

    def _update_split_enabled(self) -> None:
        on = self._split_check.isChecked() and self._split_check.isEnabled()
        self._split_size.setEnabled(on)
        self._split_unit.setEnabled(on)

    def _browse_dest(self) -> None:
        fmt = self._current_format()
        ext = default_extension(fmt)
        start = self._dest.text().strip() or os.path.expanduser("~")
        path, _ = QFileDialog.getSaveFileName(
            self, "Save archive as", start, f"{fmt.label} archive (*{ext});;All files (*)",
            "", QFileDialog.Option.DontConfirmOverwrite)
        if path:
            if not path.lower().endswith(ext):
                path += ext
            self._dest.setText(path)

    # -- accept ---------------------------------------------------------
    def _on_accept(self) -> None:
        if not self._source_paths():
            QMessageBox.warning(self, "Nothing to compress", "Add at least one file or folder.")
            return
        if not self._dest.text().strip():
            QMessageBox.warning(self, "No destination", "Choose where to save the archive.")
            return
        self.accept()

    def config(self) -> CreateConfig:
        pw = self._password.text()
        level = self._level.currentData()
        if not isinstance(level, CompressionLevel):
            level = CompressionLevel(int(level))
        volume_size = None
        if self._split_check.isChecked() and self._split_check.isEnabled():
            volume_size = self._split_size.value() * _UNIT_BYTES[self._split_unit.currentText()]
        return CreateConfig(
            dest=self._dest.text().strip(),
            format=self._current_format(),
            level=level,
            password=pw if pw else None,
            encrypt_names=self._encrypt_names.isChecked(),
            sources=self._source_paths(),
            volume_size=volume_size,
        )


# ---------------------------------------------------------------------------
# Extract
# ---------------------------------------------------------------------------
@dataclass
class ExtractConfig:
    dest: str
    open_after: bool


class ExtractDialog(QDialog):
    def __init__(self, parent=None, suggested_dest: str = "", selected_count: int = 0,
                 open_after_default: bool = True):
        super().__init__(parent)
        self.setWindowTitle("Extract")
        self.setModal(True)
        self.setMinimumWidth(480)
        self.setWindowFlag(Qt.WindowContextHelpButtonHint, False)

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(12)

        heading = QLabel(
            f"Extract {selected_count} selected item(s)" if selected_count else "Extract all files")
        heading.setStyleSheet("font-size: 14px; font-weight: 600;")
        root.addWidget(heading)

        form = QFormLayout()
        dest_row = QHBoxLayout()
        self._dest = QLineEdit(suggested_dest)
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        dest_row.addWidget(self._dest, 1)
        dest_row.addWidget(browse)
        dest_widget = QWidget()
        dest_widget.setLayout(dest_row)
        form.addRow("Destination:", dest_widget)
        root.addLayout(form)

        self._open_after = QCheckBox("Open destination folder when done")
        self._open_after.setChecked(open_after_default)
        root.addWidget(self._open_after)

        box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        box.button(QDialogButtonBox.Ok).setText("Extract")
        box.button(QDialogButtonBox.Ok).setObjectName("Primary")
        box.accepted.connect(self._on_accept)
        box.rejected.connect(self.reject)
        root.addWidget(box)

    def _browse(self) -> None:
        start = self._dest.text().strip() or os.path.expanduser("~")
        path = QFileDialog.getExistingDirectory(self, "Extract to", start)
        if path:
            self._dest.setText(path)

    def _on_accept(self) -> None:
        if not self._dest.text().strip():
            QMessageBox.warning(self, "No destination", "Choose a destination folder.")
            return
        self.accept()

    def config(self) -> ExtractConfig:
        return ExtractConfig(dest=self._dest.text().strip(), open_after=self._open_after.isChecked())


# ---------------------------------------------------------------------------
# Convert / repackage
# ---------------------------------------------------------------------------
@dataclass
class ConvertConfig:
    dest: str
    format: ArchiveFormat
    level: CompressionLevel
    password: str | None
    encrypt_names: bool
    open_after: bool


class ConvertArchiveDialog(QDialog):
    def __init__(self, parent=None, source_path: str = "", source_format: ArchiveFormat | None = None):
        super().__init__(parent)
        self.setWindowTitle("Convert archive")
        self.setModal(True)
        self.setMinimumWidth(520)
        self.setWindowFlag(Qt.WindowContextHelpButtonHint, False)
        self._source_path = source_path

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(12)

        heading = QLabel(f"Repackage “{os.path.basename(source_path)}” as a new archive:")
        heading.setWordWrap(True)
        heading.setStyleSheet("font-size: 14px; font-weight: 600;")
        root.addWidget(heading)

        form = QFormLayout()
        form.setSpacing(9)

        self._format = QComboBox()
        for fmt in CREATABLE_FORMATS:
            self._format.addItem(fmt.label, fmt)
        # Default to a format different from the source when possible.
        default_idx = 0
        for i, fmt in enumerate(CREATABLE_FORMATS):
            if fmt != source_format:
                default_idx = i
                break
        self._format.setCurrentIndex(default_idx)
        self._format.currentIndexChanged.connect(self._on_format_changed)
        form.addRow("Convert to:", self._format)

        self._level = QComboBox()
        for lvl in CompressionLevel:
            self._level.addItem(lvl.label, lvl)
        self._level.setCurrentIndex(list(CompressionLevel).index(CompressionLevel.NORMAL))
        form.addRow("Compression:", self._level)

        dest_row = QHBoxLayout()
        self._dest = QLineEdit()
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse_dest)
        dest_row.addWidget(self._dest, 1)
        dest_row.addWidget(browse)
        dest_widget = QWidget()
        dest_widget.setLayout(dest_row)
        form.addRow("Save as:", dest_widget)

        self._password = QLineEdit()
        self._password.setEchoMode(QLineEdit.Password)
        self._password.setPlaceholderText("Leave empty for no encryption")
        show_pw = QCheckBox("Show")
        show_pw.toggled.connect(
            lambda on: self._password.setEchoMode(QLineEdit.Normal if on else QLineEdit.Password))
        pw_row = QHBoxLayout()
        pw_row.addWidget(self._password, 1)
        pw_row.addWidget(show_pw)
        pw_widget = QWidget()
        pw_widget.setLayout(pw_row)
        self._pw_label = QLabel("Password:")
        form.addRow(self._pw_label, pw_widget)

        self._encrypt_names = QCheckBox("Also encrypt file names (7-Zip only)")
        form.addRow("", self._encrypt_names)
        root.addLayout(form)

        self._open_after = QCheckBox("Open the new archive when done")
        self._open_after.setChecked(True)
        root.addWidget(self._open_after)

        box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        box.button(QDialogButtonBox.Ok).setText("Convert")
        box.button(QDialogButtonBox.Ok).setObjectName("Primary")
        box.accepted.connect(self._on_accept)
        box.rejected.connect(self.reject)
        root.addWidget(box)

        self._on_format_changed()
        self._suggest_dest()

    def _current_format(self) -> ArchiveFormat:
        data = self._format.currentData()
        return data if isinstance(data, ArchiveFormat) else ArchiveFormat(data)

    def _stem(self) -> str:
        name = os.path.basename(self._source_path.rstrip("\\/")) or "archive"
        for ext in (".tar.gz", ".tar.bz2", ".tar.xz", ".tar", ".zip", ".7z", ".rar"):
            if name.lower().endswith(ext):
                return name[: -len(ext)]
        return os.path.splitext(name)[0] or "archive"

    def _suggest_dest(self) -> None:
        base_dir = os.path.dirname(self._source_path)
        ext = default_extension(self._current_format())
        self._dest.setText(os.path.join(base_dir, self._stem() + ext))

    def _on_format_changed(self) -> None:
        fmt = self._current_format()
        can_encrypt = fmt in (ArchiveFormat.ZIP, ArchiveFormat.SEVENZIP)
        self._password.setEnabled(can_encrypt)
        self._pw_label.setEnabled(can_encrypt)
        if not can_encrypt:
            self._password.clear()
        self._encrypt_names.setEnabled(fmt == ArchiveFormat.SEVENZIP)
        if fmt != ArchiveFormat.SEVENZIP:
            self._encrypt_names.setChecked(False)
        self._suggest_dest()

    def _browse_dest(self) -> None:
        fmt = self._current_format()
        ext = default_extension(fmt)
        start = self._dest.text().strip() or os.path.expanduser("~")
        path, _ = QFileDialog.getSaveFileName(
            self, "Save converted archive as", start, f"{fmt.label} archive (*{ext});;All files (*)",
            "", QFileDialog.Option.DontConfirmOverwrite)
        if path:
            if not path.lower().endswith(ext):
                path += ext
            self._dest.setText(path)

    def _on_accept(self) -> None:
        dest = self._dest.text().strip()
        if not dest:
            QMessageBox.warning(self, "No destination", "Choose where to save the converted archive.")
            return
        if os.path.abspath(dest) == os.path.abspath(self._source_path):
            QMessageBox.warning(self, "Same file",
                                "Choose a destination different from the source archive.")
            return
        self.accept()

    def config(self) -> ConvertConfig:
        level = self._level.currentData()
        if not isinstance(level, CompressionLevel):
            level = CompressionLevel(int(level))
        pw = self._password.text()
        return ConvertConfig(
            dest=self._dest.text().strip(),
            format=self._current_format(),
            level=level,
            password=pw if pw else None,
            encrypt_names=self._encrypt_names.isChecked(),
            open_after=self._open_after.isChecked(),
        )


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------
class SettingsDialog(QDialog):
    """Rich preferences: appearance, create defaults, and Explorer integration."""

    def __init__(self, parent=None, prefs: Prefs | None = None):
        super().__init__(parent)
        self._prefs = prefs or Prefs()
        self.setWindowTitle("Settings")
        self.setModal(True)
        self.setMinimumWidth(540)
        self.setWindowFlag(Qt.WindowContextHelpButtonHint, False)

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(12)

        # --- Appearance & behaviour -------------------------------------
        general = QGroupBox("Appearance & behaviour")
        gform = QFormLayout(general)
        gform.setSpacing(9)

        self._theme = QComboBox()
        self._theme.addItem("Light", False)
        self._theme.addItem("Dark", True)
        self._theme.setCurrentIndex(1 if self._prefs.get("dark") else 0)
        gform.addRow("Theme:", self._theme)

        self._confirm_delete = QCheckBox("Ask before deleting items from an archive")
        self._confirm_delete.setChecked(self._prefs.get("confirm_delete"))
        gform.addRow("", self._confirm_delete)

        self._remember_geometry = QCheckBox("Remember window size and position")
        self._remember_geometry.setChecked(self._prefs.get("remember_geometry"))
        gform.addRow("", self._remember_geometry)

        self._doubleclick_open = QCheckBox("Double-click a file to open it")
        self._doubleclick_open.setChecked(self._prefs.get("doubleclick_open"))
        gform.addRow("", self._doubleclick_open)

        self._open_after = QCheckBox("Open destination folder after extracting (default)")
        self._open_after.setChecked(self._prefs.get("open_after_extract"))
        gform.addRow("", self._open_after)
        root.addWidget(general)

        # --- Create defaults --------------------------------------------
        defaults = QGroupBox("New-archive defaults")
        dform = QFormLayout(defaults)
        dform.setSpacing(9)

        self._default_format = QComboBox()
        for fmt in CREATABLE_FORMATS:
            self._default_format.addItem(fmt.label, fmt)
        cur_fmt = self._prefs.default_format
        for i, fmt in enumerate(CREATABLE_FORMATS):
            if fmt == cur_fmt:
                self._default_format.setCurrentIndex(i)
                break
        dform.addRow("Default format:", self._default_format)

        self._default_level = QComboBox()
        for lvl in CompressionLevel:
            self._default_level.addItem(lvl.label, lvl)
        self._default_level.setCurrentIndex(list(CompressionLevel).index(self._prefs.default_level))
        dform.addRow("Default compression:", self._default_level)
        root.addWidget(defaults)

        # --- Explorer integration ---------------------------------------
        integ = QGroupBox("Explorer integration (Windows)")
        iform = QFormLayout(integ)
        iform.setSpacing(9)

        unrar_row = QHBoxLayout()
        self._unrar = QLineEdit(self._prefs.get("unrar_path"))
        self._unrar.setPlaceholderText("Path to unrar.exe or 7z.exe (optional, for RAR)")
        unrar_browse = QPushButton("Browse…")
        unrar_browse.clicked.connect(self._browse_unrar)
        unrar_row.addWidget(self._unrar, 1)
        unrar_row.addWidget(unrar_browse)
        unrar_widget = QWidget()
        unrar_widget.setLayout(unrar_row)
        iform.addRow("RAR tool:", unrar_widget)

        self._assoc_status = QLabel()
        self._assoc_status.setWordWrap(True)
        iform.addRow("File types:", self._assoc_status)

        assoc_row = QHBoxLayout()
        self._btn_register = QPushButton("Register with Explorer")
        self._btn_unregister = QPushButton("Remove")
        self._btn_register.clicked.connect(self._do_register)
        self._btn_unregister.clicked.connect(self._do_unregister)
        assoc_row.addWidget(self._btn_register)
        assoc_row.addWidget(self._btn_unregister)
        assoc_row.addStretch(1)
        assoc_widget = QWidget()
        assoc_widget.setLayout(assoc_row)
        iform.addRow("", assoc_widget)
        root.addWidget(integ)

        self._refresh_assoc_status()

        box = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        box.button(QDialogButtonBox.Save).setObjectName("Primary")
        box.accepted.connect(self._on_save)
        box.rejected.connect(self.reject)
        root.addWidget(box)

    # -- integration --------------------------------------------------
    def _refresh_assoc_status(self) -> None:
        import sys
        if sys.platform != "win32":
            self._assoc_status.setText("Only available on Windows.")
            self._btn_register.setEnabled(False)
            self._btn_unregister.setEnabled(False)
            return
        from ..associations import is_registered
        if is_registered():
            self._assoc_status.setText(
                "Registered — Magic Compress appears in Explorer's “Open with” "
                "menu and right-click menu for archives.")
            self._btn_register.setText("Re-register")
            self._btn_unregister.setEnabled(True)
        else:
            self._assoc_status.setText("Not registered.")
            self._btn_register.setText("Register with Explorer")
            self._btn_unregister.setEnabled(False)

    def _do_register(self) -> None:
        try:
            from ..associations import register
            register()
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "Registration failed", str(exc))
        self._refresh_assoc_status()

    def _do_unregister(self) -> None:
        try:
            from ..associations import unregister
            unregister()
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "Removal failed", str(exc))
        self._refresh_assoc_status()

    def _browse_unrar(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Select unrar/7z executable", "", "Executables (*.exe);;All files (*)")
        if path:
            self._unrar.setText(path)

    # -- save ---------------------------------------------------------
    def _on_save(self) -> None:
        self._prefs.set("dark", self._theme.currentData())
        self._prefs.set("confirm_delete", self._confirm_delete.isChecked())
        self._prefs.set("remember_geometry", self._remember_geometry.isChecked())
        self._prefs.set("doubleclick_open", self._doubleclick_open.isChecked())
        self._prefs.set("open_after_extract", self._open_after.isChecked())
        fmt = self._default_format.currentData()
        self._prefs.set("default_format", fmt.value if isinstance(fmt, ArchiveFormat) else str(fmt))
        self._prefs.set("default_level", int(self._default_level.currentData()))
        self._prefs.set("unrar_path", self._unrar.text().strip())
        apply_runtime_prefs(self._prefs)
        self.accept()

    @property
    def dark(self) -> bool:
        return bool(self._prefs.get("dark"))
