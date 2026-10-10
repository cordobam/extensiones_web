# Radar Laboral — extensión de Firefox

La mitad navegador del proyecto. Se para sobre una oferta de Computrabajo o
ZonaJobs y hace tres cosas:

1. **Dice contra qué perfil postulás.** Si tenés varios perfiles cargados, el
   envío se registra contra el que elegiste y no contra el que salió al azar.
2. **Pega tu carta al lado del formulario**, con un botón para copiarla. El
   texto sale del campo `presentacion` del perfil. Todavía es texto escrito a
   mano: que la arme la IA es la fase 11, y este panel es el lugar donde va a
   caer.
3. **Registra la postulación** en el embudo cuando la mandaste. El botón lo
   apretás vos, después de enviarla: la extensión nunca manda nada sola.

## Lo que no hace

- **No prellena el formulario.** El autofill de Firefox ya cubre nombre, email
  y teléfono, y la app no guarda ningún archivo de CV que se pueda subir
  solo. Lo que quedaba era selectores por portal, que son lo único que se
  rompe con cada rediseño del sitio: no valía el mantenimiento.
- **No envía la postulación.** Mandar currículum es una acción con
  consentimiento e irreversible. La extensión deja todo a la vista y espera.
- **No funciona sin la app.** No hay caché ni volcado local: si `uvicorn` no
  está corriendo, el panel lo dice y ofrece reintentar.

## Cómo se carga

1. Levantá la app en `aplicacion_web/`:
   `..\.venv\Scripts\python.exe -m uvicorn radar.api:app --reload`.
2. Abrí `about:debugging` en Firefox → *Esta Firefox* → *Cargar complemento
   temporal…* → elegí `extension_web/manifest.json`.
3. Opciones (clic derecho en el ícono → *Opciones*, o
   `about:addons` → *Radar Laboral* → *Opciones*): revisá la URL de la app y
   el perfil. *Guardar y probar conexión* consulta la app y lista los
   perfiles.
4. Abrí una oferta desde la pantalla de ofertas de la app. Aparece el botón
   **Radar** abajo a la derecha.

Para empaquetarla como `.xpi` (opcional): `npx web-ext build` dentro de esta
carpeta.

## Los archivos

| Archivo | Qué hace |
|---|---|
| `manifest.json` | MV3, sólo Firefox. Define permisos, content scripts en los portales y la página de opciones. |
| `fondo.js` | El único que habla HTTP con la app: `GET /api/extension/datos` y `POST /api/extension/enviada`, con la cabecera `X-Radar-Extension: 1`. |
| `registro.js` | El registro de adaptadores por portal. Un adaptador = `detectar(location, document)` → id de la oferta o `null`. |
| `portales/…` | (ahora adentro de `registro.js`) un adaptador por portal: sólo saben sacar el id de la URL. |
| `panel.js` | El panel flotante: perfil, estado en el embudo, carta, registrar envío. |
| `panel.css` | Estilos del panel, todos con `#radar-raiz` por delante para que el CSS de la página no lo pise. |
| `opciones.html/js/css` | URL de la app, perfil, probar conexión. |

## Por qué el HTTP vive en `fondo.js`

La app no contesta CORS —eso es a propósito, es el candado que impide que
cualquier página le pegue un POST—. Un content script corre con las
restricciones CORS de la página en la que está, así que sus requests no
llegarían. El background sí tiene `host_permissions` para `127.0.0.1:8000`,
desde donde sí se puede. El panel sólo manda mensajes:
`{tipo: "datos"}` y `{tipo: "enviada", external_id}`.

## Cómo detecta la oferta

Por URL, nunca por selectores del formulario:

- **Computrabajo**: `?oi=` en la página de postulación
  (`candidato.ar.computrabajo.com/match/`), después el hex de 32 al final de
  la URL de detalle, y en última instancia el atributo `data-oi` de la
  página.
- **ZonaJobs**: el id numérico al final de `/empleos/…-{id}.html`.

El `external_id` que saca de ahí es el mismo que compara
`POST /api/extension/enviada` (la app le saca el prefijo de la fuente).

Para sumar otro portal: una entrada nueva en `registro.js` con su
`dominios` y su `detectar()`, y sus dominios en el `manifest.json`.

## Qué probar a mano

- [ ] Oferta de Computrabajo: desde el detalle (URL con el hex) y desde la
      página de postulación (`?oi=`).
- [ ] Oferta de ZonaJobs.
- [ ] Registrar el envío y verlo en la app, en *Postulaciones*, como
      `enviada` con fecha.
- [ ] Registrar dos veces: no debe romper nada ni volver atrás el estado.
- [ ] Una oferta que no está en el radar: tiene que decir que hay que correr
      `radar ingest`.
- [ ] Con la app caída: tiene que avisa y poder reintentar.
- [ ] Con varios perfiles: cambiar el perfil en Opciones y ver que el panel
      lo respeta.

## Qué falta

- **Fase 11**: que la IA redacte la carta contra cada oferta. El texto
  generado va a reemplazar al `presentacion` fijo dentro del panel.
- **Más portales**: los adaptadores se agregan en `registro.js` cuando
  haya fuentes nuevas en la app (indeed, etc.).
