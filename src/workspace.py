# workspace.py
#
# Copyright 2026 Hobbies
# SPDX-License-Identifier: GPL-3.0-or-later

import os
from gi.repository import GObject, Gtk, Gio, GLib
from .preferences import PreferencesManager

# Directories to always skip when walking the tree
_SKIP_DIRS = {
    ".git", "__pycache__", "build", "subprojects",
    ".textay_vc", ".flatpak-builder", "node_modules",
}

# Extensions considered plain text / source files
_TEXT_EXTENSIONS = {
    '.txt', '.py', '.json', '.md', '.js', '.ts', '.css', '.scss', '.html',
    '.xml', '.c', '.h', '.cpp', '.hpp', '.sh', '.bash', '.yml', '.yaml',
    '.ini', '.conf', '.csv', '.tsv', '.rst', '.meson', '.build', '.vala',
    '.rb', '.go', '.rs', '.java', '.kt', '.swift', '.sql', '.r', '.lua',
    '.tex', '.diff', '.patch', '.toml', '.env', '.gitignore', '.gitattributes',
    '.editorconfig', '.clang-format', '.in',
}


def is_text_file(filename):
    _, ext = os.path.splitext(filename.lower())
    return ext in _TEXT_EXTENSIONS or not ext


def _icon_for_file(name):
    """Returns a symbolic icon name for the given filename."""
    _, ext = os.path.splitext(name.lower())
    return {
        '.py':    'text-x-python-symbolic',
        '.js':    'text-x-javascript-symbolic',
        '.ts':    'text-x-typescript-symbolic',
        '.json':  'text-x-json-symbolic',
        '.xml':   'text-xml-symbolic',
        '.html':  'text-html-symbolic',
        '.css':   'text-x-css-symbolic',
        '.scss':  'text-x-sass-symbolic',
        '.md':    'x-office-document-symbolic',
        '.yml':   'text-x-yaml-symbolic',
        '.yaml':  'text-x-yaml-symbolic',
        '.toml':  'text-x-toml-symbolic',
        '.sh':    'text-x-script-symbolic',
        '.bash':  'text-x-script-symbolic',
        '.c':     'text-x-csrc-symbolic',
        '.h':     'text-x-chdr-symbolic',
        '.cpp':   'text-x-c++src-symbolic',
        '.hpp':   'text-x-c++hdr-symbolic',
        '.rs':    'text-x-rust-symbolic',
        '.go':    'text-x-go-symbolic',
        '.vala':  'text-x-vala-symbolic',
        '.diff':  'text-x-patch-symbolic',
        '.patch': 'text-x-patch-symbolic',
        '.ui':    'text-xml-symbolic',
        '.in':    'text-x-generic-symbolic',
    }.get(ext, 'text-x-generic-symbolic')


# ── GObject model item ─────────────────────────────────────────────────────

class FileItem(GObject.Object):
    """A single file or directory entry used as a GtkTreeListModel item."""
    __gtype_name__ = 'TextayFileItem'

    def __init__(self, path, name, is_dir):
        super().__init__()
        self.path      = path
        self.name      = name
        self.is_dir    = is_dir
        self.icon_name = 'folder-symbolic' if is_dir else _icon_for_file(name)


# ── WorkspaceManager ──────────────────────────────────────────────────────

class WorkspaceManager:
    """Manages workspace path, the directory model, and per-file Gio monitors."""

    def __init__(self):
        self.workspace_path = None
        self.pm = PreferencesManager()
        self.active_monitors = {}   # filepath -> Gio.FileMonitor

    def set_workspace(self, path):
        self.workspace_path = path
        self.clear_monitors()

    def clear_monitors(self):
        for monitor in self.active_monitors.values():
            monitor.cancel()
        self.active_monitors.clear()

    # ──────────────────────────────────────────────────────────────────────
    # GTK4-native tree model (GtkListView + GtkTreeListModel)
    # ──────────────────────────────────────────────────────────────────────

    def create_directory_model(self, dir_path, search_query=''):
        """Returns a Gio.ListStore[FileItem] for the given directory.

        Used as the create_func for GtkTreeListModel so each directory
        level gets its own model on demand.
        """
        show_all = self.pm.get('show_all_files', False)
        store = Gio.ListStore(item_type=FileItem)
        try:
            entries = sorted(
                os.scandir(dir_path),
                key=lambda e: (not e.is_dir(), e.name.lower())
            )
        except PermissionError:
            return store

        for entry in entries:
            name = entry.name
            if name in _SKIP_DIRS:
                continue
            if name.startswith('.') and entry.is_dir():
                continue

            if entry.is_dir(follow_symlinks=False):
                store.append(FileItem(entry.path, name, True))
            else:
                if show_all or is_text_file(name):
                    if not search_query or search_query in name.lower():
                        store.append(FileItem(entry.path, name, False))

        return store

    def build_tree_model(self, search_query=''):
        """Builds a GtkTreeListModel rooted at the current workspace path.
        If search_query is active, returns a flat list model of files only (no folders).
        """
        if not self.workspace_path:
            return None

        if search_query:
            store = Gio.ListStore(item_type=FileItem)
            show_all = self.pm.get('show_all_files', False)
            self._collect_matching_files(self.workspace_path, search_query, show_all, store)
            return Gtk.TreeListModel.new(
                store,
                False,
                False,
                lambda item: None,
            )

        root_store = self.create_directory_model(self.workspace_path, search_query)

        return Gtk.TreeListModel.new(
            root_store,
            False,   # passthrough=False: items are GtkTreeListRow wrappers
            True,    # autoexpand=True: automatically pre-open all folders
            lambda item: (
                self.create_directory_model(item.path, search_query)
                if item.is_dir else None
            ),
        )

    def _collect_matching_files(self, dir_path, search_query, show_all, store):
        try:
            entries = sorted(
                os.scandir(dir_path),
                key=lambda e: (not e.is_dir(), e.name.lower())
            )
        except PermissionError:
            return

        for entry in entries:
            name = entry.name
            if name in _SKIP_DIRS:
                continue
            if name.startswith('.') and entry.is_dir():
                continue

            if entry.is_dir(follow_symlinks=False):
                self._collect_matching_files(entry.path, search_query, show_all, store)
            else:
                if show_all or is_text_file(name):
                    if search_query in name.lower():
                        store.append(FileItem(entry.path, name, False))

    # ──────────────────────────────────────────────────────────────────────
    # Legacy TreeStore (kept for compatibility; not used by the new ListView)
    # ──────────────────────────────────────────────────────────────────────

    def get_files_tree_store(self, search_query=''):
        store = Gtk.TreeStore(str, str, bool, str)
        if not self.workspace_path or not os.path.exists(self.workspace_path):
            return store
        self._fill_store(store, None, self.workspace_path, search_query.lower())
        return store

    def _fill_store(self, store, parent_iter, dir_path, search_query=''):
        show_all = self.pm.get('show_all_files', False)
        try:
            entries = sorted(os.scandir(dir_path), key=lambda e: (not e.is_dir(), e.name.lower()))
        except PermissionError:
            return
        for entry in entries:
            name = entry.name
            if name in _SKIP_DIRS:
                continue
            if name.startswith('.') and entry.is_dir():
                continue
            if entry.is_dir(follow_symlinks=False):
                it = store.append(parent_iter, [name, entry.path, True, 'folder-symbolic'])
                self._fill_store(store, it, entry.path, search_query)
                if search_query and not store.iter_has_child(it):
                    store.remove(it)
            else:
                if show_all or is_text_file(name):
                    if search_query and search_query not in name.lower():
                        continue
                    store.append(parent_iter, [name, entry.path, False, _icon_for_file(name)])

    # ──────────────────────────────────────────────────────────────────────
    # File monitoring
    # ──────────────────────────────────────────────────────────────────────

    def monitor_file(self, filepath, callback):
        if not filepath or not os.path.exists(filepath):
            return None
        if filepath in self.active_monitors:
            self.active_monitors[filepath].cancel()
        gfile = Gio.File.new_for_path(filepath)
        try:
            monitor = gfile.monitor_file(Gio.FileMonitorFlags.NONE, None)
            monitor.connect('changed', self._on_file_changed, filepath, callback)
            self.active_monitors[filepath] = monitor
            return monitor
        except Exception as e:
            print(f'File monitor error: {e}')
            return None

    def unmonitor_file(self, filepath):
        if filepath in self.active_monitors:
            self.active_monitors[filepath].cancel()
            del self.active_monitors[filepath]

    def _on_file_changed(self, monitor, file, other_file, event_type, filepath, callback):
        if event_type == Gio.FileMonitorEvent.CHANGES_DONE_HINT:
            GLib.idle_add(callback, filepath)

    # ──────────────────────────────────────────────────────────────────────
    # File I/O helpers
    # ──────────────────────────────────────────────────────────────────────

    def read_file(self, filepath):
        try:
            with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
                return f.read()
        except Exception as e:
            return f'Error reading file: {e}'

    def write_file(self, filepath, content):
        try:
            os.makedirs(os.path.dirname(filepath), exist_ok=True)
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(content)
            return True, None
        except Exception as e:
            return False, str(e)
