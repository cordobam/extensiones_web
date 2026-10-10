// El panel flotante sobre el portal.
//
// Tres cosas y nada más: muestra contra qué perfil postulás, pega tu carta
// con un botón para copiarla, y registra el envío cuando lo mandaste. Nunca
// envía nada por tu cuenta: mandar currículum es una acción con
// consentimiento, así que el botón lo apretás vos después de enviarla.
//
// El id de la oferta lo saca el adaptador del portal (registro.js) desde la
// URL, nunca desde los selectores del formulario.

(function () {
  const portal =
    globalThis.RADAR_PORTAL && globalThis.RADAR_PORTAL(location.hostname);
  if (!portal) return;

  let externalId = portal.detectar(location, document);
  let datos = null;
  let mensaje = null;
  let baseApp = "http://127.0.0.1:8000";

  function nodo(tag, clase, texto) {
    const elemento = document.createElement(tag);
    if (clase) elemento.className = clase;
    if (texto !== undefined && texto !== null) elemento.textContent = texto;
    return elemento;
  }

  function fechaCorta(iso) {
    if (!iso) return "";
    try {
      return new Date(iso).toLocaleString("es-AR", {
        dateStyle: "short",
        timeStyle: "short",
      });
    } catch (error) {
      return iso;
    }
  }

  // ------------------------------------------------------------- estructura

  const raiz = nodo("div");
  raiz.id = "radar-raiz";

  const boton = nodo("button", "radar-boton", "Radar");
  boton.type = "button";

  const tarjeta = nodo("div", "radar-tarjeta");
  tarjeta.hidden = true;

  const cabecera = nodo("div", "radar-cabecera");
  const titulo = nodo("span", "radar-titulo", "Radar Laboral");
  const portalChico = nodo("span", "radar-portal", portal.nombre);
  const cerrar = nodo("button", "radar-cerrar", "×");
  cerrar.type = "button";
  cerrar.title = "Cerrar";
  cabecera.append(titulo, portalChico, cerrar);

  const cuerpo = nodo("div", "radar-cuerpo");

  const enlaceApp = nodo("a", "radar-app", "Ver en la app");
  enlaceApp.target = "_blank";
  enlaceApp.rel = "noopener noreferrer";

  tarjeta.append(cabecera, cuerpo, enlaceApp);
  raiz.append(boton, tarjeta);
  document.documentElement.appendChild(raiz);
  raiz.hidden = !externalId;

  browser.storage.local.get(["base"]).then((guardado) => {
    if (guardado.base) baseApp = guardado.base.replace(/\/+$/, "");
    enlaceApp.href = baseApp + "/ofertas";
  });

  // ------------------------------------------------------------------ pintar

  function pintarCargando() {
    cuerpo.replaceChildren(nodo("p", "radar-nota", "Consultando la app…"));
  }

  function pintarFalla(falla) {
    const texto =
      (falla && falla.mensaje) || "No pude hablar con Radar Laboral.";
    const reintentar = nodo("button", "radar-accion", "Reintentar");
    reintentar.type = "button";
    reintentar.addEventListener("click", refrescar);
    cuerpo.replaceChildren(nodo("div", "radar-error", texto), reintentar);
  }

  function postulacionActual() {
    if (!datos || !datos.postulaciones) return null;
    // El id de la URL puede venir en otra caja que el guardado (el hex de
    // Computrabajo), y una diferencia de caja dejaría el panel pintando
    // "Sin registrar" sobre una postulación que ya existe.
    const propio = String(externalId).toUpperCase();
    return (
      datos.postulaciones.find(
        (fila) => String(fila.external_id).toUpperCase() === propio
      ) || null
    );
  }

  function pintarDatos() {
    const fragmento = document.createDocumentFragment();

    const perfil = nodo("div", "radar-dato");
    perfil.append(
      nodo("span", "radar-clave", "Perfil"),
      nodo(
        "span",
        "radar-valor",
        datos.nombre + (datos.activo ? "" : " (inactivo)")
      )
    );
    fragmento.appendChild(perfil);

    fragmento.appendChild(nodo("span", "radar-seccion", "Embudo"));
    const fila = postulacionActual();
    if (fila) {
      const estado = nodo("p", "radar-estado");
      estado.append(nodo("strong", null, fila.etiqueta));
      const cuando = fechaCorta(fila.fecha_postulacion);
      if (cuando) estado.append(document.createTextNode(" · " + cuando));
      fragmento.appendChild(estado);
    } else {
      fragmento.appendChild(nodo("p", "radar-nota", "Sin registrar en el embudo."));
    }

    fragmento.appendChild(nodo("span", "radar-seccion", "Carta"));
    const carta = datos.cv && datos.cv.presentacion;
    if (carta) {
      const caja = nodo("div", "radar-carta");
      caja.appendChild(nodo("p", "radar-carta-texto", carta));
      const copiar = nodo("button", "radar-accion", "Copiar");
      copiar.type = "button";
      copiar.addEventListener("click", () => copiarCarta(carta, copiar));
      caja.appendChild(copiar);
      fragmento.appendChild(caja);
    } else {
      fragmento.appendChild(
        nodo("p", "radar-nota", "Sin carta cargada en el perfil.")
      );
    }

    const registrar = nodo("button", "radar-accion radar-principal", "Registrar envío");
    registrar.type = "button";
    registrar.addEventListener("click", () => registrarEnvio(registrar));
    fragmento.appendChild(registrar);

    if (mensaje) {
      fragmento.appendChild(
        nodo("div", mensaje.tipo === "ok" ? "radar-nota" : "radar-error", mensaje.texto)
      );
    }

    cuerpo.replaceChildren(fragmento);
  }

  function pintar() {
    if (!externalId) return;
    if (datos === "cargando") return pintarCargando();
    if (!datos || !datos.ok) return pintarFalla(datos);
    pintarDatos();
  }

  // ------------------------------------------------------------------ acciones

  async function refrescar() {
    mensaje = null;
    datos = "cargando";
    pintarCargando();
    datos = await browser.runtime.sendMessage({ tipo: "datos" });
    pintar();
  }

  async function registrarEnvio(botonAccion) {
    botonAccion.disabled = true;
    botonAccion.textContent = "Registrando…";

    const respuesta = await browser.runtime.sendMessage({
      tipo: "enviada",
      external_id: externalId,
    });

    if (!respuesta) {
      mensaje = { tipo: "error", texto: "La extensión no respondió." };
    } else if (respuesta.ok) {
      const previa = postulacionActual();
      if (previa) {
        previa.estado = respuesta.estado;
        previa.etiqueta = respuesta.etiqueta;
        previa.fecha_postulacion = respuesta.fecha_postulacion;
      } else {
        datos.postulaciones.push({
          external_id: externalId,
          id: respuesta.id,
          titulo: respuesta.titulo,
          estado: respuesta.estado,
          etiqueta: respuesta.etiqueta,
          fecha_postulacion: respuesta.fecha_postulacion,
        });
      }
      mensaje = { tipo: "ok", texto: respuesta.mensaje };
    } else {
      mensaje = {
        tipo: "error",
        texto: respuesta.mensaje || "No se pudo registrar el envío.",
      };
    }
    pintar();
  }

  async function copiarCarta(texto, botonAccion) {
    let bien = false;
    try {
      await navigator.clipboard.writeText(texto);
      bien = true;
    } catch (error) {
      const temporal = document.createElement("textarea");
      temporal.value = texto;
      temporal.style.position = "fixed";
      temporal.style.opacity = "0";
      raiz.appendChild(temporal);
      temporal.select();
      try {
        bien = document.execCommand("copy");
      } catch (error2) {
        bien = false;
      }
      temporal.remove();
    }
    botonAccion.textContent = bien ? "Copiada" : "No se pudo copiar";
    setTimeout(() => {
      botonAccion.textContent = "Copiar";
    }, 1500);
  }

  // ------------------------------------------------------------------ abrir

  function abrir() {
    tarjeta.hidden = false;
    boton.classList.add("radar-abierto");
    refrescar();
  }

  function cerrarTarjeta() {
    tarjeta.hidden = true;
    boton.classList.remove("radar-abierto");
  }

  boton.addEventListener("click", () => {
    if (tarjeta.hidden) abrir();
    else cerrarTarjeta();
  });
  cerrar.addEventListener("click", cerrarTarjeta);

  // Las páginas de portal navegan sin recargar (o el ?oi= aparece tarde), así
  // que se revisa la URL cada tanto: si cambió el id, el panel se reajusta.
  setInterval(() => {
    const visto = portal.detectar(location, document);
    if (visto === externalId) return;
    externalId = visto;
    raiz.hidden = !externalId;
    if (!externalId) cerrarTarjeta();
    else if (!tarjeta.hidden) refrescar();
  }, 1500);
})();
