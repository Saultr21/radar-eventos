"""
test_report.py
Ejecuta el pipeline real de scanner.scan_source sobre 3-4 URLs de prueba y
guarda el informe en formato TXT estructurado en reports/test_new_events.txt
(sin pisar el informe de producción ni tocar la caché known_events).

Uso:
    uv run scripts/test_report.py
    uv run scripts/test_report.py ceoe cmc camara
"""
import io
import logging
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

# Forzar stdout en UTF-8 (Windows por defecto usa cp1252 y peta con ─, ✔, etc.)
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
if hasattr(sys.stderr, "buffer"):
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")

# ── path setup ────────────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)-7s] %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)
for lib in ("httpx", "httpcore", "hpack", "scrapling", "camoufox", "urllib3"):
    logging.getLogger(lib).setLevel(logging.WARNING)
log = logging.getLogger("test_report")

from config import DAYS_AHEAD, LLM_PROVIDER, MAX_WORKERS, ROOT_DIR
from events import event_id
from scanner import scan_source
from reports import save_events_html_report, save_events_report

# ── fuentes de prueba ─────────────────────────────────────────────────────────
ALL_TEST_SOURCES = {
    "ceoe":   {"name": "CEOE Tenerife",                       "url": "https://ceoe-tenerife.com/eventos/",        "cat": "Patronal"},
    "cmc":    {"name": "Clúster Marítimo de Canarias (CMC)",  "url": "https://clustermc.es/eventos-futuros/",     "cat": "Clúster"},
    "camara": {"name": "Cámara de Comercio Gran Canaria",     "url": "https://www.camaragrancanaria.org/formacion/oferta-formativa/", "cat": "Cámara"},
    "ahk":    {"name": "Cámara de Comercio Alemana AHK",      "url": "https://www.ahk.es/es/eventos/proximos-eventos", "cat": "Internacional"},
}

if len(sys.argv) > 1:
    slugs = sys.argv[1:]
    selected = [(s, ALL_TEST_SOURCES[s]) for s in slugs if s in ALL_TEST_SOURCES]
    unknown = [s for s in slugs if s not in ALL_TEST_SOURCES]
    if unknown:
        log.warning("Slugs no reconocidos (ignorados): %s", unknown)
    if not selected:
        log.error("Ningún slug válido. Opciones: %s", list(ALL_TEST_SOURCES))
        sys.exit(1)
else:
    selected = list(ALL_TEST_SOURCES.items())


def main() -> None:
    log.info("=" * 60)
    log.info("  TEST REPORT — pipeline real sobre %d fuentes", len(selected))
    log.info("  Proveedor LLM : %s", LLM_PROVIDER)
    log.info("  Horizonte     : %d días", DAYS_AHEAD)
    log.info("=" * 60)

    today = datetime.now()
    scan_date = today.strftime("%d/%m/%Y %H:%M")
    all_events: list[dict] = []
    workers = max(1, min(MAX_WORKERS, len(selected)))
    t_start = time.monotonic()

    with ThreadPoolExecutor(max_workers=workers) as executor:
        future_to_source = {
            executor.submit(scan_source, src, DAYS_AHEAD): (slug, src)
            for slug, src in selected
        }
        for future in as_completed(future_to_source):
            slug, src = future_to_source[future]
            try:
                events = future.result()
            except Exception as exc:
                log.warning("✘  Error en %s (%s): %s", slug, src["name"], exc)
                continue
            for ev in events:
                ev["_id"] = event_id(ev)
                all_events.append(ev)

    elapsed = time.monotonic() - t_start

    def _sort_key(ev: dict) -> datetime:
        try:
            return datetime.strptime(ev.get("date", "").split("-")[0].strip(), "%d/%m/%Y")
        except Exception:
            return datetime(2099, 12, 31)

    all_events.sort(key=_sort_key)

    out_txt = ROOT_DIR / "reports" / "test_new_events.txt"
    out_html = ROOT_DIR / "reports" / "test_new_events.html"
    save_events_report(all_events, scan_date, DAYS_AHEAD, output_path=out_txt)
    save_events_html_report(all_events, scan_date, DAYS_AHEAD, output_path=out_html)

    log.info("=" * 60)
    log.info("  Test completado en %.0fs", elapsed)
    log.info("  Eventos encontrados : %d", len(all_events))
    log.info("  Informe TXT  : %s", out_txt.relative_to(ROOT_DIR))
    log.info("  Informe HTML : %s", out_html.relative_to(ROOT_DIR))
    log.info("=" * 60)

    print()
    print(f"  {'Fuente':<40}  {'Eventos':>7}")
    print("  " + "─" * 50)
    counts: dict[str, int] = {}
    for ev in all_events:
        counts[ev.get("association", "?")] = counts.get(ev.get("association", "?"), 0) + 1
    for slug, src in selected:
        print(f"  {src['name']:<40}  {counts.get(src['name'], 0):>7}")
    print("  " + "─" * 50)
    print(f"  {'TOTAL':<40}  {len(all_events):>7}")
    print()


if __name__ == "__main__":
    main()
