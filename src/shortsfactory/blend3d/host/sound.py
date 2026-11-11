# blend3d/host/sound.py — procedural soundtrack engine (numpy → in-memory mix).
# Proven in two productions; events are plain dicts so any planner can emit them.
# No file I/O here: callers write the returned array to disk (e.g. via `wave`).
import math
import numpy as np

NOTE_NAMES = ['C', 'Cs', 'D', 'Ds', 'E', 'F', 'Fs', 'G', 'Gs', 'A', 'As', 'B']

def note_freq(name) -> float:
    """'C4' -> 261.63 Hz (A4 = 440). Raw numbers pass through as Hz."""
    if isinstance(name, (int, float)):
        return float(name)
    octave = int(name[-1])
    idx = NOTE_NAMES.index(name[:-1])
    return 440.0 * 2 ** ((idx - 9) / 12 + (octave - 4))

def bell(freq, dur, bright=1.0, sr=48000):
    """Music-box bell: harmonics with exponential decay."""
    n = int(dur * sr)
    t = np.arange(n) / sr
    s = np.zeros(n)
    for mul, amp in ((1.0, 1.0), (2.0, 0.4 * bright), (3.01, 0.15 * bright), (4.16, 0.06 * bright)):
        s += amp * np.sin(2 * math.pi * freq * mul * t)
    e = np.exp(-t * (3.0 / max(0.4, dur * 0.6)))
    na = max(1, int(0.004 * sr))
    e[:na] *= np.linspace(0.0, 1.0, na)
    return s * e

def soft_pad(freqs, dur, amp=0.05, sr=48000):
    n = int(dur * sr)
    t = np.arange(n) / sr
    s = np.zeros(n)
    e = np.ones(n)
    na = min(int(0.4 * sr), n // 2)
    nr = min(int(0.9 * sr), n - na)
    if na > 0: e[:na] *= np.linspace(0, 1, na) ** 2
    if nr > 0: e[n - nr:] *= np.linspace(1, 0, nr) ** 2
    for f in freqs:
        s += np.sin(2 * math.pi * f * t) + 0.3 * np.sin(2 * math.pi * f * 2 * t + 1.1)
    return s * e * amp / max(1, len(freqs))

def bass(freq, dur, amp=0.11, sr=48000):
    n = int(dur * sr)
    t = np.arange(n) / sr
    s = np.sin(2 * math.pi * freq * t) + 0.3 * np.sin(2 * math.pi * freq * 2 * t)
    e = np.exp(-t * 2.0)
    na = max(1, int(0.01 * sr))
    e[:na] *= np.linspace(0.0, 1.0, na)
    return s * e * amp

def noise_band(dur, lo, hi, sr=48000, seed=0):
    rng = np.random.RandomState(seed)
    n = int(dur * sr)
    x = rng.randn(n)
    X = np.fft.rfft(x)
    f = np.fft.rfftfreq(n, 1.0 / sr)
    mask = np.clip((f - lo) / 300.0, 0, 1) * np.clip((hi - f) / 800.0, 0, 1)
    return np.fft.irfft(X * mask, n)

def sweep_noise(dur, sr=48000, seed=0):
    """Noise that darkens over time (whoosh)."""
    rng = np.random.RandomState(seed)
    n = int(dur * sr)
    x = rng.randn(n)
    out = np.zeros(n)
    for i in range(8):
        a, b = i * n // 8, (i + 1) * n // 8
        k = int(sr * 0.0004 + (sr * 0.005) * (i / 7) ** 1.5)
        w = np.hanning(max(3, k)); w /= w.sum()
        out[a:b] = np.convolve(x[a:b], w, mode='same')
    return out

class Mixer:
    """Accumulates mono signals into a stereo timeline. Times in seconds."""
    def __init__(self, duration, sr=48000):
        self.sr = sr
        self.n = int(duration * sr)
        self.mix = np.zeros((self.n, 2))

    def place(self, sig, at, gain=1.0, pan=0.0):
        i0 = int(at * self.sr)
        if i0 >= self.n or i0 < 0: return
        L = min(len(sig), self.n - i0)
        th = (max(-1.0, min(1.0, pan)) + 1.0) * math.pi / 4.0
        self.mix[i0:i0 + L, 0] += sig[:L] * gain * math.cos(th)
        self.mix[i0:i0 + L, 1] += sig[:L] * gain * math.sin(th)

    def master(self, peak=0.85, fade_in=0.05, fade_out=0.0, highpass=32):
        X = np.fft.rfft(self.mix, axis=0)
        f = np.fft.rfftfreq(self.n, 1.0 / self.sr)
        X = X * np.clip((f - highpass) / 20.0, 0, 1)[:, None]
        mix = np.fft.irfft(X, self.n, axis=0)
        p = np.abs(mix).max()
        if p > 0: mix = mix / p * peak
        fi = int(fade_in * self.sr)
        if fi: mix[:fi] *= np.linspace(0, 1, fi)[:, None]
        fo = int(fade_out * self.sr)
        if fo: mix[-fo:] *= np.linspace(1, 0, fo)[:, None]
        self.mix = mix
        return mix

def render_events(events, duration, sr=48000, peak=0.85):
    """events: list of dicts:
      {'t', 'kind': 'bell'|'pad'|'bass', 'freq'|'freqs', 'dur', 'gain', 'pan', 'bright'}
    Returns (N,2) float array."""
    m = Mixer(duration, sr)
    for ev in events:
        k = ev.get('kind')
        if k == 'bell':
            m.place(bell(note_freq(ev['freq']), ev.get('dur', 1.4), ev.get('bright', 1.0), sr),
                    ev['t'], ev.get('gain', 0.15), ev.get('pan', 0.0))
        elif k == 'pad':
            m.place(soft_pad([note_freq(x) for x in ev['freqs']], ev.get('dur', 4.0),
                             ev.get('gain', 0.06), sr), ev['t'], pan=ev.get('pan', 0.0))
        elif k == 'bass':
            m.place(bass(note_freq(ev['freq']), ev.get('dur', 2.0), ev.get('gain', 0.11), sr),
                    ev['t'], pan=ev.get('pan', 0.0))
    return m.master(peak=peak)

def events_from_timings(timings, chords=None):
    """Convert a timings dict (exported by the animation driver) into render_events
    event dicts: pickaxe hits → metallic bells, footsteps → implied by walks
    (returned separately via 'walks'), diamond pops → ascending sparkle arps.
    `chords`: optional list of (t, root, kind) to add bass+arpeggio bars.
    This is the sound-side contract of the animation↔audio integration."""
    events = []
    for th, pan, p in timings.get('pickaxe_hits', []):
        events.append({'t': th, 'kind': 'bell', 'freq': 'Cs6', 'dur': 0.22,
                       'bright': 1.2, 'gain': 0.12, 'pan': pan})
    for t0, t1, pan in timings.get('walks', []):
        events.append({'t': t0, 'kind': 'pad', 'freqs': ['C3'], 'dur': max(0.5, t1 - t0),
                       'gain': 0.03, 'pan': pan})
    for base in timings.get('diamond_pops', []):
        for i, nm in enumerate(('C6', 'E6', 'G6', 'C7')):
            events.append({'t': base + 0.06 * i, 'kind': 'bell', 'freq': nm, 'dur': 1.2,
                           'bright': 1.2, 'gain': 0.09, 'pan': -0.2 + 0.13 * i})
    for t, nm in (('final_chime', 'G6'),):
        if timings.get(t) is not None:
            events.append({'t': timings[t], 'kind': 'bell', 'freq': nm, 'dur': 2.4,
                           'bright': 1.0, 'gain': 0.09, 'pan': 0.15})
    for t0, root, kind in (chords or []):
        root_f = note_freq(root)
        events.append({'t': t0, 'kind': 'bass', 'freq': root[:-1] + str(int(root[-1]) - 1),
                       'dur': 2.0, 'gain': 0.1})
        for k, semi in enumerate((0, 7, 12)):
            events.append({'t': t0 + k * 0.34, 'kind': 'bell',
                           'freq': _hz(root_f, semi), 'dur': 1.4,
                           'gain': 0.15, 'pan': -0.3 + 0.12 * k})
    return events

def _hz(base, semitone):
    return base * 2 ** (semitone / 12.0)

def pcm16(mix):
    """(N,2) float in [-1,1] → interleaved 16-bit little-endian bytes."""
    return (np.clip(mix, -1, 1) * 32767).astype('<i2').tobytes()
