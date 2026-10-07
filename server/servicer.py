"""`EventsServicer` — implementación de `events.v1.GetEvents` (T046).

Orden de resolución (research Decisión 5 y 6):

    caché (DynamoDB)  →  Wikipedia  →  catálogo semilla

y después, sea cual sea el origen, los mismos filtros para todos:

- `min_age ≤ age_years ≤ max_age` (FR-013);
- `exclude_ids` ya consumidos por la vida (SC-005);
- `limit` — **nunca** devuelve más de 2 con granularidad mensual ni 5 con
  anual (FR-003a): el servicio respeta el `limit` que pida el backend.

Si algo falla (DynamoDB, red, timeout o fuente vacía) la respuesta sale de la
semilla con `origin = SEED`: la partida **nunca** queda bloqueada (Decisión 6,
edge cases de la spec). El `servicer` jamás aborta la petición.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Callable, Iterable

from repository import (
    ORIGIN_CACHED,
    ORIGIN_FETCHED,
    ORIGIN_SEED,
    CachedEvent,
    EventsRepository,
)
from sources.classifier import stable_event_id

from ._generated import events_pb2, events_pb2_grpc

#: `origin` string → enum `EventOrigin` del contrato.
_ORIGINS = {
    ORIGIN_CACHED: events_pb2.CACHED,
    ORIGIN_FETCHED: events_pb2.FETCHED,
    ORIGIN_SEED: events_pb2.SEED,
}

#: `type` string (los 5 tipos de FR-003) → enum `EventType` del contrato.
_EVENT_TYPES = {
    name: value
    for name, value in events_pb2.EventType.items()
    if name != "EVENT_TYPE_UNSPECIFIED"
}


def to_life_event(event: CachedEvent) -> events_pb2.LifeEvent:
    """`CachedEvent` → mensaje `LifeEvent` del contrato (sin año: el año sólo
    viaja en el eco de `GetEventsResponse`)."""
    return events_pb2.LifeEvent(
        id=event.id,
        type=_EVENT_TYPES.get(event.type, events_pb2.COTIDIANO),
        text=event.text_es,
        min_age=event.min_age,
        max_age=event.max_age,
        source_url=event.source_url,
        options=[
            events_pb2.EventOption(
                id=option.get("id", ""),
                label=option.get("label", ""),
                fatal=bool(option.get("fatal", False)),
                deltas=[
                    events_pb2.StatDelta(stat=stat, value=int(value))
                    for stat, value in (option.get("deltas") or {}).items()
                ],
            )
            for option in event.options
        ],
    )


class EventsServicer(events_pb2_grpc.EventsServiceServicer):
    """Servidor de `events.v1`. Todas las dependencias se inyectan para
    poder probar la red, la caché y la semilla por separado."""

    def __init__(
        self,
        *,
        repository: EventsRepository,
        fetch: Callable[[int], Iterable[CachedEvent]],
        seed_events: Iterable[CachedEvent],
    ) -> None:
        self._repository = repository
        self._fetch = fetch
        self._seed_events = list(seed_events)

    def GetEvents(self, request, context):  # noqa: N802
        year = int(request.year)

        try:
            events, origin = self._repository.get_or_fetch(year, self._fetch)
        except Exception:  # noqa: BLE001 — degradar es obligatorio, nunca abortar
            events, origin = [], ORIGIN_SEED

        if not events:
            events = self._seeded_for_year(year)
            origin = ORIGIN_SEED

        selected = self._select(events, request)

        return events_pb2.GetEventsResponse(
            events=[to_life_event(event) for event in selected],
            origin=_ORIGINS[origin],
            year=year,
        )

    # --- helpers ----------------------------------------------------------

    def _seeded_for_year(self, year: int) -> list[CachedEvent]:
        """La semilla no tiene año: se le asigna uno para que `exclude_ids`
        no vacíe el catálogo cuando el jugador cruza de año."""
        return [
            replace(
                event,
                id=stable_event_id(year=year, section=event.section, text=event.text_es),
                year=year,
            )
            for event in self._seed_events
        ]

    def _select(self, events: list[CachedEvent], request) -> list[CachedEvent]:
        """Edad (FR-013) → no repetir (SC-005) → `limit` (FR-003a)."""
        already_seen = set(request.exclude_ids)
        eligible = [
            event
            for event in events
            if event.min_age <= request.age_years <= event.max_age
            and event.id not in already_seen
        ]
        limit = int(request.limit)
        return eligible[:limit] if limit > 0 else []


def build_servicer() -> EventsServicer:
    """Ensambla el `servicer` con sus dependencias reales (para `__main__`)."""
    from seed_catalog import load_seed_events
    from sources.wikipedia import fetch_year_events

    return EventsServicer(
        repository=EventsRepository(),
        fetch=fetch_year_events,
        seed_events=load_seed_events(),
    )


__all__ = ["EventsServicer", "build_servicer", "to_life_event"]
