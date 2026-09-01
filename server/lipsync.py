"""Dialogue audio and phoneme extraction driver.

1. Audio per line into audio/dialogue/<line_id>.wav (recorded, or synthesized
   by whatever TTS you plug into `synthesize`).
2. Rhubarb Lip Sync -> audio/phonemes/<line_id>.json. Rhubarb's own JSON output
   is stored unchanged: {"metadata": {...}, "mouthCues": [{"start","end","value"}]}.
3. Duration in frames, which the breakdown's dialogue shots must respect.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import wave
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
DIALOGUE_DIR = ROOT / "audio" / "dialogue"
PHONEME_DIR = ROOT / "audio" / "phonemes"


def audio_path(line_id: str) -> Path:
    return DIALOGUE_DIR / f"{line_id}.wav"


def phoneme_path(line_id: str) -> Path:
    return PHONEME_DIR / f"{line_id}.json"


def find_rhubarb() -> str | None:
    return os.environ.get("RHUBARB") or shutil.which("rhubarb")


def wav_seconds(path: Path) -> float:
    with wave.open(str(path), "rb") as w:
        return w.getnframes() / float(w.getframerate())


def extract_phonemes(line_id: str, text: str | None = None, recognizer: str = "pocketSphinx"
                     ) -> dict[str, Any]:
    src = audio_path(line_id)
    if not src.exists():
        raise FileNotFoundError(f"no audio for line '{line_id}' at {src}; audio comes first")
    exe = find_rhubarb()
    if exe is None:
        raise RuntimeError("Rhubarb Lip Sync not found; install it and set RHUBARB=/path/to/rhubarb")
    os.makedirs(PHONEME_DIR, exist_ok=True)
    out = phoneme_path(line_id)
    cmd = [exe, "-f", "json", "-r", recognizer, "-o", str(out)]
    if text:
        dialog = out.with_suffix(".txt")
        dialog.write_text(text, encoding="utf-8")
        cmd += ["-d", str(dialog)]
    cmd.append(str(src))
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if proc.returncode != 0:
        raise RuntimeError(f"rhubarb failed: {proc.stderr[-500:]}")
    with open(out, "r", encoding="utf-8") as fh:
        return json.load(fh)


def line_frames(line_id: str, fps: float) -> int | None:
    """Frames a line occupies, from phonemes if present, else the wav length."""
    pp = phoneme_path(line_id)
    if pp.exists():
        with open(pp, "r", encoding="utf-8") as fh:
            cues = json.load(fh).get("mouthCues", [])
        if cues:
            return int(round(cues[-1]["end"] * fps))
    ap = audio_path(line_id)
    if ap.exists():
        return int(round(wav_seconds(ap) * fps))
    return None


def status(line_ids: list[str], fps: float) -> list[dict[str, Any]]:
    return [{"line_id": lid, "audio": audio_path(lid).exists(),
             "phonemes": phoneme_path(lid).exists(), "frames": line_frames(lid, fps)}
            for lid in line_ids]
