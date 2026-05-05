"""
test_scan.py
Prueba el pipeline completo (fetch → LLM → parse) sobre 2-3 fuentes,
mostrando cada paso con detalle para detectar fallos o mejoras.

Uso:
    uv run scripts/test_scan.py
    uv run scripts/test_scan.py ceoe cmc camara   # slugs de fuentes
"""
import json
import logging
import sys
import textwrap
import time
from datetime import datetime, timedelta
from pathlib import Path

# ── path setup ────────────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

# ── logging: DEBUG en consola para este test ──────────────────────────────────
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)-7s] %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)
# silenciar libs externas muy verbosas
for lib in ("httpx", "httpcore", "hpack", "scrapling", "camoufox", "urllib3"):
    logging.getLogger(lib).setLevel(logging.WARNING)

log = logging.getLogger("test_scan")

# ── importar módulos del proyecto ─────────────────────────────────────────────
from config import DAYS_AHEAD, LLM_PROVIDER, LMSTUDIO_PROMPT_TEMPLATE, PROMPT_TEMPLATE
from events import event_id, parse_events_response
from fetcher import fetch_url_details
from llm import run_prompt

# ── fuentes de prueba ─────────────────────────────────────────────────────────
ALL_TEST_SOURCES = {
    "ceoe":   {"name": "CEOE Tenerife",                       "url": "https://ceoe-tenerife.com/eventos/",        "cat": "Patronal"},
    "cmc":    {"name": "Clúster Marítimo de Canarias (CMC)",  "url": "https://clustermc.es/eventos-futuros/",     "cat": "Clúster"},
    "camara": {"name": "Cámara de Comercio Gran Canaria",     "url": "https://www.camaragrancanaria.org/formacion/oferta-formativa/", "cat": "Cámara"},
    "cce":    {"name": "Confederación Canaria de Empresarios (CCE)", "url": "https://ccelpa.org/",               "cat": "Patronal"},
    "ahk":    {"name": "Cámara de Comercio Alemana AHK",      "url": "https://www.ahk.es/es/eventos/",           "cat": "Internacional"},
}

# Selección por argumento o por defecto las 3 primeras
if len(sys.argv) > 1:
    slugs = sys.argv[1:]
    selected = {s: ALL_TEST_SOURCES[s] for s in slugs if s in ALL_TEST_SOURCES}
    unknown = [s for s in slugs if s not in ALL_TEST_SOURCES]
    if unknown:
        log.warning("Slugs no reconocidos (ignorados): %s", unknown)
    if not selected:
        log.error("Ningún slug válido. Opciones: %s", list(ALL_TEST_SOURCES))
        sys.exit(1)
else:
    selected = dict(list(ALL_TEST_SOURCES.items())[:3])


def sep(title: str = "", width: int = 70) -> None:
    if title:
        print(f"\n{'━'*width}")
        print(f"  {title}")
        print(f"{'━'*width}")
    else:
        print("─" * width)


def test_source(slug: str, source: dict) -> dict:
    """Ejecuta fetch + LLM + parse sobre una fuente con logging exhaustivo."""
    today = datetime.now()
    horizon = (today + timedelta(days=DAYS_AHEAD)).strftime("%d/%m/%Y")
    today_str = today.strftime("%d/%m/%Y")

    sep(f"[{slug.upper()}]  {source['name']}")
    print(f"  URL      : {source['url']}")
    print(f"  Categoría: {source['cat']}")
    print(f"  Horizonte: {today_str} → {horizon}")

    result = {
        "slug": slug,
        "source": source,
        "fetch_ok": False,
        "content_len": 0,
        "links_found": 0,
        "llm_raw": "",
        "llm_ok": False,
        "events": [],
        "parse_ok": False,
        "error": None,
        "elapsed_fetch": 0.0,
        "elapsed_llm": 0.0,
    }

    # ── 1. FETCH ──────────────────────────────────────────────────────────────
    sep(f"  1/3  FETCH  {source['url']}")
    t0 = time.monotonic()
    try:
        fetched = fetch_url_details(source["url"], max_chars=6000, max_links=15)
        result["elapsed_fetch"] = time.monotonic() - t0

        print(f"  status     : {fetched.get('status')}  (HTTP {fetched.get('status_code')})")
        print(f"  final_url  : {fetched.get('final_url')}")
        content = fetched.get("content", "")
        links = fetched.get("discovered_links", [])
        result["content_len"] = len(content)
        result["links_found"] = len(links)
        print(f"  contenido  : {len(content)} chars  (fetch: {result['elapsed_fetch']:.1f}s)")
        print(f"  enlaces    : {len(links)}")

        if fetched.get("error"):
            print(f"  ⚠ error    : {fetched['error']}")
            result["error"] = fetched["error"]

        # Primeros 400 chars del contenido
        print(f"\n  ── primeros 400 chars ──")
        print(textwrap.indent(content[:400], "    "))

        # Links relevantes
        if links:
            print(f"\n  ── enlaces descubiertos ({len(links)}) ──")
            for lnk in links[:10]:
                print(f"    [{lnk.get('title', '')[:45]}]  {lnk.get('url')}")

        result["fetch_ok"] = fetched.get("status") == "ok"

    except Exception as exc:
        result["elapsed_fetch"] = time.monotonic() - t0
        result["error"] = str(exc)
        log.exception("Error en fetch de %s", source["url"])
        print(f"  ✘ EXCEPCIÓN: {exc}")
        return result

    # ── 2. LLM ────────────────────────────────────────────────────────────────
    sep(f"  2/3  LLM  ({LLM_PROVIDER})")

    template = LMSTUDIO_PROMPT_TEMPLATE if LLM_PROVIDER == "lmstudio" else PROMPT_TEMPLATE
    prompt = template.format(
        source_name=source["name"],
        source_url=source["url"],
        source_category=source["cat"],
        today_str=today_str,
        horizon=horizon,
    )
    if LLM_PROVIDER == "lmstudio":
        prompt += "\n\nRecuerda: devuelve SOLO el JSON array al final, sin markdown ni texto adicional."

    print(f"  prompt len : {len(prompt)} chars")
    print(f"\n  ── primeras 3 líneas del prompt ──")
    for line in prompt.splitlines()[:3]:
        print(f"    {line}")
    print("    ...")

    t0 = time.monotonic()
    try:
        raw = run_prompt(prompt, source_url=source["url"])
        result["elapsed_llm"] = time.monotonic() - t0
        result["llm_raw"] = raw
        result["llm_ok"] = True
        print(f"\n  elapsed LLM: {result['elapsed_llm']:.1f}s")
        print(f"  respuesta  : {len(raw)} chars")
        print(f"\n  ── respuesta raw (primeros 800 chars) ──")
        print(textwrap.indent(raw[:800], "    "))
        if len(raw) > 800:
            print(f"    ... [{len(raw)-800} chars omitidos] ...")
            print(textwrap.indent(raw[-200:], "    "))

    except Exception as exc:
        result["elapsed_llm"] = time.monotonic() - t0
        result["error"] = str(exc)
        log.exception("Error en LLM para %s", source["name"])
        print(f"  ✘ EXCEPCIÓN LLM: {exc}")
        return result

    # ── 3. PARSE ──────────────────────────────────────────────────────────────
    sep(f"  3/3  PARSE")
    try:
        events = parse_events_response(raw)
        result["events"] = events
        result["parse_ok"] = True
        print(f"  eventos parseados: {len(events)}")
        for i, ev in enumerate(events, 1):
            print(f"\n  [{i}] {ev.get('title', '(sin título)')}")
            print(f"       fecha : {ev.get('date', '—')}")
            print(f"       tipo  : {ev.get('type', '—')}")
            print(f"       lugar : {ev.get('location', '—')}")
            print(f"       url   : {ev.get('url', '—')}")
            desc = ev.get("description", "")
            if desc:
                print(f"       desc  : {desc[:120]}{'...' if len(desc)>120 else ''}")
    except Exception as exc:
        result["error"] = str(exc)
        log.exception("Error parseando respuesta de %s", source["name"])
        print(f"  ✘ PARSE ERROR: {exc}")
        print(f"  respuesta raw completa:")
        print(textwrap.indent(raw, "    "))

    return result


# ── MAIN ──────────────────────────────────────────────────────────────────────
def main() -> None:
    print(f"\n{'═'*70}")
    print(f"  TEST SCAN  —  {len(selected)} fuentes  —  provider: {LLM_PROVIDER}")
    print(f"{'═'*70}")

    results = []
    for slug, source in selected.items():
        r = test_source(slug, source)
        results.append(r)

    # ── RESUMEN ───────────────────────────────────────────────────────────────
    sep("RESUMEN FINAL", width=70)
    total_events = sum(len(r["events"]) for r in results)
    print(f"{'Fuente':<35}  {'Fetch':>5}  {'LLM':>6}  {'Eventos':>7}  {'Estado'}")
    print("─" * 70)
    for r in results:
        fetch_s = f"{r['elapsed_fetch']:.1f}s" if r["fetch_ok"] else "FAIL"
        llm_s   = f"{r['elapsed_llm']:.1f}s"   if r["llm_ok"]   else "FAIL"
        evs     = len(r["events"])              if r["parse_ok"] else "ERR"
        estado  = "✔" if r["parse_ok"] else f"✘ {r['error'][:40] if r['error'] else ''}"
        print(f"  {r['source']['name']:<33}  {fetch_s:>5}  {llm_s:>6}  {str(evs):>7}  {estado}")
    print("─" * 70)
    print(f"  Total eventos encontrados: {total_events}")
    print()

    # ── Diagnóstico automático ─────────────────────────────────────────────────
    issues = []
    for r in results:
        if not r["fetch_ok"]:
            issues.append(f"[{r['slug']}] Fetch fallido: {r['error']}")
        elif r["content_len"] < 200:
            issues.append(f"[{r['slug']}] Contenido muy corto ({r['content_len']} chars) — ¿requiere JS?")
        if r["fetch_ok"] and not r["llm_ok"]:
            issues.append(f"[{r['slug']}] LLM falló: {r['error']}")
        if r["llm_ok"] and not r["parse_ok"]:
            issues.append(f"[{r['slug']}] Parse fallido: {r['error']}")
        if r["parse_ok"] and len(r["events"]) == 0 and r["content_len"] > 500:
            issues.append(f"[{r['slug']}] LLM devolvió 0 eventos pero había {r['content_len']} chars de contenido")

    if issues:
        sep("⚠  POSIBLES PROBLEMAS DETECTADOS")
        for iss in issues:
            print(f"  • {iss}")
    else:
        print("  ✔  Sin problemas detectados")
    print()


if __name__ == "__main__":
    main()
