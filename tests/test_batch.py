"""T020 · Lote del período — FR-007.

`GetEvents` devuelve el **lote** del período: un DAILY, un RELATIONSHIPS y el
MAIN del año, en ese orden de presentación (data-model §5). El MAIN es del año,
así que se sirve una sola vez: en cuanto el backend lo consume entra en
`exclude_ids` y no vuelve a salir. Si la época no da para los tres tipos, el
lote se completa con lo que haya: el avance nunca se queda sin sucesos.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from repository import ORIGIN_FETCHED, CachedEvent
from server._generated import events_pb2
from server.servicer import EventsServicer


def _event(
    event_id: str, kind: str, *, min_age: int = 0, max_age: int = 120, country: str = "AR"
):
    return CachedEvent(
        id=event_id,
        section="Acontecimientos",
        type=kind,
        text_es=f"Suceso {event_id} de la época.",
        min_age=min_age,
        max_age=max_age,
        options=[{"id": "opt-1", "label": "Asumirlo", "deltas": {"happiness": 3}}],
        source_url="https://es.wikipedia.org/",
        country=country,
    )


def _servicer(catalog: list[CachedEvent]) -> EventsServicer:
    """Servicer con la fuente sustituida por un catálogo fijo."""
    repository = MagicMock()
    repository.get_or_fetch.return_value = (catalog, ORIGIN_FETCHED)
    return EventsServicer(repository=repository, fetch=lambda year: [], seed_events=[])


def _request(**overrides) -> events_pb2.GetEventsRequest:
    payload = dict(year=1990, age_years=30, limit=3, country="AR")
    payload.update(overrides)
    return events_pb2.GetEventsRequest(**payload)

def _kinds(response) -> list[str]:
    return [events_pb2.EventType.Name(event.type) for event in response.events]


def test_the_batch_is_one_daily_one_relationships_and_the_main() -> None:
    """FR-007: los tres tipos, en el orden del data-model."""
    catalog = [
        _event("year-main", "LABORAL"),
        _event("year-daily", "COTIDIANO"),
        _event("year-rel", "RELACION"),
    ]

    response = _servicer(catalog).GetEvents(_request(), None)

    assert _kinds(response) == ["DAILY", "RELATIONSHIPS", "MAIN"]


def test_the_main_is_served_once_per_year() -> None:
    """FR-007: el MAIN es del año; una vez consumido, no vuelve."""
    catalog = [_event("main", "LABORAL"), _event("daily", "COTIDIANO")]
    servicer = _servicer(catalog)

    first = servicer.GetEvents(_request(), None)
    assert "MAIN" in _kinds(first)

    # El backend ya lo consumió, así que llega en `exclude_ids`.
    second = servicer.GetEvents(_request(exclude_ids=["main"]), None)
    assert "MAIN" not in _kinds(second), _kinds(second)


def test_an_epoch_without_all_three_kinds_still_returns_a_batch() -> None:
    """Una época pobre no puede dejar el avance sin sucesos."""
    response = _servicer([_event("solo", "COTIDIANO")]).GetEvents(_request(), None)

    assert _kinds(response) == ["DAILY"]


def test_the_batch_respects_age_and_limit() -> None:
    """FR-013/FR-007: la edad filtra y `limit` nunca se supera."""
    catalog = [
        _event("adult-main", "LABORAL", min_age=18),
        _event("kid-daily", "COTIDIANO"),
        _event("kid-rel", "RELACION"),
    ]
    servicer = _servicer(catalog)

    # Con 6 años el MAIN (18+) no entra; el lote se queda en dos.
    child = servicer.GetEvents(_request(age_years=6), None)
    assert _kinds(child) == ["DAILY", "RELATIONSHIPS"]

    # Y con `limit = 1` el lote respeta el tope del contrato.
    capped = servicer.GetEvents(_request(limit=1), None)
    assert len(capped.events) == 1


def test_a_child_is_never_offered_a_decision_of_an_adult() -> None:
    """FR-013: ni pareja ni trabajo le piden decidir a un niño.

    Medido en pantalla el 2026-10-13: a un personaje de **1 año** le salió
    «Contar con tu pareja» / «Guardar cierta distancia», y a los 2 años
    «Aceptar el reto laboral» como suceso del año. El suceso puede ocurrir
    (el `MAIN` es del país, no una acción del personaje), pero la decisión no
    puede ser la de un adulto.
    """
    catalog = [
        _event("sentimental", "SENTIMENTAL"),
        _event("laboral", "LABORAL"),
        _event("cotidiano", "COTIDIANO"),
    ]
    servicer = _servicer(catalog)

    child = servicer.GetEvents(_request(age_years=1), None)
    adult = servicer.GetEvents(_request(age_years=40), None)

    child_ids = [event.id for event in child.events]
    assert "sentimental" not in child_ids, "una pareja no le sale a un bebé"
    laboral_child = next(event for event in child.events if event.id == "laboral")
    assert [option.label for option in laboral_child.options] == [
        "Fijarte en lo que ocurre",
        "Seguir con lo tuyo",
    ], "el trabajo le pasa al lado, no le pide un reto laboral"

    # Con edad adulta, el catálogo sale tal cual (y la pareja sí aparece).
    adult_ids = [event.id for event in adult.events]
    assert "sentimental" in adult_ids
    laboral_adult = next(event for event in adult.events if event.id == "laboral")
    assert [option.label for option in laboral_adult.options] == ["Asumirlo"]
