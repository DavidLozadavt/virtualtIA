"""
scripts/school_inventory.py — Inventario verificable de School (front + back + permisos).

Recorre el código real, no una lista escrita a mano:
- front: cada <Route path=…> de AppRoutingSetup.tsx, sus `requiredPermissions` y el
  componente que pinta; de la carpeta de ese componente, las llamadas axios
  (endpoint + verbo) → operaciones que la pantalla permite;
- base: el catálogo `permissions` (nombre, descripción, ruta, padre);
- back: `routes/api.php` → si el endpoint existe y con qué middleware.

Escribe un JSON (`--json`) que Lyra puede usar como catálogo de módulos y un
markdown con la matriz. No ejecuta nada contra el backend.

    python scripts/school_inventory.py --front ../../SchoolSena/schoolSenaFrontFinal \
        --back ../../SchoolSena/SchoolSenaBackFinal --json eval/school_inventario.json --md docs/SCHOOL_INVENTARIO.md
"""

import argparse
import json
import os
import re
import subprocess
from collections import defaultdict

ROUTE_RE = re.compile(r'<Route\s+path="([^"]+)"\s+element=\{(.*?)\}\s*/>', re.S)
PERM_RE = re.compile(r"requiredPermissions=\{\[([^\]]*)\]\}")
COMP_RE = re.compile(r"<([A-Z]\w+)[\s/>]")
IMPORT_RE = re.compile(r"import\s+\{?\s*([\w\s,]+?)\s*\}?\s+from\s+'([^']+)'")
AXIOS_RE = re.compile(r"axios\.(get|post|put|patch|delete)\s*(?:<[^>]*>)?\(\s*[`'\"]([^`'\"]+)[`'\"]")
API_ROUTE_RE = re.compile(r"Route::(get|post|put|patch|delete)\(\s*'([^']+)'")


def _permissions_from_db():
    sql = ("SELECT p.name, p.description, IFNULL(p.path,''), IFNULL(padre.description,'') "
           "FROM permissions p LEFT JOIN permissions padre ON padre.id = p.idPermissionPadre")
    out = subprocess.run(["C:/xampp/mysql/bin/mysql.exe", "-uroot", "virtualt_school-vt", "-N", "-B",
                          "--default-character-set=utf8mb4", "-e", sql], capture_output=True)
    rows = {}
    for line in out.stdout.decode("utf-8", "replace").splitlines():
        name, desc, path, grupo = (line.split("\t") + ["", "", ""])[:4]
        rows[name] = {"descripcion": desc, "ruta": path, "grupo": grupo}
    return rows


def _component_files(front, routing_file, comp):
    src = open(routing_file, encoding="utf-8").read()
    for names, mod in IMPORT_RE.findall(src):
        if comp in [n.strip() for n in names.split(",")]:
            base = os.path.join(front, "src", mod.replace("@/", "")) if mod.startswith("@/") else None
            if base:
                cands = [base + ext for ext in (".tsx", ".ts")] + [os.path.join(base, "index.ts"), os.path.join(base, "index.tsx")]
                folder = base if os.path.isdir(base) else os.path.dirname(next((c for c in cands if os.path.exists(c)), base))
                return folder
    return None


def _endpoints(folder):
    eps = set()
    if not folder or not os.path.isdir(folder):
        return []
    for root, _, files in os.walk(folder):
        for f in files:
            if f.endswith((".ts", ".tsx")):
                for verb, url in AXIOS_RE.findall(open(os.path.join(root, f), encoding="utf-8", errors="ignore").read()):
                    url = re.sub(r"\$\{[^}]+\}", "{id}", url).lstrip("/")
                    eps.add((verb.upper(), url))
    return sorted(eps)


def _spread(front, nombre):
    """`...PERMISOS_X` en una ruta: los permisos de esa constante exportada del front."""
    for root, _, files in os.walk(os.path.join(front, "src")):
        for f in files:
            if f.endswith((".ts", ".tsx")):
                txt = open(os.path.join(root, f), encoding="utf-8", errors="ignore").read()
                m = re.search(rf"export const {re.escape(nombre)}\s*=\s*\[([^\]]*)\]", txt)
                if m:
                    return re.findall(r"['\"]([A-Z_]+)['\"]", m.group(1))
    return [nombre]


OPS = {"GET": "consultar", "POST": "crear", "PUT": "editar", "PATCH": "editar", "DELETE": "eliminar"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--front", required=True)
    ap.add_argument("--back", required=True)
    ap.add_argument("--json")
    ap.add_argument("--md")
    a = ap.parse_args()

    routing = os.path.join(a.front, "src", "routing", "AppRoutingSetup.tsx")
    src = open(routing, encoding="utf-8").read()
    api = open(os.path.join(a.back, "routes", "api.php"), encoding="utf-8").read()
    back_routes = {(v.upper(), re.sub(r"\{[^}]+\}", "{id}", p)) for v, p in API_ROUTE_RE.findall(api)}
    perms = _permissions_from_db()

    modulos = []
    for path, element in ROUTE_RE.findall(src):
        req = [p.strip().strip("'\"") for p in (PERM_RE.search(element).group(1).split(",") if PERM_RE.search(element) else []) if p.strip()]
        req = [x for p in req for x in (_spread(a.front, p[3:]) if p.startswith("...") else [p])]
        comps = [c for c in COMP_RE.findall(element) if c not in ("ProtectedRoute", "Navigate", "S", "Suspense")]
        comp = comps[0] if comps else None
        eps = _endpoints(_component_files(a.front, routing, comp)) if comp else []
        ops = sorted({OPS[v] for v, _ in eps})
        meta = next((perms[p] for p in req if p in perms), {})
        modulos.append({
            "ruta": path, "permisos": req, "componente": comp,
            "nombre": meta.get("descripcion") or (comp or path), "grupo": meta.get("grupo") or "",
            "operaciones_ui": ops,
            "endpoints": [{"verbo": v, "url": u, "existe_en_back": (v, u) in back_routes} for v, u in eps],
        })
    data = {"modulos": modulos, "permisos": perms}
    if a.json:
        json.dump(data, open(a.json, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    if a.md:
        por_grupo = defaultdict(list)
        for m in modulos:
            por_grupo[m["grupo"] or "(sin grupo)"].append(m)
        lines = ["# Inventario de School (generado por scripts/school_inventory.py)", "",
                 f"{len(modulos)} rutas del front · {len(perms)} permisos en la base.", ""]
        for g in sorted(por_grupo):
            lines += [f"## {g}", "", "| Módulo | Ruta | Permisos | Operaciones en la UI | Endpoints |", "|---|---|---|---|---|"]
            for m in por_grupo[g]:
                eps = ", ".join(f"{e['verbo']} {e['url']}" for e in m["endpoints"][:6]) + (" …" if len(m["endpoints"]) > 6 else "")
                lines.append(f"| {m['nombre']} | `{m['ruta']}` | {', '.join(m['permisos']) or '—'} | "
                             f"{', '.join(m['operaciones_ui']) or '—'} | {eps or '—'} |")
            lines.append("")
        open(a.md, "w", encoding="utf-8").write("\n".join(lines))
    print(f"{len(modulos)} rutas, {len(perms)} permisos, {sum(len(m['endpoints']) for m in modulos)} endpoints")


if __name__ == "__main__":
    main()
