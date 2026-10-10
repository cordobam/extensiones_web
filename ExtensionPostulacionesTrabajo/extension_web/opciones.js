const BASE_DEFECTO = "http://127.0.0.1:8000";

const campoBase = document.getElementById("base");
const campoPerfil = document.getElementById("perfil");
const botonGuardar = document.getElementById("guardar");
const botonConsultar = document.getElementById("consultar");
const aviso = document.getElementById("estado");

function baseLimpia() {
  return (campoBase.value || BASE_DEFECTO).replace(/\/+$/, "");
}

function mostrar(texto, clase) {
  aviso.textContent = texto;
  aviso.className = "estado" + (clase ? " " + clase : "");
  aviso.hidden = false;
}

async function guardarEnStorage() {
  await browser.storage.local.set({
    base: baseLimpia(),
    perfil: campoPerfil.value || "",
  });
}

function pintarPerfiles(datos, perfilGuardado) {
  const opciones = [new Option("Primera activa (por defecto)", "")];
  for (const perfil of datos.perfiles) {
    const sufijo = perfil.activo ? "" : " (inactivo)";
    opciones.push(new Option(`${perfil.nombre}${sufijo} — ${perfil.id}`, perfil.id));
  }

  const ids = datos.perfiles.map((perfil) => perfil.id);
  if (perfilGuardado && !ids.includes(perfilGuardado)) {
    // El perfil elegido se borró en la app: se muestra igual, para que no se
    // cambie en silencio el perfil elegido por "primera activa".
    opciones.push(new Option(`${perfilGuardado} (ya no existe)`, perfilGuardado));
  }

  campoPerfil.replaceChildren(...opciones);
  campoPerfil.value = perfilGuardado || "";
  campoPerfil.disabled = false;
}

async function consultar() {
  mostrar("Consultando la app…", "");
  botonConsultar.disabled = true;
  try {
    await guardarEnStorage();
    const datos = await browser.runtime.sendMessage({ tipo: "datos" });

    if (!datos || !datos.ok) {
      const mensaje = (datos && datos.mensaje) || "La extensión no respondió.";
      mostrar(mensaje, "fallo");
      return;
    }

    pintarPerfiles(datos, campoPerfil.value);
    const vivas = datos.postulaciones.length;
    mostrar(
      `Conectada. Perfil en uso: ${datos.nombre}. ` +
        `${vivas === 1 ? "1 postulación viva" : vivas + " postulaciones vivas"}.`,
      "ok"
    );
  } finally {
    botonConsultar.disabled = false;
  }
}

botonGuardar.addEventListener("click", async () => {
  await guardarEnStorage();
  mostrar("Guardado.", "ok");
});

botonConsultar.addEventListener("click", consultar);

(async function inicio() {
  const guardado = await browser.storage.local.get(["base", "perfil"]);
  campoBase.value = guardado.base || BASE_DEFECTO;
  if (guardado.perfil) {
    const option = new Option(guardado.perfil, guardado.perfil, true, true);
    campoPerfil.replaceChildren(
      new Option("Primera activa (por defecto)", ""),
      option
    );
    campoPerfil.value = guardado.perfil;
  }
  await consultar();
})();
