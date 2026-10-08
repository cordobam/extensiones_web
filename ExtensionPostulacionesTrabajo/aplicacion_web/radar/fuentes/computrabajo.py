"""La fuente de Computrabajo Argentina.

El listado se pide por keyword: /trabajo-de-{slug}?p=N trae 20 ofertas por
página, ordenadas de más reciente a más vieja. El detalle de cada oferta es una
request aparte.

Tres cosas del portal que costaron aprender y conviene no volver a descubrir:

- El `data-id` de cada oferta viene con comillas SIMPLES (`data-id='ABC...'`),
  no dobles. El selector CSS funciona igual, pero un `re.findall` escrito a mano
  con comillas dobles no encuentra nada.
- La modalidad no es una clase `tag`: es un ícono (`icon i_home` para remoto,
  `icon i_home_office` para "presencial y remoto") y el texto está en el span
  padre. Buscar la palabra "Remoto" en el ícono no devuelve nada.
- El href del listado trae un fragmento de tracking (`#lc=ListOffers-...`) que
  cambia en cada visita. Si se guarda tal cual, la misma oferta acaba con URLs
  distintas y las comparaciones entre corridas se ensucian.

Verificado contra el portal: el listado y el detalle responden sin autenticación,
así que no hay que manejar cookies ni sesiones.
"""

from __future__ import annotations

import re
import time
from datetime import datetime
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx
from selectolax.parser import HTMLParser
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_fixed

from radar.config import get_settings
from radar.fuentes.base import OfertaCruda
from radar.fuentes.fechas import parsear

BASE_URL = "https://ar.computrabajo.com"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

# El listado viene en español rioplatense y el slug va con guiones.
SLUG_RE = re.compile(r"[^a-z0-9]+")


def slug(keyword: str) -> str:
    """Convierte una keyword en el slug que espera el portal.

    "mesa de ayuda" -> "mesa-de-ayuda", "Atención al Cliente" -> "atencion-al-cliente".
    """
    plano = keyword.strip().casefold()
    plano = plano.replace("í", "i").replace("á", "a").replace("é", "e")
    plano = plano.replace("ó", "o").replace("ú", "u")
    return SLUG_RE.sub("-", plano).strip("-")


def url_listado(keyword: str, pagina: int = 1) -> str:
    return f"{BASE_URL}/trabajo-de-{slug(keyword)}?p={pagina}"


def url_detalle(href: str) -> str:
    """Pasa un href relativo del listado a una URL absoluta y estable.

    Le saca el fragmento de tracking, que es distinto en cada visita.
    """
    partes = urlsplit(urljoin(BASE_URL, href))
    return urlunsplit((partes.scheme, partes.netloc, partes.path, partes.query, ""))


def _texto(nodo) -> str | None:
    if nodo is None:
        return None
    texto = nodo.text(strip=True)
    return texto or None


def _ubicacion(article: HTMLParser) -> str | None:
    """La ubicación está en un `p.fc_base.mt5` sin `dFlex`.

    El mismo article tiene otro `p.fc_base.mt5` con `dFlex` que lleva el
    puntaje de la empresa y el nombre. Buscar el primero sin filtrar trae
    "4,5Universidad Siglo 21" en vez de la ciudad.
    """
    for parrafo in article.css("p.fc_base.mt5"):
        if "dFlex" in (parrafo.attributes.get("class") or ""):
            continue
        hijo = parrafo.css_first("span.mr10")
        if hijo is not None:
            return _texto(hijo)
    return None


def _modalidad(article: HTMLParser) -> str | None:
    """El ícono va vacío: el texto está en el span que lo contiene.

    "Presencial y remoto" es híbrido a todos los efectos, así que se traduce a
    la palabra que usan las modalidades del perfil.
    """
    icono = article.css_first("span.icon[class*='i_home']")
    if icono is None:
        return None

    padre = icono.parent
    texto = padre.text(strip=True).casefold() if padre is not None else ""
    if not texto:
        return None
    if "y remoto" in texto or "híbrido" in texto or "hibrido" in texto:
        return "híbrido"
    if "remoto" in texto or "teletrabajo" in texto:
        return "remoto"
    if "presencial" in texto:
        return "presencial"
    return None


def _fecha(article: HTMLParser) -> str | None:
    for parrafo in article.css("p.fc_aux"):
        return _texto(parrafo)
    return None


def _salario(article: HTMLParser) -> str | None:
    """El ícono de salary está vacío y el texto, en el span padre."""
    icono = article.css_first("span.icon.i_salary")
    if icono is None or icono.parent is None:
        return None
    return _texto(icono.parent)


def parsear_listado(html: str) -> list[OfertaCruda]:
    """Saca las ofertas de un HTML de listado."""
    arbol = HTMLParser(html)
    ofertas: list[OfertaCruda] = []

    for article in arbol.css("article.box_offer[data-id]"):
        external_id = (article.attributes.get("data-id") or "").strip()
        if not external_id:
            continue

        enlace = article.css_first("h2 a.js-o-link") or article.css_first("h2 a")
        if enlace is None:
            continue

        titulo = enlace.text(strip=True)
        if not titulo:
            continue

        href = enlace.attributes.get("href") or ""

        ofertas.append(
            OfertaCruda(
                external_id=external_id,
                titulo=titulo,
                empresa=_texto(article.css_first("a[offer-grid-article-company-url]")),
                url=url_detalle(href),
                ubicacion=_ubicacion(article),
                modalidad=_modalidad(article),
                salario=_salario(article),
                fecha_texto=_fecha(article),
            )
        )

    return ofertas


def _texto_descripcion(seccion) -> str:
    """Texto del bloque de oferta, parando antes de los botones.

    La sección `div-link="oferta"` contiene la descripción, los requisitos y las
    palabras clave del portal, pero después viene la barra de "Postularme" y la
    de "Avísame con ofertas similares / Denunciar empleo". Si se saca el texto de
    toda la sección, esas frases entran en la descripción y después el catálogo
    las matchea contra cualquier skill que aparezca en la interfaz.
    """
    partes: list[str] = []
    hijo = seccion.child
    while hijo is not None:
        if hijo.tag == "-text":
            hijo = hijo.next
            continue

        clase = hijo.attributes.get("class") or ""
        if "posSticky" in clase or "tc_fx" in clase:
            break

        texto = hijo.text(separator="\n", strip=True)
        if texto:
            partes.append(texto)
        hijo = hijo.next

    return "\n\n".join(partes)


def parsear_detalle(html: str) -> str:
    """Saca la descripción del HTML de una oferta.

    El cuerpo empieza en el `h2` "Descripción de la oferta" y termina donde
    arrancan los botones de "Postularme".
    """
    arbol = HTMLParser(html)

    for seccion in arbol.css('div[div-link="oferta"]'):
        encabezado = seccion.css_first("h2")
        if encabezado is None:
            continue
        if "descripci" not in encabezado.text(strip=True).casefold():
            continue
        texto = _texto_descripcion(seccion)
        if texto:
            return texto

    return ""


def hay_siguiente_pagina(html: str) -> bool:
    """El listado termina paginando con un span `data-path` que apunta a p=N+1."""
    arbol = HTMLParser(html)
    siguiente = arbol.css_first("span.buildLink[data-path]")
    if siguiente is None:
        return False
    return "?p=" in (siguiente.attributes.get("data-path") or "")


class Cliente:
    """Cliente HTTP de Computrabajo, con pausa entre peticiones y reintentos.

    El `request_delay` sale de la config (2 s por defecto). No es un detalle:
    `robots.txt` prohíbe los filtros por query que reducirían el tráfico, así
    que la única forma de ser respetuoso es ir despacio.

    Es además la implementación de la fuente (`base.Fuente`): `listar`,
    `describir` y `fecha` son el contrato que la ingesta llama, y lo envuelven
    al HTML de este portal.
    """

    #: Identificador de la fuente. Va en `Oferta.fuente` y como prefijo del
    #: `external_id`.
    id = "computrabajo"

    def __init__(
        self,
        delay: float | None = None,
        timeout: float = 20.0,
        cliente: httpx.Client | None = None,
    ) -> None:
        settings = get_settings()
        self.delay = settings.request_delay if delay is None else delay
        self._ultima: float | None = None
        self._propio = cliente is None
        self._cliente = cliente if cliente is not None else httpx.Client(
            timeout=timeout,
            follow_redirects=True,
        )
        self._headers = {
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "es-AR,es;q=0.9",
        }

    def __enter__(self) -> Cliente:
        return self

    def __exit__(self, *_) -> None:
        self.close()

    def close(self) -> None:
        if self._propio:
            self._cliente.close()

    def _esperar(self) -> None:
        """Espera lo que falte para no pegarle dos requests seguido."""
        if self._ultima is None or self.delay <= 0:
            self._ultima = time.monotonic()
            return
        transcurrido = time.monotonic() - self._ultima
        if transcurrido < self.delay:
            time.sleep(self.delay - transcurrido)
        self._ultima = time.monotonic()

    @retry(
        retry=retry_if_exception_type((httpx.TransportError,)),
        stop=stop_after_attempt(3),
        wait=wait_fixed(2),
        reraise=True,
    )
    def _descargar(self, url: str) -> str:
        self._esperar()
        respuesta = self._cliente.get(url, headers=self._headers)
        respuesta.raise_for_status()
        return respuesta.text

    def listado(self, keyword: str, pagina: int = 1) -> str:
        return self._descargar(url_listado(keyword, pagina))

    def detalle(self, url: str) -> str:
        return self._descargar(url)

    def buscar(self, keyword: str, pagina: int = 1) -> list[OfertaCruda]:
        return parsear_listado(self.listado(keyword, pagina))

    # ---------------------------------------------------------- el contrato

    def listar(self, keyword: str, pagina: int) -> tuple[list[OfertaCruda], bool]:
        """Una página del listado, con la señal de si hay una siguiente."""
        html = self.listado(keyword, pagina)
        return parsear_listado(html), hay_siguiente_pagina(html)

    def describir(self, cruda: OfertaCruda) -> str:
        """La descripción: acá sí hay que pedirle la página de detalle."""
        return parsear_detalle(self.detalle(cruda.url))

    def fecha(self, cruda: OfertaCruda, ahora: datetime) -> datetime | None:
        """La fecha del listado ("Hace 19 horas"), que es relativa al reloj."""
        return parsear(cruda.fecha_texto, ahora)


def listar(keyword: str, pagina: int = 1, cliente: Cliente | None = None) -> list[OfertaCruda]:
    """Atajo para una sola página de listado."""
    if cliente is not None:
        return cliente.buscar(keyword, pagina)
    with Cliente() as propio:
        return propio.buscar(keyword, pagina)
