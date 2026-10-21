"""Optional GX Works2 desktop automation driven through UIA (no mouse, no foreground focus).

GX Works2 has no public automation API. What was verified on a live 1.620W install
(docs/RESEARCH.md E1/E3):

* the ladder editor does NOT accept pasted IL text - Ctrl+V was tested and did nothing,
  so the old clipboard-paste function below is legacy, not the delivery route;
* the menus are owner-drawn (no HMENU, WM_COMMAND injection fails), but the UIA tree
  exposes every MenuItem and expand()/invoke() drive them reliably WITHOUT focus;
* Edit > "Read from CSV file" (从CSV文件读取) imports a 7-column program CSV into the
  ladder editor as a whole-block replacement, then F4 compiles it.

The working seam is therefore: write the CSV, invoke the menu item, confirm, compare the
step count in the window title, save. Device-comment CSV uses the same dialog machinery.

Dependencies are imported lazily so the package stays installable everywhere, and every
write action requires confirm=true because it replaces the program in the open editor.
"""

from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

#: Window titles GX Works2 uses, checked case-insensitively as substrings.
WINDOW_HINTS = ("gx works2", "gx developer", "gppw2", "melsoft", "三菱", "gx works3")


class AutomationError(RuntimeError):
    """Raised when GX Works2 automation cannot proceed (missing dependency, no window)."""


def _require(package: str, hint: str) -> Any:
    try:
        return __import__(package)
    except ImportError as exc:  # pragma: no cover - depends on the host machine
        raise AutomationError(
            package + " is not installed; run: pip install 'gxworks2-mcp[ui]' (" + hint + ")"
        ) from exc


@dataclass
class Window:
    title: str
    handle: int


#: Registry and disk locations GX Works2 (SWnDN-GPPW2) uses on a typical install.
_INSTALL_HINTS = (
    r"C:\Program Files (x86)\MELSOFT\GPPW2",
    r"C:\Program Files\MELSOFT\GPPW2",
    r"D:\MELSOFT\GPPW2",
)

#: Executable names that host the GX Works2 main window.
_EXE_NAMES = ("GPPW2.exe", "GD2.exe", "GX Works2 FastBoot.exe", "GX Works2 Startup.exe")


def find_install() -> dict[str, object]:
    """Locate the GX Works2 installation directory and its main executable."""

    import os

    candidates = list(_INSTALL_HINTS)
    # The uninstall registry entry records the real InstallLocation, which may be
    # something like D:\新建文件夹\GPPW2.
    try:
        import winreg  # type: ignore

        keys = (
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
        )
        for root, path in keys:
            try:
                with winreg.OpenKey(root, path) as parent:
                    for index in range(winreg.QueryInfoKey(parent)[0]):
                        try:
                            with winreg.OpenKey(parent, winreg.EnumKey(parent, index)) as entry:
                                display = str(winreg.QueryValueEx(entry, "DisplayName")[0])
                                if "GX Works2" not in display and "MELSOFT" not in display:
                                    continue
                                location = str(winreg.QueryValueEx(entry, "InstallLocation")[0])
                                if location:
                                    candidates.append(os.path.join(location, "GPPW2"))
                                    candidates.append(location)
                        except OSError:
                            continue
            except OSError:
                continue
    except Exception:  # pragma: no cover - winreg is Windows only
        pass

    for directory in candidates:
        if not os.path.isdir(directory):
            continue
        for name in _EXE_NAMES:
            exe = os.path.join(directory, name)
            if os.path.isfile(exe):
                return {"found": True, "directory": directory, "executable": exe}
    return {"found": False, "searched": candidates}


def gxworks2_running() -> dict[str, object]:
    """Report whether GPPW2 / GX Works2 processes exist, without touching windows."""

    outputs: list[str] = []
    running = False
    for name in _EXE_NAMES:
        try:
            completed = subprocess.run(
                ["tasklist", "/FI", "IMAGENAME eq " + name, "/NH"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
        except OSError as exc:  # pragma: no cover
            return {"available": False, "error": str(exc)}
        text = completed.stdout or ""
        outputs.append(text.strip())
        if name.lower() in text.lower():
            running = True
    return {"available": True, "running": running, "raw": " | ".join(part for part in outputs if part)}


def connect(title_hint: str | None = None) -> Any:
    """Attach to the running GX Works2 main window (pywinauto required)."""

    _require("pywinauto", "pywinauto drives the GX Works2 windows")
    from pywinauto import Desktop  # type: ignore
    from pywinauto.application import Application  # type: ignore

    hints = [title_hint] if title_hint else list(WINDOW_HINTS)
    windows = Desktop(backend="win32").windows()
    for window in windows:
        try:
            title = window.window_text() or ""
        except Exception:  # pragma: no cover - window vanished mid-enumeration
            continue
        lowered = title.lower()
        if any(hint and hint.lower() in lowered for hint in hints):
            handle = window.handle
            app = Application(backend="win32").connect(handle=handle)
            return app.window(handle=handle)
    raise AutomationError(
        "no GX Works2 window found; open GX Works2 with a ladder program first "
        "(searched titles containing: " + ", ".join(hints) + ")"
    )


def list_windows() -> list[Window]:
    """List top-level windows whose title looks like MELSOFT software."""

    _require("pywinauto", "pywinauto drives the GX Works2 windows")
    from pywinauto import Desktop  # type: ignore

    found: list[Window] = []
    for window in Desktop(backend="win32").windows():
        try:
            title = window.window_text() or ""
        except Exception:  # pragma: no cover
            continue
        if any(hint in title.lower() for hint in WINDOW_HINTS):
            found.append(Window(title=title, handle=int(window.handle)))
    return found


def _clipboard_set_text(text: str) -> None:
    """Put text on the Windows clipboard (pyperclip preferred, tkinter fallback)."""

    try:
        import pyperclip  # type: ignore

        pyperclip.copy(text)
        return
    except Exception:
        pass
    import tkinter  # type: ignore

    root = tkinter.Tk()
    root.withdraw()
    root.clipboard_clear()
    root.clipboard_append(text)
    root.update()
    root.destroy()


def _clipboard_get_text() -> str:
    try:
        import pyperclip  # type: ignore

        return str(pyperclip.paste())
    except Exception:
        pass
    import tkinter  # type: ignore

    root = tkinter.Tk()
    root.withdraw()
    value = root.clipboard_get()
    root.update()
    root.destroy()
    return str(value)


@dataclass
class PasteResult:
    ok: bool
    window: str
    instructions: int
    method: str
    notes: list[str]

    def as_dict(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "window": self.window,
            "instructions": self.instructions,
            "method": self.method,
            "notes": self.notes,
        }


def paste_il(
    il_text: str,
    *,
    title_hint: str | None = None,
    delay: float = 1.0,
    confirm: bool = False,
) -> PasteResult:
    """Paste an IL listing into the focused GX Works2 ladder editor.

    confirm=True is required: this is a real edit in a real project window, and the
    ladder that GX Works2 builds cannot be undone by this server.
    """

    if not confirm:
        raise AutomationError(
            "refusing to type into GX Works2 without confirm=true; this pastes IL into the "
            "active ladder window and replaces the current ladder"
        )
    window = connect(title_hint)
    notes: list[str] = []
    try:
        window.set_focus()
    except Exception as exc:  # pragma: no cover
        notes.append("could not focus the window: " + str(exc))
    _clipboard_set_text(il_text)
    time.sleep(0.2)
    try:
        from pywinauto.keyboard import send_keys  # type: ignore

        send_keys("^v")
    except Exception as exc:  # pragma: no cover
        raise AutomationError("could not send Ctrl+V to GX Works2: " + str(exc)) from exc
    time.sleep(max(0.2, delay))
    count = len([line for line in il_text.splitlines() if line.strip()])
    notes.append(
        "GX Works2 rebuilds the ladder from the pasted list; if the editor ignored the paste, "
        "click inside the ladder window first and use Edit > Paste again"
    )
    return PasteResult(ok=True, window=window.window_text(), instructions=count, method="clipboard-ctrl-v", notes=notes)


def save_project(*, title_hint: str | None = None, delay: float = 1.5) -> dict[str, object]:
    """Send Ctrl+S to the GX Works2 main window and wait for the save to settle."""

    window = connect(title_hint)
    window.set_focus()
    from pywinauto.keyboard import send_keys  # type: ignore

    send_keys("^s")
    time.sleep(max(0.2, delay))
    return {"ok": True, "window": window.window_text(), "action": "ctrl+s", "note": "check the title bar for unsaved changes"}


def open_project(path: str, *, delay: float = 1.0) -> dict[str, object]:
    """Ask the running GX Works2 instance to open a project file via its window title menu."""

    target = Path(path).expanduser()
    if not target.exists():
        raise AutomationError("project file does not exist: " + str(target))
    window = connect()
    window.set_focus()
    from pywinauto.keyboard import send_keys  # type: ignore

    # Ctrl+O opens the file dialog; typing the path then Enter is the least fragile route.
    send_keys("^o")
    time.sleep(max(0.3, delay))
    send_keys(str(target))
    send_keys("{ENTER}")
    time.sleep(max(0.3, delay))
    return {"ok": True, "requested": str(target)}


def read_status(*, title_hint: str | None = None) -> dict[str, object]:
    """Return what the connected GX Works2 window reports (title and visible text)."""

    window = connect(title_hint)
    info: dict[str, object] = {
        "title": window.window_text(),
        "handle": int(window.handle),
    }
    try:
        info["texts"] = [
            child.window_text()
            for child in window.descendants(control_type="Text")[:40]
            if child.window_text()
        ]
    except Exception:  # pragma: no cover - window implementation specific
        info["texts"] = []
    return info



# --- UIA driving (verified seam: menus respond to expand/invoke without focus) ---

def connect_uia(title_hint: str | None = None, process: int | None = None) -> Any:
    """Attach to the GX Works2 main window with the UIA backend.

    UIA (not win32) is required because GX Works2's menus are owner-drawn: there is no
    HMENU, so WM_COMMAND injection fails, while every menu item is exposed as a UIA
    MenuItem control that supports ExpandCollapse and Invoke patterns.
    """

    _require("pywinauto", "pywinauto drives the GX Works2 windows")
    from pywinauto import Application  # type: ignore

    app = Application(backend="uia")
    if process is not None:
        return app.connect(process=process)
    hints = [title_hint] if title_hint else list(WINDOW_HINTS)
    from pywinauto import Desktop  # type: ignore

    for window in Desktop(backend="uia").windows():
        try:
            title = window.window_text() or ""
        except Exception:  # pragma: no cover
            continue
        if any(hint and hint.lower() in title.lower() for hint in hints):
            return app.connect(handle=window.handle)
    raise AutomationError(
        "no GX Works2 window found via UIA; start GX Works2 and open the ladder program first"
    )


def close_stray_popups(target: Any, frame: Any) -> int:
    """Close leftover combo-box popups (class ComboLBox) that hijack top_window().

    GX Works2 file dialogs contain searchable-combo popups; if a previous run was
    interrupted one stays visible as a top-level child of the process, its title
    (查找范围(I):) becomes what top_window() reports, and every menu invoke then
    fails. They take no UIA value pattern, so post WM_CLOSE to the window handle.
    """

    closed = 0
    try:
        import ctypes  # noqa: WPS433

        user32 = ctypes.windll.user32  # type: ignore[attr-defined]
        WM_CLOSE = 0x0010
        for window in target.windows():
            try:
                cls = window.class_name() or ""
            except Exception:  # pragma: no cover
                continue
            if cls in {"ComboLBox"} or (window.handle != frame.handle and "查找范围" in (window.window_text() or "")):
                try:
                    user32.PostMessageW(int(window.handle), WM_CLOSE, 0, 0)
                    closed += 1
                except Exception:  # pragma: no cover
                    continue
    except Exception:  # pragma: no cover - non-Windows
        return 0
    if closed:
        time.sleep(0.4)
    return closed

def dismiss_modals(
    target: Any,
    frame: Any,
    *,
    main_handle: int | None = None,
    pick: tuple[str, ...] = ("确定", "OK", "是", "Yes"),
    rounds: int = 6,
) -> list[str]:
    """Close blocking confirmation dialogs, wherever GX Works2 hosts them.

    GX Works2 shows some confirmations (the authentication notice, safety prompts) as
    panels EMBEDDED in the main frame instead of top-level #32770 windows. While such
    a modal is up, the frame reports is_enabled() False and menu invokes fail silently,
    so a dismissal pass that only looks at top-level windows never sees it. This walks
    the frame tree first, then any real dialogs, and clicks the first matching button.
    """

    clicked: list[str] = []
    for _ in range(rounds):
        hit = False
        scopes = [frame, *_dialog_windows(target, main_handle)]
        for scope in scopes:
            try:
                buttons = scope.descendants(control_type="Button")
            except Exception:  # pragma: no cover
                continue
            for button in buttons:
                try:
                    caption = (button.window_text() or "").strip()
                except Exception:  # pragma: no cover
                    continue
                if not any(caption.startswith(token) for token in pick):
                    continue
                for action in ("invoke", "click"):
                    try:
                        getattr(button, action)()
                        clicked.append(caption)
                        hit = True
                        break
                    except Exception:
                        continue
                if hit:
                    break
            if hit:
                break
        if not hit:
            break
        time.sleep(0.4)
    return clicked

def find_menu_item(frame: Any, *texts: str) -> Any:
    """Return the first MenuItem whose title contains any of the given substrings."""

    lowered = [text.lower() for text in texts]
    for item in frame.descendants(control_type="MenuItem"):
        try:
            title = (item.window_text() or "").lower()
        except Exception:  # pragma: no cover
            continue
        if any(token in title for token in lowered):
            return item
    raise AutomationError("menu item not found (searched: " + ", ".join(texts) + ")")


def _dialog_windows(target: Any, main_handle: int | None = None) -> list[Any]:
    """Visible top-level DIALOG windows of the attached app.

    Filters by class: the standard Win32 dialog class is #32770, and the common item
    picker / file dialogs use it too. Embedded controls (Afx*, XTP...) are NOT dialogs
    even when they are top-level children of the frame, and must be skipped - they
    have no Value-pattern edits and would hang the search otherwise.
    """

    found: list[Any] = []
    for window in target.windows():
        try:
            if main_handle is not None and window.handle == main_handle:
                continue
            if not window.is_visible():
                continue
            cls = window.class_name() or ""
        except Exception:  # pragma: no cover - window vanished
            continue
        if cls.startswith("Afx") or cls.startswith("XTP") or cls.startswith("T"):
            continue
        title = window.window_text() or ""
        if cls != "#32770" and not title:
            # unnamed non-dialog window; only keep it if it carries dialog buttons
            try:
                if not window.descendants(control_type="Button"):
                    continue
            except Exception:  # pragma: no cover
                continue
        found.append(window)
    return found


def _click_dialog_button(
    target: Any,
    titles: tuple[str, ...],
    *,
    main_handle: int | None = None,
    timeout: float = 6.0,
) -> bool:
    """Invoke a dialog button by caption (UIA Invoke, then click fallback). No mouse."""

    deadline = time.time() + timeout
    while time.time() < deadline:
        for window in _dialog_windows(target, main_handle):
            try:
                buttons = window.descendants(control_type="Button")
            except Exception:  # pragma: no cover
                continue
            for button in buttons:
                try:
                    caption = (button.window_text() or "").strip()
                except Exception:  # pragma: no cover
                    continue
                if any(caption == title or caption.startswith(title) for title in titles):
                    for action in ("invoke", "click"):
                        try:
                            getattr(button, action)()
                            return True
                        except Exception:
                            continue
        time.sleep(0.25)
    return False


def _accept_dialog_button(scope: Any) -> Any | None:
    """Find the accept button (打开/Open/保存/Save) under a dialog scope."""

    try:
        buttons = scope.descendants(control_type="Button")
    except Exception:  # pragma: no cover
        return None
    for button in buttons:
        try:
            caption = (button.window_text() or "").strip().lower()
        except Exception:  # pragma: no cover
            continue
        if any(token in caption for token in ("打开", "open", "保存", "save")):
            return button
    return None


def _file_dialog_windows(target: Any, frame: Any, main_handle: int | None) -> list[Any]:
    """Windows hosting the file picker.

    GX Works2 presents the CSV dialog as a CHILD Window of its frame (title like
    从CSV文件读取), not a top-level app window, so scan: (1) frame descendants of
    control_type Window whose name looks like a file dialog, (2) top-level dialogs,
    (3) the frame itself as the last resort.
    """

    scopes: list[Any] = []
    try:
        for w in frame.descendants(control_type="Window"):
            try:
                name = w.window_text() or ""
            except Exception:  # pragma: no cover
                continue
            if any(key in name for key in ("CSV", "打开", "保存", "读取", "写入", "文件")):
                scopes.append(w)
    except Exception:  # pragma: no cover
        pass
    scopes.extend(_dialog_windows(target, main_handle))
    scopes.append(frame)
    return scopes


def _set_file_dialog_path(
    target: Any,
    path: str,
    *,
    main_handle: int | None = None,
    frame: Any | None = None,
    timeout: float = 12.0,
) -> None:
    """Fill the file dialog name box and accept it, without mouse or foreground focus.

    The name box is the Edit with UIA AutomationId 1148 (File name); sidebar
    pseudo-Edits (name '1', folder labels) reject the Value pattern and must be
    skipped. Acceptance presses the dialog 打开/Open button via Invoke.
    """

    if frame is None:
        raise AutomationError("_set_file_dialog_path needs the main frame")
    deadline = time.time() + timeout
    last_error: Exception | None = None
    while time.time() < deadline:
        for scope in _file_dialog_windows(target, frame, main_handle):
            try:
                edits = scope.descendants(control_type="Edit")
            except Exception:  # pragma: no cover
                continue

            def rank(edit: Any) -> int:
                try:
                    auto = str(edit.element_info.automation_id or "")
                    name = (edit.element_info.name or "").lower()
                except Exception:  # pragma: no cover
                    return 9
                if auto in {"1148", "1001"}:
                    return 0
                if "文件名" in name or "file name" in name or "filename" in name:
                    return 1
                return 5

            for edit in sorted(edits, key=rank):
                if rank(edit) >= 5:
                    continue  # never type into sidebar junk
                try:
                    edit.set_edit_text(path)
                except Exception as exc:
                    last_error = exc
                    continue
                accept = _accept_dialog_button(scope)
                if accept is None:
                    accept = _accept_dialog_button(frame)
                if accept is not None:
                    for action in ("invoke", "click"):
                        try:
                            getattr(accept, action)()
                            return
                        except Exception:
                            continue
                from pywinauto.keyboard import send_keys  # type: ignore

                send_keys("{ENTER}")
                return
        time.sleep(0.3)
    raise AutomationError(
        "no usable file dialog appeared within " + str(timeout) + "s"
        + ((" (last error: " + str(last_error) + ")") if last_error else "")
    )

def finish_file_dialog_win32(target, frame, path):
    """Drive GX Works2's file dialog via raw Win32, PID-scoped desktop scan.

    The dialog is a #32770 top-level window owned by the GD2 process, NOT a child
    of the main frame, and clients like pywinauto may hide it from window() lists
    (owned dialogs). So: EnumWindows over the whole desktop, keep windows whose
    PID matches the frame's, look for the file-name Edit (control id 1148, class
    Edit), WM_SETTEXT the path, then BM_CLICK the dialog's id-1 push button.
    """

    import ctypes

    if not hasattr(ctypes, "windll"):  # non-Windows: caller falls back to UIA
        return {"ok": False, "why": "no win32 on this platform"}

    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    WM_SETTEXT = 0x000C
    BM_CLICK = 0x00F5
    ID_FILE_NAME = 1148
    ID_OPEN = 1

    def pid_of(hwnd: int) -> int:
        proc_id = ctypes.c_ulong(0)
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(proc_id))
        return int(proc_id.value)

    try:
        frame_pid = pid_of(int(frame.handle))
    except Exception:
        return {"ok": False, "why": "frame handle gone"}

    def find_edit_in(root_hwnd: int) -> int | None:
        found = {"edit": 0, "parent": 0}

        @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_ssize_t)
        def cb(hwnd, lparam):
            try:
                if user32.GetDlgCtrlID(hwnd) != ID_FILE_NAME:
                    return True
                buf = ctypes.create_unicode_buffer(64)
                user32.GetClassNameW(hwnd, buf, 64)
                if buf.value != "Edit":
                    return True
                if not user32.IsWindowVisible(hwnd):
                    return True
                found["edit"] = int(hwnd or 0)
                found["parent"] = int(user32.GetParent(int(hwnd or 0)) or 0)
                return False
            except Exception:
                return True

        user32.EnumChildWindows(root_hwnd, cb, 0)
        if found["edit"]:
            return found["edit"]
        return None

    dialogs: list[int] = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_ssize_t)
    def enum_top(hwnd, lparam):
        try:
            if pid_of(int(hwnd or 0)) != frame_pid:
                return True
            if not user32.IsWindowVisible(hwnd):
                return True
            buf = ctypes.create_unicode_buffer(64)
            user32.GetClassNameW(hwnd, buf, 64)
            if buf.value == "#32770":
                dialogs.append(int(hwnd or 0))
        except Exception:
            pass
        return True

    user32.EnumWindows(enum_top, 0)
    edit = None
    owner = None
    for hwnd in dialogs:
        edit = find_edit_in(hwnd)
        if edit:
            owner = hwnd
            break
    if not edit:
        # last resort: the frame subtree (embedded dialog variants)
        edit = find_edit_in(int(frame.handle))
        if not edit:
            for window in target.windows():
                try:
                    edit = find_edit_in(int(window.handle))
                except Exception:
                    continue
                if edit:
                    break
    if not edit:
        return {"ok": False, "why": "no visible 1148 Edit", "dialogs": len(dialogs)}

    user32.SendMessageW(edit, WM_SETTEXT, 0, path)

    dialog = owner or int(user32.GetParent(edit) or 0) or int(frame.handle)
    ok_button = {"hwnd": 0}

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_ssize_t)
    def find_ok(hwnd, lparam):
        try:
            if user32.GetDlgCtrlID(hwnd) != ID_OPEN:
                return True
            buf = ctypes.create_unicode_buffer(64)
            user32.GetClassNameW(hwnd, buf, 64)
            if buf.value != "Button" or not user32.IsWindowVisible(hwnd):
                return True
            ok_button["hwnd"] = int(hwnd or 0)
            return False
        except Exception:
            return True

    if owner:
        user32.EnumChildWindows(owner, find_ok, 0)
    if ok_button["hwnd"]:
        user32.SendMessageW(ok_button["hwnd"], BM_CLICK, 0, 0)
        return {"ok": True, "method": "win32-pid-dialog+bm_click", "dialog": hex(owner or 0)}
    return {"ok": True, "method": "win32-settext-only",
            "note": "path typed into 1148; Open button not found"}


def import_program_csv(
    csv_path: str,
    *,
    title_hint: str | None = None,
    process: int | None = None,
    wait_seconds: float = 2.5,
    confirm: bool = False,
) -> dict[str, object]:
    """Drive GX Works2's verified import: Edit > Read from CSV file > confirm.

    The import replaces the whole current ladder program (the dialog says so: the
    current editor content is discarded). That is why confirm=True is mandatory,
    and why the tool reports the step count before and after so the caller can
    see the replacement actually took effect. Verified on a Chinese 1.620W.

    Menu Expand/Invoke on the DevExpress menu bar can hit a one-off COM disconnect
    while the app is settling, so both the menu open and the dialog fill retry.
    """

    if not confirm:
        raise AutomationError(
            "import replaces the program in the open ladder editor; pass confirm=true"
        )
    target_csv = Path(csv_path).expanduser()
    if not target_csv.exists():
        raise AutomationError("CSV file does not exist: " + str(target_csv))

    connection = connect_uia(title_hint, process)
    close_stray_popups(connection, connection.top_window())
    frame = connection.top_window()
    main_handle = frame.handle
    # a just-loaded project can leave the frame disabled behind an embedded
    # notification; wait until it is enabled before touching menus
    settle = time.time() + 15.0
    while time.time() < settle:
        try:
            if frame.is_enabled():
                break
        except Exception:  # pragma: no cover
            break
        dismiss_modals(connection, frame, main_handle=main_handle)
        time.sleep(0.5)
    dismiss_modals(connection, frame, main_handle=main_handle)
    before_title = frame.window_text()
    if "[PRG]" not in before_title and "MAIN" not in before_title:
        raise AutomationError(
            "the GX Works2 window has no open ladder program (title: " + repr(before_title) + "); "
            "open the project first - use gx_open_project with the .gxw path"
        )

    last_error: Exception | None = None
    opened = False
    for _attempt in range(5):
        try:
            edit_menu = find_menu_item(frame, "编辑(E)", "编辑", "edit")
            edit_menu.expand()
        except Exception as exc:
            last_error = exc
            time.sleep(0.7)
            continue
        leaf = None
        leaf_deadline = time.time() + 4.0
        while time.time() < leaf_deadline:
            try:
                leaf = find_menu_item(frame, "从CSV文件读取", "read from csv")
            except AutomationError:
                time.sleep(0.4)
                continue
            break
        if leaf is None:
            last_error = AutomationError("CSV read menu item not present after expanding 编辑")
            continue
        try:
            leaf.invoke()
            opened = True
            break
        except Exception as exc:
            last_error = exc
            time.sleep(0.7)
    if not opened:
        raise AutomationError(
            "could not open Edit > Read from CSV file after retries: " + str(last_error)
        )

    # fill and accept the classic file dialog with raw Win32 (poll while it opens);
    # the UIA path is a last resort for hosts without a classic dialog
    dialog_result: dict[str, object] = {"ok": False, "why": "not attempted"}
    fill_deadline = time.time() + 10.0
    while time.time() < fill_deadline:
        dialog_result = finish_file_dialog_win32(connection, frame, str(target_csv))
        if dialog_result.get("ok"):
            break
        time.sleep(0.5)
    if not dialog_result.get("ok"):
        try:
            _set_file_dialog_path(connection, str(target_csv), main_handle=main_handle, frame=frame)
            dialog_result = {"ok": True, "method": "uia-fallback"}
        except AutomationError:
            pass

    # confirmation panel: "读取指定的文件内容。确定吗？" -> 是(Y). It is an EMBEDDED
    # panel inside the frame (dismiss_modals searches the frame tree first).
    time.sleep(0.8)
    confirmations = dismiss_modals(
        connection, frame, main_handle=main_handle, pick=("是", "Yes", "确定", "OK"), rounds=4
    )
    clicked = bool(confirmations)

    time.sleep(max(0.5, wait_seconds))
    after_title = frame.window_text()

    def parse_steps(text: str) -> int | None:
        import re as _re

        match = _re.search(r"(\d+)\s*步|(\d+)\s*step", text, _re.IGNORECASE)
        if not match:
            return None
        return int(match.group(1) or match.group(2))

    return {
        # Legacy heuristic only: unchanged step counts can still mean a correct
        # replacement, and changed counts do not establish content equality.
        "ok": parse_steps(before_title) != parse_steps(after_title),
        "content_verified": False,
        "verification_required": "gx_verify_import",
        "title_before": before_title,
        "title_after": after_title,
        "steps_before": parse_steps(before_title),
        "steps_after": parse_steps(after_title),
        "confirmed_dialog": clicked,
        "confirmations": confirmations,
        "csv": str(target_csv),
        "dialog": dialog_result,
        "next": "run Transform/Compile (F4) then save; afterwards verify devices with plc_read",
    }


def compile_program(*, title_hint: str | None = None, process: int | None = None) -> dict[str, object]:
    """Trigger the ladder transform (F4) through the menu, not keyboard focus.

    F4 is the standard accelerator for 转换/编译; UIA invoke on the menu entry avoids
    needing foreground focus.
    """

    connection = connect_uia(title_hint, process)
    close_stray_popups(connection, connection.top_window())
    frame = connection.top_window()
    dismiss_modals(connection, frame, main_handle=frame.handle)
    try:
        # 转换/编译(C) is a top menu; its first leaf is 转换(B) - the F4 command.
        top = find_menu_item(frame, "转换/编译", "transform/compile", "build")
        top.expand()
        time.sleep(0.3)
        leaf = None
        for item in frame.descendants(control_type="MenuItem"):
            try:
                title = (item.window_text() or "").strip()
            except Exception:  # pragma: no cover
                continue
            if title.startswith("转换") or title.lower().startswith("transform"):
                leaf = item
                break
        if leaf is not None:
            leaf.invoke()
            method = "menu-leaf-invoke"
        else:
            top.invoke()
            method = "menu-top-invoke"
    except AutomationError:
        from pywinauto.keyboard import send_keys  # type: ignore

        send_keys("{F4}")
        method = "f4-accelerator"
    time.sleep(1.2)
    return {"ok": True, "method": method, "title": frame.window_text()}


def export_program_csv(
    csv_path: str,
    *,
    title_hint: str | None = None,
    process: int | None = None,
) -> dict[str, object]:
    """Drive Edit > Write to CSV file to export the current ladder as a program CSV."""

    target_csv = Path(csv_path).expanduser()
    connection = connect_uia(title_hint, process)
    close_stray_popups(connection, connection.top_window())
    frame = connection.top_window()
    main_handle = frame.handle
    dismiss_modals(connection, frame, main_handle=main_handle)
    try:
        edit_menu = find_menu_item(frame, "编辑", "edit")
        edit_menu.expand()
        time.sleep(0.4)
    except AutomationError:
        pass
    write_menu = find_menu_item(frame, "写入至CSV文件", "write to csv")
    write_menu.invoke()
    time.sleep(0.6)
    _set_file_dialog_path(connection, str(target_csv), main_handle=main_handle, frame=frame)
    _click_dialog_button(connection, ("是", "Yes", "OK", "确定"), main_handle=main_handle)
    dismiss_modals(connection, frame, main_handle=main_handle)
    time.sleep(1.2)
    return {"ok": target_csv.exists(), "csv": str(target_csv)}


def available() -> dict[str, object]:
    """Capability probe used by the MCP tool: is the UI automation path usable here?"""

    report: dict[str, object] = {"platform": "windows"}
    try:
        _require("pywinauto", "pywinauto drives the GX Works2 windows")
        report["pywinauto"] = True
    except AutomationError as exc:
        report["pywinauto"] = False
        report["pywinauto_error"] = str(exc)
    try:
        import pyperclip  # type: ignore  # noqa: F401

        report["clipboard"] = "pyperclip"
    except Exception:
        report["clipboard"] = "tkinter"
    report["verified_route"] = "UIA menu invoke: Edit > 从CSV文件读取 (import_program_csv)"
    report.update(gxworks2_running())
    try:
        report["install"] = find_install()
    except Exception as exc:  # pragma: no cover - registry access is best effort
        report["install"] = {"found": False, "error": str(exc)}
    try:
        report["windows"] = [window.title for window in list_windows()]
    except Exception as exc:
        report["windows"] = []
        report["window_error"] = str(exc)
    return report
