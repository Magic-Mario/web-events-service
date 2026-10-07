"""Repositorio de `${project}-events-cache` (T045).

Tabla de **dueño único** el microservicio (principio III): ni el backend ni
nadie más puede tocarla. El backend no tiene acceso a esta tabla y este
servicio no tiene acceso a `${project}-lives`.

Esquema (data-model §3):

    PK = YEAR#<yyyy>     SK = EVT#<id>    id = hash estable de year+sección+texto

Flujo (FR-003a, SC-010): **primero `Query`**; sólo si el año está vacío se
busca en internet y se hace `BatchWriteItem`. Un año ya cacheado **nunca** se
reescribe, así que cada año se consulta a la fuente externa **una sola vez**.

El microservicio vive en el clúster `backend` y usa Floci en local (principio
II); en producción las credenciales llegan por IRSA (research Decisión 15).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Callable, Iterable

import boto3
from boto3.dynamodb.conditions import Key

from config import DYNAMODB_ENDPOINT, DYNAMODB_REGION, EVENTS_TABLE

#: `origin` del contrato gRPC (enum `EventOrigin`).
ORIGIN_CACHED = "CACHED"
ORIGIN_FETCHED = "FETCHED"
ORIGIN_SEED = "SEED"


@dataclass
class CachedEvent:
    """Un suceso ya clasificado, listo para cachearlo o servirlo."""

    id: str
    section: str
    type: str
    text_es: str
    min_age: int = 0
    max_age: int = 120
    year: int = 0
    options: list[dict[str, Any]] = field(default_factory=list)
    source_url: str = ""
    fetched_at: str = ""

    def to_item(self, year: int, fetched_at: str) -> dict[str, Any]:
        return {
            "PK": f"YEAR#{year}",
            "SK": f"EVT#{self.id}",
            "id": self.id,
            "year": year,
            "section": self.section,
            "type": self.type,
            "text_es": self.text_es,
            "min_age": self.min_age,
            "max_age": self.max_age,
            "options": _to_dynamo(self.options),
            "source_url": self.source_url,
            "fetched_at": fetched_at,
        }

    @classmethod
    def from_item(cls, item: dict[str, Any]) -> "CachedEvent":
        data = _from_dynamo(item)
        return cls(
            id=data["id"],
            year=data["year"],
            section=data["section"],
            type=data["type"],
            text_es=data["text_es"],
            min_age=data["min_age"],
            max_age=data["max_age"],
            options=data.get("options") or [],
            source_url=data.get("source_url", ""),
            fetched_at=data.get("fetched_at", ""),
        )


def _to_dynamo(value: Any) -> Any:
    """`float`/`int` → `Decimal`: boto3 rechaza `float` en los items."""
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, dict):
        return {key: _to_dynamo(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_dynamo(item) for item in value]
    return value


def _from_dynamo(value: Any) -> Any:
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, dict):
        return {key: _from_dynamo(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_from_dynamo(item) for item in value]
    return value


def _now() -> str:
    """Marca de búsqueda única en RFC 3339 UTC (FR-003a, SC-010)."""
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )


class EventsRepository:
    """Acceso a `${PROJECT}-events-cache`."""

    def __init__(
        self,
        table_name: str = EVENTS_TABLE,
        endpoint: str | None = DYNAMODB_ENDPOINT,
        region: str = DYNAMODB_REGION,
        table: Any | None = None,
    ) -> None:
        if table is not None:
            self._table = table
        else:
            kwargs: dict[str, Any] = {"region_name": region}
            if endpoint:
                kwargs["endpoint_url"] = endpoint
                # Floci no valida credenciales, pero boto3 sí que firma.
                kwargs.setdefault("aws_access_key_id", "test")
                kwargs.setdefault("aws_secret_access_key", "test")
            self._table = (
                boto3.session.Session().resource("dynamodb", **kwargs).Table(table_name)
            )
        self.table_name = table_name

    # --- lectura ----------------------------------------------------------

    def get_year(self, year: int) -> list[CachedEvent]:
        """`Query PK = YEAR#<yyyy>`, ordenado por `SK` y con paginación."""
        items: list[dict[str, Any]] = []
        kwargs: dict[str, Any] = {
            "KeyConditionExpression": Key("PK").eq(f"YEAR#{year}"),
            "ScanIndexForward": True,
        }
        while True:
            response = self._table.query(**kwargs)
            items.extend(response.get("Items", []))
            last_key = response.get("LastEvaluatedKey")
            if not last_key:
                break
            kwargs["ExclusiveStartKey"] = last_key
        return [CachedEvent.from_item(item) for item in items]

    def get_or_fetch(
        self, year: int, fetch: Callable[[int], Iterable[CachedEvent]]
    ) -> tuple[list[CachedEvent], str]:
        """Primero caché; sólo si está vacía va a la fuente externa (SC-010).

        Devuelve `(eventos, origin)`. Un año vacío **no** se cachea, para que
        la búsqueda se pueda repetir más adelante. Si `fetch` lanza, la
        excepción se propaga: quien decide cómo degradar es el `servicer`
        (Decisión 6: si algo falla, se responde con la semilla).
        """
        cached = self.get_year(year)
        if cached:
            return cached, ORIGIN_CACHED

        fetched = list(fetch(year))
        if not fetched:
            return [], ORIGIN_FETCHED

        self.put_year(year, fetched)
        return fetched, ORIGIN_FETCHED

    # --- escritura --------------------------------------------------------

    def put_year(self, year: int, events: Iterable[CachedEvent]) -> None:
        """`BatchWriteItem` de todo el año, **una sola vez**.

        Si el año ya está cacheado la escritura se ignora (FR-003a: nunca se
        reescribe un año ya cacheado).
        """
        batch_events = list(events)
        if not batch_events or self.get_year(year):
            return

        fetched_at = _now()
        with self._table.batch_writer() as batch:
            for event in batch_events:
                batch.put_item(Item=event.to_item(year, fetched_at))
