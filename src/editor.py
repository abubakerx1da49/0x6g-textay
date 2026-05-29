# editor.py
#
# Copyright 2026 Hobbies
# SPDX-License-Identifier: GPL-3.0-or-later

import os
import gi

gi.require_version('GtkSource', '5')
from gi.repository import Adw, Gtk, GLib, GtkSource, Gdk
from .preferences import PreferencesManager

# Dynamically load WebKitGTK
WebKit = None
try:
    gi.require_version("WebKit", "6.0")
    from gi.repository import WebKit
except ValueError:
    try:
        gi.require_version("WebKit2", "4.1")
        from gi.repository import WebKit2 as WebKit
    except ValueError:
        pass


# Initialise GtkSourceView's subsystem (language defs, style schemes, etc.)
GtkSource.init()

# Map file extensions → GtkSource language IDs
_EXT_TO_LANG = {
    '.txt':   'plain',
}


def _get_language(filepath):
    """Returns a GtkSource.Language for the given file, or None for plain text."""
    lang_manager = GtkSource.LanguageManager.get_default()
    # Let GtkSource auto-detect first via content-type / filename
    lang = lang_manager.guess_language(filepath, None)
    if lang:
        return lang
    # Fallback: explicit extension map
    ext = os.path.splitext(filepath)[1].lower()
    lang_id = _EXT_TO_LANG.get(ext)
    if lang_id:
        return lang_manager.get_language(lang_id)
    return None


def _get_style_scheme():
    """Returns Adwaita-dark or Adwaita style scheme matching the system theme."""
    scheme_manager = GtkSource.StyleSchemeManager.get_default()
    
    # Query dark/light theme dynamically from Adw.StyleManager
    style_manager = Adw.StyleManager.get_default()
    is_dark = style_manager.get_dark()
    
    preferred = ("Adwaita-dark", "classic-dark") if is_dark else ("Adwaita", "classic")
    for name in preferred:
        scheme = scheme_manager.get_scheme(name)
        if scheme:
            return scheme
            
    # Fallback to any matching scheme if preferred is missing
    for name in ("Adwaita-dark", "Adwaita", "classic-dark", "classic"):
        scheme = scheme_manager.get_scheme(name)
        if scheme:
            return scheme
    return None


class TextayEditor(Gtk.Box):
    """
    A native GNOME source editor using GtkSource.View —
    the exact same widget used by GNOME Text Editor and GNOME Builder.
    """

    def __init__(self, workspace_manager, on_save_status_changed=None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)

        self.wm = workspace_manager
        self.pm = PreferencesManager()
        self.on_save_status_changed = on_save_status_changed

        self.filepath = None
        self.original_disk_content = ""
        self.is_monitoring = False
        self.auto_save_timeout_id = 0
        self.ignore_buffer_changes = False

        # ── AdwBanner: external modification warning ───────────────────────
        self.banner = Adw.Banner()
        self.banner.set_title("File modified outside Textay")
        self.banner.set_button_label("Reload")
        self.banner.set_revealed(False)
        self.banner.connect("button-clicked", self._on_reload_clicked)
        self.append(self.banner)

        # "Keep mine" secondary action beneath banner
        self.keep_mine_btn = Gtk.Button(label="Keep my version and overwrite")
        self.keep_mine_btn.add_css_class("flat")
        self.keep_mine_btn.set_visible(False)
        self.keep_mine_btn.connect("clicked", self._on_keep_mine_clicked)
        self.append(self.keep_mine_btn)



        # ── GtkSource.Buffer ───────────────────────────────────────────────
        self.source_buffer = GtkSource.Buffer()
        self.source_buffer.set_highlight_syntax(True)
        self.source_buffer.set_highlight_matching_brackets(True)
        scheme = _get_style_scheme()
        if scheme:
            self.source_buffer.set_style_scheme(scheme)

        # ── GtkSource.View ─────────────────────────────────────────────────
        self.source_view = GtkSource.View.new_with_buffer(self.source_buffer)

        # Native GtkSourceView features — same as GNOME Text Editor defaults
        self.source_view.set_show_line_numbers(True)
        self.source_view.set_show_line_marks(True)
        self.source_view.set_highlight_current_line(True)
        self.source_view.set_auto_indent(True)
        self.source_view.set_indent_on_tab(True)
        self.source_view.set_tab_width(4)
        self.source_view.set_insert_spaces_instead_of_tabs(True)
        self.source_view.set_smart_home_end(GtkSource.SmartHomeEndType.BEFORE)
        self.source_view.set_smart_backspace(True)
        self.source_view.set_monospace(True)
        self.source_view.set_hexpand(True)
        self.source_view.set_vexpand(True)
        self.source_view.set_left_margin(4)
        self.source_view.set_top_margin(4)
        self.source_view.set_bottom_margin(4)

        # Wrap long lines (like GNOME Text Editor)
        self.source_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)

        # ── Scrolled window ────────────────────────────────────────────────
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_hexpand(True)
        scrolled.set_vexpand(True)
        scrolled.set_child(self.source_view)

        # ── Gtk.Paned Split container for Markdown Live Preview ────────────
        self.main_paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        self.main_paned.set_hexpand(True)
        self.main_paned.set_vexpand(True)
        self.main_paned.set_start_child(scrolled)

        self.preview_scrolled = Gtk.ScrolledWindow()
        self.preview_scrolled.set_hexpand(True)
        self.preview_scrolled.set_vexpand(True)
        self.preview_scrolled.set_visible(False)
        # Hide the outer scrolled window's scrollbars since WebKit renders its own scrollbar
        self.preview_scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.NEVER)

        if WebKit:
            self.webview = WebKit.WebView()
            self.preview_scrolled.set_child(self.webview)

            # Redirect physical scroll inputs inside WebKit to the editor
            scroll_controller = Gtk.EventControllerScroll.new(Gtk.EventControllerScrollFlags.VERTICAL)
            scroll_controller.connect("scroll", self._on_webview_scroll_event)
            self.webview.add_controller(scroll_controller)

            # WebKit User Content Manager for bidirectional scrollbar drag synchronization
            ucm = self.webview.get_user_content_manager()
            ucm.register_script_message_handler("scrollSync")
            ucm.connect("script-message-received::scrollSync", self._on_javascript_scroll)
        else:
            self.webview = None
            self.preview_scrolled.set_child(Gtk.Label(label="WebKitGTK is not installed on your system."))

        self.main_paned.set_end_child(self.preview_scrolled)
        self.append(self.main_paned)

        # Proportional scroll synchronization setup
        self.source_scrolled = scrolled
        self._syncing_scroll = False
        scrolled.get_vadjustment().connect("value-changed", self._on_editor_scroll)

        # ── Buffer signals ─────────────────────────────────────────────────
        self.source_buffer.connect("changed", self._on_buffer_changed)



        # ── System Theme Dynamic Tracking ─────────────────────────────────
        style_manager = Adw.StyleManager.get_default()
        style_manager.connect("notify::dark", self._on_system_theme_changed)

    def _on_system_theme_changed(self, style_manager, pspec):
        scheme = _get_style_scheme()
        if scheme:
            self.source_buffer.set_style_scheme(scheme)



    # ──────────────────────────────────────────────────────────────────────
    # Public API
    # ──────────────────────────────────────────────────────────────────────

    def load_file(self, filepath):
        """Loads a file into the GtkSource.View editor."""
        self.filepath = filepath

        # Hide any stale external-change warnings
        self.banner.set_revealed(False)
        self.keep_mine_btn.set_visible(False)

        # Apply syntax highlighting based on the file
        lang = _get_language(filepath)
        self.source_buffer.set_language(lang)

        # Read & populate buffer
        self.ignore_buffer_changes = True
        self.original_disk_content = self.wm.read_file(filepath)
        self.source_buffer.set_text(self.original_disk_content)
        self.ignore_buffer_changes = False

        # Place cursor at top
        self.source_buffer.place_cursor(self.source_buffer.get_start_iter())
        self.source_view.scroll_to_mark(
            self.source_buffer.get_insert(), 0.0, True, 0.0, 0.0
        )

        # Start file-system monitoring
        self.wm.monitor_file(filepath, self._on_external_modification)
        self.is_monitoring = True

        if self.on_save_status_changed:
            self.on_save_status_changed(self.filepath, "Saved")

        # Automatically display live preview for Markdown files
        is_md = filepath.lower().endswith(".md")
        self.set_preview_visible(is_md)

    def unload_file(self):
        """Saves pending changes, stops monitoring, and clears the buffer."""
        self.save_immediately()
        if self.filepath and self.is_monitoring:
            self.wm.unmonitor_file(self.filepath)
        self.filepath = None
        self.original_disk_content = ""
        self.is_monitoring = False
        self.banner.set_revealed(False)
        self.keep_mine_btn.set_visible(False)
        self.set_preview_visible(False)

        self.ignore_buffer_changes = True
        self.source_buffer.set_text("")
        self.source_buffer.set_language(None)
        self.ignore_buffer_changes = False

    def save_immediately(self):
        """Writes the buffer content to disk synchronously."""
        if not self.filepath:
            return

        start, end = self.source_buffer.get_bounds()
        current_content = self.source_buffer.get_text(start, end, True)

        if current_content == self.original_disk_content:
            if self.on_save_status_changed:
                self.on_save_status_changed(self.filepath, "Saved")
            return

        # Pause monitoring so we don't trigger our own callback
        if self.is_monitoring:
            self.wm.unmonitor_file(self.filepath)

        success, err = self.wm.write_file(self.filepath, current_content)

        if success:
            self.original_disk_content = current_content
            if self.on_save_status_changed:
                self.on_save_status_changed(self.filepath, "Saved")
        else:
            print(f"Auto-save error: {err}")
            if self.on_save_status_changed:
                self.on_save_status_changed(self.filepath, f"Error: {err}")

        # Re-attach monitor
        if self.is_monitoring:
            self.wm.monitor_file(self.filepath, self._on_external_modification)

    # ──────────────────────────────────────────────────────────────────────
    # Buffer change / auto-save
    # ──────────────────────────────────────────────────────────────────────

    def _on_buffer_changed(self, *_):
        if self.ignore_buffer_changes:
            return

        if self.on_save_status_changed:
            self.on_save_status_changed(self.filepath, "Unsaved changes…")

        if self.preview_scrolled.get_visible():
            self.update_markdown_preview()

        if self.pm.get("auto_save", True):
            self.save_immediately()

    # ──────────────────────────────────────────────────────────────────────
    # Markdown Live Preview
    # ──────────────────────────────────────────────────────────────────────

    def set_preview_visible(self, visible):
        self.preview_scrolled.set_visible(visible)
        if visible:
            # Split equally
            width = self.get_width()
            self.main_paned.set_position(int(width / 2))
            self.update_markdown_preview()

    def _on_editor_scroll(self, adj):
        if not self.webview or self._syncing_scroll:
            return
        self._syncing_scroll = True
        try:
            editor_adj = self.source_scrolled.get_vadjustment()
            editor_val = editor_adj.get_value()
            editor_max = editor_adj.get_upper() - editor_adj.get_page_size()

            if editor_max > 0:
                ratio = editor_val / editor_max
                # Evaluate scroll position directly inside WebView document body via JS
                js = f"""
                window.isSyncing = true;
                var maxScroll = document.documentElement.scrollHeight - window.innerHeight;
                window.scrollTo(0, maxScroll * {ratio});
                setTimeout(function() {{
                    window.isSyncing = false;
                }}, 80);
                """
                self.webview.evaluate_javascript(js, -1, None, None, None, None)
        except Exception as e:
            print(f"Scroll sync error: {e}")
        finally:
            self._syncing_scroll = False

    def _on_webview_scroll_event(self, controller, dx, dy):
        """Redirect scroll wheel events from the WebKit preview to the editor."""
        editor_adj = self.source_scrolled.get_vadjustment()
        new_val = editor_adj.get_value() + dy * 32
        new_val = max(0, min(new_val, editor_adj.get_upper() - editor_adj.get_page_size()))
        editor_adj.set_value(new_val)
        return True

    def _on_javascript_scroll(self, ucm, js_result):
        """Receive scroll updates from JavaScript inside WebKit and synchronize back to the editor."""
        if self._syncing_scroll:
            return
        self._syncing_scroll = True
        try:
            val = None
            if hasattr(js_result, "get_js_value"):
                js_val = js_result.get_js_value()
                if js_val.is_number():
                    val = js_val.to_double()
            else:
                val = js_result.get_value()

            if val is not None:
                ratio = float(val)
                editor_adj = self.source_scrolled.get_vadjustment()
                editor_max = editor_adj.get_upper() - editor_adj.get_page_size()
                if editor_max > 0:
                    editor_adj.set_value(ratio * editor_max)
        except Exception as e:
            print(f"JS scroll sync error: {e}")
        finally:
            self._syncing_scroll = False
    def update_markdown_preview(self):
        if not self.webview:
            return
        start, end = self.source_buffer.get_bounds()
        md_text = self.source_buffer.get_text(start, end, True)

        # Custom checklist syntax preprocessor: {% checklist title="..." items=[...] /%}
        import re
        def parse_custom_checklist(match):
            title = match.group(1).strip()
            items_str = match.group(2)
            # Find all double-quoted strings within the items array
            items = re.findall(r'"([^"]*)"', items_str)
            
            card_html = '<div class="checklist-card">'
            card_html += f'<div class="checklist-header">{title}</div>'
            card_html += '<ul class="checklist-items">'
            for item in items:
                checked_attr = ""
                finished_class = ""
                item_text = item
                
                # Normalize different unchecked/checked bracket representations: [-], [- ], [x], [X], [ ]
                if item.startswith("[-] "):
                    item_text = item[4:]
                elif item.startswith("[-]"):
                    item_text = item[3:]
                elif item.startswith("[x] ") or item.startswith("[X] "):
                    checked_attr = "checked"
                    finished_class = "finished"
                    item_text = item[4:]
                elif item.startswith("[x]") or item.startswith("[X]"):
                    checked_attr = "checked"
                    finished_class = "finished"
                    item_text = item[3:]
                elif item.startswith("[ ] "):
                    item_text = item[4:]
                elif item.startswith("[ ]"):
                    item_text = item[3:]

                label_html = f'<del>{item_text}</del>' if checked_attr else item_text
                card_html += f'<li class="todo-item {finished_class}">'
                card_html += f'<input type="checkbox" {checked_attr} disabled />'
                card_html += f'<span>{label_html}</span>'
                card_html += '</li>'
            card_html += '</ul>'
            card_html += '</div>'
            return card_html

        def parse_custom_progress(match):
            attributes = match.group(1)
            
            val_match = re.search(r'value=(\d+)', attributes)
            val = int(val_match.group(1)) if val_match else 0
            
            max_match = re.search(r'max=(\d+)', attributes)
            max_val = int(max_match.group(1)) if max_match else 100
            
            emoji_match = re.search(r'emoji="([^"]*)"', attributes)
            emoji = emoji_match.group(1) if emoji_match else None
            
            if max_val > 0:
                percentage = int((val / max_val) * 100)
            else:
                percentage = 0
            percentage = max(0, min(percentage, 100))
            
            num_segments = 20
            filled_segments = int((percentage / 100) * num_segments)
            empty_segments = num_segments - filled_segments
            
            if emoji:
                filled_html = f'<span class="progress-emoji">{"".join([emoji]*filled_segments)}</span>'
                empty_html = f'<span class="progress-empty">{"░" * empty_segments}</span>'
            else:
                filled_html = f'<span class="progress-filled">{"█" * filled_segments}</span>'
                empty_html = f'<span class="progress-empty">{"░" * empty_segments}</span>'
                
            card_html = '<div class="progress-card">'
            card_html += f'<span class="progress-ratio">{val}/{max_val} &nbsp;</span>'
            card_html += f'<span class="progress-bar-text">{filled_html}{empty_html} &nbsp;</span>'
            card_html += f'<span class="progress-percent">{percentage}%</span>'
            card_html += '</div>'
            return card_html

        try:
            pattern = re.compile(r'\{%\s*checklist\s+title="([^"]*)"\s+items=\[(.*?)\]\s*/%\}', re.DOTALL)
            md_text = pattern.sub(parse_custom_checklist, md_text)
            
            # Progress bar syntax preprocessor
            progress_pattern = re.compile(r'\{%\s*progress\s+([^%]*)/\%\}', re.DOTALL)
            md_text = progress_pattern.sub(parse_custom_progress, md_text)
        except Exception as e:
            print(f"Checklist/Progress parsing error: {e}")

        try:
            import markdown
            html_body = markdown.markdown(
                md_text,
                extensions=["fenced_code", "tables", "toc"]
            )
        except Exception:
            try:
                import markdown
                html_body = markdown.markdown(md_text)
            except Exception as e:
                html_body = f"<p>Error parsing markdown: {e}</p>"

        # Task list custom syntax handling: converts [ ] and [x] lists into gorgeous checkboxes
        import re
        html_body = re.sub(
            r'<li>\s*\[\s*\]\s*(.*?)</li>',
            r'<li class="todo-item"><input type="checkbox" disabled /> \1</li>',
            html_body
        )
        html_body = re.sub(
            r'<li>\s*\[\s*[xX]\s*\]\s*(.*?)</li>',
            r'<li class="todo-item finished"><input type="checkbox" checked disabled /> <del>\1</del></li>',
            html_body
        )

        # Get current scroll ratio of the editor
        editor_adj = self.source_scrolled.get_vadjustment()
        editor_val = editor_adj.get_value()
        editor_max = editor_adj.get_upper() - editor_adj.get_page_size()
        ratio = editor_val / editor_max if editor_max > 0 else 0.0

        # Dynamic System-themed beautiful CSS stylesheet
        style_manager = Adw.StyleManager.get_default()
        is_dark = style_manager.get_dark()

        bg_color = "#1e1e1e" if is_dark else "#ffffff"
        fg_color = "#e0e0e0" if is_dark else "#2e2e2e"
        code_bg = "#2d2d2d" if is_dark else "#f5f5f5"
        link_color = "#3584e4" if is_dark else "#1c71d8"
        border_color = "#333333" if is_dark else "#e0e0e0"

        html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="utf-8">
            <style>
                body {{
                    font-family: system-ui, -apple-system, sans-serif;
                    background-color: {bg_color};
                    color: {fg_color};
                    line-height: 1.6;
                    padding: 24px;
                    margin: 0;
                }}
                h1, h2, h3, h4, h5, h6 {{
                    border-bottom: 1px solid {border_color};
                    padding-bottom: 8px;
                    margin-top: 24px;
                }}
                code {{
                    background-color: {code_bg};
                    padding: 2px 6px;
                    border-radius: 4px;
                    font-family: monospace;
                }}
                pre code {{
                    display: block;
                    padding: 12px;
                    overflow-x: auto;
                }}
                blockquote {{
                    border-left: 4px solid {link_color};
                    margin: 0;
                    padding-left: 16px;
                    color: gray;
                }}
                table {{
                    border-collapse: collapse;
                    width: 100%;
                    margin: 16px 0;
                }}
                th, td {{
                    border: 1px solid {border_color};
                    padding: 8px 12px;
                    text-align: left;
                }}
                th {{
                    background-color: {code_bg};
                }}
                a {{
                    color: {link_color};
                    text-decoration: none;
                }}
                a:hover {{
                    text-decoration: underline;
                }}
                .todo-item {{
                    display: flex;
                    align-items: center;
                    gap: 8px;
                    margin: 2px 0px 2px -40px !important;
                    border: 1px solid {border_color};
                    padding: 8px 12px;
                    border-radius: 8px;
                }}
                .todo-item input[type="checkbox"] {{
                    -webkit-appearance: none;
                    appearance: none;
                    margin: 0;
                    width: 20px;
                    height: 20px;
                    border: 2px solid {border_color};
                    border-radius: 50%;
                    background-color: transparent;
                    display: inline-grid;
                    place-content: center;
                    outline: none;
                    cursor: not-allowed;
                    transition: background-color 0.15s, border-color 0.15s;
                }}
                .todo-item input[type="checkbox"]:checked {{
                    background-color: #3584e4;
                    border-color: #3584e4;
                }}
                .todo-item input[type="checkbox"]:checked::before {{
                    content: "✓";
                    color: white;
                    font-size: 12px;
                    font-weight: bold;
                }}
                .todo-item.finished {{
                    opacity: 0.7;
                }}
                .checklist-card {{
                    background-color: {code_bg};
                    border: 1px solid {border_color};
                    border-radius: 12px;
                    padding: 16px 20px;
                    margin: 20px 0;
                    box-shadow: 0 4px 10px rgba(0, 0, 0, 0.08);
                }}
                .checklist-header {{
                    font-size: 14px;
                    font-weight: bold;
                    letter-spacing: 0.8px;
                    text-transform: uppercase;
                    border-bottom: 1px solid {border_color};
                    padding-bottom: 8px;
                    margin-bottom: 12px;
                    color: {link_color};
                }}
                .checklist-items {{
                    list-style-type: none;
                    padding: 0;
                    margin: 0;
                }}
                .checklist-items .todo-item {{
                    background-color: {bg_color};
                    margin: 6px 0 !important;
                }}
                .progress-card {{
                    background-color: {code_bg};
                    border: 1px solid {border_color};
                    border-radius: 8px;
                    padding: 4px 12px;
                    margin: 2px 0;
                    display: inline-flex;
                    font-family: monospace;
                    font-size: 14px;
                }}
                .progress-ratio {{
                    font-weight: bold;
                    color: {fg_color};
                    min-width: 50px;
                }}
                .progress-bar-text {{
                    letter-spacing: 1px;
                    font-size: 15px;
                }}
                .progress-filled {{
                    color: #2ec27e;
                }}
                .progress-empty {{
                    color: #77767b;
                    opacity: 0.6;
                }}
                .progress-percent {{
                    font-weight: bold;
                    color: {link_color};
                    min-width: 45px;
                    text-align: right;
                }}
            </style>
        </head>
        <body>
            {html_body}
            <script>
                window.isSyncing = false;

                // Instant layout scroll alignment on document load/re-render
                window.onload = function() {{
                    window.isSyncing = true;
                    var maxScroll = document.documentElement.scrollHeight - window.innerHeight;
                    window.scrollTo(0, maxScroll * {ratio});
                    setTimeout(function() {{
                        window.isSyncing = false;
                    }}, 80);
                }};

                // Bidirectional user scroll listener
                window.onscroll = function() {{
                    if (!window.isSyncing) {{
                        var maxScroll = document.documentElement.scrollHeight - window.innerHeight;
                        var ratio = maxScroll > 0 ? (window.scrollY / maxScroll) : 0;
                        window.webkit.messageHandlers.scrollSync.postMessage(ratio);
                    }}
                }};
            </script>
        </body>
        </html>
        """
        self.webview.load_html(html, "")

    # ──────────────────────────────────────────────────────────────────────
    # External modification handling
    # ──────────────────────────────────────────────────────────────────────

    def _on_external_modification(self, filepath):
        if filepath != self.filepath:
            return

        disk_content = self.wm.read_file(filepath)
        start, end = self.source_buffer.get_bounds()
        editor_content = self.source_buffer.get_text(start, end, True)

        if disk_content != editor_content:
            # Cancel any pending auto-save to avoid overwriting the new version
            if self.auto_save_timeout_id > 0:
                GLib.source_remove(self.auto_save_timeout_id)
                self.auto_save_timeout_id = 0

            self.banner.set_revealed(True)
            self.keep_mine_btn.set_visible(True)

            if self.on_save_status_changed:
                self.on_save_status_changed(self.filepath, "Modified externally")

    def _on_reload_clicked(self, *_):
        """Reload from disk, discarding in-editor changes."""
        self.banner.set_revealed(False)
        self.keep_mine_btn.set_visible(False)
        if self.filepath:
            self.load_file(self.filepath)

    def _on_keep_mine_clicked(self, *_):
        """Overwrite disk with current editor content."""
        self.banner.set_revealed(False)
        self.keep_mine_btn.set_visible(False)
        self.save_immediately()
