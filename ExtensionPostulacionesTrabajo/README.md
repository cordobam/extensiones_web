# Radar Laboral

Radar de ofertas de empleo: scraper, matching por reglas y dashboard.

## Requisitos

- Python 3.11+ (probado en 3.14)
- PostgreSQL corriendo en `localhost:5432`

## Instalación

```bat
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev]"
copy .env.example .env
```

Editá `.env` con tus credenciales de PostgreSQL.

## Base de datos

```bat
alembic upgrade head
```

`scripts\init_db.py` sigue existiendo como atajo, pero el camino normal es
Alembic. Para tocar el esquema: `alembic revision --autogenerate -m "..."`.

## Levantar la app

```bat
uvicorn radar.api:app --reload
```

Queda en <http://127.0.0.1:8000>, y redirige a la gestión de perfiles.

## Perfiles

Un perfil describe **una búsqueda** (puestos y keywords) y **un CV** (quién sos,
con qué skills y qué experiencia). Sirven para correr el radar y, más adelante,
para que la extensión complete postulaciones.

### Dónde viven

- `perfiles/{id}.yml`: el artefacto que se versiona y se comparte. Lo escribe la
  app cada vez que guardás un perfil.
- PostgreSQL: la fuente de verdad en runtime.

Si borrás el YAML, el perfil sigue en la base. Si borrás el perfil desde la app,
se borran la fila y el archivo.

### Editar un YAML a mano

Se puede, pero la app lo va a avisar. Compara el `mtime` del archivo contra el
último guardado en la base (`perfiles.actualizado_en`, con 2s de tolerancia por la
precisión de timestamps en Windows). Si el archivo es más nuevo, aparece un
aviso y **no** se pisa el archivo hasta que confirmes.

### Cargar un ejemplo

```bat
copy perfiles\data-engineer.yml.example perfiles\data-engineer.yml
```

Después abrí `/perfiles`: aparece el botón **Importar desde perfiles/** porque hay
un YAML sin cargar. Es el camino inverso al guardado, y es el que usás cuando
alguien te comparte un `perfiles/*.yml`. Un YAML que no valida se saltea con el
motivo y no frena la importación de los demás.

Los `.example` van con comentarios: al guardar desde la UI se reescriben sin
ellos, y al importarlos el archivo queda tal cual.

## Catálogo de skills

`catalogo/skills.yml` es la lista canónica de skills: nombre único, categoría, y
los alias por los que esa skill aparece en las ofertas reales. Hoy tiene 219
skills en 12 categorías.

Está versionado en el repo a propósito. Es un dato de referencia, cambia poco, y
con git se ve quién agregó qué skill y cuándo. La alternativa era una tabla
`skills` + `skill_aliases`, que sólo va a hacer falta el día que quieras
estadísticas del tipo "estas 3 skills son las que más filtran ofertas".

```python
from radar.catalogo import catalogo

cat = catalogo()                      # cacheado, no toca la base

cat.resolver("pyspark")               # -> "PySpark"
cat.resolver("sklearn")               # -> "scikit-learn"
cat.detectar("Airflow, dbt y Snowflake")
                                    # -> ["Apache Airflow", "dbt", "Snowflake"]
cat.sin_conocer(["Python", "Cobol"])  # -> ["Cobol"]
cat.normalizar_lista(["k8s"])         # -> {"k8s": "Kubernetes"}
```

Lo importante es `detectar`, que es lo que se usará contra el título y la
descripción de cada oferta. Devuelve las skills que aparecen en un texto, en
orden de aparición y sin repetir. Los límites de palabra evitan los falsos
positivos que después se convierten en puntajes raros sin explicación: "spark"
no matchea dentro de "PySpark", ni "js" dentro de "node.js".

### Agregar una skill

Al final de su categoría en el YAML. No hay que tocar código ni migración.

```yaml
  - nombre: Polars
    categoria: datos
    alias: [polars dataframe]
```

Dos reglas las valida el cargador al arrancar, y conviene respetarlas:

1. **Una forma no puede pertenecer a dos skills.** Si "spark" fuera alias de
   Apache Spark y de PySpark a la vez, el catálogo no carga. Es preferible fallar
   al arrancar y no devolver puntajes raros más adelante.
2. **La categoría tiene que estar declarada** en la lista `categorias` del mismo
   archivo.

Una limitación conocida: las claves de un solo carácter no se indexan, porque
matchearían medio idioma. `R` como lenguaje está cargado con alias de varias
letras ("r language", "lenguaje r") para poder detectarlo igual.

### Skills de soporte IT y atención al cliente

Además de las de datos, el catálogo tiene dos categorías más:

- **`soporte_it`** (55 skills): Mesa de Ayuda, Soporte Técnico, niveles 1/2/3,
  ITIL, Active Directory, Windows, Microsoft 365, Exchange, redes (TCP/IP, DNS,
  VLAN, Cisco, Fortinet), virtualización (VMware, Hyper-V), backup, y las
  herramientas de la mesa de ayuda (ServiceNow, GLPI, ManageEngine).
- **`atencion_cliente`** (26 skills): Atención al Cliente, Contact Center, manejo
  de reclamos, escalamiento, retención, cobranzas, los CRM (Salesforce,
  Dynamics, HubSpot), las plataformas (Zendesk, Genesys, Five9) y las métricas
  (SLA, NPS, CSAT).

**El soporte IT matchea mucho mejor que atención al cliente**, y no es un tema
de código: las ofertas de soporte nombran productos ("Active Directory, Windows
10, ServiceNow"), que es justo lo que `detectar` sabe encontrar. Las de atención
al cliente describen actividades y volúmenes ("30 llamados por hora"), así que
su puntaje va a depender mucho más de `busqueda.keywords` que del catálogo.

Hay skills que sirven para los dos rubros (Zendesk, Salesforce). Como
`categoria` es un solo valor, cada una quedó donde más se usa en el mercado
argentino, y el otro rubro las sigue detectando igual: la categoría sirve para
agrupar y filtrar, no para detectar.

`test_perfiles_catalogo.py` falla si un perfil declara una skill que el catálogo
no conoce. Es el test que mantiene sincronizadas las dos cosas: si el matching
no encuentra una skill del CV, el síntoma sería un puntaje inexplicablemente
bajo.

## Perfiles activos

Un perfil activo es uno que el radar rastrea. **Pueden convivir varios**, y eso
se decidió cuando se cargaron los perfiles de soporte IT y atención al cliente:
antes `activar` desactivaba todos los demás, así que con cuatro perfiles la
ingesta sólo podía ver uno y había que estar alternando a mano.

En `/perfiles`, cada perfil tiene su botón Activar o Desactivar, y arriba dice
cuántos hay activos. Desactivar es idempotente, así que un doble clic no rompe.

`radar/perfiles/servicio.py` expone `activos(sesion)`, que devuelve los perfiles
activos ordenados: es lo que va a consumir la ingesta.

Los perfiles `.example` van con datos de ejemplo a propósito, porque están
versionados en un repo público. `test_los_perfiles_ejemplo_no_traen_datos_
personales` falla si alguien deja un nombre, un teléfono o un email reales.

## Traer ofertas de Computrabajo

`radar/fuentes/` es la capa que sabe de dónde vienen las ofertas. Hoy tiene una
sola fuente: `ar.computrabajo.com`. Todo lo de más abajo (fecha, matching, base)
recibe objetos `Offer` y no sabe de dónde salieron, así que agregar otro portal
es escribir otra implementación, no tocar el resto.

Corrida de humo (pega contra el portal real):

```bat
python scripts/ingesta_humo.py --listados            : rápido, sin detalles
python scripts/ingesta_humo.py --dias 3              : con detalle, ~20s
python scripts/ingesta_humo.py --perfil soporte-it
python scripts/ingesta_humo.py --limpiar              : vacía la tabla ofertas
```

Corre la ingesta dos veces seguidas y reporta los números de cada una. Sin
`--perfil` recorre todos los perfiles **activos**.

### Tres cosas del portal que no salen de la documentación

Se verificaron contra el sitio, y contradicen lo que se suponía:

- **No hace falta autenticación.** El listado y el detalle responden sin cookies.
- **La modalidad no es una clase `tag`.** Es un ícono: `icon i_home` para
  "Remoto" y `icon i_home_office` para "Presencial y remoto". El texto está en
  el `<span>` padre, porque el ícono va vacío. Buscar la palabra "Remoto" en el
  ícono no devuelve nada y se pierden 2 de cada 20 ofertas.
- **El `data-id` viene con comillas simples** (`data-id='ABC...'`), no dobles.
  Un `re.findall` escrito a mano con comillas dobles no encuentra nada.

El `robots.txt` prohíbe los filtros por query (`?pubdate=`, `?sal=`, `?by=`), así
que el filtro de fechas va del lado nuestro. Por eso `request_delay` (2 s por
petición) no es un detalle: es la forma de ser respetuoso.

### Las fechas del listado son relativas

El portal escribe "Hace 19 horas", "Ayer", "Hace 2 días". `radar/fuentes/fechas.py`
los convierte a datetime UTC. **Si el texto no se reconoce devuelve `None`, y la
oferta se descarta**: preferimos perder una oferta a mostrar una vieja como si
fuera nueva. "Más de 30 días" devuelve 31 días, porque la oferta tiene 30 o
más y asumir exactamente 30 la haría entrar en una ventana de 30 siendo vieja.

### La ingesta es barata de repetir

Tres decisiones hacen que una segunda corrida cueste 2 s en vez de 22:

1. **El filtro de fecha corre antes de bajar los detalles.** El listado trae la
   fecha, así que se descartan las viejas sin pedir 20 páginas que no se van a
   usar. Con ventana de 3 días: 24 vistas, 10 guardadas, 14 descartadas.
2. **Corta cuando una página no trae nada nuevo.** El listado viene ordenado por
   fecha, así que una página entera de viejas es la última.
3. **Upsert por `external_id`.** Si la oferta ya está y tiene descripción, no se
   vuelve a bajar su detalle.

Las fechas se guardan truncadas al minuto. Sin eso, "Hace 19 horas" se calcula
contra el reloj de cada corrida y da un valor distinto cada vez, así que el
upsert cree que todo cambió y reescribe veinte filas por pasada.

## Estado

Fases 1 a 7 completas: andamiaje, modelo de datos con Alembic, perfiles con UI
(alta, edición, activación múltiple, borrado) escribiendo YAML, catálogo de
219 skills en 12 categorías, y la ingesta de Computrabajo con normalizador y
filtro temporal. 276 tests.

Próximas: matching por reglas, CLI de ingesta y dashboard.

## Estructura

```
radar/
  api.py         rutas FastAPI y templates Jinja
  catalogo.py    catálogo de skills: índice de alias y detección en texto
  config.py      configuración por variables de entorno
  db.py          engine y sesión de SQLAlchemy
  models.py      Oferta, OfertaSkill, Perfil, Match, Postulacion
  schema.py      contrato Offer de la capa de fuentes
  ingesta.py     perfil -> páginas del portal -> tabla ofertas, con upsert
  perfiles/
    validacion.py      esquemas pydantic del perfil
    serializacion.py   YAML <-> dict y aviso de edición manual
    formulario.py      parseo del formulario (filas repetibles)
    servicio.py        CRUD sobre PostgreSQL
  fuentes/
    computrabajo.py  cliente HTTP con pausa, y el parser del listado y el detalle
    fechas.py        "Hace 19 horas" -> datetime UTC
catalogo/        skills.yml, la lista canónica de skills (versionada)
templates/       Jinja2 (base + gestión de perfiles)
static/          CSS y JS sin build step
perfiles/        YAML de perfiles (el .example va versionado, el .yml no)
migrations/      Alembic
scripts/
  init_db.py       creación del esquema (atajo, preferí Alembic)
  ingesta_humo.py  corrida de la ingesta contra el portal real
tests/
  fixtures/computrabajo/  HTML real del portal, recortado (sin style ni script)
```

## Tests

```bat
pytest
```

Los tests de perfiles tocan la base real (`radar_laboral`) y usan directorios
temporarios para los YAML, así que no tocan `perfiles/`. `tests/test_db.py`
limpia sus filas al terminar.

**Ningún test toca la red.** Los de Computrabajo parsean el HTML real guardado en
`tests/fixtures/computrabajo/`, y los de ingesta usan un `httpx.MockTransport`.
Para probar contra el portal está `scripts/ingesta_humo.py`.