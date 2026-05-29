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
        self.editor_zoom = self.pm.get("editor_zoom", 120)
        self.preview_zoom = self.pm.get("preview_zoom", 90)
        self._zoom_css_provider = None

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
        self.set_editor_zoom(self.editor_zoom)
        self.set_preview_zoom(self.preview_zoom)

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
            if width <= 50:
                # Retrieve root window size or fallback to standard splits
                root = self.get_root()
                if root:
                    win_width = root.get_width()
                    width = int(win_width * 0.78) if win_width > 50 else 900
                else:
                    width = 900
            
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
            
            # Unbox WebKit.JavascriptResult to JSC.Value if wrapper is present
            if hasattr(js_result, "get_js_value"):
                js_val = js_result.get_js_value()
            else:
                js_val = js_result

            # Bulletproof numeric extraction from JSC.Value or raw python types
            if hasattr(js_val, "is_number") and js_val.is_number():
                val = js_val.to_double()
            elif hasattr(js_val, "to_double"):
                val = js_val.to_double()
            elif hasattr(js_val, "to_string"):
                val_str = js_val.to_string()
                if val_str:
                    val = float(val_str)
            elif isinstance(js_val, (int, float)):
                val = float(js_val)
            elif isinstance(js_val, str):
                val = float(js_val)

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
            max_val = int(max_match.group(1)) if max_match else 90
            
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

        bg_color = "#121212" if is_dark else "#fafafa" # Clean slate canvas
        fg_color = "#d4d4d8" if is_dark else "#3f3f46" # neutral-300 / neutral-700
        headings_color = "#f4f4f5" if is_dark else "#18181b" # neutral-50 / neutral-900
        code_bg = "#1e1e1f" if is_dark else "#f4f4f5" # neutral-800 / neutral-100
        code_fg = "#f4f4f5" if is_dark else "#18181b" # neutral-50 / neutral-900
        link_color = "#60a5fa" if is_dark else "#2563eb" # blue-400 / blue-600
        border_color = "#27272a" if is_dark else "#e4e4e7" # neutral-800 / neutral-200
        quote_border = "#3f3f46" if is_dark else "#d4d4d8" # neutral-700 / neutral-300

        html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="utf-8">
            <style>
                body {{
                    font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
                    background-color: {bg_color};
                    color: {fg_color};
                    line-height: 1.75;
                    font-size: 1rem;
                    padding: 32px 40px;
                    margin: 0;
                    max-width: 720px;
                    margin-left: auto;
                    margin-right: auto;
                }}
                hr {{
                    margin: 48px 0;
                    color: {border_color};
                    opacity: 0.5;
                }}
                h1, h2, h3, h4 {{
                    color: {headings_color};
                    font-weight: 800;
                    line-height: 1.25;
                    margin-bottom: 0.88em;
                }}
                h1 {{
                    font-size: 2.25em;
                    margin-top: 0;
                    margin-bottom: 0.88em;
                    border-bottom: 1px solid {border_color};
                    padding-bottom: 12px;
                }}
                h2 {{
                    font-size: 1.5em;
                    margin-top: 2em;
                    margin-bottom: 1em;
                    font-weight: 700;
                    border-bottom: 1px solid {border_color};
                    padding-bottom: 8px;
                }}
                h3 {{
                    font-size: 1.25em;
                    margin-top: 1.6em;
                    margin-bottom: 0.6em;
                    font-weight: 600;
                }}
                h4 {{
                    font-size: 1em;
                    margin-top: 1.5em;
                    margin-bottom: 0.5em;
                    font-weight: 600;
                }}
                p {{
                    margin-top: 1.25em;
                    margin-bottom: 1.25em;
                }}
                ol, ul {{
                    margin-top: 1.25em;
                    margin-bottom: 1.25em;
                    padding-left: 1.625em;
                }}
                li {{
                    margin-top: 0.5em;
                    margin-bottom: 0.5em;
                }}
                ol ol, ol ul, ul ol, ul ul {{
                    margin-top: 0.75em;
                    margin-bottom: 0.75em;
                }}
                blockquote {{
                    font-weight: 500;
                    font-style: italic;
                    color: {headings_color};
                    border-left: 4px solid {quote_border};
                    margin-top: 1.6em;
                    margin-bottom: 1.6em;
                    padding-left: 1em;
                }}
                code {{
                    color: {code_fg};
                    background-color: {code_bg};
                    padding: 0.25em 0.5em;
                    border-radius: 0.375em;
                    font-size: 0.875em;
                    font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace;
                    font-weight: 600;
                }}
                pre {{
                    background-color: {code_bg};
                    border-radius: 0.5em;
                    margin-top: 1.71em;
                    margin-bottom: 1.71em;
                    overflow-x: auto;
                    padding: 1em 1.5em;
                    border: 1px solid {border_color};
                }}
                pre code {{
                    background-color: transparent;
                    padding: 0;
                    border-radius: 0;
                    font-size: 0.875em;
                    color: {code_fg};
                    font-weight: 400;
                }}
                table {{
                    width: 100%;
                    table-layout: auto;
                    text-align: left;
                    margin-top: 2em;
                    margin-bottom: 2em;
                    font-size: 0.875em;
                    line-height: 1.71;
                    border-collapse: collapse;
                }}
                thead {{
                    border-bottom: 1px solid {border_color};
                }}
                thead th {{
                    color: {headings_color};
                    font-weight: 600;
                    padding-bottom: 0.75em;
                    padding-left: 0.75em;
                    padding-right: 0.75em;
                }}
                tbody tr {{
                    border-bottom: 1px solid {border_color};
                }}
                tbody tr:last-child {{
                    border-bottom: none;
                }}
                tbody td {{
                    padding-top: 0.75em;
                    padding-bottom: 0.75em;
                    padding-left: 0.75em;
                    padding-right: 0.75em;
                }}
                a {{
                    color: {link_color};
                    text-decoration: none;
                    font-weight: 500;
                    border-bottom: 1px solid transparent;
                    transition: border-color 0.15s;
                }}
                a:hover {{
                    border-color: {link_color};
                }}
                .todo-item {{
                    display: flex;
                    align-items: center;
                    gap: 12px;
                    margin: 4px 0px 4px -28px !important;
                    border: 1px solid {border_color};
                    padding: 2px 6px;
                    border-radius: 8px;
                    background-color: {code_bg};
                }}
                .todo-item input[type="checkbox"] {{
                    -webkit-appearance: none;
                    appearance: none;
                    margin: 0;
                    width: 20px;
                    height: 20px;
                    border: 2px solid {quote_border};
                    border-radius: 6px;
                    background-color: transparent;
                    display: inline-grid;
                    place-content: center;
                    outline: none;
                    cursor: not-allowed;
                }}
                .todo-item input[type="checkbox"]:checked {{
                    background-color: #3b82f6;
                    border-color: #3b82f6;
                }}
                .todo-item.finished {{
                    opacity: 0.6;
                    text-decoration: line-through;
                }}
                .checklist-card {{
                    border: 1px solid {border_color};
                    border-radius: 12px;
                    padding: 4px;
                    margin: 20px 0;
                }}
                .checklist-header {{
                    font-size: 16px;
                    padding: 4px 8px;
                    color: {link_color};
                }}
                .checklist-items {{
                    list-style-type: none;
                    padding: 0;
                    margin: 0;
                }}
                .checklist-items .todo-item {{
                    background-color: {bg_color};
                    margin: 4px 0 0 0 !important;
                }}
                .progress-card {{
                    margin: 4px 0px 4px 4px !important;
                    border: 1px solid {border_color};
                    padding: 2px 6px 2px 8px;
                    border-radius: 8px;
                    display: inline-flex;
                }}
                .progress-ratio {{
                    color: {fg_color};
                }}
                .progress-bar-text {{
                    letter-spacing: 1px;
                }}
                .progress-filled {{
                    color: #2ec27e;
                }}
                .progress-empty {{
                    color: #77767b;
                    opacity: 0.6;
                }}
                .progress-percent {{
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
        self.last_compiled_html = html
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

    def set_editor_zoom(self, zoom):
        """Scale text size in the GtkSourceView editor."""
        self.editor_zoom = zoom
        pt_size = 11.0 * (zoom / 100.0)
        css = f"textview {{ font-size: {pt_size:.1f}pt; }}"
        
        if hasattr(self, "_zoom_css_provider") and self._zoom_css_provider:
            self.source_view.get_style_context().remove_provider(self._zoom_css_provider)
        
        self._zoom_css_provider = Gtk.CssProvider()
        self._zoom_css_provider.load_from_data(css.encode('utf-8'))
        self.source_view.get_style_context().add_provider(
            self._zoom_css_provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )
        
    def set_preview_zoom(self, zoom):
        """Scale text size in the WebKit markdown preview."""
        self.preview_zoom = zoom
        if self.webview:
            self.webview.set_zoom_level(zoom / 100.0)

    def export_to_pdf(self, pdf_path, page_size="A4", orientation="Portrait", margin_mm=20):
        """Export the rendered HTML content to a PDF file with custom page setup."""
        if not hasattr(self, "webview") or not self.webview:
            return False
            
        try:
            # Create paper size based on user selection
            sz = page_size.upper()
            if sz == "LETTER":
                paper_name = Gtk.PAPER_NAME_LETTER
            elif sz == "A5":
                paper_name = Gtk.PAPER_NAME_A5
            elif sz == "A3":
                paper_name = Gtk.PAPER_NAME_A3
            else:
                paper_name = Gtk.PAPER_NAME_A4
                
            paper_size = Gtk.PaperSize.new(paper_name)
            
            # Configure page setup (orientation & margins)
            page_setup = Gtk.PageSetup.new()
            page_setup.set_paper_size(paper_size)
            
            if orientation.lower() == "landscape":
                page_setup.set_orientation(Gtk.PageOrientation.LANDSCAPE)
            else:
                page_setup.set_orientation(Gtk.PageOrientation.PORTRAIT)
                
            # Set margins in millimeters
            page_setup.set_top_margin(margin_mm, Gtk.Unit.MM)
            page_setup.set_bottom_margin(margin_mm, Gtk.Unit.MM)
            page_setup.set_left_margin(margin_mm, Gtk.Unit.MM)
            page_setup.set_right_margin(margin_mm, Gtk.Unit.MM)
            
            # Configure print settings to output directly to the chosen PDF path
            print_settings = Gtk.PrintSettings.new()
            print_settings.set("printer", "Print to File")
            print_settings.set("output-file-format", "pdf")
            
            # Print settings require a file:// URI for saving to a file
            uri = "file://" + os.path.abspath(pdf_path)
            print_settings.set("output-uri", uri)
            
            # Create print operation and retain strong reference on self to prevent garbage collection
            self._current_print_op = WebKit.PrintOperation.new(self.webview)
            self._current_print_op.set_print_settings(print_settings)
            self._current_print_op.set_page_setup(page_setup)
            
            def on_finished(op):
                # Clean up the reference after printing is fully done
                self._current_print_op = None
                
            def on_failed(op, error):
                print(f"Print failed: {error.message}")
                self._current_print_op = None
                
            self._current_print_op.connect("finished", on_finished)
            self._current_print_op.connect("failed", on_failed)
            
            # Execute print operation asynchronously without showing standard dialogs
            self._current_print_op.print_()
            return True
        except Exception as e:
            print(f"Error during WebKit PDF print operation: {e}")
            return False

    def get_preview_html(self):
        """Returns the last compiled HTML content for live printing/preview usage."""
        return getattr(self, "last_compiled_html", "")
