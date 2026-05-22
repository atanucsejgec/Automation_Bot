"""
recorder.py — Low-level input recorder using pynput.

Captures mouse clicks (position, button, pressed/released), keyboard presses,
and scroll events with sub-millisecond relative timestamps. All events are
stored as a JSON-serializable list of dicts.

Usage (standalone):
    python recorder.py              # records until you press the stop hotkey
    python recorder.py my_workflow   # saves to recordings/my_workflow.json

Within the app:
    from recorder import ActionRecorder
    rec = ActionRecorder(config)
    rec.start()   # non-blocking, returns immediately
    rec.stop()    # stops & returns the action list
    rec.save("my_workflow")
"""

import json
import os
import sys
import time
import threading
from datetime import datetime

from pynput import mouse, keyboard
from pynput.keyboard import Key, KeyCode
from sounds import play_announcement, play_announcement_sync


# ---------------------------------------------------------------------------
# Key name mapping:  pynput Key.name  →  pyautogui key string
# ---------------------------------------------------------------------------

PYNPUT_TO_PYAUTOGUI = {
    # Modifier keys
    "ctrl_l":       "ctrlleft",
    "ctrl_r":       "ctrlright",
    "shift":        "shiftleft",
    "shift_r":      "shiftright",
    "alt_l":        "altleft",
    "alt_r":        "altright",
    "alt_gr":       "altright",
    "cmd":          "winleft",
    "cmd_r":        "winright",

    # Navigation
    "backspace":    "backspace",
    "enter":        "enter",
    "tab":          "tab",
    "space":        "space",
    "delete":       "delete",
    "home":         "home",
    "end":          "end",
    "page_up":      "pageup",
    "page_down":    "pagedown",
    "insert":       "insert",
    "up":           "up",
    "down":         "down",
    "left":         "left",
    "right":        "right",

    # Lock & toggle keys
    "caps_lock":    "capslock",
    "num_lock":     "numlock",
    "scroll_lock":  "scrolllock",

    # System keys
    "esc":          "escape",
    "print_screen": "printscreen",
    "pause":        "pause",
    "menu":         "apps",

    # Function keys
    "f1": "f1",   "f2": "f2",   "f3": "f3",   "f4": "f4",
    "f5": "f5",   "f6": "f6",   "f7": "f7",   "f8": "f8",
    "f9": "f9",   "f10": "f10", "f11": "f11", "f12": "f12",
    "f13": "f13", "f14": "f14", "f15": "f15", "f16": "f16",
    "f17": "f17", "f18": "f18", "f19": "f19", "f20": "f20",
}

# Reverse mapping for loading saved recordings (pyautogui → pynput)
PYAUTOGUI_TO_PYNPUT = {v: k for k, v in PYNPUT_TO_PYAUTOGUI.items()}


def _key_to_str(key):
    """
    Convert a pynput key object to a pyautogui-compatible string.

    Handles:
      - Special keys (Ctrl, Shift, Alt, Backspace, etc.) via lookup table
      - Regular printable characters (a-z, 0-9, symbols)
      - Control characters (e.g. Ctrl+A gives char='\\x01') → resolves via vk code
      - Unknown keys → stored as <vk_code> for later resolution
    """
    if isinstance(key, Key):
        # Special key — map to pyautogui name
        return PYNPUT_TO_PYAUTOGUI.get(key.name, key.name)

    elif isinstance(key, KeyCode):
        # Regular key — check if char is a normal printable character
        if key.char is not None and key.char.isprintable() and len(key.char) == 1:
            return key.char

        # Control character (Ctrl held) or char is None — resolve from vk code
        if key.vk is not None:
            # Letters:  vk 65 ('A') – 90 ('Z')  →  'a' – 'z'
            if 65 <= key.vk <= 90:
                return chr(key.vk).lower()
            # Digits:  vk 48 ('0') – 57 ('9')  →  '0' – '9'
            if 48 <= key.vk <= 57:
                return chr(key.vk)
            # Numpad:  vk 96 – 105  →  'num0' – 'num9'
            if 96 <= key.vk <= 105:
                return f"num{key.vk - 96}"
            # Numpad operators
            numpad_map = {106: "multiply", 107: "add", 109: "subtract",
                          110: "decimal", 111: "divide"}
            if key.vk in numpad_map:
                return numpad_map[key.vk]
            # OEM keys (semicolon, equals, comma, etc.) — fallback to vk code
            return f"<{key.vk}>"

        # Absolute fallback
        if key.char is not None:
            return key.char
        return "<unknown>"

    return str(key)


def _str_to_key(s):
    """Reconstruct a pynput key from its string representation."""
    # Check reverse mapping first (pyautogui name → pynput key)
    if s in PYAUTOGUI_TO_PYNPUT:
        try:
            return Key[PYAUTOGUI_TO_PYNPUT[s]]
        except (KeyError, ValueError):
            pass
    # Try pynput special key names directly
    try:
        return Key[s]
    except (KeyError, ValueError):
        pass
    # Single character → KeyCode
    if len(s) == 1:
        return KeyCode.from_char(s)
    # Virtual-key code like <65>
    if s.startswith("<") and s.endswith(">"):
        try:
            return KeyCode.from_vk(int(s[1:-1]))
        except ValueError:
            pass
    return s  # fallback: return raw string


# ---------------------------------------------------------------------------
# Raw scroll hook  (captures exact scroll deltas via Windows API)
# ---------------------------------------------------------------------------

class _RawScrollHook:
    """
    Low-level Windows mouse hook that captures raw scroll deltas.

    pynput normalizes scroll values by dividing by WHEEL_DELTA (120),
    which means trackpad scrolls (delta < 120) become 0 and are lost.
    This hook captures the exact delta from WM_MOUSEWHEEL / WM_MOUSEHWHEEL.
    """

    WM_MOUSEWHEEL  = 0x020A
    WM_MOUSEHWHEEL = 0x020E
    WM_QUIT        = 0x0012
    WH_MOUSE_LL    = 14

    def __init__(self, callback):
        """
        Parameters
        ----------
        callback : callable(x, y, raw_dx, raw_dy)
            Called for each scroll event with raw delta values.
            One physical mouse notch = ±120. Trackpads send smaller values.
        """
        self._callback = callback
        self._hook = None
        self._thread = None
        self._thread_id = None

    def start(self):
        """Install the hook in a background thread with its own message pump."""
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        """Unhook and stop the message pump."""
        import ctypes
        if self._thread_id:
            ctypes.windll.user32.PostThreadMessageW(
                self._thread_id, self.WM_QUIT, 0, 0
            )
        if self._thread:
            self._thread.join(timeout=2)
            self._thread = None
        self._thread_id = None

    def _run(self):
        import ctypes
        from ctypes import wintypes, WINFUNCTYPE

        self._thread_id = threading.current_thread().ident

        # Use a FRESH user32 handle — the shared ctypes.windll.user32
        # singleton has argtypes set by pynput, which rejects our HOOKPROC.
        user32 = ctypes.WinDLL('user32', use_last_error=True)

        class MSLLHOOKSTRUCT(ctypes.Structure):
            _fields_ = [
                ("pt",          wintypes.POINT),
                ("mouseData",   wintypes.DWORD),
                ("flags",       wintypes.DWORD),
                ("time",        wintypes.DWORD),
                ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
            ]

        HOOKPROC = WINFUNCTYPE(
            ctypes.c_long,            # return  (LRESULT)
            ctypes.c_int,             # nCode
            wintypes.WPARAM,          # wParam
            wintypes.LPARAM,          # lParam
        )

        # Explicitly declare argtypes for the functions we'll call
        user32.SetWindowsHookExW.argtypes = [
            ctypes.c_int,       # idHook
            HOOKPROC,           # lpfn
            wintypes.HINSTANCE, # hMod
            wintypes.DWORD,     # dwThreadId
        ]
        user32.SetWindowsHookExW.restype = ctypes.c_void_p

        user32.CallNextHookEx.argtypes = [
            ctypes.c_void_p,    # hhk
            ctypes.c_int,       # nCode
            wintypes.WPARAM,    # wParam
            wintypes.LPARAM,    # lParam
        ]
        user32.CallNextHookEx.restype = ctypes.c_long

        user32.UnhookWindowsHookEx.argtypes = [ctypes.c_void_p]
        user32.UnhookWindowsHookEx.restype = wintypes.BOOL

        def low_level_mouse_proc(nCode, wParam, lParam):
            if nCode >= 0 and wParam in (self.WM_MOUSEWHEEL, self.WM_MOUSEHWHEEL):
                data = ctypes.cast(
                    lParam, ctypes.POINTER(MSLLHOOKSTRUCT)
                ).contents

                # Extract signed delta from the high word of mouseData
                high_word = (data.mouseData >> 16) & 0xFFFF
                delta = ctypes.c_short(high_word).value  # unsigned → signed

                x, y = data.pt.x, data.pt.y
                if wParam == self.WM_MOUSEWHEEL:
                    self._callback(x, y, 0, delta)
                else:
                    self._callback(x, y, delta, 0)

            return user32.CallNextHookEx(self._hook, nCode, wParam, lParam)

        # prevent garbage collection of the callback
        self._proc = HOOKPROC(low_level_mouse_proc)

        self._hook = user32.SetWindowsHookExW(
            self.WH_MOUSE_LL, self._proc, None, 0
        )
        if not self._hook:
            return

        # message pump — keeps the hook alive
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

        user32.UnhookWindowsHookEx(self._hook)
        self._hook = None


# ---------------------------------------------------------------------------
# ActionRecorder
# ---------------------------------------------------------------------------

class ActionRecorder:
    """
    Records mouse and keyboard actions with timestamps relative to
    the first event.  Thread-safe; listeners run in daemon threads.
    """

    def __init__(self, config: dict):
        self.config = config
        self.actions: list[dict] = []
        self._recording = False
        self._start_time: float = 0.0
        self._lock = threading.Lock()

        self._mouse_listener = None
        self._kb_listener = None
        self._scroll_hook = None

        # Parse stop hotkey from config (e.g. "F7")
        stop_key_name = config.get("recording_hotkey_stop", "F7")
        try:
            self._stop_key = Key[stop_key_name.lower()]
        except (KeyError, AttributeError):
            self._stop_key = Key.f7

        self._stop_event = threading.Event()

    # ---- internal helpers -------------------------------------------------

    def _elapsed(self) -> float:
        """Seconds since recording started, rounded to 4 decimal places."""
        return round(time.perf_counter() - self._start_time, 4)

    def _append(self, event: dict):
        with self._lock:
            if self._recording:
                self.actions.append(event)

    # ---- mouse callbacks --------------------------------------------------

    def _on_click(self, x: int, y: int, button, pressed: bool):
        if not self._recording:
            return
        self._append({
            "type": "click",
            "time": self._elapsed(),
            "x": x,
            "y": y,
            "button": button.name,       # "left" | "right" | "middle"
            "pressed": pressed,           # True = down, False = up
        })

    def _on_scroll(self, x: int, y: int, dx: int, dy: int):
        """Fallback scroll handler (pynput). Only used if raw hook is unavailable."""
        if not self._recording:
            return
        self._append({
            "type": "scroll",
            "time": self._elapsed(),
            "x": x,
            "y": y,
            "dx": dx,
            "dy": dy,
            "raw_dx": dx * 120,
            "raw_dy": dy * 120,
        })

    def _on_raw_scroll(self, x: int, y: int, raw_dx: int, raw_dy: int):
        """
        Receives raw scroll deltas from _RawScrollHook (in WHEEL_DELTA units).
        One physical mouse notch = ±120. Trackpads send smaller increments.
        """
        if not self._recording:
            return
        self._append({
            "type": "scroll",
            "time": self._elapsed(),
            "x": x,
            "y": y,
            "dx": raw_dx // 120 if raw_dx else 0,  # backward-compat normalized
            "dy": raw_dy // 120 if raw_dy else 0,
            "raw_dx": raw_dx,
            "raw_dy": raw_dy,
        })

    def _on_move(self, x: int, y: int):
        """
        Mouse movement is recorded at a throttled rate to avoid
        flooding the action list (we keep ~every 50 ms worth of moves).
        """
        if not self._recording:
            return
        with self._lock:
            if self.actions and self.actions[-1]["type"] == "move":
                last = self.actions[-1]
                if self._elapsed() - last["time"] < 0.05:
                    return  # skip — too soon
        self._append({
            "type": "move",
            "time": self._elapsed(),
            "x": x,
            "y": y,
        })

    # ---- keyboard callbacks -----------------------------------------------

    def _on_press(self, key):
        if not self._recording:
            return
        # Check for stop hotkey
        if key == self._stop_key:
            print(f"\n[autopilot] Stop hotkey [{self.config.get('recording_hotkey_stop', 'F7')}] detected. Stopping recording.")
            self._stop_event.set()
            return False  # stop keyboard listener

        self._append({
            "type": "key_down",
            "time": self._elapsed(),
            "key": _key_to_str(key),
        })

    def _on_release(self, key):
        if not self._recording:
            return
        if key == self._stop_key:
            return  # ignore the stop key release
        self._append({
            "type": "key_up",
            "time": self._elapsed(),
            "key": _key_to_str(key),
        })

    # ---- public API -------------------------------------------------------

    def start(self):
        """Begin recording. Non-blocking — listeners run in background."""
        with self._lock:
            self.actions.clear()
            self._recording = True
            self._stop_event.clear()

        self._start_time = time.perf_counter()

        # Raw scroll hook (bypasses pynput's lossy normalization)
        self._scroll_hook = _RawScrollHook(self._on_raw_scroll)
        self._scroll_hook.start()

        # pynput mouse listener — clicks & moves only (scroll via raw hook)
        self._mouse_listener = mouse.Listener(
            on_click=self._on_click,
            on_move=self._on_move,
        )
        self._kb_listener = keyboard.Listener(
            on_press=self._on_press,
            on_release=self._on_release,
        )

        self._mouse_listener.start()
        self._kb_listener.start()

        stop_key = self.config.get("recording_hotkey_stop", "F7")
        print(f"[autopilot] Recording started. Press [{stop_key}] to stop.")
        play_announcement_sync("recording_started", self.config.get("sound_enabled", True))

    def wait(self):
        """Block until the stop hotkey is pressed."""
        self._stop_event.wait()

    def stop(self) -> list[dict]:
        """Stop recording and return the captured action list."""
        with self._lock:
            self._recording = False

        # Gracefully shut down listeners
        if self._scroll_hook:
            self._scroll_hook.stop()
        if self._mouse_listener:
            self._mouse_listener.stop()
        if self._kb_listener:
            self._kb_listener.stop()

        total = len(self.actions)
        duration = self.actions[-1]["time"] if self.actions else 0
        print(f"[autopilot] Recording stopped. {total} actions captured over {duration:.2f}s.")
        play_announcement("recording_stopped", self.config.get("sound_enabled", True))

        return self.actions

    def save(self, name: str, directory: str | None = None):
        """
        Persist the recorded actions to a JSON file.

        Parameters
        ----------
        name : str
            Base name for the file (without extension).
        directory : str, optional
            Override for the recordings folder path.
        """
        rec_dir = directory or self.config.get("recordings_dir", "recordings")
        os.makedirs(rec_dir, exist_ok=True)

        filepath = os.path.join(rec_dir, f"{name}.json")
        payload = {
            "name": name,
            "recorded_at": datetime.now().isoformat(),
            "event_count": len(self.actions),
            "duration_sec": self.actions[-1]["time"] if self.actions else 0,
            "actions": self.actions,
        }
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        print(f"[autopilot] Saved {len(self.actions)} actions >> {filepath}")
        return filepath

    @staticmethod
    def load(filepath: str) -> list[dict]:
        """Load a previously saved recording and return the action list."""
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
        actions = data.get("actions", data)  # support raw list or wrapped format
        print(f"[autopilot] Loaded {len(actions)} actions from {filepath}")
        return actions


# ---------------------------------------------------------------------------
# Standalone entry-point
# ---------------------------------------------------------------------------

def main():
    # Load config
    config_path = os.path.join(os.path.dirname(__file__), "config.json")
    if os.path.exists(config_path):
        with open(config_path, "r") as f:
            config = json.load(f)
    else:
        config = {}

    name = sys.argv[1] if len(sys.argv) > 1 else f"recording_{datetime.now():%Y%m%d_%H%M%S}"

    rec = ActionRecorder(config)
    rec.start()
    rec.wait()      # blocks until stop hotkey
    rec.stop()
    rec.save(name)


if __name__ == "__main__":
    main()
