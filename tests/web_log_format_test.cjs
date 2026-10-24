const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const zlib = require("node:zlib");

const script = fs.readFileSync(path.join(__dirname, "../tools/web/log-format.js"), "utf8");

class Log {
  render() { return this.logs; }
}

vm.runInNewContext(script, {
  customElements: { whenDefined: () => Promise.resolve(), get: () => Log },
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
  console.log("PASS: ANSI cleanup, preserved text and severity, repeat rendering, and optional firmware asset check");
});