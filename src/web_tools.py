import re
from html import unescape
from urllib.parse import parse_qs, unquote, urljoin, urlparse

import httpx

try:
    from bs4 import BeautifulSoup as _BS
    _HAS_BS4 = True
except ImportError:
    _HAS_BS4 = False

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

EVENT_HINTS = (
    "agenda",
    "evento",
    "eventos",
    "formacion",
    "curso",
    "cursos",
    "jornada",
    "jornadas",
    "feria",
    "ferias",
    "taller",
    "workshop",
    "seminario",
)


def clean_html_text(raw_html: str) -> str:
    if _HAS_BS4:
        try:
            soup = _BS(raw_html, "html.parser")
            # Eliminar elementos no-contenido: nav, header, footer, aside, scripts, estilos, cookies
            for tag in soup.find_all(["script", "style", "nav", "header", "footer", "aside", "form"]):
                tag.decompose()
            # Eliminar divs de cookies/banners comunes
            for tag in soup.find_all(True, attrs={"class": lambda c: c and any(
                kw in " ".join(c).lower() for kw in ("cookie", "banner", "gdpr", "consent", "popup")
            )}):
                tag.decompose()
            text = soup.get_text(separator=" ")
            text = re.sub(r"\s+", " ", text)
            return text.strip()
        except Exception:
            pass
    # Fallback regex
    text = re.sub(r"<script.*?</script>", " ", raw_html, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<style.*?</style>", " ", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<[^>]+>", " ", text)
    text = unescape(text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def normalize_domain(url_or_host: str) -> str:
    parsed = urlparse(url_or_host if "://" in url_or_host else f"https://{url_or_host}")
    host = parsed.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    return host


def same_domain(candidate_url: str, source_url: str) -> bool:
    candidate_host = normalize_domain(candidate_url)
    source_host = normalize_domain(source_url)
    return candidate_host == source_host or candidate_host.endswith(f".{source_host}")


def extract_relevant_links(html: str, base_url: str, max_links: int = 20) -> list[dict[str, str]]:
    links: list[dict[str, str]] = []
    seen: set[str] = set()
    pattern = re.compile(r'<a[^>]*href=["\'](?P<href>[^"\']+)["\'][^>]*>(?P<title>.*?)</a>', re.IGNORECASE | re.DOTALL)

    candidates: list[dict[str, str]] = []
    for match in pattern.finditer(html):
        href = match.group("href").strip()
        if href.startswith("#") or href.startswith("javascript:") or href.startswith("mailto:"):
            continue

        url = urljoin(base_url, href)
        if not url.startswith("http") or not same_domain(url, base_url):
            continue
        if url in seen:
            continue

        title = clean_html_text(match.group("title"))
        lowered = f"{title} {url}".lower()
        if not any(hint in lowered for hint in EVENT_HINTS):
            continue

        seen.add(url)
        # Medir profundidad del path: más segmentos = más probable que sea una página de evento
        path_depth = len([s for s in urlparse(url).path.split("/") if s])
        candidates.append({"title": title or url, "url": url, "_depth": path_depth})

    # Priorizar URLs más profundas (páginas de evento) sobre links de navegación
    candidates.sort(key=lambda x: -x["_depth"])
    for c in candidates:
        c.pop("_depth", None)
        links.append(c)
        if len(links) >= max_links:
            break

    return links


def search_web(query: str, max_results: int = 5) -> list[dict[str, str]]:
    response = httpx.post(
        "https://html.duckduckgo.com/html/",
        data={"q": query},
        timeout=20,
        headers={"User-Agent": USER_AGENT},
    )
    response.raise_for_status()
    html = response.text

    results: list[dict[str, str]] = []
    pattern = re.compile(
        r'<a[^>]*class="result__a"[^>]*href="(?P<href>[^"]+)"[^>]*>(?P<title>.*?)</a>',
        flags=re.IGNORECASE | re.DOTALL,
    )

    for match in pattern.finditer(html):
        href = unescape(match.group("href"))
        title = clean_html_text(match.group("title"))
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
    source_host = normalize_domain(source_url)
    search_query = f"site:{source_host} {query}"
    raw_results = search_web(search_query, max_results=max_results * 2)
    filtered_results = [item for item in raw_results if same_domain(item["url"], source_url)]

    # Fallback: si DuckDuckGo no encuentra nada, extraer links relevantes de la propia página
    if not filtered_results:
        try:
            page_data = fetch_url_details(source_url, max_chars=500, max_links=30)
            links = page_data.get("discovered_links") or []
            query_words = [w.lower() for w in query.split() if len(w) > 2]
            scored: list[tuple[int, dict]] = []
            for link in links:
                link_text = f"{link.get('title', '')} {link.get('url', '')}".lower()
                score = sum(1 for w in query_words if w in link_text)
                scored.append((score, link))
            scored.sort(key=lambda x: -x[0])
            filtered_results = [lnk for _, lnk in scored[:max_results]]
        except Exception:
            pass

    return {
        "source_url": source_url,
        "query": search_query,
        "results": filtered_results[:max_results],
    }


def fetch_url_details(url: str, max_chars: int = 6000, max_links: int = 12) -> dict:
    try:
        response = httpx.get(
            url,
            timeout=20,
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT},
        )
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        return {
            "requested_url": url,
            "final_url": str(exc.request.url),
            "status": "http_error",
            "status_code": exc.response.status_code,
            "error": str(exc),
            "content": "",
            "discovered_links": [],
        }
    except Exception as exc:
        return {
            "requested_url": url,
            "final_url": url,
            "status": "error",
            "error": str(exc),
            "content": "",
            "discovered_links": [],
        }

    return {
        "requested_url": url,
        "final_url": str(response.url),
        "status": "ok",
        "status_code": response.status_code,
        "content": clean_html_text(response.text)[:max_chars],
        "discovered_links": extract_relevant_links(response.text, str(response.url), max_links=max_links),
    }