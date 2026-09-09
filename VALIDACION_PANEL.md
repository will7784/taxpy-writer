# Correcciones del panel local — 2026-09-07

## Uso

1. Detener el panel con Ctrl+C y volver a abrir `run_panel.bat`.
2. Recargar `http://localhost:8000`.
3. Dentro de un cliente, abrir Co-Work y pulsar **Seleccionar carpeta CoWork**. El navegador abre el selector de carpetas del equipo. Se importan copias de PDF, DOCX, TXT y MD, con límite de 25 MB por archivo. No es una vinculación ni una vigilancia automática de la carpeta original.
4. Tras la carga, pulsar **Ver entrada y procesar documentos** y después **Procesar entrada**. Los errores de lectura se registran y los documentos originales de entrada se conservan cuando falla el procesamiento.
5. En Investigación, escribir la pregunta. Se buscan fuentes y se genera una respuesta con enlaces. Las fuentes secundarias se identifican como tales.

Los archivos de subcarpetas se importan con los segmentos separados por `__`, para distinguir documentos con nombres iguales. No se sobrescriben duplicados; la pantalla informa cuáles no se cargaron.

## Causas corregidas

- CoWork no tenía un selector ni un endpoint para cargar documentación del equipo.
- Investigación listaba resultados, sin llamar al modelo para contestar la consulta.
- Los fallos de Tavily se convertían silenciosamente en una lista vacía.
- La prueba real con la consulta de arriendo entre SpA y accionista devolvió cero resultados con `country=chile`, y tres sin ese filtro. El país ahora forma parte del texto de búsqueda.
- La validación TLS del entorno local fallaba al usar el paquete de certificados de Python. Se incorporó `truststore` para utilizar la validación nativa del sistema, manteniendo la verificación de certificados e identidad. Referencia: https://truststore.readthedocs.io/en/latest/
- Las respuestas extensas de Investigación en Telegram se envían en fragmentos y sin interpretar Markdown generado por el modelo.

## Verificación realizada

- `python -m unittest discover -s tests -v`: 20 pruebas satisfactorias.
- Compilación de los módulos modificados y revisión de espacios del diff.
- `python scripts/check_panel_connectivity.py`: buscador y modelo reales. DeepSeek respondió correctamente.
- `python scripts/check_panel_connectivity.py --research`: API del panel con búsqueda y modelo reales, autenticación sustituida solo en el proceso de diagnóstico y guardado simulado. HTTP 200, tres fuentes, respuesta de 5.821 caracteres y sin advertencias. No se guardaron documentos de prueba.

La última prueba verifica el funcionamiento de la consulta, no constituye una revisión de la exactitud jurídica de la respuesta. Queda por verificar manualmente el selector de carpetas en el navegador del usuario, el OCR de documentos escaneados y el guardado en su carpeta de cliente. No se desplegó a producción.

En otra instalación, actualizar primero las dependencias con `python -m pip install -r requirements.txt`.
