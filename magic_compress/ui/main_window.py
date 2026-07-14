"""Main application window — the WinRAR-style archive browser."""

from __future__ import annotations

import glob
import os
import tempfile

from PySide6.QtCore import Qt, QMimeData, QSettings, QTimer, QUrl
from PySide6.QtGui import (
    QAction,
    QDesktopServices,
    QDrag,
    QIcon,
    QKeySequence,
    QPixmap,
    QStandardItem,
    QStandardItemModel,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QSizePolicy,
    QStyle,
    QToolBar,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

from .. import APP_NAME, __version__
from ..resources import asset_path
from ..core.base import (
    ArchiveError,
    OperationCancelled,
    PasswordRequired,
    ToolMissing,
    WrongPassword,
    collect_sources,
)
from ..core.model import ArchiveEntry, CompressionLevel
from ..core.registry import create_archive, detect_format, open_archive, repackage
from ..workers import Task
from .dialogs import (
    ConvertArchiveDialog,
    CreateArchiveDialog,
    ExtractConfig,
    ExtractDialog,
    ProgressDialog,
    SettingsDialog,
    ask_password,
    show_comment_dialog,
)
from .prefs import Prefs, apply_runtime_prefs
from .util import human_ratio, human_size, human_time

_PATH_ROLE = Qt.UserRole
_ISDIR_ROLE = Qt.UserRole + 1

_ARCHIVE_FILTER = (
    "All archives (*.zip *.zipx *.7z *.rar *.tar *.tar.gz *.tgz "
    "*.tar.bz2 *.tbz2 *.tar.xz *.txz);;"
    "ZIP (*.zip *.zipx);;7-Zip (*.7z);;RAR (*.rar);;"
    "TAR family (*.tar *.tar.gz *.tgz *.tar.bz2 *.tbz2 *.tar.xz *.txz);;"
    "All files (*)"
)


class _NumericItem(QStandardItem):
    """A right-aligned cell that sorts by a numeric key rather than its text."""

    def __init__(self, text: str, sort_key: float):
        super().__init__(text)
        self._key = sort_key
        self.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.setEditable(False)

    def __lt__(self, other) -> bool:
        return self._key < getattr(other, "_key", 0.0)


class ArchiveTreeView(QTreeView):
    """Tree that can drag its selection out to Explorer as real files.

    Qt can't hand over deferred/virtual files, so on drag start we ask the
    window to extract the selection to a temp folder and drag those paths.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.export_callback = None  # () -> list[str] | None

    def startDrag(self, supported_actions) -> None:
        if self.export_callback is None:
            super().startDrag(supported_actions)
            return
        paths = self.export_callback()
        if not paths:
            return
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(p) for p in paths])
        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.exec(Qt.CopyAction)


class MainWindow(QMainWindow):
    def __init__(self, dark: bool = False):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.resize(960, 620)
        self.setAcceptDrops(True)
        icon = QIcon(asset_path("icon.png"))
        if not icon.isNull():
            self.setWindowIcon(icon)

        self.handler = None
        self._entries: list[ArchiveEntry] = []
        self._archive_path: str | None = None
        self._password: str | None = None
        self._active_task: Task | None = None
        self._dark = dark
        self._settings = QSettings()
        self._prefs = Prefs()
        self._temp_dirs: list[str] = []  # cleaned up on close
        self._filter = ""
        self._by_path: dict[str, ArchiveEntry] = {}
        self._pending_add: list[str] = []
        self._active_create_dialog = None
        self._add_timer = QTimer(self)
        self._add_timer.setSingleShot(True)
        self._add_timer.setInterval(600)  # coalesce a burst of per-file "add" launches
        self._add_timer.timeout.connect(self._flush_pending_add)
        raw_recent = self._settings.value("recent", [])
        self._recent: list[str] = list(raw_recent) if raw_recent else []

        self._build_actions()
        self._build_toolbar()
        self._build_menus()
        self._build_central()
        self._build_statusbar()
        self._restore_geometry()
        self._update_actions()

    def _restore_geometry(self) -> None:
        if not self._prefs.get("remember_geometry"):
            return
        geo = self._settings.value("geometry")
        if geo is not None:
            self.restoreGeometry(geo)

    # ------------------------------------------------------------------ UI
    def _icon(self, sp):
        return self.style().standardIcon(sp)

    def _build_actions(self) -> None:
        S = QStyle
        self.act_open = QAction(self._icon(S.SP_DirOpenIcon), "Open", self)
        self.act_open.setShortcut(QKeySequence.Open)
        self.act_open.triggered.connect(self.open_dialog)

        self.act_new = QAction(self._icon(S.SP_FileIcon), "New", self)
        self.act_new.setShortcut(QKeySequence.New)
        self.act_new.triggered.connect(self.create_dialog)

        self.act_extract = QAction(self._icon(S.SP_ArrowDown), "Extract To", self)
        self.act_extract.triggered.connect(self.extract_all)

        self.act_extract_here = QAction(self._icon(S.SP_DirLinkIcon), "Extract Here", self)
        self.act_extract_here.setToolTip("Extract everything into the archive's own folder")
        self.act_extract_here.triggered.connect(self.extract_here)

        self.act_extract_sel = QAction(self._icon(S.SP_DialogSaveButton), "Extract Selected", self)
        self.act_extract_sel.triggered.connect(self.extract_selected)

        self.act_add = QAction(self._icon(S.SP_ArrowUp), "Add", self)
        self.act_add.triggered.connect(self.add_files)

        self.act_add_folder = QAction(self._icon(S.SP_DirIcon), "Add Folder", self)
        self.act_add_folder.triggered.connect(self.add_folder)

        self.act_delete = QAction(self._icon(S.SP_TrashIcon), "Delete", self)
        self.act_delete.setShortcut(QKeySequence.Delete)
        self.act_delete.triggered.connect(self.delete_selected)

        self.act_test = QAction(self._icon(S.SP_DialogApplyButton), "Test", self)
        self.act_test.triggered.connect(self.test_archive)

        self.act_refresh = QAction(self._icon(S.SP_BrowserReload), "Refresh", self)
        self.act_refresh.setShortcut(QKeySequence.Refresh)
        self.act_refresh.triggered.connect(self.reload)

        self.act_info = QAction(self._icon(S.SP_FileDialogInfoView), "Info", self)
        self.act_info.setShortcut("Ctrl+I")
        self.act_info.triggered.connect(self.show_archive_info)

        self.act_convert = QAction(self._icon(S.SP_FileDialogDetailedView), "Convert", self)
        self.act_convert.setToolTip("Repackage this archive as a different format")
        self.act_convert.triggered.connect(self.convert_archive)

        self.act_comment = QAction(self._icon(S.SP_FileDialogListView), "Comment", self)
        self.act_comment.setToolTip("View or edit the archive comment")
        self.act_comment.triggered.connect(self.show_comment)

        self.act_find = QAction("Find", self)
        self.act_find.setShortcut(QKeySequence.Find)
        self.act_find.triggered.connect(lambda: (self._search.setFocus(), self._search.selectAll()))
        self.addAction(self.act_find)  # window-level shortcut (Ctrl+F)

        self.act_dark = QAction("Dark mode", self)
        self.act_dark.setCheckable(True)
        self.act_dark.setChecked(self._dark)
        self.act_dark.toggled.connect(self._set_dark)

        self.act_settings = QAction(self._icon(S.SP_FileDialogListView), "Settings…", self)
        self.act_settings.setShortcut("Ctrl+,")
        self.act_settings.triggered.connect(self.open_settings)

        self.act_about = QAction(self._icon(S.SP_MessageBoxInformation), "About", self)
        self.act_about.triggered.connect(self.show_about)

        self.act_exit = QAction("Exit", self)
        self.act_exit.setShortcut(QKeySequence.Quit)
        self.act_exit.triggered.connect(self.close)

    def _build_toolbar(self) -> None:
        tb = QToolBar("Main")
        tb.setMovable(False)
        tb.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        tb.setIconSize(tb.iconSize())
        # Brand logo pinned at the far left (always visible).
        tb.addWidget(self._build_brand(emblem_px=26, font_px=14))
        tb.addSeparator()
        for act in (self.act_open, self.act_new):
            tb.addAction(act)
        tb.addSeparator()
        for act in (self.act_extract, self.act_extract_here, self.act_extract_sel):
            tb.addAction(act)
        tb.addSeparator()
        for act in (self.act_add, self.act_add_folder, self.act_delete, self.act_test):
            tb.addAction(act)
        tb.addSeparator()
        for act in (self.act_info, self.act_refresh):
            tb.addAction(act)
        self.addToolBar(tb)

    def _build_brand(self, emblem_px: int = 22, font_px: int = 13) -> QWidget:
        brand = QWidget()
        row = QHBoxLayout(brand)
        row.setContentsMargins(4, 0, 10, 0)
        row.setSpacing(7)

        emblem = QLabel()
        pixmap = QPixmap(asset_path("icon.png"))
        if not pixmap.isNull():
            emblem.setPixmap(pixmap.scaled(emblem_px, emblem_px,
                                           Qt.KeepAspectRatio, Qt.SmoothTransformation))
            row.addWidget(emblem)

        wordmark = QLabel(
            f"<span style='font-size:{font_px}px; font-weight:700;'>"
            "<span style='color:#2f6df0;'>Magic</span> "
            "<span style='color:#33a457;'>Compress</span></span>")
        row.addWidget(wordmark)
        return brand

    def _build_menus(self) -> None:
        mb = self.menuBar()
        file_menu = mb.addMenu("&File")
        file_menu.addAction(self.act_new)
        file_menu.addAction(self.act_open)
        self._recent_menu = file_menu.addMenu("Open &Recent")
        self._rebuild_recent_menu()
        file_menu.addSeparator()
        file_menu.addAction(self.act_refresh)
        file_menu.addSeparator()
        file_menu.addAction(self.act_settings)
        file_menu.addAction(self.act_exit)

        cmd_menu = mb.addMenu("&Commands")
        cmd_menu.addAction(self.act_extract)
        cmd_menu.addAction(self.act_extract_here)
        cmd_menu.addAction(self.act_extract_sel)
        cmd_menu.addSeparator()
        cmd_menu.addAction(self.act_add)
        cmd_menu.addAction(self.act_add_folder)
        cmd_menu.addAction(self.act_delete)
        cmd_menu.addSeparator()
        cmd_menu.addAction(self.act_convert)
        cmd_menu.addAction(self.act_test)
        cmd_menu.addAction(self.act_info)
        cmd_menu.addAction(self.act_comment)

        view_menu = mb.addMenu("&View")
        view_menu.addAction(self.act_dark)

        help_menu = mb.addMenu("&Help")
        help_menu.addAction(self.act_about)

    def _build_central(self) -> None:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(6)

        self._breadcrumb = QLabel("No archive open — open one or drop files here to create one.")
        self._breadcrumb.setObjectName("Breadcrumb")
        layout.addWidget(self._breadcrumb)

        self._search = QLineEdit()
        self._search.setPlaceholderText("Filter files…  (Ctrl+F)")
        self._search.setClearButtonEnabled(True)
        self._filter_timer = QTimer(self)
        self._filter_timer.setSingleShot(True)
        self._filter_timer.setInterval(150)
        self._filter_timer.timeout.connect(self._apply_filter)
        self._search.textChanged.connect(lambda _: self._filter_timer.start())
        layout.addWidget(self._search)

        self._tree = ArchiveTreeView()
        self._tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self._tree.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._tree.setSortingEnabled(True)
        self._tree.setAlternatingRowColors(True)
        self._tree.setUniformRowHeights(True)
        self._tree.setExpandsOnDoubleClick(True)
        self._tree.doubleClicked.connect(self._on_double_click)
        self._tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self._tree.customContextMenuRequested.connect(self._show_context_menu)
        # Drag selected entries OUT to Explorer/desktop; the window still
        # receives external drops (tree itself doesn't accept them).
        self._tree.setDragEnabled(True)
        self._tree.setDragDropMode(QAbstractItemView.DragOnly)
        self._tree.setAcceptDrops(False)
        self._tree.export_callback = self._export_selection
        layout.addWidget(self._tree, 1)

        self._model = QStandardItemModel(self)
        self._reset_model()
        self.setCentralWidget(container)

    def _build_statusbar(self) -> None:
        self._status = self.statusBar()
        self._status.showMessage("Ready")

    # -------------------------------------------------------------- model
    def _reset_model(self) -> None:
        self._model.clear()
        self._model.setHorizontalHeaderLabels(
            ["Name", "Size", "Packed", "Ratio", "Modified", "CRC32"])
        self._tree.setModel(self._model)
        header = self._tree.header()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        for col in range(1, 6):
            header.setSectionResizeMode(col, QHeaderView.ResizeToContents)
        self._tree.selectionModel().selectionChanged.connect(self._update_actions)

    def _set_entries(self, entries: list[ArchiveEntry]) -> None:
        self._entries = entries
        self._by_path = {e.path: e for e in entries}
        self._rebuild_tree()

    def _match(self, entry: ArchiveEntry) -> bool:
        return (not self._filter) or (self._filter in entry.path.lower())

    def _apply_filter(self) -> None:
        self._filter = self._search.text().strip().lower()
        self._rebuild_tree()

    def _rebuild_tree(self) -> None:
        entries = [e for e in self._entries if self._match(e)]
        self._reset_model()
        root = self._model.invisibleRootItem()
        folder_items: dict[str, QStandardItem] = {"": root}
        dir_icon = self._icon(QStyle.SP_DirIcon)
        file_icon = self._icon(QStyle.SP_FileIcon)

        def ensure_folder(path: str) -> QStandardItem:
            if path in folder_items:
                return folder_items[path]
            parent_path, _, name = path.rpartition("/")
            parent = ensure_folder(parent_path)
            name_item = QStandardItem(dir_icon, name)
            name_item.setEditable(False)
            name_item.setData(path + "/", _PATH_ROLE)
            name_item.setData(True, _ISDIR_ROLE)
            blanks = [_NumericItem("", -1), _NumericItem("", -1),
                      _NumericItem("", -1), QStandardItem(""), QStandardItem("")]
            for b in blanks:
                b.setEditable(False)
            parent.appendRow([name_item, *blanks])
            folder_items[path] = name_item
            return name_item

        for entry in sorted(entries, key=lambda e: e.path.lower()):
            clean = entry.path.rstrip("/")
            if entry.is_dir:
                if clean:
                    ensure_folder(clean)
                continue
            parent_path, _, name = clean.rpartition("/")
            parent = ensure_folder(parent_path) if parent_path else root
            name_item = QStandardItem(file_icon, name or clean)
            name_item.setEditable(False)
            name_item.setData(entry.path, _PATH_ROLE)
            name_item.setData(False, _ISDIR_ROLE)
            if entry.encrypted:
                name_item.setToolTip("Encrypted")
            ratio = entry.ratio
            row = [
                name_item,
                _NumericItem(human_size(entry.size), entry.size),
                _NumericItem(human_size(entry.compressed_size), entry.compressed_size or -1),
                _NumericItem(human_ratio(ratio), ratio if ratio is not None else -1),
                QStandardItem(human_time(entry.modified)),
                QStandardItem((entry.crc or "") + (" 🔒" if entry.encrypted else "")),
            ]
            for cell in row[4:]:
                cell.setEditable(False)
            parent.appendRow(row)

        self._tree.sortByColumn(0, Qt.AscendingOrder)
        if self._filter:
            self._tree.expandAll()
        else:
            self._tree.expandToDepth(0)
        self._update_status_line()

    # ------------------------------------------------------------- helpers
    def _selected_name_items(self) -> list[QStandardItem]:
        items = []
        for index in self._tree.selectionModel().selectedRows(0):
            item = self._model.itemFromIndex(index)
            if item is not None:
                items.append(item)
        return items

    def _selected_paths(self, expand_dirs: bool) -> list[str]:
        """Collect in-archive paths for the current selection.

        With *expand_dirs*, folders are replaced by all file members beneath
        them (used for extraction); otherwise folder paths are kept as-is
        (used for deletion, where handlers prune by prefix).
        """
        result: list[str] = []
        seen: set[str] = set()
        for item in self._selected_name_items():
            path = item.data(_PATH_ROLE)
            is_dir = bool(item.data(_ISDIR_ROLE))
            if is_dir and expand_dirs:
                prefix = path  # ends with "/"
                for entry in self._entries:
                    if not entry.is_dir and entry.path.startswith(prefix) and entry.path not in seen:
                        seen.add(entry.path)
                        result.append(entry.path)
            elif path not in seen:
                seen.add(path)
                result.append(path)
        return result

    def _update_actions(self, *args) -> None:
        has_archive = self.handler is not None
        has_selection = bool(self._selected_name_items())
        can_add = has_archive and getattr(self.handler, "can_add", False)
        can_delete = has_archive and getattr(self.handler, "can_delete", False)
        self.act_extract.setEnabled(has_archive)
        self.act_extract_sel.setEnabled(has_archive and has_selection)
        self.act_test.setEnabled(has_archive)
        self.act_refresh.setEnabled(has_archive)
        self.act_add.setEnabled(can_add)
        self.act_add_folder.setEnabled(can_add)
        self.act_delete.setEnabled(can_delete and has_selection)
        self.act_extract_here.setEnabled(has_archive)
        self.act_info.setEnabled(has_archive)
        self.act_convert.setEnabled(has_archive)
        self.act_comment.setEnabled(has_archive and getattr(self.handler, "can_comment", False))
        self._update_status_line()

    def _update_status_line(self) -> None:
        items = self._selected_name_items()
        if items:
            files = self._selected_paths(expand_dirs=True)
            size = sum(self._by_path[p].size for p in files if p in self._by_path)
            self._status.showMessage(f"{len(files)} file(s) selected — {human_size(size)}")
            return
        files = [e for e in self._entries if not e.is_dir]
        folders = [e for e in self._entries if e.is_dir]
        total = sum(e.size for e in files)
        suffix = f"  ·  filtered by “{self._filter}”" if self._filter else ""
        self._status.showMessage(
            f"{len(files)} file(s), {len(folders)} folder(s) — "
            f"{human_size(total)} uncompressed{suffix}")

    # --------------------------------------------------------------- open
    def open_dialog(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Open archive", "", _ARCHIVE_FILTER)
        if path:
            self.open_path(path)

    def open_path(self, path: str) -> None:
        try:
            handler = open_archive(path)
        except ArchiveError as exc:
            QMessageBox.critical(self, "Cannot open", str(exc))
            return

        password: str | None = None
        try:
            if handler.needs_password():
                password = ask_password(self, "This archive's file list is encrypted.")
                if password is None:
                    return
        except ArchiveError:
            pass

        while True:
            try:
                entries = handler.entries(password=password)
                break
            except (PasswordRequired, WrongPassword) as exc:
                password = ask_password(self, str(exc))
                if password is None:
                    return
            except ArchiveError as exc:
                QMessageBox.critical(self, "Cannot open", str(exc))
                return

        self.handler = handler
        self._archive_path = path
        self._password = password
        self._search.blockSignals(True)
        self._search.clear()
        self._search.blockSignals(False)
        self._filter = ""
        self._set_entries(entries)
        self.setWindowTitle(f"{os.path.basename(path)} — {APP_NAME}")
        note = "  (read-only)" if not handler.can_add else ""
        self._breadcrumb.setText(f"📦  {path}    [{handler.format.label}]{note}")
        self._remember_recent(path)
        self._update_actions()

    def reload(self) -> None:
        if not self.handler:
            return
        try:
            self._set_entries(self.handler.entries(password=self._password))
        except ArchiveError as exc:
            QMessageBox.warning(self, "Refresh failed", str(exc))

    # -------------------------------------------------------- recent files
    def _remember_recent(self, path: str) -> None:
        path = os.path.abspath(path)
        self._recent = [p for p in self._recent if os.path.normcase(p) != os.path.normcase(path)]
        self._recent.insert(0, path)
        del self._recent[12:]
        self._settings.setValue("recent", self._recent)
        self._rebuild_recent_menu()

    def _rebuild_recent_menu(self) -> None:
        self._recent_menu.clear()
        existing = [p for p in self._recent if os.path.isfile(p)]
        if not existing:
            empty = self._recent_menu.addAction("(no recent archives)")
            empty.setEnabled(False)
            return
        for path in existing:
            act = self._recent_menu.addAction(os.path.basename(path))
            act.setToolTip(path)
            act.triggered.connect(lambda _checked=False, p=path: self.open_path(p))
        self._recent_menu.addSeparator()
        clear = self._recent_menu.addAction("Clear list")
        clear.triggered.connect(self._clear_recent)

    def _clear_recent(self) -> None:
        self._recent = []
        self._settings.setValue("recent", self._recent)
        self._rebuild_recent_menu()

    # ------------------------------------------------------------- create
    def create_dialog(self, initial_sources: list[str] | None = None) -> None:
        dlg = CreateArchiveDialog(
            self, initial_sources=initial_sources,
            default_format=self._prefs.default_format,
            default_level=self._prefs.default_level)
        self._active_create_dialog = dlg  # lets late "add" launches append here
        try:
            accepted = dlg.exec() == CreateArchiveDialog.Accepted
        finally:
            self._active_create_dialog = None
        if not accepted:
            return
        cfg = dlg.config()
        sources = collect_sources(cfg.sources)
        if not sources:
            QMessageBox.warning(self, "Nothing to compress", "No files were found in the selection.")
            return

        split = cfg.volume_size is not None
        final_dest = self._confirm_dest(cfg.dest, split)
        if final_dest is None:
            return
        cfg.dest = final_dest
        original_size = sum(os.path.getsize(fs) for fs, _arc in sources if os.path.isfile(fs))

        def factory(_password):
            return Task(
                create_archive, cfg.format, cfg.dest, sources,
                level=cfg.level, password=cfg.password, encrypt_names=cfg.encrypt_names,
                volume_size=cfg.volume_size)

        def on_success(_result):
            self._status.showMessage(f"Created {os.path.basename(cfg.dest)}", 6000)
            self._show_create_result(cfg.dest, split, original_size)
            # A split archive writes cfg.dest.0001 … rather than cfg.dest itself.
            target = cfg.dest
            if not os.path.exists(target) and os.path.exists(cfg.dest + ".0001"):
                target = cfg.dest + ".0001"
            if os.path.exists(target):
                self.open_path(target)

        self._run_task(f"Creating {os.path.basename(cfg.dest)}", factory, on_success)

    # ------------------------------------------ destination + result helpers
    @staticmethod
    def _split_dest_ext(path: str) -> tuple[str, str]:
        low = path.lower()
        for ext in (".tar.gz", ".tar.bz2", ".tar.xz", ".tgz", ".tbz2", ".txz",
                    ".tar", ".zip", ".zipx", ".7z"):
            if low.endswith(ext):
                return path[: -len(ext)], path[len(path) - len(ext):]
        base, ext = os.path.splitext(path)
        return base, ext

    def _existing_targets(self, dest: str, split: bool) -> list[str]:
        if split:
            return sorted(glob.glob(glob.escape(dest) + ".[0-9]*"))
        return [dest] if os.path.exists(dest) else []

    def _archive_on_disk_size(self, dest: str, split: bool) -> int:
        targets = self._existing_targets(dest, split)
        return sum(os.path.getsize(f) for f in targets if os.path.isfile(f))

    def _confirm_dest(self, dest: str, split: bool) -> str | None:
        """Resolve a name clash: overwrite, pick a new name, or cancel (None)."""
        if not self._existing_targets(dest, split):
            return dest
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle("File already exists")
        box.setText(f'"{os.path.basename(dest)}" already exists in that folder.')
        box.setInformativeText("Do you want to overwrite it, or save under a new name?")
        overwrite_btn = box.addButton("Overwrite", QMessageBox.DestructiveRole)
        rename_btn = box.addButton("Save As New", QMessageBox.AcceptRole)
        box.addButton("Cancel", QMessageBox.RejectRole)
        box.setDefaultButton(rename_btn)
        box.exec()
        clicked = box.clickedButton()
        if clicked is overwrite_btn:
            for f in self._existing_targets(dest, split):
                try:
                    os.remove(f)
                except OSError:
                    pass
            return dest
        if clicked is rename_btn:
            base, ext = self._split_dest_ext(dest)
            n = 2
            while self._existing_targets(f"{base} ({n}){ext}", split):
                n += 1
            return f"{base} ({n}){ext}"
        return None  # cancelled

    def _show_create_result(self, dest: str, split: bool, original_size: int) -> None:
        archive_size = self._archive_on_disk_size(dest, split)
        saved = (1 - archive_size / original_size) if original_size else 0.0
        lines = (
            f"Original size:   {human_size(original_size)}\n"
            f"Archive size:    {human_size(archive_size)}\n"
            f"Space saved:     {round(saved * 100)}%"
        )
        note = ""
        if original_size > 100_000 and saved < 0.05:
            note = ("\n\nThese files are already compressed (images, video, PDFs, MP3s, "
                    "or other archives), so they can't be made much smaller — this is "
                    "normal and happens in WinRAR and 7-Zip too. Plain text, documents, "
                    "code and databases compress a lot.")
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Information)
        box.setWindowTitle("Archive created")
        box.setText(os.path.basename(dest))
        box.setInformativeText(lines + note)
        box.exec()

    # ------------------------------------------- single-instance dispatch
    def _raise_to_front(self) -> None:
        self.setWindowState((self.windowState() & ~Qt.WindowMinimized) | Qt.WindowActive)
        self.show()
        self.raise_()
        self.activateWindow()

    def dispatch_action(self, action) -> None:
        """Run a startup/forwarded action (kind, data)."""
        self._raise_to_front()
        kind, data = action
        if kind == "add":
            self.queue_add(list(data or []))
        elif kind == "open" and data:
            self.open_path(data)
        elif kind == "extract_here" and data:
            self.open_path(data)
            QTimer.singleShot(0, self.extract_here)
        elif kind == "extract_to" and data:
            self.open_path(data)
            QTimer.singleShot(0, self.extract_to_subfolder)
        # kind == "raise": just bring the window forward (already done above)

    def handle_forwarded(self, payload: dict) -> None:
        """Handle an action forwarded from a secondary instance."""
        kind = payload.get("action")
        if kind == "add":
            self.dispatch_action(("add", payload.get("paths", [])))
        elif kind in ("open", "extract_here", "extract_to"):
            self.dispatch_action((kind, payload.get("path")))
        else:
            self._raise_to_front()

    def queue_add(self, paths: list[str]) -> None:
        """Accumulate files to add, coalescing bursts into a single dialog.

        If a Create dialog is already open, the files are appended to it so
        even staggered launches (slow cold-start) still land in one dialog.
        """
        clean = [p for p in paths if p]
        if self._active_create_dialog is not None:
            self._active_create_dialog.add_sources(clean)
            return
        for p in clean:
            if p not in self._pending_add:
                self._pending_add.append(p)
        self._raise_to_front()
        self._add_timer.start()  # (re)start the debounce window

    def _flush_pending_add(self) -> None:
        paths = self._pending_add
        self._pending_add = []
        if paths:
            self.create_dialog(initial_sources=paths)

    # ------------------------------------------------------------ convert
    def convert_archive(self) -> None:
        if not self.handler or not self._archive_path:
            return
        dlg = ConvertArchiveDialog(self, self._archive_path, self.handler.format)
        if dlg.exec() != ConvertArchiveDialog.Accepted:
            return
        cfg = dlg.config()
        final_dest = self._confirm_dest(cfg.dest, split=False)
        if final_dest is None:
            return
        cfg.dest = final_dest

        def factory(password):
            # `password` is the SOURCE password (prompted/retried); the dialog's
            # own password is applied to the new archive.
            return Task(
                repackage, self._archive_path, cfg.format, cfg.dest,
                level=cfg.level, dest_password=cfg.password,
                encrypt_names=cfg.encrypt_names, src_password=password)

        def on_success(_result):
            self._status.showMessage(f"Converted to {os.path.basename(cfg.dest)}", 6000)
            if cfg.open_after and os.path.exists(cfg.dest):
                self.open_path(cfg.dest)

        self._run_task(f"Converting to {cfg.format.label}", factory, on_success,
                       password=self._password)

    # ------------------------------------------------------------ extract
    def _suggested_extract_dest(self) -> str:
        if not self._archive_path:
            return os.path.expanduser("~")
        base = os.path.dirname(self._archive_path)
        stem = os.path.basename(self._archive_path)
        for ext in (".tar.gz", ".tar.bz2", ".tar.xz", ".tar", ".zip", ".7z", ".rar"):
            if stem.lower().endswith(ext):
                stem = stem[: -len(ext)]
                break
        else:
            stem = os.path.splitext(stem)[0]
        return os.path.join(base, stem)

    def extract_all(self) -> None:
        if not self.handler:
            return
        dlg = ExtractDialog(self, self._suggested_extract_dest(), selected_count=0,
                            open_after_default=self._prefs.get("open_after_extract"))
        if dlg.exec() != ExtractDialog.Accepted:
            return
        cfg = dlg.config()

        def factory(password):
            return Task(self.handler.extract_all, cfg.dest, password=password)

        self._run_extract("Extracting all files", factory, cfg)

    def extract_here(self) -> None:
        if not self.handler or not self._archive_path:
            return
        dest = os.path.dirname(self._archive_path) or os.getcwd()
        cfg = ExtractConfig(dest=dest, open_after=False)

        def factory(password):
            return Task(self.handler.extract_all, dest, password=password)

        self._run_extract("Extracting here", factory, cfg)

    def extract_to_subfolder(self) -> None:
        """Extract everything into a subfolder named after the archive."""
        if not self.handler or not self._archive_path:
            return
        dest = self._suggested_extract_dest()
        cfg = ExtractConfig(dest=dest, open_after=self._prefs.get("open_after_extract"))

        def factory(password):
            return Task(self.handler.extract_all, dest, password=password)

        self._run_extract(f"Extracting to {os.path.basename(dest)}", factory, cfg)

    def extract_selected(self) -> None:
        if not self.handler:
            return
        members = self._selected_paths(expand_dirs=True)
        if not members:
            QMessageBox.information(self, "Nothing selected", "Select files or folders to extract.")
            return
        dlg = ExtractDialog(self, self._suggested_extract_dest(), selected_count=len(members),
                            open_after_default=self._prefs.get("open_after_extract"))
        if dlg.exec() != ExtractDialog.Accepted:
            return
        cfg = dlg.config()

        def factory(password):
            return Task(self.handler.extract, members, cfg.dest, password=password)

        self._run_extract(f"Extracting {len(members)} item(s)", factory, cfg)

    def _run_extract(self, title, factory, cfg) -> None:
        def on_success(_result):
            self._status.showMessage(f"Extracted to {cfg.dest}", 6000)
            if cfg.open_after and os.path.isdir(cfg.dest):
                QDesktopServices.openUrl(QUrl.fromLocalFile(cfg.dest))

        self._run_task(title, factory, on_success, password=self._password)

    # --------------------------------------------------------------- add
    def add_files(self) -> None:
        if not self.handler:
            return
        paths, _ = QFileDialog.getOpenFileNames(self, "Add files to archive")
        if paths:
            self._add_paths(paths)

    def add_folder(self) -> None:
        if not self.handler:
            return
        path = QFileDialog.getExistingDirectory(self, "Add folder to archive")
        if path:
            self._add_paths([path])

    def _add_paths(self, paths: list[str]) -> None:
        sources = collect_sources(paths)
        if not sources:
            return

        def factory(password):
            return Task(self.handler.add, sources,
                        level=CompressionLevel.NORMAL, password=password)

        def on_success(_result):
            self._status.showMessage(f"Added {len(sources)} item(s)", 5000)
            self.reload()

        self._run_task("Adding files", factory, on_success, password=self._password)

    # ------------------------------------------------------------ delete
    def delete_selected(self) -> None:
        if not self.handler:
            return
        members = self._selected_paths(expand_dirs=False)
        if not members:
            return
        if self._prefs.get("confirm_delete"):
            answer = QMessageBox.question(
                self, "Delete from archive",
                f"Remove {len(members)} item(s) from the archive?\nThis rewrites the archive file.",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if answer != QMessageBox.Yes:
                return

        def factory(password):
            return Task(self.handler.delete, members, password=password)

        def on_success(_result):
            self._status.showMessage(f"Deleted {len(members)} item(s)", 5000)
            self.reload()

        self._run_task("Deleting files", factory, on_success, password=self._password)

    # -------------------------------------------------------------- test
    def test_archive(self) -> None:
        if not self.handler:
            return

        def factory(password):
            return Task(self.handler.test, password=password)

        def on_success(bad):
            if not bad:
                QMessageBox.information(self, "Test complete",
                                        "No errors found — the archive is intact.")
            else:
                listed = "\n".join(bad[:20])
                more = f"\n… and {len(bad) - 20} more" if len(bad) > 20 else ""
                QMessageBox.warning(self, "Test found problems",
                                    f"{len(bad)} member(s) failed the integrity check:\n\n{listed}{more}")

        self._run_task("Testing archive", factory, on_success, password=self._password)

    # ---------------------------------------------------- task plumbing
    def _run_task(self, title: str, factory, on_success=None, password: str | None = None) -> None:
        """Run *factory(password)* as a background Task with a progress dialog.

        On a password error the user is prompted and the task is retried with
        the new password; the confirmed password is remembered for later ops.
        """
        if self._active_task is not None:
            QMessageBox.information(self, "Busy", "Another operation is still running.")
            return

        task = factory(password)
        self._active_task = task
        dlg = ProgressDialog(title, self)
        task.progress.connect(dlg.on_progress)
        task.message.connect(dlg.on_message)
        dlg.cancel_requested.connect(task.cancel)

        def finish():
            self._active_task = None

        def on_ok(result):
            finish()
            dlg.accept()
            if on_success:
                on_success(result)

        def on_fail(exc, tb):
            finish()
            dlg.reject()
            if isinstance(exc, OperationCancelled):
                self._status.showMessage("Operation cancelled.", 4000)
                return
            if isinstance(exc, (PasswordRequired, WrongPassword)):
                pw = ask_password(self, str(exc) or "This archive is encrypted.")
                if pw is None:
                    return
                self._password = pw
                self._run_task(title, factory, on_success, password=pw)
                return
            if isinstance(exc, ToolMissing):
                QMessageBox.warning(self, "Extra tool needed", str(exc))
                return
            self._show_error(title, exc, tb)

        task.succeeded.connect(on_ok)
        task.failed.connect(on_fail)
        task.start()
        dlg.exec()

    def _show_error(self, title: str, exc: Exception, tb: str) -> None:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Critical)
        box.setWindowTitle(title)
        box.setText(f"{type(exc).__name__}: {exc}")
        if tb:
            box.setDetailedText(tb)
        box.exec()

    # ---------------------------------------------------------- settings
    def open_settings(self) -> None:
        dlg = SettingsDialog(self, self._prefs)
        if dlg.exec() != SettingsDialog.Accepted:
            return
        # Apply theme change (writes prefs + restyles via the toggle handler).
        dark = bool(self._prefs.get("dark"))
        if dark != self.act_dark.isChecked():
            self.act_dark.setChecked(dark)
        apply_runtime_prefs(self._prefs)

    # ------------------------------------------------------------- theme
    def _set_dark(self, dark: bool) -> None:
        self._dark = dark
        app = QApplication.instance()
        if app is not None:
            from .style import apply_theme
            apply_theme(app, dark)
        self._settings.setValue("dark", dark)

    # ------------------------------------------------- open member in place
    def _on_double_click(self, index) -> None:
        if not self._prefs.get("doubleclick_open"):
            return
        item = self._model.itemFromIndex(index.siblingAtColumn(0))
        if item is None or bool(item.data(_ISDIR_ROLE)):
            return  # folders keep their default expand/collapse behaviour
        self._open_member(item.data(_PATH_ROLE))

    def _open_member(self, member: str) -> None:
        if not self.handler:
            return
        tmpdir = tempfile.mkdtemp(prefix="mc_open_")
        self._temp_dirs.append(tmpdir)

        def factory(password):
            return Task(self.handler.extract, [member], tmpdir, password=password)

        def on_success(_result):
            target = os.path.join(tmpdir, *member.split("/"))
            if os.path.exists(target):
                QDesktopServices.openUrl(QUrl.fromLocalFile(target))
            else:
                self._status.showMessage("Could not open item.", 4000)

        self._run_task(f"Opening {member.rsplit('/', 1)[-1]}", factory, on_success,
                       password=self._password)

    # --------------------------------------------------------- context menu
    def _build_context_menu(self) -> QMenu:
        items = self._selected_name_items()
        menu = QMenu(self)
        if len(items) == 1 and not bool(items[0].data(_ISDIR_ROLE)):
            open_act = menu.addAction("Open")
            open_act.triggered.connect(lambda: self._open_member(items[0].data(_PATH_ROLE)))
            menu.addSeparator()
        if items:
            menu.addAction(self.act_extract_sel)
        menu.addAction(self.act_extract)
        menu.addAction(self.act_extract_here)
        if getattr(self.handler, "can_delete", False) and items:
            menu.addSeparator()
            menu.addAction(self.act_delete)
        menu.addSeparator()
        menu.addAction(self.act_convert)
        menu.addAction(self.act_test)
        menu.addAction(self.act_info)
        if getattr(self.handler, "can_comment", False):
            menu.addAction(self.act_comment)
        return menu

    def _show_context_menu(self, pos) -> None:
        if not self.handler:
            return
        menu = self._build_context_menu()
        menu.exec(self._tree.viewport().mapToGlobal(pos))

    # ----------------------------------------------------- drag out to shell
    def _selection_export_plan(self) -> tuple[list[tuple[str, bool]], list[str]]:
        """Return (top-level items, file members) for the current selection.

        Top-level items are what the user actually dragged (files or folder
        roots); nested selections under a selected folder are dropped. File
        members are every file to extract so the structure comes out intact.
        """
        items = self._selected_name_items()
        if not items:
            return [], []
        sel = [(it.data(_PATH_ROLE).rstrip("/"), bool(it.data(_ISDIR_ROLE))) for it in items]
        dirs = [p for p, is_dir in sel if is_dir]

        def under_selected_dir(path: str) -> bool:
            return any(path != d and path.startswith(d + "/") for d in dirs)

        top = [(p, d) for (p, d) in sel if not under_selected_dir(p)]
        members: list[str] = []
        seen: set[str] = set()
        for path, is_dir in top:
            if is_dir:
                for e in self._entries:
                    if not e.is_dir and e.path.startswith(path + "/") and e.path not in seen:
                        seen.add(e.path)
                        members.append(e.path)
            elif path not in seen:
                seen.add(path)
                members.append(path)
        return top, members

    def _export_selection(self) -> list[str] | None:
        """Drag callback: extract the selection to a temp folder, return paths."""
        if not self.handler:
            return None
        top, members = self._selection_export_plan()
        if not members:
            return None
        tmp = tempfile.mkdtemp(prefix="mc_drag_")
        self._temp_dirs.append(tmp)

        pw = self._password
        while True:
            QApplication.setOverrideCursor(Qt.WaitCursor)
            err = None
            try:
                self.handler.extract(members, tmp, password=pw)
            except (PasswordRequired, WrongPassword) as exc:
                err = ("password", exc)
            except ToolMissing as exc:
                err = ("tool", exc)
            except Exception as exc:  # noqa: BLE001
                err = ("other", exc)
            finally:
                QApplication.restoreOverrideCursor()
            if err is None:
                break
            kind, exc = err
            if kind == "password":
                pw = ask_password(self, str(exc))
                if pw is None:
                    return None
                self._password = pw
                continue
            if kind == "tool":
                QMessageBox.warning(self, "Extra tool needed", str(exc))
            else:
                self._show_error("Drag out", exc, "")
            return None

        paths = []
        for path, _is_dir in top:
            local = os.path.join(tmp, *path.split("/"))
            if os.path.exists(local):
                paths.append(local)
        return paths or None

    def _archive_info_rows(self) -> list[tuple[str, str]]:
        files = [e for e in self._entries if not e.is_dir]
        folders = [e for e in self._entries if e.is_dir]
        total = sum(e.size for e in files)
        packed_vals = [e.compressed_size for e in files if e.compressed_size is not None]
        packed = sum(packed_vals) if packed_vals else None
        ratio = (1 - packed / total) if (packed is not None and total) else None
        encrypted = any(e.encrypted for e in files) or bool(getattr(self.handler, "needs_password", lambda: False)())
        try:
            on_disk = os.path.getsize(self._archive_path) if self._archive_path else 0
        except OSError:
            on_disk = 0
        return [
            ("Archive", os.path.basename(self._archive_path or "")),
            ("Location", os.path.dirname(self._archive_path or "")),
            ("Format", self.handler.format.label),
            ("Files", str(len(files))),
            ("Folders", str(len(folders))),
            ("Uncompressed size", human_size(total)),
            ("Packed size", human_size(packed) if packed is not None else "—"),
            ("Archive on disk", human_size(on_disk)),
            ("Overall ratio", human_ratio(ratio) if ratio is not None else "—"),
            ("Encrypted", "Yes" if encrypted else "No"),
        ]

    def show_archive_info(self) -> None:
        if not self.handler:
            return
        rows = self._archive_info_rows()
        html = "<table cellspacing='7'>" + "".join(
            f"<tr><td style='color:#8a93a0; padding-right:14px'>{k}</td>"
            f"<td><b>{v}</b></td></tr>" for k, v in rows
        ) + "</table>"
        box = QMessageBox(self)
        box.setWindowTitle("Archive information")
        box.setTextFormat(Qt.RichText)
        box.setIcon(QMessageBox.Information)
        box.setText(html)
        box.exec()

    def show_comment(self) -> None:
        if not self.handler or not getattr(self.handler, "can_comment", False):
            return
        try:
            comment = self.handler.read_comment()
        except Exception:  # noqa: BLE001 — treat unreadable comment as empty
            comment = ""
        new = show_comment_dialog(self, comment, self.handler.can_edit_comment)
        if new is not None and self.handler.can_edit_comment:
            try:
                self.handler.write_comment(new)
                self._status.showMessage("Comment saved", 5000)
            except Exception as exc:  # noqa: BLE001
                self._show_error("Save comment", exc, "")

    def show_about(self) -> None:
        box = QMessageBox(self)
        box.setWindowTitle(f"About {APP_NAME}")
        box.setTextFormat(Qt.RichText)
        box.setText(
            f"<h3>{APP_NAME} {__version__}</h3>"
            "<p>A desktop archive manager — create and open ZIP, 7z and TAR "
            "archives, and extract RAR.</p>"
            "<p>AES encryption, adjustable compression, add/delete inside "
            "archives, convert, split volumes, drag-and-drop, and Explorer "
            "integration.</p>"
            "<p style='color:#6a7280'>Built with Python and PySide6.</p>")
        pixmap = QPixmap(asset_path("icon.png"))
        if not pixmap.isNull():
            box.setIconPixmap(pixmap.scaled(72, 72, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        box.exec()

    # -------------------------------------------------------- drag & drop
    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dragMoveEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        paths = [u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()]
        paths = [p for p in paths if p]
        if not paths:
            return
        event.acceptProposedAction()

        # A single archive dropped with nothing open → open it.
        if self.handler is None and len(paths) == 1 and os.path.isfile(paths[0]) \
                and detect_format(paths[0]) is not None:
            self.open_path(paths[0])
            return
        # Something is open and supports adding → add the dropped items.
        if self.handler is not None and getattr(self.handler, "can_add", False):
            self._add_paths(paths)
            return
        # Otherwise start a new archive seeded with the dropped items.
        self.create_dialog(initial_sources=paths)

    def closeEvent(self, event) -> None:
        if self._active_task is not None:
            self._active_task.cancel()
            self._active_task.wait(3000)
        if self._prefs.get("remember_geometry"):
            self._settings.setValue("geometry", self.saveGeometry())
        self._settings.setValue("dark", self._dark)
        import shutil
        for d in self._temp_dirs:
            shutil.rmtree(d, ignore_errors=True)
        event.accept()
