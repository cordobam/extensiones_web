# PostulacionesTrabajo

Un lugar para llevar el seguimiento de las postulaciones a ofertas de empleo, y
una extensión de Firefox para no volver a cargar los mismos datos en cada
formulario.

Son dos mitades y viven separadas a propósito:

```
aplicacion_web/    la app Python: ingesta, matching, perfiles y el embudo
extension_web/     la extensión de Firefox: prellena los formularios
ROADMAP.txt        el plan de las fases que faltan
```

## Qué hay de cada lado

| | `aplicacion_web/` | `extension_web/` |
|---|---|---|
| Qué es | FastAPI + scraping + PostgreSQL | extensión MV3 de Firefox |
| Se corre con | `uvicorn radar.api:app` | se carga en `about:debugging` |
| Docs | su [`README.md`](aplicacion_web/README.md) | (se escribe con la Fase 13) |

El plan de las fases está en [`ROADMAP.txt`](ROADMAP.txt). Las fases 1 a 12 están
terminadas; la 13 es la extensión, que es justamente lo que va en
`extension_web/`.

## Cómo se levanta hoy

El entorno y la base son de la app, así que hay que estar en `aplicacion_web/`.
El `.venv` quedó en la raíz del producto porque los entornos virtuales de
Windows no se pueden reubicar.

```bat
cd ExtensionPostulacionesTrabajo\aplicacion_web
..\.venv\Scripts\python.exe -m uvicorn radar.api:app --reload
```

Queda en <http://127.0.0.1:8000>.

## Por qué están en carpetas separadas

La app y la extensión se mueven a ritmos distintos y con ferramentas distintas:
la app es Python con tests y migraciones, la extensión es JavaScript que se
carga en el navegador. Meterlas en el mismo nivel hacía que los archivos de una
confundieran a los de la otra y no quedara claro qué se despliega junto con qué.

La carpeta donde vive cada una también es la que se publica. La extensión se
empaqueta sola como `.xpi` sin arrastrar la app, y la app se despliega sin
arrastrar la extensión.

## Datos reales

Dos archivos con tus datos no se versionan, y hay dos `.gitignore` porque cada
uno protege lo que tiene debajo:

- `aplicacion_web/.env` — la URL de la base.
- `aplicacion_web/perfiles/*.yml` — nombre, email, teléfono y experiencia.
- `extension_web/datos.json` — el volcado del CV para la extensión.

Los patrones de un `.gitignore` se resuelven relativos a la carpeta donde está
el archivo. Por eso `perfiles/*.yml` está en el de `aplicacion_web/`: puesto en
la raíz no protegería `aplicacion_web/perfiles/`.