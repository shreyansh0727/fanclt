"""Generate the DFPlayer Mini voice clips used by the firmware.

Every phrase the controller speaks is a short MP3 on the SD card at
/mp3/NNNN.mp3. This script synthesises them on Windows with the built-in
System.Speech voice (22050 Hz mono) and encodes them with lameenc
(`pip install lameenc`). Track 52 is not speech: it is a generated cartoon
meow for the pet, replaceable with a real recording. Tracks 58 and 59 are the
synthesised alarm and reminder chimes.

    .venv/Scripts/python.exe scripts/make_voice_clips.py
    .venv/Scripts/python.exe scripts/make_voice_clips.py --voice Zira --rate 1
    .venv/Scripts/python.exe scripts/make_voice_clips.py --tracks 52
    .venv/Scripts/python.exe scripts/make_voice_clips.py --tracks 58,59

Output: firmware/sdcard/mp3/0001.mp3 ... 0059.mp3 plus a README for the card.
Keep the track table in sync with the Clip enum in firmware/src/audio.cpp.
"""

from __future__ import annotations

import argparse
import array
import json
import math
import shutil
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

import lameenc

REPO_ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = REPO_ROOT / "firmware" / "sdcard"

# Spoken letter names so SAPI does not read a lone "A" as the article "uh".
# Each letter is followed by its NATO word: "bee" and "vee" (or "dee" and "ee")
# are indistinguishable through a small speaker, the extra word is not.
LETTER_NAMES = {
    "A": "ay, alpha",
    "B": "bee, bravo",
    "C": "sea, charlie",
    "D": "dee, delta",
    "E": "ee, echo",
    "F": "eff, foxtrot",
    "G": "gee, golf",
    "H": "aitch, hotel",
    "I": "eye, india",
    "J": "jay, juliet",
    "K": "kay, kilo",
    "L": "el, lima",
    "M": "em, mike",
    "N": "en, november",
    "O": "oh, oscar",
    "P": "pee, papa",
    "Q": "cue, quebec",
    "R": "are, romeo",
    "S": "ess, sierra",
    "T": "tee, tango",
    "U": "you, uniform",
    "V": "vee, victor",
    "W": "double you, whiskey",
    "X": "ex, x-ray",
    "Y": "why, yankee",
    "Z": "zee, zulu",
}

DIGITS = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine"]

PHRASES = {
    1: "pairing code is",
    2: "connected to wifi",
    3: "connect to hotspot",
    4: "then open one nine two dot one six eight dot four dot one",
    5: "setup mode",
    6: "paired",
    7: "ready",
    8: "channel",
    9: "all",
    10: "on",
    11: "off",
    12: "fan",
    13: "light",
    14: "socket",
    15: "here i am",
    16: "zero",
    17: "one",
    18: "two",
    19: "three",
    20: "four",
    21: "five",
    22: "six",
    23: "seven",
    24: "eight",
    25: "nine",
}
for offset, letter in enumerate("ABCDEFGHIJKLMNOPQRSTUVWXYZ"):
    PHRASES[26 + offset] = LETTER_NAMES[letter]

# Not speech: the pet's meow, synthesised below. Tracks after it are the pet
# reactions the app can trigger; keep them in sync with firmware/src/audio.cpp.
MEOW_TRACK = 52
PHRASES.update(
    {
        53: "purr purr",
        54: "yum yum",
        55: "let's play",
        56: "good night",
        57: "good morning",
    }
)

# Synthesised chimes, not speech. 58 is the alarm siren, 59 the reminder ding.
TONE_TRACKS = (58, 59)

# PowerShell with System.Speech renders every clip in one process; starting it
# once per word costs about a second per clip.
PS_SCRIPT = r"""
param([Parameter(Mandatory=$true)][string]$JobFile)
$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Speech
$job = Get-Content -Raw -LiteralPath $JobFile | ConvertFrom-Json
$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
if ($job.voice) { $synth.SelectVoice($job.voice) }
else {
  $voice = $synth.GetInstalledVoices() |
    Where-Object { $_.Enabled -and $_.VoiceInfo.Culture.Name -like 'en*' } |
    Select-Object -First 1
  if ($voice) { $synth.SelectVoice($voice.VoiceInfo.Name) }
}
$synth.Rate = [int]$job.rate
$format = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(
  22050,
  [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen,
  [System.Speech.AudioFormat.AudioChannel]::Mono)
foreach ($clip in $job.clips) {
  $synth.SetOutputToWaveFile($clip.wav, $format)
  $synth.Speak($clip.text)
  $synth.SetOutputToNull()
}
Write-Output $synth.Voice.Name
$synth.Dispose()
"""


def powershell() -> str:
    for name in ("powershell", "pwsh", "powershell.exe"):
        found = shutil.which(name)
        if found:
            return found
    raise SystemExit("PowerShell not found - this script needs Windows System.Speech")


def synthesise(jobs: list[dict], voice: str | None, rate: int) -> None:
    with tempfile.TemporaryDirectory(prefix="fanctl-voice-") as tmp:
        tmp_path = Path(tmp)
        job_file = tmp_path / "job.json"
        ps_file = tmp_path / "speak.ps1"
        job_file.write_text(
            json.dumps({"voice": voice, "rate": rate, "clips": jobs}, ensure_ascii=False),
            encoding="utf-8",
        )
        ps_file.write_text(PS_SCRIPT, encoding="utf-8")
        result = subprocess.run(
            [
                powershell(),
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(ps_file),
                "-JobFile",
                str(job_file),
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            sys.stderr.write(result.stdout + "\n" + result.stderr + "\n")
            raise SystemExit("Speech synthesis failed")
        used = (result.stdout or "").strip().splitlines()
        if used:
            print(f"voice: {used[-1]}")


def trim_silence(pcm: bytes, rate: int, threshold: int = 400) -> bytes:
    """Drop leading/trailing near-silence so clips follow each other tightly."""
    samples = array.array("h", pcm)
    start = 0
    while start < len(samples) and abs(samples[start]) < threshold:
        start += 1
    end = len(samples)
    while end > start and abs(samples[end - 1]) < threshold:
        end -= 1
    pad = int(rate * 0.02)
    start = max(0, start - pad)
    end = min(len(samples), end + pad)
    return samples[start:end].tobytes()


def encode(wav_path: Path, mp3_path: Path, bitrate: int) -> None:
    with wave.open(str(wav_path), "rb") as wav:
        if wav.getnchannels() != 1 or wav.getsampwidth() != 2:
            raise SystemExit(f"{wav_path.name}: expected 16-bit mono PCM")
        rate = wav.getframerate()
        pcm = trim_silence(wav.readframes(wav.getnframes()), rate)

    encoder = lameenc.Encoder()
    encoder.set_bit_rate(bitrate)
    encoder.set_in_sample_rate(rate)
    encoder.set_channels(1)
    encoder.set_quality(5)
    mp3_path.write_bytes(bytes(encoder.encode(pcm) + encoder.flush()))


def write_wav(path: Path, pcm: bytes, rate: int) -> None:
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(pcm)


def synth_meow(rate: int = 22050) -> bytes:
    """A short cartoon meow: a buzzing source swept through two formants."""
    n = int(0.62 * rate)
    values: list[float] = []
    phase = 0.0
    peak = 0.0
    for i in range(n):
        t = i / rate
        # Pitch climbs into an "ee" and falls through an "ow".
        if t < 0.20:
            f0 = 430.0 + (700.0 - 430.0) * (t / 0.20)
        else:
            f0 = 700.0 - (700.0 - 380.0) * min(1.0, (t - 0.20) / 0.36)
        f0 *= 1.0 + 0.015 * math.sin(2.0 * math.pi * 5.0 * t)
        phase += 2.0 * math.pi * f0 / rate
        value = 0.0
        for k in range(1, 13):
            fk = k * f0
            weight = math.exp(-(((fk - 850.0) / 600.0) ** 2)) + 0.7 * math.exp(
                -(((fk - 1900.0) / 900.0) ** 2)
            )
            value += weight * math.sin(k * phase) / math.sqrt(k)
        attack = min(1.0, t / 0.05)
        release = 1.0 if t < 0.42 else max(0.0, 1.0 - (t - 0.42) / 0.20)
        value *= attack * release
        values.append(value)
        peak = max(peak, abs(value))

    scale = 0.7 * 32767.0 / (peak or 1.0)
    samples = array.array("h")
    for value in values:
        samples.append(max(-32768, min(32767, int(value * scale))))
    return samples.tobytes()


def _pcm(values: list[float], peak: float, rate: int) -> bytes:
    scale = 0.7 * 32767.0 / (peak or 1.0)
    samples = array.array("h")
    for value in values:
        samples.append(max(-32768, min(32767, int(value * scale))))
    return samples.tobytes()


def _tone(freq: float, dur: float, rate: int, partials=(1.0, 0.3, 0.12)) -> tuple[list[float], float]:
    """One soft-edged note with a few harmonics so the small speaker carries."""
    count = int(dur * rate)
    values: list[float] = []
    peak = 0.0
    for i in range(count):
        t = i / rate
        envelope = min(1.0, t / 0.01) * min(1.0, (dur - t) / 0.05)
        value = 0.0
        for harmonic, weight in enumerate(partials, start=1):
            value += weight * math.sin(2.0 * math.pi * freq * harmonic * t)
        value *= max(0.0, envelope)
        values.append(value)
        peak = max(peak, abs(value))
    return values, peak


def _silence(dur: float, rate: int) -> list[float]:
    return [0.0] * int(dur * rate)


def synth_alarm(rate: int = 22050) -> bytes:
    """Five two-tone siren bursts, about 2.5 s: beep-beep, beep-beep..."""
    values: list[float] = []
    peak = 0.0
    for burst in range(5):
        for freq in (784.0, 587.0):
            note, note_peak = _tone(freq, 0.20, rate)
            values.extend(note)
            peak = max(peak, note_peak)
        if burst < 4:
            values.extend(_silence(0.09, rate))
    return _pcm(values, peak, rate)


def synth_reminder(rate: int = 22050) -> bytes:
    """Two soft ding-dong pairs with a long decay, about 2.6 s."""
    values: list[float] = []
    peak = 0.0
    for _ in range(2):
        for freq in (1046.5, 784.0):
            count = int(0.55 * rate)
            for i in range(count):
                t = i / rate
                envelope = min(1.0, t / 0.008) * math.exp(-t * 4.5)
                value = (
                    math.sin(2.0 * math.pi * freq * t)
                    + 0.25 * math.sin(2.0 * math.pi * freq * 2.0 * t)
                ) * envelope
                values.append(value)
                peak = max(peak, abs(value))
        values.extend(_silence(0.18, rate))
    return _pcm(values, peak, rate)


TONE_SYNTHS = {58: synth_alarm, 59: synth_reminder}


CARD_README = """FanCtl voice clips
==================

The ESP32 controller drives a DFPlayer Mini (MP3-TF-16P). The clips in the
mp3/ folder are the words the speaker can say.

Copy the whole mp3 folder to the root of a FAT32 microSD card (32 GB or
smaller) so the module sees /mp3/0001.mp3 ... /mp3/0059.mp3. DFPlayer needs
four digit file names; the folder name must be lowercase "mp3".

Track table
-----------
0001  pairing code is            0016-0025  zero .. nine
0002  connected to wifi          0026-0051  A .. Z as "letter, NATO word"
0003  connect to hotspot         0052       cat meow (generated, not speech)
0004  then open 192.168.4.1      0053       purr purr
0005  setup mode                 0054       yum yum
0006  paired                     0055       let's play
0007  ready                      0056       good night
0008  channel                    0057       good morning
0009  all                        0058       alarm siren (generated)
0010  on                         0059       reminder chime (generated)
0011  off
0012  fan
0013  light
0014  socket
0015  here i am

Regenerate with: .venv/Scripts/python.exe scripts/make_voice_clips.py
Just the meow:   .venv/Scripts/python.exe scripts/make_voice_clips.py --tracks 52
Just the chimes: .venv/Scripts/python.exe scripts/make_voice_clips.py --tracks 58,59
"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--voice", help="exact System.Speech voice name (default: first English voice)")
    parser.add_argument("--rate", type=int, default=0, choices=range(-3, 4), metavar="-3..3")
    parser.add_argument("--bitrate", type=int, default=48, help="MP3 bitrate in kbps (default 48)")
    parser.add_argument("--out", type=Path, default=OUT_DIR, help="output directory (default firmware/sdcard)")
    parser.add_argument(
        "--tracks",
        help="comma-separated track numbers to write (default: every track); e.g. 52 for just the meow",
    )
    args = parser.parse_args()

    tracks = sorted(set(PHRASES) | set(TONE_TRACKS))
    # 1-51 speech, 52 reserved for the meow, 53+ reactions and chimes are dense.
    expected = list(range(1, MEOW_TRACK)) + list(range(MEOW_TRACK + 1, max(tracks) + 1))
    if tracks != expected:
        raise SystemExit("tracks 1-51, 53+ must be contiguous and 52 stays the meow")

    selected = set(tracks) | {MEOW_TRACK}
    if args.tracks:
        try:
            selected = {int(part) for part in args.tracks.split(",") if part.strip()}
        except ValueError:
            raise SystemExit("--tracks wants comma-separated track numbers")
        unknown = selected - (set(tracks) | {MEOW_TRACK})
        if unknown:
            raise SystemExit(f"unknown track(s): {sorted(unknown)}")

    mp3_dir = args.out / "mp3"
    mp3_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="fanctl-voice-") as tmp:
        tmp_path = Path(tmp)
        speech = [track for track in tracks if track in selected and track in PHRASES]
        jobs = [
            {"track": track, "text": PHRASES[track], "wav": str(tmp_path / f"{track:04d}.wav")}
            for track in speech
        ]
        if jobs:
            synthesise(jobs, args.voice, args.rate)

        if MEOW_TRACK in selected:
            meow_wav = tmp_path / f"{MEOW_TRACK:04d}.wav"
            write_wav(meow_wav, synth_meow(), 22050)
            jobs.append({"track": MEOW_TRACK, "wav": str(meow_wav)})

        for track in TONE_TRACKS:
            if track not in selected:
                continue
            tone_wav = tmp_path / f"{track:04d}.wav"
            write_wav(tone_wav, TONE_SYNTHS[track](), 22050)
            jobs.append({"track": track, "wav": str(tone_wav)})

        total = 0
        for job in jobs:
            wav_path = Path(job["wav"])
            mp3_path = mp3_dir / f"{job['track']:04d}.mp3"
            encode(wav_path, mp3_path, args.bitrate)
            total += mp3_path.stat().st_size

    (args.out / "README.txt").write_text(CARD_README, encoding="utf-8")
    print(f"wrote {len(jobs)} clips ({total / 1024:.0f} KiB) to {mp3_dir}")
    print("copy the contents of firmware/sdcard to the root of the SD card")


if __name__ == "__main__":
    main()
