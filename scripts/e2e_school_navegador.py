"""
scripts/e2e_school_navegador.py — Nexo en el front REAL de School (navegador).

Inicia sesión por el formulario de login con un usuario sintético (la clave se
lee de LYRA_TEST_PASSWORD, nunca se imprime), abre el chat y recorre los flujos
de continuidad. Guarda capturas en --out.

    LYRA_TEST_PASSWORD=... python scripts/e2e_school_navegador.py --front http://localhost:5175 \
        --email lyra.admin@lyra-prueba.local --out ../../.playwright-mcp
"""

import argparse
import os
import re
import sys

from playwright.sync_api import sync_playwright

ap = argparse.ArgumentParser()
ap.add_argument("--front", default="http://localhost:4210")
ap.add_argument("--email", default="lyra.admin@lyra-prueba.local")
ap.add_argument("--out", default=".")
a = ap.parse_args()
clave = os.environ.get("LYRA_TEST_PASSWORD") or sys.exit("Falta LYRA_TEST_PASSWORD")
OK = []


def check(nombre, cond, detalle=""):
    OK.append(bool(cond))
    print(("  OK    " if cond else "  FALLA ") + nombre + (f" — {str(detalle)[:170]}" if detalle else ""))


with sync_playwright() as p:
    # Usa el Chromium ya instalado si se indica (PLAYWRIGHT_CHROMIUM); si no, el de Playwright.
    exe = os.environ.get("PLAYWRIGHT_CHROMIUM")
    nav = p.chromium.launch(executable_path=exe) if exe else p.chromium.launch()
    page = nav.new_page(viewport={"width": 1366, "height": 900})
    page.goto(f"{a.front}/auth/login")
    page.fill("input[name=email]", a.email)
    page.fill("input[name=password]", clave)
    page.click("button:has-text('Iniciar sesión')")
    page.wait_for_timeout(6000)
    if "/auth" in page.url:
        # Paso intermedio del login real (p. ej. elegir empresa): se registra qué muestra.
        page.screenshot(path=os.path.join(a.out, "f6_school_login.png"))
        print("LOGIN_URL=" + page.url)
        print("LOGIN_TEXTO=" + " | ".join(page.locator("body").inner_text()[:600].splitlines()))
        botones = [b.inner_text().strip() for b in page.locator("button").all()][:12]
        print("BOTONES=" + str(botones))
        sys.exit(0)
    page.wait_for_timeout(2500)
    check("login real por el formulario", "/auth" not in page.url, page.url)

    orb = page.locator("button.lx-orb")
    etiqueta = orb.get_attribute("aria-label") or ""
    check("identidad: el botón es de Nexo", "Nexo" in etiqueta, etiqueta)
    orb.click()
    page.wait_for_timeout(1500)
    if page.locator(".lx-pick-row", has_text="Nexo").count():
        page.locator(".lx-pick-row", has_text="Nexo").click()
        page.wait_for_timeout(3000)
    caja = page.locator("textarea#lx-input")

    def decir(texto, espera=1200):
        antes = page.locator(".lx-scroll").inner_text()
        caja.fill(texto)
        caja.press("Enter")
        page.wait_for_timeout(espera)
        for _ in range(60):
            if page.locator(".lx-typing").count() == 0:
                break
            page.wait_for_timeout(500)
        page.wait_for_timeout(1200)
        todo = page.locator(".lx-scroll").inner_text()
        return todo[len(antes):] if todo.startswith(antes[:200]) else todo[-900:]

    saludo = page.locator(".lx-scroll").inner_text()
    check("saludo de Nexo", "Nexo" in saludo, saludo[:120])

    r = decir("¿qué permisos tengo?")
    check("permisos reales en panorama", "pantallas" in page.locator(".lx-scroll").inner_text()[-1500:], r[:200])
    r = decir("¿qué puedo hacer en Gestión académica?")
    check("detalle de un área", "Gestión académica" in page.locator(".lx-scroll").inner_text()[-2500:], r[:200])
    r = decir("muéstrame los medios de pago")
    check("ofrece abrir la pantalla", "¿Te llevo" in r, r)
    r = decir("sí", 2500)
    page.wait_for_timeout(2000)
    check("el 'sí' abre ESA pantalla", "/pagos/medio-pagos" in page.url, page.url)
    page.screenshot(path=os.path.join(a.out, "f6_school_si_navega.png"))

    r = decir("quiero crear un evento")
    check("pregunta el nombre", "llamar" in r, r)
    r = decir("Encuentro Nexo navegador")
    check("toma el nombre y pide la fecha", "¿Qué día" in r, r)
    r = decir("el viernes de la próxima semana a las nueve de la mañana")
    check("resumen con fecha completa", "¿Lo creo?" in r and re.search(r"viernes \d+ de \w+ de \d{4}", r), r)
    r = decir("antes dime qué eventos tengo programados")
    check("cambio de tema sin perder el borrador", "evento" in r.lower(), r)
    r = decir("continuemos con el evento")
    check("retoma el evento", "Encuentro Nexo navegador" in r and "¿Lo creo?" in r, r)
    r = decir("sí", 2500)
    m = re.search(r"número (\d+)", r)
    check("evento creado y verificado", bool(m), r)
    page.screenshot(path=os.path.join(a.out, "f6_school_evento_creado.png"))
    print("EVENTO_CREADO=" + (m.group(1) if m else ""))

    r = decir("Planeación", 2500)
    url1 = page.url
    r = decir("Redes", 2500)
    check("'Redes' cambia de pantalla", page.url != url1 and page.url.endswith("/redes"), (url1, page.url))
    r = decir("dame un resumen completo")
    check("resumen con datos autorizados", "Resumen" in r, r)
    page.screenshot(path=os.path.join(a.out, "f6_school_resumen.png"))
    nav.close()

print(f"\n{sum(OK)}/{len(OK)} verificaciones en navegador")
