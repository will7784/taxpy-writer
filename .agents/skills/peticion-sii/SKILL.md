---
description: Redaccion de peticiones administrativas y respuestas a observaciones
  del Servicio de Impuestos Internos (SII). Cubre respuestas a citaciones, liquidaciones,
  observaciones G113, G22, y otros requerimientos.
name: peticion-sii
triggers:
- peticion administrativa
- respuesta SII
- observacion G113
- observacion G22
- citacion SII
- reclamo SII
- escrito SII
- presentacion SII
- liquidacion
- giro
- recurso reposicion
- solicitud administrativa
whenToUse: Cuando el usuario necesite redactar una peticion administrativa, respuesta
  a observacion, o cualquier escrito dirigido al Servicio de Impuestos Internos (SII).
  Ideal para responder citaciones, liquidaciones, observaciones de renta (G113, G22)
  y otros requerimientos.
---

# Skill: peticion-sii

## Cuando usar
Cuando el usuario necesite redactar una peticion administrativa, respuesta a observacion, o cualquier escrito dirigido al Servicio de Impuestos Internos (SII). Ideal para responder citaciones, liquidaciones, observaciones de renta (G113, G22) y otros requerimientos.

## Instrucciones
Eres un redactor especializado en peticion sii.

Debes redactar el documento siguiendo esta estructura:

1. **SOLICITA: Levantamiento de observación G113 y validación de costo tributario. PETICIONARIO: Hernán Calderón Argandoña. RUT: 19.636.328-9. DOMICILIO: San Olav 6477, Las Condes.**
   - Contenido esperado: SOLICITA: Levantamiento de observación G113 y validación de costo tributario. PETICIONARIO: Hernán Calderón Argandoña. RUT: 19.636.328-9. DOMICILIO: San Olav 6477, Las Condes....

2. **A LA OFICINA SANTIAGO ORIENTE: 1. RESUMEN EJECUTIVO Por medio de la presente, vengo en responder a la observación G113 practicada a mi declaración de renta AT 2026 (Folio 320981826). Solicito el levantamiento de dicha observación mediante la acreditación del costo tributario y el correcto cálculo del mayor valor obtenido en la enajenación por dación en pago del inmueble ubicado en Los Trigales 7435, amparado en el ingreso no constitutivo de renta de las 8.000 UF.**
   - Contenido esperado: A LA OFICINA SANTIAGO ORIENTE: 1. RESUMEN EJECUTIVO Por medio de la presente, vengo en responder a la observación G113 practicada a mi declaración de renta AT 2026 (Folio 320981826). Solicito el levan...

3. **2.  EXPOSICIÓN DE ANTECEDENTES (HECHOS)**
   - Contenido esperado: 2. EXPOSICIÓN DE ANTECEDENTES (HECHOS) Adquisición: Con fecha 22 de marzo de 2019, adquirí de Inmobiliaria Paz SpA el departamento B-1005, estacionamientos 190 y 191, y bodega 136, del Condominio Edif...

4. **3.  FUNDAMENTOS DE DERECHO La presente reclamación se sustenta en:**
   - Contenido esperado: 3. FUNDAMENTOS DE DERECHO La presente reclamación se sustenta en: Artículo 17 N°8, letra b) de la Ley de Impuesto a la Renta (LIR): Establece que no constituye renta el mayor valor obtenido en la enaj...

5. **4.  ARGUMENTACIÓN TÉCNICA**
   - Contenido esperado: 4. ARGUMENTACIÓN TÉCNICA Cálculo de la Ganancia: Al restar del precio de venta ($312.209.508) el costo de adquisición reajustado ($288.410.431 declarados), se determina una ganancia bruta de $23.799.0...

6. **5.  DOCUMENTACIÓN DE RESPALDO Se adjuntan los siguientes archivos legibles (formato PDF):**
   - Contenido esperado: 5. DOCUMENTACIÓN DE RESPALDO Se adjuntan los siguientes archivos legibles (formato PDF): Escritura pública de compraventa y dación en pago (documento complementario de Depto. Los Trigales). Copia con...

7. **6.  PETICIÓN CONCRETA En virtud de lo expuesto, solicito a este Servicio:**
   - Contenido esperado: 6. PETICIÓN CONCRETA En virtud de lo expuesto, solicito a este Servicio: Tener por acreditado el costo tributario de adquisición del inmueble. Validar que el mayor valor obtenido se encuentra íntegram...

Contexto adicional: Cuando el usuario necesite redactar una peticion administrativa, respuesta a observacion, o cualquier escrito dirigido al Servicio de Impuestos Internos (SII). Ideal para responder citaciones, liquidaciones, observaciones de renta (G113, G22) y otros requerimientos.

## Reglas de redaccion
- Usa lenguaje formal pero claro, dirigido al Servicio de Impuestos Internos (SII).
- Incluye fundamentos de derecho con citas exactas (articulo, ley, decreto).
- Agrega una seccion de documentacion de respaldo al final.
- Solicita al usuario los datos faltantes (RUT, nombre, direccion, etc.) antes de redactar.
- Usa el formato de la plantilla de ejemplo como referencia exacta de tono y estilo.

## Checklist de calidad
- [ ] Todos los datos del contribuyente estan completos?
- [ ] Cada afirmacion legal tiene su cita exacta?
- [ ] La peticion concreta es clara y accionable por el SII?
- [ ] Se menciona la documentacion de respaldo adjunta?

## Ejemplos

- `RESPUESTA A OBSERVACIÓN G113 RENTA AT 2026 HERNAN CALDERON.docx` — 3962 caracteres
