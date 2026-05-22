"""
main.py — CLI entry point for AutoPilot Desktop Automation.

Provides an interactive menu to:
  1. Record a new automation sequence
  2. Replay a saved recording
  3. Manage saved recordings
  4. Capture a screenshot
  5. Edit configuration
  6. Exit

Run:  python main.py
"""

import json
import os
import sys
import time
from sounds import pregenerate_all

# Resolve paths relative to this file
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
RECORDINGS_DIR = os.path.join(BASE_DIR, "recordings")


# ---------------------------------------------------------------------------
# Config management
# ---------------------------------------------------------------------------

def load_config() -> dict:
    """Load config.json, returning defaults if missing."""
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {
        "loop_count": 5,
        "delay_between_loops": 10.0,
        "playback_speed": 1.0,
        "recording_hotkey_start": "F6",
        "recording_hotkey_stop": "F7",
        "abort_hotkey": "F8",
        "recordings_dir": "recordings",
        "mouse_move_duration": 0.1,
        "countdown_seconds": 10,
        "sound_enabled": True,
    }


def save_config(config: dict):
    """Persist config to disk."""
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=4)
    print("  [OK] Settings saved.\n")


# ---------------------------------------------------------------------------
# Menu helpers
# ---------------------------------------------------------------------------

def print_banner():
    """Print the application banner."""
    banner = r"""
    ╔══════════════════════════════════════════════════════════════════╗
    ║                                                                  ║
    ║       █████╗ ██╗   ██╗████████╗ ██████╗ ██████╗ ██╗██╗      ║
    ║      ██╔══██╗██║   ██║╚══██╔══╝██╔═══██╗██╔══██╗██║██║      ║
    ║      ███████║██║   ██║   ██║   ██║   ██║██████╔╝██║██║      ║
    ║      ██╔══██║██║   ██║   ██║   ██║   ██║██╔═══╝ ██║██║      ║
    ║      ██║  ██║╚██████╔╝   ██║   ╚██████╔╝██║     ██║███████╗ ║
    ║      ╚═╝  ╚═╝ ╚═════╝    ╚═╝    ╚═════╝ ╚═╝     ╚═╝╚══════╝ ║
    ║                                                                  ║
    ║              Desktop Automation Suite  v1.0                      ║
    ║         Record · Replay · Automate — Any Application             ║
    ║                                                                  ║
    ╚══════════════════════════════════════════════════════════════════╝
    """
    print(banner)


def print_menu():
    """Print the main menu."""
    print("  ┌──────────────────────────────────────────┐")
    print("  │             CONTROL  PANEL                │")
    print("  ├──────────────────────────────────────────┤")
    print("  │  [1]  ●  New Recording                    │")
    print("  │  [2]  ▶  Replay Automation                │")
    print("  │  [3]  ≡  Manage Recordings                │")
    print("  │  [4]  □  Capture Screenshot               │")
    print("  │  [5]  ⚙  Settings                         │")
    print("  │  [6]  ✕  Exit                              │")
    print("  └──────────────────────────────────────────┘")
    print()


def get_recordings(config: dict) -> list[str]:
    """Return sorted list of recording filenames."""
    rec_dir = os.path.join(BASE_DIR, config.get("recordings_dir", "recordings"))
    os.makedirs(rec_dir, exist_ok=True)
    return sorted(f for f in os.listdir(rec_dir) if f.endswith(".json"))


def choose_recording(config: dict) -> str | None:
    """Interactive picker for recordings. Returns full path or None."""
    files = get_recordings(config)
    if not files:
        print("  [!] No recordings found. Create one first.\n")
        return None

    print("\n  Available Recordings:")
    for i, f in enumerate(files, 1):
        # Show file size and preview
        fpath = os.path.join(BASE_DIR, config.get("recordings_dir", "recordings"), f)
        size_kb = os.path.getsize(fpath) / 1024
        try:
            with open(fpath, "r") as fp:
                data = json.load(fp)
            n_events = data.get("event_count", len(data.get("actions", [])))
            duration = data.get("duration_sec", 0)
            info = f"{n_events} actions, {duration:.1f}s"
        except Exception:
            info = "?"
        print(f"    [{i}] {f}  ({size_kb:.1f} KB — {info})")

    print()
    choice = input("  Select recording [#] or 'q' to cancel: ").strip()
    if choice.lower() == "q":
        return None
    try:
        idx = int(choice) - 1
        if 0 <= idx < len(files):
            return os.path.join(
                BASE_DIR, config.get("recordings_dir", "recordings"), files[idx]
            )
    except ValueError:
        pass
    print("  [!] Invalid selection.\n")
    return None


# ---------------------------------------------------------------------------
# Menu actions
# ---------------------------------------------------------------------------

def action_record(config: dict):
    """Record a new automation sequence."""
    from recorder import ActionRecorder

    name = input("  Recording name (Enter for auto-generated): ").strip()
    if not name:
        from datetime import datetime
        name = f"automation_{datetime.now():%Y%m%d_%H%M%S}"

    # Make name filesystem-safe
    name = "".join(c if c.isalnum() or c in "-_" else "_" for c in name)

    stop_key = config.get("recording_hotkey_stop", "F7")
    countdown = config.get("countdown_seconds", 10)
    print(f"\n  >> Recording begins in {countdown} seconds. Prepare your workflow.")
    print(f"  >> Press [{stop_key}] to stop recording.\n")

    for i in range(countdown, 0, -1):
        print(f"    {i}...")
        time.sleep(1)

    recorder = ActionRecorder(config)
    recorder.start()
    recorder.wait()
    actions = recorder.stop()

    if actions:
        rec_dir = os.path.join(BASE_DIR, config.get("recordings_dir", "recordings"))
        recorder.save(name, directory=rec_dir)
    else:
        print("  [!] No actions captured. Recording discarded.\n")


def choose_multiple_recordings(config: dict) -> list[str] | None:
    """
    Interactive multi-picker for recordings.
    User enters comma-separated numbers (e.g. '1,3,5').
    Returns list of full paths, or None if cancelled.
    """
    files = get_recordings(config)
    if not files:
        print("  [!] No recordings found. Create some first.\n")
        return None

    rec_dir = os.path.join(BASE_DIR, config.get("recordings_dir", "recordings"))

    print("\n  Available Recordings:")
    for i, f in enumerate(files, 1):
        fpath = os.path.join(rec_dir, f)
        size_kb = os.path.getsize(fpath) / 1024
        try:
            with open(fpath, "r") as fp:
                data = json.load(fp)
            n_events = data.get("event_count", len(data.get("actions", [])))
            duration = data.get("duration_sec", 0)
            info = f"{n_events} events, {duration:.1f}s"
        except Exception:
            info = "?"
        print(f"    [{i}] {f}  ({size_kb:.1f} KB — {info})")

    print()
    print("  Enter recording numbers separated by commas (e.g. 1,3,5):")
    choice = input("  > ").strip()
    if choice.lower() == "q":
        return None

    # Parse comma-separated numbers
    selected: list[str] = []
    try:
        indices = [int(x.strip()) - 1 for x in choice.split(",") if x.strip()]
        for idx in indices:
            if 0 <= idx < len(files):
                fpath = os.path.join(rec_dir, files[idx])
                if fpath not in selected:  # avoid duplicates
                    selected.append(fpath)
            else:
                print(f"  [!] Skipping invalid number: {idx + 1}")
    except ValueError:
        print("  [!] Invalid input. Use comma-separated numbers (e.g. 1,3,5)\n")
        return None

    if not selected:
        print("  [!] No valid recordings selected.\n")
        return None

    print(f"\n  [OK] Selected {len(selected)} recordings for shuffle pool:")
    for fp in selected:
        print(f"     • {os.path.basename(fp)}")
    print()

    return selected


def action_replay(config: dict):
    """Replay a saved automation recording."""
    from player import ActionPlayer

    files = get_recordings(config)
    if not files:
        print("  [!] No recordings found. Create one first.\n")
        return

    # Sub-menu: replay mode selection
    print("\n  ┌──────────────────────────────────────────┐")
    print("  │           REPLAY  MODE                    │")
    print("  ├──────────────────────────────────────────┤")
    print("  │  [1]  ▶  Standard Replay                  │")
    print("  │       Run one recording in a loop          │")
    print("  │                                            │")
    print("  │  [2]  ⟳  Shuffle Replay                   │")
    print("  │       Randomize across multiple recordings │")
    print("  │       with timing variation per loop       │")
    print("  └──────────────────────────────────────────┘")
    print()

    mode = input("  Select replay mode [1-2]: ").strip()

    if mode == "1":
        # ── Standard Replay ──
        filepath = choose_recording(config)
        if not filepath:
            return

        default_loops = config.get("loop_count", 5)
        loops_str = input(f"  Loop count [{default_loops}]: ").strip()
        loops = int(loops_str) if loops_str.isdigit() else default_loops

        default_speed = config.get("playback_speed", 1.0)
        speed_str = input(f"  Playback speed [{default_speed}x]: ").strip()
        try:
            speed = float(speed_str) if speed_str else default_speed
        except ValueError:
            speed = default_speed

        play_config = dict(config)
        play_config["playback_speed"] = speed

        abort_key = config.get("abort_hotkey", "F8")
        countdown = config.get("countdown_seconds", 10)
        print(f"\n  >> Automation starts in {countdown}s. Press [{abort_key}] to abort.\n")
        for i in range(countdown, 0, -1):
            print(f"    {i}...")
            time.sleep(1)

        player = ActionPlayer(play_config)
        player.play(filepath, loops=loops)
        print()

    elif mode == "2":
        # ── Shuffle Replay ──
        filepaths = choose_multiple_recordings(config)
        if not filepaths:
            return

        if len(filepaths) < 2:
            print("  [!] Shuffle mode works best with 2+ recordings.")
            print("     (Continuing with 1 — timing variation still applies)\n")

        default_loops = config.get("loop_count", 5)
        loops_str = input(f"  Total loop count [{default_loops}]: ").strip()
        loops = int(loops_str) if loops_str.isdigit() else default_loops

        default_speed = config.get("playback_speed", 1.0)
        speed_str = input(f"  Playback speed [{default_speed}x]: ").strip()
        try:
            speed = float(speed_str) if speed_str else default_speed
        except ValueError:
            speed = default_speed

        play_config = dict(config)
        play_config["playback_speed"] = speed

        abort_key = config.get("abort_hotkey", "F8")
        countdown = config.get("countdown_seconds", 10)
        print(f"\n  >> Shuffle replay starts in {countdown}s. Press [{abort_key}] to abort.\n")
        for i in range(countdown, 0, -1):
            print(f"    {i}...")
            time.sleep(1)

        player = ActionPlayer(play_config)
        player.play_random(filepaths, loops=loops)
        print()

    else:
        print("  [!] Invalid mode. Enter 1 or 2.\n")


def action_list_recordings(config: dict):
    """Display and manage saved recordings."""
    files = get_recordings(config)
    if not files:
        print("  [!] No recordings found.\n")
        return

    print(f"\n  {'Name':<30} {'Actions':>8} {'Duration':>10} {'Size':>8} {'Recorded'}")
    print(f"  {'─'*30} {'─'*8} {'─'*10} {'─'*8} {'─'*20}")

    rec_dir = os.path.join(BASE_DIR, config.get("recordings_dir", "recordings"))
    for f in files:
        fpath = os.path.join(rec_dir, f)
        size_kb = os.path.getsize(fpath) / 1024
        try:
            with open(fpath, "r") as fp:
                data = json.load(fp)
            n = data.get("event_count", "?")
            dur = data.get("duration_sec", 0)
            date = data.get("recorded_at", "?")[:19]
            print(f"  {f:<30} {n:>8} {dur:>9.1f}s {size_kb:>6.1f}KB {date}")
        except Exception:
            print(f"  {f:<30} {'?':>8} {'?':>10} {size_kb:>6.1f}KB")

    print()

    # Offer delete option
    choice = input("  Delete a recording? Enter [#] or press Enter to skip: ").strip()
    if choice.isdigit():
        idx = int(choice) - 1
        if 0 <= idx < len(files):
            fpath = os.path.join(rec_dir, files[idx])
            confirm = input(f"  Confirm delete '{files[idx]}'? [y/N]: ").strip().lower()
            if confirm == "y":
                os.remove(fpath)
                print(f"  [OK] Deleted: {files[idx]}\n")
            else:
                print("  [--] Cancelled.\n")


def action_screenshot(config: dict):
    """Capture a screenshot for reference or template matching."""
    from image_matcher import ImageMatcher

    templates_dir = os.path.join(BASE_DIR, "templates")
    os.makedirs(templates_dir, exist_ok=True)

    name = input("  Screenshot name (e.g. 'login_screen'): ").strip()
    if not name:
        from datetime import datetime
        name = f"screen_{datetime.now():%Y%m%d_%H%M%S}"
    name = "".join(c if c.isalnum() or c in "-_" else "_" for c in name)

    print("  >> Capturing screen in 3 seconds. Switch to your target window.\n")
    for i in range(3, 0, -1):
        print(f"    {i}...")
        time.sleep(1)

    matcher = ImageMatcher(config)
    filepath = os.path.join(templates_dir, f"{name}.png")
    matcher.save_screenshot(filepath)
    print(f"  [OK] Saved: {filepath}\n")
    print("  TIP: Crop this image to isolate the UI element you need to match.\n")


def action_edit_config(config: dict) -> dict:
    """Interactively edit configuration settings."""
    print("\n  Current Settings:")
    print("  " + "─" * 45)
    editable = [
        ("loop_count",          "Loop count",           int),
        ("delay_between_loops", "Delay between loops (s)", float),
        ("playback_speed",      "Playback speed",       float),
        ("recording_hotkey_stop", "Stop recording hotkey", str),
        ("abort_hotkey",        "Abort replay hotkey",  str),
        ("mouse_move_duration", "Mouse move duration (s)", float),
        ("countdown_seconds",   "Countdown seconds",    int),
        ("sound_enabled",       "Sound announcements",  bool),
    ]

    for i, (key, label, _) in enumerate(editable, 1):
        val = config.get(key, "?")
        print(f"    [{i:>2}] {label:<28} = {val}")

    print(f"\n  Enter [#] to edit a setting, or 'q' to return.")
    choice = input("  > ").strip()
    if choice.lower() == "q":
        return config

    try:
        idx = int(choice) - 1
        if 0 <= idx < len(editable):
            key, label, typ = editable[idx]
            current = config.get(key, "")
            new_val = input(f"  New value for '{label}' [{current}]: ").strip()
            if new_val:
                if typ == bool:
                    config[key] = new_val.lower() in ("true", "1", "yes", "on")
                else:
                    config[key] = typ(new_val)
                save_config(config)
            else:
                print("  No change.\n")
    except (ValueError, TypeError) as e:
        print(f"  [!] Invalid input: {e}\n")

    return config


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def main():
    print_banner()

    config = load_config()

    # Ensure recordings directory exists
    os.makedirs(os.path.join(BASE_DIR, config.get("recordings_dir", "recordings")),
                exist_ok=True)

    # Pre-generate sound files on first run
    if config.get("sound_enabled", True):
        pregenerate_all()

    while True:
        print_menu()
        choice = input("  Select [1-6]: ").strip()

        if choice == "1":
            action_record(config)
        elif choice == "2":
            action_replay(config)
        elif choice == "3":
            action_list_recordings(config)
        elif choice == "4":
            action_screenshot(config)
        elif choice == "5":
            config = action_edit_config(config)
        elif choice == "6":
            print("\n  Session ended. Goodbye.\n")
            break
        else:
            print("  [!] Invalid option. Enter 1-6.\n")


if __name__ == "__main__":
    main()
