"""
generate_html.py
Regenera reports/latest_new_events.html a partir del último HTML guardado.
Útil para actualizar la plantilla sin relanzar el scan completo.

Uso:
    uv run scripts/generate_html.py
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from config import DAYS_AHEAD, REPORT_HTML_TEMPLATE, ROOT_DIR
from reports import save_events_html_report

reports_dir = ROOT_DIR / "reports"
src_html = reports_dir / "latest_new_events.html"

if not src_html.exists():
    candidates = sorted(reports_dir.glob("*_events.html"), reverse=True)
    if not candidates:
        print(f"No hay ningún HTML en {reports_dir}")
        sys.exit(1)
    src_html = candidates[0]
    print(f"latest_new_events.html no encontrado, usando: {src_html.name}")

content = src_html.read_text(encoding="utf-8")

# Extraer el JSON embebido: const EVENTS = [...];
m = re.search(r"const EVENTS\s*=\s*(\[.*?\]);", content, re.DOTALL)
if not m:
    print("No se encontró el bloque 'const EVENTS = [...]' en el HTML.")
    sys.exit(1)

events = json.loads(m.group(1))

# Recuperar scan_date del HTML si está disponible
date_m = re.search(r"Escaneo[^:]*:\s*([^<\n]+)", content)
scan_date = date_m.group(1).strip() if date_m else "—"

save_events_html_report(events, scan_date, DAYS_AHEAD, output_path=reports_dir / "latest_new_events.html")
print(f"HTML regenerado: {src_html}")
