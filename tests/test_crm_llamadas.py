"""Llamadas grabadas del CRM por FreeSWITCH (services/telephony/crm_calls.py)."""

import asyncio
import wave

import pytest

from core.config import settings
from services.telephony import crm_calls


class FakeESL:
    def __init__(self, originate=(True, "+OK"), vivas=1, grabar=None):
        self.originate = originate
        self.vivas = vivas
        self.grabar = grabar
        self.comandos = []

    async def _run_api(self, cmd):
        self.comandos.append(cmd)
        if cmd.startswith("originate"):
            if self.originate[0] and self.grabar:
                self.grabar()
            return self.originate
        self.vivas -= 1
        return True, "true" if self.vivas > 0 else "false"


@pytest.fixture
def entorno(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "CRM_SIP_GATEWAY", "troncal")
    monkeypatch.setattr(settings, "CRM_RECORDINGS_DIR", str(tmp_path))
    monkeypatch.setattr(settings, "CRM_RECORDINGS_FS_DIR", "/fs/rec")
    avisos = []

    async def avisar(url, m, estado, duracion=None, error=None, archivo=None):
        avisos.append({"estado": estado, "duracion": duracion, "error": error,
                       "archivo": archivo.name if archivo else None})

    async def dormir(_s):
        return None

    monkeypatch.setattr(crm_calls, "_avisar_erp", avisar)
    monkeypatch.setattr(crm_calls.asyncio, "sleep", dormir)
    monkeypatch.setattr(crm_calls, "_a_mp3", lambda p: p)
    return tmp_path, avisos


def _correr(esl, uuid="u1"):
    asyncio.run(crm_calls._seguir_llamada(esl, uuid, 7, "+573112223333", "+573001234567", "", "http://erp/cb"))


def test_comando_suena_asesor_y_graba_al_cliente(monkeypatch):
    monkeypatch.setattr(settings, "CRM_SIP_GATEWAY", "troncal")
    monkeypatch.setattr(settings, "CRM_SIP_PREFIX", "")
    cmd = crm_calls.comando_originate("u1", 7, "+573112223333", "+573001234567", "+576021112233", "/fs/rec/x.wav")
    assert "origination_uuid=u1" in cmd
    assert cmd.index("sofia/gateway/troncal/573112223333") < cmd.index("&bridge(")
    assert "execute_on_answer='record_session /fs/rec/x.wav']sofia/gateway/troncal/573001234567" in cmd


def test_asesor_no_contesta(entorno):
    _, avisos = entorno
    esl = FakeESL(originate=(False, "-ERR NO_ANSWER"))
    _correr(esl)
    assert avisos == [{"estado": "no_contesta", "duracion": None, "error": "El asesor no contestó.", "archivo": None}]


def test_falla_tecnica_de_la_central(entorno):
    _, avisos = entorno
    _correr(FakeESL(originate=(False, "-ERR GATEWAY_DOWN")))
    assert avisos[0]["estado"] == "fallida"
    assert "GATEWAY_DOWN" in avisos[0]["error"]


def test_cliente_no_contesta_sin_grabacion(entorno):
    _, avisos = entorno
    _correr(FakeESL(vivas=2))
    assert [a["estado"] for a in avisos] == ["en_curso", "no_contesta"]


def test_llamada_completa_sube_grabacion_con_duracion(entorno):
    carpeta, avisos = entorno

    def grabar():
        with wave.open(str(carpeta / "crm_7_u1.wav"), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(8000)
            w.writeframes(b"\x00\x00" * 8000 * 3)

    _correr(FakeESL(vivas=2, grabar=grabar))
    assert [a["estado"] for a in avisos] == ["en_curso", "completada"]
    assert avisos[1]["duracion"] == 3
    assert avisos[1]["archivo"] == "crm_7_u1.wav"
    assert not (carpeta / "crm_7_u1.wav").exists()


def test_llamada_ia_se_reporta_al_crm(monkeypatch):
    enviados = []

    class FakeResp:
        status_code = 200

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json=None, headers=None):
            enviados.append((url, json, headers))
            return FakeResp()

    monkeypatch.setattr(crm_calls.httpx, "AsyncClient", FakeClient)
    monkeypatch.setattr(settings, "CRM_API_KEY", "clave")

    async def correr(url):
        monkeypatch.setattr(settings, "CRM_ERP_URL", url)
        crm_calls.avisar_llamada_ia("uuid-9", "+573001234567", 42, [("Lyra", "Hola"), ("Cliente", "Necesito info")])
        await asyncio.gather(*list(crm_calls._tareas))

    asyncio.run(correr(""))
    assert enviados == []
    asyncio.run(correr("https://erp.test/"))
    url, datos, headers = enviados[0]
    assert url == "https://erp.test/api/integration/crm/llamadas/ia"
    assert datos == {"call_uuid": "uuid-9", "telefono": "+573001234567", "duracion": 42,
                     "transcripcion": "Lyra: Hola\nCliente: Necesito info"}
    assert headers == {"X-Lyra-Key": "clave"}
