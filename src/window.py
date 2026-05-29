# window.py
#
# Copyright 2026 Hobbies
# SPDX-License-Identifier: GPL-3.0-or-later

import os
from gi.repository import Adw, Gtk, GLib, Pango

# Ensure Adw types used in XML are registered before template parsing
_ = Adw.WindowTitle
_ = Adw.OverlaySplitView
_ = Adw.ToastOverlay

from .preferences import PreferencesManager, PreferencesWindow
from .workspace import WorkspaceManager
from .editor import TextayEditor


@Gtk.Template(resource_path='/com/x1da49/textay/window.ui')
class TextayWindow(Adw.ApplicationWindow):
    __gtype_name__ = 'TextayWindow'

    # Start screen
    toast_overlay     = Gtk.Template.Child()
    main_stack        = Gtk.Template.Child()
    start_status_page = Gtk.Template.Child()
    open_folder_btn   = Gtk.Template.Child()
    recent_box        = Gtk.Template.Child()
    recent_listbox    = Gtk.Template.Child()

    # Workspace header
    workspace_title       = Gtk.Template.Child()
    close_workspace_btn   = Gtk.Template.Child()
    refresh_workspace_btn = Gtk.Template.Child()
    sidebar_toggle_btn    = Gtk.Template.Child()
    save_status_box       = Gtk.Template.Child()
    save_spinner          = Gtk.Template.Child()
    save_status_label     = Gtk.Template.Child()

    # Sidebar
    sidebar_toolbar_view = Gtk.Template.Child()
    sidebar_title        = Gtk.Template.Child()
    split_view           = Gtk.Template.Child()

    # Main view
    main_view_stack = Gtk.Template.Child()

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.init_template()

        self.pm = PreferencesManager()
        self.wm = WorkspaceManager()
        self.current_filepath = None

        # Editor widget
        self.editor = TextayEditor(self.wm, self._on_save_status_changed)

        # Main view pages
        welcome = Adw.StatusPage()
        welcome.set_title("No File Open")
        welcome.set_description("Select a file from the sidebar to start editing.")
        welcome.set_icon_name("document-edit-symbolic")
        self.main_view_stack.add_named(welcome, "welcome")
        self.main_view_stack.add_named(self.editor, "editor")
        self.main_view_stack.set_visible_child_name("welcome")

        # Build the file explorer sidebar
        self._build_explorer_sidebar()

        # Wire up signals
        self.open_folder_btn.connect("clicked", self._on_open_folder_clicked)
        self.close_workspace_btn.connect("clicked", self._on_close_workspace_clicked)
        self.refresh_workspace_btn.connect("clicked", self._on_refresh_workspace_clicked)
        self.sidebar_toggle_btn.connect("toggled", self._on_sidebar_toggle_toggled)

        self.main_stack.set_visible_child_name("start_screen")
        self._update_recent_folders_ui()

        # Restore the last opened workspace or the most recent folder from history
        last = self.pm.get("last_workspace")
        if last and os.path.isdir(last):
            self._load_workspace(last)
        else:
            recent = self.pm.get("recent_workspaces", [])
            existing = [r for r in recent if os.path.isdir(r)]
            if existing:
                self._load_workspace(existing[0])

    # ──────────────────────────────────────────────────────────────────────
    # File explorer sidebar
    # ──────────────────────────────────────────────────────────────────────

    def _build_explorer_sidebar(self):
        """Modern GTK4 file explorer using GtkListView + GtkTreeListModel.
        Matches GNOME Builder's file panel: tree fills space, search pinned at bottom.
        """
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        outer.set_hexpand(True)
        outer.set_vexpand(True)

        # ── Scrolled list (fills all space above search bar) ───────────────────
        scroll = Gtk.ScrolledWindow()
        scroll.set_hexpand(True)
        scroll.set_vexpand(True)
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        # Item factory — setup creates the widget tree, bind populates it
        factory = Gtk.SignalListItemFactory()
        factory.connect('setup', self._on_item_setup)
        factory.connect('bind',  self._on_item_bind)

        self._file_selection = Gtk.SingleSelection()
        self._file_selection.set_autoselect(False)
        self._file_selection.set_can_unselect(True)

        self._list_view = Gtk.ListView.new(self._file_selection, factory)
        self._list_view.set_single_click_activate(True)
        self._list_view.add_css_class('navigation-sidebar')
        self._list_view.connect('activate', self._on_list_item_activated)

        scroll.set_child(self._list_view)
        outer.append(scroll)

        # ── Separator + search pinned at bottom (like GNOME Builder) ─────────
        outer.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

        self._search_entry = Gtk.SearchEntry()
        self._search_entry.set_placeholder_text('Filter…')
        self._search_entry.set_margin_start(8)
        self._search_entry.set_margin_end(8)
        self._search_entry.set_margin_top(6)
        self._search_entry.set_margin_bottom(6)
        self._search_entry.connect('search-changed', self._on_search_changed)
        outer.append(self._search_entry)

        self.sidebar_toolbar_view.set_content(outer)

    # ── GtkSignalListItemFactory callbacks ─────────────────────────────────────

    def _on_item_setup(self, factory, list_item):
        """Create the widget structure for a file/folder row."""
        # GtkTreeExpander handles indentation and the expand arrow automatically
        expander = Gtk.TreeExpander()
        expander.set_indent_for_icon(True)

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        box.set_margin_start(2)
        box.set_margin_end(8)
        box.set_margin_top(3)
        box.set_margin_bottom(3)

        icon = Gtk.Image()
        icon.set_icon_size(Gtk.IconSize.NORMAL)

        label = Gtk.Label()
        label.set_halign(Gtk.Align.START)
        label.set_hexpand(True)
        label.set_ellipsize(Pango.EllipsizeMode.END)
        label.set_xalign(0.0)

        box.append(icon)
        box.append(label)
        expander.set_child(box)
        list_item.set_child(expander)

    def _on_item_bind(self, factory, list_item):
        """Populate a row with data from its FileItem."""
        expander = list_item.get_child()
        tree_row = list_item.get_item()          # GtkTreeListRow
        expander.set_list_row(tree_row)           # connects expander → row

        item = tree_row.get_item()               # our FileItem
        box  = expander.get_child()
        icon  = box.get_first_child()
        label = icon.get_next_sibling()

        icon.set_from_icon_name(item.icon_name)
        label.set_label(item.name)

    # ──────────────────────────────────────────────────────────────────────
    # Sidebar toggle
    # ──────────────────────────────────────────────────────────────────────

    def _on_sidebar_toggle_toggled(self, btn):
        # AdwOverlaySplitView handles the slide animation natively
        self.split_view.set_show_sidebar(btn.get_active())

    # ──────────────────────────────────────────────────────────────────────
    # Folder opening
    # ──────────────────────────────────────────────────────────────────────

    def _on_open_folder_clicked(self, btn):
        if hasattr(Gtk, "FileDialog"):
            dialog = Gtk.FileDialog()
            dialog.set_title("Open Workspace Folder")
            dialog.select_folder(self, None, self._on_folder_dialog_cb)
        else:
            dialog = Gtk.FileChooserDialog(
                title="Open Workspace Folder",
                parent=self,
                action=Gtk.FileChooserAction.SELECT_FOLDER,
            )
            dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
            dialog.add_button("Open", Gtk.ResponseType.ACCEPT)
            dialog.connect("response", self._on_legacy_folder_chosen)
            dialog.show()

    def _on_folder_dialog_cb(self, dialog, result):
        try:
            folder = dialog.select_folder_finish(result)
            if folder:
                self._load_workspace(folder.get_path())
        except Exception as e:
            print(f"Folder select error: {e}")

    def _on_legacy_folder_chosen(self, dialog, response):
        if response == Gtk.ResponseType.ACCEPT:
            self._load_workspace(dialog.get_file().get_path())
        dialog.destroy()

    # ──────────────────────────────────────────────────────────────────────
    # Workspace lifecycle
    # ──────────────────────────────────────────────────────────────────────

    def _load_workspace(self, path):
        if not path or not os.path.exists(path):
            return
        self.wm.set_workspace(path)
        folder_name = os.path.basename(path)
        self.workspace_title.set_title(folder_name)
        self.workspace_title.set_subtitle("")
        self.sidebar_title.set_title(folder_name)
        self.main_stack.set_visible_child_name("workspace_screen")
        self.editor.unload_file()
        self.current_filepath = None
        self.main_view_stack.set_visible_child_name("welcome")
        self._refresh_tree()

        # Track in recent workspaces and save preferences
        self.pm.set("last_workspace", path)
        recent = self.pm.get("recent_workspaces", [])
        if path in recent:
            recent.remove(path)
        recent.insert(0, path)
        self.pm.set("recent_workspaces", recent[:10])
        self._update_recent_folders_ui()

    def _on_close_workspace_clicked(self, btn):
        self.editor.unload_file()
        self.current_filepath = None
        self.save_status_box.set_visible(False)
        self.save_spinner.stop()
        self.wm.clear_monitors()
        self.pm.set("last_workspace", None)
        self._update_recent_folders_ui()
        self.main_stack.set_visible_child_name("start_screen")

    def _update_recent_folders_ui(self):
        """Clears and rebuilds the beautiful Libadwaita ActionRow items for recently opened workspaces."""
        # Clear existing children
        while True:
            child = self.recent_listbox.get_first_child()
            if not child:
                break
            self.recent_listbox.remove(child)

        recent = self.pm.get("recent_workspaces", [])
        # filter only existing directories
        recent = [r for r in recent if os.path.isdir(r)]
        self.pm.set("recent_workspaces", recent)

        if not recent:
            self.recent_box.set_visible(False)
            return

        self.recent_box.set_visible(True)
        for path in recent:
            row = Adw.ActionRow()
            row.set_title(os.path.basename(path))
            row.set_subtitle(path)
            row.set_activatable(True)

            # Add an open icon to the right
            icon = Gtk.Image.new_from_icon_name("document-open-symbolic")
            row.add_suffix(icon)

            # Connect row activation to open that folder
            def on_row_activated(r, p=path):
                self._load_workspace(p)

            row.connect("activated", on_row_activated)
            self.recent_listbox.append(row)

    def _on_refresh_workspace_clicked(self, btn):
        self._refresh_tree()
        toast = Adw.Toast.new("Workspace refreshed")
        toast.set_timeout(2)
        self.toast_overlay.add_toast(toast)

    def _refresh_tree(self):
        if not self.wm.workspace_path:
            return
        query = self._search_entry.get_text().strip().lower() if hasattr(self, '_search_entry') else ''
        tree_model = self.wm.build_tree_model(search_query=query)
        self._file_selection.set_model(tree_model)
        if self.current_filepath and not os.path.exists(self.current_filepath):
            self.editor.unload_file()
            self.current_filepath = None
            self.main_view_stack.set_visible_child_name('welcome')
        elif self.current_filepath:
            self._select_file_in_sidebar(self.current_filepath)

    def _on_search_changed(self, entry):
        self._refresh_tree()

    # ──────────────────────────────────────────────────────────────────────
    # File open
    # ──────────────────────────────────────────────────────────────────────

    def _on_list_item_activated(self, list_view, position):
        """Single-click handler: open file or toggle folder."""
        self._file_selection.set_selected(position)
        tree_row = self._file_selection.get_item(position)
        if tree_row is None:
            return
        item = tree_row.get_item()
        if item.is_dir:
            tree_row.set_expanded(not tree_row.get_expanded())
        else:
            self._open_file(item.path)

    # Keep _on_file_row_activated as a no-op alias so nothing breaks
    def _on_file_row_activated(self, *_):
        pass

    def _select_file_in_sidebar(self, filepath):
        """Searches the tree model and selects the index representing filepath."""
        model = self._file_selection.get_model()
        if not model:
            return
        n_items = model.get_n_items()
        for i in range(n_items):
            row = model.get_item(i)
            if row:
                item = row.get_item()
                if item and not item.is_dir and item.path == filepath:
                    self._file_selection.set_selected(i)
                    break

    def _open_file(self, filepath):
        self.editor.unload_file()
        self.current_filepath = filepath
        self.editor.load_file(filepath)
        self.main_view_stack.set_visible_child_name("editor")
        self.workspace_title.set_title(os.path.basename(filepath))
        rel = (os.path.relpath(os.path.dirname(filepath), self.wm.workspace_path)
               if self.wm.workspace_path else "")
        self.workspace_title.set_subtitle(rel if rel != "." else "")

        # Highlight in sidebar
        self._select_file_in_sidebar(filepath)

    # ──────────────────────────────────────────────────────────────────────
    # Save status
    # ──────────────────────────────────────────────────────────────────────

    def _on_save_status_changed(self, filepath, status):
        if filepath != self.current_filepath:
            return

        self.save_status_label.set_text(status)

        if status == "Saving…":
            self.save_spinner.set_visible(True)
            self.save_spinner.start()
        else:
            self.save_spinner.stop()
            self.save_spinner.set_visible(False)

        # Show the box if we have a status, otherwise hide
        self.save_status_box.set_visible(bool(status))

    # ──────────────────────────────────────────────────────────────────────
    # Preferences
    # ──────────────────────────────────────────────────────────────────────

    def show_preferences(self):
        PreferencesWindow(self, self._on_preference_changed).present()

    def _on_preference_changed(self, key, value):
        if key == "show_all_files" and self.wm.workspace_path:
            self._refresh_tree()
