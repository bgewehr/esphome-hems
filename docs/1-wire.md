# 1-Wire-Temperatursensoren

Stand: 2026-07-26

## Ziel und Status

Das HEMS soll mehrere kabelgebundene Temperatursensoren, insbesondere
DS18B20, zuverlaessig erfassen. Dieses Dokument bewertet einen direkt per
GPIO erzeugten 1-Wire-Bus gegen einen dedizierten I2C-zu-1-Wire-Master.

Der DS2484 und sieben DS18B20-Sensoren sind installiert und als fest an ihre
ROM-IDs gebundene ESPHome-Entities in der produktiven Konfiguration aktiv. Die
vollstaendige Hardware-Abnahme, insbesondere Langzeit- und Fehlertests, steht
noch aus.

## Hardware-Bestand

Das Waveshare ESP32-S3-POE-ETH-8DI-8RO belegt im aktuellen HEMS folgende Pins:

| Funktion | GPIO |
|---|---|
| isolierte Digitaleingaenge DI1 bis DI8 | 4 bis 11 |
| W5500 Ethernet | 12 bis 16 |
| isoliertes RS485 | 17 und 18 |
| WS2812-Status-LED | 38 |
| bestehender I2C-Bus | 41 und 42 |
| Buzzer auf der Platine | 46 |

Der bestehende I2C-Bus auf GPIO41/42 steuert den TCA9554-Relaisexpander. Auf
der Platine ist ausserdem die PCF85063A-Echtzeituhr mit diesem Bus verbunden.
Ein externer Fehler, der SDA oder SCL dauerhaft auf Low zieht, koennte daher
Relaiszugriffe und die Echtzeituhr gleichzeitig blockieren.

Die Erweiterungsleiste stellt unter anderem GPIO1, GPIO2, GPIO3 und GPIO21
sowie 3,3 V und GND bereit. GPIO21 ist fuer einen direkten 1-Wire-Prototyp
geeignet. Fuer einen zweiten I2C-Bus werden GPIO1 als SDA und GPIO2 als SCL
reserviert. Beide Pins sind in der aktuellen Konfiguration unbenutzt und sind
keine ESP32-S3-Strapping-Pins.

GPIO3 soll wegen seiner Strapping-Funktion nicht verwendet werden. GPIO19/20
sind fuer USB, GPIO43/44 fuer eine moegliche UART-Erweiterung und GPIO45/47/48
fuer die TF-Karten-Schnittstelle freizuhalten.

## Entscheidung

Fuer die fest installierte HEMS-Hardware wird ein **DS2484 auf einem eigenen
zweiten I2C-Bus an GPIO1/2** bevorzugt.

Der DS2484 sitzt auf einer kurzen lokalen Verbindung nahe am HEMS. Nur der
1-Wire-Bus verlaesst das Gehaeuse. Der vorhandene I2C-Bus auf GPIO41/42 wird
nicht fuer den Temperaturadapter verwendet, damit ein Fehler im externen
Temperaturzweig die Relaissteuerung nicht beeintraechtigt.

Der DS2484 bietet gegenueber einem direkten GPIO-Bus:

- reproduzierbares 1-Wire-Timing unabhaengig von Ethernet-, TLS- und
  EEBus-Last,
- eine aktive Pull-up-Unterstuetzung fuer kapazitive Leitungen,
- eine optionale starke Pull-up-Unterstuetzung fuer parasitaer versorgte
  Teilnehmer,
- eine austauschbare Schnittstellenstufe zwischen Feldleitung und ESP32,
- native Unterstuetzung durch ESPHome ohne lokale Komponente.

Der DS2484 stellt keine galvanische Trennung und keinen vollstaendigen
Ueberspannungsschutz bereit. Bei Leitungen ausserhalb des Gehaeuses bleiben
geeigneter ESD-/Transientenschutz und eine saubere Leitungsfuehrung notwendig.

## Wann ein direkter GPIO-Bus ausreicht

ESPHome erzeugt 1-Wire bei `platform: gpio` per CPU auf einem bidirektionalen
MCU-Pin. Der ESP32-S3 besitzt kein RP2040-artiges PIO. Die CPU-Last weniger
Temperaturabfragen ist gering; der Hauptunterschied zum DS2484 ist deshalb die
elektrische Robustheit und nicht die Rechenleistung.

Ein direkter Bus auf GPIO21 ist fuer einen Prototyp vertretbar, wenn alle
folgenden Bedingungen erfuellt sind:

- etwa zwei bis vier Sensoren,
- hoechstens ungefaehr 5 m Gesamtleitung,
- linearer Bus mit sehr kurzen Abzweigen,
- keine gemeinsame Fuehrung mit Netz- oder Relaisleitungen,
- externe Versorgung aller Sensoren,
- externer Pull-up von etwa 4,7 kOhm zwischen 3,3 V und DQ.

Diese Werte sind Auslegungsrichtwerte und keine garantierten Grenzwerte. Die
Anzahl der Sensoren allein entscheidet nicht ueber die Zuverlaessigkeit.
Gesamtleitung, Entfernung zum entferntesten Sensor, Abzweige, Kabelkapazitaet
und Stoerumgebung sind wichtiger. Schon drei Sensoren an einer langen
Sternverkabelung koennen problematischer als zehn Sensoren an einem kurzen
linearen Bus sein.

## Elektrisches Konzept

Die bevorzugte Tochterplatine enthaelt mindestens:

- DS2484 mit lokaler 100-nF-Abblockung,
- I2C-Pull-ups nach 3,3 V, sofern sie nicht bereits auf dem Modul vorhanden
  sind,
- den laut DS2484-Referenzschaltung erforderlichen passiven 1-Wire-Pull-up,
- einen dreipoligen Anschluss fuer `VDD`, `DQ` und `GND`,
- ESD-/Transientenschutz am externen Anschluss,
- optional einen passend dimensionierten Serienwiderstand zur
  Reflexionsdaempfung.

Die DS18B20 werden im Dreileiterbetrieb versorgt. Parasitaere Versorgung soll
nicht eingesetzt werden. Damit bleibt `strong_pullup` deaktiviert; der aktive
Pull-up des DS2484 kann fuer die Flankenunterstuetzung aktiviert werden.

Fuer die Feldverkabelung gelten folgende Regeln:

1. Einen linearen Stamm mit kurzen Stichleitungen verwenden; ungeschaltete
   Sterne vermeiden.
2. DQ und GND als verdrilltes Paar fuehren. Bei CAT-Kabel kann ein weiteres
   Paar fuer VDD und GND verwendet werden.
3. Abstand zu Netzleitungen, Schuetzen, Relaiskontakten und Motorleitungen
   halten.
4. Alle Sensoren extern ueber VDD versorgen und eine durchgehende gemeinsame
   Masse sicherstellen.
5. Pull-up und eventuelle Seriendaempfung an der realen Maximalkonfiguration
   aus Sensorzahl, Leitung und Topologie pruefen.

Bei langen, unvermeidbar sternfoermigen Leitungen sind mehrere getrennte
1-Wire-Busse oder robuste RS485-Temperaturmodule einem einzelnen grossen
1-Wire-Netz vorzuziehen.

## Vorgesehene ESPHome-Konfiguration

Der vorhandene Bus behaelt seine ID und Pinbelegung. Der Temperaturadapter
erhaelt einen eigenen Bus:

```yaml
i2c:
  - id: i2c_bus
    sda: GPIO42
    scl: GPIO41
    scan: true
    frequency: 400kHz

  - id: temperature_bridge_i2c
    sda: GPIO1
    scl: GPIO2
    scan: true
    frequency: 100kHz

one_wire:
  - platform: ds2484
    id: temperature_bus
    i2c_id: temperature_bridge_i2c
    address: 0x18
    active_pullup: true
    strong_pullup: false
```

### Automatische Sensorerkennung

ESPHome erzeugt seine Entities beim Kompilieren. Der native 1-Wire-Bus findet
ROM-IDs beim Start, kann daraus zur Laufzeit aber keine neuen Home-Assistant-
Entities erzeugen. Vorab konfigurierte Index-Slots wuerden ohne Kompilieren
auskommen, koennen sich beim Hinzufuegen oder Entfernen eines Sensors jedoch
verschieben. Ein sprechender Name koennte dann unbemerkt zum falschen
physischen Messpunkt gehoeren. Diese Variante wird deshalb nicht verwendet.

**OW-10** nutzt ausschliesslich native ESPHome-Komponenten und automatisiert
stattdessen die sichere Adressregistrierung:

1. Der DS2484 fuehrt beim HEMS-Neustart seinen normalen Bus-Scan aus.
2. `tools/diagnostics/discover_one_wire.py` liest die protokollierten
  DS18B20-ROM-IDs und ignoriert andere Familien sowie CRC-Fehler.
3. Fuer jede neue ROM-ID fragt das Werkzeug einmalig nach einem sprechenden
  Namen, zum Beispiel `Pufferspeicher oben` oder `Vorlauf`.
4. Die Zuordnung wird in `1-wire-sensor-names.json` gespeichert. Eine kurz
  fehlende Sonde bleibt registriert und wird nicht einer anderen Sonde
  zugeordnet.
5. `1-wire-sensors.yaml` wird deterministisch mit festen Adressen erzeugt.
6. Der VS-Code-Task `Register 1-Wire sensors OTA` fuehrt Erkennung,
  Clean-Build und OTA-Upload nacheinander aus.

Damit erfordert eine neue Sonde technisch weiterhin einen Firmware-Build,
aber keine manuelle Adressregistrierung und nur einen Task-Aufruf plus die
einmalige Namenseingabe. Die ROM-ID bleibt die verlaessliche Identitaet; ein
sprechender Name wird niemals nur an eine veraenderliche Busposition gebunden.

Soll eine Sonde dauerhaft entfernt werden, wird ihr Eintrag bewusst aus
`1-wire-sensor-names.json` geloescht und der Registrierungstask erneut
ausgefuehrt. `1-wire-sensors.yaml` wird nicht von Hand bearbeitet.

Die Erstinbetriebnahme mit leerer Sensorliste und der anschliessende native
Bus-Scan sind abgeschlossen. Das Paket ist in `esphome-hems.yaml` aktiviert;
der Registrierungstask hat sieben fest adressierte Sensoren erzeugt und per OTA
uebernommen.

## Hardware-Abnahme

Vor der Uebernahme in den Regelbetrieb sind mindestens folgende Pruefungen
durchzufuehren:

- ESPHome-Konfiguration mit beiden I2C-Bussen kompilieren,
- DS2484 auf Adresse `0x18` erkennen und alle erwarteten Sensoradressen
  wiederholbar finden,
- automatisch erkannte Sensoren ohne manuelle Adressregistrierung als
  ROM-ID-gebundene Entities bereitstellen,
- Kaltstart und Neustart mit vollstaendig angeschlossenem Bus testen,
- alle Sensoren bei maximaler geplanter Leitung und Sensorzahl mindestens
  24 Stunden ohne CRC- oder Disconnect-Fehler erfassen,
- alle acht Relais wiederholt schalten und dabei die Temperaturkommunikation
  beobachten,
- offenen und kurzgeschlossenen 1-Wire-Feldanschluss simulieren; Relaiszugriff
  und bestehender I2C-Bus muessen funktionsfaehig bleiben,
- Ausfall und Wiederkehr einzelner Sensoren als ungueltigen beziehungsweise
  veralteten Messwert sichtbar machen,
- Temperaturwerte an mindestens zwei Referenzpunkten plausibilisieren.

Temperaturwerte duerfen erst dann in eine Regelentscheidung eingehen, wenn
Messwertalter und Sensorstatus ausgewertet werden. Ein fehlender oder alter
Sensorwert darf keine zusaetzliche Leistung oder eine weniger sichere
Betriebsgrenze freigeben.

## Quellen

- [Waveshare ESP32-S3-ETH-8DI-8RO Hardwarebeschreibung](https://www.waveshare.com/wiki/ESP32-S3-ETH-8DI-8RO)
- [ESPHome 1-Wire Bus](https://esphome.io/components/one_wire/)
- [ESPHome 1-Wire ueber GPIO](https://esphome.io/components/one_wire/gpio/)
- [ESPHome 1-Wire ueber DS2484](https://esphome.io/components/one_wire/ds2484/)
- [ESPHome Dallas Temperature Sensor](https://esphome.io/components/sensor/dallas_temp/)
- [Analog Devices: Guidelines for Reliable Long Line 1-Wire Networks](https://www.analog.com/en/resources/technical-articles/guidelines-for-reliable-long-line-1wire-networks.html)