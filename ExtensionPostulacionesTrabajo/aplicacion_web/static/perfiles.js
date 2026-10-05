/* Filas repetibles y validación del formulario de perfiles.
   Sin framework ni build step. El servidor vuelve a validar todo igual con
   pydantic: esto es UX, nunca seguridad. */

(function () {
  "use strict";

  var RE_EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

  /* ---------- indices de filas ---------- */

  /* Renumera los name de una lista para que sean 0..n-1 contiguos.
     hace falta porque al quitar una fila del medio quedan huecos, y el
     servidor agrupa por indice. Las filas que llegan del servidor vienen con
     el placeholder IDX en vez de un número, así que también se arreglan. */
  function renumerar(contenedor) {
    var filas = contenedor.querySelectorAll(":scope > .fila");
    filas.forEach(function (fila, indice) {
      fila.querySelectorAll("[name]").forEach(function (campo) {
        campo.name = campo.name.replace(/^([a-z_]+)__(?:IDX|\d+)__/, "$1__" + indice + "__");
      });
    });
  }

  function inicializarLista(lista) {
    var contenedor = lista.querySelector("[data-filas]");
    var plantilla = lista.querySelector("template[data-plantilla]");
    var boton = lista.querySelector("[data-agregar]");
    if (!contenedor || !plantilla || !boton) return;

    renumerar(contenedor);

    boton.addEventListener("click", function () {
      var html = plantilla.innerHTML.replace(/IDX/g, String(contenedor.children.length));
      contenedor.insertAdjacentHTML("beforeend", html);
      var ultima = contenedor.lastElementChild;
      if (ultima) {
        renumerar(contenedor);
        var primero = ultima.querySelector("input:not([type=checkbox]), textarea");
        if (primero) primero.focus();
      }
    });

    contenedor.addEventListener("click", function (evento) {
      var botonQuitar = evento.target.closest("[data-quitar]");
      if (!botonQuitar) return;
      botonQuitar.closest(".fila").remove();
      renumerar(contenedor);
    });
  }

  /* ---------- validacion ---------- */

  function marcar(campo, mensaje) {
    var error = document.querySelector('[data-error-para="' + campo.id + '"]');
    campo.classList.toggle("invalido", Boolean(mensaje));
    if (error) {
      error.textContent = mensaje || "";
      error.hidden = !mensaje;
    }
  }

  function validarCampo(campo) {
    var valor = (campo.value || "").trim();
    var tipo = campo.dataset.validar;

    if (tipo === "requerido" && !valor) {
      marcar(campo, "Este campo es obligatorio");
      return false;
    }
    if (tipo === "email" && valor && !RE_EMAIL.test(valor)) {
      marcar(campo, "Ese email no parece válido");
      return false;
    }
    if (campo.validity && campo.validity.typeMismatch && campo.value) {
      marcar(campo, "Revisá el formato, parece incorrecto");
      return false;
    }
    marcar(campo, "");
    return true;
  }

  function validacionEsContinua(evento) {
    var campo = evento.target;
    if (!campo.matches || !campo.matches("[data-validar], input, textarea")) return;
    // no marcar required mientras el usuario apenas está escribiendo
    if (campo.value === "" && !campo.dataset.tocado) return;
    validarCampo(campo);
  }

  document.addEventListener("input", validacionEsContinua);
  document.addEventListener("blur", function (evento) {
    var campo = evento.target;
    if (!campo.matches || !campo.matches("[data-validar]")) return;
    campo.dataset.tocado = "1";
    validarCampo(campo);
  }, true);

  document.addEventListener("submit", function (evento) {
    var formulario = evento.target;
    if (!formulario.matches('form[method="post"]') || formulario.dataset.noValidar) return;

    var invalidos = [];
    formulario.querySelectorAll("[data-validar]").forEach(function (campo) {
      campo.dataset.tocado = "1";
      if (!validarCampo(campo)) invalidos.push(campo);
    });

    if (invalidos.length) {
      evento.preventDefault();
      invalidos[0].scrollIntoView({ block: "center", behavior: "smooth" });
      invalidos[0].focus();
    }
  });

  /* ---------- arranque ---------- */

  document.addEventListener("DOMContentLoaded", function () {
    document.querySelectorAll("[data-lista]").forEach(inicializarLista);
  });
})();
