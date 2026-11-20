"""
scripts/e2e_school_navegador_f7.py — Fase 7 en el front REAL de School (navegador).

Login por el formulario, arma un formulario con Nexo, lo guarda, lo vincula con un
evento creado en la misma conversación, lo abre en el constructor existente, lo
edita AHÍ y comprueba que Nexo ve el cambio. También comprueba que el vínculo aparece
en la pantalla de Formularios. Borra lo que crea (marca F7NAV).

    LYRA_TEST_PASSWORD=... python scripts/e2e_school_navegador_f7.py --front http://localhost:4210 --out DIR
"""

import argparse
import os
import re
import subprocess
import sys

from playwright.sync_api import sync_playwright

ap = argparse.ArgumentParser()
ap.add_argument("--front", default="http://localhost:4210")
ap.add_argument("--email", default="lyra.admin@lyra-prueba.local")
ap.add_argument("--out", default=".")
a = ap.parse_args()
clave = os.environ.get("LYRA_TEST_PASSWORD") or sys.exit("Falta LYRA_TEST_PASSWORD")
MY = ["C:/xampp/mysql/bin/mysql.exe", "-uroot", "virtualt_school-vt", "-N", "--default-character-set=utf8mb4", "-e"]
MARCA = "F7NAV"
OK = []


def sql(q):
    return subprocess.run(MY + [q], capture_output=True, text=True, encoding="utf-8").stdout.strip()


def check(nombre, cond, detalle=""):
    OK.append(bool(cond))
    print(("  OK    " if cond else "  FALLA ") + nombre + (f" — {str(detalle)[:200]}" if detalle and not cond else ""))


try:
    with sync_playwright() as p:
        exe = os.environ.get("PLAYWRIGHT_CHROMIUM")
        nav = p.chromium.launch(executable_path=exe) if exe else p.chromium.launch()
        page = nav.new_page(viewport={"width": 1366, "height": 900})
        page.goto(f"{a.front}/auth/login")
        page.fill("input[name=email]", a.email)
        page.fill("input[name=password]", clave)
        page.click("button:has-text('Iniciar sesión')")
        page.wait_for_timeout(7000)
        check("login real por el formulario", "/auth" not in page.url, page.url)
        page.locator("button.lx-orb").click()
        page.wait_for_timeout(1500)
        if page.locator(".lx-pick-row", has_text="Nexo").count():
            page.locator(".lx-pick-row", has_text="Nexo").click()
            page.wait_for_timeout(3000)
        caja = page.locator("textarea#lx-input")

        def decir(texto, espera=1200):
            n = page.locator(".lx-msg[data-role='assistant']").count()
            caja.fill(texto)
            caja.press("Enter")
            page.wait_for_timeout(espera)
            for _ in range(80):
                if page.locator(".lx-typing").count() == 0 and page.locator(".lx-msg[data-role='assistant']").count() > n:
                    break
                page.wait_for_timeout(500)
            page.wait_for_timeout(800)
            return page.locator(".lx-msg[data-role='assistant']").last.inner_text()

        tarjeta = page.locator("[data-testid=lyra-form-draft]")

        decir("quiero crear un evento")
        decir(f"se llama Expo {MARCA}")
        decir("el 20 de diciembre a las 9 de la mañana")
        r = decir("sí", 2500)
        ev = re.search(r"número (\d+)", r)
        check("evento creado", bool(ev), r)
        decir("quiero crear un formulario de inscripción")
        r = decir(f"Se llamará Inscripción {MARCA}")
        check("tarjeta del borrador: sin guardar", tarjeta.count() == 1 and "Sin guardar" in tarjeta.inner_text(), r)
        decir("agrégale nombre completo, documento, correo y teléfono")
        check("la tarjeta muestra las 4 preguntas", tarjeta.locator("li").count() == 4, tarjeta.inner_text())
        decir("que todos sean obligatorios menos el teléfono")
        check("obligatorias indicadas con texto", tarjeta.locator(".lx-form-req").count() == 3, tarjeta.inner_text())
        check("una sola tarjeta (la vigente)", tarjeta.count() == 1)
        page.screenshot(path=os.path.join(a.out, "f7_borrador.png"))
        r = decir("guárdalo", 2500)
        fid = re.search(r"número (\d+)", r)
        check("guardado: la tarjeta lo dice", fid and "Guardado" in tarjeta.inner_text(), r)
        r = decir("vincúlalo con el evento que creamos")
        check("pide confirmar el vínculo", "¿Lo vinculo?" in r, r)
        r = decir("sí", 2500)
        check("vinculado (base)", ev and fid and sql(f"SELECT idFormularioInterno FROM evento WHERE idEvento={ev.group(1)}") == fid.group(1), r)
        page.screenshot(path=os.path.join(a.out, "f7_vinculado.png"))

        # Abrir en el constructor existente desde la tarjeta.
        decir("muéstrame cómo quedó")
        tarjeta.locator(".lx-form-open").click()
        page.wait_for_timeout(5000)
        check("abre el constructor del formulario", f"/formularios/builder/{fid.group(1)}" in page.url, page.url)
        valores = page.locator("input[type=text]").evaluate_all("els => els.map(e => e.value)")
        esperadas = ["Nombre completo", "Número de documento", "Correo electrónico", "Teléfono"]
        check("el constructor tiene exactamente esas preguntas",
              all(any(v.lower() == e.lower() for v in valores) for e in esperadas), valores)
        page.screenshot(path=os.path.join(a.out, "f7_constructor.png"))
        # Editarlo en el constructor y guardar desde ahí.
        page.keyboard.press("Escape")  # se cierra el chat para usar el constructor
        page.wait_for_timeout(800)
        idx = next(i for i, v in enumerate(valores) if v.lower() == "correo electrónico")
        page.locator("input[type=text]").nth(idx).fill("Correo de contacto")
        page.locator("button:has-text('Guardar')").first.click()
        page.wait_for_timeout(3000)
        en_bd = sql(f"SELECT titulo FROM formulario_preguntas WHERE idFormulario={fid.group(1)} ORDER BY orden")
        check("editado y guardado en el constructor", "correo de contacto" in en_bd.lower(), en_bd)
        # Nexo ve el cambio hecho fuera de la conversación.
        if not caja.is_visible():
            page.locator("button.lx-orb").click()
            page.wait_for_timeout(1500)
        r = decir("muéstrame cómo quedó el formulario")
        check("Nexo relee el cambio del constructor", "correo de contacto" in (r + tarjeta.inner_text()).lower(), r)

        # El vínculo aparece donde School lo muestra (Formularios › eventos).
        page.goto(f"{a.front}/formularios")
        page.wait_for_timeout(3000)
        page.locator("button:has-text('Integración de Formularios Externos')").click()
        page.wait_for_timeout(3000)
        fila = page.locator("div", has_text=f"Expo {MARCA}").last
        check("el evento aparece con formulario Interno en Formularios",
              page.get_by_text(f"Expo {MARCA}", exact=False).count() > 0, page.locator("body").inner_text()[:300])
        page.screenshot(path=os.path.join(a.out, "f7_formularios_eventos.png"), full_page=True)
        nav.close()
finally:
    sql(f"DELETE FROM evento WHERE nombre LIKE '%{MARCA}%'; DELETE FROM formularios WHERE titulo LIKE '%{MARCA}%'")

print(f"\n{sum(OK)}/{len(OK)} verificaciones en navegador")
