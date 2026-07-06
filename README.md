# esphome-hems

ESPHome-Konfiguration für einen HEMS-Controller auf Basis des **Waveshare ESP32-S3-POE-ETH-8DI-8RO**.

Der Controller empfängt §14a-EnWG-Leistungslimits vom Verteilnetzbetreiber (VNB) über EEBus SHIP/SPINE und verteilt sie intern an steuerbare Verbrauchseinheiten (StVE) wie Wärmepumpen und Wallboxen — ebenfalls über EEBus.

---

## Übersicht

```
VNB-Gateway (CLS-Steuerbox)
        │ EEBus LPC (CS-Rolle, Port 4712)
        ▼
  ESP32 HEMS  ──── §14a-Limit ────►  Wärmepumpe  (EG-Rolle, Port 4713)
  (dieser Controller)              (EEBus LPC)
        │
        └────────────────────────►  Wallbox, weitere StVE ...
```

Der Controller kombiniert:

- **EEBus CS-Empfänger** (`eebus_lpc`): empfängt §14a-Limits von der CLS-Steuerbox des VNB
- **EEBus EG-Sender** (`eebus_eg1`): sendet Limits an EEBus-fähige Geräte (Wärmepumpe, Wallbox)
- **OSSHPCF/SEMP**: passives Monitoring der Kompressor-Flexibilität (PV-Eigenverbrauch)
- **MPC**: liest die aktuelle Leistungsaufnahme der Geräte via EEBus zurück
- **Xemex CSMB Modbus RTU** Server-Emulation
- **Fronius Gen24** Modbus TCP Monitoring und Batterie-Entladebegrenzung
- **S0-Impulszählung** (Wasserzähler)
- **8 Relaisausgänge / 8 Digitaleingänge**

---

## Repository-Struktur

```
.
├── esphome-hems.yaml          # Haupt-ESPHome-Konfiguration
├── eebus-openeebus.yaml       # EEBus-Paket (CS + EG1) — per packages: eingebunden
├── xemex-csmb.yaml            # Xemex CSMB Modbus RTU Paket
├── fronius-gen24.yaml         # Fronius Paket
├── compile.ps1                # Compile/Flash-Skript (Windows PowerShell)
├── apply_patches.py           # Patching-Skript für openeebus-Anpassungen
├── requirements-dev.txt       # Python-Abhängigkeiten für Diagnose-Skripte
├── secrets.example.yaml       # Template für lokale Zugangsdaten
├── components/
│   ├── eebus_lpc/             # EEBus CS-Komponente (LPC-Empfänger vom VNB)
│   └── eebus_eg1/             # EEBus EG-Komponente (LPC-Sender an StVE)
├── openeebus/                 # openeebus SHIP/SPINE C-Bibliothek (Submodule)
├── port/                      # ESP32-Port-Anpassungen für openeebus
└── tools/
    └── diagnostics/           # Fronius-Diagnose-Skripte
```

---

## Einsteiger-Guide: In 3 Schritten zur §14a-Steuerung

### Schritt 1 — Zugangsdaten anlegen

```bash
cp secrets.example.yaml secrets.yaml
```

`secrets.yaml` öffnen und ausfüllen:

```yaml
api_encryption_key: "..."          # ESPHome API-Schlüssel (zufällig generieren)
ota_password: "..."                # OTA-Passwort
fronius_ip: "192.168.x.x"         # Fronius-IP (oder leer lassen)
mqtt_broker: "192.168.x.x"        # MQTT-Broker-IP

# EEBus-Pairing: leer lassen → Pairing über Web-UI
eebus_cls_remote_ski: ""           # CLS-Steuerbox des VNB (wird beim Pairing gesetzt)
eebus_eg1_remote_ski: ""           # Wärmepumpe / Wallbox (wird beim Pairing gesetzt)
```

> **Hinweis:** `secrets.yaml` wird nie eingecheckt — nur `secrets.example.yaml` liegt im Repository.

---

### Schritt 2 — Kompilieren und flashen

```powershell
# Nur kompilieren:
.\compile.ps1

# Kompilieren + per OTA auf das Gerät laden:
.\compile.ps1 --upload
```

> `esphome run` **nicht** verwenden — das Skript übernimmt die richtige Reihenfolge und das Zielgerät.

---

### Schritt 3 — EEBus-Pairing durchführen

Das Pairing muss **einmalig** für jede Verbindung gemacht werden:

#### CLS-Steuerbox (VNB-Gateway → HEMS)

1. ESPHome Web-UI öffnen → **„CS Pairing-Modus starten"** drücken (5 Minuten Fenster)
2. Am VNB-Gateway / CLS-Gerät das Pairing auslösen
3. Im Web-UI erscheint die SKI des Geräts unter „CS Pairing ausstehend" → **„CS Pairing akzeptieren"** drücken
4. Status wechselt auf „Verbunden" — SKI wird in NVS gespeichert, Pairing bleibt nach Neustart erhalten

#### Wärmepumpe / Wallbox (HEMS → StVE)

1. ESPHome Web-UI → **„EG1 Pairing-Modus starten"** drücken
2. Am Gerät (z.B. Bosch-App oder WP-Display) das EEBus-Pairing aktivieren
3. Verbindung wird automatisch hergestellt — Status wechselt auf „Verbunden"

> **Tipp:** Nach erfolgreichem Pairing ist die SKI in NVS gespeichert. Beim nächsten Start verbindet sich der Controller automatisch ohne erneutes Pairing.

---

## YAML-Referenz: EEBus-Komponenten

### `eebus_lpc` — CS-Empfänger (VNB → HEMS)

Empfängt §14a-Leistungslimits vom VNB-Gateway.

```yaml
eebus_lpc:
  id: hems_lpc
  ship_port: 4712                  # SHIP-Port (fixer Wert, nicht ändern)
  remote_ski: !secret eebus_cls_remote_ski
  device_brand: "DIY"
  device_type: "HEMS"
  device_model: "esphome-hems"
  failsafe_limit_w: 4200           # Fallback-Limit wenn Verbindung verloren geht

  on_limit_active:                 # Wird aufgerufen wenn VNB ein Limit sendet
    - lambda: |-
        float limit = x;           # x = Leistungslimit in Watt
        id(hems_eg1).set_limit(limit);   # Limit an WP weitergeben

  on_limit_cleared:                # Wird aufgerufen wenn Limit aufgehoben wird
    - lambda: |-
        id(hems_eg1).clear_limit();
```

**Verfügbare Methoden im Lambda:**

| Methode | Rückgabe | Beschreibung |
|---------|----------|--------------|
| `id(hems_lpc).is_limit_active()` | `bool` | Ist gerade ein Limit aktiv? |
| `id(hems_lpc).current_limit_w()` | `float` | Aktuelles Limit in Watt |
| `id(hems_lpc).is_connected()` | `bool` | Verbindung zur CLS-Steuerbox aktiv? |
| `id(hems_lpc).local_ski()` | `std::string` | Eigene SKI (für Pairing-Übergabe an VNB) |
| `id(hems_lpc).paired_remote_ski()` | `std::string` | SKI der gepaarten CLS-Steuerbox |

---

### `eebus_eg1` — EG-Sender (HEMS → StVE)

Sendet §14a-Leistungslimits an EEBus-fähige Geräte (Wärmepumpe, Wallbox).

```yaml
eebus_eg1:
  id: hems_eg1
  ship_port: 4713                  # SHIP-Port (pro StVE eindeutig)
  remote_ski: !secret eebus_eg1_remote_ski
  instance_name: "EG1"            # Bezeichnung in Logs und Web-UI
  device_brand: "DIY"
  device_type: "HEMS"
  device_model: "esphome-hems"
  failsafe_limit_w: 2600           # Limit das das Gerät hält wenn Verbindung abbricht
  failsafe_duration_s: 7200        # Dauer des Failsafe in Sekunden (2–24 h)

  on_eg1_connected:                # Wird aufgerufen wenn StVE verbunden
    - lambda: |-
        # Bei Reconnect: aktives Limit erneut senden
        if (id(hems_lpc).is_limit_active())
          id(hems_eg1).set_limit(id(hems_lpc).current_limit_w());

  on_eg1_disconnected:             # Wird aufgerufen wenn StVE getrennt
    - lambda: |-
        ESP_LOGW("eebus", "Wärmepumpe getrennt");

  on_power_reading:                # Wird aufgerufen bei neuer MPC-Leistungsmessung
    - lambda: |-
        float watt = x;            # x = gemessene Leistung in Watt
```

**Verfügbare Methoden im Lambda:**

| Methode | Parameter | Beschreibung |
|---------|-----------|--------------|
| `id(hems_eg1).set_limit(watts)` | `float` | Leistungslimit setzen (min. 4.200 W) |
| `id(hems_eg1).clear_limit()` | — | Limit aufheben (volle Leistung) |
| `id(hems_eg1).current_power_w()` | — | Letzte MPC-Leistungsmessung in Watt |
| `id(hems_eg1).active_limit_w()` | — | Zuletzt bestätigtes Limit in Watt |
| `id(hems_eg1).is_connected()` | — | Verbindung zur StVE aktiv? |
| `id(hems_eg1).update_failsafe_limit_w(w)` | `float` | Failsafe-Limit zur Laufzeit ändern |
| `id(hems_eg1).update_failsafe_duration_s(s)` | `uint32_t` | Failsafe-Dauer zur Laufzeit ändern |

---

### Mehrere StVE einbinden

Für jede weitere steuerbare Verbrauchseinheit (z.B. zweite Wärmepumpe oder Wallbox) eine zusätzliche `eebus_eg1`-Instanz mit eigenem Port anlegen:

```yaml
eebus_eg1:
  - id: hems_eg1_wp
    ship_port: 4713
    instance_name: "Wärmepumpe"
    remote_ski: !secret eebus_wp_remote_ski
    failsafe_limit_w: 4200
    failsafe_duration_s: 7200

  - id: hems_eg1_wb
    ship_port: 4714
    instance_name: "Wallbox"
    remote_ski: !secret eebus_wb_remote_ski
    failsafe_limit_w: 4200
    failsafe_duration_s: 7200
```

**Limit intern verteilen** (im `on_limit_active`-Handler von `eebus_lpc`):

```yaml
on_limit_active:
  - lambda: |-
      float total = x;               // Gesamtlimit vom VNB
      // Beispiel: Wärmepumpe hat Vorrang, Rest an Wallbox
      float wp_limit = std::min(total, 7000.0f);   // max 7 kW an WP
      float wb_limit = total - wp_limit;            // Rest an Wallbox
      if (wb_limit < 4200.0f) wb_limit = 0.0f;     // unter Minimum → abschalten
      id(hems_eg1_wp).set_limit(wp_limit);
      id(hems_eg1_wb).set_limit(wb_limit);
```

> **§14a-Hinweis:** Der VNB sendet mindestens 4.200 W pro angemeldeter StVE. Die interne Verteilung liegt beim HEMS-Betreiber. Limits unter 4.200 W werden von EEBus-Geräten nicht quittiert (die WP ignoriert sie still).

---

## EEBus Use Cases

| Use Case | Kürzel | Richtung | Beschreibung |
|----------|--------|----------|--------------|
| LimitationOfPowerConsumption | LPC | VNB→HEMS→StVE | §14a-Leistungslimit |
| MonitoringOfPowerConsumption | MPC | StVE→HEMS | Leistungsrückmeldung |
| OptimizationOfSelfConsumptionByHPCF | OSSHPCF | StVE→HEMS | Kompressor-Flexibilität (PV) |

**OSSHPCF/SEMP** wird automatisch erkannt wenn die Wärmepumpe den Kompressor ankündigt. Der Controller subscribt sich dann auf die SEMP-Datenpunkte (`fn=103`) und loggt eingehende Kompressor-Schedules — Grundlage für spätere PV-Eigenverbrauch-Optimierung.

---

## Failsafe-Verhalten

Wenn die Verbindung zwischen HEMS und StVE abbricht, fällt die StVE auf ihr Failsafe-Limit zurück:

- `failsafe_limit_w`: Leistung die das Gerät selbst hält (z.B. 2.600 W)
- `failsafe_duration_s`: Wie lange der Failsafe gilt (danach: volle Leistung)

Über die Web-UI können beide Werte zur Laufzeit geändert werden ohne Neustart.

Der Failsafe-Test (Web-UI → „EG1 Failsafe testen") stoppt den Heartbeat für 120 s und prüft so, ob das Gerät korrekt auf seinen Failsafe-Wert wechselt.

---

## Kompilieren und Flashen

```powershell
# Kompilieren (ohne Upload):
.\compile.ps1

# Kompilieren + per OTA flashen (Standard-Ziel: 192.168.178.24):
.\compile.ps1 --upload

# Anderes Zielgerät:
.\compile.ps1 --upload --device 192.168.x.x
```

> `esphome run` **nicht** direkt verwenden — `compile.ps1` stellt sicher dass beide Build-Cache-Pfade synchron sind.

---

## Diagnose-Skripte (Fronius)

```powershell
$env:FRONIUS_HOST = "192.168.x.x"
python tools/diagnostics/read_fronius.py
```

Abhängigkeiten installieren:

```powershell
python -m pip install -r requirements-dev.txt
```

---

## Sicherheit

`secrets.yaml`, ESPHome Build-Ausgabe, virtuelle Umgebungen und private Dokumentation werden nicht eingecheckt. Das Repository enthält nur `secrets.example.yaml` mit leeren Werten.

---

## Credits

| Komponente | Autor / Projekt | Lizenz |
|------------|----------------|--------|
| **openeebus** — SHIP/SPINE C-Bibliothek | [NIBE AB](https://github.com/nibe-stefan/openeebus) | Apache 2.0 |
| **esphome_modbus_tcp** — Modbus TCP Server | [creepystefan](https://github.com/creepystefan/esphome_modbus_tcp) | siehe Repo |
| **xemex-csmb** — Modbus RTU Server-Emulation | [thomase1234](https://github.com/thomase1234/esphome-fake-xemex-csmb) | siehe Repo |
| **eebus_lpc / eebus_eg1** — EEBus ESPHome-Komponenten | bgewehr (Eigenentwicklung) | MIT |

---

## Lizenz

MIT License. Siehe `LICENSE`.
