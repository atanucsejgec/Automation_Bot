"""
main.py — CLI entry point for the Clash of Clans attack bot.

Provides an interactive menu to:
  1. Record a new attack sequence
  2. Replay a saved recording
  3. List saved recordings
  4. Take a screenshot (for template images)
  5. Edit configuration
  6. Exit

Run:  python main.py
"""

import json
import os
import sys
import time

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
        "target_window_title": "Google Play Games",
        "recordings_dir": "recordings",
        "mouse_move_duration": 0.1,
        "confidence_threshold": 0.8,
        "pre_attack_delay": 2.0,
        "post_attack_delay": 5.0,
    }


def save_config(config: dict):
    """Persist config to disk."""
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=4)
    print("  ✅ Config saved.\n")


# ---------------------------------------------------------------------------
# Menu helpers
# ---------------------------------------------------------------------------

def print_banner():
    """Print a cool ASCII banner."""
    banner = r"""
    ╔══════════════════════════════════════════════════════════════╗
    ║                                                              ║
    ║       ⚔️   CLASH OF CLANS — ATTACK BOT  ⚔️                  ║
    ║                                                              ║
    ║       Record · Replay · Automate                             ║
    ║                                                              ║
    ╚══════════════════════════════════════════════════════════════╝
    """
    print(banner)


def print_menu():
    """Print the main menu."""
    print("  ┌─────────────────────────────────────┐")
    print("  │          MAIN MENU                   │")
    print("  ├─────────────────────────────────────┤")
    print("  │  [1]  🔴  Record new attack          │")
    print("  │  [2]  ▶️   Replay a recording         │")
    print("  │  [3]  📂  List saved recordings      │")
    print("  │  [4]  📸  Take screenshot            │")
    print("  │  [5]  ⚙️   Edit configuration         │")
    print("  │  [6]  🚪  Exit                       │")
    print("  └─────────────────────────────────────┘")
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
        print("  ⚠️  No recordings found. Record one first!\n")
        return None

    print("\n  Available recordings:")
    for i, f in enumerate(files, 1):
        # Show file size and preview
        fpath = os.path.join(BASE_DIR, config.get("recordings_dir", "recordings"), f)
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
    choice = input("  Enter number (or 'q' to cancel): ").strip()
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
    print("  ❌ Invalid selection.\n")
    return None


# ---------------------------------------------------------------------------
# Menu actions
# ---------------------------------------------------------------------------

def action_record(config: dict):
    """Record a new attack sequence."""
    from recorder import ActionRecorder

    name = input("  Enter a name for this recording (or Enter for auto): ").strip()
    if not name:
        from datetime import datetime
        name = f"attack_{datetime.now():%Y%m%d_%H%M%S}"

    # Make name filesystem-safe
    name = "".join(c if c.isalnum() or c in "-_" else "_" for c in name)

    stop_key = config.get("recording_hotkey_stop", "F7")
    print(f"\n  Get ready! Recording will start in 3 seconds...")
    print(f"  Press {stop_key} to stop recording.\n")

    for i in range(3, 0, -1):
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
        print("  ⚠️  No actions were recorded.\n")


def choose_multiple_recordings(config: dict) -> list[str] | None:
    """
    Interactive multi-picker for recordings.
    User enters comma-separated numbers (e.g. '1,3,5').
    Returns list of full paths, or None if cancelled.
    """
    files = get_recordings(config)
    if not files:
        print("  ⚠️  No recordings found. Record some attacks first!\n")
        return None

    rec_dir = os.path.join(BASE_DIR, config.get("recordings_dir", "recordings"))

    print("\n  Available recordings:")
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
    print("  Enter recording numbers separated by commas (e.g. 1,3,5)")
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
                print(f"  ⚠️  Skipping invalid number: {idx + 1}")
    except ValueError:
        print("  ❌ Invalid input. Use comma-separated numbers like: 1,3,5\n")
        return None

    if not selected:
        print("  ❌ No valid recordings selected.\n")
        return None

    print(f"\n  ✅ Selected {len(selected)} recordings for random pool:")
    for fp in selected:
        print(f"     • {os.path.basename(fp)}")
    print()

    return selected


def action_replay(config: dict):
    """Replay a saved recording — single or random mode."""
    from player import ActionPlayer

    files = get_recordings(config)
    if not files:
        print("  ⚠️  No recordings found. Record one first!\n")
        return

    # Sub-menu: Selected vs Random replay
    print("\n  ┌───────────────────────────────────────┐")
    print("  │         REPLAY MODE                    │")
    print("  ├───────────────────────────────────────┤")
    print("  │  [1]  ▶️   Selected Replay              │")
    print("  │        (one recording, loop normally)  │")
    print("  │                                        │")
    print("  │  [2]  🎲  Random Replay                │")
    print("  │        (pick multiple, shuffle each    │")
    print("  │         loop for anti-detection)       │")
    print("  └───────────────────────────────────────┘")
    print()

    mode = input("  Select mode [1-2]: ").strip()

    if mode == "1":
        # ── Selected Replay (original behavior) ──
        filepath = choose_recording(config)
        if not filepath:
            return

        default_loops = config.get("loop_count", 5)
        loops_str = input(f"  Number of loops [{default_loops}]: ").strip()
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
        print(f"\n  Starting replay in 3 seconds... (press {abort_key} to abort)\n")
        for i in range(3, 0, -1):
            print(f"    {i}...")
            time.sleep(1)

        player = ActionPlayer(play_config)
        player.play(filepath, loops=loops)
        print()

    elif mode == "2":
        # ── Random Replay (anti-detection) ──
        filepaths = choose_multiple_recordings(config)
        if not filepaths:
            return

        if len(filepaths) < 2:
            print("  ⚠️  Random mode works best with 2+ recordings.")
            print("     (Continuing with 1 — timing jitter will still apply)\n")

        default_loops = config.get("loop_count", 5)
        loops_str = input(f"  Total number of loops [{default_loops}]: ").strip()
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
        print(f"\n  🎲 Starting RANDOM replay in 3 seconds... (press {abort_key} to abort)\n")
        for i in range(3, 0, -1):
            print(f"    {i}...")
            time.sleep(1)

        player = ActionPlayer(play_config)
        player.play_random(filepaths, loops=loops)
        print()

    else:
        print("  ❌ Invalid mode. Please enter 1 or 2.\n")


def action_list_recordings(config: dict):
    """Show all saved recordings with details."""
    files = get_recordings(config)
    if not files:
        print("  📭 No recordings found.\n")
        return

    print(f"\n  {'Name':<30} {'Events':>8} {'Duration':>10} {'Size':>8} {'Date'}")
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
    choice = input("  Delete a recording? (enter number, or Enter to skip): ").strip()
    if choice.isdigit():
        idx = int(choice) - 1
        if 0 <= idx < len(files):
            fpath = os.path.join(rec_dir, files[idx])
            confirm = input(f"  Really delete {files[idx]}? (y/N): ").strip().lower()
            if confirm == "y":
                os.remove(fpath)
                print(f"  🗑️  Deleted {files[idx]}\n")
            else:
                print("  Cancelled.\n")


def action_screenshot(config: dict):
    """Take a screenshot for creating template images."""
    from image_matcher import ImageMatcher

    templates_dir = os.path.join(BASE_DIR, "templates")
    os.makedirs(templates_dir, exist_ok=True)

    name = input("  Screenshot name (e.g. 'attack_button'): ").strip()
    if not name:
        from datetime import datetime
        name = f"screen_{datetime.now():%Y%m%d_%H%M%S}"
    name = "".join(c if c.isalnum() or c in "-_" else "_" for c in name)

    print("  📸 Capturing screen in 3 seconds... (switch to game window!)\n")
    for i in range(3, 0, -1):
        print(f"    {i}...")
        time.sleep(1)

    matcher = ImageMatcher(config)
    filepath = os.path.join(templates_dir, f"{name}.png")
    matcher.save_screenshot(filepath)
    print(f"  Saved to: {filepath}\n")
    print("  TIP: Crop this image to just the UI element you want to match.\n")


def action_edit_config(config: dict) -> dict:
    """Interactively edit configuration values."""
    print("\n  Current configuration:")
    print("  " + "─" * 45)
    editable = [
        ("loop_count",          "Loop count",           int),
        ("delay_between_loops", "Delay between loops (s)", float),
        ("playback_speed",      "Playback speed",       float),
        ("recording_hotkey_stop", "Stop recording hotkey", str),
        ("abort_hotkey",        "Abort replay hotkey",  str),
        ("target_window_title", "Target window title",  str),
        ("pre_attack_delay",    "Pre-attack delay (s)", float),
        ("post_attack_delay",   "Post-attack delay (s)", float),
        ("mouse_move_duration", "Mouse move duration (s)", float),
        ("confidence_threshold","Image match threshold", float),
    ]

    for i, (key, label, _) in enumerate(editable, 1):
        val = config.get(key, "?")
        print(f"    [{i:>2}] {label:<28} = {val}")

    print(f"\n  Enter the number to edit, or 'q' to go back.")
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
                config[key] = typ(new_val)
                save_config(config)
            else:
                print("  No change.\n")
    except (ValueError, TypeError) as e:
        print(f"  ❌ Invalid input: {e}\n")

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

    while True:
        print_menu()
        choice = input("  Select an option [1-6]: ").strip()

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
            print("\n  👋 Goodbye! Happy clashing!\n")
            break
        else:
            print("  ❌ Invalid option. Please enter 1-6.\n")


if __name__ == "__main__":
    main()
