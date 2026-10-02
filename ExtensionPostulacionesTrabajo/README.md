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

## Estado

Fases 1 a 4 completas: andamiaje, modelo de datos con Alembic, perfiles con UI
(alta, edición, activación múltiple, borrado) escribiendo YAML, y catálogo de
219 skills en 12 categorías.

Próximas: fuente de Computrabajo, normalizador, filtro temporal, matching, CLI de
ingesta y dashboard.

## Estructura

```
radar/
  api.py         rutas FastAPI y templates Jinja
  catalogo.py    catálogo de skills: índice de alias y detección en texto
  config.py      configuración por variables de entorno
  db.py          engine y sesión de SQLAlchemy
  models.py      Oferta, OfertaSkill, Perfil, Match, Postulacion
  schema.py      contrato Offer de la capa de fuentes
  perfiles/
    validacion.py      esquemas pydantic del perfil
    serializacion.py   YAML <-> dict y aviso de edición manual
    formulario.py      parseo del formulario (filas repetibles)
    servicio.py        CRUD sobre PostgreSQL
catalogo/        skills.yml, la lista canónica de skills (versionada)
templates/       Jinja2 (base + gestión de perfiles)
static/          CSS y JS sin build step
perfiles/        YAML de perfiles (el .example va versionado, el .yml no)
migrations/      Alembic
scripts/
  init_db.py    creación del esquema (atajo, preferí Alembic)
tests/
```

## Tests

```bat
pytest
```

Los tests de perfiles tocan la base real (`radar_laboral`) y usan directorios
temporarios para los YAML, así que no tocan `perfiles/`. `tests/test_db.py`
limpia sus filas al terminar.