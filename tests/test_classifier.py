"""T035 · De suceso histórico a evento jugable — transformación determinista.

Cubre research **Decisión 2** y FR-003 / FR-013:

- clasificación por sección y palabras clave → los 5 tipos de FR-003;
- tabla de rangos de edad (colegio/universidad → 6–30; por defecto → 0–120);
- `TYPE_EFFECTS`: 2–3 opciones con `deltas` **no vacíos** (SC-003) y una
  marcada `fatal` cuando el suceso menciona muerte, desastre o guerra;
- determinismo: la misma entrada produce el mismo `id` estable.

Escrito **antes** de `sources/classifier.py` (test-first).
"""

from __future__ import annotations

import pytest

from sources.classifier import TYPE_EFFECTS, classify, stable_event_id

#: Los 5 tipos de FR-003, tal como los declara `proto/events.proto`.
VALID_TYPES = {"COTIDIANO", "EDUCATIVO", "LABORAL", "RELACION", "SENTIMENTAL"}

GENERIC = "Se inaugura una plaza mayor en el centro de la ciudad."


def _classify(text: str, section: str = "Acontecimientos", year: int = 1990):
    return classify(year=year, section=section, text=text)


# --- clasificación por sección y palabras clave ----------------------------


def test_science_and_technology_section_is_educational() -> None:
    event = _classify(
        "Se lanza un satélite de observación terrestre desde la base.",
        section="Ciencia y tecnología",
    )
    assert event.type == "EDUCATIVO"


def test_industry_and_work_keywords_are_laboral() -> None:
    event = _classify(
        "Abre una gran fábrica de automóviles y crea miles de empleos."
    )
    assert event.type == "LABORAL"


def test_marriage_keywords_are_sentimental() -> None:
    event = _classify("Se casa con Elena tras un largo noviazgo.")
    assert event.type == "SENTIMENTAL"


def test_family_and_friends_keywords_are_relation() -> None:
    event = _classify("Nace su tercera hija y la familia se reúne a celebrarlo.")
    assert event.type == "RELACION"


def test_everything_else_is_cotidiano() -> None:
    event = _classify(GENERIC)
    assert event.type == "COTIDIANO"


@pytest.mark.parametrize(
    "text",
    [
        "Se lanza un satélite.",
        "Abre una fábrica con nuevos empleos.",
        "Se casa con Elena.",
        "Nace su hija y la familia lo celebra.",
        GENERIC,
    ],
)
def test_type_is_always_one_of_the_five(text: str) -> None:
    assert _classify(text).type in VALID_TYPES


# --- rango de edad (FR-013) ------------------------------------------------


def test_school_and_university_keywords_narrow_the_age_range() -> None:
    event = _classify("Se abre un nuevo colegio público en la ciudad.")
    assert (event.min_age, event.max_age) == (6, 30)


def test_default_age_range_covers_the_whole_life() -> None:
    event = _classify(GENERIC)
    assert (event.min_age, event.max_age) == (0, 120)


def test_war_and_accident_texts_keep_the_whole_life_range() -> None:
    event = _classify("La guerra arrasa el frente y deja miles de muertos.")
    assert (event.min_age, event.max_age) == (0, 120)


# --- opciones: deltas no vacíos y opción mortal ----------------------------


def test_type_effects_have_between_two_and_three_options() -> None:
    assert set(TYPE_EFFECTS) == VALID_TYPES
    for type_, options in TYPE_EFFECTS.items():
        assert 2 <= len(options) <= 3, type_
        for option in options:
            assert option["id"] and option["label"], type_
            # SC-003: toda decisión cambia algo observable.
            assert option["deltas"], f"{type_}: los deltas no pueden estar vacíos"
            assert all(isinstance(v, int) and v != 0 for v in option["deltas"].values())
            assert isinstance(option["fatal"], bool), type_


def test_every_type_has_a_fatal_option_available() -> None:
    for type_, options in TYPE_EFFECTS.items():
        assert any(option["fatal"] for option in options), type_


def test_death_war_or_disaster_text_presents_a_fatal_option() -> None:
    event = _classify("La guerra arrasa el frente y deja miles de muertos.")
    assert any(option["fatal"] for option in event.options)


def test_ordinary_text_presents_no_fatal_option() -> None:
    event = _classify(GENERIC)
    assert not any(option["fatal"] for option in event.options)
    assert len(event.options) >= 2, "FR-003: siempre hay de elegir"


def test_presented_options_are_never_more_than_three() -> None:
    for text in (GENERIC, "La guerra arrasa el frente y deja miles de muertos."):
        assert 2 <= len(_classify(text).options) <= 3


# --- determinismo e id estable (FR-003 idempotencia) -----------------------


def test_same_input_produces_the_same_event_and_id() -> None:
    first = _classify("Se lanza un satélite.", section="Ciencia y tecnología")
    second = _classify("Se lanza un satélite.", section="Ciencia y tecnología")
    assert first == second
    assert first.id == second.id


def test_id_depends_on_year_section_and_text() -> None:
    base = _classify("Se lanza un satélite.", section="Ciencia y tecnología")
    assert base.id == stable_event_id(
        year=1990, section="Ciencia y tecnología", text="Se lanza un satélite."
    )
    assert (
        _classify("Se lanza un satélite.", section="Ciencia y tecnología", year=1991).id
        != base.id
    )
    assert (
        _classify("Se lanza un satélite.", section="Deporte").id != base.id
    )
    assert _classify("Otro texto distinto.", section="Ciencia y tecnología").id != base.id
