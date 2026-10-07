"""Transformación **determinista** de suceso histórico a evento jugable (T042).

Implementa la research **Decisión 2** sin modelos externos ni claves de API:

1. **Clasificación** — por sección de origen y, después, por palabras clave:
   industria/trabajo → `LABORAL`; cónyuges y fórmulas matrimoniales →
   `SENTIMENTAL`; familia y amigos → `RELACION`; el resto → `COTIDIANO`.
2. **Plantilla de decisión** — `TYPE_EFFECTS` envuelve cada suceso en 2–3
   opciones con `deltas` predefinidos por tipo (FR-003, SC-003: toda decisión
   cambia algo observable) y una **opción mortal** (`fatal`) que sólo se
   presenta cuando el suceso menciona muerte, desastre o guerra (FR-005a).
3. **Rango de edad** — colegio/universidad → `6–30`; por defecto → `0–120`
   (FR-013).
4. **Id estable** — `hash(year + sección + texto)`, para que `exclude_ids`
   y la idempotencia de la caché funcionen (data-model §3).

Todo es una función pura: misma entrada, mismo evento. Por eso se puede
probar con `pytest` y reproducir en CI (Decisión 2, *Rationale*).
"""

from __future__ import annotations

import hashlib
import unicodedata
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

# --- tipos de FR-003 (espejo de `proto/events.proto`) ----------------------

COTIDIANO = "COTIDIANO"
EDUCATIVO = "EDUCATIVO"
LABORAL = "LABORAL"
RELACION = "RELACION"
SENTIMENTAL = "SENTIMENTAL"

EVENT_TYPES = (COTIDIANO, EDUCATIVO, LABORAL, RELACION, SENTIMENTAL)

# --- secciones de Wikipedia que ya clasifican por sí solas ------------------

SECTION_TYPES = {
    "ciencia y tecnologia": EDUCATIVO,
    "educacion": EDUCATIVO,
}

# --- palabras clave (se comparan sin acentos ni mayúsculas) -----------------

#: Orden relevante: primero industria, luego pareja, luego familia.
KEYWORD_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        LABORAL,
        (
            "fabrica",
            "industria",
            "industrial",
            "empresa",
            "empleo",
            "trabajo",
            "obrero",
            "sindicato",
            "mineria",
            "produccion",
            "economia",
            "comercio",
        ),
    ),
    (
        SENTIMENTAL,
        (
            "se casa",
            "matrimonio",
            "noviazgo",
            "divorcio",
            "conyuge",
            "esposa",
            "esposo",
            "boda",
            "compromiso",
            "viuda",
            "viudo",
        ),
    ),
    (
        RELACION,
        (
            "familia",
            "hijo",
            "hija",
            "hermano",
            "hermana",
            "amigo",
            "amiga",
            "padre",
            "madre",
            "nace",
            "nacimiento",
        ),
    ),
)

#: Rangos de edad por palabra clave (FR-013). El primero que coincida gana.
AGE_RULES: tuple[tuple[tuple[str, ...], tuple[int, int]], ...] = (
    (
        (
            "colegio",
            "universidad",
            "escuela",
            "estudiante",
            "estudia",
            "facultad",
            "aula",
            "bachillerato",
        ),
        (6, 30),
    ),
    (
        (
            "guerra",
            "accidente",
            "terremoto",
            "tsunami",
            "inundacion",
            "incendio",
            "explosion",
            "desastre",
        ),
        (0, 120),
    ),
)

DEFAULT_AGE_RANGE = (0, 120)

#: El suceso sólo presenta opción mortal si habla de esto (FR-005a).
FATAL_KEYWORDS = (
    "muerte",
    "muerto",
    "muertos",
    "morir",
    "guerra",
    "desastre",
    "incendio",
    "explosion",
    "accidente",
    "terremoto",
    "catástrofe",
    "catástrofes",
)

# --- plantilla de decisión por tipo (Decisión 2, paso 2) -------------------
#
# Cada tipo lleva 2 opciones corrientes + 1 marcada `fatal`. `classify` sólo
# devuelve la mortal si el suceso la justifica, así que el evento presentado
# tiene siempre 2–3 opciones (contrato `PresentedEvent`, `minItems: 2`).

TYPE_EFFECTS: dict[str, list[dict[str, Any]]] = {
    COTIDIANO: [
        {
            "id": "opt-1",
            "label": "Tomarte el día con calma",
            "deltas": {"happiness": 3},
            "fatal": False,
        },
        {
            "id": "opt-2",
            "label": "Dejarlo pasar sin más",
            "deltas": {"happiness": -2, "health": -1},
            "fatal": False,
        },
        {
            "id": "opt-3",
            "label": "Meterete de lleno en el asunto",
            "deltas": {"happiness": -6, "health": -8},
            "fatal": True,
        },
    ],
    EDUCATIVO: [
        {
            "id": "opt-1",
            "label": "Dedicar esfuerzo a estudiar",
            "deltas": {"intelligence": 5, "happiness": -2},
            "fatal": False,
        },
        {
            "id": "opt-2",
            "label": "Prefieres el recreo",
            "deltas": {"happiness": 4, "intelligence": -3},
            "fatal": False,
        },
        {
            "id": "opt-3",
            "label": "Ignorar los avisos del profesorado",
            "deltas": {"intelligence": -15, "health": -5},
            "fatal": True,
        },
    ],
    LABORAL: [
        {
            "id": "opt-1",
            "label": "Aceptar el reto laboral",
            "deltas": {"intelligence": 3, "happiness": -3},
            "fatal": False,
        },
        {
            "id": "opt-2",
            "label": "Buscar un equilibrio",
            "deltas": {"happiness": 3, "health": 2},
            "fatal": False,
        },
        {
            "id": "opt-3",
            "label": "Trabajar sin descanso alguno",
            "deltas": {"health": -20, "happiness": -8},
            "fatal": True,
        },
    ],
    RELACION: [
        {
            "id": "opt-1",
            "label": "Estar para los tuyos",
            "deltas": {"happiness": 5, "health": 1},
            "fatal": False,
        },
        {
            "id": "opt-2",
            "label": "Dedicarte a lo tuyo",
            "deltas": {"intelligence": 3, "happiness": -3},
            "fatal": False,
        },
        {
            "id": "opt-3",
            "label": "Aislarte de quienes te quieren",
            "deltas": {"happiness": -15, "health": -5},
            "fatal": True,
        },
    ],
    SENTIMENTAL: [
        {
            "id": "opt-1",
            "label": "Contar con tu pareja",
            "deltas": {"happiness": 6, "looks": 2},
            "fatal": False,
        },
        {
            "id": "opt-2",
            "label": "Guardar cierta distancia",
            "deltas": {"happiness": -6},
            "fatal": False,
        },
        {
            "id": "opt-3",
            "label": "Terminar mal la relación",
            "deltas": {"happiness": -20, "health": -5},
            "fatal": True,
        },
    ],
}


@dataclass
class PlayEvent:
    """Evento ya jugable: el microservicio lo volca directo en el proto."""

    id: str
    type: str
    text: str
    min_age: int
    max_age: int
    section: str
    source_url: str = ""
    options: list[dict[str, Any]] = field(default_factory=list)


def _normalize(value: str) -> str:
    """Minúsculas y sin acentos, para que las palabras clave no dependan de la escritura."""
    stripped = unicodedata.normalize("NFD", value.casefold())
    return "".join(char for char in stripped if not unicodedata.combining(char))


def stable_event_id(*, year: int, section: str, text: str) -> str:
    """Hash estable de `year + sección + texto` (data-model §3, FR-003)."""
    payload = f"{year}|{_normalize(section)}|{_normalize(text)}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _type_for(section: str, normalized_text: str) -> str:
    section_type = SECTION_TYPES.get(_normalize(section))
    if section_type:
        return section_type
    for event_type, keywords in KEYWORD_RULES:
        if any(keyword in normalized_text for keyword in keywords):
            return event_type
    return COTIDIANO


def _age_range_for(normalized_text: str) -> tuple[int, int]:
    for keywords, age_range in AGE_RULES:
        if any(keyword in normalized_text for keyword in keywords):
            return age_range
    return DEFAULT_AGE_RANGE


def _options_for(event_type: str, normalized_text: str) -> list[dict[str, Any]]:
    template = TYPE_EFFECTS[event_type]
    is_fatal_situation = any(
        _normalize(keyword) in normalized_text for keyword in FATAL_KEYWORDS
    )
    return [
        deepcopy(option)
        for option in template
        if is_fatal_situation or not option["fatal"]
    ]


def classify(
    *, year: int, section: str, text: str, source_url: str = ""
) -> PlayEvent:
    """Convierte un suceso en un evento jugable. Determinista y puro."""
    normalized_text = _normalize(text)
    event_type = _type_for(section, normalized_text)
    min_age, max_age = _age_range_for(normalized_text)

    return PlayEvent(
        id=stable_event_id(year=year, section=section, text=text),
        type=event_type,
        text=text,
        min_age=min_age,
        max_age=max_age,
        section=section,
        source_url=source_url,
        options=_options_for(event_type, normalized_text),
    )
