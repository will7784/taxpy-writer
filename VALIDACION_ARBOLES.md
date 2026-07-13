# Validación de Árboles de Decisión — Taxpy

> Archivo generado automáticamente para revisión jurídica.
> Cada árbol está mapeado a artículos específicos del Código Tributario (DL-830).

---

## Índice de Árboles

| # | Archivo | Título | Artículo CT | Nodos |
|---|---------|--------|-------------|-------|
| 1 | `01_citacion_sii.json` | Citación del SII para fiscalizar | Art. 63 | 9 |
| 2 | `02_liquidacion_giro.json` | Liquidación y Giro de Oficio | Art. 64-65 | 9 |
| 3 | `03_renta_presunta.json` | Determinación de Oficio y Renta Presunta | Art. 59-61 | 7 |
| 4 | `04_prescripcion.json` | Prescripción de la Acción Tributaria | Art. 200-201 | 12 |
| 5 | `05_infracciones_sanciones.json` | Infracciones y Sanciones | Art. 97-98 | 9 |
| 6 | `06_recurso_reposicion.json` | Recurso de Reposición y Reclamación | Art. 120-122 | 12 |
| 7 | `07_intereses_mora.json` | Intereses y Reajustes por Mora | Art. 53-54 | 6 |
| 8 | `08_cobranza_embargo.json` | Cobranza Ejecutiva y Embargo | Art. 172-177 | 6 |
| 9 | `09_secrecy_tributario.json` | Secreto Tributario | Art. 35-37 | 4 |
| 10 | `10_convenio_pago.json` | Convenio de Pago y Facilidades | Art. 56, 192 | 6 |

---

## Formato de cada árbol

Cada archivo JSON tiene esta estructura:

```json
{
  "tree_id": "identificador_unico",
  "title": "Título para el usuario",
  "law": "Código Tributario (DL-830)",
  "article": "Art. XX",
  "description": "Descripción del tema",
  "tags": ["palabras", "clave", "para", "matching"],
  "root": { ... nodo inicial ... },
  "nodes": { "node_id": { ... }, ... }
}
```

### Tipos de nodo

| Tipo | Propósito | Campos clave |
|------|-----------|--------------|
| `decision` | Pregunta al usuario con opciones | `question`, `help`, `legal_ref`, `branches` |
| `info` | Explica algo y avanza automático | `summary`, `help`, `legal_ref`, `next_node` |
| `result` | Respuesta final prevalidada | `summary`, `details`, `warnings`, `next_steps`, `legal_refs` |

### Formato de branches (solo nodos `decision`)

```json
"branches": [
  {"condition": "fact_booleano", "next_node": "siguiente_nodo_id"},
  {"condition": "otro_fact", "next_node": "resultado_id"}
]
```

El `condition` es el **fact** que el LLM debe extraer del texto del usuario.

---

## Árbol 1: Citación del SII (Art. 63)

**Archivo:** `decision_trees/codigo_tributario/01_citacion_sii.json`

### Root
- **Pregunta:** ¿El SII ha citado al contribuyente para comparecer?
- **Help:** La citación es el acto formal mediante el cual el SII requiere al contribuyente que se presente ante la administración para fines de fiscalización. No es lo mismo que un requerimiento de información por escrito.
- **Legal ref:** Art. 63, inciso 1°, Código Tributario (DL-830)
- **Branches:**
  - `sii_citado` → `forma_citacion`
  - `no_citado` → `result_sin_citacion`

### Nodos

| ID | Tipo | Pregunta / Resumen | Branches / Next |
|----|------|--------------------|-----------------|
| `forma_citacion` | decision | ¿La citación consta por escrito y está firmada por el Director Regional? | `forma_correcta` → `plazo_comparecencia` <br> `forma_defectuosa` → `result_forma_defectuosa` |
| `plazo_comparecencia` | decision | ¿El plazo otorgado es de al menos 5 días hábiles? | `plazo_ok` → `comparecencia_obligatoria` <br> `plazo_corto` → `result_plazo_corto` |
| `comparecencia_obligatoria` | info | La citación cumple requisitos. El contribuyente está OBLIGADO a comparecer. | → `derechos_contribuyente` |
| `derechos_contribuyente` | decision | ¿El contribuyente desea conocer sus derechos durante la fiscalización? | `conocer_derechos` → `result_derechos` <br> `no_conocer` → `result_obligaciones_basicas` |

### Resultados

| ID | Resumen | Legal refs |
|----|---------|------------|
| `result_sin_citacion` | Sin citación formal, no hay obligación de comparecer. El SII puede requerir antecedentes por escrito (Art. 62). | Art. 60, 62, 63 CT |
| `result_forma_defectuosa` | Vicios formales → puede NO comparecer e impugnar. Plazo 30 días (Art. 122). | Art. 63 inc. 2°, 120, 122, 8 CT |
| `result_plazo_corto` | Plazo < 5 días hábiles → reclamable. Opción: comparecer bajo protesta. | Art. 63 inc. 3°, 122, 8 CT |
| `result_derechos` | Derechos: asistencia, no autoincriminarse, información, plazos razonables, reclamar formalidades, reserva terceros. | Art. 63, 35, 8, 60, 97 N° 6 CT |
| `result_obligaciones_basicas` | Obligaciones: comparecer, presentar antecedentes, no declarar contra sí mismo. Multa 5-20 UTAs si no comparece. | Art. 63, 97 N° 6, 59 CT |

---

## Árbol 2: Liquidación y Giro de Oficio (Art. 64-65)

**Archivo:** `decision_trees/codigo_tributario/02_liquidacion_giro.json`

### Root
- **Pregunta:** ¿El SII ha emitido una liquidación de oficio?
- **Help:** La liquidación de oficio es el acto por el cual el SII determina la deuda tributaria cuando el contribuyente no presenta declaración, la presenta en forma incorrecta, o no paga.
- **Legal ref:** Art. 64, Código Tributario (DL-830)
- **Branches:**
  - `sii_emitio_liquidacion` → `notificacion_valida`
  - `no_liquidacion` → `result_sin_liquidacion`

### Nodos

| ID | Tipo | Pregunta / Resumen | Branches / Next |
|----|------|--------------------|-----------------|
| `notificacion_valida` | decision | ¿La liquidación fue notificada válidamente? | `notificacion_valida` → `plazo_pago` <br> `no_notificada` → `result_no_notificada` |
| `plazo_pago` | decision | ¿Han transcurrido más de 15 días hábiles sin pagar ni reclamar? | `dentro_plazo` → `opciones_contribuyente` <br> `plazo_vencido` → `result_plazo_vencido` |
| `opciones_contribuyente` | decision | ¿Qué quiere hacer el contribuyente? | `pagar` → `result_pagar` <br> `reclamar` → `result_reclamar` <br> `pagar_protesta` → `result_pagar_protesta` |

### Resultados

| ID | Resumen | Legal refs |
|----|---------|------------|
| `result_sin_liquidacion` | Sin liquidación → régimen de declaración jurada. Declarar y pagar voluntariamente. | Art. 64, 65, 201 CT |
| `result_no_notificada` | Notificación defectuosa → plazos NO corren. Reclamación por forma (Art. 122). | Art. 64, 124, 122 CT |
| `result_plazo_vencido` | Liquidación FIRME. SII puede iniciar cobranza ejecutiva. Convenio de pago o PRO-PYME. | Art. 65, 56, 172-177, 192, 122 CT |
| `result_pagar` | Pago voluntario dentro de 15 días → sin intereses ni multas adicionales. | Art. 65, 81, 56 CT |
| `result_reclamar` | Reclamación por fondo (Art. 121) o por forma (Art. 122). Plazo 15 días. No confundir con reposición. | Art. 121, 122, 120, 65 CT |
| `result_pagar_protesta` | Paga pero deja constancia de desacuerdo. Permite reclamar después sin perder firmeza. | Art. 65, 121, 200, 81 CT |

---

## Árbol 3: Determinación de Oficio y Renta Presunta (Art. 59-61)

**Archivo:** `decision_trees/codigo_tributario/03_renta_presunta.json`

### Root
- **Pregunta:** ¿El SII ha determinado la renta o deuda de oficio?
- **Help:** La determinación de oficio es el acto más gravoso del SII. Ocurre cuando el contribuyente no declara, declara mal, o no responde requerimientos.
- **Legal ref:** Art. 59, Código Tributario (DL-830)
- **Branches:**
  - `sii_determino_oficio` → `causal_determinacion`
  - `no_determinacion` → `result_sin_determinacion`

### Nodos

| ID | Tipo | Pregunta / Resumen | Branches / Next |
|----|------|--------------------|-----------------|
| `causal_determinacion` | decision | ¿Cuál fue la causal de la determinación de oficio? | `no_declaro` → `result_no_declaro` <br> `declaro_incompleta` → `result_incompleta` <br> `no_atendio_requerimiento` → `result_no_atendio` <br> `operaciones_ficticias` → `result_ficticias` <br> `retiro_utilidades_sociedad` → `result_retiro` |

### Resultados

| ID | Resumen | Legal refs |
|----|---------|------------|
| `result_sin_determinacion` | Régimen de declaración jurada. Declarar y pagar oportunamente. | Art. 59, 62, 125 CT |
| `result_no_declaro` | Causal: no declaró (Art. 59 inc. 1°). Multa 20%-100% renta omitida. Rectificación tardía (Art. 109) es más barata. | Art. 59 inc. 1°, 97 N° 1, 109, 121, 125 CT |
| `result_incompleta` | Causal: declaración incompleta/inexacta (Art. 59 inc. 2°). SII corrige omisiones. Rectificación (Art. 125) tiene multa reducida. | Art. 59 inc. 2°, 125, 97 N° 1, 21 LIR |
| `result_no_atendio` | Causal: no atendió requerimiento/citación (Art. 59 inc. 3°). Verificar notificación válida. Causal justificada documentada. | Art. 59 inc. 3°, 62, 63, 121, 122 CT |
| `result_ficticias` | Causal: operaciones ficticias (Art. 59 inc. 4°). SII presume renta. Carga de prueba invertida. | Art. 59 inc. 4°, 97 N° 1, 21 LIR, 41 LIR |
| `result_retiro` | Causal: retiro de utilidades de sociedad sin declarar (Art. 59 inc. 5°). Aplica a socios/accionistas. | Art. 59 inc. 5°, 14 N° 1, 14 N° 2 LIR, 97 N° 1 CT |

---

## Árbol 4: Prescripción de la Acción Tributaria (Art. 200-201)

**Archivo:** `decision_trees/codigo_tributario/04_prescripcion.json`

### Root
- **Pregunta:** ¿El SII está exigiendo el pago de una obligación tributaria?
- **Help:** La prescripción es la extinción de la obligación tributaria por el transcurso del tiempo. El plazo general es 3 años (Art. 200 CT), pero hay excepciones (Art. 201 CT).
- **Legal ref:** Art. 200, Código Tributario (DL-830)
- **Branches:**
  - `sii_exige_pago` → `plazo_transcurrido`
  - `no_exige` → `result_no_exige`

### Nodos

| ID | Tipo | Pregunta / Resumen | Branches / Next |
|----|------|--------------------|-----------------|
| `plazo_transcurrido` | decision | ¿Han transcurrido más de 3 años desde el vencimiento de la obligación tributaria? | `mas_3_anos` → `causal_interrupcion` <br> `menos_3_anos` → `result_no_prescrito` |
| `causal_interrupcion` | decision | ¿Ha ocurrido alguna causal de interrupción de la prescripción? | `interrumpida` → `result_interrumpida` <br> `no_interrumpida` → `result_prescrito` |
| `result_no_exige` | info | Si el SII no está exigiendo pago, no aplica prescripción. Verificar deuda real en sii.cl. | (resultado) |
| `result_no_prescrito` | info | Han transcurrido menos de 3 años. La obligación NO está prescrita. El SII puede exigirla. | (resultado) |
| `result_interrumpida` | info | La prescripción fue interrumpida. El plazo de 3 años comenzó de nuevo desde la interrupción. | (resultado) |
| `result_prescrito` | info | Han transcurrido más de 3 años sin interrupción. La obligación ESTÁ PRESCRITA. El contribuyente puede oponerse. | (resultado) |

> **Nota:** Este árbol tiene varios nodos `info` con `next_node` encadenados que explican paso a paso el análisis de prescripción.

---

## Árbol 5: Infracciones y Sanciones (Art. 97-98)

**Archivo:** `decision_trees/codigo_tributario/05_infracciones_sanciones.json`

### Root
- **Pregunta:** ¿El SII ha notificado al contribuyente una infracción o multa tributaria?
- **Help:** Las infracciones tributarias están en el Art. 97 CT. Cada numeral tiene una multa específica. Es distinto de una liquidación de oficio.
- **Legal ref:** Art. 97, Código Tributario (DL-830)
- **Branches:**
  - `multa_notificada` → `tipo_infraccion`
  - `no_multa` → `result_sin_multa`

### Nodos

| ID | Tipo | Pregunta / Resumen | Branches / Next |
|----|------|--------------------|-----------------|
| `tipo_infraccion` | decision | ¿Qué tipo de infracción se imputa? | `no_declarar` → `result_no_declarar` <br> `falsa_declaracion` → `result_falsa` <br> `no_pagar` → `result_no_pagar` <br> `no_retener` → `result_no_retener` <br> `no_atender_requerimiento` → `result_no_atender` <br> `no_comparecer` → `result_no_comparecer` <br> `obstruccion` → `result_obstruccion` <br> `infraccion_aduanera` → `result_aduanera` |

### Resultados (uno por numeral del Art. 97)

| ID | Infracción | Multa | Legal refs |
|----|-----------|-------|------------|
| `result_sin_multa` | Sin multa notificada → no aplica sanción. | — | Art. 97 CT |
| `result_no_declarar` | No presentar declaración (Art. 97 N° 1) | 20%-100% renta omitida | Art. 97 N° 1, 109 CT |
| `result_falsa` | Falsa declaración (Art. 97 N° 2) | 20%-100% renta omitida | Art. 97 N° 2, 109 CT |
| `result_no_pagar` | No pagar tributos (Art. 97 N° 3) | 5%-30% deuda + intereses | Art. 97 N° 3, 53, 54 CT |
| `result_no_retener` | No retener o enterar (Art. 97 N° 4) | 5%-30% deuda + intereses | Art. 97 N° 4, 53, 54 CT |
| `result_no_atender` | No atender requerimiento (Art. 97 N° 5) | 5%-30% deuda + intereses | Art. 97 N° 5, 62 CT |
| `result_no_comparecer` | No comparecer a citación (Art. 97 N° 6) | 5-20 UTAs | Art. 97 N° 6, 63 CT |
| `result_obstruccion` | Obstrucción a fiscalización (Art. 97 N° 7) | 10%-50% deuda | Art. 97 N° 7, 60 CT |
| `result_aduanera` | Infracciones aduaneras (Art. 97 N° 8) | Variable según normas aduaneras | Art. 97 N° 8, 120-122 CT |

---

## Árbol 6: Recurso de Reposición y Reclamación (Art. 120-122)

**Archivo:** `decision_trees/codigo_tributario/06_recurso_reposicion.json`

### Root
- **Pregunta:** ¿Qué acto del SII quiere impugnar el contribuyente?
- **Help:** El Art. 120 CT (reposición) vs. Art. 121-122 CT (reclamación) son vías distintas. La reposición va contra actos administrativos; la reclamación va contra liquidaciones.
- **Legal ref:** Art. 120-122, Código Tributario (DL-830)
- **Branches:**
  - `acto_administrativo` → `tipo_acto`
  - `liquidacion` → `tipo_reclamacion`
  - `no_sabe` → `result_que_impugnar`

### Nodos

| ID | Tipo | Pregunta / Resumen | Branches / Next |
|----|------|--------------------|-----------------|
| `tipo_acto` | decision | ¿Qué tipo de acto administrativo? | `resolucion` → `plazo_reposicion` <br> `despacho` → `plazo_reposicion` <br> `providencia` → `plazo_reposicion` |
| `plazo_reposicion` | info | Plazo para recurso de reposición: 15 días hábiles desde notificación. | → `result_reposicion` |
| `tipo_reclamacion` | decision | ¿Reclamación por fondo o por forma? | `por_fondo` → `plazo_reclamacion_fondo` <br> `por_forma` → `plazo_reclamacion_forma` |
| `plazo_reclamacion_fondo` | info | Plazo 15 días hábiles desde notificación de liquidación. | → `result_reclamacion_fondo` |
| `plazo_reclamacion_forma` | info | Plazo 30 días desde notificación del acto con vicios formales. | → `result_reclamacion_forma` |

### Resultados

| ID | Resumen | Legal refs |
|----|---------|------------|
| `result_que_impugnar` | Explica diferencia entre reposición (Art. 120) y reclamación (Art. 121-122). | Art. 120-122 CT |
| `result_reposicion` | Reposición contra actos administrativos. Plazo 15 días. SII debe resolver en 30 días. | Art. 120 CT |
| `result_reclamacion_fondo` | Reclamación por fondo: impugna renta, tasa, créditos. Plazo 15 días. | Art. 121 CT |
| `result_reclamacion_forma` | Reclamación por forma: vicios formales del acto. Plazo 30 días. | Art. 122 CT |

---

## Árbol 7: Intereses y Reajustes por Mora (Art. 53-54)

**Archivo:** `decision_trees/codigo_tributario/07_intereses_mora.json`

### Root
- **Pregunta:** ¿El contribuyente tiene una deuda tributaria vencida y sin pagar?
- **Help:** Los intereses por mora se calculan desde el vencimiento de la obligación. La tasa es la que fija el Banco Central (Art. 53 CT).
- **Legal ref:** Art. 53, Código Tributario (DL-830)
- **Branches:**
  - `deuda_vencida` → `tipo_deuda`
  - `no_deuda` → `result_sin_deuda`

### Nodos

| ID | Tipo | Pregunta / Resumen | Branches / Next |
|----|------|--------------------|-----------------|
| `tipo_deuda` | decision | ¿Qué tipo de deuda? | `tributaria` → `info_intereses` <br> `previsional` → `result_previsional` <br> `aduana` → `result_aduana` |
| `info_intereses` | info | Intereses: tasa activa Banco Central + 50% (Art. 53 CT). Reajuste: IPC acumulado (Art. 54 CT). | → `result_intereses` |

### Resultados

| ID | Resumen | Legal refs |
|----|---------|------------|
| `result_sin_deuda` | Sin deuda vencida → no aplica intereses. Verificar en sii.cl. | Art. 53, 54 CT |
| `result_previsional` | Deuda previsional → reglas distintas (DL-3500, no Art. 53 CT). | DL-3500 |
| `result_aduana` | Deuda aduanera → intereses y reajustes según Código Aduanero. | Código Aduanero |
| `result_intereses` | Deuda tributaria: intereses = tasa BCentral + 50%. Reajuste = IPC. Ejemplo de cálculo. | Art. 53, 54 CT |

---

## Árbol 8: Cobranza Ejecutiva y Embargo (Art. 172-177)

**Archivo:** `decision_trees/codigo_tributario/08_cobranza_embargo.json`

### Root
- **Pregunta:** ¿El SII ha iniciado cobranza ejecutiva contra el contribuyente?
- **Help:** La cobranza ejecutiva es el procedimiento forzoso para cobrar deudas tributarias firmes. Incluye embargo, retención, remate.
- **Legal ref:** Art. 172, Código Tributario (DL-830)
- **Branches:**
  - `cobranza_iniciada` → `etapa_cobranza`
  - `no_cobranza` → `result_sin_cobranza`

### Nodos

| ID | Tipo | Pregunta / Resumen | Branches / Next |
|----|------|--------------------|-----------------|
| `etapa_cobranza` | decision | ¿En qué etapa está la cobranza? | `apremio` → `result_apremio` <br> `embargo` → `result_embargo` <br> `retencion` → `result_retencion` <br> `remate` → `result_remate` <br> `frustratoria` → `result_frustratoria` |

### Resultados

| ID | Resumen | Legal refs |
|----|---------|------------|
| `result_sin_cobranza` | Sin cobranza → puede regularizar voluntariamente. Convenio de pago (Art. 56). | Art. 56, 172 CT |
| `result_apremio` | Etapa de apremio: intimación de pago. Plazo 5 días para pagar o presentar garantía. | Art. 172-173 CT |
| `result_embargo` | Embargo de bienes: inscripción en conservador. Bienes inembargables (Art. 177). | Art. 174-177 CT |
| `result_retencion` | Retención de terceros: retenedores (bancos, empleadores). Obligación de retener. | Art. 176 CT |
| `result_remate` | Remate de bienes embargados. Derecho a excedentes. | Art. 175 CT |
| `result_frustratoria` | Cobranza frustratoria: liquidación de empresa, quiebra. | Art. 172, 56 CT |

---

## Árbol 9: Secreto Tributario (Art. 35-37)

**Archivo:** `decision_trees/codigo_tributario/09_secrecy_tributario.json`

### Root
- **Pregunta:** ¿Qué situación involucra secreto o acceso a información tributaria?
- **Help:** El secreto tributario protege la información del contribuyente. Solo ciertos funcionarios pueden acceder y bajo causales específicas.
- **Legal ref:** Art. 35, Código Tributario (DL-830)
- **Branches:**
  - `acceso_indebido` → `result_acceso_indebido`
  - `filtracion` → `result_filtracion`
  - `consulta_propio` → `result_consulta`
  - `tercero_solicita` → `result_tercero`

> **Nota:** Este árbol es más simple: 4 resultados directos desde root, sin nodos intermedios.

### Resultados

| ID | Resumen | Legal refs |
|----|---------|------------|
| `result_acceso_indebido` | Acceso indebido por funcionario: denunciable ante Contraloría y TTA. | Art. 35, 36, 122 CT |
| `result_filtracion` | Filtración de info tributaria: delito penal (Art. 292 CP) + administrativo. | Art. 35 CT, Art. 292 CP |
| `result_consulta` | El contribuyente puede acceder a su propia info en sii.cl. Derecho de información. | Art. 35 CT |
| `result_tercero` | Terceros NO pueden acceder a info ajena. Excepciones muy limitadas (Art. 36). | Art. 35, 36 CT |

---

## Árbol 10: Convenio de Pago y Facilidades (Art. 56, 192)

**Archivo:** `decision_trees/codigo_tributario/10_convenio_pago.json`

### Root
- **Pregunta:** ¿El contribuyente tiene una deuda tributaria y quiere fraccionarla?
- **Help:** El convenio de pago permite fraccionar deudas tributarias. PRO-PYME (Art. 192 CT) es un régimen especial para pymes.
- **Legal ref:** Art. 56, Código Tributario (DL-830)
- **Branches:**
  - `quiere_fraccionar` → `califica_pro_pyme`
  - `no_quiere` → `result_no_convenio`

### Nodos

| ID | Tipo | Pregunta / Resumen | Branches / Next |
|----|------|--------------------|-----------------|
| `califica_pro_pyme` | decision | ¿El contribuyente califica como PRO-PYME (Art. 14 D LIR)? | `si_pro_pyme` → `info_pro_pyme` <br> `no_pro_pyme` → `result_convenio_normal` |
| `info_pro_pyme` | info | PRO-PYME: hasta 48 cuotas, tasa reducida, sin garantía hasta cierto monto. | → `result_pro_pyme` |

### Resultados

| ID | Resumen | Legal refs |
|----|---------|------------|
| `result_no_convenio` | Sin convenio → pagar de contado o esperar cobranza. | Art. 56 CT |
| `result_convenio_normal` | Convenio ordinario: hasta 24 cuotas, requiere garantía, intereses normales. | Art. 56 CT |
| `result_pro_pyme` | PRO-PYME: hasta 48 cuotas, tasa preferencial, facilidades de garantía. | Art. 192 CT, Art. 14 D LIR |

---

## Cómo validar un árbol

### 1. Verificar referencias legales
Cada nodo tiene `legal_ref` o `legal_refs`. Verifica que:
- El artículo citado existe en el DL-830
- El inciso o numeral es correcto
- La interpretación es la vigente (no derogada)
- **¿Este tema tiene un componente de vigencia/fecha que falta representar?**
  La tributación cambia con el tiempo (ej. venta de inmuebles: el régimen depende
  de la fecha de adquisición). Si el artículo menciona reformas, normas
  transitorias o "a partir del"/"con anterioridad a", el árbol debería preguntar
  la fecha relevante en vez de asumir un solo régimen vigente.

### 2. Verificar condiciones (facts)
Las `branches` usan `condition` como identificador booleano. Ejemplos:
- `sii_citado`, `no_citado` → mutuamente excluyentes
- `forma_correcta`, `forma_defectuosa` → mutuamente excluyentes

Verifica que:
- Las condiciones sean claras para el LLM
- No haya condiciones ambiguas o superpuestas
- Cada nodo `decision` tenga al menos 2 branches

### 3. Verificar completitud del camino
Desde `root`, cada branch debe llevar a:
- Otro nodo `decision` (más preguntas)
- Un nodo `info` (explicación intermedia)
- Un nodo `result` (respuesta final)

No debe haber caminos sin salida.

### 4. Verificar contenido de resultados
Cada nodo `result` debe tener:
- `summary`: respuesta corta (1-2 párrafos)
- `details`: respuesta completa (3-5 párrafos)
- `warnings`: al menos 1 advertencia clave
- `next_steps`: acciones concretas para el usuario
- `legal_refs`: fuentes exactas

### 5. Probar el árbol
Puedes probar un árbol individual con este comando:

```bash
python -c "
from decision_engine import engine
from pprint import pprint

tree = engine._trees['citacion_sii']
facts = {'sii_citado': True, 'forma_correcta': True, 'plazo_ok': True, 'conocer_derechos': True}
result, path = engine.walk_tree(tree, facts)
print('Path:', [n.id for n in path])
print('Result:', result.id)
print(engine.render_result(tree, result, path, include_diagram=True))
"
```

---

## Archivos fuente

Los archivos originales están en:
```
decision_trees/codigo_tributario/
├── 01_citacion_sii.json
├── 02_liquidacion_giro.json
├── 03_renta_presunta.json
├── 04_prescripcion.json
├── 05_infracciones_sanciones.json
├── 06_recurso_reposicion.json
├── 07_intereses_mora.json
├── 08_cobranza_embargo.json
├── 09_secrecy_tributario.json
└── 10_convenio_pago.json
```

Para editar un árbol, modifica directamente el JSON correspondiente y reinicia el bot.
