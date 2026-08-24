"""Deterministic Morse/CW audio for the optional post-transfer identifier."""

from __future__ import annotations

import numpy as np


MORSE = {
    "A": ".-", "B": "-...", "C": "-.-.", "D": "-..", "E": ".",
    "F": "..-.", "G": "--.", "H": "....", "I": "..", "J": ".---",
    "K": "-.-", "L": ".-..", "M": "--", "N": "-.", "O": "---",
    "P": ".--.", "Q": "--.-", "R": ".-.", "S": "...", "T": "-",
    "U": "..-", "V": "...-", "W": ".--", "X": "-..-", "Y": "-.--",
    "Z": "--..",
    "0": "-----", "1": ".----", "2": "..---", "3": "...--", "4": "....-",
    "5": ".....", "6": "-....", "7": "--...", "8": "---..", "9": "----.",
    "/": "-..-.", "-": "-....-",
}


def normalise_morse_text(text: str) -> str:
    words = []
    for word in str(text or "").upper().split():
        clean = "".join(char for char in word if char in MORSE)
        if clean:
            words.append(clean)
    return " ".join(words)


def modulate_morse(
    text: str,
    *,
    sample_rate: int = 48_000,
    wpm: float = 40.0,
    tone_hz: float = 800.0,
    amplitude: float = 0.55,
) -> np.ndarray:
    """Return mono float64 CW using standard PARIS timing."""
    clean = normalise_morse_text(text)
    if not clean:
        return np.zeros(0, dtype=np.float64)
    rate = max(1, int(sample_rate))
    unit = max(1, int(round(rate * 1.2 / max(1.0, float(wpm)))))
    silence = lambda units: np.zeros(unit * units, dtype=np.float64)

    def tone(units: int) -> np.ndarray:
        count = unit * units
        phase = 2.0 * np.pi * float(tone_hz) * np.arange(count) / rate
        samples = float(amplitude) * np.sin(phase)
        ramp_count = min(count // 2, max(1, int(rate * 0.004)))
        ramp = 0.5 - 0.5 * np.cos(np.linspace(0.0, np.pi, ramp_count))
        samples[:ramp_count] *= ramp
        samples[-ramp_count:] *= ramp[::-1]
        return samples

    parts: list[np.ndarray] = []
    words = clean.split()
    for word_index, word in enumerate(words):
        for char_index, char in enumerate(word):
            symbols = MORSE[char]
            for symbol_index, symbol in enumerate(symbols):
                parts.append(tone(1 if symbol == "." else 3))
                if symbol_index + 1 < len(symbols):
                    parts.append(silence(1))
            if char_index + 1 < len(word):
                parts.append(silence(3))
        if word_index + 1 < len(words):
            parts.append(silence(7))
    return np.concatenate(parts) if parts else np.zeros(0, dtype=np.float64)
