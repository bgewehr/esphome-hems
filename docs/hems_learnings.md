# HEMS Project Learnings

Hard-won lessons from building a DIY EEBus §14a HEMS on ESP32-S3 with ESPHome
and the openeebus C library. Written down so the next person (or the next
debugging session) doesn't have to rediscover them.

Reviewed: **2026-10-17**. Current library baseline: submodule `hems` at
`f714766`. These are implementation lessons and bounded observations, not
certification claims. A running experiment is not a completed acceptance test.

## 1. EEBus protocol (SHIP / SPINE)

- **SPINE JSON is SEQUENCE-encoded.** Every structure is an *array of
  single-key objects*, not a flat object: `[{"device": ...}, {"entity": [1]},
  {"feature": 2}]`. A flat `{"device": ..., "entity": ...}` fails openeebus's
  `JsonIsArray()` check and the whole datagram is silently rejected. This also
  applies to the datagram itself: `"datagram": [{"header": [...]}, {"payload":
  [...]}]` — header and payload are *separate list elements*; parsers must
  merge the sequence, not take element 0.

- **Resolve feature addresses through discovery.** NodeManagement is at
  `[0]:0`; do not infer other feature IDs from an entity's number or one
  simulator's numbering scheme.

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
  exact JSON for the implemented steps. Use them as executable examples,
  alongside the specification and real-device captures, not as a replacement
  for the protocol specification.

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

- **Event managers are per local device in the current library.** The old
  process-global event-bus warning no longer describes this baseline. Register
  listeners against their owning local device and still filter the remote
  peer: a device can discover more than its intended partner.

- **Pairing cancellation is asynchronous, not a stub.**
  `CancelPairingWithSki()` queues cancellation; the SHIP owner closes the
  connection. Verify the resulting disconnect and absence of unwanted retries
  rather than treating the API call itself as completion.

- **Required listener callbacks must be supplied.** Upstream invokes some
  CS remote-added/removed and EG nominal-max callbacks unconditionally.
  Missing callbacks caused a live `PC=0` crash after Bosch discovery despite
  a successful build. Use correctly typed no-op callbacks where appropriate.

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

- **Native ESP-IDF cJSON includes need the platform-specific path.** A Linux
  build alone does not validate the `ESP_PLATFORM` branch. Keep the dedicated
  ESP-IDF header/syntax check alongside host tests.

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

- **Device quirks live in per-instance policy.** The Bosch integration uses
  a configurable 4200 W active-limit floor (`set_min_limit_w`). Do not infer
  that every heat pump has this floor or that a wallbox can physically follow
  every requested wattage. Distinguish protocol acceptance, charging minimum,
  measured response time and the effective aggregate budget.

## 5. Testing without real hardware

- **Simulators beat waiting for devices.** `tools/fake_steuerbox.py` plays
  the VNB Steuerbox (EG role) against the CS port; `tools/fake_wallbox.py`
  plays a controllable EVSE (CS role) against the EG2 port. Together they
  exercise the complete chain Steuerbox → CS → EG → device with assertable
  output — this is how both the stack-overflow crash and the wrong-device
  bug were proven and their fixes verified.

- **Build simulators from the library's test fixtures.** Extract the JSON
  from the `.inc` files (note: they're split into concatenated C raw-string
  chunks) and adapt addresses. Check both fixture expectations and real-device
  responses; passing simulator tests alone does not establish interoperability.

- **A pure responder is enough for the CS side.** The EG drives the whole
  establishment; the simulator only needs to answer reads, ACK calls, accept
  writes, and notify — plus periodic heartbeat NOTIFYs.

- **Windows console traps:** `python -u` for backgrounded scripts (stdout
  buffering swallows output), no non-cp1252 characters in prints (a `★`
  crashed the wallbox mid-handshake), per-recv socket timeouts + explicit
  `ConnectionError` propagation so resets surface at the right place.

- **Persist simulator certs** (gitignored) so the SKI stays stable across
  runs and re-pairing isn't needed every time.

- **Use distinct mDNS identities for isolated demo devices.** Shared demo
  brand/serial values can produce the same service name. This is a discovery
  collision, not necessarily a SHIP pairing failure.

## 6. Build & workflow

- **Edit source files, not generated build copies.** ESPHome copies component
  sources during generation; the library wrappers are generated before that
  copy. New C sources can still be missed by cached CMake source discovery:
  use a clean build after changing the library's source set. Manual copying
  into multiple caches is not the normal build procedure.

- **Separate build success from deployment permission.** Use the existing
  compile/upload workflow and do not upload as a side effect of a diagnostic
  test. Shared build directories make overlapping builds unsafe: before OTA,
  use one controlled build/upload sequence and verify the live Build Time.

- **Per-source wrappers for the C library:** each openeebus `.c` compiles
  as its own translation unit via generated wrapper files, because the
  library reuses file-static function names across files (e.g. `Destruct`,
  `Tick`) that collide in a single TU.

- **openeebus lives as a git submodule** (fork, `hems` branch) — patches as
  loose files in `patches/` proved unmaintainable and were migrated.

- **The current entry point is this repository.** `bgewehr/openeebus@bg-hems`
  is historical; the active dependency is the pinned `hems` commit. Clone
  recursively. The CI-tested minimal example is `eebus-minimal.yaml`, not the
  historical `eebus_wp` component. Version/hardware claims belong to the
  tested combination documented in [README](../README.md).

- **Windows Docker builds benefit from Linux-backed build storage.** For
  ESPHome toolchain extraction, a named volume avoids the severe overhead
  observed on a Windows bind mount. Keep source mounts and build storage
  separate when reproducing the minimal firmware build.

## 7. §14a EnWG domain

- The VNB limit caps only the **netzwirksame** power of the *controllable*
  devices; normal household load is exempt. Local generation (PV, battery
  discharge) may be netted on top of the limit.
- With an EMS, **one aggregate limit** arrives (EEBus LPC); how it was
  computed depends on the applicable installation and VNB configuration.
  Do not confuse the configured device floor with the aggregate limit or
  treat 4.2 kW as a universal protocol validation rule.
- Forwarding the full limit to every device in parallel is **not** a
  distribution — each consumer then believes it may use the whole budget.
  A priority allocator with per-device floors/caps is required (see
  [power-distribution-concept.md](power-distribution-concept.md)).
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

- Check whether PRs are stacked before applying them. A later PR may contain
  all earlier commits; merging every complete diff independently duplicates
  changes. Review API migrations and preserve the ESP32-specific port fixes.

## 9. Bosch heat-pump flexibility (OSSHPCF)

- OSSHPCF is not a direct compressor power setpoint. The heat pump publishes
  alternatives, sequences, time slots and constraints through
  `SmartEnergyManagementPs`; the HEMS may select an offered `sequenceId` only
  when the node and sequence are marked remote-controllable.
- **The library already has CEM and compressor OHPCF actors and tests.**
  Our ESPHome integration does not instantiate the CEM OHPCF controller: it
  subscribes and reads SEMP directly, with a bounded decoder. Updating the
  library alone therefore does not enable active scheduling.
- **A successful local read request is not a received reply.** `read_err=0`
  only confirms request submission; a matching `OSSHPCF read reply` message
  counter proves the response. Subscription alone need not produce a snapshot.
- **The completed 2026-10-07/08 capture contained 11 SEMP snapshots**, all
  with zero alternatives, sequences, slots and values, and no decoder
  truncation. HP power reached 2937 W (Bosch MPC: 2899 W), but all snapshots
  were taken at only 16-23 W. No new SEMP snapshot was recorded during the
  high-power phase. This does not prove that Bosch never offers flexibility.
- **Capture counters must count events, not log lines.** The old summary's
  "33 SEMP messages" represented three lines for each of 11 snapshots.
  "0 logger reconnects" counted subprocess restarts, not the 10 API
  disconnects recovered inside ESPHome. A 24-hour process lifetime is not
  evidence of gap-free coverage.
- **Start-triggered observation uses the external HP meter.** The owner
  identifies a transition from <=50 W to >50 W as plant startup, not
  necessarily compressor startup. The opt-in `--start-reads` capture mode
  schedules ten read attempts at 60-second intervals, starting at that edge.
  A high initial reading is not a new start; short threshold dips do not
  rearm detection (60 seconds at <=50 W is required after a detected start).
  The series continues if power falls, but missing samples for more than
  15 seconds or an API disconnect cancel it. Busy/disconnected read status
  skips an attempt; missed attempts are not replayed in a burst.
- **The new 24-hour experiment is still pending.** Started 2026-10-17 at
  10:49 local time, with nominal end 2026-10-18 at 10:49. Seven offline tests
  and an eight-second live startup/deadline check passed. The short check
  sent no SEMP read because the plant was already running. Successful
  event-triggered Bosch responses and the completed 24-hour result are not
  yet established. Keep the recording machine awake and avoid OTA during
  the observation.
- **citolen's PRs are relevant, not a fix for empty raw snapshots.** As
  reviewed on 2026-10-17, [#75](https://github.com/NIBEGroup/openeebus/pull/75)
  fixes state mapping, [#76](https://github.com/NIBEGroup/openeebus/pull/76)
  isolates compressor state per remote entity, and
  [#77](https://github.com/NIBEGroup/openeebus/pull/77) reports offers even
  when connecting to an already-running process and uses merged feature
  data for partial updates. Draft
  [#78](https://github.com/NIBEGroup/openeebus/pull/78) includes those commits
  and adds `power` fallback, default watts when the unit is absent, and an
  optional minimum duration. These changes were reviewed, not integrated or
  locally test-run. Our decoder already reports both `power` and `powerMax`.
- **Estimated power is not a guaranteed ceiling.** PR #78 stores a fallback
  `power` estimate in `max_power_w`; retain that distinction before using it
  for budgeting. It cannot replace an LPC limit or measured enforcement.
- Keep OSSHPCF read-only until the concrete Bosch units, time base, mandatory
  slots, state transition after selection and behavior under concurrent LPC
  are documented. The capture matrix and staged implementation plan are in
  [oss-hpcf-bosch.md](oss-hpcf-bosch.md); dated historical status there must
  not be mistaken for the latest experiment result.
- LPC and device failsafe always take precedence over flexibility scheduling.
  OSSHPCF may shift heat-pump operation but must never increase its allocated
  §14a budget.

## 10. Evidence and private diagnostics

- Keep captures, local addresses, credentials and installation-specific task
  settings out of public commits. Use `private/`, ignored local files and
  environment/CLI configuration rather than embedding endpoints in scripts.
- `.gitignore` matches paths, not sensitive content, and does not untrack
  existing files or erase history. Previously exposed credentials need
  rotation; deleting a branch does not retract clones or cached commits.
- Separate request acceptance, protocol response and physical effect in
  reports. A green host test or firmware build is not live-device acceptance,
  and electrical power alone does not distinguish room heating, hot water,
  defrost or auxiliary heating.
- HTTP diagnostic values can be strings. Validate availability and numeric
  conversion before applying thresholds; never trigger controls merely to
  make an entity's state look healthy.
