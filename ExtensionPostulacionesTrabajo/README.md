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

## Estado

Fases 1 a 3 completas: andamiaje, modelo de datos con Alembic, y perfiles con
UI (alta, edición, activación, borrado) escribiendo YAML.

Próximas: catálogo de skills, fuente de Computrabajo, normalizador, filtro
temporal, matching, CLI de ingesta y dashboard.

## Estructura

```
radar/
  api.py         rutas FastAPI y templates Jinja
  config.py      configuración por variables de entorno
  db.py          engine y sesión de SQLAlchemy
  models.py      Oferta, OfertaSkill, Perfil, Match, Postulacion
  schema.py      contrato Offer de la capa de fuentes
  perfiles/
    validacion.py      esquemas pydantic del perfil
    serializacion.py   YAML <-> dict y aviso de edición manual
    formulario.py      parseo del formulario (filas repetibles)
    servicio.py        CRUD sobre PostgreSQL
templates/       Jinja2 (base + gestión de perfiles)
static/          CSS y JS sin build step
perfiles/        YAML de perfiles (el .example va versionado, el .yml también)
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