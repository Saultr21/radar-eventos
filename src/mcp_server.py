"""
mcp_server.py
Servidor MCP local con herramientas web para LM Studio.
Expone fetch_url y search_source_pages sobre HTTP (streamable-http).
"""
import logging
import os

from mcp.server.fastmcp import FastMCP

from fetcher import fetch_url_details, search_source_pages

logging.basicConfig(level=logging.INFO)

PORT = int(os.environ.get("LMSTUDIO_WEB_MCP_PORT", "8765"))

mcp = FastMCP(
    "canarias-web-tools",
    instructions=(
        "Herramientas web deterministas para descubrir paginas reales del mismo sitio y leerlas "
        "sin inventar URLs. Usa fetch_url para leer páginas y search_source_pages para buscar "
        "dentro del mismo dominio."
    ),
    host="127.0.0.1",
    port=PORT,
    json_response=True,
    stateless_http=True,
)


@mcp.tool()
def search_source_pages_tool(source_url: str, query: str, max_results: int = 8) -> dict:
    """Busca paginas relevantes del mismo dominio de la fuente para evitar URLs inventadas."""
    return search_source_pages(source_url, query, max_results)


@mcp.tool(name="search_source_pages")
def search_source_pages_alias(source_url: str, query: str, max_results: int = 8) -> dict:
    """Alias explícito para LM Studio (compatibilidad con project-mcp)."""
    return search_source_pages(source_url, query, max_results)


@mcp.tool(name="fetch_url")
def fetch_url_tool(url: str, max_chars: int = 8000) -> dict:
    """Lee una URL y devuelve texto y enlaces descubiertos del mismo sitio."""
    return fetch_url_details(url, max_chars)


def main() -> None:
    mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
