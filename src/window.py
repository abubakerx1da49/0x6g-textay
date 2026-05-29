# window.py
#
# Copyright 2026 Hobbies
# SPDX-License-Identifier: GPL-3.0-or-later

import os
from gi.repository import Adw, Gtk, GLib, Pango, Gdk, GObject

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
    sidebar_header       = Gtk.Template.Child()
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

        # F2 key listener for renaming
        key_controller = Gtk.EventControllerKey.new()
        key_controller.connect("key-pressed", self._on_list_view_key_pressed)
        self._list_view.add_controller(key_controller)

        # Add symbolic header buttons for fast file & folder creation
        new_file_btn = Gtk.Button.new_from_icon_name("document-new-symbolic")
        new_file_btn.set_tooltip_text("New File…")
        new_file_btn.set_has_frame(False)
        new_file_btn.connect("clicked", self._on_new_file_clicked)
        self.sidebar_header.pack_start(new_file_btn)

        new_folder_btn = Gtk.Button.new_from_icon_name("folder-new-symbolic")
        new_folder_btn.set_tooltip_text("New Folder…")
        new_folder_btn.set_has_frame(False)
        new_folder_btn.connect("clicked", self._on_new_folder_clicked)
        self.sidebar_header.pack_start(new_folder_btn)

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

        # Right-click gesture for context menu
        right_click = Gtk.GestureClick.new()
        right_click.set_button(3)  # Right click
        right_click.connect("pressed", self._on_item_right_clicked, list_item)
        expander.add_controller(right_click)

        # Drag and Drop source setup
        drag_source = Gtk.DragSource.new()
        drag_source.set_actions(Gdk.DragAction.MOVE)
        drag_source.connect("prepare", self._on_drag_prepare, list_item)
        expander.add_controller(drag_source)

        # Drag and Drop target setup
        drop_target = Gtk.DropTarget.new(GObject.TYPE_STRING, Gdk.DragAction.MOVE)
        drop_target.connect("drop", self._on_drop_item, list_item)
        expander.add_controller(drop_target)

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
        # Saved indicator is permanently removed from the top bar
        self.save_status_box.set_visible(False)

    # ──────────────────────────────────────────────────────────────────────
    # Preferences
    # ──────────────────────────────────────────────────────────────────────

    def show_preferences(self):
        PreferencesWindow(self, self._on_preference_changed).present()

    def _on_preference_changed(self, key, value):
        if key == "show_all_files" and self.wm.workspace_path:
            self._refresh_tree()

    # ──────────────────────────────────────────────────────────────────────
    # Sidebar item renaming
    # ──────────────────────────────────────────────────────────────────────

    def _on_item_right_clicked(self, gesture, n_press, x, y, list_item):
        tree_row = list_item.get_item()
        if not tree_row:
            return
        item = tree_row.get_item()
        if not item:
            return

        # Show a beautiful Popover context menu positioned to the right side
        popover = Gtk.Popover()
        popover.set_parent(list_item.get_child())
        popover.set_position(Gtk.PositionType.RIGHT)
        popover.set_has_arrow(True)
        
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        box.set_margin_start(4)
        box.set_margin_end(4)
        box.set_margin_top(4)
        box.set_margin_bottom(4)
        
        # Determine parent path for relative creations
        parent_path = item.path if item.is_dir else os.path.dirname(item.path)

        # ── Action: New File ──────────────────────────────────────────────────
        new_file_btn = Gtk.Button()
        new_file_btn.set_has_frame(False)
        new_file_btn.set_halign(Gtk.Align.FILL)
        new_file_btn.add_css_class("flat")
        
        new_file_content = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        new_file_content.set_margin_start(8)
        new_file_content.set_margin_end(12)
        new_file_content.set_margin_top(6)
        new_file_content.set_margin_bottom(6)
        
        new_file_icon = Gtk.Image.new_from_icon_name("document-new-symbolic")
        new_file_icon.set_icon_size(Gtk.IconSize.NORMAL)
        new_file_label = Gtk.Label(label="New File…")
        new_file_label.set_halign(Gtk.Align.START)
        
        new_file_content.append(new_file_icon)
        new_file_content.append(new_file_label)
        new_file_btn.set_child(new_file_content)
        
        def on_new_file_clicked(btn):
            popover.popdown()
            self._create_new_item_flow(is_dir=False, parent_path=parent_path)
            
        new_file_btn.connect("clicked", on_new_file_clicked)
        box.append(new_file_btn)

        # ── Action: New Folder ────────────────────────────────────────────────
        new_folder_btn = Gtk.Button()
        new_folder_btn.set_has_frame(False)
        new_folder_btn.set_halign(Gtk.Align.FILL)
        new_folder_btn.add_css_class("flat")
        
        new_folder_content = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        new_folder_content.set_margin_start(8)
        new_folder_content.set_margin_end(12)
        new_folder_content.set_margin_top(6)
        new_folder_content.set_margin_bottom(6)
        
        new_folder_icon = Gtk.Image.new_from_icon_name("folder-new-symbolic")
        new_folder_icon.set_icon_size(Gtk.IconSize.NORMAL)
        new_folder_label = Gtk.Label(label="New Folder…")
        new_folder_label.set_halign(Gtk.Align.START)
        
        new_folder_content.append(new_folder_icon)
        new_folder_content.append(new_folder_label)
        new_folder_btn.set_child(new_folder_content)
        
        def on_new_folder_clicked(btn):
            popover.popdown()
            self._create_new_item_flow(is_dir=True, parent_path=parent_path)
            
        new_folder_btn.connect("clicked", on_new_folder_clicked)
        box.append(new_folder_btn)

        # Separator line
        box.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

        # ── Action: Rename ────────────────────────────────────────────────────
        rename_btn = Gtk.Button()
        rename_btn.set_has_frame(False)
        rename_btn.set_halign(Gtk.Align.FILL)
        rename_btn.add_css_class("flat")
        
        btn_content = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        btn_content.set_margin_start(8)
        btn_content.set_margin_end(12)
        btn_content.set_margin_top(6)
        btn_content.set_margin_bottom(6)
        
        icon = Gtk.Image.new_from_icon_name("document-properties-symbolic")
        icon.set_icon_size(Gtk.IconSize.NORMAL)
        
        label = Gtk.Label(label="Rename…")
        label.set_halign(Gtk.Align.START)
        
        btn_content.append(icon)
        btn_content.append(label)
        rename_btn.set_child(btn_content)
        
        def on_rename_clicked(btn):
            popover.popdown()
            self._show_rename_dialog(item.path)
            
        rename_btn.connect("clicked", on_rename_clicked)
        box.append(rename_btn)

        # ── Action: Delete ────────────────────────────────────────────────────
        delete_btn = Gtk.Button()
        delete_btn.set_has_frame(False)
        delete_btn.set_halign(Gtk.Align.FILL)
        delete_btn.add_css_class("flat")
        delete_btn.add_css_class("destructive-action")
        
        del_content = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        del_content.set_margin_start(8)
        del_content.set_margin_end(12)
        del_content.set_margin_top(6)
        del_content.set_margin_bottom(6)
        
        del_icon = Gtk.Image.new_from_icon_name("user-trash-symbolic")
        del_icon.set_icon_size(Gtk.IconSize.NORMAL)
        
        del_label = Gtk.Label(label="Delete…")
        del_label.set_halign(Gtk.Align.START)
        
        del_content.append(del_icon)
        del_content.append(del_label)
        delete_btn.set_child(del_content)
        
        def on_delete_clicked(btn):
            popover.popdown()
            self._show_delete_dialog(item.path)
            
        delete_btn.connect("clicked", on_delete_clicked)
        box.append(delete_btn)
        
        popover.set_child(box)
        popover.popup()

    def _on_list_view_key_pressed(self, controller, keyval, keycode, state):
        if keyval == Gdk.KEY_F2:
            selected_idx = self._file_selection.get_selected()
            if selected_idx != Gtk.INVALID_LIST_POSITION:
                tree_row = self._file_selection.get_item(selected_idx)
                if tree_row:
                    item = tree_row.get_item()
                    if item:
                        self._show_rename_dialog(item.path)
                        return True
        elif keyval in (Gdk.KEY_Delete, Gdk.KEY_KP_Delete):
            selected_idx = self._file_selection.get_selected()
            if selected_idx != Gtk.INVALID_LIST_POSITION:
                tree_row = self._file_selection.get_item(selected_idx)
                if tree_row:
                    item = tree_row.get_item()
                    if item:
                        self._show_delete_dialog(item.path)
                        return True
        return False

    def _show_rename_dialog(self, filepath):
        # Create a beautiful transient dialog
        dialog = Adw.MessageDialog.new(self, "Rename Item", f"Enter a new name for {os.path.basename(filepath)}:")
        
        # Add an input text entry
        entry = Gtk.Entry()
        entry.set_text(os.path.basename(filepath))
        entry.set_hexpand(True)
        entry.set_margin_top(12)
        entry.set_margin_bottom(12)
        
        # In Adw.MessageDialog, we can set an extra child!
        dialog.set_extra_child(entry)
        
        # Add response buttons
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("rename", "Rename")
        dialog.set_response_appearance("rename", Adw.ResponseAppearance.SUGGESTED)
        
        # Select entry text by default for easy overwriting
        entry.select_region(0, -1)
        entry.grab_focus()
        
        def on_response(dlg, response):
            if response == "rename":
                new_name = entry.get_text().strip()
                if new_name and new_name != os.path.basename(filepath):
                    parent_dir = os.path.dirname(filepath)
                    new_path = os.path.join(parent_dir, new_name)
                    
                    try:
                        # Rename on disk
                        os.rename(filepath, new_path)
                        
                        # If the renamed file is the currently open file in editor, update the editor's path!
                        if self.current_filepath == filepath:
                            self.current_filepath = new_path
                            self.editor.filepath = new_path
                            if self.editor.on_save_status_changed:
                                self.editor.on_save_status_changed(new_path, "Saved")
                        
                        # Refresh tree model
                        self._refresh_tree()
                        
                        # Show success toast
                        toast = Adw.Toast.new(f"Renamed to {new_name}")
                        self.toast_overlay.add_toast(toast)
                    except Exception as e:
                        # Show error toast
                        toast = Adw.Toast.new(f"Rename failed: {e}")
                        self.toast_overlay.add_toast(toast)
            dialog.destroy()
            
        dialog.connect("response", on_response)
        dialog.present()

    def _on_new_file_clicked(self, btn):
        self._create_new_item_flow(is_dir=False)

    def _on_new_folder_clicked(self, btn):
        self._create_new_item_flow(is_dir=True)

    def _create_new_item_flow(self, is_dir=False, parent_path=None):
        if not self.wm.workspace_path:
            return

        if not parent_path:
            # Determine parent directory from current selection
            parent_dir = self.wm.workspace_path
            selected_idx = self._file_selection.get_selected()
            if selected_idx != Gtk.INVALID_LIST_POSITION:
                tree_row = self._file_selection.get_item(selected_idx)
                if tree_row:
                    item = tree_row.get_item()
                    if item:
                        parent_dir = item.path if item.is_dir else os.path.dirname(item.path)
        else:
            parent_dir = parent_path

        title = "New Folder" if is_dir else "New File"
        msg = f"Enter a name for the new {'folder' if is_dir else 'file'} in {os.path.basename(parent_dir) or 'Workspace'}:"

        dialog = Adw.MessageDialog.new(self, title, msg)
        entry = Gtk.Entry()
        entry.set_hexpand(True)
        entry.set_margin_top(12)
        entry.set_margin_bottom(12)
        dialog.set_extra_child(entry)

        dialog.add_response("cancel", "Cancel")
        dialog.add_response("create", "Create")
        dialog.set_response_appearance("create", Adw.ResponseAppearance.SUGGESTED)

        entry.grab_focus()

        def on_response(dlg, response):
            if response == "create":
                name = entry.get_text().strip()
                if name:
                    new_path = os.path.join(parent_dir, name)
                    try:
                        if is_dir:
                            os.makedirs(new_path, exist_ok=True)
                        else:
                            # Create an empty file
                            open(new_path, 'w').close()

                        self._refresh_tree()

                        if not is_dir:
                            self._open_file(new_path)

                        toast = Adw.Toast.new(f"Created {name}")
                        self.toast_overlay.add_toast(toast)
                    except Exception as e:
                        toast = Adw.Toast.new(f"Creation failed: {e}")
                        self.toast_overlay.add_toast(toast)
            dialog.destroy()

        dialog.connect("response", on_response)
        dialog.present()

    def _on_drag_prepare(self, source, x, y, list_item):
        tree_row = list_item.get_item()
        if not tree_row:
            return None
        item = tree_row.get_item()
        if not item:
            return None

        # Return a Gdk.ContentProvider with the item path string
        return Gdk.ContentProvider.new_for_value(item.path)

    def _on_drop_item(self, target, value, x, y, list_item):
        dragged_path = value
        if not dragged_path or not isinstance(dragged_path, str):
            return False

        tree_row = list_item.get_item()
        if not tree_row:
            return False
        target_item = tree_row.get_item()
        if not target_item:
            return False

        # Determine target directory
        target_dir = target_item.path if target_item.is_dir else os.path.dirname(target_item.path)

        # Do not allow dropping on itself or its own direct parent
        if dragged_path == target_dir or os.path.dirname(dragged_path) == target_dir:
            return False

        # Do not allow moving a directory into itself or its own subdirectories
        if target_dir.startswith(dragged_path + os.sep) or target_dir == dragged_path:
            return False

        new_path = os.path.join(target_dir, os.path.basename(dragged_path))

        try:
            os.rename(dragged_path, new_path)

            # Update currently open file path if moved
            if self.current_filepath == dragged_path:
                self.current_filepath = new_path
                self.editor.filepath = new_path
                if self.editor.on_save_status_changed:
                    self.editor.on_save_status_changed(new_path, "Saved")

            self._refresh_tree()

            toast = Adw.Toast.new(f"Moved {os.path.basename(dragged_path)} successfully")
            self.toast_overlay.add_toast(toast)
            return True
        except Exception as e:
            toast = Adw.Toast.new(f"Move failed: {e}")
            self.toast_overlay.add_toast(toast)
            return False

    def _show_delete_dialog(self, filepath):
        is_dir = os.path.isdir(filepath)
        name = os.path.basename(filepath)

        # Create a beautiful transient destructive dialog
        dialog = Adw.MessageDialog.new(
            self,
            "Delete Item?",
            f"Are you sure you want to permanently delete \"{name}\"?\nThis action cannot be undone."
        )

        # Add response buttons
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("delete", "Delete")
        dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)

        def on_response(dlg, response):
            if response == "delete":
                try:
                    import shutil
                    if is_dir:
                        shutil.rmtree(filepath)
                    else:
                        os.remove(filepath)

                    # If deleted item is (or is inside) the currently open file path, unload it!
                    if self.current_filepath:
                        if self.current_filepath == filepath or self.current_filepath.startswith(filepath + os.sep):
                            self.editor.unload_file()
                            self.current_filepath = None
                            self.main_view_stack.set_visible_child_name('welcome')

                    self._refresh_tree()

                    toast = Adw.Toast.new(f"Deleted {name}")
                    self.toast_overlay.add_toast(toast)
                except Exception as e:
                    toast = Adw.Toast.new(f"Delete failed: {e}")
                    self.toast_overlay.add_toast(toast)
            dialog.destroy()

        dialog.connect("response", on_response)
        dialog.present()
