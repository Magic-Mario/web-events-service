"""T044 · Parsing del wikitext de Wikipedia — **sin salir a la red**.

La red (y sus `429`) se degradan a semilla en `test_fallback.py`; aquí sólo
se prueba la parte determinista: limpiar el marcado, extraer viñetas y
recortar el wikitext en secciones con los `codepointOffset` del `tocdata`.
"""

from __future__ import annotations

from sources.wikipedia import parse_bullets, slice_sections, strip_markup

WIKITEXT = (
    "== Acontecimientos ==\n"
    "\n"
    "=== Enero ===\n"
    "\n"
    "* [[1 de enero]]: \n"
    "**Un suceso importante de enero que todos recuerdan.\n"
    "**Otro suceso de enero también muy interesante.\n"
    "\n"
    "=== Febrero ===\n"
    "\n"
    "* [[2 de febrero]] – Un suceso de febrero que nadie olvida.\n"
    "\n"
    "== Deporte ==\n"
    "\n"
    "El equipo local cierra una temporada memorable.\n"
    "\n"
    "=== Fútbol ===\n"
    "\n"
    "* Un equipo asciende de categoría tras un año duro.\n"
)


def _toc() -> list[dict]:
    """Índice mínimo con la forma real de `prop=tocdata`."""
    return [
        {"line": "Acontecimientos", "index": "1", "tocLevel": 1,
         "codepointOffset": WIKITEXT.index("== Acontecimientos ==")},
        {"line": "Enero", "index": "2", "tocLevel": 2,
         "codepointOffset": WIKITEXT.index("=== Enero ===")},
        {"line": "Febrero", "index": "3", "tocLevel": 2,
         "codepointOffset": WIKITEXT.index("=== Febrero ===")},
        {"line": "Deporte", "index": "4", "tocLevel": 1,
         "codepointOffset": WIKITEXT.index("== Deporte ==")},
        {"line": "Fútbol", "index": "5", "tocLevel": 2,
         "codepointOffset": WIKITEXT.index("=== Fútbol ===")},
    ]


# --- limpieza del marcado --------------------------------------------------


def test_wiki_markup_is_stripped_away() -> None:
    text = strip_markup(
        "El alcalde de [[Lima|la capital]] firma el acuerdo<ref>Archivo 1990</ref>"
    )
    assert text == "El alcalde de la capital firma el acuerdo"


def test_refs_templates_and_dates_are_removed() -> None:
    text = strip_markup(
        "[[1 de enero]]: Se inaugura {{cita|un puente}} ''nuevo'' "
        "<ref name=\"a\" /> y la gente lo celebra."
    )
    assert "1 de enero" not in text
    assert "{{" not in text and "''" not in text and "<ref" not in text
    # La plantilla desaparece entera: su contenido es metadato de cita.
    assert "Se inaugura nuevo y la gente lo celebra." == text


def test_the_date_prefix_alone_leaves_no_usable_text() -> None:
    assert strip_markup("[[1 de enero]]: ") == ""


def test_display_templates_keep_their_readable_value() -> None:
    assert (
        strip_markup("Primer día del {{siglo|XXI||s|1}} y del milenio III.")
        == "Primer día del XXI y del milenio III."
    )
    assert (
        strip_markup("Deja 1255,4 millones {{esd|10|000}} de dólares.")
        == "Deja 1255,4 millones 10 000 de dólares."
    )


def test_citation_templates_are_dropped_entirely() -> None:
    text = strip_markup(
        "Los estudiantes ocupan la facultad. {{Cita web|url=https://x.es|título=Noticia}}"
    )
    assert "Cita" not in text and "url=" not in text
    assert text == "Los estudiantes ocupan la facultad."


def test_a_leading_link_is_only_stripped_when_it_looks_like_a_date() -> None:
    lead_in = strip_markup("[[Ricardo Belmont]] es elegido alcalde de la capital.")
    assert lead_in.startswith("Ricardo Belmont"), "no se come el nombre propio"
    assert strip_markup("[[1 de enero]] – comienza la obra.") == "comienza la obra."


# --- viñetas ---------------------------------------------------------------


def test_only_bullet_lines_are_events() -> None:
    bullets = parse_bullets(WIKITEXT)
    # 2 de Enero + 1 de Febrero + 1 de Fútbol; la línea de fecha sola no cuenta.
    assert len(bullets) == 4
    assert bullets[0] == "Un suceso importante de enero que todos recuerdan."
    assert bullets[1] == "Otro suceso de enero también muy interesante."
    assert bullets[2] == "Un suceso de febrero que nadie olvida."


def test_bullets_shorter_than_the_minimum_are_discarded() -> None:
    bullets = parse_bullets("* Sí\n* También\n* Un suceso con texto suficiente.")
    assert bullets == ["Un suceso con texto suficiente."]


# --- recorte por secciones -------------------------------------------------


def test_direct_slice_stops_at_the_first_subsection() -> None:
    direct = slice_sections(_toc(), WIKITEXT, include_subsections=False)

    # Los meses se toman aparte (índice 2–3), así que `Acontecimientos`
    # sólo conserva sus viñetas propias, anteriores al primer mes.
    assert "=== Enero ===" not in direct["Acontecimientos"]
    assert "== Deporte ==" not in direct["Acontecimientos"]
    # `Deporte` se queda en su texto propio, sin el bloque `Fútbol`.
    assert "El equipo local cierra una temporada memorable." in direct["Deporte"]
    assert "Un equipo asciende" not in direct["Deporte"]


def test_full_slice_swallows_the_subsections_like_section_i() -> None:
    full = slice_sections(_toc(), WIKITEXT, include_subsections=True)

    # `Acontecimientos` (índice 1) ya contiene sus meses (2–3).
    assert "=== Enero ===" in full["Acontecimientos"]
    assert "=== Febrero ===" in full["Acontecimientos"]
    assert "== Deporte ==" not in full["Acontecimientos"]
    # `Deporte` arrastra sus subsecciones, igual que `prop=wikitext&section=<i>`.
    assert "Un equipo asciende" in full["Deporte"]
    assert full["Fútbol"].startswith("=== Fútbol ===")
