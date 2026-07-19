"""Grabadora server-side: mezcla near/far y escritura WAV."""

import wave

import numpy as np

from services.voice.recorder import CallRecorder


def test_mix_and_write(tmp_path, monkeypatch):
    from core.config import settings

    monkeypatch.setattr(settings, "FREESWITCH_RECORDINGS_DIR", str(tmp_path))

    rec = CallRecorder("test-uuid-1")
    near = (np.ones(8000) * 1000).astype(np.int16)  # 1 s de usuario
    rec.add_user_audio(near[:4000].tobytes())
    # El bot habla anclado al cursor actual (0.5 s).
    bot = (np.ones(4000) * -500).astype(np.int16)
    rec.add_bot_audio(bot.tobytes())
    rec.add_user_audio(near[4000:].tobytes())

    path = rec.write_wav()
    assert path is not None and path.name == "test-uuid-1.wav"

    with wave.open(str(path), "rb") as wf:
        assert wf.getframerate() == 8000
        assert wf.getnchannels() == 1
        pcm = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16)

    assert pcm.size == 8000  # duración = pista near (el bot cabe dentro)
    assert int(pcm[0]) == 1000            # solo usuario
    assert int(pcm[4100]) == 500          # 1000 + (-500) mezclados
    assert int(pcm[-1]) == 500


def test_empty_recorder_writes_nothing(tmp_path, monkeypatch):
    from core.config import settings

    monkeypatch.setattr(settings, "FREESWITCH_RECORDINGS_DIR", str(tmp_path))
    assert CallRecorder("empty").write_wav() is None
