"""Fuente *country-first*: la crónica del país, y el filtro del artículo global (T015).

FR-002 exige que **todo** suceso sea atribuible al país del personaje, y FR-004
que el `MAIN` sea un suceso **real del país**. La fuente global (el artículo del
año, en `sources/wikipedia.py`) es una crónica **mundial**: por sí sola no puede
cumplirlo — es de donde se colaban los sucesos de todos los países.

La resolución es la de research **Decisión 1**, en dos escalones:

1. `Anexo:<país> en <año>` — la crónica **del país**. Existe de forma parcial
   (medido el 2026-10-13: para los 19 países del catálogo en unos años sí y en
   otros no) y entra **entera**: la página ya viene con ámbito.
2. el artículo global del año, del que sólo entra lo que **menciona** al país
   (`own`). Un suceso del artículo mundial que nombra a otro país se descarta.

La asimetría es deliberada: el filtro de texto es un apaño para la fuente
mundial y en el anexo sólo descartaría sucesos legítimos (un tratado con el país
vecino sigue siendo del país).

El formato del anexo **no** es uniforme: `Anexo:Colombia en 1902` usa prosa
fechada (`12 de mayo: …`) y `Anexo:México en 1903` usa viñetas. Se aceptan las
dos formas, medidas contra la API real.
"""

from __future__ import annotations

import logging
import re
from dataclasses import replace

from repository import CachedEvent
from sources.classifier import classify
from sources.countries import APP_BY_CODE, normalize, scope_for
from sources.wikipedia import (
    MAX_EVENTS_PER_YEAR,
    MIN_TEXT_LENGTH,
    WikipediaError,
    api_get,
    fetch_year_events,
    strip_markup,
    to_cached,
)

logger = logging.getLogger(__name__)

#: Fuente *country-first* (FR-002, FR-004): el anexo de país por año
#: (`Anexo:Colombia en 1902`) es la **crónica del país**, no la mundial.
ANNEX_TEMPLATE = "Anexo:{country} en {year}"

#: Encabezado de la sección de sucesos del anexo.
ANNEX_HEADING = "acontecimientos"

#: Un suceso del anexo llega como viñeta (`* …`) o como línea fechada
#: (`12 de mayo: …`). El formato **no** es uniforme entre países.
_DATED_LINE_RE = re.compile(r"^\d{1,2}\s+de\s+[a-záéíóúü]+\s*[:\-–—]\s*", re.IGNORECASE)
_HEADING_RE = re.compile(r"^(=+)\s*(.+?)\s*\1\s*$")

#: Líneas del anexo que son plantilla, tabla o categoría, no sucesos.
_ANNEX_NOISE_PREFIXES = ("=", "{{", "|", "{|", "[[categoría:", "[[categoria:")


def fetch_annex_wikitext(country_name: str, year: int) -> str | None:
    """Wikitext de `Anexo:<país> en <año>`, o `None` si ese anexo no existe.

    Se usa `action=query` (y no `parse`) porque distingue **«no existe»** de
    «falló»: la cobertura de anexos es parcial y la ausencia no es un error — el
    que llama cae al artículo global.

    `redirects=1` no es opcional: los nombres del catálogo van **sin tildes**
    (`Mexico`) y el anexo se titula con ellas (`Anexo:México en 1903`), así que
    sin seguirlas se leía el texto de la redirección —que no tiene sucesos— y el
    anexo se daba por vacío **en silencio** (medido el 2026-10-13).
    """
    payload = api_get(
        {
            "action": "query",
            "titles": ANNEX_TEMPLATE.format(country=country_name, year=year),
            "prop": "revisions",
            "rvprop": "content",
            "rvslots": "main",
            "redirects": "1",
            "format": "json",
            "formatversion": "2",
        }
    )
    pages = payload.get("query", {}).get("pages") or []
    page = pages[0] if pages else {}
    if page.get("missing"):
        return None
    revisions = page.get("revisions") or []
    if not revisions:
        return None
    content = (revisions[0].get("slots") or {}).get("main", {}).get("content")
    return str(content) if content else None


def annex_section(wikitext: str) -> str:
    """Recorta la sección de sucesos del anexo de país.

    Si el anexo no tiene la sección esperada se devuelve el texto completo: es
    preferible intentarlo entero que perder la crónica del país.
    """
    lines = wikitext.splitlines()
    start: int | None = None
    level = 0
    for index, line in enumerate(lines):
        match = _HEADING_RE.match(line)
        if match and normalize(match.group(2)) == ANNEX_HEADING:
            start, level = index + 1, len(match.group(1))
            break
    if start is None:
        return wikitext

    for index in range(start, len(lines)):
        match = _HEADING_RE.match(lines[index])
        if match and len(match.group(1)) <= level:
            return "\n".join(lines[start:index])
    return "\n".join(lines[start:])


def parse_annex_events(wikitext: str) -> list[str]:
    """Enunciados jugables de un anexo de país, en cualquiera de sus formatos.

    Acepta las dos formas medidas contra la API real: la **viñeta** (`* …`,
    `Anexo:México en 1903`) y la **línea fechada** (`12 de mayo: …`,
    `Anexo:Colombia en 1902`). Se descartan plantillas, tablas y categorías.
    """
    events: list[str] = []
    for raw_line in wikitext.splitlines():
        line = raw_line.strip()
        if not line or line.lower().startswith(_ANNEX_NOISE_PREFIXES):
            continue
        if line.startswith("*"):
            content = line.lstrip("*").strip()
        elif _DATED_LINE_RE.match(line):
            content = line
        else:
            continue
        cleaned = strip_markup(content)
        if len(cleaned) >= MIN_TEXT_LENGTH:
            events.append(cleaned)
    return events


def fetch_country_year_events(
    country_name: str, year: int, code: str
) -> list[CachedEvent]:
    """La crónica del **país** en ese año, desde su anexo (T015, FR-002/FR-004).

    Los sucesos del anexo son del país **por construcción** (la página es suya),
    así que se marcan con su código y **no** pasan por el filtro de texto: un
    tratado con el país vecino sigue siendo un suceso del país, y exigirle que
    nombre a Colombia lo descartaría.
    """
    wikitext = fetch_annex_wikitext(country_name, year)
    if not wikitext:
        return []

    source_url = (
        "https://es.wikipedia.org/wiki/"
        + ANNEX_TEMPLATE.format(country=country_name.replace(" ", "_"), year=year)
    )
    seen: set[str] = set()
    events: list[CachedEvent] = []
    for text in parse_annex_events(annex_section(wikitext)):
        event = to_cached(
            classify(
                year=year,
                section="Acontecimientos",
                text=text,
                source_url=source_url,
                country=code,
            ),
            year,
        )
        if event.id in seen:
            continue
        seen.add(event.id)
        events.append(event)
        if len(events) >= MAX_EVENTS_PER_YEAR:
            break
    return events


def fetch_scoped_year_events(year: int, country: str) -> list[CachedEvent]:
    """Año *country-first*: la crónica del país y el artículo global filtrado.

    Orden de resolución de research **Decisión 1**:

    1. el **anexo del país** (`Anexo:Colombia en 1902`), que entra entero;
    2. el **artículo global** del año, del que sólo entra lo que **menciona al
       país** (`own`).

    Sin `country` no se aplica ámbito (se devuelve el año tal cual): es lo que
    necesita un uso sin catálogo de países y las pruebas de la fuente.
    """
    if not country:
        return fetch_year_events(year)

    events: list[CachedEvent] = []
    annex = APP_BY_CODE.get(country)
    if annex is not None:
        # Que el anexo falle **no** puede tirar el artículo global: son dos
        # fuentes independientes y perder las dos por un 429 de la primera sería
        # degradar de más (Decisión 6).
        try:
            events.extend(fetch_country_year_events(annex.article, year, annex.code))
        except WikipediaError:
            logger.warning("sin anexo de %s para %s", annex.name, year, exc_info=True)

    try:
        global_events = fetch_year_events(year)
    except WikipediaError:
        # El anexo puede haber servido ya; si no sirvió ninguno, el que llama
        # degrada a la semilla (Decisión 6).
        global_events = []
    events.extend(
        replace(event, country=country)
        for event in global_events
        if scope_for(event.text_es, country) == "own"
    )

    if not events:
        raise WikipediaError(
            f"ni el anexo de {country} ni la página {year} "
            "aportaron sucesos de ese país"
        )
    return events


__all__ = [
    "ANNEX_TEMPLATE",
    "annex_section",
    "fetch_annex_wikitext",
    "fetch_country_year_events",
    "fetch_scoped_year_events",
    "parse_annex_events",
]
