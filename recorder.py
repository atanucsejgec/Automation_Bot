"""
recorder.py — Low-level input recorder using pynput.

Captures mouse clicks (position, button, pressed/released), keyboard presses,
and scroll events with sub-millisecond relative timestamps. All events are
stored as a JSON-serializable list of dicts.

Usage (standalone):
    python recorder.py              # records until you press the stop hotkey
    python recorder.py my_attack    # saves to recordings/my_attack.json

Within the bot:
    from recorder import ActionRecorder
    rec = ActionRecorder(config)
    rec.start()   # non-blocking, returns immediately
    rec.stop()    # stops & returns the action list
    rec.save("my_attack")
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
# Helpers
# ---------------------------------------------------------------------------

def _key_to_str(key):
    """Convert a pynput key object to a JSON-safe string."""
    if isinstance(key, KeyCode):
        # Regular character key
        return key.char if key.char else f"<{key.vk}>"
    elif isinstance(key, Key):
        # Special key (shift, ctrl, etc.)
        return key.name
    return str(key)


def _str_to_key(s):
    """Reconstruct a pynput key from its string representation."""
    # Try special key names first
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
        if not self._recording:
            return
        self._append({
            "type": "scroll",
            "time": self._elapsed(),
            "x": x,
            "y": y,
            "dx": dx,
            "dy": dy,
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
            print(f"\n[recorder] Stop hotkey ({self.config.get('recording_hotkey_stop', 'F7')}) detected — stopping recording.")
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

        self._mouse_listener = mouse.Listener(
            on_click=self._on_click,
            on_scroll=self._on_scroll,
            on_move=self._on_move,
        )
        self._kb_listener = keyboard.Listener(
            on_press=self._on_press,
            on_release=self._on_release,
        )

        self._mouse_listener.start()
        self._kb_listener.start()

        stop_key = self.config.get("recording_hotkey_stop", "F7")
        print(f"[recorder] 🔴 Recording started.  Press {stop_key} to stop.")
        play_announcement_sync("recording_started", self.config.get("sound_enabled", True))

    def wait(self):
        """Block until the stop hotkey is pressed."""
        self._stop_event.wait()

    def stop(self) -> list[dict]:
        """Stop recording and return the captured action list."""
        with self._lock:
            self._recording = False

        # Gracefully shut down listeners
        if self._mouse_listener:
            self._mouse_listener.stop()
        if self._kb_listener:
            self._kb_listener.stop()

        total = len(self.actions)
        duration = self.actions[-1]["time"] if self.actions else 0
        print(f"[recorder] ⏹  Recording stopped.  {total} events over {duration:.2f}s")
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
        print(f"[recorder] 💾 Saved {len(self.actions)} actions → {filepath}")
        return filepath

    @staticmethod
    def load(filepath: str) -> list[dict]:
        """Load a previously saved recording and return the action list."""
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
        actions = data.get("actions", data)  # support raw list or wrapped format
        print(f"[recorder] 📂 Loaded {len(actions)} actions from {filepath}")
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
