# preferences.py
#
# Copyright 2026 Hobbies
# SPDX-License-Identifier: GPL-3.0-or-later

import os
import json
from gi.repository import Adw, Gtk, Gio

CONFIG_DIR = os.path.expanduser("~/.config/textay")
CONFIG_FILE = os.path.join(CONFIG_DIR, "preferences.json")

class PreferencesManager:
    """Manages application preferences stored in a JSON file."""
    
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(PreferencesManager, cls).__new__(cls)
            cls._instance.load()
        return cls._instance

    def load(self):
        self.prefs = {
            "show_all_files": False,
            "auto_save": True,
            "theme": "default",
            "editor_zoom": 120,
            "preview_zoom": 90
        }
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "r") as f:
                    data = json.load(f)
                    self.prefs.update(data)
            except Exception as e:
                print(f"Error loading preferences: {e}")
        else:
            self.save()

    def save(self):
        try:
            os.makedirs(CONFIG_DIR, exist_ok=True)
            with open(CONFIG_FILE, "w") as f:
                json.dump(self.prefs, f, indent=4)
        except Exception as e:
            print(f"Error saving preferences: {e}")

    def get(self, key, default=None):
        return self.prefs.get(key, default)

    def set(self, key, value):
        self.prefs[key] = value
        self.save()


class PreferencesWindow(Adw.PreferencesWindow):
    """A Libadwaita Preferences Window for Textay."""
    
    def __init__(self, parent_window, on_changed_callback=None):
        super().__init__(transient_for=parent_window, modal=True)
        self.set_title("Preferences")
        self.on_changed_callback = on_changed_callback
        self.pm = PreferencesManager()

        # Page
        page = Adw.PreferencesPage()
        page.set_title("General")
        page.set_icon_name("preferences-system-symbolic")
        self.add(page)

        # Group
        group = Adw.PreferencesGroup()
        group.set_title("File Explorer &amp; Editor")
        page.add(group)

        # Row 1: Show all files
        show_all_row = Adw.ActionRow()
        show_all_row.set_title("Show All Files")
        show_all_row.set_subtitle("Show all files as text instead of only standard text files")
        
        self.show_all_switch = Gtk.Switch()
        self.show_all_switch.set_active(self.pm.get("show_all_files", False))
        self.show_all_switch.set_valign(Gtk.Align.CENTER)
        self.show_all_switch.connect("state-set", self.on_show_all_toggled)
        
        show_all_row.add_suffix(self.show_all_switch)
        group.add(show_all_row)

        # Row 2: Auto save
        auto_save_row = Adw.ActionRow()
        auto_save_row.set_title("Auto Save")
        auto_save_row.set_subtitle("Automatically save changes as you type")
        
        self.auto_save_switch = Gtk.Switch()
        self.auto_save_switch.set_active(self.pm.get("auto_save", True))
        self.auto_save_switch.set_valign(Gtk.Align.CENTER)
        self.auto_save_switch.connect("state-set", self.on_auto_save_toggled)
        
        auto_save_row.add_suffix(self.auto_save_switch)
        group.add(auto_save_row)

        # Row 3: Editor Zoom
        editor_zoom_row = Adw.ActionRow()
        editor_zoom_row.set_title("Editor Text Scale (%)")
        editor_zoom_row.set_subtitle("Zoom level for the editor font (best experience: 110%)")
        
        editor_zoom_adj = Gtk.Adjustment.new(
            self.pm.get("editor_zoom", 120), 50.0, 200.0, 10.0, 10.0, 0.0
        )
        self.editor_zoom_spin = Gtk.SpinButton.new(editor_zoom_adj, 10.0, 0)
        self.editor_zoom_spin.set_valign(Gtk.Align.CENTER)
        self.editor_zoom_spin.connect("value-changed", self.on_editor_zoom_changed)
        
        editor_zoom_row.add_suffix(self.editor_zoom_spin)
        group.add(editor_zoom_row)

        # Row 4: Preview Zoom
        preview_zoom_row = Adw.ActionRow()
        preview_zoom_row.set_title("Preview Text Scale (%)")
        preview_zoom_row.set_subtitle("Zoom level for the live preview layout (best experience: 90%)")
        
        preview_zoom_adj = Gtk.Adjustment.new(
            self.pm.get("preview_zoom", 90), 50.0, 200.0, 10.0, 10.0, 0.0
        )
        self.preview_zoom_spin = Gtk.SpinButton.new(preview_zoom_adj, 10.0, 0)
        self.preview_zoom_spin.set_valign(Gtk.Align.CENTER)
        self.preview_zoom_spin.connect("value-changed", self.on_preview_zoom_changed)
        
        preview_zoom_row.add_suffix(self.preview_zoom_spin)
        group.add(preview_zoom_row)

    def on_show_all_toggled(self, widget, state):
        self.pm.set("show_all_files", state)
        if self.on_changed_callback:
            self.on_changed_callback("show_all_files", state)
        return False

    def on_auto_save_toggled(self, widget, state):
        self.pm.set("auto_save", state)
        if self.on_changed_callback:
            self.on_changed_callback("auto_save", state)
        return False

    def on_editor_zoom_changed(self, widget):
        val = int(widget.get_value())
        self.pm.set("editor_zoom", val)
        if self.on_changed_callback:
            self.on_changed_callback("editor_zoom", val)

    def on_preview_zoom_changed(self, widget):
        val = int(widget.get_value())
        self.pm.set("preview_zoom", val)
        if self.on_changed_callback:
            self.on_changed_callback("preview_zoom", val)
