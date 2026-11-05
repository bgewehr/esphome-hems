customElements.whenDefined("esp-log").then(() => {
  const prototype = customElements.get("esp-log").prototype;
  const render = prototype.render;
  const ansiStyle = /\x1b\[[0-9;:]*m/g;

  prototype.render = function () {
    for (const entry of this.logs) {
      entry.tag = entry.tag.replace(ansiStyle, "");
      entry.detail = entry.detail.replace(ansiStyle, "");
    }
    return render.call(this);
  };
});

customElements.whenDefined("esp-entity-table").then(() => {
  const prototype = customElements.get("esp-entity-table").prototype;
  const updated = prototype.updated;
  const prefix = /^ESPHome HEMS\s+/i;
  const entityIcons = {
    "EV Sperre zuruecksetzen": "mdi:lock-reset",
    "Battery Budget Reservation": "mdi:battery-lock",
    "Battery Priority Charge Target": "mdi:battery-charging-100",
    "Battery Priority OutWRte": "mdi:battery-arrow-up",
    "CSMB Anfragealter": "mdi:timer-outline",
    "CSMB CRC Fehler": "mdi:alert-circle-outline",
    "CSMB Stromantwortalter": "mdi:timer-outline",
    "EV Effektiver Sollwert": "mdi:current-ac",
    "EV Ladeabbrueche 5min": "mdi:counter",
    "EV Messwertalter": "mdi:timer-outline",
    "EV Solar Support": "mdi:solar-power-variant",
    "Storage Revert Time": "mdi:timer-sand",
    "Storage Writer Status": "mdi:database-check-outline",
    "EV Regelstatus": "mdi:state-machine",
    "CSMB Letzte Stromantwort": "mdi:message-reply-text-outline",
  };
  const domainIcons = {
    sensor: "mdi:gauge",
    binary_sensor: "mdi:checkbox-marked-circle-outline",
    text_sensor: "mdi:information-outline",
    switch: "mdi:toggle-switch",
    button: "mdi:gesture-tap-button",
    number: "mdi:tune",
    select: "mdi:form-select",
    text: "mdi:form-textbox",
    light: "mdi:lightbulb",
    fan: "mdi:fan",
    cover: "mdi:window-shutter",
    climate: "mdi:thermostat",
    lock: "mdi:lock",
    valve: "mdi:valve",
    date: "mdi:calendar",
    time: "mdi:clock-outline",
    datetime: "mdi:calendar-clock",
    update: "mdi:update",
  };

  prototype.updated = function (changedProperties) {
    updated?.call(this, changedProperties);
    if (!this.renderRoot.querySelector("#hems-local-display")) {
      const style = document.createElement("style");
      style.id = "hems-local-display";
      style.textContent = ":host .entity-row > :nth-child(2) { white-space: normal; overflow-wrap: anywhere; " +
        "text-overflow: clip; line-height: 1.35; padding-block: 8px; }";
      this.renderRoot.appendChild(style);
    }
    const entities = new Map();
    for (const entity of this.entities) {
      entities.set(`${entity.domain}:${entity.name}`, entity);
      entities.set(`${entity.domain}:${entity.name.replace(prefix, "")}`, entity);
    }
    for (const row of this.renderRoot.querySelectorAll(".entity-row")) {
      let entity;
      let displayName = "";
      for (const node of row.children[1].childNodes) {
        if (node.nodeType !== 3) continue;
        const name = node.data.replace(prefix, "");
        const candidate = entities.get(`${row.domain}:${node.data}`) || entities.get(`${row.domain}:${name}`);
        if (candidate) {
          entity = candidate;
          displayName = name;
        }
        if (name) node.data = name;
      }
      const cell = row.children[0];
      const icons = [...cell.querySelectorAll("iconify-icon")];
      let fallback = icons.find(icon => icon.hasAttribute("data-hems-fallback"));
      if (icons.some(icon => !icon.hasAttribute("data-hems-fallback"))) {
        fallback?.remove();
        continue;
      }
      if (!fallback) {
        fallback = document.createElement("iconify-icon");
        fallback.setAttribute("data-hems-fallback", "");
        fallback.setAttribute("height", "24px");
        cell.appendChild(fallback);
      }
      const icon = entity?.icon || entityIcons[displayName] ||
        (displayName.startsWith("FBH ") ? "mdi:thermometer" : domainIcons[row.domain]) || "mdi:information-outline";
      fallback.setAttribute("icon", icon);
      fallback.setAttribute("title", displayName || row.domain || "Status");
    }
  };
});