// Adaptadores por portal.
//
// Cada adaptador sabe una sola cosa: cómo sacar el id de la oferta de la URL
// donde estás parado. No hay selectores de formularios: lo único que se
// rompía con cada rediseño del portal eran los selectores, y el prellenado
// quedó fuera de alcance (el autofill de Firefox cubre el contacto y la app
// no guarda ningún archivo de CV que se pueda subir solo).

(function (global) {
  const computrabajo = {
    nombre: "Computrabajo",
    dominios: ["ar.computrabajo.com", "candidato.ar.computrabajo.com"],

    detectar(loc, doc) {
      // La página de postulación (candidato.…/match/) trae el id en ?oi=.
      const oi = new URLSearchParams(loc.search).get("oi");
      if (oi) return oi;

      // La de detalle lo trae al final del path:
      // /ofertas-de-trabajo/oferta-de-trabajo-de-{slug}-{32 hex}.
      const detalle = loc.pathname.match(/-([0-9a-f]{32})\/?$/i);
      if (detalle) return detalle[1];

      // Último recurso: el portal pone el id en atributos data-oi de la
      // página. Sirve si cambia la forma de la URL y sigue habiendo id.
      const enPagina = doc && doc.querySelector("[data-oi]");
      if (enPagina) return enPagina.getAttribute("data-oi");

      return null;
    },
  };

  const zonajobs = {
    nombre: "ZonaJobs",
    dominios: ["www.zonajobs.com.ar", "zonajobs.com.ar"],

    detectar(loc) {
      // /empleos/{titulo}-{empresa}-{id}.html, con el id numérico al final.
      const aviso = loc.pathname.match(/\/empleos\/.*-(\d+)\.html\/?$/);
      if (aviso) return aviso[1];

      return null;
    },
  };

  const portales = [computrabajo, zonajobs];

  global.RADAR_PORTALES = portales;
  global.RADAR_PORTAL = function (hostname) {
    return portales.find((portal) => portal.dominios.includes(hostname)) || null;
  };
})(globalThis);
