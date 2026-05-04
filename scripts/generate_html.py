"""
Genera reports/latest_new_events.html a partir de reports/latest_new_events.txt.
Ejecutar tras el scan:
    .venv/Scripts/python.exe scripts/generate_html.py
"""
import json
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT     = Path(__file__).resolve().parent.parent
TXT_PATH = ROOT / "reports" / "latest_new_events.txt"
HTML_TPL = ROOT / "config" / "templates" / "report_html.html"
OUT_PATH = ROOT / "reports" / "latest_new_events.html"

def parse_txt(path: Path) -> tuple[list[dict], str, int]:
    text = path.read_text(encoding="utf-8")

    # Extraer cabecera
    scan_date  = re.search(r"Escaneo:\s+(.+)", text)
    days_ahead = re.search(r"\((\d+) días\)", text)
    scan_date  = scan_date.group(1).strip()  if scan_date  else "—"
    days_ahead = int(days_ahead.group(1))    if days_ahead else 30

    events: list[dict] = []
    fields = ["title", "type", "date", "time", "location",
              "description", "url", "price", "deadline", "association", "category"]
    labels = {
        "Título":       "title",
        "Tipo":         "type",
        "Fecha":        "date",
        "Hora":         "time",
        "Lugar":        "location",
        "Descripción":  "description",
        "URL":          "url",
        "Precio":       "price",
        "Inscripción":  "deadline",
        "Asociación":   "association",
        "Categoría":    "category",
    }

    current: dict | None = None
    for line in text.splitlines():
        stripped = line.strip()
        # Detectar inicio de nuevo evento (línea en blanco seguida de "Título:")
        if stripped.startswith("Título:"):
            if current:
                events.append(current)
            current = {f: "—" for f in fields}
        if current is None:
            continue
        for label, key in labels.items():
            prefix = f"{label}:"
            if stripped.startswith(prefix):
                val = stripped[len(prefix):].strip()
                current[key] = val if val else "—"
                break

    if current:
        events.append(current)

    return events, scan_date, days_ahead


if __name__ == "__main__":
    if not TXT_PATH.exists():
        print(f"No se encontró {TXT_PATH}")
        print("Asegúrate de que el scan ha terminado antes de ejecutar este script.")
        sys.exit(1)

    events, scan_date, days_ahead = parse_txt(TXT_PATH)
    print(f"Eventos leídos del TXT: {len(events)}")

    horizon_label = (datetime.now() + timedelta(days=days_ahead)).strftime("%d/%m/%Y")
    template = HTML_TPL.read_text(encoding="utf-8")
    html = (
        template
        .replace("__EVENTS_JSON__",   json.dumps(events, ensure_ascii=False))
        .replace("__SCAN_DATE__",     scan_date)
        .replace("__HORIZON_LABEL__", horizon_label)
        .replace("__DAYS_AHEAD__",    str(days_ahead))
    )
    OUT_PATH.write_text(html, encoding="utf-8")
    print(f"HTML generado en: {OUT_PATH}")
