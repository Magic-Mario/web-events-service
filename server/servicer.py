"""`EventsServicer` — implementación de `events.v2.GetEvents` (T046).

Orden de resolución (research Decisión 5 y 6, y Decisión 1 para el ámbito):

    caché (DynamoDB, por año **y país**)  →  fuente *country-first*  →  semilla

y después, sea cual sea el origen, los mismos filtros para todos:

- **ámbito** (FR-002): nunca se presenta un suceso de otro país, y uno **sin
  señal de país** no puede ser `MAIN` (Decisión 1) — «el suceso del año» tiene
  que ser real y del país;
- `min_age ≤ age_years ≤ max_age` (FR-013);
- `exclude_ids` ya consumidos por la vida (SC-005);
- `limit` — el backend pide el tamaño del lote (FR-007) y el servicio nunca da
  más de lo pedido.

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

#: El clasificador razona en 5 categorías de **contenido** (heredadas de 002) y
#: el contrato `events.v2` tiene 3 tipos más un **sistema** de vida aparte.
#: Este es el mapeo puente, explícito y en un único sitio.
#:
#: ⚠️ **Temporal**: T029–T031 mueven la composición al `composer` y reducen el
#: clasificador a los tres tipos; entonces este puente desaparece. Existe para
#: que la partida use sucesos históricos reales mientras eso llega.
_TYPE_TO_V2: dict[str, tuple[int, str]] = {
    "COTIDIANO": (events_pb2.DAILY, ""),
    "EDUCATIVO": (events_pb2.DAILY, "education"),
    "LABORAL": (events_pb2.MAIN, "work"),
    "RELACION": (events_pb2.RELATIONSHIPS, "relationships"),
    "SENTIMENTAL": (events_pb2.RELATIONSHIPS, "relationships"),
}

#: Ámbito del material (FR-004/FR-005): el suceso del año es `YEAR`.
_SCOPE_BY_V2_TYPE = {
    events_pb2.MAIN: events_pb2.YEAR,
    events_pb2.DAILY: events_pb2.DECADE,
    events_pb2.RELATIONSHIPS: events_pb2.DECADE,
}

#: Magnitud de la curva FR-009, para recorrerla al revés (ver `_score_for_points`).
_POINTS_BY_MAGNITUDE = (0, 4, 8, 16, 30, 50, 100)

#: Edad mínima por **categoría de contenido** (FR-013). Un suceso de pareja no
#: puede salirle a un bebé: la categoría no aplica, así que se descarta.
#:
#: ⚠️ **Temporal**, igual que el puente: T029–T031 mueven la composición al
#: `composer` y esto desaparece con él.
_CATEGORY_MIN_AGE: dict[str, int] = {
    "SENTIMENTAL": 14,
}

#: Por debajo de esta edad el personaje no puede tomar una decisión de adulto,
#: así que las categorías que la piden cambian de opciones: el suceso **ocurre**
#: (el `MAIN` es un suceso del país, no una acción del personaje) pero no se le
#: piden «reto laboral» ni «jornada de trabajo».
ADULT_AGE_YEARS = 16

_CHILD_OPTIONS: dict[str, list[dict[str, Any]]] = {
    "LABORAL": [
        {"id": "opt-1", "label": "Fijarte en lo que ocurre", "deltas": {"intelligence": 2}},
        {"id": "opt-2", "label": "Seguir con lo tuyo", "deltas": {"happiness": 2}},
    ],
}


def _score_for_points(points: int) -> int:
    """Puntos del modelo viejo → score de `events.v2` (−6…+6).

    La opción del contrato declara **scores** y el backend los convierte en
    puntos con la curva FR-009; el clasificador todavía trae `deltas` ya en
    puntos, así que se recorren al score más cercano de la tabla. Para los
    valores de la propia curva el viaje de ida y vuelta es exacto (4→1→4),
    que es el caso de todos los `TYPE_EFFECTS`.
    """
    magnitude = abs(int(points))
    nearest = min(
        range(len(_POINTS_BY_MAGNITUDE)),
        key=lambda index: abs(_POINTS_BY_MAGNITUDE[index] - magnitude),
    )
    return -nearest if int(points) < 0 else nearest


def to_life_event(
    event: CachedEvent, *, country: str, age_years: int
) -> events_pb2.LifeEvent:
    """`CachedEvent` → mensaje `LifeEvent` del contrato (sin año: el año sólo
    viaja en el eco de `GetEventsResponse`).

    El `country` es el **atribuido** (FR-002): el país al que pertenece el
    suceso para esta partida. El `servicer` ya filtró por **procedencia** —el
    material de otro país no llega hasta aquí—, así que todo lo que se presenta
    se atribuye al país pedido. Un suceso genérico (semilla) es «la vida
    cotidiana de ese país» (FR-005), no un suceso real ajeno.

    La procedencia se queda **dentro** (`CachedEvent.country`), que es lo que
    usa el filtro y lo que impide que un neutral sea `MAIN`.

    Con `age_years` por debajo de `ADULT_AGE_YEARS` las categorías que asumen
    vida adulta cambian de opciones (FR-013): el trabajo **le pasa al lado**, no
    se le pide decidir sobre él.
    """
    v2_type, system = _TYPE_TO_V2.get(event.type, (events_pb2.DAILY, ""))
    options = event.options
    if age_years < ADULT_AGE_YEARS:
        options = _CHILD_OPTIONS.get(event.type, options)
    return events_pb2.LifeEvent(
        id=event.id,
        type=v2_type,
        text=event.text_es,
        min_age=event.min_age,
        max_age=event.max_age,
        source_url=event.source_url,
        country=country,
        scope=_SCOPE_BY_V2_TYPE[v2_type],
        system=system,
        options=[
            events_pb2.EventOption(
                id=option.get("id", ""),
                label=option.get("label", ""),
                fatal=bool(option.get("fatal", False)),
                scores=[
                    events_pb2.StatScore(stat=stat, score=_score_for_points(int(value)))
                    for stat, value in (option.get("deltas") or {}).items()
                ],
            )
            for option in options
        ],
    )


class EventsServicer(events_pb2_grpc.EventsServiceServicer):
    """Servidor de `events.v2`. Todas las dependencias se inyectan para
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
        country = request.country

        try:
            events, origin = self._repository.get_or_fetch(
                year, country, self._fetch
            )
        except Exception:  # noqa: BLE001 — degradar es obligatorio, nunca abortar
            events, origin = [], ORIGIN_SEED

        if not events:
            events = self._seeded_for_year(year)
            origin = ORIGIN_SEED

        selected = self._select(events, request)

        return events_pb2.GetEventsResponse(
            events=[
                to_life_event(event, country=country, age_years=int(request.age_years))
                for event in selected
            ],
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
        """FR-007 + FR-002: el lote del período, con el ámbito ya resuelto.

        El orden de presentación es el del data-model §5 (DAILY, RELATIONSHIPS,
        MAIN) y el MAIN se sirve **una vez**: en cuanto el backend lo consume
        entra en `exclude_ids` y no vuelve a salir.

        Ámbito (FR-002, Decisión 1):

        - un suceso de **otro** país no se presenta nunca;
        - un suceso **neutral** (sin señal de país) sólo puede ser `DAILY` o
          `RELATIONSHIPS`: el `MAIN` es «el suceso real del año del país»
          (FR-004), así que una frase genérica no puede ocupar su lugar.
        """
        requested = request.country
        already_seen = set(request.exclude_ids)

        # Sin país en la petición no hay ámbito que aplicar: es el uso sin
        # catálogo (y el de las pruebas de la fuente). Con país, manda FR-002.
        def in_scope(event: CachedEvent) -> bool:
            return not requested or event.country in ("", requested)

        def owned(event: CachedEvent) -> bool:
            return not requested or event.country == requested

        def kind_of(event: CachedEvent) -> int:
            return _TYPE_TO_V2.get(event.type, (events_pb2.DAILY, ""))[0]

        pool = [
            event
            for event in events
            if in_scope(event)
            and event.min_age <= request.age_years <= event.max_age
            # FR-013: hay categorías que no aplican a un niño (una pareja, por
            # ejemplo), por mucho que el suceso no declare edad mínima.
            and _CATEGORY_MIN_AGE.get(event.type, 0) <= request.age_years
            and event.id not in already_seen
        ]
        limit = int(request.limit)
        if limit <= 0:
            return []

        chosen: list[CachedEvent] = []
        chosen_ids: set[str] = set()

        def pick(kind: int, candidates: list[CachedEvent]) -> None:
            for event in candidates:
                if event.id in chosen_ids or kind_of(event) != kind:
                    continue
                chosen.append(event)
                chosen_ids.add(event.id)
                return

        for kind in (events_pb2.DAILY, events_pb2.RELATIONSHIPS, events_pb2.MAIN):
            if len(chosen) >= limit:
                break
            # El MAIN exige un suceso del país (Decisión 1).
            candidates = [e for e in pool if owned(e)] if kind == events_pb2.MAIN else pool
            pick(kind, candidates)

        # Relleno: una época puede no tener material de los tres tipos. Nunca
        # entra aquí un neutral marcado como MAIN, que sería lo contrario de
        # FR-004 — pero sí como DAILY/RELATIONSHIPS.
        for event in pool:
            if len(chosen) >= limit:
                break
            if event.id in chosen_ids:
                continue
            if kind_of(event) == events_pb2.MAIN and not owned(event):
                continue
            chosen.append(event)
            chosen_ids.add(event.id)

        return chosen


def build_servicer() -> EventsServicer:
    """Ensambla el `servicer` con sus dependencias reales (para `__main__`)."""
    from seed_catalog import load_seed_events
    from sources.country_year import fetch_scoped_year_events

    return EventsServicer(
        repository=EventsRepository(),
        fetch=fetch_scoped_year_events,
        seed_events=load_seed_events(),
    )


__all__ = ["EventsServicer", "build_servicer", "to_life_event"]
