# Radar de Eventos Empresariales — Canarias

Sistema de monitorización automática que escanea las webs de 54 asociaciones empresariales
de Canarias y detecta eventos nuevos en los próximos 30 días.

Usa **LM Studio en local** con un modelo Qwen como motor de extracción, con herramientas de
búsqueda web y descarga de páginas. Al terminar genera un informe HTML interactivo y puede
enviar notificación a Teams o email.

---

## Cómo funciona

1. El script carga las 54 fuentes de `config/sources.json`.
2. Para cada fuente lanza el modelo LM Studio con dos herramientas: `fetch_url` (descarga y
   limpia el HTML de una URL) y `search_source_pages` (búsqueda complementaria).
3. El modelo navega la web de cada asociación, extrae eventos con fecha en el rango indicado
   y devuelve un JSON estructurado.
4. Los eventos nuevos (no vistos antes) se guardan en `data/known_events.json`.
5. Se genera `reports/latest_new_events.html` — visor interactivo con filtros — y
   `reports/latest_new_events.txt` como respaldo legible.
6. Si hay eventos nuevos, envía notificación al canal configurado (Teams o email).

---

## Requisitos

- Python 3.11+
- [LM Studio](https://lmstudio.ai) corriendo en local con el modelo `qwen/qwen3.5-9b` cargado
- Dependencias Python: `uv sync`

---

## Ejecución

```bash
# Crear entorno e instalar dependencias (primera vez)
uv sync

# Ejecutar el scan completo
uv run src/scanner.py
```

El scan con 54 fuentes tarda entre 30 y 90 minutos dependiendo de la velocidad del modelo.

---

## Configuración

### `config/settings.json` — parámetros operativos

```json
{
  "llm_provider": "lmstudio",
  "model": "qwen/qwen3.5-9b",
  "lmstudio_api_mode": "project-mcp",
  "lmstudio_context_window": 34096,
  "lmstudio_web_mcp_port": 8765,
  "days_ahead": 30,
  "max_workers": 4,
  "notification_channel": "teams"
}
```

| Campo | Descripción |
|-------|-------------|
| `days_ahead` | Ventana de búsqueda en días (por defecto 30) |
| `max_workers` | Fuentes procesadas en paralelo |
| `notification_channel` | `teams` o `email` |

### Variables de entorno (`.env` o entorno del sistema)

| Variable | Descripción |
|----------|-------------|
| `TEAMS_WEBHOOK_URL` | Webhook del canal de Teams |
| `EMAIL_FROM` / `EMAIL_PASSWORD` / `EMAIL_TO` | Credenciales SMTP (si usas email) |
| `SMTP_HOST` / `SMTP_PORT` | Servidor SMTP (por defecto Gmail) |

---

## Añadir o eliminar fuentes

Edita `config/sources.json`. Cada entrada:

```json
{"name": "Nombre de la asociación", "url": "https://web.org/eventos", "cat": "Categoría"}
```

Categorías disponibles: `Cámara`, `Patronal`, `Promoción`, `Clúster`, `AJE`,
`Emprendimiento`, `Turismo`, `Construcción`, `Mujeres empresarias`, `Internacional`,
`Ferias`, `Polígono`, `Industrial`, `Formación`, `Innovación`.

---

## Ajustar el prompt de extracción

Edita `config/prompt_lmstudio.txt`. Variables disponibles:

```
{source_name}     nombre de la asociación
{source_url}      URL principal
{source_category} categoría
{today_str}       fecha de hoy (DD/MM/YYYY)
{horizon}         fecha límite de la ventana (DD/MM/YYYY)
```

---

## Plantillas de notificación

Editables sin tocar Python:

| Archivo | Uso |
|---------|-----|
| `config/templates/teams_title.txt` | Título del mensaje de Teams |
| `config/templates/teams_body.txt` | Cuerpo del mensaje de Teams |
| `config/templates/email_subject.txt` | Asunto del email |
| `config/templates/email_html.html` | Cuerpo HTML del email |
| `config/templates/email_plain.txt` | Cuerpo texto plano del email |
| `config/templates/report_html.html` | Visor HTML interactivo de eventos |

---

## Visor HTML

Tras cada scan se genera `reports/latest_new_events.html`. Ábrelo en cualquier navegador.

Funcionalidades:
- Búsqueda libre por título, descripción o asociación
- Filtros por tipo de evento, categoría y asociación
- Ordenación por fecha, título o asociación
- Vista en tarjetas o tabla
- Funciona offline como archivo adjunto (sin dependencias externas)

Para personalizar el diseño edita únicamente `config/templates/report_html.html`.

---

## Estructura del proyecto

```
canarias-eventos/
├── config/
│   ├── prompt_lmstudio.txt      # Prompt editable de extracción (LM Studio)
│   ├── prompt.txt               # Prompt genérico (otros proveedores)
│   ├── settings.json            # Parámetros operativos
│   ├── sources.json             # 54 fuentes monitorizadas
│   └── templates/
│       ├── report_html.html     # Visor HTML interactivo (editable)
│       ├── teams_title.txt
│       ├── teams_body.txt
│       ├── email_subject.txt
│       ├── email_html.html
│       └── email_plain.txt
├── src/
│   ├── scanner.py               # Orquestador principal
│   ├── web_tools.py             # Herramientas de descarga y limpieza web
│   ├── lmstudio_web_mcp_server.py  # Servidor MCP local para LM Studio
│   └── scraper.py               # Utilidades de scraping
├── data/
│   └── known_events.json        # Caché de eventos ya notificados (auto-generado)
├── reports/
│   ├── latest_new_events.html   # Visor interactivo (auto-generado)
│   └── latest_new_events.txt    # Informe texto plano (auto-generado)
├── requirements.txt
└── README.md
```

---

## Fuentes monitorizadas (54)

### Cámaras de Comercio (4)
Cámara Gran Canaria · Cámara Tenerife · Cámara Lanzarote · Cámara Fuerteventura

### Patronales y confederaciones (8)
CCE · CEOE Tenerife · FEMEPA · AVAL Canarias · FEDECO Canarias · ASAGA (ASAJA) ·
CEL Lanzarote · ASINCA

### Promoción económica (3)
SPEGC · OBIDIC · PROEXCA

### Clústeres sectoriales (10)
Clúster Marítimo (CMC) · Clúster Audiovisual · CET · Clúster Enoturismo · CCTL ·
AEI Turismo Innova GC · Turisfera Tenerife · Smart Island (IncoLAB) ·
Clúster Aeronáutico · OIC ITC Canarias

### Jóvenes empresarios (3)
AJE Las Palmas · AJE Tenerife · AJE Canarias

### Emprendimiento e innovación (5)
EMERGE · Canarias Destino Startup · Emprender en Canarias · APTE · PCTT

### Turismo y hostelería (6)
Ashotel Tenerife · ASOLAN Lanzarote · FEHT Las Palmas · FTL Lanzarote ·
Cluster Turismo ITC · PROMOTUR

### Formación (1)
FPCT

### Construcción (2)
FEPECO Gran Canaria · FEPECO Tenerife

### Mujeres empresarias (2)
AMMCA · saVia Canarias

### Internacionalización (3)
AHK España · Comisión Europea R&I · EEN Canarias

### Ferias y recintos (2)
INFECAR · Recinto Ferial de Tenerife

### Polígonos industriales (2)
Parque Empresarial El Goro (AEGORO) · AMIXTA Arinaga

### Otros (3)
EFCA (Empresa Familiar) · AEDAL · AENAGA

