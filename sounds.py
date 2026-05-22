"""
sounds.py — Audio announcements using Windows text-to-speech.

Generates and caches WAV files on first use, then plays them
asynchronously so they don't block the main thread.

Requires: Windows (uses win32 SAPI via comtypes/PowerShell fallback).
"""

import os
import subprocess
import threading
import winsound

# Directory to cache generated WAV files
_SOUNDS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sounds_cache")

# Pre-defined announcements
ANNOUNCEMENTS = {
    "recording_started": "Recording started",
    "recording_stopped": "Recording stopped",
    "loop_started": "Loop started",
    "loop_ended": "Loop ended",
}


def _ensure_cache_dir():
    """Create the sounds cache directory if it doesn't exist."""
    os.makedirs(_SOUNDS_DIR, exist_ok=True)


def _generate_wav(text: str, filepath: str):
    """
    Generate a WAV file from text using Windows SAPI via PowerShell.
    This is called once per announcement and cached for future use.
    """
    # PowerShell script to generate speech WAV file
    ps_script = f'''
Add-Type -AssemblyName System.Speech
$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
$synth.SetOutputToWaveFile("{filepath}")
$synth.Speak("{text}")
$synth.Dispose()
'''
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_script],
            capture_output=True, timeout=15,
            creationflags=subprocess.CREATE_NO_WINDOW
        )
    except Exception as e:
        print(f"[autopilot] [!] Could not generate speech for '{text}': {e}")


def _get_wav_path(name: str) -> str:
    """Get the path to a cached WAV file, generating it if needed."""
    _ensure_cache_dir()
    filepath = os.path.join(_SOUNDS_DIR, f"{name}.wav")
    if not os.path.exists(filepath):
        text = ANNOUNCEMENTS.get(name, name.replace("_", " "))
        print(f"[autopilot] Generating audio: '{text}' (one-time setup)")
        _generate_wav(text, filepath)
    return filepath


def play_announcement(name: str, enabled: bool = True):
    """
    Play a named announcement asynchronously.

    Parameters
    ----------
    name : str
        Key from ANNOUNCEMENTS (e.g. 'recording_started').
    enabled : bool
        If False, does nothing (respects the sound_enabled config).
    """
    if not enabled:
        return

    def _play():
        try:
            wav_path = _get_wav_path(name)
            if os.path.exists(wav_path):
                winsound.PlaySound(wav_path, winsound.SND_FILENAME)
        except Exception as e:
            print(f"[autopilot] [!] Audio playback error: {e}")

    # Play in a background thread so it doesn't block
    t = threading.Thread(target=_play, daemon=True)
    t.start()


def play_announcement_sync(name: str, enabled: bool = True):
    """
    Play a named announcement and WAIT for it to finish.
    Use this when you need the sound to complete before proceeding.
    """
    if not enabled:
        return
    try:
        wav_path = _get_wav_path(name)
        if os.path.exists(wav_path):
            winsound.PlaySound(wav_path, winsound.SND_FILENAME)
    except Exception as e:
        print(f"[autopilot] [!] Playback error: {e}")


def pregenerate_all():
    """
    Pre-generate all announcement WAV files.
    Call this once at startup to avoid delays during recording/playback.
    """
    print("[autopilot] Preparing audio files...")
    for name in ANNOUNCEMENTS:
        _get_wav_path(name)
    print("[autopilot] Audio files ready.")
