"""Catálogo semilla embebido en la imagen (research **Decisión 6**, T043).

Si Wikipedia falla, supera el timeout o devuelve vacío, la respuesta sale de
aquí: sucesos **genéricos y sin año**, en español, suficientes para que la
partida no se quede nunca sin eventos (los 5 tipos de FR-003).

`seed_events.json` guarda sólo `tipo → frases`: los `deltas` de las opciones
salen de la **misma** tabla `TYPE_EFFECTS` que usa el clasificador (T042), así
que una decisión de la semilla y una decisión de Wikipedia real pesan igual.

La semilla **no se cachea nunca** en DynamoDB: si lo hiciera, un año servido
desde la semilla quedaría marcado como cacheado y la búsqueda real de ese año
quedaría bloqueada para siempre.
"""

from __future__ import annotations

import json
from copy import deepcopy
from functools import lru_cache
from pathlib import Path

from repository import CachedEvent
from sources.classifier import TYPE_EFFECTS, stable_event_id

_SEED_FILE = Path(__file__).with_name("seed_events.json")

#: Sección de la semilla. Lleva el tipo dentro para que dos tipos con la misma
#: frase no acaben compartiendo id.
_SEED_SECTION = "Semilla/{type}"

#: Rango de edad propio de cada tipo de suceso genérico (FR-013): un niño de
#: cinco años no debería recibir un suceso laboral.
TYPE_AGE_RANGE: dict[str, tuple[int, int]] = {
    "COTIDIANO": (0, 120),
    "EDUCATIVO": (6, 30),
    "LABORAL": (18, 120),
    "RELACION": (0, 120),
    "SENTIMENTAL": (14, 120),
}


@lru_cache(maxsize=1)
def _load_texts() -> dict[str, list[str]]:
    with _SEED_FILE.open(encoding="utf-8") as handle:
        return json.load(handle)


def _to_event(event_type: str, text: str) -> CachedEvent:
    min_age, max_age = TYPE_AGE_RANGE.get(event_type, (0, 120))
    # Sólo las opciones corrientes: la mortal pertenece a sucesos reales que
    # la justifiquen (FR-005a), no a un texto genérico de reserva.
    options = deepcopy(
        [option for option in TYPE_EFFECTS[event_type] if not option["fatal"]]
    )
    section = _SEED_SECTION.format(type=event_type)
    return CachedEvent(
        id=stable_event_id(year=0, section=section, text=text),
        section=section,
        type=event_type,
        text_es=text,
        min_age=min_age,
        max_age=max_age,
        options=options,
        source_url="seed",
    )


def load_seed_events() -> list[CachedEvent]:
    """Catálogo completo: 40 sucesos por tipo, en los 5 tipos de FR-003.

    Los tipos van **intercalados**: si la lista fueran 40 COTIDIANO seguidos,
    una partida que hubiera caído en la semilla durante un año entero (12
    sucesos con granularidad mensual) no vería nunca otra cosa.
    """
    texts_by_type = _load_texts()
    events: list[CachedEvent] = []
    for position in range(max(len(texts) for texts in texts_by_type.values())):
        for event_type, texts in texts_by_type.items():
            if position < len(texts):
                events.append(_to_event(event_type, texts[position]))
    return events


__all__ = ["TYPE_AGE_RANGE", "load_seed_events"]
