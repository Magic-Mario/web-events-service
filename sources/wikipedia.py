"""Fuente de eventos: Wikipedia en español por MediaWiki Action API (T044).

Research **Decisión 1**: `es.wikipedia.org`, **sin clave de API**, solo HTTPS
(principio V). Cada año de vida tiene su propia página (`/wiki/1990`), que es
exactamente el índice por el que se localiza la época (FR-003, FR-007).

Cómo se obtiene el texto — **2 peticiones por año**:

1. `action=parse&page=<año>&prop=tocdata` → índice de secciones con su
   `index`, su `line`, su `tocLevel` y su `codepointOffset` dentro del
   wikitext.
2. `action=parse&page=<año>&prop=wikitext` → **toda la página de una vez** y
   se recorta localmente en cada sección por su `codepointOffset`.

   ⚠️ **Desviación medida de la planificación**, documentada aquí a propósito:
   el plan decía pedir `prop=wikitext&section=<i>` por separado para
   `Acontecimientos` (índice 1), sus meses (2–13) y las secciones temáticas.
   Eso son **20 peticiones por año**, y contra la API real **Wikipedia
   responde `429 Too Many Requests` a partir de la novena** (comprobado el
   2026-10-06). Con 2 peticiones no se llega al límite, la página entera
   pesa 60 KB y los cortes por offset son exactos: se procesan **las mismas
   secciones** (índice 1, meses 2–13, `Arte y literatura`, `Ciencia y
   tecnología`, `Deporte`, `Cine`, `Música`, `Televisión` y `Videojuegos`)
   sin duplicar nada.

   Además, recortar por offset entrega a cada sección su contenido **directo**
   (sin las subsecciones), de modo que `Acontecimientos` no se traga los meses:
   cada viñeta acaba en su mes y con un id distinto.

3. Las viñetas (`* [[1 de enero]] – texto<ref>…</ref>`) se limpian de
   marcado wiki y se pasan al clasificador determinista (T042).

Toda petición lleva `User-Agent` y un **timeout corto**; los `429`/`5xx` se
reintentan un par de veces y si aún así no hay texto, esta función lanza:
quien degrada es el `servicer`, que responde con la semilla (Decisión 6).
**Nunca** se bloquea la partida.
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request

from config import WIKIPEDIA_BASE_URL, WIKIPEDIA_TIMEOUT, WIKIPEDIA_USER_AGENT
from repository import CachedEvent
from sources.classifier import PlayEvent, classify

MONTHS = (
    "Enero",
    "Febrero",
    "Marzo",
    "Abril",
    "Mayo",
    "Junio",
    "Julio",
    "Agosto",
    "Septiembre",
    "Octubre",
    "Noviembre",
    "Diciembre",
)

#: Secciones de la página de año que aportan sucesos jugables, en el orden en
#: el que aparecen en el artículo.
WANTED_SECTIONS = (
    "Acontecimientos",
    *MONTHS,
    "Arte y literatura",
    "Ciencia y tecnología",
    "Deporte",
    "Cine",
    "Música",
    "Televisión",
    "Videojuegos",
)

#: Tope de sucesos por sección: una sección enorme (`Cine`, con sus premios)
#: no debe tragarse el resto de la página.
MAX_EVENTS_PER_SECTION = 40

#: Tope total por año: acotado y suficiente para toda una vida (con
#: granularidad mensual se consumen 12 sucesos al año).
MAX_EVENTS_PER_YEAR = 600

#: Los sucesos más cortos que esto son fragmentos, no enunciados jugables.
MIN_TEXT_LENGTH = 15

#: Reintentos ante `429`/`5xx`. Wikipedia limita ráfagas de peticiones.
_MAX_ATTEMPTS = 3
_BACKOFF_SECONDS = 1.5

_REF_RE = re.compile(r"<ref[^>]*>.*?</ref>|<ref[^>]*/>", re.DOTALL)
_TEMPLATE_RE = re.compile(r"\{\{[^{}]*\}\}")

#: Plantillas que sólo aportan metadato: se borran enteras. En las páginas de
#: año aparecen como citas al final de la frase y estropearían el enunciado.
_CITATION_PREFIXES = ("cita", "cite", "wayback", "ref", "enlace roto")
_WIKILINK_RE = re.compile(r"\[\[(?:[^|\]]*\|)?([^\]]+)\]\]")
_EXTERNAL_LINK_RE = re.compile(r"\[([^\s\]]+)\s+([^\]]+)\]")
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_BOLD_ITALIC_RE = re.compile(r"'+")
_DATE_PREFIX_RE = re.compile(
    r"^\s*(?:\[\[\s*)?\d{1,2}\s+de\s+[a-záéíóúü]+(?:\s*\]\])?\s*[:\-–—]\s*"
)


class WikipediaError(RuntimeError):
    """La fuente externa no ha podido responder con sucesos utilizables."""


# --- HTTP ------------------------------------------------------------------


def _api(params: dict[str, str]) -> dict:
    url = f"{WIKIPEDIA_BASE_URL}?{urllib.parse.urlencode(params)}"
    last_error: Exception | None = None

    for attempt in range(1, _MAX_ATTEMPTS + 1):
        request = urllib.request.Request(
            url, headers={"User-Agent": WIKIPEDIA_USER_AGENT}
        )
        try:
            with urllib.request.urlopen(request, timeout=WIKIPEDIA_TIMEOUT) as response:
                payload = json.loads(response.read().decode("utf-8"))
            break
        except urllib.error.HTTPError as error:
            last_error = error
            # Sólo tiene sentido repetir si es limitación o fallo del servidor.
            if error.code not in (429, 500, 503, 504) or attempt == _MAX_ATTEMPTS:
                raise
            time.sleep(_BACKOFF_SECONDS * attempt)
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            last_error = error
            if attempt == _MAX_ATTEMPTS:
                raise
            time.sleep(_BACKOFF_SECONDS * attempt)

    if last_error is not None:  # pragma: no cover — defensa ante bucles raros
        raise last_error
    if "error" in payload:
        raise WikipediaError(
            payload["error"].get("info", "la API de Wikipedia devolvió un error")
        )
    return payload


def fetch_toc(year: int) -> list[dict]:
    """`prop=tocdata` → secciones con `index`, `line`, `tocLevel` y offset."""
    payload = _api(
        {
            "action": "parse",
            "page": str(year),
            "prop": "tocdata",
            "format": "json",
            "formatversion": "2",
        }
    )
    sections = payload.get("parse", {}).get("tocdata", {}).get("sections")
    if sections is None:
        raise WikipediaError(f"la página {year} no tiene índice de secciones")
    return sections


def fetch_wikitext(year: int) -> str:
    """`prop=wikitext` de la página completa, en una sola petición."""
    payload = _api(
        {
            "action": "parse",
            "page": str(year),
            "prop": "wikitext",
            "format": "json",
            "formatversion": "2",
        }
    )
    wikitext = payload.get("parse", {}).get("wikitext")
    if wikitext is None:
        raise WikipediaError(f"la página {year} no tiene texto")
    return str(wikitext)


# --- secciones -------------------------------------------------------------


def _wanted_sections(sections: list[dict]) -> list[tuple[int, str]]:
    """Secciones buscadas con su `index` oficial y su nombre, en orden de artículo."""
    by_line: dict[str, int] = {}
    for section in sections:
        index = section.get("index")
        if index is None:
            continue
        by_line.setdefault(str(section.get("line", "")).strip(), int(index))
    return [(by_line[name], name) for name in WANTED_SECTIONS if name in by_line]


def slice_sections(
    sections: list[dict], wikitext: str, *, include_subsections: bool = True
) -> dict[str, str]:
    """Recorta el wikitext completo en un texto por sección.

    Cada entrada del índice lleva su `codepointOffset` dentro del wikitext y
    el índice está en orden de aparición:

    - `include_subsections=False` → el **contenido directo** de la sección,
      que siempre termina en la siguiente entrada del índice.
    - `include_subsections=True` → como `prop=wikitext&section=<i>`: se traga
      las subsecciones y termina en el próximo encabezado del mismo nivel o
      superior.
    """
    entries = sorted(
        (
            int(section["codepointOffset"]),
            str(section.get("line", "")).strip(),
            int(section.get("tocLevel", 1)),
        )
        for section in sections
        if section.get("codepointOffset") is not None
    )

    sliced: dict[str, str] = {}
    for position, (offset, line, level) in enumerate(entries):
        end = len(wikitext)
        for next_offset, _, next_level in entries[position + 1 :]:
            if not include_subsections or next_level <= level:
                end = next_offset
                break
        sliced.setdefault(line, wikitext[offset:end])
    return sliced


# --- limpieza del wikitext -------------------------------------------------


def _replace_template(match: re.Match[str]) -> str:
    """Resuelve `{{…}}` a texto legible sin perder el enunciado.

    - Citas y plantillas de referencia (`{{Cita web|…}}`, `{{Wayback|…}}`):
      **se borran enteras**, son metadato.
    - Plantillas de presentación (`{{siglo|XXI||s|1}}`, `{{sel|Japón}}`,
      `{{esd|10|000}}`): se queda con su primer argumento posicional. Si
      todos los argumentos son cifras, se unen con espacio, que es como se
      escriben los miles.
    - Sólo argumentos con nombre (`k=v`): no hay texto visible, se borra.
    """
    name, *arguments = (part.strip() for part in match.group(0)[2:-2].split("|"))
    lowered = name.lower()
    if any(lowered.startswith(prefix) for prefix in _CITATION_PREFIXES):
        return " "

    positional = [part for part in arguments if "=" not in part and part]
    if not positional:
        return " "
    if all(part.isdigit() for part in positional):
        return f" {' '.join(positional)} "
    return f" {positional[0]} "


def strip_markup(text: str) -> str:
    """Deja sólo el enunciado en español: sin refs, plantillas, ni enlaces."""
    cleaned = _REF_RE.sub(" ", text)
    while True:
        without_templates = _TEMPLATE_RE.sub(_replace_template, cleaned)
        if without_templates == cleaned:
            break
        cleaned = without_templates
    cleaned = _WIKILINK_RE.sub(r"\1", cleaned)
    cleaned = _EXTERNAL_LINK_RE.sub(r"\2", cleaned)
    cleaned = _HTML_TAG_RE.sub(" ", cleaned)
    cleaned = _BOLD_ITALIC_RE.sub("", cleaned)
    cleaned = _DATE_PREFIX_RE.sub("", cleaned)
    return re.sub(r"\s+", " ", cleaned).strip()


def parse_bullets(wikitext: str) -> list[str]:
    """Extrae los enunciados en español de un bloque de viñetas.

    Toda línea que empieza por `*` es un suceso; se limpia de marcado wiki y
    se descarta si queda demasiado corta para ser un enunciado jugable.
    """
    bullets: list[str] = []
    for raw_line in wikitext.splitlines():
        line = raw_line.strip()
        if not line.startswith("*"):
            continue
        content = strip_markup(line.lstrip("*").strip())
        if len(content) >= MIN_TEXT_LENGTH:
            bullets.append(content)
    return bullets


# --- ensamblaje ------------------------------------------------------------


def _to_cached(event: PlayEvent, year: int) -> CachedEvent:
    return CachedEvent(
        id=event.id,
        year=year,
        section=event.section,
        type=event.type,
        text_es=event.text,
        min_age=event.min_age,
        max_age=event.max_age,
        options=event.options,
        source_url=event.source_url,
    )


def fetch_year_events(year: int) -> list[CachedEvent]:
    """Sucesos de un año, clasificados y listos para cachearlos.

    Lanza `WikipediaError` si la fuente no aporta nada: quien degrada es el
    `servicer` (Decisión 6).
    """
    toc = fetch_toc(year)
    wanted = _wanted_sections(toc)
    if not wanted:
        raise WikipediaError(f"la página {year} no tiene secciones reconocidas")

    wikitext = fetch_wikitext(year)
    with_children = slice_sections(toc, wikitext, include_subsections=True)
    direct = slice_sections(toc, wikitext, include_subsections=False)
    months_present = bool({line for _, line in wanted} & set(MONTHS))

    source_url = f"https://es.wikipedia.org/wiki/{year}"
    seen_ids: set[str] = set()
    events: list[CachedEvent] = []

    for _, line in wanted:
        if line == "Acontecimientos" and months_present:
            # Sólo las viñetas directas: los doce meses se procesan aparte con
            # su propio nombre, y así ningún suceso queda duplicado.
            text = direct.get(line)
        else:
            # Igual que `prop=wikitext&section=<i>`: con sus subsecciones
            # (los torneos de `Deporte`, los premios de `Cine`, …).
            text = with_children.get(line)
        if not text:
            continue

        section_events = 0
        for bullet in parse_bullets(text):
            event = _to_cached(
                classify(year=year, section=line, text=bullet, source_url=source_url),
                year,
            )
            if event.id in seen_ids:
                continue
            seen_ids.add(event.id)
            events.append(event)
            section_events += 1
            if section_events >= MAX_EVENTS_PER_SECTION:
                break
        if len(events) >= MAX_EVENTS_PER_YEAR:
            break

    if not events:
        raise WikipediaError(f"la página {year} no aportó ningún suceso jugable")
    return events


__all__ = [
    "MAX_EVENTS_PER_SECTION",
    "MAX_EVENTS_PER_YEAR",
    "MIN_TEXT_LENGTH",
    "MONTHS",
    "WANTED_SECTIONS",
    "WikipediaError",
    "fetch_toc",
    "fetch_wikitext",
    "fetch_year_events",
    "parse_bullets",
    "slice_sections",
    "strip_markup",
]
