"""Hold music, made in code: a soft, slow arpeggio over four chords, ~16 s, looped. No audio file and no
licence to worry about; quiet enough that the "thank you for waiting" message stands out."""

from functools import cache

import numpy as np

# C major - A minor - F major - G major, as note frequencies (Hz)
_CHORDS = [(261.63, 329.63, 392.00, 523.25), (220.00, 261.63, 329.63, 440.00),
           (174.61, 220.00, 261.63, 349.23), (196.00, 246.94, 293.66, 392.00)]


@cache
def hold_music(rate: int = 16000, amplitude: int = 2200) -> bytes:
    note_s = 0.5
    t = np.arange(int(rate * note_s)) / rate
    envelope = np.minimum(1, t / 0.03) * np.exp(-3.2 * t)  # quick attack, gentle decay: like a soft piano
    notes = []
    for chord in _CHORDS:
        for _ in range(2):
            for f in (*chord, chord[2], chord[1]):  # up and partly back down
                wave = np.sin(2 * np.pi * f * t) + 0.25 * np.sin(4 * np.pi * f * t)
                notes.append(wave * envelope)
    pcm = np.concatenate(notes)
    pcm = np.convolve(pcm, np.ones(8) / 8, mode="same")  # take the edge off
    return (pcm / np.max(np.abs(pcm)) * amplitude).astype(np.int16).tobytes()
