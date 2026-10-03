"""API y páginas del radar.

Dos cosas: la gestión de perfiles y la lista de ofertas del dashboard. La
consulta de ofertas vive en `radar/ofertas.py`, que es donde está la lógica de
filtrar y ordenar; acá sólo se traducen los query params a filtros.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, ValidationError
from sqlalchemy.orm import Session

from radar import matching, ofertas
from radar.catalogo import normalizar
from radar.config import RAIZ, get_settings
from radar.db import get_session
from radar.models import Perfil
from radar.perfiles import formulario, serializacion, servicio
from radar.perfiles.validacion import PerfilCompleto

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
