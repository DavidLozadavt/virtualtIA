"""AEC: estimación de retardo, atenuación de eco y guardas."""

import numpy as np

from services.voice.aec import SAMPLE_RATE, EchoCanceller


def _speechlike(seconds: float, seed: int = 7) -> np.ndarray:
    """Señal con espectro tipo voz (ruido filtrado) a ~-12 dBFS."""
    rng = np.random.default_rng(seed)
    n = int(SAMPLE_RATE * seconds)
    x = rng.normal(0.0, 1.0, n)
    # Suavizado (pole simple) para concentrar energía en bajas frecuencias.
    y = np.empty_like(x)
    acc = 0.0
    for i in range(n):
        acc = 0.92 * acc + x[i]
        y[i] = acc
    y = y / np.max(np.abs(y)) * 8000.0
    return y.astype(np.int16).astype(np.float64)


def _rms(x: np.ndarray) -> float:
    return float(np.sqrt(np.mean(x**2))) if x.size else 0.0


def test_passthrough_when_far_silent():
    aec = EchoCanceller(taps=64)
    frame = (np.ones(160) * 1000).astype(np.int16).tobytes()
    assert aec.process_near(frame) == frame


def test_echo_attenuation_with_delay():
    aec = EchoCanceller(taps=128)
    far = _speechlike(4.0)
    delay = 400  # 50 ms
    echo = np.zeros_like(far)
    echo[delay:] = 0.6 * far[:-delay]

    frame = 160
    out_rms: list[float] = []
    in_rms: list[float] = []
    for i in range(0, far.size - frame, frame):
        aec.add_far(far[i : i + frame].astype(np.int16).tobytes())
        near = echo[i : i + frame]
        residual = aec.process_near(near.astype(np.int16).tobytes())
        res = np.frombuffer(residual, dtype=np.int16).astype(np.float64)
        in_rms.append(_rms(near))
        out_rms.append(_rms(res))

    # Tras converger (última cuarta parte), el eco debe atenuarse >= 6 dB.
    tail = slice(int(len(out_rms) * 0.75), None)
    attenuation_db = 20.0 * np.log10(
        (np.mean(in_rms[tail]) + 1e-9) / (np.mean(out_rms[tail]) + 1e-9)
    )
    assert attenuation_db >= 6.0, f"atenuación insuficiente: {attenuation_db:.1f} dB"
    assert aec._delay_locked
    assert abs(aec._delay - delay) <= 40


def test_double_talk_freezes_adaptation_not_voice():
    """La voz del usuario (sin correlación con el far) debe sobrevivir."""
    aec = EchoCanceller(taps=64)
    far = _speechlike(2.0, seed=1)
    user = _speechlike(2.0, seed=2) * 0.9

    frame = 160
    survived: list[float] = []
    original: list[float] = []
    for i in range(0, far.size - frame, frame):
        aec.add_far(far[i : i + frame].astype(np.int16).tobytes())
        near = user[i : i + frame]  # solo voz del usuario, sin eco
        res = np.frombuffer(
            aec.process_near(near.astype(np.int16).tobytes()), dtype=np.int16
        ).astype(np.float64)
        original.append(_rms(near))
        survived.append(_rms(res))

    # La voz no debe atenuarse más de ~3 dB.
    loss_db = 20.0 * np.log10(
        (np.mean(original) + 1e-9) / (np.mean(survived) + 1e-9)
    )
    assert loss_db < 3.0, f"voz del usuario dañada: {loss_db:.1f} dB"
