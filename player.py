"""
player.py — Replays recorded action sequences using pyautogui.

Handles:
  • Precise timing between events (respects playback_speed multiplier)
  • Window focus verification before each loop
  • Coordinate offset correction if the game window has moved
  • Abort hotkey (F8 by default) to emergency-stop mid-replay
  • Loop count with configurable delay between iterations

Usage:
    from player import ActionPlayer
    player = ActionPlayer(config)
    player.play("recordings/my_attack.json", loops=5)
"""

import json
import os
import sys
import time
import threading

import pyautogui
from pynput import keyboard
from pynput.keyboard import Key

# Safety: pyautogui will raise an exception if the cursor hits a corner
pyautogui.FAILSAFE = True
pyautogui.PAUSE = 0.0  # we manage our own timing


# ---------------------------------------------------------------------------
# Window utilities  (win32 via ctypes — no extra dependencies)
# ---------------------------------------------------------------------------

def _find_window(title_fragment: str):
    """
    Find a top-level window whose title contains *title_fragment*.
    Tries the given fragment first, then falls back to common game
    window titles.  Returns (hwnd, rect) or (None, None).
    """
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    EnumWindows = user32.EnumWindows
    GetWindowTextW = user32.GetWindowTextW
    GetWindowTextLengthW = user32.GetWindowTextLengthW
    IsWindowVisible = user32.IsWindowVisible
    GetWindowRect = user32.GetWindowRect

    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

    # Collect all visible windows (for fallback search + diagnostics)
    all_windows: list[tuple[int, str, tuple]] = []

    def collect(hwnd, _):
        if not IsWindowVisible(hwnd):
            return True
        length = GetWindowTextLengthW(hwnd)
        if length == 0:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        GetWindowTextW(hwnd, buf, length + 1)
        rect = wintypes.RECT()
        GetWindowRect(hwnd, ctypes.byref(rect))
        all_windows.append((
            hwnd, buf.value,
            (rect.left, rect.top, rect.right, rect.bottom),
        ))
        return True

    EnumWindows(WNDENUMPROC(collect), 0)

    # Search order: user-configured title, then common fallbacks
    search_titles = [title_fragment]
    for fallback in ("Clash of Clans", "Google Play Games", "BlueStacks"):
        if fallback.lower() != title_fragment.lower():
            search_titles.append(fallback)

    for fragment in search_titles:
        frag_lower = fragment.lower()
        for hwnd, title, rect in all_windows:
            if frag_lower in title.lower():
                if fragment != title_fragment:
                    print(f"[player] ℹ️  Matched fallback title '{fragment}' "
                          f"→ \"{title}\"")
                return hwnd, rect

    # Nothing matched — print all windows to help the user fix config
    print("[player] 🔍 No matching window found. Visible windows:")
    for _, title, _ in all_windows:
        print(f"           • {title}")
    print(f"[player]    Update 'target_window_title' in config.json to match "
          f"one of the above.\n")

    return None, None


def _focus_window(hwnd):
    """Bring a window to the foreground without changing its size."""
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32

    # Check current window state via GetWindowPlacement
    class WINDOWPLACEMENT(ctypes.Structure):
        _fields_ = [
            ("length", ctypes.c_uint),
            ("flags", ctypes.c_uint),
            ("showCmd", ctypes.c_uint),
            ("ptMinPosition", wintypes.POINT),
            ("ptMaxPosition", wintypes.POINT),
            ("rcNormalPosition", wintypes.RECT),
        ]

    wp = WINDOWPLACEMENT()
    wp.length = ctypes.sizeof(WINDOWPLACEMENT)
    user32.GetWindowPlacement(hwnd, ctypes.byref(wp))

    SW_SHOWMINIMIZED = 2
    SW_SHOWMAXIMIZED = 3
    SW_SHOWNORMAL = 1
    SW_RESTORE = 9

    if wp.showCmd == SW_SHOWMINIMIZED:
        # Window is minimized — restore to its previous state
        user32.ShowWindow(hwnd, SW_RESTORE)
    elif wp.showCmd == SW_SHOWMAXIMIZED:
        # Window is maximized — keep it maximized
        user32.ShowWindow(hwnd, SW_SHOWMAXIMIZED)
    else:
        # Window is in normal state — just show it as-is
        user32.ShowWindow(hwnd, SW_SHOWNORMAL)

    user32.SetForegroundWindow(hwnd)
    time.sleep(0.3)


# ---------------------------------------------------------------------------
# ActionPlayer
# ---------------------------------------------------------------------------

class ActionPlayer:
    """Replays a recorded action sequence with timing fidelity."""

    def __init__(self, config: dict):
        self.config = config
        self._abort = threading.Event()
        self._abort_listener = None

        # Parse abort hotkey
        abort_key_name = config.get("abort_hotkey", "F8")
        try:
            self._abort_key = Key[abort_key_name.lower()]
        except (KeyError, AttributeError):
            self._abort_key = Key.f8

    # ---- abort hotkey listener --------------------------------------------

    def _start_abort_listener(self):
        """Start a background keyboard listener that watches for the abort key."""
        def on_press(key):
            if key == self._abort_key:
                print(f"\n[player] ⚠️  Abort hotkey pressed! Stopping replay...")
                self._abort.set()
                return False  # stop listener
        self._abort_listener = keyboard.Listener(on_press=on_press)
        self._abort_listener.daemon = True
        self._abort_listener.start()

    def _stop_abort_listener(self):
        if self._abort_listener:
            self._abort_listener.stop()
            self._abort_listener = None

    # ---- window management ------------------------------------------------

    def _ensure_window_focus(self) -> tuple[int, int] | None:
        """
        Find and focus the target window.  Returns the window's
        top-left (x, y) offset so we can correct coordinates, or
        None if the window wasn't found.
        """
        title = self.config.get("target_window_title", "Google Play Games")
        hwnd, rect = _find_window(title)
        if hwnd is None:
            print(f"[player] ❌ Window '{title}' not found. Is the game running?")
            return None
        _focus_window(hwnd)
        return (rect[0], rect[1])

    # ---- replay logic -----------------------------------------------------

    def _execute_action(self, action: dict, speed: float, offset: tuple[int, int]):
        """Execute a single recorded action."""
        ox, oy = offset
        atype = action["type"]
        move_dur = self.config.get("mouse_move_duration", 0.1) / speed

        if atype == "click":
            x, y = action["x"] + ox, action["y"] + oy
            btn = action.get("button", "left")
            pressed = action.get("pressed", True)
            # Move to position first
            pyautogui.moveTo(x, y, duration=move_dur)
            if pressed:
                pyautogui.mouseDown(x, y, button=btn)
            else:
                pyautogui.mouseUp(x, y, button=btn)

        elif atype == "move":
            x, y = action["x"] + ox, action["y"] + oy
            pyautogui.moveTo(x, y, duration=move_dur * 0.5)

        elif atype == "scroll":
            x, y = action["x"] + ox, action["y"] + oy
            pyautogui.moveTo(x, y, duration=move_dur * 0.3)
            pyautogui.scroll(action.get("dy", 0), x, y)

        elif atype == "key_down":
            key_str = action["key"]
            try:
                pyautogui.keyDown(key_str)
            except Exception:
                pyautogui.press(key_str)

        elif atype == "key_up":
            key_str = action["key"]
            try:
                pyautogui.keyUp(key_str)
            except Exception:
                pass  # ignore if key wasn't held

    def play(self, filepath: str, loops: int | None = None,
             offset: tuple[int, int] = (0, 0),
             auto_focus: bool = True):
        """
        Replay a recorded action sequence.

        Parameters
        ----------
        filepath : str
            Path to the recording JSON file.
        loops : int, optional
            Number of replay loops.  Defaults to config["loop_count"].
        offset : tuple[int, int]
            Additional (dx, dy) to add to all coordinates.
        auto_focus : bool
            If True, find & focus the game window before each loop.
        """
        # Load recording
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
        actions = data.get("actions", data)

        if not actions:
            print("[player] ⚠️  Recording is empty, nothing to play.")
            return

        loops = loops or self.config.get("loop_count", 1)
        speed = self.config.get("playback_speed", 1.0)
        delay = self.config.get("delay_between_loops", 10.0)
        pre_delay = self.config.get("pre_attack_delay", 2.0)
        post_delay = self.config.get("post_attack_delay", 5.0)

        total_events = len(actions)
        duration = actions[-1]["time"] if actions else 0

        print(f"[player] ▶️  Replaying {total_events} events ({duration:.1f}s) × {loops} loops "
              f"at {speed}x speed")
        print(f"[player] Press {self.config.get('abort_hotkey', 'F8')} to abort at any time.\n")

        self._abort.clear()
        self._start_abort_listener()

        try:
            for loop_idx in range(1, loops + 1):
                if self._abort.is_set():
                    break

                print(f"[player] 🔄 Loop {loop_idx}/{loops}")

                # Focus the game window
                if auto_focus:
                    win_offset = self._ensure_window_focus()
                    if win_offset is None:
                        print("[player] Skipping loop — window not found.")
                        continue
                    # Use window offset for coordinate correction
                    # (0, 0) offset means coordinates are already absolute
                    effective_offset = (offset[0], offset[1])
                else:
                    effective_offset = offset

                # Pre-attack delay
                if pre_delay > 0:
                    print(f"[player]    ⏳ Pre-attack delay: {pre_delay:.1f}s")
                    if self._abort.wait(pre_delay):
                        break

                # Replay each action with timing
                prev_time = 0.0
                for i, action in enumerate(actions):
                    if self._abort.is_set():
                        break

                    # Wait for the correct time delta
                    dt = (action["time"] - prev_time) / speed
                    if dt > 0:
                        if self._abort.wait(dt):
                            break
                    prev_time = action["time"]

                    self._execute_action(action, speed, effective_offset)

                    # Progress indicator every 20% of events
                    if (i + 1) % max(1, total_events // 5) == 0:
                        pct = (i + 1) / total_events * 100
                        print(f"[player]    📍 {pct:.0f}% complete ({i+1}/{total_events})")

                if self._abort.is_set():
                    break

                # Post-attack delay
                if post_delay > 0 and loop_idx < loops:
                    print(f"[player]    ⏳ Post-attack delay: {post_delay:.1f}s")
                    if self._abort.wait(post_delay):
                        break

                # Delay between loops
                if loop_idx < loops and delay > 0:
                    print(f"[player]    💤 Waiting {delay:.1f}s before next loop...")
                    if self._abort.wait(delay):
                        break

        finally:
            self._stop_abort_listener()

        if self._abort.is_set():
            print("\n[player] 🛑 Replay aborted by user.")
        else:
            print(f"\n[player] ✅ All {loops} loops completed successfully!")

    def play_single(self, actions: list[dict], speed: float = 1.0,
                    offset: tuple[int, int] = (0, 0)):
        """
        Play a list of actions once (no looping, no file I/O).
        Useful for testing or chaining with image_matcher.
        """
        if not actions:
            return

        self._abort.clear()
        prev_time = 0.0
        for action in actions:
            if self._abort.is_set():
                break
            dt = (action["time"] - prev_time) / speed
            if dt > 0:
                time.sleep(dt)
            prev_time = action["time"]
            self._execute_action(action, speed, offset)

    def play_random(self, filepaths: list[str], loops: int | None = None,
                    offset: tuple[int, int] = (0, 0),
                    auto_focus: bool = True):
        """
        Anti-detection random replay: each loop picks a random recording
        from the provided list and adds slight timing jitter.

        Parameters
        ----------
        filepaths : list[str]
            List of recording JSON file paths to randomly choose from.
        loops : int, optional
            Total number of loops. Defaults to config["loop_count"].
        offset : tuple[int, int]
            Additional (dx, dy) to add to all coordinates.
        auto_focus : bool
            If True, find & focus the game window before each loop.
        """
        import random

        # Pre-load all recordings
        loaded: list[tuple[str, list[dict]]] = []
        for fp in filepaths:
            with open(fp, "r", encoding="utf-8") as f:
                data = json.load(f)
            actions = data.get("actions", data)
            name = os.path.basename(fp)
            if actions:
                loaded.append((name, actions))
            else:
                print(f"[player] ⚠️  Skipping empty recording: {name}")

        if not loaded:
            print("[player] ⚠️  No valid recordings to play.")
            return

        loops = loops or self.config.get("loop_count", 1)
        speed = self.config.get("playback_speed", 1.0)
        delay = self.config.get("delay_between_loops", 10.0)
        pre_delay = self.config.get("pre_attack_delay", 2.0)
        post_delay = self.config.get("post_attack_delay", 5.0)

        print(f"[player] 🎲 RANDOM REPLAY — {len(loaded)} recordings × {loops} loops "
              f"at {speed}x speed")
        print(f"[player]    Recordings pool:")
        for name, acts in loaded:
            dur = acts[-1]["time"] if acts else 0
            print(f"             • {name}  ({len(acts)} events, {dur:.1f}s)")
        print(f"[player] Press {self.config.get('abort_hotkey', 'F8')} to abort at any time.\n")

        self._abort.clear()
        self._start_abort_listener()

        try:
            for loop_idx in range(1, loops + 1):
                if self._abort.is_set():
                    break

                # Randomly pick a recording
                rec_name, actions = random.choice(loaded)
                total_events = len(actions)

                print(f"[player] 🔄 Loop {loop_idx}/{loops} — using: {rec_name}")

                # Focus the game window
                if auto_focus:
                    win_offset = self._ensure_window_focus()
                    if win_offset is None:
                        print("[player] Skipping loop — window not found.")
                        continue
                    effective_offset = (offset[0], offset[1])
                else:
                    effective_offset = offset

                # Pre-attack delay with random jitter (±30%)
                jittered_pre = pre_delay * random.uniform(0.7, 1.3)
                if jittered_pre > 0:
                    print(f"[player]    ⏳ Pre-attack delay: {jittered_pre:.1f}s")
                    if self._abort.wait(jittered_pre):
                        break

                # Replay each action with timing + random jitter
                prev_time = 0.0
                for i, action in enumerate(actions):
                    if self._abort.is_set():
                        break

                    # Time delta with ±5% jitter for anti-detection
                    dt = (action["time"] - prev_time) / speed
                    if dt > 0:
                        jitter = dt * random.uniform(-0.05, 0.05)
                        wait = max(0, dt + jitter)
                        if self._abort.wait(wait):
                            break
                    prev_time = action["time"]

                    self._execute_action(action, speed, effective_offset)

                    # Progress indicator every 20%
                    if (i + 1) % max(1, total_events // 5) == 0:
                        pct = (i + 1) / total_events * 100
                        print(f"[player]    📍 {pct:.0f}% complete ({i+1}/{total_events})")

                if self._abort.is_set():
                    break

                # Post-attack delay with jitter
                if post_delay > 0 and loop_idx < loops:
                    jittered_post = post_delay * random.uniform(0.7, 1.3)
                    print(f"[player]    ⏳ Post-attack delay: {jittered_post:.1f}s")
                    if self._abort.wait(jittered_post):
                        break

                # Delay between loops with jitter
                if loop_idx < loops and delay > 0:
                    jittered_delay = delay * random.uniform(0.7, 1.3)
                    print(f"[player]    💤 Waiting {jittered_delay:.1f}s before next loop...")
                    if self._abort.wait(jittered_delay):
                        break

        finally:
            self._stop_abort_listener()

        if self._abort.is_set():
            print("\n[player] 🛑 Replay aborted by user.")
        else:
            print(f"\n[player] ✅ All {loops} random loops completed successfully!")


# ---------------------------------------------------------------------------
# Standalone entry-point
# ---------------------------------------------------------------------------

def main():
    config_path = os.path.join(os.path.dirname(__file__), "config.json")
    if os.path.exists(config_path):
        with open(config_path, "r") as f:
            config = json.load(f)
    else:
        config = {}

    if len(sys.argv) < 2:
        # List available recordings
        rec_dir = config.get("recordings_dir",
                             os.path.join(os.path.dirname(__file__), "recordings"))
        if os.path.exists(rec_dir):
            files = [f for f in os.listdir(rec_dir) if f.endswith(".json")]
            if files:
                print("Available recordings:")
                for f in sorted(files):
                    print(f"  • {f}")
            else:
                print("No recordings found. Record one first with recorder.py")
        else:
            print(f"Recordings directory not found: {rec_dir}")
        print(f"\nUsage: python player.py <recording.json> [loops]")
        sys.exit(1)

    filepath = sys.argv[1]
    if not os.path.exists(filepath):
        # Try in recordings dir
        alt = os.path.join(config.get("recordings_dir", "recordings"), filepath)
        if os.path.exists(alt):
            filepath = alt
        elif os.path.exists(alt + ".json"):
            filepath = alt + ".json"
        else:
            print(f"File not found: {filepath}")
            sys.exit(1)

    loops = int(sys.argv[2]) if len(sys.argv) > 2 else None

    player = ActionPlayer(config)
    player.play(filepath, loops=loops)


if __name__ == "__main__":
    main()
