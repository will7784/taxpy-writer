# Manual de uso — Impuestia

Guía práctica para entender **cómo funciona esta aplicación** y, sobre todo, qué hace el botón **"Procesar"** del Co-Work, para qué sirve y dónde deja los archivos.

> Escrito a partir del código (`cowork_manager.py`, `obsidian_writer.py`, `web_server.py`, `article_index.py`, `study_agent.py`). Si algo del comportamiento real difiere, el código manda.

---

## 1. Qué es Impuestia

Impuestia es un asistente de **derecho tributario chileno** que trabaja sobre un **buzón (vault) de Obsidian**. Tiene **dos puertas de entrada**, pero comparten el mismo motor y el mismo almacén:

| Puerta | Cómo se abre | Para qué |
|---|---|---|
| **Panel web** | `run_panel.bat` → `http://localhost:8000` | Gestionar clientes, Co-Work, investigación, revisar notas |
| **Bot de Telegram** | `telegram_mvp_bot.py` | Mismos flujos vía chat (`/procesar`, `/redactar`, `/investigar`, `/estudio`) |

Todo lo que genera se guarda como **archivos Markdown (.md) con metadatos (frontmatter)** dentro del vault, para que se vean y naveguen en Obsidian.

---

## 2. El "disco duro" de la aplicación: el vault

La base está en:

```
C:\Users\lyf-a\Dropbox\OBSIDIAN\Impuestia
```

(si no existe, se crea). Esa raíz la defines en `config.py` con `OBSIDIAN_VAULT_PATH`.

Dentro hay una carpeta **por cliente**:

```
C:\Users\lyf-a\Dropbox\OBSIDIAN\Impuestia\Clientes\<Cliente>
```

Esta carpeta del cliente (`COWORK_PATH` = `Clientes/`) es el corazón del **Co-Work**. Crea clientes desde el panel (**Trabajos → Nuevo Cliente**) o con `/cliente`.

### 2.1 Estructura que se genera para cada cliente

Al crear un cliente, Impuestia crea esta estructura con un archivo `.cliente.yaml` (nombre, RUT, rubro, régimen, contacto, notas):

```
Clientes/<Cliente>/
├── .cliente.yaml          # datos del cliente (se inyectan como contexto al redactar)
├── .trabajos/             # registros de cada "trabajo" procesado (YAML)
├── entrada/               # ← lo que subes (bandeja de ENTRADA)
├── procesados/            # ← adónde se mueve el ORIGINAL cuando se procesa bien
├── Peticiones/            # ← salida de tipo "petición"
├── Analisis/              # ← salida de tipo "análisis"
├── Notas/                 # ← salida de tipo "nota" / resúmenes de investigación
├── Jurisprudencia/        # ← salida de investigación (research_*.md)
└── Documentacion/         # documentación suelta del cliente
```

---

## 3. El flujo del Co-Work (lo que hiciste: carpeta → "procesar")

Esto es lo que viste. Paso a paso:

### Paso 1 — Entrar al cliente
En el panel: **Trabajos → clic en el cliente → botón "Co-Work"**. Se abre la pantalla con dos tarjetas: *Documentación de tu equipo* y *Estado del Co-Work*.

### Paso 2 — Subir tu carpeta ("Seleccionar carpeta CoWork")
Pulsas **"Seleccionar carpeta CoWork"**. El navegador abre el **selector de carpetas de tu equipo** (ese diálogo parecido al de "Guardar como" — a eso te refieres con "como Word"). Eliges tu carpeta.

Qué hace realmente:
- **Copia** los archivos soportados (`.pdf`, `.docx`, `.txt`, `.md` y fotos/escaneos `.png/.jpg/...`), máx. **25 MB** por archivo, dentro de `Clientes/<Cliente>/entrada/`.
- **NO vincula ni vigila** la carpeta original. Los originales quedan intactos en tu equipo. Si luego cambias un archivo en tu disco, aquí no cambia nada.
- **Subcarpetas**: los archivos que estaban en subcarpetas se van nombrando con `__`. Ej.: `Carpeta/Sub/archivo.pdf` → `Carpeta__Sub__archivo.pdf`. Así no se pisan archivos con el mismo nombre. Por eso en tus títulos ves `04 y 08 sep 2026__CostoTributario_...pdf`.
- Si un nombre ya existe, **no se sobrescribe**; la pantalla te avisa cuáles no se cargaron.

Después de cargar, aparece el enlace **"Ver entrada y procesar documentos"**.

### Paso 3 — "Procesar entrada"
Pulsas **"Procesar entrada"** (en el bot: `/procesar <Cliente>`). Esto es **"procesar"**. ¿Qué significa exactamente? Por **cada archivo** que hay en `entrada/` hace esto:

1. **Lee el texto (con OCR si hace falta).** `.txt`/`.md` se leen directo; `.docx` con `python-docx`; `.pdf` y las **imágenes** pasan por OCR de visión (GPT-4o), así que también ingiere escaneos y fotos. Requiere `OPENAI_API_KEY`; si no está, ese tipo de archivo queda en **error**.
2. **Detecta el tipo** de documento automáticamente mirando título + texto:
   - contiene *observación / citación / G113 / G22 / fiscalización / notificación / liquidación / giro / declaración / renta / IVA* → **peticion**
   - contiene *sentencia / fallo / TDT / tribunal / contrato / escritura / compraventa / sociedad / constitución* → **analisis**
   - por defecto → **analisis**
3. **Crea un registro de trabajo** (`.trabajos/<id>_<tipo>.yaml`) con estado `pendiente`.
4. **Llama al modelo de lenguaje** (`redactar_para_cliente`) con: datos del cliente (`.cliente.yaml`) + el skill relevante (ej. `peticion-sii`) + evidencia de fuentes oficiales + el texto del documento. El modelo redacta un **borrador**.
5. **Guarda el borrador** y lo registra:
   - tipo `peticion` → `Peticiones/Respuesta_<nombre>.md`
   - tipo `analisis` → `Analisis/Analisis_<nombre>.md`
   - otro → `Notas/...`
6. **Si el borrador se guarda bien**, marca el trabajo como **completado** y **mueve el original de `entrada/` a `procesados/`**. Si falla, el trabajo queda en **error** y el **original se queda en `entrada/`** (no lo pierdes).

> En una palabra: **"procesar" = convertir un documento suelto en una nota .md redactada para el cliente, y archivarlo**. No es una traducción ni un OCR forzoso; es una **redacción asistida**.

### Paso 4 — Dónde quedó todo (tu ejemplo real)

Para el cliente `Nano_Calderon` con tus documentos, esto es lo que realmente pasó:

- **Entrada** quedó vacía (todos se procesaron bien).
- **Procesados** quedó con los PDF/DOCX originales:
  - `04 y 08 sep 2026__CostoTributario_DacionDePago_DeptoLosTrigales.pdf`
  - `04 y 08 sep 2026__RESPUESTA A OBSERVACIÓN G113 RENTA AT 2026 HERNAN CALDERON.pdf`
  - etc.
- **Analisis** quedó con los borradores (ej. `analisis_20260908_104258.md` — el contrato de arrendamiento y promesa).
- **Peticiones** quedó con los borradores (ej. `peticion_20260908_104312.md` — respuesta a la citación).
- **.trabajos/** guardó cada registro YAML con estado `completado`, `archivo_entrada` y `archivo_salida` (la ruta exacta del .md generado).

La tabla **"Últimos trabajos"** del panel es justamente la lectura de esos `.trabajos` YAML.

---

## 4. ¿Cómo "tratar" un trabajo?

Cada trabajo es un **borrador** generado por el modelo. No es una respuesta jurídica final ni un hecho verificado.

1. **Abrir el trabajo**: va a `Peticiones/` o `Analisis/` como `.md`. Está en Obsidian y también desde el explorador del panel (**Explorador**).
2. **Revisarlo con criterio profesional.** Los borradores suelen incluir frases como *"Pendiente de verificación"*, *"se requiere el documento completo"*, *"se recomienda"*. Son advertencias de que el modelo no pudo confirmar todos los datos.
3. **Corregir lo que haga falta** (RUT, fechas, montos, carátula, citas exactas). El modelo no lee tu expediente completo por sí solo; usa lo que le diste.
4. **Convertir en conocimiento validado (opcional y recomendado):**
   - En **Notas aprobadas** (`/review/notas`) puedes subir/abrir una nota y marcarla **"Aprobar"**. Al aprobarla se cambia solo el `frontmatter` a `aprobada: true`.
   - Las notas **aprobadas** se consultan **con prioridad** por el bot en Investigación/Estudio **antes** de buscar en la web. Es tu forma de "enseñarle" criterios que ya validaste.

Estados posibles de un trabajo: `pendiente`, `en_proceso`, `completado`, `error`. Los de `error` te dicen el motivo (lectura fallida, LLM caído, etc.) y **no** mueven el original.

---

## 5. ¿Cómo se relaciona la jurisprudencia con los archivos y la carpeta?

Es la duda clave. Te separo dos cosas que **no** son lo mismo:

### 5.1 La jurisprudencia es una **base de conocimiento consultada**, no un producto del Co-Work
- El Co-Work ("procesar") **no** busca jurisprudencia. Redacta usando el texto del documento + datos del cliente + un skill + evidencia oficial, pero **no** va al índice de jurisprudencia.
- La jurisprudencia scrapeada del SII (fallos de los Tribunales Tributarios y Aduaneros, circulares, oficios) vive en el repositorio en `documents/jurisprudencia_sii/` y `documents/jurisprudencia_sii_circulares/`, organizada por artículo (ej. `art_17/sii_pron_17120.md`).
- `article_index.py` **indexa** esos `.md` (más todos los `.md` del vault, incluidos tus `Analisis`, `Peticiones`, `Jurisprudencia/`) y construye un **índice invertido por artículo** en `knowledge/article_index.json`. Es el puente: artículo de ley ↔ documentos que lo citan.

### 5.2 La jurisprudencia sí se usa en **Investigación** y **Estudio**
Estos dos flujos **sí** consultan la jurisprudencia:

- En **Investigación** (`/research` en el panel o `/investigar` en Telegram) escribes una pregunta (ej. *"prescripción art 200 CT"*). El sistema busca fuentes en vivo (Tavily), y además usa el índice de artículos para traer **ley + jurisprudencia + tus notas propias**, y arma una respuesta con **enlaces y pasajes**.
- En **Estudio** (`/estudio [CLIENTE] TEMA`) hace lo propio pero como documento largo.
- El resultado se guarda en `Clientes/<Cliente>/Jurisprudencia/research_*.md` y un resumen en `Clientes/<Cliente>/Notas/`.

> **En resumen:** la carpeta del cliente (y lo que genera "procesar") es tu **expediente de trabajo**. La jurisprudencia es tu **biblioteca de referencia**. Se encuentran cuando haces Investigación o Estudio: ahí el índice por artículo junta lo que tú hiciste con lo que dice la jurisprudencia y la ley.

---

## 6. Cómo arrancar

1. Configura las claves en `.env` (LLM: Gemini/DeepSeek/Kimi/etc., y `TAVILY_API_KEY` para investigación en vivo).
2. Instala dependencias: `python -m pip install -r requirements.txt`.
3. Panel: ejecuta `run_panel.bat` y abre `http://localhost:8000` (login con `ADMIN_USERNAME`/`ADMIN_PASSWORD`).
4. Bot: ejecuta `telegram_mvp_bot.py` con el token de Telegram.

---

## 7. Comandos útiles de Telegram

| Comando | Qué hace |
|---|---|
| `/procesar CLIENTE` | Igual que "Procesar entrada": procesa la bandeja `entrada/` del cliente |
| `/redactar CLIENTE TIPO INSTRUCCIONES` | Redacta a pedido (tipo `peticion`/`analisis`...). Ej.: `/redactar Nano_Calderon peticion Respuesta a observacion G113` |
| `/investigar PREGUNTA` | Investigación jurídica profunda con fuentes + jurisprudencia |
| `/estudio [CLIENTE] TEMA` | Estudio en formato documento largo |
| `/cliente` | Gestionar clientes (crear/listar) |
| `/manual` | Ayuda rápida del bot |

---

## 8. Qué NO hace la aplicación (para que no te sorprenda)

- **No edita ni vigila tu carpeta original.** Solo copia; los originales quedan en tu PC.
- **No es una respuesta jurídica final.** Los borradores requieren revisión humana; contienen advertencias y puntos que quedan pendientes.
- **OCR de visión (requiere `OPENAI_API_KEY`).** El Co-Work también OCR imágenes y PDFs escaneados. Sin esa clave, esos archivos quedan en `error`. El OCR usa GPT-4o y tiene un tope de páginas (coste/tiempo).
- **Límite de 25 MB** por archivo y **formatos** restringidos a `.pdf`, `.docx`, `.txt`, `.md` e imágenes (`.png/.jpg/...`).
- **No sobrescribe** documentos con el mismo nombre.

---

## 9. Mapa rápido de carpetas y rutas

| Concepto | Ruta / definición |
|---|---|
| Vault Obsidian | `config.OBSIDIAN_VAULT_PATH` → `C:\Users\lyf-a\Dropbox\OBSIDIAN\Impuestia` |
| Carpetas por cliente | `config.COWORK_PATH` → `<vault>/Clientes` |
| Bandeja de entrada | `<Cliente>/entrada` |
| Originales procesados | `<Cliente>/procesados` |
| Salida petición | `<Cliente>/Peticiones/Respuesta_*.md` |
| Salida análisis | `<Cliente>/Analisis/Analisis_*.md` |
| Registros de trabajo | `<Cliente>/.trabajos/*_tipo.yaml` |
| Datos del cliente | `<Cliente>/.cliente.yaml` |
| Jurisprudencia scrapeada | `documents/jurisprudencia_sii*` |
| Índice artículo ↔ documento | `knowledge/article_index.json` (lo crea `article_index.py`) |
| Resultados de investigación | `<Cliente>/Jurisprudencia/research_*.md` |

---

## 10. Tu carpeta ↔ la biblioteca: cómo pedir trabajo y unirlas en el chat

Hay dos momentos distintos: **generar** el trabajo de tu carpeta y **consultar** tu expediente junto con la biblioteca.

### 10.1 Generar el trabajo sobre tu carpeta (Co-Work)
- En el panel: **Trabajos → tu cliente → Co-Work** → *Seleccionar carpeta CoWork* → *Ver entrada y procesar documentos* → **Procesar entrada**.
- En Telegram: `/procesar CLIENTE`.
- Esto lee cada documento y produce un borrador en `Peticiones/` o `Analisis/`. Es el paso que **rellena tu expediente**.

### 10.2 Consultar tu expediente + la biblioteca (el chat que une todo)
El chat que integra tu carpeta con la **biblioteca** (leyes + jurisprudencia) y las **fuentes en vivo** es la **Investigación** (`/research` en el panel) y los comandos `/investigar` y `/estudio` en Telegram:

- En **Investigación** hay un selector **Cliente**. Al elegir uno, el sistema lee **todas las notas de ese cliente** (los borradores que generó el Co-Work) **más** la jurisprudencia y la ley relevantes, y arma una respuesta con enlaces y pasajes.
- Desde el Co-Work ahora hay un botón **"Investigar este cliente (biblioteca + expediente)"** que te lleva directo a Investigación con el cliente ya seleccionado (`/research?cliente=Nombre`).
- En Telegram es lo mismo: `/investigar CLIENTE pregunta` o `/estudio CLIENTE tema`.

### 10.3 Cómo se unen las dos cosas
El puente es **`article_index.py`**: indexa todos los `.md` del vault (incluidas tus `Analisis/` y `Peticiones/`) y la jurisprudencia scrapeada, y arma un índice **artículo de ley ↔ documentos**. Al investigar con un cliente, ese índice devuelve solo **lo tuyo + lo jurídico relevante**.

> **El Co-Work "rellena" el expediente; la Investigación lo cruza con la biblioteca.**

### 10.4 Notas de uso
- Las notas que tu Co-Work genera y que **apruebes** en *Notas aprobadas* se consultan con prioridad por el bot.
- Para que una consulta use tu expediente, **selecciona el cliente** (en el panel) o **mencionalo** en Telegram.

---

*Manual generado a partir del código fuente del proyecto. Revisa el código si necesitas el detalle fino de cada flujo.*
