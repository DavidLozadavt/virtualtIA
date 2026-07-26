"""Backend client: un origen YA resuelto no se vuelve a geocodificar.

Cubre el bug de propagación de estado: tras el Geographic Context Resolver +
confirmación, las coordenadas del origen son autoritativas y la creación del
servicio debe consumirlas tal cual, SIN reabrir la ambigüedad (sin re-geocoding).
"""

import asyncio

import pytest

from services.telephony.backend_client import TelephonyBackendClient


def run(coro):
    return asyncio.run(coro)


def _client_with_stubbed_post(monkeypatch, captured):
    client = TelephonyBackendClient(api_base="http://backend.test")

    async def fake_create_service(payload, http_client=None, *, skip_idempotency=False):
        captured["payload"] = payload
        return True, "Servicio creado", {"id": 1}

    monkeypatch.setattr(client, "create_service", fake_create_service)
    return client


def _install_geocode_spy(monkeypatch, calls, result):
    """Reemplaza core.geocoder_service.geocode (import perezoso dentro del método)."""
    import core.geocoder_service as gs

    async def fake_geocode(query, barrio=None):
        calls.append((query, barrio))
        return result

    monkeypatch.setattr(gs, "geocode", fake_geocode)


def test_resolved_coords_skip_regeocoding(monkeypatch):
    calls = []
    captured = {}
    _install_geocode_spy(monkeypatch, calls, ("999.0", "999.0", "should-not-be-used"))
    client = _client_with_stubbed_post(monkeypatch, captured)

    ok, _msg = run(client.create_service_from_geocoded(
        celular="+573001112233",
        origen="Cl. 17 #6E-20",
        destino=None,
        call_uuid="uuid-1",
        origen_barrio="Santa Teresa",
        origen_lat=2.4310,
        origen_lng=-76.6010,
    ))

    assert ok is True
    assert calls == []                                   # NUNCA re-geocodificó el origen
    assert captured["payload"]["origen_lat"] == 2.4310   # coords resueltas tal cual
    assert captured["payload"]["origen_lng"] == -76.6010
    assert captured["payload"]["origen"] == "Cl. 17 #6E-20"


def test_missing_coords_falls_back_to_geocoding(monkeypatch):
    calls = []
    captured = {}
    _install_geocode_spy(monkeypatch, calls, (2.44, -76.61, "Popayán"))
    client = _client_with_stubbed_post(monkeypatch, captured)

    ok, _msg = run(client.create_service_from_geocoded(
        celular="+573001112233",
        origen="La Esmeralda",
        destino=None,
        call_uuid="uuid-2",
        origen_barrio="La Esmeralda",
        # sin origen_lat/origen_lng → comportamiento legacy (geocodifica con barrio)
    ))

    assert ok is True
    assert calls == [("La Esmeralda", "La Esmeralda")]   # geocodificó como antes
    assert captured["payload"]["origen_lat"] == 2.44
    assert captured["payload"]["origen_lng"] == -76.61
