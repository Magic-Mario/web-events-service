"""T012 · Ámbito por país — FR-002, FR-004 y SC-001.

Ningún suceso devuelto puede ser de **otro** país, y uno **sin señal de país**
no puede ocupar el `MAIN` (research Decisión 1: «un item sin señal de país no es
elegible para Main»). Aquí se prueba lo que decide el `servicer` y lo que
reconoce el vocabulario; el recorte del anexo tiene lo suyo más abajo.

Los tres casos del vocabulario son los que importan y son fáciles de romper:

- **límite de palabra** — sin él, «Malí» casa dentro de «forMALIdad»;
- **entidades y signos** — `EE.&nbsp;UU.` tiene que casar con la señal `ee uu`;
- **señal suelta sin coma** — `("marroqui")` es un `str`, no una tupla.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from repository import ORIGIN_FETCHED, CachedEvent
from server._generated import events_pb2
from server.servicer import EventsServicer
from sources import countries, country_year, wikipedia
from sources.countries import APP_BY_CODE, mentioned, normalize, scope_for


# --- vocabulario de país ---------------------------------------------------


def test_a_bare_string_signal_is_coerced_to_a_single_signal() -> None:
    """`("marroqui")` **no** es una tupla: es un `str`.

    Desempaquetarlo daba una señal **por letra** (`a`, `i`, `m`, …) que casaba
    con cualquier texto y descartaba sucesos legítimos en silencio. Se encontró
    midiendo contra la API real el 2026-10-13.
    """
    country = countries.Country("Marruecos", ("marroqui"))  # type: ignore[arg-type]

    assert country.signals == ("marroqui",)


def test_no_country_signal_is_a_single_letter() -> None:
    """Ninguna señal puede ser tan corta que case con medio idioma.

    Un país puede no declarar gentilicio (Luxemburgo, por ejemplo): su nombre
    ya es señal — lo comprueba `test_every_country_recognises_its_own_name`.
    """
    for country in countries.ALL_COUNTRIES:
        for signal in country.signals:
            assert len(signal) >= 3, f"{country.name}: señal sospechosa {signal!r}"


def test_every_country_recognises_its_own_name() -> None:
    for country in countries.ALL_COUNTRIES:
        pattern = countries._PATTERNS[countries._key(country)]
        assert pattern.search(normalize(country.name)), country.name


def test_matching_is_by_word_boundary() -> None:
    """Un nombre corto no puede saltar dentro de otra palabra."""
    assert "india" not in mentioned("las Indias Occidentales")
    assert "india" in mentioned("en la India")
    assert mentioned("con toda formalidad") == set()


def test_html_entities_and_punctuation_do_not_hide_a_country() -> None:
    """El limpiado de wikitext deja `&nbsp;`: «EE. UU.» tiene que seguir casando."""
    assert "US" in mentioned("en EE.&nbsp;UU. sale a la venta el primer Ford")


def test_scope_is_own_foreign_or_neutral() -> None:
    assert scope_for("en Colombia se funda la Academia de Historia", "CO") == "own"
    assert scope_for("en Francia el Gobierno fija la jornada de los mineros", "CO") == "foreign"
    assert scope_for("Domingo Sangriento", "CO") == "neutral"
    # El país propio gana: un tratado con el vecino sigue siendo suyo.
    assert scope_for("Colombia firma un tratado con Venezuela", "CO") == "own"


# --- el servicer no sirve sucesos de otro país -----------------------------


def _cached(
    event_id: str,
    type_: str,
    *,
    country: str = "",
    min_age: int = 0,
    max_age: int = 120,
    text: str | None = None,
) -> CachedEvent:
    return CachedEvent(
        id=event_id,
        section="Acontecimientos",
        type=type_,
        text_es=text or f"Suceso {event_id} de la época.",
        min_age=min_age,
        max_age=max_age,
        options=[{"id": "opt-1", "label": "Asumirlo", "deltas": {"happiness": 1}}],
        country=country,
    )


def _servicer(catalog: list[CachedEvent]) -> EventsServicer:
    repository = MagicMock()
    repository.get_or_fetch.return_value = (catalog, ORIGIN_FETCHED)
    return EventsServicer(
        repository=repository, fetch=lambda year, country: [], seed_events=[]
    )


def _request(country: str = "AR", **overrides) -> events_pb2.GetEventsRequest:
    payload = dict(year=1990, age_years=30, limit=3, country=country, exclude_ids=[])
    payload.update(overrides)
    return events_pb2.GetEventsRequest(**payload)


def test_no_returned_event_belongs_to_another_country() -> None:
    """SC-001: ni uno solo de otro país, aunque el catálogo los mezcle."""
    catalog = [
        _cached("ar-main", "LABORAL", country="AR"),
        _cached("ar-rel", "RELACION", country="AR"),
        _cached("co-daily", "COTIDIANO", country="CO"),
        _cached("es-main", "LABORAL", country="ES"),
        _cached("neutral", "COTIDIANO", country=""),
    ]

    response = _servicer(catalog).GetEvents(_request("AR"), None)

    ids = [event.id for event in response.events]
    assert response.events, "el lote no puede quedar vacío"
    assert {event.country for event in response.events} == {"AR"}, (
        "FR-002: todo lo presentado se atribuye al país de la partida"
    )
    assert "co-daily" not in ids, "un suceso de Colombia no se sirve a Argentina"
    assert "es-main" not in ids, "un suceso de España no se sirve a Argentina"


def test_a_neutral_event_can_never_be_the_main() -> None:
    """FR-004 + Decisión 1: «el suceso del año» tiene que ser real y del país.

    El suceso neutral se presenta (es material de vida cotidiana, atribuible),
    pero **no** puede ocupar el `MAIN` aunque el clasificador lo marque como
    laboral.
    """
    catalog = [
        _cached("neutral-job", "LABORAL", country=""),
        _cached("neutral-daily", "COTIDIANO", country=""),
    ]

    response = _servicer(catalog).GetEvents(_request("AR"), None)

    assert response.events, "el lote no puede quedar vacío"
    assert all(event.type != events_pb2.MAIN for event in response.events)
    assert {event.country for event in response.events} == {"AR"}


def test_a_country_event_is_the_main() -> None:
    catalog = [_cached("ar-job", "LABORAL", country="AR")]

    response = _servicer(catalog).GetEvents(_request("AR"), None)

    assert [event.type for event in response.events] == [events_pb2.MAIN]
    assert response.events[0].country == "AR"


# --- recorte del anexo de país (T015) --------------------------------------

#: Los dos formatos medidos contra la API real: `Anexo:Colombia en 1902` usa
#: prosa fechada y `Anexo:México en 1903` usa viñetas. El de Colombia imita
#: también su sección de jefatura, que **no** es un suceso.
ANNEX_PROSE = """
{{Anexo de país por año|Colombia|1902}}

== Jefatura del Estado y del Gobierno ==

* [[Presidente de Colombia|Presidente de la República]]: [[José Manuel Marroquín]].

== Acontecimientos ==
Colombia finalizó el {{siglo|XIX||s}} e inició el {{siglo|XX||s}} con la [[Guerra de los Mil Días]].

=== Febrero ===
12 de febrero: Promulgadas medidas del presidente Marroquín contra la corrupción administrativa.<ref name=":0">{{Cita web|url=http://ejemplo|título=Colombia y el mundo}}</ref>

=== Mayo ===
11 de mayo: Fundada oficialmente la [[Academia Colombiana de Historia]].
"""

ANNEX_BULLETS = """
== Acontecimientos ==
* 16 de abril: Son aprehendidos por la policía los periodistas independientes del periódico ''El hijo del Ahuizote''.
* 1 de julio: Es inaugurada en la calle Bucareli la Escuela Nacional Preparatoria.
"""


def test_an_annex_is_read_in_both_measured_formats(monkeypatch) -> None:
    """Colombia (prosa fechada) y México (viñetas) producen los mismos sucesos."""
    monkeypatch.setattr(country_year, "fetch_annex_wikitext", lambda name, year: ANNEX_PROSE)
    prose = country_year.fetch_country_year_events("Colombia", 1902, "CO")

    monkeypatch.setattr(country_year, "fetch_annex_wikitext", lambda name, year: ANNEX_BULLETS)
    bullets = country_year.fetch_country_year_events("México", 1903, "MX")

    prose_texts = [event.text_es for event in prose]
    bullet_texts = [event.text_es for event in bullets]
    assert any("Marroquín" in text for text in prose_texts)
    assert any("Academia Colombiana de Historia" in text for text in prose_texts)
    assert any("periodistas" in text for text in bullet_texts)

    # La jefatura del Estado no es un suceso, y el país queda declarado.
    assert not any("Presidente de la República" in text for text in prose_texts)
    assert all(event.country == "CO" for event in prose)
    assert all(event.country == "MX" for event in bullets)


def test_a_missing_annex_is_not_an_error(monkeypatch) -> None:
    """La cobertura de anexos es parcial: sin página, se devuelve vacío."""
    monkeypatch.setattr(country_year, "fetch_annex_wikitext", lambda name, year: None)

    assert country_year.fetch_country_year_events("Colombia", 1902, "CO") == []


def test_the_annex_lookup_uses_the_accented_article_name(monkeypatch) -> None:
    """MediaWiki **no** normaliza tildes en títulos: `Anexo:Mexico` no existe.

    Medido el 2026-10-13: sin el nombre con tilde, el anexo de México se perdía
    en silencio (`missing`) y el país se quedaba con un único suceso global
    mientras `Anexo:México en 1903` tenía cuatro viñetas esperando.
    """
    captured: dict = {}

    def fake_api(params):
        captured.update(params)
        return {"query": {"pages": [{"title": "Anexo:México en 1903"}]}}

    monkeypatch.setattr(country_year, "api_get", fake_api)

    country_year.fetch_annex_wikitext(APP_BY_CODE["MX"].article, 1903)

    assert captured["titles"] == "Anexo:México en 1903"


def test_the_article_name_keeps_the_accents() -> None:
    """El nombre del artículo y el del vocabulario no son lo mismo."""
    acentuados = {
        "MX": "México",
        "PE": "Perú",
        "ES": "España",
        "DO": "República Dominicana",
    }
    for code, expected in acentuados.items():
        assert APP_BY_CODE[code].article == expected, code
        # …y el nombre sin tildes sigue valiendo para reconocer el texto.
        assert code in mentioned(f"algo ocurre en {expected}")


def test_a_failing_annex_does_not_discard_the_global_article(monkeypatch) -> None:
    """Dos fuentes independientes: un 429 en el anexo no tira el artículo global."""
    def failing_annex(name: str, year: int, code: str):
        raise wikipedia.WikipediaError("429")

    global_events = [_cached("g-own", "COTIDIANO", text="en Colombia se funda la Academia.")]
    monkeypatch.setattr(country_year, "fetch_country_year_events", failing_annex)
    monkeypatch.setattr(country_year, "fetch_year_events", lambda year: global_events)

    events = country_year.fetch_scoped_year_events(1902, "CO")

    assert [event.id for event in events] == ["g-own"]


def test_the_scoped_year_takes_the_annex_whole_and_the_global_one_filtered(
    monkeypatch,
) -> None:
    """La asimetría es deliberada: el filtro de texto es para la fuente mundial."""
    annex = [_cached("a-1", "COTIDIANO", country="CO", text="Medidas contra la corrupción administrativa.")]
    global_events = [
        _cached("g-own", "COTIDIANO", text="en Colombia se inaugura el ferrocarril."),
        _cached("g-foreign", "COTIDIANO", text="en Francia el Gobierno fija la jornada de los mineros."),
        _cached("g-neutral", "COTIDIANO", text="Domingo Sangriento"),
    ]
    monkeypatch.setattr(
        country_year, "fetch_country_year_events", lambda name, year, code: annex
    )
    monkeypatch.setattr(country_year, "fetch_year_events", lambda year: global_events)

    events = country_year.fetch_scoped_year_events(1902, "CO")

    assert [event.id for event in events] == ["a-1", "g-own"], (
        "el anexo entra entero; del artículo global sólo lo que menciona al país"
    )
    assert all(event.country == "CO" for event in events)
    assert normalize(events[1].text_es).startswith("en colombia")


def test_without_a_country_the_year_is_not_scoped(monkeypatch) -> None:
    """Uso sin catálogo (y pruebas de la fuente): el año tal cual."""
    global_events = [_cached("g-1", "COTIDIANO", text="en Francia el Gobierno fija la jornada.")]
    monkeypatch.setattr(country_year, "fetch_year_events", lambda year: global_events)

    assert country_year.fetch_scoped_year_events(1902, "") == global_events
