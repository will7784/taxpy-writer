"""
Relaciones críticas hardcodeadas del grafo de conocimiento legal.

Estas relaciones son "verdades inmutables" que el LLM podría no extraer
automáticamente o que necesitamos garantizar. Se insertan al iniciar el
sistema y sirven como fallback cuando la extracción automática falla.
"""

from __future__ import annotations

# Cada tupla: (source_chunk_uid, target_chunk_uid, relation_type, confidence, extracted_by)
# Nota: los chunks del Art. 14 D quedaron como ley_lir_art_14_d_0..5 tras el re-chunking.
CRITICAL_RELATIONS: list[tuple[str, str, str, float, str]] = [
    # ═════════════════════════════════════════════════════════════════
    # PRO-PYME / Régimen transparencia tributaria (Art. 14 D LIR)
    # ═════════════════════════════════════════════════════════════════
    ("ley_lir_art_14_d_0", "ley_codigo_tributario_art_192", "beneficia_a", 1.0, "rule"),
    ("ley_lir_art_14_d_0", "ley_lir_art_20", "tributa_con", 1.0, "rule"),
    ("ley_lir_art_14_d_0", "ley_lir_art_34", "relacionado_con", 1.0, "rule"),
    ("ley_lir_art_14_d_0", "ley_lir_art_14_e_0", "relacionado_con", 0.9, "rule"),

    # ═════════════════════════════════════════════════════════════════
    # Art. 192 CT (facilidades de pago) — conecta con PRO-PYME y condonación
    # ═════════════════════════════════════════════════════════════════
    ("ley_codigo_tributario_art_192", "ley_lir_art_14_d_0", "menciona", 1.0, "rule"),
    ("ley_codigo_tributario_art_192", "ley_codigo_tributario_art_207", "complementa", 1.0, "rule"),
    ("ley_codigo_tributario_art_192", "ley_codigo_tributario_art_56", "relacionado_con", 0.9, "rule"),

    # ═════════════════════════════════════════════════════════════════
    # Art. 56 CT (condonación de intereses y rebajas)
    # ═════════════════════════════════════════════════════════════════
    ("ley_codigo_tributario_art_56", "ley_codigo_tributario_art_192", "relacionado_con", 0.9, "rule"),

    # ═════════════════════════════════════════════════════════════════
    # Art. 21 LIR (gastos rechazados) — conecta con renta y depreciación
    # ═════════════════════════════════════════════════════════════════
    ("ley_lir_art_21", "ley_lir_art_31", "relacionado_con", 0.8, "rule"),
    ("ley_lir_art_21", "ley_lir_art_20", "relacionado_con", 0.7, "rule"),

    # ═════════════════════════════════════════════════════════════════
    # Art. 31 LIR (depreciación, gastos, renta neta)
    # ═════════════════════════════════════════════════════════════════
    ("ley_lir_art_31", "ley_lir_art_21", "relacionado_con", 0.8, "rule"),
    ("ley_lir_art_31", "ley_lir_art_30", "precede_a", 0.9, "rule"),

    # ═════════════════════════════════════════════════════════════════
    # Art. 33 bis LIR (créditos) — conecta con IVA y renta
    # ═════════════════════════════════════════════════════════════════
    ("ley_lir_art_33_bis", "ley_iva_art_23", "relacionado_con", 0.8, "rule"),
    ("ley_lir_art_33_bis", "ley_lir_art_54", "relacionado_con", 0.7, "rule"),

    # ═════════════════════════════════════════════════════════════════
    # IVA: débito/crédito fiscal (Art. 11 ↔ Art. 12 DL-825)
    # ═════════════════════════════════════════════════════════════════
    ("ley_iva_art_11", "ley_iva_art_12", "complementa", 0.9, "rule"),
    ("ley_iva_art_12", "ley_iva_art_11", "complementa", 0.9, "rule"),
    ("ley_iva_art_11", "ley_iva_art_74", "relacionado_con", 0.8, "rule"),
    ("ley_iva_art_12", "ley_iva_art_74", "relacionado_con", 0.8, "rule"),

    # Retención de IVA
    ("ley_iva_art_74", "ley_iva_art_11", "relacionado_con", 0.8, "rule"),
    ("ley_iva_art_74", "ley_iva_art_12", "relacionado_con", 0.8, "rule"),

    # ═════════════════════════════════════════════════════════════════
    # Código Tributario: procedimiento fiscal
    # Citación (63) → Liquidación (64) → Reclamación (123-124)
    # ═════════════════════════════════════════════════════════════════
    ("ley_codigo_tributario_art_63", "ley_codigo_tributario_art_64", "precede_a", 0.9, "rule"),
    ("ley_codigo_tributario_art_64", "ley_codigo_tributario_art_200", "relacionado_con", 0.8, "rule"),
    ("ley_codigo_tributario_art_64", "ley_codigo_tributario_art_97", "relacionado_con", 0.7, "rule"),
    ("ley_codigo_tributario_art_97", "ley_codigo_tributario_art_63", "relacionado_con", 0.7, "rule"),

    # Prescripción (200-201)
    ("ley_codigo_tributario_art_200", "ley_codigo_tributario_art_201", "complementa", 0.95, "rule"),
    ("ley_codigo_tributario_art_201", "ley_codigo_tributario_art_200", "complementa", 0.95, "rule"),

    # ═════════════════════════════════════════════════════════════════
    # Art. 20 LIR (renta presunta, tasa 25/27%)
    # ═════════════════════════════════════════════════════════════════
    ("ley_lir_art_20", "ley_lir_art_14_d_0", "menciona", 1.0, "rule"),
    ("ley_lir_art_20", "ley_lir_art_21", "relacionado_con", 0.8, "rule"),
    ("ley_lir_art_20", "ley_lir_art_31", "relacionado_con", 0.8, "rule"),

    # ═════════════════════════════════════════════════════════════════
    # Art. 17 LIR (dividendos y retiros)
    # ═════════════════════════════════════════════════════════════════
    ("ley_lir_art_17", "ley_lir_art_54", "relacionado_con", 0.8, "rule"),
    ("ley_lir_art_17", "ley_lir_art_14_d_0", "relacionado_con", 0.7, "rule"),

    # ═════════════════════════════════════════════════════════════════
    # Art. 14 E LIR (régimen simplificado / microempresa)
    # ═════════════════════════════════════════════════════════════════
    ("ley_lir_art_14_e_0", "ley_lir_art_14_d_0", "relacionado_con", 0.9, "rule"),
    ("ley_lir_art_14_e_0", "ley_lir_art_20", "relacionado_con", 0.8, "rule"),
]


def get_critical_relations() -> list[dict]:
    """Devuelve las relaciones críticas como diccionarios listos para insertar."""
    return [
        {
            "source_chunk_uid": src,
            "target_chunk_uid": tgt,
            "relation_type": rel,
            "confidence": conf,
            "extracted_by": by,
        }
        for src, tgt, rel, conf, by in CRITICAL_RELATIONS
    ]
