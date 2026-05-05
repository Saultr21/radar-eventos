# Radar de Eventos Empresariales — Canarias

Sistema de monitorización automática que escanea las webs de 48 asociaciones empresariales
de Canarias y extrae eventos en los próximos 30 días.

Usa **LM Studio en local** con el modelo Qwen3.5-9B como motor de extracción estructurada.
Al terminar genera un informe HTML interactivo y un informe TXT, y puede enviar notificación
a Teams o email.

---

## Cómo funciona

1. El scanner carga las 48 fuentes de `config/sources.json`.
2. Para cada fuente descarga la página principal (y hasta 3 subpáginas relevantes).
3. Llama al modelo LM Studio con `response_format: json_schema` — extrae título, fecha,
   tipo, hora, lugar, precio, descripción y URL de cada evento.
4. Los campos `time` y `location` se completan también desde datos estructurados JSON-LD
   cuando el modelo los deja vacíos.
5. Se generan `reports/latest_new_events.html` y `reports/latest_new_events.txt`, más
   una copia con timestamp (`YYYY-MM-DD_HH-MM_events.*`) como archivo histórico.
6. Si hay eventos, envía notificación al canal configurado (Teams o email).

---

## Requisitos

- Python 3.11+
- [LM Studio](https://lmstudio.ai) corriendo en local con el modelo `qwen/qwen3.5-9b` cargado
- Dependencias Python: `uv sync`

---

## Ejecución

```bash
# Instalar dependencias (primera vez)
uv sync

# Ejecutar el scan completo
uv run src/scanner.py

# Regenerar el HTML desde el último scan (sin relanzar el scan)
uv run scripts/generate_html.py
```

El scan con 48 fuentes tarda entre 4 y 6 minutos.

---

## Configuración

### `config/settings.json`

| Campo | Descripción |
|-------|-------------|
| `model` | Nombre del modelo en LM Studio |
| `lmstudio_base_url` | URL de la API de LM Studio (por defecto `http://localhost:1234/v1`) |
| `lmstudio_context_window` | Ventana de contexto del modelo en tokens |
| `extractor_max_subpages` | Subpáginas adicionales a descargar por fuente (por defecto 3) |
| `extractor_per_page_chars` | Máximo de caracteres por página enviados al LLM |
| `days_ahead` | Ventana de búsqueda en días (por defecto 30) |
| `max_workers` | Fuentes procesadas en paralelo |
| `notification_channel` | `teams`, `email` o `none` |

### Variables de entorno

| Variable | Descripción |
|----------|-------------|
| `TEAMS_WEBHOOK_URL` | Webhook del canal de Teams |
| `EMAIL_FROM` / `EMAIL_PASSWORD` / `EMAIL_TO` | Credenciales SMTP |
| `SMTP_HOST` / `SMTP_PORT` | Servidor SMTP (por defecto Gmail, 587) |

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

## Plantillas de notificación

Editables sin tocar Python:

| Archivo | Uso |
|---------|-----|
| `config/templates/report_html.html` | Visor HTML interactivo de eventos |
| `config/templates/teams_title.txt` | Título del mensaje de Teams |
| `config/templates/teams_body.txt` | Cuerpo del mensaje de Teams |
| `config/templates/email_subject.txt` | Asunto del email |
| `config/templates/email_html.html` | Cuerpo HTML del email |
| `config/templates/email_plain.txt` | Cuerpo texto plano del email |

---

## Visor HTML

Tras cada scan se genera `reports/latest_new_events.html`. Ábrelo en cualquier navegador.

- Búsqueda libre por título, descripción o asociación
- Filtros por tipo de evento, categoría y asociación
- Ordenación por fecha, título o asociación
- Vista en tarjetas o tabla
- Funciona offline (sin dependencias externas)

Para actualizar el diseño sin relanzar el scan: edita `config/templates/report_html.html`
y ejecuta `uv run scripts/generate_html.py`.

---

## Estructura del proyecto

```
radar-eventos/
├── config/
│   ├── settings.json            # Parámetros operativos
│   ├── sources.json             # 48 fuentes monitorizadas
│   └── templates/
│       ├── report_html.html     # Visor HTML interactivo
│       ├── teams_title.txt
│       ├── teams_body.txt
│       ├── email_subject.txt
│       ├── email_html.html
│       └── email_plain.txt
├── src/
│   ├── scanner.py               # Orquestador principal
│   ├── extractor.py             # Pipeline de extracción estructurada con LM Studio
│   ├── fetcher.py               # Descarga y limpieza de páginas web
│   ├── events.py                # Filtrado y agrupación de eventos
│   ├── reports.py               # Generación de informes TXT y HTML
│   ├── config.py                # Carga de configuración
│   ├── log_setup.py             # Configuración de logging
│   ├── notifications/           # Envío por Teams y email
│   └── llm/                     # Cliente LM Studio
├── scripts/
│   └── generate_html.py         # Regenera el HTML sin relanzar el scan
├── reports/                     # Informes generados (auto-generado)
├── logs/                        # Logs de ejecución (auto-generado)
└── README.md
```

---

## Fuentes monitorizadas (48)

### Cámaras de Comercio (4)
Cámara Gran Canaria · Cámara Tenerife · Cámara Lanzarote · Cámara Fuerteventura

### Patronales y confederaciones (6)
CCE · CEOE Tenerife · FEDECO Canarias · ASAGA (ASAJA) · CEL Lanzarote · ASINCA

### Promoción económica (3)
SPEGC · OBIDIC · PROEXCA

### Clústeres sectoriales (10)
Clúster Marítimo (CMC) · Clúster Audiovisual · CET · Clúster Enoturismo · CCTL ·
AEI Turismo Innova GC · Turisfera Tenerife · Smart Island (IncoLAB) ·
Clúster Aeronáutico · OIC ITC Canarias

### Jóvenes empresarios (2)
AJE Tenerife · AJE Canarias

### Emprendimiento e innovación (4)
EMERGE · Emprender en Canarias · APTE · PCTT

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

### Otros (1)
EFCA (Empresa Familiar)
