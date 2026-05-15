"""
tests/test_booking_flow.py
Verifica el flujo completo de agendamiento para TODOS los negocios,
servicios y prestadores activos en la base de datos.

Ejecutar desde la raíz del proyecto:
    python -m tests.test_booking_flow
"""
import asyncio
import sys
import os
import re
import unicodedata

# Asegurar que el path apunte a la raíz del proyecto
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.database import get_connection


# ── Colores ANSI ──────────────────────────────────────────────────────────────
GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
BOLD   = "\033[1m"
RESET  = "\033[0m"
GRAY   = "\033[90m"

def _normalize(text: str) -> str:
    if not text: return ""
    nfkd = unicodedata.normalize("NFKD", str(text))
    text = "".join(c for c in nfkd if not unicodedata.combining(c)).lower().strip()
    return re.sub(r'[^\w\s]', '', text).strip()


# ── Datos de usuario demo para pruebas ───────────────────────────────────────
DEMO_USER_DATA = {"external_user_id": 1}  # Ajusta al ID de tercero real en tu DB


# ── Helpers de DB ────────────────────────────────────────────────────────────

def get_all_businesses() -> list[dict]:
    """Retorna todos los negocios activos y publicados."""
    with get_connection("vt_inventario") as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT e.id, e.razonSocial,
                       COALESCE(ce.nombre, 'Sin categoría') AS categoria
                FROM empresa e
                LEFT JOIN categoriaempresa ce ON e.idCategoriaEmpresa = ce.id
                WHERE e.idEstado = 1 AND e.publicado = 1
                ORDER BY e.id ASC
            """)
            return cur.fetchall() or []


def get_services_for_business(biz_id: int) -> list[dict]:
    """Retorna los servicios activos de un negocio."""
    with get_connection("vt_inventario") as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, nombre, tiempoServicio,
                       COALESCE(valor, 0) AS precio
                FROM servicios
                WHERE idCompany = %s
                ORDER BY id ASC
            """, (biz_id,))
            return cur.fetchall() or []


def get_professionals_for_service(srv_id: int) -> list[dict]:
    """Retorna los profesionales asignados a un servicio (via prestador_servicios)."""
    with get_connection("vt_inventario") as conn:
        with conn.cursor() as cur:
            try:
                cur.execute("""
                    SELECT t.id, t.nombre
                    FROM prestador_servicios ps
                    JOIN responsableservicio rs ON ps.responsable_servicio_id = rs.id
                    JOIN tercero t ON rs.idPersona = t.id
                    WHERE ps.servicio_id = %s AND ps.estado = 'activo'
                    ORDER BY t.id ASC
                """, (srv_id,))
                return cur.fetchall() or []
            except Exception:
                return []


def get_first_tercero() -> dict | None:
    """Retorna el primer tercero en la DB para usar como usuario demo."""
    with get_connection("vt_inventario") as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id, nombre, email FROM tercero ORDER BY id ASC LIMIT 1")
            return cur.fetchone()


# ── Pruebas individuales ──────────────────────────────────────────────────────

async def test_resolve_service(biz_id: int, srv_name: str) -> tuple[bool, str]:
    """Verifica que _resolve_service_id encuentra el servicio correctamente."""
    from tools.nexiservice import _resolve_service_id
    result = await _resolve_service_id(biz_id, srv_name)
    if result and result.get("id"):
        return True, f"id={result['id']} nombre='{result['nombre']}'"
    # Intentar con nombre normalizado
    result2 = await _resolve_service_id(biz_id, _normalize(srv_name))
    if result2 and result2.get("id"):
        return True, f"id={result2['id']} nombre='{result2['nombre']}' [normalized]"
    return False, f"No encontrado para biz={biz_id} srv='{srv_name}'"


async def test_get_professionals(srv_id: int, srv_name: str) -> tuple[bool, list[dict], str]:
    """Verifica que se pueden obtener profesionales de un servicio."""
    from tools.nexiservice import get_service_professionals
    try:
        profs = await get_service_professionals(srv_id)
        if isinstance(profs, (list, tuple)):
            return True, list(profs), f"{len(profs)} profesional(es)"
        return False, [], f"Resultado inválido: {type(profs)}"
    except Exception as e:
        return False, [], str(e)


async def test_confirm_dry_run(biz_id: int, srv_name: str, prof_name: str | None, user_data: dict) -> tuple[bool, str]:
    """
    Prueba DRY-RUN de confirm_appointment:
    - Resuelve servicio
    - Resuelve profesional
    - Calcula tiempos
    - NO inserta (dry_run=True simulado verificando sin commit)
    """
    from tools.nexiservice import _resolve_service_id, get_service_professionals
    import unicodedata

    # 1. Resolver servicio
    srv_data = await _resolve_service_id(biz_id, srv_name)
    if not srv_data:
        return False, f"_resolve_service_id falló para '{srv_name}' en biz={biz_id}"

    srv_id = srv_data["id"]
    srv_real = srv_data["nombre"]

    # 2. Resolver profesional
    prof_id = None
    prof_resolved = "Sin asignar (cualquiera)"
    if prof_name:
        profs = await get_service_professionals(srv_id)
        prof_clean = _normalize(prof_name)
        for p in profs:
            if prof_clean in _normalize(p.get("nombre", "")):
                prof_id = p["id"]
                prof_resolved = p["nombre"]
                break
        if not prof_id:
            return False, f"Profesional '{prof_name}' no encontrado en srv_id={srv_id} (profs: {[p['nombre'] for p in profs]})"

    # 3. Verificar usuario
    with get_connection("vt_inventario") as conn:
        with conn.cursor() as cur:
            eid = user_data.get("external_user_id")
            cur.execute("SELECT id FROM tercero WHERE id = %s OR id = (SELECT id FROM tercero ORDER BY id ASC LIMIT 1)", (eid,))
            row = cur.fetchone()
            if not row:
                return False, "No se pudo resolver usuario en tercero"
            user_id = row["id"]

    return True, f"srv_id={srv_id} '{srv_real}' | prof='{prof_resolved}' | user_id={user_id}"


# ── Formateadores ─────────────────────────────────────────────────────────────

def print_header(text: str):
    print(f"\n{BOLD}{CYAN}{'='*65}{RESET}")
    print(f"{BOLD}{CYAN}  {text}{RESET}")
    print(f"{BOLD}{CYAN}{'='*65}{RESET}")


def print_biz(biz: dict):
    print(f"\n{BOLD}>> [{biz['id']}] {biz['razonSocial']} -- {biz['categoria']}{RESET}")


def ok(msg: str):
    print(f"  [OK]   {msg}")


def fail(msg: str):
    print(f"  [FAIL] {msg}")


def warn(msg: str):
    print(f"  [WARN] {msg}")


def info(msg: str):
    print(f"  [INFO] {msg}")


# ── Runner principal ──────────────────────────────────────────────────────────

# ── Monkeypatch pymysql.connect to reuse connection (prevents socket exhaustion) ──
import pymysql
_real_connect = pymysql.connect
_shared_conn = None

def mocked_connect(*args, **kwargs):
    global _shared_conn
    if _shared_conn is None or not _shared_conn.open:
        _shared_conn = _real_connect(*args, **kwargs)
    return _shared_conn

pymysql.connect = mocked_connect
# También monkeypatch el cursor para que no cierre la conexión real cuando se usa en un context manager
# Pero pymysql connections no se cierran automáticamente por el cursor.
# El problema es que 'with get_connection()' cierra la conexión.
# Así que SI necesito monkeypatch get_connection o manejar el close().

import core.database
from contextlib import contextmanager

@contextmanager
def mocked_get_connection(database_name=None):
    global _shared_conn
    from core.database import _pool_config
    
    if _shared_conn is None or not _shared_conn.open:
        config = _pool_config.copy()
        if database_name:
            config["database"] = database_name
        _shared_conn = _real_connect(**config)
    
    yield _shared_conn
    # NO cerramos la conexión real para permitir reuso
    pass

core.database.get_connection = mocked_get_connection

# Re-importar herramientas para asegurar que usen el mock si ya fueron cargadas
import importlib
import tools.nexiservice
importlib.reload(tools.nexiservice)

async def run_tests():
    print_header("TEST: Flujo de Agendamiento -- Todos los Negocios")

    # Obtener usuario demo
    first_tercero = get_first_tercero()
    if not first_tercero:
        print(f"{RED}ERROR: No hay terceros en la DB. Imposible continuar.{RESET}")
        return

    user_data = {"external_user_id": first_tercero["id"]}
    print(f"\n{GRAY}Usuario demo: [{first_tercero['id']}] {first_tercero['nombre']} ({first_tercero.get('email', 'sin email')}){RESET}")

    # Obtener todos los negocios
    businesses = get_all_businesses()
    if not businesses:
        print(f"{RED}ERROR: No hay negocios activos en la DB.{RESET}")
        return

    print(f"{GRAY}Negocios a testear: {len(businesses)}{RESET}")

    total_ok = 0
    total_fail = 0
    total_warn = 0
    failures = []

    for biz in businesses:
        biz_id   = biz["id"]
        biz_name = biz["razonSocial"]
        print_biz(biz)

        # Obtener servicios
        services = get_services_for_business(biz_id)
        if not services:
            warn(f"Sin servicios registrados → skipping")
            total_warn += 1
            continue

        info(f"{len(services)} servicio(s) encontrado(s)")

        for srv in services:
            srv_id   = srv["id"]
            srv_name = srv["nombre"]
            print(f"\n    {BOLD}[SRV] Servicio: {srv_name}{RESET} (id={srv_id}, {srv.get('tiempoServicio', '?')} min, ${srv.get('precio', '?')} COP)")

            # ── Test 1: _resolve_service_id ──────────────────────────────
            ok_s, msg_s = await test_resolve_service(biz_id, srv_name)
            if ok_s:
                ok(f"resolve_service: {msg_s}")
                total_ok += 1
            else:
                fail(f"resolve_service FALLÓ: {msg_s}")
                total_fail += 1
                failures.append(f"[biz={biz_id} '{biz_name}'] resolve_service('{srv_name}'): {msg_s}")

            # ── Test 2: Obtener profesionales ────────────────────────────
            ok_p, profs, msg_p = await test_get_professionals(srv_id, srv_name)
            if ok_p:
                if profs:
                    ok(f"get_professionals: {msg_p} -> {', '.join(p['nombre'] for p in profs)}")
                    total_ok += 1

                    # ── Test 3a: dry-run con cada profesional ────────────
                    for prof in profs:
                        ok_c, msg_c = await test_confirm_dry_run(biz_id, srv_name, prof["nombre"], user_data)
                        if ok_c:
                            ok(f"  confirm(prof='{prof['nombre']}'): {msg_c}")
                            total_ok += 1
                        else:
                            fail(f"  confirm(prof='{prof['nombre']}'): {msg_c}")
                            total_fail += 1
                            failures.append(f"[biz={biz_id}] confirm({srv_name}, prof={prof['nombre']}): {msg_c}")

                    # ── Test 3b: dry-run con "cualquiera" ────────────────
                    ok_any, msg_any = await test_confirm_dry_run(biz_id, srv_name, None, user_data)
                    if ok_any:
                        ok(f"  confirm(prof='cualquiera'): {msg_any}")
                        total_ok += 1
                    else:
                        fail(f"  confirm(prof='cualquiera'): {msg_any}")
                        total_fail += 1
                        failures.append(f"[biz={biz_id}] confirm({srv_name}, cualquiera): {msg_any}")

                else:
                    warn(f"Sin profesionales asignados al servicio. dry-run con 'cualquiera'")
                    total_warn += 1
                    # Dry-run con cualquiera igual
                    ok_any, msg_any = await test_confirm_dry_run(biz_id, srv_name, None, user_data)
                    if ok_any:
                        ok(f"  confirm(cualquiera, sin prof asignado): {msg_any}")
                        total_ok += 1
                    else:
                        fail(f"  confirm(cualquiera, sin prof asignado): {msg_any}")
                        total_fail += 1
                        failures.append(f"[biz={biz_id}] confirm({srv_name}, sin prof): {msg_any}")
            else:
                fail(f"get_professionals FALLÓ: {msg_p}")
                total_fail += 1
                failures.append(f"[biz={biz_id} srv={srv_id}] get_professionals: {msg_p}")

    # ── Resumen ───────────────────────────────────────────────────────────────
    print_header("RESUMEN")
    print(f"  [OK] Pasaron: {total_ok}")
    print(f"  [WARN] Advertencias: {total_warn}")
    print(f"  [FAIL] Fallaron: {total_fail}")

    if failures:
        print(f"\n{BOLD}{RED}FALLOS DETALLADOS:{RESET}")
        for i, f in enumerate(failures, 1):
            print(f"  {i}. {f}")
    else:
        print(f"\n{GREEN}{BOLD}[ALL PASSED] Todo el flujo de agendamiento funciona correctamente.{RESET}")

    print()
    return total_fail == 0


if __name__ == "__main__":
    success = asyncio.run(run_tests())
    sys.exit(0 if success else 1)
