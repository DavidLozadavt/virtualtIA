"""Almacén de audio compartido para playback vía ESL uuid_broadcast."""

import wave

from services.voice.audio_file_store import AudioFileStore


def _make_store(tmp_path, monkeypatch):
    from core.config import settings

    monkeypatch.setattr(settings, "FREESWITCH_TTS_SHARED_DIR", str(tmp_path))
    monkeypatch.setattr(settings, "FREESWITCH_TTS_CONTAINER_DIR", "/tmp/lyra-tts")
    return AudioFileStore()


def test_save_pcm_writes_valid_wav(tmp_path, monkeypatch):
    store = _make_store(tmp_path, monkeypatch)
    pcm = (b"\x00\x01" * 8000)  # 1 s @ 8kHz 16-bit mono
    audio_id, container_path, duration = store.save_pcm(pcm, call_uuid="call-1")

    assert container_path == f"/tmp/lyra-tts/{audio_id}.wav"
    assert abs(duration - 1.0) < 1e-6

    local_path = tmp_path / f"{audio_id}.wav"
    assert local_path.is_file()
    with wave.open(str(local_path), "rb") as wf:
        assert wf.getframerate() == 8000
        assert wf.getnchannels() == 1
        assert wf.getsampwidth() == 2


def test_save_pcm_unique_id_per_call(tmp_path, monkeypatch):
    store = _make_store(tmp_path, monkeypatch)
    id1, _, _ = store.save_pcm(b"\x00\x01" * 100, call_uuid="same-uuid")
    id2, _, _ = store.save_pcm(b"\x00\x01" * 100, call_uuid="same-uuid")
    assert id1 != id2  # nunca reusar nombre de archivo entre turnos


def test_prune_removes_oldest_beyond_max(tmp_path, monkeypatch):
    import services.voice.audio_file_store as afs

    monkeypatch.setattr(afs, "_MAX_FILES", 3)
    store = _make_store(tmp_path, monkeypatch)
    for i in range(5):
        store.save_pcm(b"\x00\x01" * 10, call_uuid=f"c{i}")
    assert len(list(tmp_path.glob("*.wav"))) == 3
