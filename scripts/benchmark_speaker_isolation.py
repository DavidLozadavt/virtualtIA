"""Banco de pruebas de aislamiento del hablante objetivo.

Mide lo único que importa para el reconocedor: **cuánta voz ajena sobrevive** al
pipeline y **cuánta voz propia se pierde por el camino**. Los dos números están
en tensión, así que ninguno significa nada por separado: un sistema que silencia
todo saca un rechazo perfecto y una transcripción vacía.

Escenas
-------
Cada escena reproduce una de las condiciones reportadas en las llamadas reales:
gente conversando cerca, televisión, música, dos personas hablando a la vez,
ruido continuo e impulsivo. El hablante objetivo siempre está en campo cercano
(seco, nivel alto, tilt espectral de proximidad) y los interferentes en campo
lejano (convolucionados con una respuesta de sala, filtrados por el aire y
atenuados), que es la diferencia física que existe de verdad en una llamada.

Las voces se sintetizan con el mismo motor de TTS que ya usa el proyecto
(`edge-tts`, varias voces en español) y se guardan en un corpus local. La razón
de generarlas en vez de grabarlas es que **hace falta la señal limpia de cada
hablante por separado**: sin esa referencia el aislamiento no se puede medir,
solo opinar sobre él. Y la razón de no usar voces sintetizadas a mano (tonos con
formantes) es que se midió: el detector neuronal del pipeline las rechaza (4.8 %
de tramas por encima del umbral), así que un banco construido sobre ellas mide
el detector, no el aislador.

Si el corpus no se puede generar (sin red o sin decodificador de MP3), el banco
cae a voces sintéticas y lo avisa: los números siguen siendo comparables entre
ejecuciones, pero no son extrapolables a una llamada real.

Métricas
--------
* `target_keep_db`   — energía a la salida / a la entrada en los tramos donde
                       **solo** habla el usuario. 0 dB es perfecto; por debajo
                       de −3 dB el pipeline se está comiendo al usuario.
* `bg_reject_db`     — la misma relación en los tramos donde **solo** hay voz o
                       ruido ajeno. Cuanto más alto, mejor; es lo que evita que
                       el reconocedor invente frases.
* `overlap_sisdr_db` — SI-SDR contra la voz limpia del usuario en los tramos de
                       habla simultánea, y su mejora respecto a la mezcla de
                       entrada. Es el único número que dice si el sistema aísla
                       de verdad o solo espera a que el otro se calle.
* `clean_sisdr_db`   — SI-SDR contra la voz limpia en los tramos limpios: mide
                       el daño (artefactos) que el proceso causa al usuario.
* `lsd_db`           — distorsión log-espectral del usuario en tramos limpios.

Uso
---
    python scripts/benchmark_speaker_isolation.py
    python scripts/benchmark_speaker_isolation.py --scene crosstalk --wav out/
    python scripts/benchmark_speaker_isolation.py --stages preprocess,voice_gate
"""

from __future__ import annotations

import argparse
import sys
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

RATE = 8000
EPS = 1e-12


# ────────────────────────────── síntesis de voz ──────────────────────────────


@dataclass(frozen=True)
class VoiceProfile:
    """Identidad acústica de un hablante sintético."""

    f0: float
    formants: tuple[tuple[float, float], ...]  # (frecuencia Hz, ancho Hz)
    speech_rate: float = 4.2  # sílabas por segundo

    @staticmethod
    def male() -> "VoiceProfile":
        return VoiceProfile(f0=112.0, formants=((520.0, 80.0), (1180.0, 110.0), (2500.0, 160.0)))

    @staticmethod
    def female() -> "VoiceProfile":
        return VoiceProfile(f0=205.0, formants=((650.0, 90.0), (1700.0, 130.0), (2900.0, 180.0)))

    @staticmethod
    def child() -> "VoiceProfile":
        return VoiceProfile(
            f0=268.0,
            formants=((780.0, 100.0), (2050.0, 150.0), (3200.0, 200.0)),
            speech_rate=5.0,
        )


def _formant_envelope(freqs: np.ndarray, profile: VoiceProfile) -> np.ndarray:
    """Ganancia por frecuencia de los formantes (resonancias tipo Lorentz)."""
    envelope = np.full(freqs.shape, 0.02, dtype=np.float64)
    for center, width in profile.formants:
        envelope += 1.0 / (1.0 + ((freqs - center) / width) ** 2)
    # Caída natural de la fuente glotal: −12 dB/octava.
    return envelope * (200.0 / np.maximum(freqs, 60.0)) ** 1.0


def _syllable_plan(
    duration: float, profile: VoiceProfile, rng: np.random.Generator
) -> list[tuple[float, float, str]]:
    """Secuencia (inicio, duración, tipo) de sílabas: sonora, sorda o pausa."""
    plan: list[tuple[float, float, str]] = []
    position = 0.0
    mean = 1.0 / max(profile.speech_rate, 0.5)
    while position < duration:
        kind = rng.choice(["voiced", "voiced", "voiced", "unvoiced", "pause"])
        length = float(np.clip(rng.normal(mean, mean * 0.25), mean * 0.4, mean * 2.0))
        if kind == "pause":
            length *= float(rng.uniform(1.0, 3.5))
        plan.append((position, min(length, duration - position), str(kind)))
        position += length
    return plan


def synth_voice(
    duration: float,
    profile: VoiceProfile,
    *,
    rate: int = RATE,
    seed: int = 0,
) -> np.ndarray:
    """Voz sintética con jitter de tono, formantes, sílabas y consonantes sordas.

    No pretende sonar humana; pretende **tener las propiedades que distinguen a
    una voz**: un único tono fundamental con sus armónicos, una envolvente que
    varía al ritmo silábico y tramos inarmónicos de fricativa. Un aislador que
    supere el banco con estas señales no lo hace por un atajo trivial.
    """
    rng = np.random.default_rng(seed)
    n = int(duration * rate)
    time = np.arange(n, dtype=np.float64) / rate

    # Contorno de f0: entonación lenta + jitter (micro-variación ciclo a ciclo).
    intonation = 1.0 + 0.07 * np.sin(2.0 * np.pi * 0.45 * time + rng.uniform(0, 6.28))
    jitter = 1.0 + 0.012 * np.convolve(
        rng.standard_normal(n), np.ones(64) / 64.0, mode="same"
    )
    f0_track = profile.f0 * intonation * jitter
    phase = 2.0 * np.pi * np.cumsum(f0_track) / rate

    voiced = np.zeros(n, dtype=np.float64)
    harmonics = int(3800.0 / profile.f0)
    for k in range(1, max(2, harmonics + 1)):
        freqs = k * f0_track
        amplitude = _formant_envelope(freqs, profile)
        amplitude[freqs > 3700.0] = 0.0  # el canal telefónico no lleva más
        voiced += amplitude * np.sin(k * phase + rng.uniform(0.0, 6.28))
    voiced /= max(float(np.max(np.abs(voiced))), EPS)

    # Fricativa: ruido de banda alta, deliberadamente inarmónico.
    noise = rng.standard_normal(n)
    fricative = noise - np.convolve(noise, np.ones(9) / 9.0, mode="same")
    fricative /= max(float(np.max(np.abs(fricative))), EPS)

    out = np.zeros(n, dtype=np.float64)
    for start, length, kind in _syllable_plan(duration, profile, rng):
        if kind == "pause":
            continue
        a, b = int(start * rate), int(min(start + length, duration) * rate)
        if b <= a:
            continue
        window = np.hanning(b - a) ** 0.5  # ataque y caída suaves, no clics
        if kind == "voiced":
            out[a:b] += voiced[a:b] * window * float(rng.uniform(0.7, 1.0))
        else:
            out[a:b] += fricative[a:b] * window * float(rng.uniform(0.12, 0.22))
    peak = float(np.max(np.abs(out)))
    return (out / peak if peak > EPS else out).astype(np.float32)


# ──────────────────────────── campo lejano y ruido ────────────────────────────


def _room_response(rate: int, rt60: float, drr_db: float, seed: int) -> np.ndarray:
    """Respuesta de sala: impulso directo + cola difusa con la relación pedida."""
    rng = np.random.default_rng(seed)
    length = int(max(rt60, 0.05) * rate)
    decay = np.exp(-6.9 * np.arange(length) / max(length, 1))
    late = rng.standard_normal(length) * decay
    late[: int(0.004 * rate)] = 0.0  # los primeros 4 ms son el sonido directo
    late /= max(float(np.sqrt(np.sum(late**2))), EPS)
    direct = 10.0 ** (drr_db / 20.0)
    response = np.zeros(length, dtype=np.float64)
    response[0] = direct
    return response + late


def far_field(
    signal: np.ndarray,
    *,
    rate: int = RATE,
    level_db: float = -14.0,
    rt60: float = 0.5,
    drr_db: float = -2.0,
    cutoff_hz: float = 2400.0,
    seed: int = 3,
) -> np.ndarray:
    """Lleva una señal al campo lejano: reverberación, filtrado del aire y nivel.

    Las tres transformaciones son las que separan físicamente a la persona que
    habla al teléfono de la que habla en la habitación, y son también las que
    **sobreviven a la normalización de nivel**: por eso el aislamiento no puede
    apoyarse solo en el volumen.
    """
    reverberant = np.convolve(signal.astype(np.float64), _room_response(rate, rt60, drr_db, seed))
    reverberant = reverberant[: signal.size]
    # Absorción del aire y de los obstáculos: paso-bajo de un polo.
    alpha = float(np.exp(-2.0 * np.pi * cutoff_hz / rate))
    filtered = np.empty_like(reverberant)
    state = 0.0
    for i, sample in enumerate(reverberant):
        state = (1.0 - alpha) * sample + alpha * state
        filtered[i] = state
    rms = float(np.sqrt(np.mean(filtered**2)))
    if rms <= EPS:
        return filtered.astype(np.float32)
    return (filtered / rms * 10.0 ** (level_db / 20.0)).astype(np.float32)


def synth_music(duration: float, *, rate: int = RATE, seed: int = 11) -> np.ndarray:
    """Acorde sostenido con vibrato y percusión: varias fundamentales a la vez."""
    rng = np.random.default_rng(seed)
    n = int(duration * rate)
    time = np.arange(n, dtype=np.float64) / rate
    out = np.zeros(n, dtype=np.float64)
    for root in (196.0, 246.9, 293.7):  # sol mayor
        vibrato = 1.0 + 0.004 * np.sin(2.0 * np.pi * 5.5 * time)
        for k in range(1, 12):
            if k * root > 3700.0:
                break
            out += (0.8**k) * np.sin(2.0 * np.pi * k * root * vibrato * time)
    for onset in np.arange(0.0, duration, 0.5):
        a = int(onset * rate)
        b = min(n, a + int(0.09 * rate))
        if b > a:
            out[a:b] += 0.5 * rng.standard_normal(b - a) * np.exp(
                -np.arange(b - a) / (0.02 * rate)
            )
    peak = float(np.max(np.abs(out)))
    return (out / peak if peak > EPS else out).astype(np.float32)


def synth_noise(
    duration: float, *, rate: int = RATE, impulsive: bool = False, seed: int = 5
) -> np.ndarray:
    """Ruido continuo tipo ventilador, con golpes opcionales."""
    rng = np.random.default_rng(seed)
    n = int(duration * rate)
    white = rng.standard_normal(n)
    pink = np.convolve(white, np.ones(24) / 24.0, mode="same")  # espectro inclinado
    out = pink / max(float(np.max(np.abs(pink))), EPS)
    if impulsive:
        for onset in rng.uniform(0.5, duration - 0.5, size=int(duration / 1.6)):
            a = int(onset * rate)
            b = min(n, a + int(0.12 * rate))
            burst = rng.standard_normal(b - a) * np.exp(-np.arange(b - a) / (0.015 * rate))
            out[a:b] += 3.0 * burst
    peak = float(np.max(np.abs(out)))
    return (out / peak if peak > EPS else out).astype(np.float32)


def _scale_to_dbfs(signal: np.ndarray, dbfs: float) -> np.ndarray:
    rms = float(np.sqrt(np.mean(np.square(signal, dtype=np.float64))))
    if rms <= EPS:
        return signal
    return (signal * (10.0 ** (dbfs / 20.0) / rms)).astype(np.float32)


# ─────────────────────────── corpus de voz real (TTS) ───────────────────────────

CORPUS_DIR = PROJECT_ROOT / "scratch" / "voice_corpus"

# Un hablante por papel. Las voces son de países distintos a propósito: timbres,
# ritmos y tonos medios separados, como en una escena real.
CORPUS_VOICES: dict[str, tuple[str, str]] = {
    "target": (
        "es-CO-GonzaloNeural",
        "Buenas tardes, necesito un taxi para la carrera diecisiete número ocho "
        "guion cincuenta y cinco, en el barrio La Esmeralda. Sí, para ahora mismo, "
        "por favor. Voy hasta el centro comercial Campanario. Mi nombre es Miguel "
        "y el teléfono es el mismo desde el que estoy llamando. Muchas gracias.",
    ),
    "woman": (
        "es-CO-SalomeNeural",
        "Oye, ¿tú viste dónde dejé las llaves del carro? Es que las puse encima de "
        "la mesa del comedor y ya no están. Mira a ver si están en el bolso azul, "
        "porque yo salí de afán y no me acuerdo de nada.",
    ),
    "man2": (
        "es-MX-JorgeNeural",
        "No, mira, lo que pasa es que el pedido llegó incompleto otra vez. Faltaron "
        "dos cajas y nadie avisó. Hay que llamar al proveedor mañana temprano para "
        "que lo resuelvan antes del mediodía.",
    ),
    "announcer": (
        "es-ES-AlvaroNeural",
        "Continuamos con la información del tiempo. Se esperan lluvias dispersas "
        "durante la tarde y una temperatura máxima de veintidós grados. En el "
        "capítulo deportivo, el partido de esta noche comienza a las ocho.",
    ),
    "child": (
        "es-MX-DaliaNeural",
        "¡Mamá, mamá! Es que él me quitó el juguete y no me lo quiere devolver. "
        "Yo lo tenía primero y no vale, dile que me lo dé.",
    ),
    "woman2": (
        "es-AR-ElenaNeural",
        "Bueno, entonces quedamos así: nos vemos el jueves a las cuatro en la "
        "oficina de siempre y llevamos los papeles firmados. Avisame si cambia algo.",
    ),
}


def _decode_mp3(data: bytes, rate: int) -> np.ndarray:
    """MP3 → float32 mono a `rate`. Usa PyAV si está; si no, ffmpeg del sistema."""
    import io

    try:
        import av  # type: ignore

        with av.open(io.BytesIO(data)) as container:
            resampler = av.AudioResampler(format="s16", layout="mono", rate=rate)
            chunks: list[np.ndarray] = []
            for frame in container.decode(audio=0):
                for piece in resampler.resample(frame):
                    chunks.append(piece.to_ndarray().reshape(-1))
            for piece in resampler.resample(None):  # vaciar el remuestreador
                chunks.append(piece.to_ndarray().reshape(-1))
        raw = np.concatenate(chunks) if chunks else np.zeros(0, dtype=np.int16)
        return (raw.astype(np.float32) / 32768.0)
    except ImportError:
        pass

    import shutil
    import subprocess

    binary = shutil.which("ffmpeg")
    if binary is None:
        raise RuntimeError("ni PyAV ni ffmpeg disponibles para decodificar MP3")
    process = subprocess.run(
        [binary, "-hide_banner", "-loglevel", "error", "-i", "pipe:0",
         "-f", "s16le", "-ac", "1", "-ar", str(rate), "pipe:1"],
        input=data,
        capture_output=True,
        check=True,
    )
    return np.frombuffer(process.stdout, dtype="<i2").astype(np.float32) / 32768.0


def _synthesize(voice: str, text: str, rate: int) -> np.ndarray:
    import asyncio

    import edge_tts  # type: ignore

    async def render() -> bytes:
        buffer = bytearray()
        # Resolutor de DNS en hilos: el asíncrono (aiodns/c-ares) falla en
        # algunos Windows y deja el corpus sin generar por una razón que no
        # tiene nada que ver con el audio.
        connector = None
        try:
            import aiohttp
            from aiohttp.resolver import ThreadedResolver

            connector = aiohttp.TCPConnector(resolver=ThreadedResolver())
        except Exception:  # noqa: BLE001 — sin aiohttp, edge-tts usa el suyo
            connector = None
        try:
            communicate = edge_tts.Communicate(text, voice, connector=connector)
        except TypeError:  # versión de edge-tts sin `connector`
            communicate = edge_tts.Communicate(text, voice)
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                buffer.extend(chunk["data"])
        return bytes(buffer)

    return _decode_mp3(asyncio.run(render()), rate)


def ensure_corpus(
    directory: Path = CORPUS_DIR, *, rate: int = RATE, force: bool = False
) -> dict[str, np.ndarray]:
    """Devuelve {papel: voz limpia a `rate`}, generándola la primera vez.

    El corpus se cachea en disco: generarlo cuesta una llamada de red por voz y
    no cambia entre ejecuciones, así que el banco debe ser reproducible y rápido
    a partir de la segunda vez.
    """
    directory.mkdir(parents=True, exist_ok=True)
    corpus: dict[str, np.ndarray] = {}
    for role, (voice, text) in CORPUS_VOICES.items():
        path = directory / f"{role}.wav"
        if path.is_file() and not force:
            with wave.open(str(path), "rb") as handle:
                raw = handle.readframes(handle.getnframes())
                stored_rate = handle.getframerate()
            samples = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
            if stored_rate == rate and samples.size:
                corpus[role] = samples
                continue
        samples = _synthesize(voice, text, rate)
        peak = float(np.max(np.abs(samples))) if samples.size else 0.0
        if peak > EPS:
            samples = samples / peak * 0.9
        _write_wav(path, samples, rate)
        corpus[role] = samples.astype(np.float32)
    return corpus


def _trim_silence(signal: np.ndarray, rate: int, threshold_db: float = -55.0) -> np.ndarray:
    """Recorta el silencio de los extremos (el TTS deja cola al final)."""
    if signal.size == 0:
        return signal
    level = _envelope_db(signal, max(1, int(0.02 * rate)))
    active = np.flatnonzero(level > threshold_db)
    if active.size == 0:
        return signal
    return signal[active[0] : active[-1] + 1]


def _fill(signal: np.ndarray, duration: float, rate: int, *, seed: int) -> np.ndarray:
    """Repite una locución hasta cubrir `duration`, con pausas naturales entre medias."""
    rng = np.random.default_rng(seed)
    trimmed = _trim_silence(signal, rate)
    if trimmed.size == 0:
        return np.zeros(int(duration * rate), dtype=np.float32)
    total = int(duration * rate)
    out = np.zeros(total, dtype=np.float32)
    position = int(rng.uniform(0.0, 0.4) * rate)
    while position < total:
        gap = int(rng.uniform(0.35, 0.9) * rate)
        piece = trimmed[: total - position]
        out[position : position + piece.size] += piece
        position += piece.size + gap
    return out


# ───────────────────────────────── escenas ─────────────────────────────────


@dataclass
class Scene:
    """Mezcla con las componentes separadas: sin ellas no se puede medir nada."""

    name: str
    target: np.ndarray  # voz limpia del usuario, tal como entra al micrófono
    interference: np.ndarray  # todo lo demás, ya en campo lejano
    description: str

    @property
    def mixture(self) -> np.ndarray:
        return (self.target + self.interference).astype(np.float32)


_CORPUS: Optional[dict[str, np.ndarray]] = None

_FALLBACK_PROFILES: dict[str, VoiceProfile] = {
    "target": VoiceProfile.male(),
    "woman": VoiceProfile.female(),
    "man2": VoiceProfile(f0=98.0, formants=((480.0, 75.0), (1080.0, 100.0), (2400.0, 150.0))),
    "announcer": VoiceProfile(f0=128.0, formants=((560.0, 85.0), (1300.0, 115.0), (2600.0, 165.0))),
    "child": VoiceProfile.child(),
    "woman2": VoiceProfile(f0=190.0, formants=((620.0, 88.0), (1620.0, 125.0), (2850.0, 175.0))),
}


def use_corpus(corpus: Optional[dict[str, np.ndarray]]) -> None:
    """Fija el corpus de voz real que usarán las escenas (None = sintético)."""
    global _CORPUS
    _CORPUS = corpus


def voice(role: str, duration: float, *, seed: int = 0, rate: int = RATE) -> np.ndarray:
    """Locución continua de `role` durante `duration` segundos."""
    if _CORPUS is not None and role in _CORPUS:
        return _fill(_CORPUS[role], duration, rate, seed=seed)
    return synth_voice(duration, _FALLBACK_PROFILES[role], rate=rate, seed=seed)


def _target(duration: float, seed: int = 1) -> np.ndarray:
    """Usuario: campo cercano, seco, nivel de conversación telefónica."""
    return _scale_to_dbfs(voice("target", duration, seed=seed), -20.0)


SceneBuilder = Callable[[float], Scene]


def scene_quiet(duration: float) -> Scene:
    target = _target(duration)
    return Scene(
        "quiet",
        target,
        np.zeros_like(target),
        "solo el usuario (control: el pipeline no debe dañarlo)",
    )


def scene_near_conversation(duration: float) -> Scene:
    target = _target(duration)
    other = voice("woman", duration, seed=21)
    return Scene(
        "near_conversation",
        target,
        far_field(other, level_db=-32.0, rt60=0.45, drr_db=0.0, cutoff_hz=2800.0, seed=7),
        "una persona conversando a un par de metros",
    )


def scene_tv(duration: float) -> Scene:
    target = _target(duration)
    announcer = voice("announcer", duration, seed=31)
    bed = synth_music(duration, seed=13)
    tv = _scale_to_dbfs(announcer, -6.0) + _scale_to_dbfs(bed, -12.0)
    return Scene(
        "tv",
        target,
        far_field(tv, level_db=-30.0, rt60=0.55, drr_db=-3.0, cutoff_hz=2200.0, seed=8),
        "televisión encendida (locutor + música)",
    )


def scene_music(duration: float) -> Scene:
    target = _target(duration)
    return Scene(
        "music",
        target,
        far_field(
            synth_music(duration, seed=17),
            level_db=-28.0,
            rt60=0.5,
            drr_db=-2.0,
            cutoff_hz=3000.0,
            seed=9,
        ),
        "música de fondo",
    )


def scene_crosstalk(duration: float) -> Scene:
    """Dos personas hablando **a la vez**: el caso que no resuelve esperar turnos."""
    target = _target(duration)
    other = voice("woman", duration, seed=41)
    return Scene(
        "crosstalk",
        target,
        far_field(other, level_db=-26.0, rt60=0.4, drr_db=1.0, cutoff_hz=3000.0, seed=10),
        "otra persona hablando simultáneamente, cerca",
    )


def scene_babble(duration: float) -> Scene:
    target = _target(duration)
    crowd = np.zeros(int(duration * RATE), dtype=np.float32)
    for index, role in enumerate(("woman", "man2", "child", "woman2", "announcer", "woman")):
        crowd = crowd + voice(role, duration, seed=50 + index)
    return Scene(
        "babble",
        target,
        far_field(crowd, level_db=-30.0, rt60=0.7, drr_db=-6.0, cutoff_hz=2000.0, seed=12),
        "restaurante / centro comercial (varias voces a la vez)",
    )


def scene_noisy(duration: float) -> Scene:
    target = _target(duration)
    continuous = _scale_to_dbfs(synth_noise(duration, seed=61), -34.0)
    impulses = _scale_to_dbfs(synth_noise(duration, impulsive=True, seed=62), -28.0)
    return Scene(
        "noisy",
        target,
        (continuous + impulses).astype(np.float32),
        "ruido continuo (ventilador) + golpes impulsivos",
    )


SCENES: dict[str, SceneBuilder] = {
    "quiet": scene_quiet,
    "near_conversation": scene_near_conversation,
    "tv": scene_tv,
    "music": scene_music,
    "crosstalk": scene_crosstalk,
    "babble": scene_babble,
    "noisy": scene_noisy,
}


# ───────────────────────────────── métricas ─────────────────────────────────


def _envelope_db(signal: np.ndarray, window: int) -> np.ndarray:
    """Nivel en dBFS por muestra, integrado sobre una ventana silábica."""
    power = np.convolve(np.square(signal, dtype=np.float64), np.ones(window) / window, mode="same")
    return 10.0 * np.log10(np.maximum(power, EPS))


def _align(output: np.ndarray, reference: np.ndarray) -> np.ndarray:
    """Compensa el retardo estructural del pipeline antes de comparar."""
    from services.audio.dsp import gcc_phat

    size = min(output.size, reference.size)
    lag, _ = gcc_phat(output[:size], reference[:size], max_lag=RATE // 2)
    if lag > 0:
        shifted = np.concatenate((output[lag:], np.zeros(lag, dtype=np.float32)))
    elif lag < 0:
        shifted = np.concatenate((np.zeros(-lag, dtype=np.float32), output[:lag]))
    else:
        shifted = output
    return shifted[:size].astype(np.float32)


def _si_sdr(estimate: np.ndarray, reference: np.ndarray) -> float:
    """SI-SDR: cuánto de la salida es la referencia y cuánto es cualquier otra cosa."""
    if estimate.size == 0 or float(np.sum(reference**2)) <= EPS:
        return float("nan")
    est = estimate.astype(np.float64) - np.mean(estimate)
    ref = reference.astype(np.float64) - np.mean(reference)
    projection = ref * (float(np.dot(est, ref)) / float(np.dot(ref, ref) + EPS))
    noise = est - projection
    return 10.0 * float(
        np.log10((np.sum(projection**2) + EPS) / (np.sum(noise**2) + EPS))
    )


def _energy_db(signal: np.ndarray, mask: np.ndarray) -> float:
    if not np.any(mask):
        return float("nan")
    return 10.0 * float(np.log10(np.mean(np.square(signal[mask], dtype=np.float64)) + EPS))


def _projection_gain_db(
    output: np.ndarray, reference: np.ndarray, mask: np.ndarray
) -> float:
    """Ganancia aplicada a la componente `reference` dentro de `output`, en dB.

    Es la proyección de mínimos cuadrados de la salida sobre la voz limpia del
    usuario: mide **cuánto sobrevive el usuario** aunque a su lado suene otra
    cosa, que es justo lo que una diferencia de energías no sabe separar. 0 dB
    significa intacto; −6 dB significa que el pipeline se comió la mitad.
    """
    if not np.any(mask):
        return float("nan")
    out = output[mask].astype(np.float64)
    ref = reference[mask].astype(np.float64)
    denominator = float(np.dot(ref, ref))
    if denominator <= EPS:
        return float("nan")
    return 20.0 * float(np.log10(abs(float(np.dot(out, ref)) / denominator) + EPS))


def _log_spectral_distance(estimate: np.ndarray, reference: np.ndarray) -> float:
    """Distancia log-espectral media (dB): mide artefactos, no nivel."""
    if estimate.size < 256 or reference.size < 256:
        return float("nan")
    frame, hop = 256, 128
    values = []
    for start in range(0, min(estimate.size, reference.size) - frame, hop):
        a = np.abs(np.fft.rfft(estimate[start : start + frame] * np.hanning(frame)))
        b = np.abs(np.fft.rfft(reference[start : start + frame] * np.hanning(frame)))
        if float(np.mean(b)) <= 1e-6:
            continue
        diff = 20.0 * (np.log10(a + 1e-8) - np.log10(b + 1e-8))
        values.append(float(np.sqrt(np.mean(diff**2))))
    return float(np.mean(values)) if values else float("nan")


@dataclass
class Result:
    scene: str
    target_keep_db: float
    bg_reject_db: float
    clean_sisdr_db: float
    overlap_sisdr_db: float
    overlap_sisdr_gain_db: float
    lsd_db: float
    notes: dict


def evaluate(scene: Scene, *, stages: Optional[str] = None, rate: int = RATE) -> Result:
    """Pasa la escena por el pipeline real y mide el resultado."""
    from services.audio import CaptureEnhancer
    from services.audio.frames import float_to_pcm16, pcm16_to_float

    enhancer = CaptureEnhancer(rate=rate, enabled=True, stages=stages)
    mixture = scene.mixture
    block = rate // 50  # 20 ms, el tamaño real del transporte
    chunks: list[np.ndarray] = []
    for start in range(0, mixture.size, block):
        piece = mixture[start : start + block]
        processed, _ = enhancer.process(
            float_to_pcm16(piece), timestamp=(start + piece.size) / rate
        )
        chunks.append(pcm16_to_float(processed))
    output = np.concatenate(chunks) if chunks else np.zeros(0, dtype=np.float32)

    aligned = _align(output, mixture)
    size = aligned.size
    target = scene.target[:size]
    interference = scene.interference[:size]
    mix = mixture[:size]

    # Las regiones se definen **relativas a cada fuente**, no con un umbral fijo:
    # un fondo continuo (ventilador, música) nunca baja de un umbral absoluto y
    # dejaría todas las regiones vacías.
    window = int(0.2 * rate)
    target_db = _envelope_db(target, window)
    interference_db = _envelope_db(interference, window)
    target_on = target_db > max(float(np.max(target_db)) - 30.0, -60.0)
    interference_on = (
        interference_db > max(float(np.max(interference_db)) - 30.0, -70.0)
        if float(np.max(np.abs(interference))) > EPS
        else np.zeros_like(target_on)
    )

    clean = target_on & (target_db > interference_db + 15.0)
    background = interference_on & ~target_on
    overlap = target_on & ~clean

    keep = _projection_gain_db(aligned, target, target_on)
    reject = _energy_db(mix, background) - _energy_db(aligned, background)
    clean_sisdr = _si_sdr(aligned[clean], target[clean]) if np.any(clean) else float("nan")
    overlap_sisdr = (
        _si_sdr(aligned[overlap], target[overlap]) if np.any(overlap) else float("nan")
    )
    overlap_input = (
        _si_sdr(mix[overlap], target[overlap]) if np.any(overlap) else float("nan")
    )
    lsd = _log_spectral_distance(aligned[clean], target[clean]) if np.any(clean) else float("nan")

    return Result(
        scene=scene.name,
        target_keep_db=keep,
        bg_reject_db=reject,
        clean_sisdr_db=clean_sisdr,
        overlap_sisdr_db=overlap_sisdr,
        overlap_sisdr_gain_db=overlap_sisdr - overlap_input,
        lsd_db=lsd,
        notes={
            "clean_frac": round(float(np.mean(clean)), 3),
            "overlap_frac": round(float(np.mean(overlap)), 3),
            "background_frac": round(float(np.mean(background)), 3),
            "stages": ",".join(enhancer.pipeline.active_stage_names)
            if enhancer.pipeline
            else "",
            "latency_ms": enhancer.latency_ms,
        },
    )


def _write_wav(path: Path, signal: np.ndarray, rate: int = RATE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(
            np.clip(signal * 32768.0, -32768, 32767).astype("<i2").tobytes()
        )


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scene", choices=sorted(SCENES), help="una sola escena")
    parser.add_argument("--duration", type=float, default=14.0)
    parser.add_argument("--stages", default=None, help="lista de etapas a evaluar")
    parser.add_argument("--wav", default=None, help="directorio donde volcar los WAV")
    parser.add_argument("--corpus", default=str(CORPUS_DIR), help="caché de voces reales")
    parser.add_argument(
        "--synthetic",
        action="store_true",
        help="forzar voces sintéticas (el detector neuronal las rechaza: solo para depurar)",
    )
    parser.add_argument("--refresh-corpus", action="store_true", help="regenerar las voces")
    args = parser.parse_args(argv)

    if args.synthetic:
        print("AVISO: voces sintéticas; los números no son extrapolables a una llamada real.\n")
    else:
        try:
            use_corpus(
                ensure_corpus(Path(args.corpus), force=args.refresh_corpus)
            )
        except Exception as error:  # noqa: BLE001 — sin red o sin decodificador
            print(
                f"AVISO: no se pudo preparar el corpus de voz real ({error}); "
                "se usan voces sintéticas y los números NO son extrapolables.\n"
            )

    names = [args.scene] if args.scene else list(SCENES)
    results: list[Result] = []
    for name in names:
        scene = SCENES[name](args.duration)
        result = evaluate(scene, stages=args.stages)
        results.append(result)
        if args.wav:
            base = Path(args.wav)
            _write_wav(base / f"{name}_mixture.wav", scene.mixture)
            _write_wav(base / f"{name}_target.wav", scene.target)

    header = (
        f"{'escena':<19}{'usuario':>9}{'rechazo':>9}{'limpio':>9}"
        f"{'solape':>9}{'ganancia':>10}{'LSD':>8}"
    )
    print(header)
    print(
        f"{'':19}{'dB':>9}{'dB':>9}{'SI-SDR':>9}{'SI-SDR':>9}{'dB':>10}{'dB':>8}"
    )
    print("-" * len(header))
    for result in results:
        print(
            f"{result.scene:<19}"
            f"{result.target_keep_db:>9.1f}"
            f"{result.bg_reject_db:>9.1f}"
            f"{result.clean_sisdr_db:>9.1f}"
            f"{result.overlap_sisdr_db:>9.1f}"
            f"{result.overlap_sisdr_gain_db:>10.1f}"
            f"{result.lsd_db:>8.1f}"
        )
    if results:
        print(f"\netapas: {results[0].notes['stages']}")
        print(f"latencia: {results[0].notes['latency_ms']} ms")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
