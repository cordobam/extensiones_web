"""La fuente de ZonaJobs Argentina.

ZonaJobs (del grupo Bumeran) no deja leer su HTML: la lista es una SPA que
pinta un loader y después le pega a una API interna. Eso, lejos de ser un
obstáculo, es la mejor noticia posible --era justo la tercera pregunta del
roadmap ("¿hay RSS o JSON en vez de HTML? Si hay, gana el feed")--: en vez de
scrapear hay un JSON con todo adentro.

Lo que costó averiguar, verificado en vivo contra el portal:

- El endpoint es ``POST /api/avisos/searchV2?page=N&pageSize=20&sort=RECIENTES``
  y el ``page`` arranca en 0. El listado sale con `id`, título, descripción
  entera (`detalle`), empresa, fecha absoluta, ubicación y modalidad: no hace
  falta bajar el detalle oferta por oferta.
- No pide login, pero sí un header de sitio: ``x-site-id: ZJAR`` (sale de
  ``/candidate/site.js``). Sin él el backend devuelve 400 --``"No se incluyo
  el header x-site-id"``--, aunque venga un jwt válido en la mano. La
  respuesta trae ``x-session-jwt`` (vence en 30 días) que se reusa en los
  siguientes requests; el ``x-pre-session-token`` (uuid v4) sólo va mientras
  no haya jwt, igual que en la página.
- Los headers tienen que parecer de un navegador de verdad: con un User-Agent
  corto o sin ``Referer``, Cloudflare contesta 403 antes de que el backend
  llegue a mirar nada. Se manda el mismo juego que un Chrome de verdad.
- No trae salario. Hay un flag ``salarioObligatorio`` y nada más, así que el
  campo queda en None y ya: inventar un salario sería peor que no tenerlo.
- La fecha (``fechaHoraPublicacion``, "06-10-2026 19:00:08") es hora de
  Argentina. Desde 2009 el país no tiene horario de verano, así que el offset
  es siempre UTC-3 y no hace falta la base de tzdata (que en Windows no viene
  con Python).
- El ``robots.txt`` prohíbe ``/*recientes=true``, que es la query de las
  páginas HTML. Al API se le manda ``sort=RECIENTES``, que no matchea ese
  patrón, y se respeta el mismo ``request_delay`` que Computrabajo.
"""

from __future__ import annotations

import re
import time
import unicodedata
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_fixed

from radar.config import get_settings
from radar.fuentes.base import OfertaCruda

BASE_URL = "https://www.zonajobs.com.ar"
SITE_ID = "ZJAR"
BUSQUEDA = "api/avisos/searchV2"
TAMANIO_PAGINA = 20

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

#: El portal escribe las fechas en hora argentina y no las cambia nunca.
HORA_ARGENTINA = timezone(timedelta(hours=-3))

#: Lo que dice el portal ("Presencial") a lo que espera el contrato
#: (`Offer.modalidad`): "remoto", "híbrido", "presencial".
MODALIDADES = {
    "presencial": "presencial",
    "remoto": "remoto",
    "hibrido": "híbrido",
    "híbrido": "híbrido",
}

ESPACIOS_RE = re.compile(r"\s+")
# Los diacríticos que deja la descomposición NFD (U+0300..U+036F), igual que
# el regex del portal: /[\u0300-\u036f]/g.
COMBINANTES_RE = re.compile("[\u0300-\u036f]")
# La puntuación que el portal se saca de encima al armar la URL, más el
# apostrofo tipografico (U+2018) que el regex original incluye.
PUNTUACION_RE = re.compile("[!#$%&\u2018()*+,/:;=?@\\[\\]]")
# Lo único que se deja al final: los caracteres reservados de una URL
# (RFC 3986). El JavaScript del sitio sólo descarta lo no-ASCII, así que deja
# pasar símbolos como `|`; ver el docstring de `nombre_a_id`.
URL_SEGURA_RE = re.compile("[^A-Za-z0-9._~-]")


def nombre_a_id(texto: str) -> str:
    """El fragmento de URL que el portal arma a partir de un título.

    Sigue ``formatNameToStringId`` del JavaScript del sitio: pasa a
    minúsculas, le saca la barra (que separa secciones), baja los acentos con
    NFD, quita la puntuación de la lista y manda todo el resto a guiones.

    La única diferencia es el remate: el portal descarta sólo lo no-ASCII y
    deja símbolos como `|` adentro de la URL. Acá se queda con los caracteres
    reservados de RFC 3986, que es lo mismo salvo por esos símbolos, porque un
    `|` crudo hay que escaparlo en cada lado donde la URL se muestre o se
    copie. No cambia la oferta que se resuelve: el sitio ignora el slug y
    manda por el id.
    """
    plano = texto.lower().strip().replace("/", " ")
    plano = unicodedata.normalize("NFD", plano)
    plano = COMBINANTES_RE.sub("", plano)
    plano = PUNTUACION_RE.sub("", plano)
    plano = ESPACIOS_RE.sub(" ", plano)
    plano = plano.replace(" ", "-")
    plano = URL_SEGURA_RE.sub("", plano)
    # Los guiones van al final: sacar un `|` entre dos palabras deja dos
    # guiones seguidos y colapsarlos después es lo que hace el portal.
    return re.sub("-+", "-", plano).strip("-")


def url_aviso(item: dict[str, Any]) -> str:
    """La URL de una oferta, con la fórmula del sitio.

    ``/empleos/{título}[-{empresa}]-{id}.html``. Las ofertas confidenciales no
    llevan el tramo de empresa, porque no lo hay.
    """
    empresa = "" if item.get("confidencial") else item.get("empresa") or ""
    partes = nombre_a_id(item.get("titulo") or "")
    if empresa:
        partes = f"{partes}-{nombre_a_id(empresa)}"
    return f"{BASE_URL}/empleos/{partes}-{item['id']}.html"


def modalidad(valor: str | None) -> str | None:
    """"Híbrido" -> "híbrido". Lo que no se reconoce queda en None."""
    if not valor:
        return None
    return MODALIDADES.get(valor.strip().casefold())


def a_cruda(item: dict[str, Any]) -> OfertaCruda | None:
    """Un aviso del JSON a la fila cruda del contrato.

    Devuelve None si el aviso viene incompleto (sin id o sin título): una fila
    así no se puede guardar ni dedupe-ar, y tratar de adivinarle el título
    sería peor que perderla.
    """
    if not item.get("id") or not item.get("titulo"):
        return None
    return OfertaCruda(
        external_id=str(item["id"]),
        titulo=item["titulo"],
        empresa=item.get("empresa") or None,
        url=url_aviso(item),
        ubicacion=item.get("localizacion") or None,
        modalidad=modalidad(item.get("modalidadTrabajo")),
        # ZonaJobs no publica salario en el listado: sólo avisa si es
        # obligatorio mostrarlo (`salarioObligatorio`).
        salario=None,
        fecha_texto=item.get("fechaHoraPublicacion") or item.get("fechaPublicacion"),
        descripcion=item.get("detalle") or None,
    )


class Cliente:
    """Cliente HTTP de ZonaJobs: pausa, reintentos y la sesión del portal.

    Mismo espíritu que el de Computrabajo (delay de la config, tres intentos
    ante errores de transporte, ``httpx.Client`` inyectable para los tests) con
    la salvedad de este portal: hay que sostener el ``x-session-jwt`` entre
    requests.
    """

    #: Identificador de la fuente. Va en `Oferta.fuente` y como prefijo del
    #: `external_id`.
    id = "zonajobs"

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
        self._jwt: str | None = None
        self._headers = {
            "User-Agent": USER_AGENT,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "es-AR,es;q=0.9",
            "Content-Type": "application/json",
            "Origin": BASE_URL,
            "Referer": f"{BASE_URL}/empleos.html",
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

    def _headers_con_sesion(self) -> dict[str, str]:
        """Los headers de esta corrida.

        ``x-site-id`` va SIEMPRE: es el único header que el backend exige, y
        sin él contesta 400 (``"No se incluyo el header x-site-id"``), incluso
        con un jwt válido en la mano. Ese fue el error que encontró el smoke
        real: el jwt no reemplaza al site-id, se le suma.

        El ``x-session-jwt`` que devuelve la respuesta se manda a partir de la
        segunda vuelta, y mientras no exista va un ``x-pre-session-token`` (un
        uuid v4, como el que manda el sitio). El portal no lo pide --con sólo
        ``x-site-id`` responde--, pero es lo que hace la página real.
        """
        headers = dict(self._headers)
        headers["x-site-id"] = SITE_ID
        if self._jwt:
            headers["x-session-jwt"] = self._jwt
        else:
            headers["x-pre-session-token"] = str(uuid.uuid4())
        return headers

    @retry(
        retry=retry_if_exception_type((httpx.TransportError,)),
        stop=stop_after_attempt(3),
        wait=wait_fixed(2),
        reraise=True,
    )
    def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        """Un POST al API, con la sesión del portal.

        Ante un 403 con sesión se descarta el jwt y se reintenta una vez con
        un uuid fresco, que es lo que hace el sitio cuando la sesión venció.
        Un 403 sin sesión es Cloudflare enojado con los headers: ahí no hay
        segunda vuelta que valga, se propaga el error.
        """
        for _ in range(2):
            self._esperar()
            respuesta = self._cliente.post(
                f"{BASE_URL}/{path}",
                headers=self._headers_con_sesion(),
                json=body,
            )
            if respuesta.status_code == 403 and self._jwt:
                self._jwt = None
                continue
            respuesta.raise_for_status()
            jwt = respuesta.headers.get("x-session-jwt")
            if jwt:
                self._jwt = jwt
            return respuesta.json()
        raise RuntimeError("ZonaJobs rechazó la sesión después de reintentar.")

    # ---------------------------------------------------------- el contrato

    def listar(self, keyword: str, pagina: int) -> tuple[list[OfertaCruda], bool]:
        """Una página de resultados para una keyword.

        El portal pagina desde 0, así que la página 1 de la ingesta es la 0
        del API.
        """
        numero = pagina - 1
        datos = self._post(
            f"{BUSQUEDA}?pageSize={TAMANIO_PAGINA}&page={numero}&sort=RECIENTES",
            {
                "filtros": [],
                "busquedaExtendida": False,
                "query": keyword,
                "tipoDetalle": "full",
                "withHome": False,
            },
        )
        crudas = [
            cruda
            for item in datos.get("content") or []
            if (cruda := a_cruda(item)) is not None
        ]
        # `number`/`size`/`total` son la verdad del servidor: no hace falta
        # adivinar si hay más páginas ni parsear ningún "siguiente".
        tamanio = int(datos.get("size") or TAMANIO_PAGINA)
        actual = int(datos.get("number") or numero)
        hay_siguiente = (actual + 1) * tamanio < int(datos.get("total") or 0)
        return crudas, hay_siguiente

    def describir(self, cruda: OfertaCruda) -> str:
        """La descripción ya venía en el listado: no hay nada que pedir."""
        return cruda.descripcion or ""

    def fecha(self, cruda: OfertaCruda, ahora: datetime) -> datetime | None:
        """La fecha absoluta del aviso, pasada a UTC.

        Si el texto no es el formato del portal se devuelve None y la oferta
        se descarta, igual que con Computrabajo.
        """
        return parsear_fecha(cruda.fecha_texto)


def parsear_fecha(texto: str | None) -> datetime | None:
    """"06-10-2026 19:00:08" (hora argentina) -> datetime UTC.

    Acepta también la fecha corta ("06-10-2026"), por si el portal manda sólo
    el día. None si no es ninguna de las dos.
    """
    if not texto:
        return None
    for formato in ("%d-%m-%Y %H:%M:%S", "%d-%m-%Y"):
        try:
            momento = datetime.strptime(texto.strip(), formato)
        except ValueError:
            continue
        return momento.replace(tzinfo=HORA_ARGENTINA).astimezone(timezone.utc)
    return None


def listar(
    keyword: str, pagina: int = 1, cliente: Cliente | None = None
) -> list[OfertaCruda]:
    """Atajo para una sola página de listado."""
    if cliente is not None:
        crudas, _ = cliente.listar(keyword, pagina)
        return crudas
    with Cliente() as propio:
        crudas, _ = propio.listar(keyword, pagina)
        return crudas
