"""La ingesta: de las páginas del portal a la tabla `ofertas`.

Es la costura entre la capa de fuentes y la base. Hace tres cosas, en orden:

1. Recorre los perfiles **activos** (pueden ser varios) y sus keywords.
2. Filtra por fecha ANTES de bajar los detalles. Es la decisión que hace que
   esto sea barato: el listado trae la fecha de cada oferta, así que se pueden
   descartar las viejas sin pedir 20 páginas de detalle que no vamos a usar.
3. Guarda con upsert por `external_id`. Una oferta que ya existe se actualiza
   pero no vuelve a bajar su detalle.

Devuelve un resumen con números, porque la Fase 9 (CLI) lo va a imprimir y un
proceso que no dice qué hizo es un proceso en el que no se puede confiar.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from radar import catalogo as modulo_catalogo
from radar.config import get_settings
from radar.fuentes import computrabajo, fechas
from radar.fuentes.computrabajo import Cliente, OfertaCruda
from radar.models import Oferta, OfertaSkill
from radar.perfiles import servicio
from radar.perfiles.validacion import PerfilCompleto
from radar.schema import Offer


@dataclass
class Resumen:
    """Qué pasó en una corrida de ingesta."""

    perfiles: list[str] = field(default_factory=list)
    ofertas_vistas: int = 0
    nuevas: int = 0
    actualizadas: int = 0
    duplicadas: int = 0
    fuera_de_ventana: int = 0
    sin_fecha: int = 0
    fallidas: list[tuple[str, str]] = field(default_factory=list)

    def linea(self) -> str:
        partes = [
            f"{self.ofertas_vistas} vistas",
            f"{self.nuevas} nuevas",
            f"{self.actualizadas} actualizadas",
        ]
        if self.duplicadas:
            partes.append(f"{self.duplicadas} repetidas entre keywords")
        if self.fuera_de_ventana:
            partes.append(f"{self.fuera_de_ventana} fuera de la ventana")
        if self.sin_fecha:
            partes.append(f"{self.sin_fecha} sin fecha legible")
        if self.fallidas:
            partes.append(f"{len(self.fallidas)} fallidas")
        return ", ".join(partes)


def _texto_para_detectar(cruda: OfertaCruda, descripcion: str) -> str:
    """El texto del que se sacan las skills: el título SIEMPRE, más la descripción.

    El título es la mitad de la detección y costaba verlo. Medido sobre 40
    ofertas reales de un perfil de soporte IT: 35 de 40 (87%) nombran en el
    título alguna skill obligatoria del perfil, y ninguna nombra las tres. O sea
    que si sólo se mira la descripción, "Mesa de Ayuda" y "Soporte Técnico" se
    pierden en la mayoría de los avisos, y esas dos son precisamente las
    obligatorias del perfil.

    No mete falsos positivos: sobre los mismos 40 títulos, las skills que salen
    son exactamente el nombre del puesto. El título no inventa nada.
    """
    partes = [cruda.titulo]
    if descripcion:
        partes.append(descripcion)
    return " ".join(p for p in partes if p)


def _sin_segundos(fecha: datetime | None) -> datetime | None:
    """Quita los segundos de una fecha de publicación.

    El portal no dice "Hace 19 horas 3 minutos", dice "Hace 19 horas". Así que
    la fecha se calcula restándole horas al reloj de cada corrida, y dos
    corridas separadas por diez minutos dan dos valores distintos. Sin
    truncar, el upsert cree que la oferta cambió siempre y la reescribe en cada
    pasada. Al minuto, dos corridas que caen en el mismo minuto coinciden.
    """
    if fecha is None:
        return None
    return fecha.replace(second=0, microsecond=0)


def _a_offer(cruda: OfertaCruda, fecha: datetime | None, skills: list[str]) -> Offer:
    """Arma el contrato de la capa de fuentes a partir del listado.

    `external_id` es el `data-id` del portal: 32 hex, estable entre corridas.
    """
    return Offer(
        external_id=cruda.external_id,
        titulo=cruda.titulo,
        empresa=cruda.empresa,
        url=cruda.url,
        ubicacion=cruda.ubicacion,
        modalidad=cruda.modalidad,
        salario=cruda.salario,
        fecha_publicacion=_sin_segundos(fecha),
        skills=skills,
        fuente="computrabajo",
    )


def _guardar(sesion: Session, offer: Offer) -> str:
    """Upsert por external_id. Devuelve 'nueva', 'actualizada' o 'repetida'."""
    fila = sesion.query(Oferta).filter(Oferta.external_id == offer.external_id).one_or_none()

    if fila is None:
        sesion.add(_nueva_fila(offer))
        return "nueva"

    if _sin_cambios(fila, offer):
        return "repetida"

    _actualizar(fila, offer)
    return "actualizada"


def _sin_cambios(fila: Oferta, offer: Offer) -> bool:
    """True si la fila guardada ya tiene todo lo que trae esta corrida.

    Compara sólo los campos que la corrida trajo. Un `None` significa "no lo
    sé en esta pasada", no "borrá lo que había": si no, una corrida que no baja
    detalles marcaría veinte ofertas como modificadas y las reescribiría.
    """
    if offer.descripcion is not None and fila.descripcion != offer.descripcion:
        return False
    if offer.skills and sorted(s.skill for s in fila.skills) != sorted(offer.skills):
        return False
    if offer.fecha_publicacion is not None and fila.fecha_publicacion != offer.fecha_publicacion:
        return False
    return (
        fila.titulo == offer.titulo
        and fila.empresa == offer.empresa
        and fila.url == offer.url
        and fila.ubicacion == offer.ubicacion
        and fila.modalidad == offer.modalidad
        and fila.salario == offer.salario
    )


def _nueva_fila(offer: Offer) -> Oferta:
    fila = Oferta(
        external_id=offer.external_id,
        fuente=offer.fuente,
        titulo=offer.titulo,
        empresa=offer.empresa,
        url=offer.url,
        ubicacion=offer.ubicacion,
        modalidad=offer.modalidad,
        salario=offer.salario,
        fecha_publicacion=offer.fecha_publicacion,
        descripcion=offer.descripcion,
    )
    fila.skills = [OfertaSkill(skill=s) for s in offer.skills]
    return fila


def _actualizar(fila: Oferta, offer: Offer) -> None:
    fila.titulo = offer.titulo
    fila.empresa = offer.empresa
    fila.url = offer.url
    fila.ubicacion = offer.ubicacion
    fila.modalidad = offer.modalidad
    fila.salario = offer.salario
    if offer.fecha_publicacion is not None:
        fila.fecha_publicacion = offer.fecha_publicacion
    if offer.descripcion:
        fila.descripcion = offer.descripcion
    _actualizar_skills(fila, offer.skills)


def _actualizar_skills(fila: Oferta, skills: list[str]) -> None:
    """Deja las skills de la fila iguales a las de la corrida, con un diff.

    No se reemplaza la colección entera (`fila.skills = [...]`) porque eso le
    dice a SQLAlchemy "borra estos objetos huérfanos e insertá estos nuevos". Con
    la misma lista de skills, borra cinco filas e inserta las mismas cinco, y en
    el mismo flush el INSERT puede ejecutarse antes que el DELETE. El resultado
    es un `UniqueViolation` sobre `uq_oferta_skill`, que es justo lo que pasaba
    en la segunda corrida de una oferta cuya fecha se había movido.

    Con el diff, una skill que sigue estando no se toca, y sólo se borran las
    que desaparecieron de verdad.
    """
    actuales = {s.skill: s for s in fila.skills}
    nuevas = set(skills)

    for nombre, objeto in actuales.items():
        if nombre not in nuevas:
            fila.skills.remove(objeto)

    for nombre in sorted(nuevas - set(actuales)):
        fila.skills.append(OfertaSkill(skill=nombre))


def _recorrer_listados(
    perfil: PerfilCompleto,
    cliente: Cliente,
    resumen: Resumen,
    dias: int,
    ahora: datetime,
) -> list[OfertaCruda]:
    """Recorre las páginas del perfil y se queda con las que están en ventana.

    Para cuando una página no trae ninguna oferta que no hayamos visto: el
    listado viene ordenado por fecha, así que una página entera de viejas es la
    última y seguir bajando es traer basura.
    """
    limite = ahora - timedelta(days=dias)

    vistas: dict[str, OfertaCruda] = {}
    # Lo que ya se vio, se guardó o se descartó. El corte de página necesita
    # lo segundo ("ya la vi"), el guardado necesita lo primero.
    en_vista: set[str] = set()
    max_paginas = get_settings().paginas_max_por_keyword

    for keyword in perfil.busqueda.keywords:
        for pagina in range(1, max_paginas + 1):
            try:
                html = cliente.listado(keyword, pagina)
            except Exception as error:  # noqa: BLE001
                resumen.fallidas.append((f"{keyword} p{pagina}", str(error)))
                break

            crudas = computrabajo.parsear_listado(html)
            if not crudas:
                break

            nuevas_en_pagina = 0
            for cruda in crudas:
                resumen.ofertas_vistas += 1

                # El dedupe va antes del filtro de fecha: la misma oferta sale
                # en varias keywords y, si ya la contamos, contarla de nuevo
                # infla los números que después se comparan entre corridas.
                if cruda.external_id in en_vista:
                    resumen.duplicadas += 1
                    continue
                en_vista.add(cruda.external_id)

                fecha = fechas.parsear(cruda.fecha_texto, ahora)
                if fecha is None:
                    # No sabemos cuándo se publicó: se descarta. Preferimos
                    # perder una oferta a mostrar una vieja como si fuera nueva.
                    resumen.sin_fecha += 1
                    continue
                if fecha < limite:
                    resumen.fuera_de_ventana += 1
                    continue

                vistas[cruda.external_id] = cruda
                nuevas_en_pagina += 1

            if nuevas_en_pagina == 0:
                break
            if not computrabajo.hay_siguiente_pagina(html):
                break

    return list(vistas.values())


def ingestar_perfil(
    sesion: Session,
    perfil: PerfilCompleto,
    cliente: Cliente | None = None,
    dias: int | None = None,
    con_detalle: bool = True,
    resumen: Resumen | None = None,
    ahora: datetime | None = None,
) -> Resumen:
    """Trae las ofertas de un perfil y las guarda.

    `con_detalle=False` sirve para probar el pipeline rápido: trae los listados,
    filtra por fecha y guarda, pero no baja ninguna página de detalle. Quedan
    sin descripción y sin skills, que es lo único que el detalle agrega.

    `ahora` existe para los tests: las fechas del fixture son relativas, así
    que sin un reloj fijo un test de ventana puede fallar por el reloj y no por
    el código.
    """
    settings = get_settings()
    ventana = settings.dias_por_defecto if dias is None else dias
    referencia = ahora if ahora is not None else datetime.now(timezone.utc)

    propio = cliente is None
    cliente = cliente if cliente is not None else Cliente()

    res = resumen if resumen is not None else Resumen()
    if perfil.id not in res.perfiles:
        res.perfiles.append(perfil.id)

    try:
        crudas = _recorrer_listados(perfil, cliente, res, ventana, referencia)
        cat = modulo_catalogo.catalogo()

        for cruda in crudas:
            existing = (
                sesion.query(Oferta).filter(Oferta.external_id == cruda.external_id).one_or_none()
            )
            necesita_detalle = con_detalle and (existing is None or not existing.descripcion)

            descripcion = ""
            if necesita_detalle:
                try:
                    descripcion = computrabajo.parsear_detalle(cliente.detalle(cruda.url))
                except Exception as error:  # noqa: BLE001
                    res.fallidas.append((cruda.external_id[:8], str(error)))

            fecha = fechas.parsear(cruda.fecha_texto, referencia)
            # Si no bajamos el detalle porque la oferta ya lo tenía guardado,
            # se detecta contra esa descripción. Si no, una corrida sin detalle
            # detectaría sólo contra el título y le recortaría a la oferta las
            # skills que encontró la corrida anterior.
            texto_detalle = descripcion or (existing.descripcion if existing else None)
            skills = cat.detectar(_texto_para_detectar(cruda, texto_detalle))

            offer = _a_offer(cruda, fecha, skills)
            offer.descripcion = descripcion or None

            resultado = _guardar(sesion, offer)
            if resultado == "nueva":
                res.nuevas += 1
            elif resultado == "actualizada":
                res.actualizadas += 1
            else:
                res.duplicadas += 1

        sesion.commit()
    finally:
        if propio:
            cliente.close()

    return res


def ingestar(
    sesion: Session,
    perfil_id: str | None = None,
    dias: int | None = None,
    con_detalle: bool = True,
    cliente: Cliente | None = None,
    ahora: datetime | None = None,
) -> Resumen:
    """Corre la ingesta.

    Sin `perfil_id`, recorre todos los perfiles **activos**: la decisión de la
    Etapa 0 fue que pueden convivir varios, y la ingesta tiene que verlos a todos.
    """
    if perfil_id:
        perfiles = [servicio.obtener(sesion, perfil_id)]
    else:
        perfiles = servicio.activos(sesion)

    propio = cliente is None
    cliente = cliente if cliente is not None else Cliente()

    resumen = Resumen()
    try:
        for perfil in perfiles:
            ingestar_perfil(
                sesion,
                perfil,
                cliente=cliente,
                dias=dias,
                con_detalle=con_detalle,
                resumen=resumen,
                ahora=ahora,
            )
    finally:
        if propio:
            cliente.close()

    return resumen
