// El filtro de la lista de ofertas.
//
// Los checkboxes de nivel y el select de provincia mandan el form apenas se
// tocan, porque si esperaran al botón "Filtrar" el usuario los marca, ve la
// lista sin cambiar y cree que la pantalla anda mal. Los campos de texto no: se
// tipea despacio y no tiene sentido pegarle una consulta por cada tecla.
//
// No hace falta mandar la página a la que se estaba: el servidor usa la 1
// cuando no viene `pagina`, y filtrar tiene que volver a la primera igual.

(function () {
  "use strict";

  var form = document.getElementById("filtros");
  if (!form) return;

  form
    .querySelectorAll('.filtro-niveles input[type="checkbox"]')
    .forEach(function (caja) {
      caja.addEventListener("change", function () {
        form.submit();
      });
    });

  // El select de provincia también: es una elección cerrada y deliberada, no
  // una tecla tipeada, así que se parece más a un checkbox que a un campo de
  // texto. Además el filtro de provincia casi siempre deja la lista en una
  // sola pantalla, donde el botón "Filtrar" queda lejos del control.
  var provincia = document.getElementById("provincia");
  if (provincia) {
    provincia.addEventListener("change", function () {
      form.submit();
    });
  }
})();