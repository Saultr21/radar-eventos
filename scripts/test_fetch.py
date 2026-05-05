"""
test_fetch.py
Prueba el fetcher para una URL y muestra qué contenido ve el LLM.

Uso:
    uv run scripts/test_fetch.py https://ceoe-tenerife.com/eventos/
    uv run scripts/test_fetch.py  (usa URL por defecto)
"""
import sys
import textwrap
from pathlib import Path

# Añadir src al path para importar los módulos del proyecto
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from fetcher import fetch_url_details
from config import LMSTUDIO_FETCH_CHARS

URL = sys.argv[1] if len(sys.argv) > 1 else "https://ceoe-tenerife.com/eventos/"
MAX_CHARS = int(sys.argv[2]) if len(sys.argv) > 2 else LMSTUDIO_FETCH_CHARS


def separator(title: str = "") -> None:
    if title:
        print(f"\n{'─'*60}")
        print(f"  {title}")
        print(f"{'─'*60}")
    else:
        print("─" * 60)


def main() -> None:
    print(f"\nFetching: {URL}")
    print(f"max_chars: {MAX_CHARS}")

    result = fetch_url_details(URL, max_chars=MAX_CHARS, max_links=15)

    separator("ESTADO")
    print(f"  status      : {result.get('status')}")
    print(f"  status_code : {result.get('status_code')}")
    print(f"  final_url   : {result.get('final_url')}")
    if result.get("error"):
        print(f"  error       : {result.get('error')}")

    content = result.get("content", "")
    separator("CONTENIDO")
    print(f"  longitud total : {len(content)} chars")
    print(f"  primeros 800 chars:")
    print()
    print(textwrap.indent(content[:800], "    "))

    links = result.get("discovered_links", [])
    separator(f"ENLACES RELEVANTES ({len(links)})")
    for lnk in links:
        print(f"  [{lnk.get('title', '')[:50]}]  {lnk.get('url')}")

    separator()

    # Diagnóstico: ¿podría necesitar JS?
    if len(content) < 200:
        print("\n⚠  Contenido muy corto — la página probablemente necesita JavaScript.")
        print("   Considera usar StealthyFetcher (scrapling install) para renderizado real.")
    elif len(content) < 500:
        print("\n⚠  Contenido corto — puede ser una SPA que carga datos por JS.")
    else:
        print(f"\n✔  Contenido recibido OK ({len(content)} chars)")

    # Mostrar muestra del final también (donde suelen estar los eventos)
    if len(content) > 800:
        separator("ÚLTIMOS 400 CHARS DEL CONTENIDO")
        print(textwrap.indent(content[-400:], "    "))


if __name__ == "__main__":
    main()
