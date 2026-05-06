# Radar de Eventos Empresariales — Canarias

Sistema de monitorización automática que escanea las webs de 48 asociaciones empresariales
de Canarias y extrae eventos en los próximos 30 días.

Usa **LM Studio en local** con el modelo Qwen3.5-9B como motor de extracción estructurada.
Al terminar genera un informe HTML interactivo y un informe TXT, y puede enviar el informe
por email con el HTML adjunto.

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
6. Si hay eventos y `NOTIFICATION_CHANNEL=email`, envía un correo con el HTML adjunto.

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
| `notification_channel` | `email` o `none` |

### Variables de entorno

Copia `.env.example` a `.env` y rellena los valores:

```bash
cp .env.example .env
```

| Variable | Descripción |
|----------|-------------|
| `NOTIFICATION_CHANNEL` | `email` o `none` (sobrescribe el valor de `settings.json`) |
| `EMAIL_FROM` | Buzón desde el que se envía (usuario de tu tenant M365) |
| `EMAIL_TO` | Destinatarios separados por comas |
| `AZURE_TENANT_ID` | ID del tenant de Azure AD |
| `AZURE_CLIENT_ID` | ID de la app registrada en Azure AD |
| `AZURE_CLIENT_SECRET` | Valor del secreto de la app (no el ID) |

---

## Configuración del envío de email (Microsoft Graph API)

El correo se envía mediante la API de Microsoft Graph con OAuth2 `client_credentials`,
sin contraseñas ni SMTP. El email incluye un resumen en el cuerpo y el informe completo
(`latest_new_events.html`) adjunto.

### 1. Registrar una app en Azure AD

1. Ve a [portal.azure.com](https://portal.azure.com) → **Microsoft Entra ID** → **App registrations** → **New registration**.
2. Dale un nombre (p. ej. `radar-eventos`) y haz clic en **Register**.
3. Anota el **Application (client) ID** y el **Directory (tenant) ID** — los necesitarás en `.env`.

### 2. Crear un secreto de cliente

1. Dentro de la app → **Certificates & secrets** → **New client secret**.
2. Elige una duración y haz clic en **Add**.
3. Copia el **Value** del secreto (solo se muestra una vez). Es el `AZURE_CLIENT_SECRET`.

### 3. Conceder el permiso `Mail.Send`

1. Dentro de la app → **API permissions** → **Add a permission** → **Microsoft Graph**.
2. Selecciona **Application permissions** (no Delegated) → busca `Mail.Send` → **Add**.
3. Haz clic en **Grant admin consent for [tu organización]** y confirma.

### 4. Configurar `.env`

```env
NOTIFICATION_CHANNEL=email
EMAIL_FROM=tu.nombre@tudominio.com
EMAIL_TO=destinatario@ejemplo.com,otro@ejemplo.com
AZURE_TENANT_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
AZURE_CLIENT_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
AZURE_CLIENT_SECRET=el_valor_del_secreto
```

> `EMAIL_FROM` debe ser un buzón real de tu tenant M365. La app actúa en nombre de ese usuario gracias al permiso `Mail.Send` de tipo Application.

### Nota sobre spam

Los correos enviados desde apps de Azure sin interacción humana pueden acabar en la carpeta
de Spam o Promociones del destinatario. La primera vez, márcalo como "No es spam" para que
los siguientes lleguen al inbox.

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
| `config/templates/email_subject.txt` | Asunto del email (`{total_events}`, `{today_date}`) |
| `config/templates/email_html.html` | Cuerpo HTML del email (resumen + contadores) |

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
├── .env.example                     # Plantilla de variables de entorno
├── config/
│   ├── settings.json                # Parámetros operativos
│   ├── sources.json                 # 48 fuentes monitorizadas
│   └── templates/
│       ├── report_html.html         # Visor HTML interactivo
│       ├── email_subject.txt        # Asunto del email
│       └── email_html.html          # Cuerpo del email
├── src/
│   ├── scanner.py                   # Orquestador principal
│   ├── extractor.py                 # Pipeline de extracción estructurada con LM Studio
│   ├── fetcher.py                   # Descarga y limpieza de páginas web
│   ├── events.py                    # Filtrado y agrupación de eventos
│   ├── reports.py                   # Generación de informes TXT y HTML
│   ├── config.py                    # Carga de configuración
│   ├── log_setup.py                 # Configuración de logging
│   ├── notifications/               # Envío por email (Graph API)
│   └── llm/                         # Cliente LM Studio
├── scripts/
│   └── generate_html.py             # Regenera el HTML sin relanzar el scan
├── reports/                         # Informes generados (auto-generado)
├── logs/                            # Logs de ejecución (auto-generado)
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
