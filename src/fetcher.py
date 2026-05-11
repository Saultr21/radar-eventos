"""
fetcher.py
Herramientas de obtención y parseo web.

Motor principal: Scrapling — TLS fingerprinting real, selección CSS/XPath nativa,
                 bypass automático de bloqueos anti-bot (403/429).
Fallback:        httpx + regex — activo si Scrapling no está instalado o falla.
"""
import json
import re
from html import unescape
from urllib.parse import parse_qs, unquote, urljoin, urlparse

# ── Scrapling ─────────────────────────────────────────────────────────────────
try:
    from scrapling.fetchers import Fetcher as _Fetcher
    _HAS_FETCHER = True
except ImportError:
    _HAS_FETCHER = False

# StealthyFetcher necesita `scrapling install` para descargar browsers (Camoufox).
# Si no está disponible, la lógica degrada automáticamente a _Fetcher.
try:
    from scrapling.fetchers import StealthyFetcher as _StealthyFetcher
    _HAS_STEALTHY = True
except ImportError:
    _HAS_STEALTHY = False

# ── Fallback httpx ────────────────────────────────────────────────────────────
try:
    import httpx as _httpx
    _HAS_HTTPX = True
except ImportError:
    _HAS_HTTPX = False

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

EVENT_HINTS = (
    "agenda", "evento", "eventos", "formacion", "curso", "cursos",
    "jornada", "jornadas", "feria", "ferias", "taller", "workshop",
    "seminario",
)

# Códigos HTTP que indican bloqueo anti-bot → reintentar en modo stealth
_BLOCK_CODES = {403, 429, 503}


# ── Extracción de texto ───────────────────────────────────────────────────────

_BOILERPLATE_TAGS = ("nav", "aside", "footer", "header", "script", "style", "noscript", "form", "iframe")


def _strip_boilerplate(raw_html: str) -> str:
    """Elimina nav/aside/footer/header/script/style del HTML para que el truncado
    posterior no se coma el contenido útil con menús laterales."""
    out = raw_html
    for tag in _BOILERPLATE_TAGS:
        out = re.sub(
            rf"<{tag}\b[^>]*>.*?</{tag}>",
            " ",
            out,
            flags=re.IGNORECASE | re.DOTALL,
        )
    return out


def _html_text_fallback(raw_html: str) -> str:
    """Extrae texto de HTML usando regex puro (sin dependencias externas)."""
    text = _strip_boilerplate(raw_html)
    text = re.sub(r"<[^>]+>", " ", text)
    text = unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _extract_jsonld_events(raw_html: str) -> list[dict]:
    """Detecta bloques <script type=application/ld+json> con schema.org/Event
    y devuelve sus payloads en bruto. Devolver primero estos al modelo es un
    atajo enorme: ya están estructurados."""
    events: list[dict] = []
    pattern = re.compile(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        re.IGNORECASE | re.DOTALL,
    )
    for m in pattern.finditer(raw_html):
        try:
            data = json.loads(m.group(1).strip())
        except Exception:
            continue
        items = data if isinstance(data, list) else [data]
        for item in items:
            if not isinstance(item, dict):
                continue
            graph = item.get("@graph") if "@graph" in item else [item]
            for node in graph or []:
                if not isinstance(node, dict):
                    continue
                t = node.get("@type", "")
                types = t if isinstance(t, list) else [t]
                if any("Event" in str(x) for x in types):
                    events.append(node)
    return events


def _page_html(page) -> str:
    """Devuelve el HTML como string desde un objeto Scrapling Response.
    Maneja body (bytes), html_content (str) y encoding según API actual."""
    html_attr = getattr(page, "html_content", None)
    if isinstance(html_attr, str) and html_attr:
        return html_attr
    body = getattr(page, "body", None)
    if isinstance(body, bytes):
        encoding = getattr(page, "encoding", None) or "utf-8"
        try:
            return body.decode(encoding, errors="replace")
        except (LookupError, TypeError):
            return body.decode("utf-8", errors="replace")
    if isinstance(body, str):
        return body
    return ""


def _scrapling_page_to_text(page) -> str:
    """Extrae texto limpio: limpia siempre el HTML de scripts/estilos/nav/footer
    antes de extraer texto. Devuelve string normalizado."""
    html = _page_html(page)
    if not html:
        return ""
    return _html_text_fallback(html)


# ── Extracción de enlaces ─────────────────────────────────────────────────────

def normalize_domain(url_or_host: str) -> str:
    parsed = urlparse(url_or_host if "://" in url_or_host else f"https://{url_or_host}")
    host = parsed.netloc.lower()
    return host[4:] if host.startswith("www.") else host


def same_domain(candidate_url: str, source_url: str) -> bool:
    c = normalize_domain(candidate_url)
    s = normalize_domain(source_url)
    return c == s or c.endswith(f".{s}")


def _links_from_scrapling_page(page, base_url: str, max_links: int) -> list[dict[str, str]]:
    """Extrae enlaces relevantes usando los selectores CSS nativos de Scrapling."""
    seen: set[str] = set()
    candidates: list[dict] = []
    try:
        for anchor in page.css("a[href]"):
            href = (anchor.attrib.get("href") or "").strip()
            if not href or href.startswith(("#", "javascript:", "mailto:")):
                continue
            url = urljoin(base_url, href)
            if not url.startswith("http") or not same_domain(url, base_url) or url in seen:
                continue
            title = (anchor.css("::text").get() or "").strip()
            if not any(hint in f"{title} {url}".lower() for hint in EVENT_HINTS):
                continue
            seen.add(url)
            depth = len([s for s in urlparse(url).path.split("/") if s])
            candidates.append({"title": title or url, "url": url, "_depth": depth})
    except Exception:
        pass

    candidates.sort(key=lambda x: -x["_depth"])
    for c in candidates[:max_links]:
        c.pop("_depth", None)
    return candidates[:max_links]


def _links_from_html(html: str, base_url: str, max_links: int) -> list[dict[str, str]]:
    """Extrae enlaces relevantes de HTML en bruto usando regex (fallback)."""
    seen: set[str] = set()
    candidates: list[dict] = []
    pattern = re.compile(
        r'<a[^>]*href=["\'](?P<href>[^"\']+)["\'][^>]*>(?P<text>.*?)</a>',
        re.IGNORECASE | re.DOTALL,
    )
    for m in pattern.finditer(html):
        href = m.group("href").strip()
        if href.startswith(("#", "javascript:", "mailto:")):
            continue
        url = urljoin(base_url, href)
        if not url.startswith("http") or not same_domain(url, base_url) or url in seen:
            continue
        title = unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", m.group("text")))).strip()
        if not any(hint in f"{title} {url}".lower() for hint in EVENT_HINTS):
            continue
        seen.add(url)
        depth = len([s for s in urlparse(url).path.split("/") if s])
        candidates.append({"title": title or url, "url": url, "_depth": depth})

    candidates.sort(key=lambda x: -x["_depth"])
    for c in candidates[:max_links]:
        c.pop("_depth", None)
    return candidates[:max_links]


# ── Obtención de páginas ──────────────────────────────────────────────────────

def _scrapling_get(url: str):
    """
    Intenta fetch con Scrapling Fetcher (TLS fingerprinting real).
    Si el status indica bloqueo anti-bot y StealthyFetcher está disponible,
    reintenta automáticamente en modo stealth (Camoufox + fingerprint spoofing).
    Devuelve (page_or_none, status, final_url, error_or_none).
    """
    if not _HAS_FETCHER:
        return None, None, url, "scrapling[fetchers] no instalado"

    try:
        page = _Fetcher.get(
            url,
            impersonate="chrome",
            stealthy_headers=True,
            timeout=20,
            follow_redirects=True,
        )
        status = getattr(page, "status", 200)
        final_url = str(getattr(page, "url", url))

        if status in _BLOCK_CODES and _HAS_STEALTHY:
            try:
                s_page = _StealthyFetcher.fetch(
                    url,
                    headless=True,
                    solve_cloudflare=True,
                    timeout=30,
                )
                return s_page, getattr(s_page, "status", 200), str(getattr(s_page, "url", url)), None
            except Exception:
                pass  # devolver respuesta original si stealth también falla

        return page, status, final_url, None
    except Exception as exc:
        return None, None, url, str(exc)


def _httpx_get(url: str):
    """Fallback httpx puro."""
    if not _HAS_HTTPX:
        return None, None, url, "httpx no disponible"
    try:
        resp = _httpx.get(
            url,
            timeout=20,
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT},
        )
        return resp, resp.status_code, str(resp.url), None
    except Exception as exc:
        return None, None, url, str(exc)


# ── API pública ───────────────────────────────────────────────────────────────

def fetch_url_details(url: str, max_chars: int = 6000, max_links: int = 12) -> dict:
    """
    Descarga una URL y devuelve texto limpio + enlaces relevantes del mismo dominio.

    Prioridad:
      1. Scrapling Fetcher  — TLS fingerprinting Chrome, stealthy headers
      2. Scrapling Stealth  — browser Camoufox con anti-bot bypass (si está instalado)
      3. httpx              — fallback para entornos sin Scrapling
    """
    scrapling_page, status, final_url, error = _scrapling_get(url)
    raw_html: str | None = None

    if error is not None:
        httpx_resp, status, final_url, error = _httpx_get(url)
        if error is not None:
            return {
                "requested_url": url, "final_url": final_url,
                "status": "error", "error": error,
                "content": "", "discovered_links": [],
            }
        raw_html = getattr(httpx_resp, "text", "") or ""
        scrapling_page = None  # forzar rama de fallback

    if status and status >= 400:
        return {
            "requested_url": url, "final_url": final_url,
            "status": "http_error", "status_code": status,
            "error": f"HTTP {status}", "content": "", "discovered_links": [],
        }

    # Para JSON-LD necesitamos el HTML crudo. Si no lo tenemos del fallback httpx,
    # lo extraemos de la respuesta Scrapling.
    if raw_html is None and scrapling_page is not None:
        raw_html = _page_html(scrapling_page)

    jsonld_events = _extract_jsonld_events(raw_html or "")

    if scrapling_page is not None:
        content = _scrapling_page_to_text(scrapling_page)
        links = _links_from_scrapling_page(scrapling_page, final_url, max_links)
    else:
        content = _html_text_fallback(raw_html or "")
        links = _links_from_html(raw_html or "", final_url, max_links)

    return {
        "requested_url": url,
        "final_url": final_url,
        "status": "ok",
        "status_code": status,
        "content": content[:max_chars],
        "discovered_links": links,
        "jsonld_events": jsonld_events,
    }


def search_web(query: str, max_results: int = 5) -> list[dict[str, str]]:
    """Busca en DuckDuckGo HTML y devuelve lista de resultados."""
    html = _ddg_html(query)
    return _parse_ddg_results(html, max_results)


def _ddg_html(query: str) -> str:
    """Obtiene el HTML de resultados de DuckDuckGo (siempre con httpx, es un POST simple)."""
    if not _HAS_HTTPX:
        return ""
    try:
        resp = _httpx.post(
            "https://html.duckduckgo.com/html/",
            data={"q": query},
            timeout=20,
            headers={"User-Agent": USER_AGENT},
        )
        return resp.text
    except Exception:
        return ""


def _parse_ddg_results(html: str, max_results: int) -> list[dict[str, str]]:
    results: list[dict[str, str]] = []
    pattern = re.compile(
        r'<a[^>]*class="result__a"[^>]*href="(?P<href>[^"]+)"[^>]*>(?P<title>.*?)</a>',
        re.IGNORECASE | re.DOTALL,
    )
    for m in pattern.finditer(html):
        href = unescape(m.group("href"))
        title = unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", m.group("title")))).strip()
        if not title:
            continue
        parsed = urlparse(href)
        if "duckduckgo.com" in parsed.netloc and parsed.path.startswith("/l/"):
            redirected = parse_qs(parsed.query).get("uddg", [""])[0]
            url = unquote(redirected) if redirected else href
        else:
            url = href
        if not url.startswith("http"):
            continue
        results.append({"title": title, "url": url})
        if len(results) >= max_results:
            break
    return results


def search_source_pages(source_url: str, query: str, max_results: int = 8) -> dict:
    """Busca páginas relevantes dentro del mismo dominio de la fuente."""
    source_host = normalize_domain(source_url)
    search_query = f"site:{source_host} {query}"
    raw = search_web(search_query, max_results=max_results * 2)
    filtered = [r for r in raw if same_domain(r["url"], source_url)]

    if not filtered:
        try:
            page_data = fetch_url_details(source_url, max_chars=500, max_links=30)
            links = page_data.get("discovered_links") or []
            words = [w.lower() for w in query.split() if len(w) > 2]
            filtered = sorted(
                links,
                key=lambda lnk: sum(
                    1 for w in words
                    if w in f"{lnk.get('title', '')} {lnk.get('url', '')}".lower()
                ),
                reverse=True,
            )[:max_results]
        except Exception:
            pass

    return {
        "source_url": source_url,
        "query": search_query,
        "results": filtered[:max_results],
    }
