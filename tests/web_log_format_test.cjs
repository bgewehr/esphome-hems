const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const zlib = require("node:zlib");

const script = fs.readFileSync(path.join(__dirname, "../tools/web/log-format.js"), "utf8");

class Log {
  render() { return this.logs; }
}

class EntityTable {
  updated(changedProperties) { this.lastUpdate = changedProperties; }
}

function createIcon() {
  const attributes = new Map();
  return {
    setAttribute: (name, value) => attributes.set(name, value),
    getAttribute: name => attributes.get(name),
    hasAttribute: name => attributes.has(name),
    remove() { this.parent.icons = this.parent.icons.filter(icon => icon !== this); },
  };
}

function createIconCell(icons = []) {
  return {
    icons,
    querySelectorAll() { return this.icons; },
    appendChild(icon) { icon.parent = this; this.icons.push(icon); },
  };
}

function createRenderRoot(rows) {
  return {
    styles: [],
    querySelector() { return this.styles.find(style => style.id === "hems-local-display"); },
    querySelectorAll: () => rows,
    appendChild(style) { this.styles.push(style); },
  };
}

vm.runInNewContext(script, {
  document: { createElement: name => { assert.ok(["iconify-icon", "style"].includes(name)); return createIcon(); } },
  customElements: {
    whenDefined: () => Promise.resolve(),
    get: name => name === "esp-log" ? Log : EntityTable,
  },
});

Promise.resolve().then(() => {
  const log = new Log();
  log.logs = [
    { type: "d", tag: "[eebus_eg:195]\x1b[1;31m[EebusThread]\x1b[0;36m", detail: "\x1b[1mEG1 SPINE data change\x1b[0m" },
    { type: "w", tag: "[test:1]", detail: "unchanged [0] [1] \u00e4" },
    { type: "e", tag: "\x1b[38;2;255;0;0m[test]\x1b[m", detail: "\x1b[1mfirst\nsecond\x1b[22m" },
  ];
  assert.equal(log.render(), log.logs);
  assert.equal(log.logs[0].tag, "[eebus_eg:195][EebusThread]");
  assert.equal(log.logs[0].detail, "EG1 SPINE data change");
  assert.equal(log.logs[1].detail, "unchanged [0] [1] \u00e4");
  assert.equal(log.logs[2].tag, "[test]");
  assert.equal(log.logs[2].detail, "first\nsecond");
  assert.deepEqual(log.logs.map(entry => entry.type), ["d", "w", "e"]);
  const once = JSON.stringify(log.logs);
  log.render();
  assert.equal(JSON.stringify(log.logs), once);

  const table = new EntityTable();
  const name = { nodeType: 3, data: "ESPHome HEMS EG1 Leistung" };
  const device = { nodeType: 3, data: "[Bosch] " };
  const other = { nodeType: 3, data: "Anderes ESPHome HEMS Geraet" };
  const button = { url: "/button/esphome_hems_restart/press" };
  const row = { domain: "sensor", children: [createIconCell(), { childNodes: [device, name, { nodeType: 8, data: "ESPHome HEMS marker" }] }, button] };
  const otherRow = { domain: "text_sensor", children: [createIconCell(), { childNodes: [other] }] };
  table.entities = [{ name: name.data, domain: "sensor", unique_id: "sensor-esphome_hems_eg1_leistung" }];
  table.renderRoot = createRenderRoot([row, otherRow]);
  const originalEntities = JSON.stringify(table.entities);
  const changed = new Map();
  table.updated(changed);
  assert.equal(table.lastUpdate, changed, "upstream update lifecycle is preserved");
  assert.equal(name.data, "EG1 Leistung");
  assert.equal(device.data, "[Bosch] ");
  assert.equal(other.data, "Anderes ESPHome HEMS Geraet");
  assert.equal(row.children[1].childNodes[2].data, "ESPHome HEMS marker", "Lit markers are preserved");
  assert.equal(button.url, "/button/esphome_hems_restart/press");
  assert.equal(JSON.stringify(table.entities), originalEntities, "API names and object IDs must not change");
  table.updated(changed);
  assert.equal(name.data, "EG1 Leistung", "display shortening is idempotent");
  assert.equal(table.renderRoot.styles.length, 1, "readability styles are installed only once");
  assert.ok(table.renderRoot.styles[0].textContent.startsWith(":host .entity-row"), "local styles outrank adopted ESPHome styles");
  assert.ok(table.renderRoot.styles[0].textContent.includes("white-space: normal"), "long labels can wrap");
  assert.equal(row.children[0].icons.length, 1, "repeated updates do not duplicate icons");
  assert.equal(row.children[0].icons[0].getAttribute("icon"), "mdi:gauge");
  assert.equal(otherRow.children[0].icons[0].getAttribute("icon"), "mdi:information-outline");
  const upstreamIcon = createIcon();
  upstreamIcon.setAttribute("icon", "mdi:flash");
  row.children[0].appendChild(upstreamIcon);
  table.updated(changed);
  assert.deepEqual(row.children[0].icons, [upstreamIcon], "upstream icons replace fallbacks without duplication");

  const cases = [
    ["sensor", "FBH Vorlauf", "mdi:thermometer"],
    ["sensor", "CSMB CRC Fehler", "mdi:alert-circle-outline"],
    ["sensor", "EV Messwertalter", "mdi:timer-outline"],
    ["sensor", "Battery Budget Reservation", "mdi:battery-lock"],
    ["sensor", "EV Solar Support", "mdi:solar-power-variant"],
    ["button", "EV Sperre zuruecksetzen", "mdi:lock-reset"],
    ["text_sensor", "Storage Writer Status", "mdi:database-check-outline"],
    ["select", "Neue Auswahl", "mdi:form-select"],
    ["unknown", "Neuer Status", "mdi:information-outline"],
  ];
  table.entities = cases.map(([domain, label]) => ({domain, name: `ESPHome HEMS ${label}`}));
  const rows = table.entities.map(entity => ({domain: entity.domain, children: [createIconCell(), {childNodes: [{nodeType: 3, data: entity.name}]}]}));
  table.renderRoot.querySelectorAll = () => rows;
  const beforeIcons = JSON.stringify(table.entities);
  table.updated(changed);
  cases.forEach(([, label, icon], index) => {
    assert.equal(rows[index].children[0].icons[0].getAttribute("icon"), icon);
    assert.equal(rows[index].children[0].icons[0].getAttribute("title"), label);
  });
  assert.equal(JSON.stringify(table.entities), beforeIcons, "local icon fallbacks do not change HA metadata");

  if (process.argv[2]) {
    const root = process.argv[2];
    const header = fs.readFileSync(path.join(root, "esphome/components/web_server/server_index_v3.h"), "utf8");
    const array = header.match(/\bINDEX_GZ\s*\[\s*\]\s*[^=]*=\s*\{([^}]+)\}/);
    const unpack = values => zlib.gunzipSync(Buffer.from(values.split(",").filter(value => value.trim()).map(value => Number(value.trim())))).toString();
    const html = unpack(array[1]);
    assert.equal(html.split('<script type="module" src="/0.js"></script>').length - 1, 1);
    const main = fs.readFileSync(path.join(root, "main.cpp"), "utf8");
    const resource = main.match(/ESPHOME_WEBSERVER_JS_INCLUDE\[\d+\][^=]*=\s*\{([^}]+)\}/);
    assert.ok(resource, "embedded JavaScript resource must exist");
    assert.equal(unpack(resource[1]), script.replace(/\r\n/g, "\n"));
  }
  console.log("PASS: ANSI cleanup, local names and icons, unchanged API metadata and controls, repeat rendering, and optional firmware asset check");
});