"""
log_setup.py
Configura logging para consola (con colores) y fichero rotativo.
Llamar a setup_logging() al inicio del proceso principal.
"""
import logging
import logging.handlers
from pathlib import Path

# ── Colores ANSI para consola ─────────────────────────────────────────────────
_RESET  = "\033[0m"
_GREY   = "\033[90m"
_CYAN   = "\033[96m"
_GREEN  = "\033[92m"
_YELLOW = "\033[93m"
_RED    = "\033[91m"
_BOLD   = "\033[1m"

_LEVEL_COLORS = {
    logging.DEBUG:    _GREY,
    logging.INFO:     _CYAN,
    logging.WARNING:  _YELLOW,
    logging.ERROR:    _RED,
    logging.CRITICAL: _BOLD + _RED,
}


class _ColorFormatter(logging.Formatter):
    def __init__(self):
        super().__init__()

    def format(self, record: logging.LogRecord) -> str:
        color = _LEVEL_COLORS.get(record.levelno, _RESET)
        ts    = self.formatTime(record, "%H:%M:%S")
        level = f"{color}{record.levelname:<8}{_RESET}"
        name  = f"{_GREY}{record.name}{_RESET}"
        msg   = record.getMessage()

        exc = ""
        if record.exc_info:
            exc = "\n" + _RED + self.formatException(record.exc_info) + _RESET

        return f"{_GREY}{ts}{_RESET} {level} {name}  {msg}{exc}"


class _PlainFormatter(logging.Formatter):
    """Sin colores para el fichero de log."""
    def __init__(self):
        super().__init__(
            fmt="%(asctime)s [%(levelname)-8s] %(name)s  %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )


def setup_logging(log_dir: Path, level: int = logging.INFO) -> Path:
    """
    Configura el sistema de logging global:
      - Consola: colores, hora corta (HH:MM:SS)
      - Fichero: logs/scanner_YYYY-MM-DD.log (rotación diaria, 14 copias)

    Silencia los loggers ruidosos de httpx/hpack/mcp al nivel WARNING.

    Devuelve la ruta del fichero de log activo.
    """
    log_dir.mkdir(parents=True, exist_ok=True)
    from datetime import date
    log_file = log_dir / f"scanner_{date.today()}.log"

    root = logging.getLogger()
    root.setLevel(level)

    # Evitar handlers duplicados si se llama varias veces
    root.handlers.clear()

    # ── Consola ───────────────────────────────────────────────────────────────
    console = logging.StreamHandler()
    console.setLevel(level)
    console.setFormatter(_ColorFormatter())
    root.addHandler(console)

    # ── Fichero (rotación diaria) ──────────────────────────────────────────────
    file_handler = logging.handlers.TimedRotatingFileHandler(
        log_file, when="midnight", backupCount=14, encoding="utf-8"
    )
    file_handler.setLevel(logging.DEBUG)   # el fichero captura todo
    file_handler.setFormatter(_PlainFormatter())
    root.addHandler(file_handler)

    # ── Silenciar librerías ruidosas ───────────────────────────────────────────
    for noisy in ("httpx", "httpcore", "hpack", "mcp", "asyncio"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    return log_file
