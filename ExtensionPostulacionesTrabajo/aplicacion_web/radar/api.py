"""API y páginas del radar.

Tres cosas: la gestión de perfiles, la lista de ofertas del dashboard y el
embudo de postulaciones. La consulta de ofertas vive en `radar/ofertas.py` y el
embudo en `radar/postulaciones/`, que son donde está la lógica de filtrar,
ordenar y mover estados; acá sólo se traducen los query params y los forms.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, ValidationError
from sqlalchemy import case, or_, select
from sqlalchemy.orm import Session

from radar import matching, ofertas
from radar.catalogo import normalizar
from radar.config import RAIZ, get_settings
from radar.db import get_session
from radar.models import Oferta, Perfil
from radar.perfiles import formulario, serializacion, servicio
from radar.perfiles.validacion import PerfilCompleto
from radar.postulaciones import estados
from radar.postulaciones import servicio as postulaciones

app = FastAPI(title="Radar Laboral")

app.mount("/static", StaticFiles(directory=RAIZ / "static"), name="static")
plantillas = Jinja2Templates(directory=RAIZ / "templates")


# ---------- vista para los templates ----------


class BusquedaVista(BaseModel):
    keywords: list[str]
    ubicaciones: list[str]
    modalidades: list[str]
    seniority: list[str]
    excluir: list[str]


CONTACTOS_VACIO: dict[str, Any] = {
    "nombre": "",
    "apellido": "",
    "email": "",
    "telefono": None,
    "ciudad": None,
    "linkedin": None,
}
REQUERIDOS_CONTACTO = ("nombre", "apellido", "email")


class PerfilVista(BaseModel):
    """Lo que la plantilla necesita. Siempre completo, incluso en el alta."""

    id: str
    nombre: str
    busqueda: BusquedaVista
    cv: dict[str, Any]

    @classmethod
    def vacio(cls) -> PerfilVista:
        return cls(
            id="",
            nombre="",
            busqueda=BusquedaVista(
                keywords=[], ubicaciones=[], modalidades=[], seniority=[], excluir=[]
            ),
            cv={
                "contacto": dict(CONTACTOS_VACIO),
                "presentacion": "",
                "skills": [],
                "experiencia": [],
                "educacion": [],
                "idiomas": [],
                "certificaciones": [],
            },
        )

    @classmethod
    def desde(cls, perfil: PerfilCompleto) -> PerfilVista:
        return cls(
            id=perfil.id,
            nombre=perfil.nombre,
            busqueda=BusquedaVista(**perfil.busqueda.model_dump()),
            cv={
                "contacto": perfil.cv.contacto.model_dump(),
                "presentacion": perfil.cv.presentacion or "",
                "skills": [s.model_dump() for s in perfil.cv.skills],
                "experiencia": [e.model_dump() for e in perfil.cv.experiencia],
                "educacion": [e.model_dump() for e in perfil.cv.educacion],
                "idiomas": [i.model_dump() for i in perfil.cv.idiomas],
                "certificaciones": [c.model_dump() for c in perfil.cv.certificaciones],
            },
        )

    @classmethod
    def desde_dict_sin_validar(cls, datos: dict) -> PerfilVista:
        """Para repintar el formulario cuando la validación falla.

        Se construye sin validar a propósito: si un campo es inválido igual
        queremos que el usuario vea lo que escribió, no un formulario vacío.
        """
        cv = dict(datos.get("cv") or {})
        contacto = cv.get("contacto") or {}
        return cls(
            id=str(datos.get("id") or ""),
            nombre=str(datos.get("nombre") or ""),
            busqueda=BusquedaVista(
                **{k: list(v or []) for k, v in (datos.get("busqueda") or {}).items()
                   if k in {"keywords", "ubicaciones", "modalidades", "seniority", "excluir"}}
            ),
            cv={
                "contacto": {
                    campo: (contacto.get(campo) or "" if campo in REQUERIDOS_CONTACTO
                            else contacto.get(campo))
                    for campo in CONTACTOS_VACIO
                },
                "presentacion": cv.get("presentacion") or "",
                "skills": [_texto_a_skill(s) for s in cv.get("skills") or []],
                "experiencia": cv.get("experiencia") or [],
                "educacion": cv.get("educacion") or [],
                "idiomas": cv.get("idiomas") or [],
                "certificaciones": cv.get("certificaciones") or [],
            },
        )


def _texto_a_skill(valor) -> dict:
    """Tolera que una fila de skill venga como texto sin parsear."""
    if isinstance(valor, dict):
        return valor
    return {"nombre": str(valor), "alias": [], "anios": None, "evidencia": None, "obligatorio": False}


# ---------- helpers ----------


def _errores(excepcion: ValidationError) -> list[str]:
    mensajes: list[str] = []
    for error in excepcion.errors():
        ubicacion = " → ".join(str(parte) for parte in error["loc"]) or "perfil"
        mensajes.append(f"{ubicacion}: {error['msg']}")
    return sorted(set(mensajes))


def _formulario(
    request: Request,
    *,
    vista: PerfilVista,
    accion: str,
    modo: str,
    errores: list[str] | None = None,
    aviso=None,
    status: int = 200,
) -> HTMLResponse:
    return plantillas.TemplateResponse(
        request=request,
        name="perfiles/formulario.html",
        context={
            "perfil": vista,
            "accion": accion,
            "modo": modo,
            "errores": errores or [],
            "aviso": aviso,
        },
        status_code=status,
    )


def _guardar(
    request: Request,
    sesion: Session,
    datos: dict,
    *,
    id_actual: str | None,
    directorio: Path,
    forzar: bool,
):
    if not forzar and id_actual:
        fila = sesion.get(Perfil, id_actual)
        if fila is not None:
            aviso = serializacion.archivo_editado_a_mano(
                id_actual, fila.actualizado_en, directorio
            )
            if aviso is not None:
                return _formulario(
                    request,
                    vista=PerfilVista.desde_dict_sin_validar(datos),
                    accion=str(request.url_for("actualizar_perfil", id_perfil=id_actual)),
                    modo="Editar",
                    aviso=aviso,
                )

    try:
        perfil = serializacion.desde_dict(datos)
    except ValidationError as error:
        return _formulario(
            request,
            vista=PerfilVista.desde_dict_sin_validar(datos),
            accion=str(request.url),
            modo="Editar" if id_actual else "Nuevo",
            errores=_errores(error),
            status=422,
        )

    if id_actual and perfil.id != id_actual:
        return _formulario(
            request,
            vista=PerfilVista.desde_dict_sin_validar(datos),
            accion=str(request.url),
            modo="Editar",
            errores=["El id de un perfil no se puede cambiar. Creá uno nuevo."],
            status=422,
        )

    if not id_actual and servicio.existe(sesion, perfil.id):
        return _formulario(
            request,
            vista=PerfilVista.desde_dict_sin_validar(datos),
            accion="/perfiles",
            modo="Nuevo",
            errores=[f"Ya existe un perfil con id {perfil.id!r}."],
            status=422,
        )

    servicio.guardar(sesion, perfil, directorio=directorio)
    return RedirectResponse("/perfiles", status_code=303)


# ---------- rutas ----------


@app.get("/", include_in_schema=False)
def raiz() -> RedirectResponse:
    return RedirectResponse("/ofertas", status_code=307)


@app.get("/ofertas")
def listar_ofertas(request: Request, sesion: Session = Depends(get_session)):
    """La pantalla que por fin muestra el trabajo.

    El perfil se elige con `?perfil=`, y si no se pasa va el primero activo. La
    pantalla siempre dice contra qué perfil comparó, porque el mismo aviso
    puntaje distinto según el CV y sin eso la lista no se puede leer.
    """
    perfiles, activos = servicio.listar_con_activo(sesion)
    pedido = request.query_params.get("perfil") or ""
    perfil = next((p for p in perfiles if p.id == pedido), None)
    if perfil is None:
        perfil = next((p for p in perfiles if p.id in activos), None) or (
            perfiles[0] if perfiles else None
        )

    filtros = _filtros_de_query(request, sesion, perfil.id if perfil else "")
    contexto: dict[str, Any] = {
        "perfiles": perfiles,
        "activos": activos,
        "perfil": perfil,
        "filtros": filtros,
        "listado": None,
        "ofertas_en_base": ofertas.contar_ofertas(sesion),
        "etiquetas": _ETIQUETAS,
        "aviso": _AVISOS.get(request.query_params.get("aviso") or ""),
        "error": _ERRORES.get(request.query_params.get("error") or ""),
    }

    if perfil is not None:
        listado = ofertas.listar(sesion, perfil, filtros)
        contexto["listado"] = listado
        contexto["sin_match"] = not ofertas.esta_puntuada(sesion, perfil.id)
        _, _, desconocidas = matching.conjuntos_perfil(perfil)
        contexto["skills_desconocidas"] = desconocidas

    return plantillas.TemplateResponse(
        request=request,
        name="ofertas/lista.html",
        context=contexto,
    )


def _filtros_de_query(
    request: Request, sesion: Session, perfil_id: str
) -> ofertas.Filtros:
    """Lee los filtros de la query string, tirando lo que no tiene sentido.

    Una URL escrita a mano con `nivel=inventado` o `pagina=-3` no puede romper
    la consulta: se ignoran y la pantalla muestra la lista entera.
    """
    params = request.query_params
    niveles = tuple(n for n in params.getlist("nivel") if n in ofertas.NIVELES)
    texto = (params.get("texto") or "").strip()[:120]
    skill = (params.get("skill") or "").strip()[:120]
    provincia = (params.get("provincia") or "").strip()[:120]
    try:
        pagina = int(params.get("pagina") or 1)
    except ValueError:
        pagina = 1
    filtros = ofertas.Filtros(
        niveles=niveles,
        texto=texto,
        skill=skill,
        incluir_descartadas=params.get("descartadas") in ("1", "true", "si"),
        pagina=pagina,
    )
    return replace(filtros, provincia=_provincia_valida(sesion, perfil_id, provincia))


def _provincia_valida(sesion: Session, perfil_id: str, pedida: str) -> str:
    """La provincia pedida, en su forma canónica, o "" si no existe.

    Se valida contra las provincias que el perfil tiene de verdad y no contra
    una lista de todas las del país, por lo mismo que el filtro de skill: elegir
    una provincia que ningún aviso menciona es una pantalla vacía.

    La comparación pasa por `catalogo.normalizar`, así que "CORDÓBA", "cordoba"
    y "Córdoba" son la misma y vuelven como "Córdoba". Eso importa porque la
    provincia sale de un `<select>` pero también se puede escribir a mano, y
    además vuelve de la query string: si no se canonizara, el filtro compararía
    con una cadena distinta de la que agrupa el dropdown y no devolvería nada.

    Ante una provincia que no existe se la ignora y se muestra la lista entera,
    como con `nivel=inventado`. La alternativa --dejar el filtro y responder
    "ninguna oferta coincide"-- parece más honesta, pero el `<select>` no tendría
    ninguna opción elegida y el usuario vería la lista completa sin entender por
    qué el filtro que puso no hizo nada.
    """
    if not pedida:
        return ""

    por_clave = {
        normalizar(nombre): nombre
        for nombre, _ in ofertas.provincias_de(sesion, perfil_id)
    }
    return por_clave.get(normalizar(pedida), "")


@app.get("/perfiles")
def listar_perfiles(request: Request, sesion: Session = Depends(get_session)):
    perfiles, activos = servicio.listar_con_activo(sesion)
    directorio = get_settings().perfiles_dir
    en_base = {p.id for p in perfiles}
    faltan = (
        sorted(p.stem for p in directorio.glob("*.yml") if p.stem not in en_base)
        if directorio.exists()
        else []
    )
    return plantillas.TemplateResponse(
        request=request,
        name="perfiles/lista.html",
        context={
            "perfiles": perfiles,
            "activos": activos,
            "importacion": request.query_params.get("importacion"),
            "faltan": faltan,
        },
    )


@app.post("/perfiles/importar")
def importar_perfiles(sesion: Session = Depends(get_session)) -> RedirectResponse:
    resultados = servicio.importar_desde_directorio(
        sesion, directorio=get_settings().perfiles_dir
    )
    resumen = ", ".join(
        f"{r.archivo}: {r.estado}" + (f" ({r.detalle})" if r.estado == "error" else "")
        for r in resultados
    ) or "no había archivos"
    return RedirectResponse(
        f"/perfiles?{urlencode({'importacion': resumen})}",
        status_code=303,
    )


@app.get("/perfiles/nuevo")
def nuevo_perfil(request: Request):
    return _formulario(
        request, vista=PerfilVista.vacio(), accion="/perfiles", modo="Nuevo"
    )


@app.post("/perfiles")
async def crear_perfil(request: Request, sesion: Session = Depends(get_session)):
    valores = formulario.agrupar((await request.form()).multi_items())
    return _guardar(
        request,
        sesion,
        formulario.a_dict_formulario(valores),
        id_actual=None,
        directorio=get_settings().perfiles_dir,
        forzar=formulario.valor(valores, "confirmar_sobrescritura") == "1",
    )


@app.get("/perfiles/{id_perfil}")
def editar_perfil(
    id_perfil: str, request: Request, sesion: Session = Depends(get_session)
):
    try:
        perfil = servicio.obtener(sesion, id_perfil)
    except servicio.PerfilNoEncontrado:
        return _formulario(
            request,
            vista=PerfilVista.vacio(),
            accion="/perfiles",
            modo="Nuevo",
            errores=[f"No existe el perfil {id_perfil!r}."],
            status=404,
        )
    return _formulario(
        request,
        vista=PerfilVista.desde(perfil),
        accion=str(request.url_for("actualizar_perfil", id_perfil=id_perfil)),
        modo="Editar",
    )


@app.post("/perfiles/{id_perfil}")
async def actualizar_perfil(
    id_perfil: str, request: Request, sesion: Session = Depends(get_session)
):
    valores = formulario.agrupar((await request.form()).multi_items())
    return _guardar(
        request,
        sesion,
        formulario.a_dict_formulario(valores),
        id_actual=id_perfil,
        directorio=get_settings().perfiles_dir,
        forzar=formulario.valor(valores, "confirmar_sobrescritura") == "1",
    )


@app.post("/perfiles/{id_perfil}/activar")
def activar_perfil(id_perfil: str, sesion: Session = Depends(get_session)) -> RedirectResponse:
    try:
        servicio.activar(sesion, id_perfil)
    except servicio.PerfilNoEncontrado:
        pass
    return RedirectResponse("/perfiles", status_code=303)


@app.post("/perfiles/{id_perfil}/desactivar")
def desactivar_perfil(id_perfil: str, sesion: Session = Depends(get_session)) -> RedirectResponse:
    try:
        servicio.desactivar(sesion, id_perfil)
    except servicio.PerfilNoEncontrado:
        pass
    return RedirectResponse("/perfiles", status_code=303)


@app.get("/perfiles/{id_perfil}/borrar")
def confirmar_borrado(
    id_perfil: str, request: Request, sesion: Session = Depends(get_session)
):
    try:
        perfil = servicio.obtener(sesion, id_perfil)
    except servicio.PerfilNoEncontrado:
        return RedirectResponse("/perfiles", status_code=303)
    return plantillas.TemplateResponse(
        request=request,
        name="perfiles/confirmar_borrado.html",
        context={"perfil": perfil},
    )


@app.post("/perfiles/{id_perfil}/borrar")
def borrar_perfil(id_perfil: str, sesion: Session = Depends(get_session)) -> RedirectResponse:
    try:
        servicio.borrar(sesion, id_perfil, directorio=get_settings().perfiles_dir)
    except servicio.PerfilNoEncontrado:
        pass
    return RedirectResponse("/perfiles", status_code=303)


@app.get("/health")
def health() -> dict[str, str]:
    return {"estado": "ok"}


# ---------- postulaciones: el embudo ----------


#: Los mensajes van por código y no por texto en la URL. El texto libre en la
#: query se refleja en la página, y aunque Jinja escapea, un código no deja que
#: un link con `?aviso=<script>` dependa de que el escape funcione.
_AVISOS = {
    "postulada": "Oferta marcada. Queda en Pendiente hasta que la mandes.",
    "ya-postulada": "Esa oferta ya estaba marcada.",
    "estado-guardado": "Estado actualizado.",
    "notas-guardadas": "Notas guardadas.",
    "desmarcada": "Postulación deshecha.",
}

_ERRORES = {
    "estado-invalido": "Ese estado no existe en el embudo.",
    "transicion-invalido": "Ese cambio de estado no está permitido desde donde está.",
    "perfil-desconocido": "Ese perfil no existe.",
    "oferta-desconocida": "Esa oferta no existe.",
}

#: Los prefijos que un `volver` puede usar. Es una lista blanca porque el campo
#: viene del form y se vuelve a mandar en un redirect: sin esto, un link con
#: `?volver=https://...` manda a la persona a otro sitio.
_ORIGENES_VALIDOS = ("/ofertas", "/postulaciones")

#: Cómo se muestra cada estado. Va en el contexto y no el módulo entero para
#: que la plantilla no tenga que llamar funciones de Python: `resultado_aceptada`
#: no se lee solo, y la traducción tiene que estar en un solo lugar.
_ETIQUETAS = {estado: estados.etiqueta(estado) for estado in estados.ESTADOS}


def _volver(pedido: str | None, por_defecto: str) -> str:
    """Adónde vuelve el form, validado contra open redirect.

    `startswith` alcanza con la lista blanca, pero `//host` y `/\\host` también
    empiezan con barra y algunos navegadores los tratan como absolutos, así que
    se rechazan aparte.
    """
    if not pedido:
        return por_defecto
    limpio = pedido.strip()
    if limpio.startswith(("//", "/\\")) or any(c in limpio for c in "\r\n\t"):
        return por_defecto
    if limpio.startswith(_ORIGENES_VALIDOS):
        return limpio
    return por_defecto


def _aviso_redirect(destino: str, codigo: str) -> RedirectResponse:
    separador = "&" if "?" in destino else "?"
    return RedirectResponse(f"{destino}{separador}aviso={codigo}", status_code=303)


def _error_redirect(destino: str, codigo: str) -> RedirectResponse:
    separador = "&" if "?" in destino else "?"
    return RedirectResponse(f"{destino}{separador}error={codigo}", status_code=303)


def _perfil_de_query(
    sesion: Session, pedido: str
) -> PerfilCompleto | None:
    """El perfil pedido, o el primero activo. Igual que en `/ofertas`."""
    perfiles, activos = servicio.listar_con_activo(sesion)
    elegido = next((p for p in perfiles if p.id == pedido), None)
    if elegido is not None:
        return elegido
    return next((p for p in perfiles if p.id in activos), None) or (
        perfiles[0] if perfiles else None
    )


@app.post("/postular")
async def marcar_postulacion(
    request: Request, sesion: Session = Depends(get_session)
) -> RedirectResponse:
    """Marca una oferta de la lista como postulándose.

    Va a `pendiente` y no a `enviada` a propósito: marcar es una intención, y la
    fecha de envío se guarda cuando la persona confirma que la mandó. Si esto
    marcara `enviada`, la fecha sería la de hacer clic, que no sirve para nada.
    """
    datos = await request.form()
    perfil_id = (datos.get("perfil") or "").strip()
    destino = _volver(datos.get("volver"), "/ofertas")

    try:
        oferta_id = int(datos.get("oferta_id") or "")
    except ValueError:
        return _error_redirect(destino, "transicion-invalido")

    try:
        postulaciones.crear(sesion, oferta_id, perfil_id)
    except postulaciones.PostulacionDuplicada:
        return _aviso_redirect(destino, "ya-postulada")
    except postulaciones.PerfilDesconocido:
        return _error_redirect(destino, "perfil-desconocido")
    return _aviso_redirect(destino, "postulada")


@app.get("/postulaciones")
def listar_postulaciones(request: Request, sesion: Session = Depends(get_session)):
    """El embudo: qué marcaste, en qué estado está y desde cuándo.

    Es la respuesta a la pregunta que `/ofertas` no puede contestar por sí sola:
    qué hago con esta lista. `/ofertas` dice cuáles ofertas parecen buenas; esta
    dice en cuál estás trabajando.
    """
    pedido = request.query_params.get("perfil") or ""
    perfil = _perfil_de_query(sesion, pedido)

    contexto: dict[str, Any] = {
        "perfiles": servicio.listar(sesion),
        "perfil": perfil,
        "postulaciones": [],
        "filas": [],
        "postulaciones_estados": estados.ESTADOS,
        "etiquetas": _ETIQUETAS,
        "conteos": {estado: 0 for estado in estados.ESTADOS},
        "aviso": _AVISOS.get(request.query_params.get("aviso") or ""),
        "error": _ERRORES.get(request.query_params.get("error") or ""),
    }

    if perfil is not None:
        filas = postulaciones.listar(sesion, perfil.id)
        contexto["filas"] = [postulaciones.de_una_fila(f) for f in filas]
        contexto["conteos"] = postulaciones.contar_por_estado(sesion, perfil.id)

    return plantillas.TemplateResponse(
        request=request,
        name="postulaciones/lista.html",
        context=contexto,
    )


@app.post("/postulaciones/{id_postulacion}")
async def actualizar_postulacion(
    id_postulacion: int, request: Request, sesion: Session = Depends(get_session)
) -> RedirectResponse:
    """Cambia el estado y guarda las notas.

    Estado y notas van en el mismo form porque se editan en la misma fila y
    guardar las notas no debería obligar a tocar el estado.
    """
    datos = await request.form()
    destino = _volver(datos.get("volver"), "/postulaciones")
    nuevo = (datos.get("estado") or "").strip()

    try:
        postulaciones.cambiar_estado(sesion, id_postulacion, nuevo)
    except postulaciones.PostulacionNoEncontrada:
        return RedirectResponse("/postulaciones", status_code=303)
    except postulaciones.EstadoInvalido:
        return _error_redirect(destino, "estado-invalido")
    except postulaciones.TransicionInvalida:
        return _error_redirect(destino, "transicion-invalido")

    # Sólo si viene el campo. Si un form mandara sólo el estado, tocar las notas
    # sería borrarlas, porque `datos.get` no distingue "no vino" de "viene vacío".
    if "notas" in datos:
        notas = datos.get("notas")
        postulaciones.poner_notas(
            sesion, id_postulacion, notas if isinstance(notas, str) else None
        )
    return _aviso_redirect(destino, "estado-guardado")


@app.post("/postulaciones/{id_postulacion}/borrar")
def borrar_postulacion(
    id_postulacion: int, sesion: Session = Depends(get_session)
) -> RedirectResponse:
    """Desmarca. Es el deshacer del botón "Postular"."""
    try:
        postulaciones.borrar(sesion, id_postulacion)
    except postulaciones.PostulacionNoEncontrada:
        pass
    return RedirectResponse("/postulaciones", status_code=303)


# ---------- la API que consume la extensión de Firefox ----------


#: Cabecera que tiene que mandar la extensión.
#:
#: No es autenticación: anybody que pueda mandar esa cabecera puede leer el CV
#: de los perfiles y tocar el embudo. Es un candado, no una llave, y está puesto
#: por una razón concreta: la app escucha en 127.0.0.1:8000, así que cualquier
#: página que abras en el navegador puede intentar pegarle un POST. Una cabecera
#: personalizada obliga al navegador a hacer un preflight, y como la app no
#: responde con `Access-Control-Allow-*`, ese preflight falla y la petición nunca
#: sale. La extensión sí la manda porque tiene `host_permissions` para el
#: localhost.
#:
#: Para una app personal en localhost con datos que ya están en tu disco, es
#: suficiente. Si algún día corre en una red, esto hay que reemplazarlo por algo
#: de verdad: un token compartido, o mejor, que la extensión no acceda al CV por
#: HTTP y lo lea de un archivo generado en tu máquina.
CABECERA_EXTENSION = "X-Radar-Extension"
CLAVE_EXTENSION = "1"


def _es_la_extension(request: Request) -> bool:
    return request.headers.get(CABECERA_EXTENSION) == CLAVE_EXTENSION


def _rechazar_si_no_es_extension(request: Request) -> JSONResponse:
    return JSONResponse(
        {
            "ok": False,
            "error": "sin-cabecera",
            "mensaje": "Esta ruta sólo la usa la extensión del navegador.",
        },
        status_code=403,
    )


def _id_del_portal(external_id: str) -> str:
    """El id pelado, como lo trae la URL del portal.

    Desde la migración 0005 lo guardado lleva prefijo (`computrabajo:ABC123`),
    pero la extensión compara este valor con el `?oi=` del formulario, que no
    lo trae. Devolver el prefijado rompería esa comparación en silencio: la
    postulación existiría y la extensión no la reconocería.

    `/api/extension/enviada` acepta las dos formas, así que mandar este id
    crudo de vuelta siempre cierra el circuito.
    """
    return external_id.rsplit(":", 1)[-1]


def _instante(momento: datetime | None) -> str | None:
    """Una fecha de la base como texto, siempre en UTC.

    Sin esto el mismo envío puede volver como `2026-10-05T01:46:57-03:00` en una
    respuesta y `2026-10-05T04:46:57+00:00` en la siguiente, según si el valor se
    leyó de PostgreSQL --que lo devuelve en la zona de la conexión-- o quedó en
    memoria después del `INSERT`, que lo dejó en UTC. Son el mismo instante, pero
    la extensión no puede comparar dos textos que cambian de forma, ni pintar
    "la mandaste a las 1:46" y después "a las 4:46".
    """
    if momento is None:
        return None
    if momento.tzinfo is None:
        return momento.replace(tzinfo=timezone.utc).isoformat()
    return momento.astimezone(timezone.utc).isoformat()


class EnvioExtension(BaseModel):
    """El cuerpo de `POST /api/extension/enviada`."""

    perfil: str
    external_id: str


@app.get("/api/extension/datos")
def datos_para_la_extension(
    request: Request, perfil: str | None = None, sesion: Session = Depends(get_session)
) -> JSONResponse:
    """Todo lo que la extensión necesita, en un solo request.

    Son tres bloques y van juntos a propósito. La extensión necesita el CV para
    llenar el formulario y necesita saber qué ofertas están en el embudo para no
    ofrecer registrar un envío donde ya no corresponde. Si fueran dos requests,
    la extensión podría tener el CV de una versión y el embudo de otra, y el
    resultado sería rellenar datos viejos.

    `perfil` es opcional: sin él va el primer perfil activo, igual que en
    `/ofertas`. Con varias pestañas de la extensión abierta, cada una puede
    apuntar a un perfil distinto, así que el parámetro tiene que servir.

    `perfiles` lista todos los cargados con su `activo`. Es lo que llena el
    selector de la página de opciones: sin ese listado la extensión tendría
    que adivinar ids o limitarse a un solo perfil.
    """
    if not _es_la_extension(request):
        return _rechazar_si_no_es_extension(request)

    perfiles, activos = servicio.listar_con_activo(sesion)
    elegido = next((p for p in perfiles if p.id == perfil), None)
    if elegido is None and perfil:
        return JSONResponse(
            {
                "ok": False,
                "error": "perfil-desconocido",
                "mensaje": f"No existe el perfil {perfil!r}.",
                "perfiles_disponibles": [p.id for p in perfiles],
            },
            status_code=404,
        )
    if elegido is None:
        elegido = next((p for p in perfiles if p.id in activos), None) or (
            perfiles[0] if perfiles else None
        )
    if elegido is None:
        return JSONResponse(
            {
                "ok": False,
                "error": "sin-perfiles",
                "mensaje": "No hay ningún perfil cargado.",
            },
            status_code=404,
        )

    vivas = [
        fila
        for fila in postulaciones.listar(sesion, elegido.id)
        if fila.estado != "archivada"
    ]

    return JSONResponse(
        {
            "ok": True,
            "perfil": elegido.id,
            "nombre": elegido.nombre,
            "activo": elegido.id in activos,
            "perfiles": [
                {"id": p.id, "nombre": p.nombre, "activo": p.id in activos}
                for p in perfiles
            ],
            "cv": {
                "contacto": elegido.cv.contacto.model_dump(mode="json"),
                "presentacion": elegido.cv.presentacion,
            },
            "postulaciones": [
                {
                    "id": fila.id,
                    "external_id": _id_del_portal(fila.oferta.external_id),
                    "titulo": fila.oferta.titulo,
                    "empresa": fila.oferta.empresa,
                    "url": fila.oferta.url,
                    "estado": fila.estado,
                    "etiqueta": estados.etiqueta(fila.estado),
                    "fecha_postulacion": _instante(fila.fecha_postulacion),
                }
                for fila in vivas
            ],
        }
    )


@app.post("/api/extension/enviada")
def registrar_enviado_por_extension(
    request: Request, cuerpo: EnvioExtension, sesion: Session = Depends(get_session)
) -> JSONResponse:
    """Registra el envío de una postulación, desde la extensión.

    La extensión manda el `external_id` de la oferta, que es lo que viene en la
    URL del formulario del portal (`?oi=<external_id>`). Con eso se evita que
    la extensión tenga que mandar ids internos de la base, que cambian con cada
    dump y no le dicen nada a quien la usa.

    Desde la migración 0005 los ids guardados llevan prefijo
    ("computrabajo:ABC"), pero el de la URL viene crudo, así que se aceptan las
    dos formas. Ante la ambigüedad --el mismo id crudo en dos portales-- gana
    la coincidencia exacta, y después el id más bajo, para que la respuesta sea
    siempre la misma.

    Nunca devuelve 4xx por una transición inválida. Si la postulación ya estaba
    más avanzada, el reporte llegó tarde pero no está mal, y la extensión
    tiene que poder pintar el estado real sin tratarlo como un error.
    """
    if not _es_la_extension(request):
        return _rechazar_si_no_es_extension(request)

    candidato = cuerpo.external_id
    oferta = sesion.scalars(
        select(Oferta)
        .where(
            or_(
                Oferta.external_id == candidato,
                Oferta.external_id.endswith(f":{candidato}"),
            )
        )
        .order_by(
            case((Oferta.external_id == candidato, 0), else_=1),
            Oferta.id,
        )
    ).first()
    if oferta is None:
        return JSONResponse(
            {
                "ok": False,
                "error": "oferta-desconocida",
                "mensaje": (
                    f"La oferta {cuerpo.external_id!r} no está en el radar. "
                    "Cargala con `radar ingest` primero."
                ),
            },
            status_code=404,
        )

    try:
        registro = postulaciones.registrar_envio(sesion, oferta, cuerpo.perfil)
    except postulaciones.PerfilDesconocido:
        return JSONResponse(
            {
                "ok": False,
                "error": "perfil-desconocido",
                "mensaje": f"No existe el perfil {cuerpo.perfil!r}.",
            },
            status_code=404,
        )

    fila = registro.postulacion
    return JSONResponse(
        {
            "ok": registro.enviada,
            "id": fila.id,
            "external_id": _id_del_portal(oferta.external_id),
            "titulo": oferta.titulo,
            "estado": fila.estado,
            "etiqueta": estados.etiqueta(fila.estado),
            "fecha_postulacion": _instante(fila.fecha_postulacion),
            "mensaje": registro.mensaje,
        }
    )
