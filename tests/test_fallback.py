"""T037 · Fallback sin red — research Decisión 6 y edge cases de la spec.

La partida **nunca** puede quedarse bloqueada por falta de eventos:

- la fuente externa falla, supera el timeout o devuelve vacío → la respuesta
  sale del catálogo semilla con `origin = SEED` y `events` **no vacío**;
- el año ya cacheado se sirve con `origin = CACHED` sin tocar la red (SC-010);
- lo que se sirva —semilla incluida— respeta `limit`, `min_age`/`max_age` y
  `exclude_ids` (FR-013, SC-005, límite 2 mensual / 5 anual).

Escrito **antes** de `server/servicer.py` (test-first).
"""

from __future__ import annotations

import pytest

from repository import CachedEvent, EventsRepository
from server._generated import events_pb2
from server.servicer import EventsServicer


# --- fixtures de datos -----------------------------------------------------


def _option(option_id: str, label: str, fatal: bool = False) -> dict:
    return {
        "id": option_id,
        "label": label,
        "deltas": {"happiness": 3, "health": -1},
        "fatal": fatal,
    }


def _seed(count: int = 6) -> list[CachedEvent]:
    """Catálogo semilla de prueba: genérico y sin año (Decisión 6)."""
    types = ["COTIDIANO", "EDUCATIVO", "LABORAL", "RELACION", "SENTIMENTAL", "COTIDIANO"]
    events = []
    for index in range(count):
        # Los dos últimos sólo interesan a un personaje ya crecido (FR-013).
        school_years = index >= 4
        events.append(
            CachedEvent(
                id=f"seed-{index}",
                section="Semilla",
                type=types[index % len(types)],
                text_es=f"Suceso genérico de reserva número {index}.",
                min_age=6 if school_years else 0,
                max_age=30 if school_years else 120,
                options=[_option("opt-1", "Asumirlo"), _option("opt-2", "Ignorarlo")],
                source_url="seed",
            )
        )
    return events


def _request(
    year: int = 1990,
    age_years: int = 30,
    limit: int = 5,
    exclude_ids: list[str] | None = None,
) -> events_pb2.GetEventsRequest:
    return events_pb2.GetEventsRequest(
        year=year,
        age_years=age_years,
        limit=limit,
        exclude_ids=exclude_ids or [],
    )


def _servicer(fetch, seed=None) -> EventsServicer:
    return EventsServicer(
        repository=EventsRepository(),
        fetch=fetch,
        seed_events=seed if seed is not None else _seed(),
    )


def _timeout(year: int, country: str) -> list[CachedEvent]:
    raise TimeoutError("la fuente externa no contestó")


def _unreachable(year: int, country: str) -> list[CachedEvent]:
    raise ConnectionError("sin salida a internet")


def _nothing(year: int, country: str) -> list[CachedEvent]:
    return []


# --- la semilla siempre responde -------------------------------------------


@pytest.mark.parametrize("fetch", [_timeout, _unreachable, _nothing])
def test_external_source_failure_falls_back_to_the_seed_catalog(fetch) -> None:
    response = _servicer(fetch).GetEvents(_request(), context=None)

    assert response.origin == events_pb2.SEED
    assert len(response.events) > 0, "la partida no puede quedarse bloqueada"
    assert response.year == 1990


def test_every_seed_event_is_a_valid_playable_event() -> None:
    response = _servicer(_nothing).GetEvents(_request(), context=None)

    valid_types = set(events_pb2.EventType.keys())
    for event in response.events:
        assert events_pb2.EventType.Name(event.type) in valid_types
        assert event.text
        assert len(event.options) >= 2, "FR-003: siempre hay de elegir"
        for option in event.options:
            assert option.label
            assert option.scores, "SC-003: toda decisión cambia algo observable"
            for score in option.scores:
                assert -6 <= score.score <= 6, "FR-009: scores, nunca puntos"


# --- el catálogo embebido (T043) -------------------------------------------


def test_the_embedded_seed_catalog_covers_every_type_and_never_runs_dry() -> None:
    # Decisión 6: ~40 sucesos genéricos por categoría de contenido, sin año y en
    # español. El contenido razona en las 5 categorías heredadas de 002 y el
    # `servicer` las proyecta al contrato (`_TYPE_TO_V2`, puente temporal), así
    # que aquí se comprueba lo observable: los tres tipos de `events.v2`.
    from seed_catalog import load_seed_events
    from server.servicer import to_life_event

    events = load_seed_events()
    # La semilla se atribuye al país de la partida (FR-002); aquí da igual cuál.
    projected = [to_life_event(event, country="AR") for event in events]

    def v2_name(event: events_pb2.LifeEvent) -> str:
        return events_pb2.EventType.Name(event.type)

    v2_types = {v2_name(event) for event in projected}
    assert v2_types == {"MAIN", "DAILY", "RELATIONSHIPS"}
    for name in v2_types:
        count = sum(1 for event in projected if v2_name(event) == name)
        assert count >= 40, f"{name}: hacen falta ~40 sucesos de reserva"

    # Todo suceso declara sistema válido o ninguno (FR-020).
    assert {event.system for event in projected} <= {
        "",
        "education",
        "work",
        "relationships",
    }

    # Ninguna entrada se queda sin opciones ni con opciones sin efecto.
    for event in events:
        assert event.text_es
        assert len(event.options) >= 2
        for option in event.options:
            assert option["deltas"], f"{event.type}: SC-003"

    # Van intercalados: los primeros 5 sucesos cubren los tres tipos del
    # contrato, o un año entero en fallback se vería monótono.
    assert {v2_name(event) for event in projected[:5]} == v2_types

    # Nunca se cachea: si lo fuera, el año quedaría bloqueado en la semilla.
    assert all(event.source_url == "seed" for event in events)


# --- límites comunes a cualquier origen ------------------------------------


def test_the_seed_never_exceeds_the_limit() -> None:
    response = _servicer(_nothing).GetEvents(_request(limit=2), context=None)
    assert len(response.events) == 2, "máximo 2 por período mensual (FR-003a)"

    yearly = _servicer(_nothing).GetEvents(_request(limit=5), context=None)
    assert len(yearly.events) == 5, "máximo 5 por período anual"


def test_events_outside_the_age_range_are_filtered_out() -> None:
    # Un niño de 5 años no debe recibir los sucesos de 6–30.
    child = _servicer(_nothing).GetEvents(_request(age_years=5, limit=5), context=None)
    assert len(child.events) == 4, "solo los de 0–120 le corresponden"

    grown = _servicer(_nothing).GetEvents(_request(age_years=20, limit=5), context=None)
    assert len(grown.events) == 5


def test_excluded_ids_are_never_served_again() -> None:
    first = _servicer(_nothing).GetEvents(_request(limit=5), context=None)
    already_seen = [event.id for event in first.events[:2]]

    again = _servicer(_nothing).GetEvents(
        _request(limit=5, exclude_ids=already_seen), context=None
    )
    served = [event.id for event in again.events]

    assert not set(already_seen) & set(served), "SC-005: sin repetir eventos"


# --- caché y red -----------------------------------------------------------


def test_a_cached_year_is_served_without_touching_the_network(test_year: int) -> None:
    calls = {"fetch": 0}

    def fetch(year: int, country: str) -> list[CachedEvent]:
        calls["fetch"] += 1
        return _seed()

    repository = EventsRepository()
    repository.get_or_fetch(test_year, "", fetch)

    servicer = EventsServicer(
        repository=repository, fetch=fetch, seed_events=_seed()
    )
    response = servicer.GetEvents(_request(year=test_year), context=None)

    assert response.origin == events_pb2.CACHED
    assert calls["fetch"] == 1, "SC-010: la segunda consulta no va a internet"
    assert response.year == test_year


def test_a_fresh_year_is_fetched_once_and_persisted(test_year: int) -> None:
    calls = {"fetch": 0}

    def fetch(year: int, country: str) -> list[CachedEvent]:
        calls["fetch"] += 1
        return _seed()

    repository = EventsRepository()
    servicer = EventsServicer(repository=repository, fetch=fetch, seed_events=_seed())

    first = servicer.GetEvents(_request(year=test_year), context=None)
    second = servicer.GetEvents(_request(year=test_year), context=None)

    assert first.origin == events_pb2.FETCHED
    assert second.origin == events_pb2.CACHED
    assert calls["fetch"] == 1
    assert repository.get_year(test_year), "el año queda persistido"
