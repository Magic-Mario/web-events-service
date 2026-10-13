"""Vocabulario de país y ámbito geográfico de un suceso (FR-002, T015).

Research **Decisión 1** exige que **todo** suceso sea atribuible al país del
personaje: no se pueden presentar sucesos de otro país y un suceso **sin señal
de país** no es elegible para `MAIN`. Aquí vive el único dato que hace falta
para decidirlo sin autorar catálogos por país: el **vocabulario** con el que se
reconoce un país en el enunciado.

Dos bloques, y la diferencia importa:

- `APP_COUNTRIES` — los 19 países que el catálogo de nacimiento ofrece (002
  FR-001). Llevan su **nombre de artículo** (`Anexo:Colombia en 1902`) y sus
  **ciudades**, porque con ellos se hace la búsqueda *country-first* (T015).
- `OTHER_COUNTRIES` — el resto del mundo, **sólo para reconocer** una mención
  ajena y descartar el suceso. No hace falta que la lista sea exhaustiva para
  que el ámbito funcione: lo que no se reconoce se queda en `neutral`, y
  `neutral` **nunca** puede ser `MAIN`.

El emparejamiento va **por límite de palabra**: sin eso, «Malí» aparecería
dentro de «forMALIdad» y descartaría sucesos legítimos. Por eso los nombres
cortos son seguros, y por eso cada país declara también sus **gentilicios**
(«italiano» no cae dentro de «Italia» una vez que hay límites de palabra).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

#: Todo lo que no sea letra, dígito o espacio se aplana a un espacio simple.
_NON_WORD_RE = re.compile(r"[^a-z0-9]+")

#: Entidades HTML (`&nbsp;`) que el limpiado de wikitext deja en el enunciado.
#: Se quitan **antes** que los signos: si no, `&nbsp;` quedaría como la palabra
#: «nbsp» en medio y «EE.&nbsp;UU.» ya no casaría con la señal `ee uu`.
_ENTITY_RE = re.compile(r"&[a-z]+;", re.IGNORECASE)


@dataclass(frozen=True)
class Country:
    """Un país reconocible en el enunciado de un suceso."""

    name: str
    """Nombre en español, como titula Wikipedia (`Anexo:<nombre> en <año>`)."""

    signals: tuple[str, ...] = ()
    """Gentilicios y adjetivos, **sin acentos** (§ `normalize`)."""

    code: str = ""
    """ISO 3166-1 alfa-2 para los países del juego; vacío para el resto."""

    cities: tuple[str, ...] = ()
    """Ciudades que el catálogo ofrece para ese país (refinan la selección)."""

    article: str = ""
    """Nombre del **artículo** en Wikipedia, con tildes (`México`, `Perú`).

    No es lo mismo que `name`: MediaWiki **no** normaliza tildes en títulos, así
    que `Anexo:Mexico en 1903` no existe y la consulta devuelve «missing» —
    medido el 2026-10-13, con el anexo de México perdiéndose en silencio y el
    país quedándose con un único suceso global. Si se deja vacío se usa `name`.
    """

    def __post_init__(self) -> None:
        """Coacciona una señal suelta escrita sin coma a una tupla de una.

        `("marroqui")` **no** es una tupla: es la cadena `"marroqui"`, y
        desempaquetarla (`*signals`) produce una señal **por letra**
        (`a`, `i`, `m`, …) que hace coincidir cualquier texto y descartaría
        sucesos legítimos en silencio. Se coacciona aquí para que el error no
        pueda existir; hay prueba de ello en `tests/test_scoping.py`.
        """
        for field_name in ("signals", "cities"):
            value = getattr(self, field_name)
            if isinstance(value, str):
                object.__setattr__(self, field_name, (value,))
        if not self.article:
            object.__setattr__(self, "article", self.name)


#: Los 19 países que ofrece el catálogo de nacimiento (002 FR-001), con el
#: nombre del artículo de año, sus gentilicios y sus ciudades.
APP_COUNTRIES: tuple[Country, ...] = (
    Country("Argentina", ("argentino", "argentina"), "AR",
            ("buenos aires", "cordoba", "rosario", "mendoza", "la plata")),
    Country("Bolivia", ("boliviano", "boliviana"), "BO",
            ("la paz", "santa cruz de la sierra", "cochabamba", "sucre", "oruro")),
    Country("Brasil", ("brasileno", "brasilena", "brasilero", "brasilera"), "BR",
            ("sao paulo", "rio de janeiro", "brasilia", "salvador", "fortaleza")),
    Country("Chile", ("chileno", "chilena"), "CL",
            ("santiago", "valparaiso", "concepcion", "antofagasta", "la serena")),
    Country("Colombia", ("colombiano", "colombiana", "neogranadino"), "CO",
            ("bogota", "medellin", "cali", "barranquilla", "cartagena")),
    Country("Costa Rica", ("costarricense", "tico", "tica"), "CR",
            ("san jose", "alajuela", "cartago", "heredia", "liberia")),
    Country("Republica Dominicana", ("dominicano", "dominicana", "quisqueyano"),
            "DO", ("santo domingo", "santiago de los caballeros", "la romana",
                   "san pedro de macoris", "puerto plata"),
            article="República Dominicana"),
    Country("Ecuador", ("ecuatoriano", "ecuatoriana"), "EC",
            ("quito", "guayaquil", "cuenca", "machala", "portoviejo")),
    Country("Espana", ("espanol", "espanola", "hispanico", "hispanica"), "ES",
            ("madrid", "barcelona", "valencia", "sevilla", "zaragoza"),
            article="España"),
    Country("Francia", ("frances", "francesa"), "FR",
            ("paris", "marsella", "lyon", "toulouse", "niza")),
    Country("Guatemala", ("guatemalteco", "guatemalteca"), "GT",
            ("ciudad de guatemala", "quetzaltenango", "escuintla", "coban",
             "antigua guatemala")),
    Country("Italia", ("italiano", "italiana"), "IT",
            ("roma", "milan", "napoles", "turin", "florencia")),
    Country("Mexico", ("mexicano", "mexicana"), "MX",
            ("ciudad de mexico", "guadalajara", "monterrey", "puebla", "tijuana"),
            article="México"),
    Country("Peru", ("peruano", "peruana"), "PE",
            ("lima", "arequipa", "trujillo", "cusco", "chiclayo"),
            article="Perú"),
    Country("Portugal", ("portugues", "portuguesa", "luso", "lusa"), "PT",
            ("lisboa", "oporto", "braga", "coimbra", "faro")),
    Country("Paraguay", ("paraguayo", "paraguaya"), "PY",
            ("asuncion", "ciudad del este", "encarnacion", "luque", "san lorenzo")),
    Country("Uruguay", ("uruguayo", "uruguaya", "oriental"), "UY",
            ("montevideo", "salto", "paysandu", "las piedras", "rivera")),
    Country("Estados Unidos", ("estadounidense", "norteamericano", "norteamericana",
                               "yanqui", "norteamerica", "ee uu", "estados unidos"), "US",
            ("nueva york", "los angeles", "chicago", "houston", "miami")),
    Country("Venezuela", ("venezolano", "venezolana"), "VE",
            ("caracas", "maracaibo", "valencia", "barquisimeto", "maracay")),
)

#: Resto del mundo. **Sólo para reconocer** una mención ajena y descartar el
#: suceso (FR-002). No lleva ciudades: con el nombre y el gentilicio basta.
OTHER_COUNTRIES: tuple[Country, ...] = (
    # Europa
    Country("Reino Unido", ("britanico", "britanica", "ingles", "inglesa",
                            "gran bretana", "inglaterra", "escocia", "gales",
                            "irlanda", "irlandes", "escoces")),
    Country("Alemania", ("aleman", "alemana", "germano", "germana", "prusia",
                         "prusiano")),
    Country("Austria", ("austriaco", "austriaca")),
    Country("Belgica", ("belga",)),
    Country("Paises Bajos", ("holanda", "neerlandes", "neerlandesa", "holandes",
                             "holandesa")),
    Country("Suiza", ("suizo", "suiza")),
    Country("Suecia", ("sueco", "sueca")),
    Country("Noruega", ("noruego", "noruega")),
    Country("Dinamarca", ("danes", "danesa")),
    Country("Finlandia", ("finlandes", "finlandesa")),
    Country("Polonia", ("polaco", "polaca")),
    Country("Rusia", ("ruso", "rusa", "sovietico", "sovietica", "union sovietica",
                      "urss", "bolchevique")),
    Country("Ucrania", ("ucraniano", "ucraniana")),
    Country("Checoslovaquia", ("checo", "checa")),
    Country("Hungria", ("hungaro", "hungara")),
    Country("Rumania", ("rumano", "rumana")),
    Country("Grecia", ("griego", "griega", "heleno", "helenico")),
    Country("Turquia", ("turco", "turca", "otomano", "otomano", "imperio otomano")),
    Country("Bulgaria", ("bulgaro", "bulgara")),
    Country("Serbia", ("serbio", "serbia", "yugoslavia", "yugoslavo")),
    Country("Croacia", ("croata",)),
    Country("Bosnia", ("bosnio", "bosnia")),
    Country("Albania", ("albanes", "albanesa")),
    Country("Islandia", ("islandes", "islandesa")),
    Country("Luxemburgo", ()),
    Country("Malta", ("maltes",)),
    Country("Chipre", ("chipriota",)),
    Country("Estonia", ("estonio", "estonia")),
    Country("Letonia", ("leton", "letona")),
    Country("Lituania", ("lituano", "lituana")),
    Country("Bielorrusia", ("bielorruso", "bielorrusa")),
    Country("Moldavia", ("moldavo", "moldava")),
    Country("Eslovenia", ("esloveno", "eslovena")),
    Country("Eslovaquia", ("eslovaco", "eslovaca")),
    Country("Georgia", ("georgiano", "georgiana")),
    Country("Armenia", ("armenio", "armenia")),
    Country("Azerbaiyan", ("azeri",)),
    # Asia
    Country("China", ("chino", "china", "pekin", "beijing", "mandarin")),
    Country("Japon", ("japones", "japonesa", "tokio", "nipon", "nipona")),
    Country("Corea", ("coreano", "coreana", "seul")),
    Country("India", ("indio", "india", "delhi", "nueva delhi", "bombay", "hindú")),
    Country("Pakistan", ("pakistani",)),
    Country("Indonesia", ("indonesio", "indonesia")),
    Country("Filipinas", ("filipino", "filipina")),
    Country("Vietnam", ("vietnamita",)),
    Country("Tailandia", ("tailandes", "tailandesa", "siam")),
    Country("Birmania", ("birmano", "birmana")),
    Country("Camboya", ("camboyano", "camboyana")),
    Country("Malasia", ("malasio", "malasia")),
    Country("Singapur", ("singapurense",)),
    Country("Taiwan", ("taiwanes", "taiwanesa")),
    Country("Mongolia", ("mongol", "mongola")),
    Country("Nepal", ("nepali",)),
    Country("Sri Lanka", ("cingales",)),
    Country("Banglades", ("bangladesi",)),
    Country("Kazajistan", ("kazajo", "kazaja")),
    Country("Uzbekistan", ("uzbeko", "uzbeka")),
    Country("Iran", ("irani", "persa", "persia")),
    Country("Irak", ("iraqui",)),
    Country("Israel", ("israeli",)),
    Country("Siria", ("sirio", "siria")),
    Country("Libano", ("libanes", "libanesa")),
    Country("Jordania", ("jordano", "jordana")),
    Country("Arabia Saudi", ("saudi",)),
    Country("Yemen", ("yemeni",)),
    Country("Afganistan", ("afgano", "afgana")),
    # Africa
    Country("Egipto", ("egipcio", "egipcia")),
    Country("Marruecos", ("marroqui")),
    Country("Argelia", ("argelino", "argelina")),
    Country("Tunez", ("tunecino", "tunecina")),
    Country("Libia", ("libio", "libia")),
    Country("Sudan", ("sudanes",)),
    Country("Etiopia", ("etiope", "abisinio")),
    Country("Kenia", ("keniata",)),
    Country("Nigeria", ("nigeriano", "nigeriana")),
    Country("Ghana", ("ghanes",)),
    Country("Senegal", ("senegales",)),
    Country("Sudafrica", ("sudafricano", "sudafricana", "afrikaner", "boer")),
    Country("Angola", ("angoleno", "angolena")),
    Country("Mozambique", ("mozambiqueno",)),
    Country("Zimbabue", ("zimbabuense",)),
    Country("Tanzania", ("tanzano",)),
    Country("Uganda", ("ugandes",)),
    Country("Somalia", ("somali",)),
    Country("Congo", ("congoleno", "congolena")),
    Country("Camerun", ("camerunes",)),
    Country("Madagascar", ("malgache",)),
    # America
    Country("Canada", ("canadiense",)),
    Country("Cuba", ("cubano", "cubana", "habana")),
    Country("Haiti", ("haitiano", "haitiana")),
    Country("Honduras", ("hondureno", "hondurena")),
    Country("Nicaragua", ("nicaraguense",)),
    Country("Panama", ("panameno", "panamena")),
    Country("El Salvador", ("salvadoreno", "salvadorena")),
    Country("Puerto Rico", ("puertorriqueno", "boricua")),
    Country("Belice", ("beliceno",)),
    Country("Jamaica", ("jamaicano", "jamaiquino")),
    Country("Bahamas", ("bahameno",)),
    Country("Trinidad y Tobago", ("trinitense",)),
    Country("Guyana", ("guyanes",)),
    Country("Surinam", ("surinames",)),
    Country("Groenlandia", ("groenlandes",)),
    # Oceania
    Country("Australia", ("australiano", "australiana")),
    Country("Nueva Zelanda", ("neozelandes", "neozelandesa")),
    Country("Fiyi", ("fiyiano",)),
    Country("Papua Nueva Guinea", ("papu",)),
)

#: Todo el vocabulario junto.
ALL_COUNTRIES: tuple[Country, ...] = APP_COUNTRIES + OTHER_COUNTRIES

#: `código ISO → país` para los países jugables.
APP_BY_CODE: dict[str, Country] = {c.code: c for c in APP_COUNTRIES}


def normalize(value: str) -> str:
    """Minúsculas, sin acentos y **sólo** letras, dígitos y espacios sueltos.

    El limpiado de wikitext deja entidades HTML (`EE.&nbsp;UU.`) y signos, que
    romperían el emparejamiento por límite de palabra: «EE. UU.» no casaría con
    la señal `ee uu`. Aquí se aplanan a un espacio simple.
    """
    stripped = unicodedata.normalize("NFD", value.casefold())
    plain = "".join(char for char in stripped if not unicodedata.combining(char))
    return _NON_WORD_RE.sub(" ", _ENTITY_RE.sub(" ", plain)).strip()


def _key(country: Country) -> str:
    """Clave interna: el ISO si es jugable, si no el nombre normalizado."""
    return country.code or normalize(country.name)


def _build_patterns() -> dict[str, re.Pattern[str]]:
    """Un patrón por país, con **límite de palabra** en ambos extremos.

    El límite es lo que permite tener nombres cortos («Malí», «Chad») sin que
    salten dentro de palabras («forMALIdad») — y lo que obliga a declarar los
    gentilicios, porque «italiano» ya no cae dentro de «Italia».
    """
    patterns: dict[str, re.Pattern[str]] = {}
    for country in ALL_COUNTRIES:
        words = {normalize(country.name), *country.signals, *country.cities}
        words = {word for word in words if word}
        alternation = "|".join(re.escape(word) for word in sorted(words))
        patterns[_key(country)] = re.compile(rf"\b(?:{alternation})\b")
    return patterns


_PATTERNS = _build_patterns()


def mentioned(text: str) -> set[str]:
    """Claves de los países **reconocidos** en el enunciado (puede haber varios)."""
    normalized = normalize(text)
    return {key for key, pattern in _PATTERNS.items() if pattern.search(normalized)}


def scope_for(text: str, code: str) -> str:
    """Ámbito de un suceso para el país del personaje (Decisión 1 de research).

    - `own` — menciona el país del personaje: elegible para **cualquier** tipo,
      `MAIN` incluido.
    - `foreign` — menciona otro país: se **descarta** (FR-002).
    - `neutral` — no menciona ninguno: elegible para `DAILY`/`RELATIONSHIPS`,
      **nunca** para `MAIN` (Decisión 1).
    """
    keys = mentioned(text)
    if code in keys:
        return "own"
    if keys:
        return "foreign"
    return "neutral"


__all__ = [
    "ALL_COUNTRIES",
    "APP_BY_CODE",
    "APP_COUNTRIES",
    "Country",
    "OTHER_COUNTRIES",
    "mentioned",
    "normalize",
    "scope_for",
]
