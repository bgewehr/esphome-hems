// HEMS UI-Vereinfachung: blendet im Standardbetrieb (Switch "Erweitertes UI"
// aus) alle Bedien- und Konfigurationselemente sowie ungepaarte EG-Sektionen
// aus. Nutzt den eingebauten Filter der esp-entity-table
// (is_disabled_by_default + show_all=false); leere Gruppen verschwinden von
// selbst. Serverseitige PIN-Logik liegt in esphome-hems.yaml.
(function () {
  "use strict";

  // Diese Entities bleiben im vereinfachten Modus sichtbar (Bedienung der
  // Umschaltung selbst)
  var KEEP = {
    "switch-esphome_hems_erweitertes_ui": true,
    "text-esphome_hems_pin": true,
  };

  // Interaktive Domains werden im vereinfachten Modus ausgeblendet
  var INTERACTIVE = {
    number: true, select: true, button: true, text: true, switch: true,
    light: true, fan: true, cover: true, climate: true, lock: true, valve: true,
  };

  function findTable() {
    var app = document.querySelector("esp-app");
    if (!app || !app.shadowRoot) return null;
    return app.shadowRoot.querySelector("esp-entity-table");
  }

  function stateOf(entities, uniqueId) {
    for (var i = 0; i < entities.length; i++) {
      if (entities[i].unique_id === uniqueId) return String(entities[i].state || entities[i].value || "");
    }
    return "";
  }

  function egUnpaired(entities, n) {
    var s = stateOf(entities, "text_sensor-esphome_hems_eg" + n + "_remote-ski");
    return s === "" || s === "(keine)" || s.indexOf("(suche") === 0;
  }

  function hideRule(e, entities, egHidden) {
    if (KEEP[e.unique_id]) return false;
    // Ungepaarte EG-Sektionen komplett ausblenden
    var g = e.sorting_group || "";
    for (var n = 1; n <= 2; n++) {
      if (egHidden[n] && g.indexOf("(EG" + n + ")") >= 0) return true;
    }
    // Konfiguration/Diagnose ausblenden
    if (e.entity_category && parseInt(e.entity_category) !== 0) return true;
    // Bedienelemente ausblenden — es bleiben Informationsanzeigen
    if (INTERACTIVE[e.domain]) return true;
    return false;
  }

  function apply() {
    try {
      var table = findTable();
      if (!table || !table.entities || !table.entities.length) return;
      var entities = table.entities;

      var advState = stateOf(entities, "switch-esphome_hems_erweitertes_ui");
      // Unbekannt/leer => erweitert (nichts verstecken)
      var advanced = advState === "" ? true : advState.toUpperCase() === "ON";

      if (table._hemsShowAll === undefined) table._hemsShowAll = table.show_all;

      var egHidden = { 1: egUnpaired(entities, 1), 2: egUnpaired(entities, 2) };

      var changed = false;
      for (var i = 0; i < entities.length; i++) {
        var e = entities[i];
        if (e._hemsOrig === undefined) e._hemsOrig = !!e.is_disabled_by_default;
        var want = advanced ? e._hemsOrig : (e._hemsOrig || hideRule(e, entities, egHidden));
        if (e.is_disabled_by_default !== want) {
          e.is_disabled_by_default = want;
          changed = true;
        }
      }

      var wantShowAll = advanced ? table._hemsShowAll : false;
      if (table.show_all !== wantShowAll) {
        table.show_all = wantShowAll;
        changed = true;
      }
      if (changed && table.requestUpdate) table.requestUpdate();
    } catch (err) {
      // defensiv: UI nie kaputt machen
    }
  }

  // Zustandsgetrieben nachziehen (SSE) + robuster 1-s-Takt fuer Initialaufbau
  function hookSource() {
    if (window.source && window.source.addEventListener && !window._hemsHooked) {
      window._hemsHooked = true;
      window.source.addEventListener("state", function () { setTimeout(apply, 50); });
    }
  }
  setInterval(function () { hookSource(); apply(); }, 1000);
})();
