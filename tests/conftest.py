"""Fixtures compartidos: la caché se prueba contra DynamoDB real (Floci)."""

from __future__ import annotations

import boto3
import pytest
from boto3.dynamodb.conditions import Key

from config import DYNAMODB_ENDPOINT, DYNAMODB_REGION, EVENTS_TABLE

#: Año de prueba: inalcanzable para un personaje (nacen 1900…año actual),
#: así que las pruebas no ensucian la caché de una partida real.
TEST_YEAR = 2999


def events_table():
    kwargs: dict = {"region_name": DYNAMODB_REGION}
    if DYNAMODB_ENDPOINT:
        kwargs["endpoint_url"] = DYNAMODB_ENDPOINT
        # Floci no valida credenciales, pero boto3 sí que firma la petición.
        kwargs.setdefault("aws_access_key_id", "test")
        kwargs.setdefault("aws_secret_access_key", "test")
    return boto3.session.Session().resource("dynamodb", **kwargs).Table(EVENTS_TABLE)


def clear_year(year: int = TEST_YEAR) -> None:
    """Borra un año de la caché para que la prueba sea repetible."""
    table = events_table()
    response = table.query(KeyConditionExpression=Key("PK").eq(f"YEAR#{year}"))
    items = response.get("Items", [])
    if not items:
        return
    with table.batch_writer() as batch:
        for item in items:
            batch.delete_item(Key={"PK": item["PK"], "SK": item["SK"]})


@pytest.fixture
def test_year() -> int:
    """Año de prueba limpio antes y después de cada prueba."""
    clear_year(TEST_YEAR)
    yield TEST_YEAR
    clear_year(TEST_YEAR)


@pytest.fixture
def table():
    """Tabla `${PROJECT}-events-cache` real (Floci en local)."""
    return events_table()
