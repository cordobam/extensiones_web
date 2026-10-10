// El único lugar que habla HTTP con la app.
//
// Todo request vive acá y no en el panel: el background tiene
// `host_permissions` para 127.0.0.1 y los content scripts no. La app no
// contesta CORS --por eso la cabecera X-Radar-Extension es un candado--, así
// que desde el contexto de la página la petición no llegaría nunca.

const BASE_DEFECTO = "http://127.0.0.1:8000";
const CABECERA = "X-Radar-Extension";
const CLAVE = "1";

async function configuracion() {
  const guardado = await browser.storage.local.get(["base", "perfil"]);
  const base = (guardado.base || BASE_DEFECTO).replace(/\/+$/, "");
  return { base, perfil: guardado.perfil || "" };
}

function cabeceras(extra) {
  return Object.assign({ [CABECERA]: CLAVE }, extra || {});
}

async function leer(respuesta) {
  try {
    return await respuesta.json();
  } catch (error) {
    return {
      ok: false,
      error: "respuesta-invalida",
      mensaje: `La app contestó algo que no es JSON (HTTP ${respuesta.status}).`,
    };
  }
}

function caida(base) {
  return {
    ok: false,
    error: "app-caida",
    mensaje: `No puedo alcanzar la app en ${base}. ¿La dejaste corriendo con uvicorn?`,
  };
}

async function traerDatos() {
  const { base, perfil } = await configuracion();
  const url = new URL(base + "/api/extension/datos");
  if (perfil) url.searchParams.set("perfil", perfil);

  let respuesta;
  try {
    respuesta = await fetch(url.toString(), { headers: cabeceras() });
  } catch (error) {
    return caida(base);
  }
  return leer(respuesta);
}

async function enviar(external_id) {
  // El perfil del cuerpo no puede venir del storage: si está vacío significa
  // "primera activa", y la app es la que resuelve cuál es. El GET de datos ya
  // lo resolvió, así que se le pide devuelta la decisión.
  const datos = await traerDatos();
  if (!datos.ok) return datos;

  const { base } = await configuracion();
  let respuesta;
  try {
    respuesta = await fetch(base + "/api/extension/enviada", {
      method: "POST",
      headers: cabeceras({ "Content-Type": "application/json" }),
      body: JSON.stringify({ perfil: datos.perfil, external_id }),
    });
  } catch (error) {
    return caida(base);
  }
  return leer(respuesta);
}

browser.runtime.onMessage.addListener((mensaje) => {
  if (!mensaje || typeof mensaje.tipo !== "string") return undefined;
  if (mensaje.tipo === "datos") return traerDatos();
  if (mensaje.tipo === "enviada" && typeof mensaje.external_id === "string") {
    return enviar(mensaje.external_id);
  }
  return undefined;
});
