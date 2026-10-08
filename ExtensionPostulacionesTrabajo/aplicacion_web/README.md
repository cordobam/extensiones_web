# Radar Laboral

Radar de ofertas de empleo: scraper, matching por reglas y dashboard.

Es la mitad web de PostulacionesTrabajo. La otra es `extension_web/`, la
extensión de Firefox que usa esta app como fuente de datos. El panorama de las
dos está en el [`README.md`](../README.md) de la raíz.

## Requisitos

- Python 3.11+ (probado en 3.14)
- PostgreSQL corriendo en `localhost:5432`

## Instalación

Todo lo de acá se corre **desde esta carpeta**. Es lo único que cambió al
separar las dos mitades: `RAIZ` en `radar/config.py` y `radar/api.py` se
derivan del propio paquete, así que `templates/`, `static/`, `catalogo/` y
`perfiles/` se resuelven solos mientras el código esté acá adentro.

El entorno quedó en la raíz de PostulacionesTrabajo, un nivel arriba, y se
llama con `..\.venv` en vez de `.venv`. Es porque los entornos virtuales de
Windows no se pueden reubicar: si se moviera la carpeta, el intérprete apuntaría
a rutas que ya no existen y habría que reinstalar todo.

```bat
cd ExtensionPostulacionesTrabajo\aplicacion_web
..\.venv\Scripts\activate
pip install -e ".[dev]"
copy .env.example .env
```

En un clon nuevo el `.venv` todavía no existe, así que primero hay que crearlo un
nivel arriba:

```bat
cd ExtensionPostulacionesTrabajo
python -m venv .venv
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

Queda en <http://127.0.0.1:8000>, y redirige a la lista de ofertas.

## Ver las ofertas

La pantalla es `/ofertas`, que es la razón de ser de la app. Muestra las
ofertas del perfil que elijas, ordenadas por puntaje de la más relevante a la
menos, y cada una con el "por qué" desplegable: qué obligatorias coinciden,
cuáles te faltan, cuántas deseables hay, y por qué fue descartada.

Tres cosas que conviene saber antes de mirar:

1. **Traer ofertas y puntuarlas son dos pasos.** `radar ingest` llena la tabla
   `ofertas`; `radar match` escribe los puntajes en `matches`. La pantalla
   distingue los tres finales —no hay ofertas, hay ofertas pero no puntuadas, o
   no hay nada— porque después de correr el primero es normal abrir la pantalla
   y el mensaje tiene que decir qué falta correr.
2. **Las descartadas están ocultas.** Con el perfil bien configurado son la
   mayoría. La cabecera dice cuántas hay y el checkbox las trae.
3. **El perfil se elige arriba.** Con varios perfiles cargados, la misma oferta
   puntúa distinto según el CV, así que la pantalla siempre dice contra cuál
   comparó.

Filtros: nivel, texto libre sobre título y empresa, skill y provincia. El de
skill se arma con las skills que aparecen en las ofertas de ese perfil, no con
las 219 del catálogo: filtrar por una skill que ningún aviso menciona devuelve
una lista vacía sin explicación. El de provincia se arma igual, y con la misma
razón.

Las tres formas que tiene el portal de decir Buenos Aires --"Capital Federal",
"Buenos Aires" y "Buenos Aires-GBA"-- se agrupan en una sola opción, porque
juntas son el 66% de lo que trae el portal (164 de 248 ofertas): mostrarlas
separadas hacía que elegir "Buenos Aires" prometiese 16 ofertas y entregara 16.
La provincia no es una columna sino una expresión sobre `ubicacion`, que se
conserva crudo y se muestra en la tarjeta. Agregar una forma nueva al mapa es
editar un diccionario, no escribir una migración.

### Por qué `Match` tiene una columna `puntaje`

El puntaje estaba dentro del JSON `detalle` y las dos consultas que lo usan lo
saqueaban con un cast. Para la CLI está bien; para una lista paginada no,
porque ordenar por una expresión casteada no usa índice y obliga a ordenar
todas las filas en cada página. La migración `0002` lo saca a una columna con
índice, con backfill desde el JSON.

El backfill va antes del `NOT NULL` a propósito: si la columna naciera `NOT NULL`
con default 0 y hubiera ofertas ya puntuadas, la migración no se aplicaría. Y
peor que fallar: si aceptara el default, la lista arrancaría con todas las
ofertas en puntaje 0, que es justo el orden que no se debe ver.

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

## Traer ofertas de los portales

`radar/fuentes/` es la capa que sabe de dónde vienen las ofertas. Hoy tiene dos:

| Fuente | De dónde sale | Nota |
| --- | --- | --- |
| `computrabajo` | HTML de `ar.computrabajo.com` | fechas relativas ("Hace 19 horas") |
| `zonajobs` | JSON de una API interna | fechas absolutas, descripción en el listado |

Las dos cumplen el mismo contrato (`radar/fuentes/base.py`: `listar`, `describir`,
`fecha`, `close`) y la ingesta habla con ese contrato, no con un portal. Todo lo
de más abajo (fecha, matching, base) recibe objetos `Offer` y no sabe de dónde
salieron, así que agregar un portal tercero es escribir otra implementación y
registrarla en `radar/fuentes/__init__.py`.

Cuáles corren en cada corrida lo decide `FUENTES_ACTIVAS` del `.env`, en orden.
Para una corrida puntual (probar un portal, evitar uno caído) está el flag, que
no toca la config:

```bat
radar ingest --fuente zonajobs
radar ingest --fuente zonajobs --fuente computrabajo --listados
```

Cada oferta guarda su `fuente` y el `external_id` va prefijado con ella
(`computrabajo:ABC123`). El prefijo hace que la unicidad de `external_id` sea
global y no por portal --dos portales pueden mandar el mismo id-- y de paso la
fila dice de dónde es. La lista del dashboard lo muestra como badge. La
migración `0005` le puso el prefijo a las filas viejas, y `api.py` acepta el id
crudo que manda la extensión (lo lee de la URL del portal).

Corrida de humo (pega contra el portal real):

```bat
python scripts/ingesta_humo.py --listados            : rápido, sin detalles
python scripts/ingesta_humo.py --dias 3              : con detalle, ~20s
python scripts/ingesta_humo.py --fuente zonajobs     : sólo ese portal
python scripts/ingesta_humo.py --perfil soporte-it
python scripts/ingesta_humo.py --limpiar              : vacía la tabla ofertas
```

Corre la ingesta dos veces seguidas y reporta los números de cada una. Sin
`--perfil` recorre todos los perfiles **activos**, y sin `--fuente` todas las
fuentes activas.

### Computrabajo: tres cosas del portal que no salen de la documentación

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

### ZonaJobs: sin HTML, con sesión

ZonaJobs no deja leer su HTML (la lista es una SPA), pero expone un JSON de
una API interna. Lo que hizo falta averiguar, verificado contra el sitio:

- **El endpoint es `POST /api/avisos/searchV2?...&page=N&sort=RECIENTES`**, con
  el `page` desde 0 y `sort=RECIENTES` porque el `robots.txt` prohíbe
  `/*recientes=true`, que es el de las páginas HTML.
- **Hay que mandar `x-site-id: ZJAR` en todos los requests**: es el único
  header que el backend exige y sin él contesta 400 (`"No se incluyo el header
  x-site-id"`), aunque venga un `x-session-jwt` válido (30 días, que devuelve
  la respuesta y se reusa) en la mano. El `x-pre-session-token` (uuid v4) va
  mientras no haya jwt. Y los headers tienen que parecer de un navegador: sin
  UA de Chrome o sin `Referer`, Cloudflare devuelve 403 antes de que el backend
  mire algo.
- **Trae todo en el listado:** descripción entera, empresa, ubicación, modalidad
  y fecha absoluta (`06-10-2026 19:00:08`, hora argentina = UTC-3 siempre, sin
  base de tzdata). No hace falta bajar el detalle oferta por oferta, y no trae
  salario: sólo un flag de si es obligatorio mostrarlo, así que el campo queda
  en `None`.

El `request_delay` aplica igual: 2 s por request, también contra este portal.

### Las fechas del listado son relativas

El portal (Computrabajo) escribe "Hace 19 horas", "Ayer", "Hace 2 días". `radar/fuentes/fechas.py`
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

Las skills se buscan en el título y en la descripción, no sólo en la
descripción. Y al reescribir una oferta se hace un diff de las skills, no se
reemplaza la lista entera: reemplazar la colección le dice a SQLAlchemy que
borre las filas viejas e inserte las nuevas, y en un mismo flush el INSERT puede
ir antes que el DELETE. Ahí salta un `UniqueViolation` sobre `uq_oferta_skill`
justo en la segunda corrida de las ofertas cuya fecha se movió.

## Puntuar ofertas

```bat
radar match --perfil soporte-it --top 10
```

`radar match` no trae ofertas: sólo puntúa las que ya están en la base, así que
corre `radar ingest` primero. Corre sobre todos los perfiles activos salvo que
pases `--perfil`.

Cada oferta recibe un nivel y un puntaje de 0 a 100: 75% de las skills
obligatorias y 25% de las deseables. Las dos mitades valen lo mismo para el
cálculo, pero las obligatorias pesan más en la decisión porque si falta una, la
oferta se descarta.

| Nivel | Cuándo |
| --- | --- |
| `alta` | Todas las obligatorias y la mitad o más de las deseables |
| `media` | Todas las obligatorias y al menos una deseable |
| `baja` | Todas las obligatorias y ninguna deseable |
| `descartada` | Falta al menos una obligatoria, o coincide con `busqueda.excluir` |

Una `descartada` no baja de 49 puntos, para que ordenar por puntaje nunca ponga
arriba algo que el filtro dejó abajo.

### Calibrá las obligatorias contra ofertas reales

Acá está el número que más costó aprender: **el filtro es tan fuerte como la
obligatoria más restrictiva, y nadie la calibró contra el mercado.**

Con el perfil de `soporte-it` de ejemplo en su primera versión, con `Mesa de
Ayuda`, `Soporte Técnico` y `Windows` como obligatorias, sobre 252 avisos reales:

| Obligatoria | Avisos que la nombran |
| --- | --- |
| `Soporte Técnico` | 60 |
| `Windows` | 30 |
| `Mesa de Ayuda` | 15 |

Y las ocho deseables (`Active Directory`, `ITIL`, `ServiceNow`, `Zendesk`,
`TCP/IP`...) aparecían entre 5 y 13 avisos cada una. Resultado: **246 de 252
ofertas `descartada` y ni una sola `alta`**. Con `Mesa de Ayuda` como palabra de
búsqueda y sólo 15 avisos nombrándola, el portal no usa las palabras que uno
supone.

Bajando a una sola obligatoria y cinco deseables que sí se repiten
(`Atención al Cliente`, `Troubleshooting`, `Gestión de Tickets`, `Hardware`,
`Comunicación`) la misma corrida da 12 `alta`, 36 `media`, 12 `baja` y 192
`descartada`: 60 ofertas para mirar en vez de 6.

El `.example` de `soporte-it` quedó corregido y lleva los números anotados, para
que se entienda el método y no sólo el resultado.

## Postulaciones

El botón "Postular" de cada fila de `/ofertas` marca esa oferta como
postulándose. Queda en **Pendiente**, que es una intención, no un envío: la
fecha de envío se guarda cuando vos movés la postulación a **Enviada**.

Los estados del embudo son `pendiente`, `enviada`, `entrevista`,
`resultado_aceptada`, `resultado_rechazada` y `archivada`. Viven en
`radar/postulaciones/estados.py` y las transiciones permitidas también, así que
el `<select>` y el servicio no se pueden desincronizar.

El embudo no es una cadena. `enviada` va directo a `resultado_rechazada` porque
te rechazan sin entrevista, y con una cadena lineal tendrías que fingir una
entrevista que no pasó para poder registrar el rechazo.

Dos fechas y por qué: `creado_en` es cuándo la marcaste y `fecha_postulacion`
cuándo la mandaste. Una postulación con la segunda vacía está esperando que la
mandes. Con una sola fecha no se distinguirían.

Las aceptadas y las rechazadas son estados distintos a propósito: juntas no
alcanzan para decir tu tasa de acierto, que es el único número para el que vale
la pena llevar el embudo.

"Desmarcar" borra la postulación. No es lo mismo que archivar: archivar dice
"la seguí y se terminó", desmarcar dice "esto nunca fue una postulación".

## La API que usa la extensión

Dos rutas JSON para la extensión de Firefox de `../extension_web/`. No son
públicas: son el cable entre las dos mitades del proyecto.

```
GET  /api/extension/datos?perfil=<id>
POST /api/extension/enviada        {"perfil": "...", "external_id": "..."}
```

`/api/extension/datos` devuelve el CV y las postulaciones vivas del perfil, todo
junto a propósito. La extensión necesita las dos cosas para funcionar, y si
fueran dos requests podría terminar rellenando un formulario con los datos de
una versión y el embudo de otra. `perfil` es opcional: sin él va el primer
perfil activo, igual que en `/ofertas`.

`/api/extension/enviada` reporta que mandaste la postulación. La extensión
manda el `external_id` de la oferta, que es lo que viaja en la URL del
formulario del portal (`?oi=<external_id>`), así que no depende de ids internos
de la base. Como desde la migración `0005` lo guardado lleva prefijo
(`computrabajo:ABC123`), `/datos` devuelve el id pelado --es con el que la
extensión compara el `?oi=`-- y `/enviada` acepta las dos formas.

### El candado de la cabecera

Las dos rutas exigen la cabecera `X-Radar-Extension: 1`:

```
X-Radar-Extension: 1
```

No es autenticación: cualquiera que pueda mandarla puede leer el CV. Es un
candado contra una amenaza concreta. La app escucha en `127.0.0.1:8000`, así que
cualquier página que abras en el navegador puede intentar pegarle un POST. Una
cabecera personalizada obliga al navegador a hacer un preflight, y como la app
no contesta con `Access-Control-Allow-*`, ese preflight falla y la petición
nunca sale. La extensión sí la manda, porque tiene `host_permissions` para el
localhost.

Para una herramienta personal con los datos en tu disco alcanza. Si algún día
corre en una red, hay que reemplazarlo por algo de verdad.

### Un detalle de las fechas

Todas las fechas del JSON van en UTC y con offset. Sin normalizar, el mismo
envío puede volver como `2026-10-05T01:46:57-03:00` en una respuesta y
`2026-10-05T04:46:57+00:00` en la siguiente, según si el valor salió de
PostgreSQL --que lo devuelve en la zona de la conexión-- o quedó en memoria
después del `INSERT`. Es el mismo instante, pero un texto que cambia de forma no
se puede comparar.

### Reportar un envío nunca es un error

Si la postulación ya estaba más avanzada, la ruta responde 200 con el estado
real y un mensaje que lo dice, en vez de un 4xx. El reporte llegó tarde, no
está mal: bajarla a `enviada` perdería una entrevista que ya pasó. El 4xx queda
para lo que sí es un error: que la oferta no esté en el radar o que el perfil no
exista.

## Estado

Fases 1 a 12 y la 16 completas: andamiaje, modelo de datos con Alembic,
perfiles con UI (alta, edición, activación múltiple, borrado) escribiendo YAML,
catálogo de 219 skills en 12 categorías, ingesta multi-fuente con registro de
fuentes (Computrabajo y ZonaJobs), normalizador y filtro temporal, matching por
reglas, CLI, el dashboard de ofertas con el badge de fuente, el embudo de
postulaciones y la API que consume la extensión. 535 tests.

Próximas: la extensión web (13), n8n (15), el RAG (14) y el resto de los
portales.

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
  matching.py    perfil + oferta -> nivel y puntaje, con upsert en matches
  ofertas.py     consulta del dashboard: filtros, orden y paginación
  postulaciones/ el embudo: estados válidos y transiciones, y el CRUD
  ubicaciones.py provincia canónica de una ubicación, en Python y en SQL
  cli.py         comandos ingest y match
  __main__.py    python -m radar
  perfiles/
    validacion.py      esquemas pydantic del perfil
    serializacion.py   YAML <-> dict y aviso de edición manual
    formulario.py      parseo del formulario (filas repetibles)
    servicio.py        CRUD sobre PostgreSQL
  fuentes/
    __init__.py      el registro: qué fuentes hay y cómo se crean
    base.py          el contrato Fuente y la fila OfertaCruda
    computrabajo.py  cliente HTTP con pausa, y el parser del listado y el detalle
    zonajobs.py      cliente con sesión (jwt), y el parser del JSON del listado
    fechas.py        "Hace 19 horas" -> datetime UTC
catalogo/        skills.yml, la lista canónica de skills (versionada)
templates/       Jinja2 (base + perfiles + ofertas)
static/          CSS y JS sin build step (perfiles.js y ofertas.js)
perfiles/        YAML de perfiles (el .example va versionado, el .yml no)
migrations/      Alembic
scripts/
  init_db.py       creación del esquema (atajo, preferí Alembic)
  ingesta_humo.py  corrida de la ingesta contra el portal real
tests/
  conftest.py           apunta los tests a `radar_laboral_test`
  fixtures/computrabajo/  HTML real del portal, recortado (sin style ni script)
  fixtures/zonajobs/      JSON real del API, con la descripción recortada
  test_fuentes.py         el registro de fuentes
  test_zonajobs.py        el scraper de ZonaJobs contra su JSON
```

## Tests

```bat
pytest
```

Los tests corren contra una base aparte, `radar_laboral_test`, no contra la del
`.env`. `tests/conftest.py` la deriva cambiando el nombre de la URL, así que no
es una configuración que se pueda olvidar: ni siquiera si `DATABASE_URL` apunta a
la base equivocada en la shell. La crea si no existe y la sube con
`alembic upgrade head` en vez de `create_all`, que además sirve de prueba de que
las migraciones de verdad funcionan sobre una base vacía. Esto importa porque
los fixtures de ofertas, matches, perfiles y postulaciones borran sus filas
antes y después de cada test; sin esto, cada `pytest` vaciaba la ingesta entera.

Los tests de perfiles usan directorios temporales para los YAML, así que no tocan
`perfiles/`.

**Ningún test toca la red.** Los de Computrabajo parsean el HTML real guardado en
`tests/fixtures/computrabajo/`, los de ZonaJobs el JSON real de
`tests/fixtures/zonajobs/`, y los de ingesta usan un `httpx.MockTransport`.
Para probar contra el portal está `scripts/ingesta_humo.py`.