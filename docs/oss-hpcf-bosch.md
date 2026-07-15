# Bosch-Waermepumpe: OSSHPCF / SEMP

## Ziel und Status

`optimizationOfSelfConsumptionByHeatPumpCompressorFlexibility` (OSSHPCF,
teilweise auch OHPCF abgekuerzt) soll den Betrieb der Bosch-Waermepumpe in
Zeiten mit lokalem PV-Ueberschuss verschieben. Der Use Case ist von der
gesetzlichen Leistungsbegrenzung LPC zu trennen:

1. LPC setzt eine zwingende Obergrenze und hat immer Vorrang.
2. OSSHPCF optimiert nur innerhalb der verbleibenden Freiheitsgrade.
3. Komfort-, Frostschutz- und geraeteseitige Betriebsgrenzen bleiben bei der
   Waermepumpe.

Der aktuelle Stand ist **read-only Beobachtung**. `eebus_eg` erkennt den vom
Bosch-Geraet angekuendigten Use Case, erzeugt ein lokales
`SmartEnergyManagementPs`-Client-Feature und abonniert das entsprechende
Server-Feature der Waermepumpe. Nach dem Subscription-Aufbau liest es einmalig
den aktuellen `SmartEnergyManagementPsData`-Zustand und dekodiert eingehende
SEMP-Daten begrenzt und null-sicher. Es werden keine OSSHPCF-Steuerbefehle
gesendet.

### Verifizierter Kommunikationsstand vom 2026-07-13

Ein kontrollierter HEMS-Neustart mit bereits laufendem EEBus-Log hat den
vollstaendigen Bosch-Reconnect erfasst:

1. SHIP und SPINE wurden zur Bosch-Waermepumpe neu aufgebaut.
2. Bosch meldete als unterstuetzte Use Cases
  `CS/LPC | MU/MPC | Comp/OSSHPCF`.
3. Das HEMS fand das entfernte `SmartEnergyManagementPs`-Server-Feature auf
  Entity 2 und protokollierte:

  ```text
  EG1 OSSHPCF: subscribed to remote SEMP (entity 2) err=0
  ```

Damit sind OSSHPCF-Ankuendigung und erfolgreicher Subscription-Request real
belegt. Eine Subscription allein lieferte jedoch auch bei laufender
Warmwasserbereitung keine initiale Zustandsmeldung.

### Verifizierter initialer Read vom 2026-07-14

`eebus_eg` fordert nach der Subscription ueber OpenEEBus `RequestData()`
einmalig `SmartEnergyManagementPsData` an. Beim ersten realen Reconnect waehrend
laufender Warmwasserbereitung wurden Subscription und Read mit `err=0`
bestaetigt. Etwa 380 ms spaeter lieferte Bosch einen `SempData`-Snapshot:

```text
remote=true single_slot_only=true alternatives=0 sequences_max=1 reselection=false
summary: alternatives=0 sequences=0 slots=0 values=0 truncated=false
```

Parallel lagen Bosch-MPC und externer HP-Zaehler bei rund 2,5 kW. Damit ist
belegt, dass Bosch den aktuellen SEMP-Zustand auf expliziten Read liefert.
Der Snapshot beschreibt eine remote steuerbare, auf maximal eine Sequenz und
Single-Slot-Scheduling begrenzte Funktion, enthaelt in diesem Betriebszustand
aber kein aktuelles Flexibilitaetsangebot. Aktive OSSHPCF-Steuerung ist damit
weiterhin weder implementiert noch belegt.

## Standardisierter Datenvertrag

Das in OpenEEBus vorhandene SPINE-Modell beschreibt OSSHPCF nicht als freien
Leistungssollwert. Die Waermepumpe bietet ausfuehrbare Alternativen und
Sequenzen an; das HEMS waehlt eine von der Waermepumpe freigegebene Sequenz.

### Daten der Waermepumpe

`smartEnergyManagementPsData` kann enthalten:

- `nodeScheduleInformation`
  - `nodeRemoteControllable`
  - `supportsSingleSlotSchedulingOnly`
  - `alternativesCount`
  - `totalSequencesCountMax`
  - `supportsReselection`
- `alternatives`
  - Relation zwischen `alternativesId` und den angebotenen `sequenceId`s
  - Beschreibung und Zustand jeder Sequenz
  - Start-/Endzeit und zulaessige Zeitfenster
  - optionale Zeit-Slots mit Dauer und Aktivierungszustand
  - Leistungs- oder Energiewerte je Slot, z. B. `power`, `powerMin`,
    `powerMax`, `powerExpectedValue`, `energy` oder entsprechende Grenzen
  - Betriebsunterbrechungs-, Dauer- und Wiederaufnahmebedingungen
  - Praeferenzen wie `greenest` oder `cheapest`

Vor jeder Steuerung muessen mindestens folgende Freigaben gelten:

```text
nodeRemoteControllable == true
sequenceRemoteControllable == true
sequenceId ist aktuell angeboten
gewaehlter Start liegt innerhalb aller Schedule-Constraints
alle Pflicht-Slots und Mindestdauern bleiben erhalten
Messdaten und Uhrzeit sind aktuell
```

### Steuerdaten des HEMS

Die standardisierte Auswahl erfolgt mit
`smartEnergyManagementPsConfigurationRequestCall`. Der eingebettete
`scheduleConfigurationRequest` enthaelt eine `sequenceId`.

Damit gilt fuer die erste Implementierung:

- Das HEMS erfindet keine eigenen Sequenzen oder Slot-Leistungen.
- Es waehlt nur eine aktuell von der Waermepumpe angebotene Sequenz.
- Ein neuer Request wird erst gesendet, nachdem Angebot und Constraints
  vollstaendig ausgewertet wurden.
- Der Erfolg wird nicht allein aus einem positiven RESULT abgeleitet. Die
  anschliessende SEMP-Zustandsmeldung muss die erwartete Sequenz bzw. den
  erwarteten Zustand bestaetigen.

Optional existiert ein
`smartEnergyManagementPsPriceCalculationRequestCall`. Dieser uebergibt eine
`sequenceId` und eine potentielle Startzeit. Ob die Bosch-Waermepumpe diese
Funktion anbietet und welche Preissemantik sie erwartet, ist noch unbekannt.

## Bosch-spezifisch noch unbekannt

Die OpenEEBus-Modelldefinitionen belegen die moeglichen Felder, aber nicht,
welche Teilmenge und Semantik das konkrete Bosch-Gateway verwendet. Es fehlen
aktuell reale, vollstaendig dekodierte Bosch-Telegramme fuer:

- `nodeScheduleInformation` und die Remote-Control-Flags,
- Anzahl und Bedeutung der Alternativen und Sequenzen,
- Einheit, Vorzeichen und Skalierung der Leistungs-/Energiewerte,
- absolute oder relative Zeitangaben und deren Bezugszeitpunkt,
- Mindest-/Maximaldauer sowie optionale und verpflichtende Slots,
- Verhalten bei laufendem Verdichter, Warmwasserbereitung und Heizbetrieb,
- Unterstuetzung von Neuauswahl und Preisberechnung,
- Antwort auf eine Sequenzauswahl und nachfolgende State-Transitions,
- Verhalten bei gleichzeitig aktivem LPC-Limit, Disconnect oder Neustart.

Diese Werte duerfen nicht aus Modellnamen oder anderen Herstellern abgeleitet
werden. Bis sie am realen Geraet nachgewiesen sind, bleibt OSSHPCF read-only.

## Capture-Matrix

Fuer eine belastbare Bosch-Dokumentation werden dekodierte SEMP-Snapshots in
folgenden Zustaenden benoetigt:

| Zustand | Erwarteter Erkenntnisgewinn |
|---|---|
| Anlage idle, kein Waermebedarf | Grundstruktur und leere/inaktive Sequenzen |
| Raumheizung angefordert, Verdichter noch aus | verschiebbare Startfenster |
| Raumheizung, Verdichter laeuft | Unterbrechbarkeit und Restlaufzeit |
| Warmwasserbereitung angefordert | eigene Sequenz bzw. strengere Constraints |
| Warmwasserbereitung laeuft | Pflicht-Slots und Resume-Verhalten |
| hoher PV-Ueberschuss | Aenderung von Empfehlungen oder Alternativen |
| LPC aktiv | Zusammenspiel von Flexibilitaet und Leistungsobergrenze |
| Disconnect/Reconnect | Gueltigkeit und Neuaussendung alter Sequenz-IDs |

Jeder Capture soll enthalten:

- Firmwareversion und Modell des Bosch-Gateways bzw. der Waermepumpe,
- lokale Uhrzeit und synchronisierten SPINE-Zeitbezug,
- Use-Case-Ankuendigung mit Version, Szenarien und Subrevision,
- Remote-Entity- und Feature-Adresse,
- Funktionstyp, Change-Type und vollstaendig dekodierte Nutzdaten,
- parallele MPC-Leistung, Betriebsart und aktives LPC-Limit,
- Folge-Telegramme bis mindestens 60 Sekunden nach einer Zustandsaenderung.

Private SKIs und Seriennummern werden beim Ablegen der Captures anonymisiert.

### Capture-Matrix

| Datum | Zustand | Beobachtung | Ergebnis |
|---|---|---|---|
| 2026-07-13 | Idle/Standby, MPC 25 W, externer Zaehler ca. 16 W | Kontrollierter Neustart, 90 Sekunden Beobachtung mit Decoder-Build `14:59:31` | `Comp/OSSHPCF` angekuendigt, SEMP-Subscription auf Entity 2 mit `err=0`; ohne expliziten Read keine initiale Nachricht, daher kein belastbarer SEMP-Zustandsbefund |
| 2026-07-14 | Warmwasserbereitung laeuft, Bosch-MPC 2461-2569 W, externer HP-Zaehler 2505-2566 W | Reconnect mit einmaligem read-only `SmartEnergyManagementPsData`-Read | Subscription und Read `err=0`; Snapshot mit `remote=true`, `single_slot_only=true`, `sequences_max=1`, aber ohne Alternativen, Sequenzen, Slots oder Werte |

Der Rohmitschnitt liegt ausschliesslich im ignorierten Verzeichnis
`private/captures/`. Die Matrix enthaelt keine SKI, Seriennummer oder sonstige
Geraeteidentifikation. Die Beobachtungen zeigen, dass Subscription und
initialer Zustandsabruf getrennte Operationen sind und auch ein erfolgreicher
Read noch kein aktuelles Flexibilitaetsangebot garantiert.

## Entwicklungsplan

Die Aufgaben-IDs werden auch in der zentralen [TODO-Liste](../TODO.md)
verwendet. Die aktive Steuerung bleibt bis Phase 4 technisch deaktiviert.

### Phase 0: Vorhandene Basis

- **OHP-00 (erledigt):** OSSHPCF-Use-Case erkennen, lokales
  `SmartEnergyManagementPs`-Client-Feature anlegen und das Bosch-Server-
  Feature abonnieren. Am realen Geraet am 2026-07-13 mit `err=0` verifiziert.
- **OHP-01 (erledigt):** Standardvertrag, Bosch-Unbekannte, Capture-Matrix
  und Sicherheitsgrenzen dokumentieren.

### Phase 1: Strukturierter Decoder, weiterhin read-only

- **OHP-10 (erledigt):** `EventPayload.function_data` wird bei
  `kFunctionTypeSmartEnergyManagementPsData` als
  `SmartEnergyManagementPsDataType` ausgewertet. Der begrenzte, null-sichere
  Decoder gibt Knoten-, Alternativen-, Sequenz-, Zeitfenster-, Slot- und
  Rohwertdaten strukturiert aus und loest keine Steueraktion aus. Der
  ESPHome-Clean-Build wurde am 2026-07-13 verifiziert.
- **OHP-11 (erledigt):** Root-Host-Tests decken leere, partielle,
  unbekannte, vollstaendige und uebergrosse Payloads ab. Die begrenzte
  Diagnose enthaelt weder SKIs noch Seriennummern. Build und CTest wurden am
  2026-07-13 mit GCC 14 in einer isolierten Linux-Umgebung verifiziert.

Abnahme: Ein unbekannter oder partieller Payload fuehrt weder zu einem Crash
noch zu einem Request; alle fuer die Capture-Matrix benoetigten Felder sind
strukturiert auslesbar.

### Phase 2: Bosch-Vertrag und Flexibilitaetsmodell

- **OHP-20:** Capture-Matrix am realen Bosch-Geraet abarbeiten und Rohdaten
  plus dekodierte Darstellung reproduzierbar ablegen. Idle/Standby ohne
  initialen Read ist am 2026-07-13 erfasst; laufende Warmwasserbereitung mit
  initialem SEMP-Snapshot am 2026-07-14. Anforderung vor Verdichterstart,
  Raumheizung, PV-Ueberschuss, LPC und Zustandswechsel sind noch offen.
- **OHP-21:** Einheit, Skalierung, Zeitbasis, Remote-Control-Flags,
  Pflicht-Slots, Neuauswahl und State-Transitions dokumentieren. Nicht
  beobachtete Semantik bleibt explizit unbekannt.
- **OHP-22:** Bosch-Daten in ein herstellerneutrales Flexibilitaetsmodell
  ueberfuehren:

```text
offer_id / sequence_id
valid_from / valid_until
earliest_start / latest_end
min_duration / max_duration
expected_power / min_power / max_power
expected_energy
interruptible / optional
remote_controllable
source_timestamp / quality
```

Dieses Modell gehoert in den geplanten Regelkern, nicht in eine lange
YAML-Lambda.

Abnahme: Alle verwendeten Modellfelder lassen sich auf einen realen
Bosch-Payload und eine dokumentierte Einheit beziehungsweise Zeitbasis
zurueckfuehren.

### Phase 3: Dry-run-Planer

- **OHP-30:** Planer implementieren, der Angebote gegen PV-Verfuegbarkeit und
  das §14a-Budget bewertet und nur protokolliert, welche Sequenz er waehlen
  wuerde. Mindestens mehrere Heiz- und Warmwasserzyklen muessen ohne
  Constraint-Verletzung nachvollzogen werden.
- **OHP-31:** Tests fuer Zeitfenster, Pflicht-Slots, abgelaufene Angebote,
  Uhrzeitsprung, Reconnect und gleichzeitig aktives LPC ergaenzen.

Abnahme: Der Planer erzeugt keine eigenen Sequenzen, waehlt nie ein
abgelaufenes oder nicht fernsteuerbares Angebot und sendet keine Requests.

### Phase 4: Explizit freigeschaltete Sequenzauswahl

- **OHP-40:** Request-Transport und Transaktionszustand fuer
  `smartEnergyManagementPsConfigurationRequestCall` implementieren. Pro
  Angebot wird hoechstens ein Request gesendet; RESULT, aktualisierter
  SEMP-State und Timeout werden als requested / acknowledged / measured
  getrennt verfolgt.
- **OHP-41:** Standardmaessig deaktivierten Betreiber-Schalter und einen
  sofortigen Rueckfall auf read-only implementieren.
- **OHP-42:** Sequenzauswahl mit simulierten RESULT-, Ablehnungs-, Timeout-,
  Reconnect- und Neustartfaellen testen.

Abnahme: Ohne explizites Opt-in kann kein
`smartEnergyManagementPsConfigurationRequestCall` gesendet werden. Erwarteter
RESULT und nachfolgender State stimmen ueberein; Fehler fuehren zu read-only,
und eine alte `sequenceId` wird nie erneut angewandt.

### Phase 5: Einbindung in das HEMS

- **OHP-50:** OSSHPCF-Planer mit Budget- und Qualitaetsmodell verbinden. Die
  §14a-Budgetverteilung begrenzt dessen momentane Leistung. Bei Konflikten
  gilt:

```text
LPC / Failsafe > technische Bosch-Constraints > Komfortreserve
               > PV-Eigenverbrauch > Tarifoptimierung
```

Ein aktives oder aus Messgruenden degradiertes LPC-Regime darf durch OSSHPCF
niemals aufgeweicht werden.
- **OHP-51:** Kontrollierte Hardwareabnahme ueber Heiz-, Warmwasser-, PV-,
  LPC-, Disconnect- und Neustartszenarien durchfuehren und dokumentieren.

Abnahme: Die folgenden Freigabekriterien sind vollständig erfuellt.

## Abnahmekriterien fuer aktive Steuerung

Aktives OSSHPCF wird erst freigegeben, wenn:

1. die Capture-Matrix fuer das konkrete Bosch-Geraet dokumentiert ist,
2. alle genutzten Felder samt Einheit, Skalierung und Zeitbasis bekannt sind,
3. unbekannte oder widerspruechliche Payloads sicher zu read-only fuehren,
4. Sequenzwahl, RESULT, State-Update und Timeout simuliert getestet sind,
5. Reconnect und Neustart keine alte `sequenceId` erneut anwenden,
6. LPC-Aktivierung waehrend Planung und Ausfuehrung getestet ist,
7. ein Betreiber die Funktion jederzeit deaktivieren kann.

## Quellen im Repository

- `components/eebus_eg/eebus_eg.cpp`: Erkennung, SEMP-Client und Subscription
- `openeebus/src/spine/model/smart_energy_management_ps_types.h`: SEMP-Daten
- `openeebus/src/spine/model/power_sequences_types.h`: Sequenzen, Slots und Constraints
- `openeebus/src/spine/model/function_types.h`: SEMP-Funktionstypen
- `openeebus/tests/src/spine/model/datagram_payload/sma_use_case_data_reply.inc`:
  Beispiel einer OSSHPCF-Use-Case-Ankuendigung, keine Bosch-Steuerpayload
