"""T036 · Repositorio de `${project}-events-cache` — SC-010 y data-model §3.

Se ejecuta **contra DynamoDB real (Floci)**, no en memoria: la caché de
eventos es el almacenamiento del microservicio y así es como se prueba.

Usa el año `2999` (fixture `test_year`), que ningún personaje puede alcanzar
—nacen entre 1900 y el año actual— y que se limpia antes y después de cada
prueba, así que no ensucia la caché de una partida y es repetible.

Cubre:
- `Query PK = YEAR#<yyyy>` vacío → se persiste **una** vez con `BatchWriteItem`;
- la segunda consulta **no** vuelve a internet (`SC-010`);
- `fetched_at` en RFC 3339 UTC;
- **nunca** se reescribe un año ya cacheado (FR-003a);
- claves `YEAR#<yyyy>` / `EVT#<id>` (data-model §3).
"""

from __future__ import annotations

from datetime import datetime, timedelta

from boto3.dynamodb.conditions import Key

from repository import CachedEvent, EventsRepository


def _event(event_id: str, text: str) -> CachedEvent:
    return CachedEvent(
        id=event_id,
        section="Acontecimientos",
        type="COTIDIANO",
        text_es=text,
        min_age=0,
        max_age=120,
        options=[
            {
                "id": "opt-1",
                "label": "Tomarlo con calma",
                "deltas": {"happiness": 2},
                "fatal": False,
            },
            {
                "id": "opt-2",
                "label": "Dejarlo correr",
                "deltas": {"happiness": -3},
                "fatal": False,
            },
        ],
        source_url="https://es.wikipedia.org/wiki/2999",
    )


EVENTS = [
    _event("ev-001", "Sucede algo inquietante en la ciudad."),
    _event("ev-002", "Otro suceso del mismo año."),
]


# --- lectura ---------------------------------------------------------------


def test_empty_year_returns_no_events(test_year: int) -> None:
    assert EventsRepository().get_year(test_year) == []


def test_stored_events_round_trip_with_all_fields(test_year: int) -> None:
    repo = EventsRepository()
    repo.put_year(test_year, EVENTS)

    stored = repo.get_year(test_year)

    assert [event.id for event in stored] == ["ev-001", "ev-002"]
    first = stored[0]
    assert first.year == test_year
    assert first.section == "Acontecimientos"
    assert first.type == "COTIDIANO"
    assert first.text_es == EVENTS[0].text_es
    assert (first.min_age, first.max_age) == (0, 120)
    assert first.options[0]["deltas"] == {"happiness": 2}
    assert first.source_url == "https://es.wikipedia.org/wiki/2999"


def test_fetched_at_is_rfc3339_utc(test_year: int) -> None:
    """FR-003a / SC-010: la marca de la búsqueda única."""
    EventsRepository().put_year(test_year, EVENTS)

    for event in EventsRepository().get_year(test_year):
        parsed = datetime.fromisoformat(event.fetched_at.replace("Z", "+00:00"))
        assert parsed.tzinfo is not None, event.fetched_at
        assert parsed.utcoffset() == timedelta(0), "debe ser UTC"
        assert event.fetched_at.endswith("Z"), event.fetched_at


def test_keys_follow_the_contract(test_year: int, table) -> None:
    """`PK = YEAR#<yyyy>` y `SK = EVT#<id>` (data-model §3)."""
    EventsRepository().put_year(test_year, EVENTS)

    response = table.query(KeyConditionExpression=Key("PK").eq(f"YEAR#{test_year}"))
    keys = {(item["PK"], item["SK"]) for item in response["Items"]}
    assert keys == {("YEAR#2999", f"EVT#{event.id}") for event in EVENTS}


# --- SC-010: una sola consulta a internet por año --------------------------


def test_second_query_is_served_from_cache_and_never_goes_to_the_internet(
    test_year: int,
) -> None:
    repo = EventsRepository()
    calls = {"fetch": 0}

    def fetch(year: int) -> list[CachedEvent]:
        calls["fetch"] += 1
        return EVENTS

    first, first_origin = repo.get_or_fetch(test_year, fetch)
    second, second_origin = repo.get_or_fetch(test_year, fetch)

    assert calls["fetch"] == 1, "SC-010: solo la primera consulta sale a internet"
    assert first_origin == "FETCHED"
    assert second_origin == "CACHED"
    assert [event.id for event in first] == ["ev-001", "ev-002"]
    assert [event.id for event in second] == ["ev-001", "ev-002"]


def test_a_cached_year_is_never_rewritten(test_year: int) -> None:
    """FR-003a: si el año ya está cacheado, la escritura se ignora."""
    repo = EventsRepository()
    repo.put_year(test_year, EVENTS)

    repo.put_year(test_year, [_event("otro-id", "Un suceso distinto.")])

    assert [event.id for event in repo.get_year(test_year)] == ["ev-001", "ev-002"]


def test_an_empty_result_is_not_cached_forever(test_year: int) -> None:
    """Si no hay sucesos no se cachea el vacío: el año se volverá a buscar."""
    repo = EventsRepository()

    events, origin = repo.get_or_fetch(test_year, lambda year: [])

    assert events == []
    assert origin == "FETCHED"
    assert repo.get_year(test_year) == [], "no se cachea un año vacío"
