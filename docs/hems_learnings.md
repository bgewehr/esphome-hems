# HEMS Project Learnings

Hard-won lessons from building a DIY EEBus §14a HEMS on ESP32-S3 with ESPHome
and the openeebus C library. Written down so the next person (or the next
debugging session) doesn't have to rediscover them.

## 1. EEBus protocol (SHIP / SPINE)

- **SPINE JSON is SEQUENCE-encoded.** Every structure is an *array of
  single-key objects*, not a flat object: `[{"device": ...}, {"entity": [1]},
  {"feature": 2}]`. A flat `{"device": ..., "entity": ...}` fails openeebus's
  `JsonIsArray()` check and the whole datagram is silently rejected. This also
  applies to the datagram itself: `"datagram": [{"header": [...]}, {"payload":
  [...]}]` — header and payload are *separate list elements*; parsers must
  merge the sequence, not take element 0.

- **Feature ID numbering:** entity `[0]` features start at ID 0
  (NodeManagement is always `[0]:0`), entity `[1]` features start at ID 1.

- **SPINE device addresses are unrelated to TLS SKIs.** Address format is
  `d:_n:{vendor}_{serial}` (e.g. `d:_n:DIY_HEMS-CS-01`) or `d:_i:...` for some
  vendors. `GetFeatureWithAddress()` drops any message whose destination
  device doesn't match — wrong device string means silent message loss.
  Never compare a SPINE address against a TLS SKI; they identify different
  layers.

- **The LPC establishment sequence is long and strict.** Discovery READ/reply
  both directions → NM subscription both directions → UC data READ/reply →
  then (triggered by UC data processing) LC/DC/DD subscriptions + bindings +
  description reads → limit list read → only then limit WRITEs. The openeebus
  test fixtures (`openeebus/tests/src/use_case/actor/*/lpc/`) contain the
  exact JSON for every step — **they are the authoritative protocol
  documentation**, better than the spec PDFs for implementation work.

- **Use-case compatibility is checked hard.** An EG accepts a remote CS entity
  only if: entity type ∈ {CEM, Compressor, EVSE, HeatPumpAppliance, Inverter,
  SmartEnergyAppliance, SubMeterElectricity}, actor = `ControllableSystem`,
  UC = `limitationOfPowerConsumption`, and the mandatory scenarios 1–3 map to
  LoadControl / DeviceConfiguration / DeviceDiagnosis SERVER features in the
  discovery data. Any mismatch → the entity is silently ignored.

- **Heartbeat semantics:** the wire field `heartbeatTimeout` declares the
  sender's transmission interval; the receiver's enforcement deadline is its
  own policy (typically ≥ 1× the declared value). Send at *period ≤ remote
  deadline* — sending exactly at the deadline is a race. (Upstream PR #41
  renamed the API from "timeout" to "period" to make this unambiguous.)

- **Writes with `ackRequest: true` require a RESULT** (`resultData
  errorNumber: 0`) referencing the `msgCounter`. Devices also NOTIFY the new
  data state back to subscribers after accepting a write — the EG's limit-ACK
  path is driven by that notify, not by the RESULT.

## 2. openeebus library pitfalls

- **Thread stacks are tiny and stack overflow looks like a network bug.**
  `DeviceLocalLoop` was created with 4 KB. Processing a
  `nodeManagementUseCaseData` reply fires use-case event handlers
  *synchronously on the same stack*, which build and serialize new datagrams
  through recursive cJSON calls → FreeRTOS stack-overflow panic → reboot →
  the peer sees a connection reset ~3.5 s later. Symptom: "the device RSTs
  after my message"; cause: firmware crash. Fixed with 12 KB. Same class of
  bug hit cert generation earlier (fixed by heap-allocating the buffers).

- **The event bus is process-global.** Every SPINE event reaches every
  subscriber in the process — all EG instances and the CS see each other's
  events. Every listener must filter by instance (paired SKI / device
  address) or state gets cross-contaminated.

- **Several API functions are silent TODO stubs**, e.g.
  `CancelPairingWithSki()` does nothing. "I rejected the pairing" does not
  mean the connection closed. Grep for `// TODO: Implement method` before
  relying on any ship_node function.

- **Entities must be registered explicitly.** `EntityLocalCreate()` alone is
  not enough — without `DEVICE_LOCAL_ADD_ENTITY()` the entity is invisible to
  `GetFeatureWithAddress()` and subscriptions/bindings to it fail *silently*
  (no RESULT at all, not even an error).

- **Timing constants:** the SPINE tick is exactly 1 s
  (`DeviceLocal1sTickCallback`); pending write requests expire after
  `kDefaultMaxResponseDelayMs` = 10 s and answer with a timeout RESULT — they
  do not close connections.

## 3. ESP32 / ESPHome platform

- **`esp32.crash` component + background `esphome logs` is the fastest crash
  debugger.** Stream logs while reproducing; after the reboot the previous
  crash is printed with a decoded backtrace. This is how the 4 KB stack
  overflow was found in minutes after hours of protocol-level speculation.

- **`CONFIG_MBEDTLS_SSL_KEEP_PEER_CERTIFICATE=y` is mandatory** — without it
  the peer SKI cannot be extracted from the TLS session and every pairing
  shows SKI "unknown".

- **NVS namespace hygiene matters with multiple instances.** EG2 once
  inherited EG1's certificate from a legacy shared namespace → duplicate SKI
  → the remote device "recognized" the wrong instance. Per-instance
  namespaces + boot-time duplicate-SKI self-healing fixed it.

- **Watch the task watchdog during crypto.** ECC key generation takes long
  enough to trip the TWDT; feed it explicitly.

- **Large allocations belong in PSRAM** (EEBus buffers), stacks stay in
  internal RAM. W5500 SPI Ethernet + big heap use collided until allocs were
  routed explicitly.

- **Socket budget must be computed, not guessed.** Each SHIP instance costs
  listen + data + mDNS sockets; `CONFIG_LWIP_MAX_SOCKETS` is derived in the
  component Python (`consume_sockets()`), which is the only reason three
  EEBus instances + web server + API coexist.

## 4. Multi-instance architecture (CS + 2× EG on one device)

- **Remote devices probe every port.** The WP gateway connects to *all* EG
  instances it discovers via mDNS. Every callback must therefore verify the
  event belongs to *this instance's paired device*:
  - unpaired + not pairing → reject (early guard),
  - paired → **compare the entity's SPINE device address against the paired
    device** (resolved via `DEVICE_LOCAL_GET_REMOTE_DEVICE_WITH_SKI`).
  Missing the second check let a foreign entity overwrite
  `remote_entity_addr_` — EG2's limit writes then went to the WP while its
  actual device got nothing. This was the "EG2 not integrated" bug.

- **Unique per-instance identity everywhere:** own cert/SKI, own SHIP ID
  (`HEMS-EG-01` / `HEMS-EG-02` — identical SHIP IDs made the WP auto-trust
  the wrong instance), own NVS namespace, own mDNS TXT.

- **mDNS `register` TXT field controls who initiates pairing.** Advertise
  `register=true` only while a pairing window is open; unpaired idle
  instances must not advertise or remote devices connect endlessly.

- **Log every message with the instance name** (`EG1`/`EG2`/`CS` prefix).
  Interleaved multi-instance logs without instance tags are undebuggable.

- **Device quirks live in config, not code:** the WP silently ignores active
  limits below 4200 W (hence the clamp), a wallbox accepts any value — so the
  minimum is per-instance (`set_min_limit_w`), selected by device type in the
  UI, not hardcoded.

## 5. Testing without real hardware

- **Simulators beat waiting for devices.** `tools/fake_steuerbox.py` plays
  the VNB Steuerbox (EG role) against the CS port; `tools/fake_wallbox.py`
  plays a controllable EVSE (CS role) against the EG2 port. Together they
  exercise the complete chain Steuerbox → CS → EG → device with assertable
  output — this is how both the stack-overflow crash and the wrong-device
  bug were proven and their fixes verified.

- **Build simulators from the library's test fixtures.** Extract the JSON
  from the `.inc` files (note: they're split into concatenated C raw-string
  chunks) and adapt addresses. Guessing message formats from the spec wastes
  days; the fixtures are exact.

- **A pure responder is enough for the CS side.** The EG drives the whole
  establishment; the simulator only needs to answer reads, ACK calls, accept
  writes, and notify — plus periodic heartbeat NOTIFYs.

- **Windows console traps:** `python -u` for backgrounded scripts (stdout
  buffering swallows output), no non-cp1252 characters in prints (a `★`
  crashed the wallbox mid-handshake), per-recv socket timeouts + explicit
  `ConnectionError` propagation so resets surface at the right place.

- **Persist simulator certs** (gitignored) so the SKI stays stable across
  runs and re-pairing isn't needed every time.

## 6. Build & workflow

- **Component sources must be synced to two build paths** after every edit:
  `.esphome/external_components/<hash>/components/<name>/` **and**
  `c:/temp/esphome-hems/src/esphome/components/<name>/` — otherwise the
  compile silently uses stale code. The openeebus submodule is exempt: it is
  included via `-I` straight from the repo working tree.

- **Never `esphome run`** — always `esphome compile` + `esphome upload
  --device <ip>` (or `.\compile.ps1 --upload`), so a broken build never
  bricks the running device unattended.

- **Unity-build wrappers for the C library:** each openeebus `.c` compiles
  as its own translation unit via generated wrapper files, because the
  library reuses file-static function names across files (e.g. `Destruct`,
  `Tick`) that collide in a single TU.

- **openeebus lives as a git submodule** (fork, `hems` branch) — patches as
  loose files in `patches/` proved unmaintainable and were migrated.

## 7. §14a EnWG domain

- The VNB limit caps only the **netzwirksame** power of the *controllable*
  devices; normal household load is exempt. Local generation (PV, battery
  discharge) may be netted on top of the limit.
- With an EMS, **one aggregate limit** arrives (EEBus LPC); how it was
  computed (Gleichzeitigkeitsfaktoren) is the VNB's business. It is never
  below 4.2 kW.
- Forwarding the full limit to every device in parallel is **not** a
  distribution — each consumer then believes it may use the whole budget.
  A priority allocator with per-device floors/caps is required (see
  `power-distribution-concept.md`).
- Failsafe is per-device and must survive disconnects: on heartbeat loss the
  configured failsafe limit applies until the EG explicitly clears it —
  clearing state on disconnect would defeat the mechanism.

## 8. Upstream collaboration (NIBEGroup/openeebus)

- The maintainer prefers **`//` line comments** (no `/* */` blocks) and
  interface macros (`INFO_PROVIDER_...`) over direct function calls.
- Behavior changes to the library core get pushed back toward *renames +
  app-side policy* (see heartbeat period discussion) — propose the minimal
  library change and keep policy in the application.
- Read review comments precisely: "update the HEMS demo" meant
  `examples/hems/hems.c`, not the heat-pump demo the comment was anchored on.
  When a request is already satisfied, answer with file + commit evidence
  instead of pushing a no-op change.
- Rebase PR branches onto current `main` before discussion — stale bases make
  the diff show unrelated merged changes and confuse review.
