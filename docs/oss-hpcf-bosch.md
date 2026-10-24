# Bosch-Waermepumpe: OSSHPCF / SEMP

Stand: 2026-10-19. Lokaler Build ersetzt keine Hardwareabnahme.

## Ziel und Status

`optimizationOfSelfConsumptionByHeatPumpCompressorFlexibility` (OSSHPCF,
teilweise auch OHPCF abgekuerzt) soll den Betrieb der Bosch-Waermepumpe in
Zeiten mit lokalem PV-Ueberschuss verschieben. Der Use Case ist von der
gesetzlichen Leistungsbegrenzung LPC zu trennen:

1. LPC setzt eine zwingende Obergrenze und hat immer Vorrang.
2. OSSHPCF optimiert nur innerhalb der verbleibenden Freiheitsgrade.
3. Komfort-, Frostschutz- und geraeteseitige Betriebsgrenzen bleiben bei der
   Waermepumpe.

Der lokale Stand ist **Beobachtung mit automatischer Subscription und
Binding, ohne Heizungs-Steuerbefehle**. Das vorbereitete lokale
`SmartEnergyManagementPs`-Client-Feature wird fuer diese Operationen erst
aktiv, wenn das gekoppelte EG-Geraet `Compressor / OSSHPCF` anbietet.
Ein separater Binding-Schalter ist nicht erforderlich. Ohne Angebot wird
auch ein manueller Read abgewiesen. Unveraenderte Ankuendigungen starten
keinen erneuten Aufbau. Ruecknahme sperrt Reads und veranlasst bestmoegliches
Unsubscribe/Unbind; Disconnect und vergessenes Pairing setzen die Erkennung
zurueck. Verspaetete Antworten aus einer alten Aktivierung werden verworfen.

`Aktive UC` zeigt `OSSHPCF (lesend)`, sobald bei bestehender Verbindung und
HPCF-Angebot die Subscription in der Geraetetabelle bestaetigt ist.
Ein Flexibilitaetsangebot, eine erfolgreiche Datenabfrage oder ein Binding
sind fuer diesen Eintrag nicht erforderlich. Ohne bestaetigte Subscription,
bei Disconnect oder Ruecknahme des UC fehlt der Eintrag. Ein separates
Subscription-Status-Item entfaellt; interne Pruefung und Logs bleiben erhalten.
`Binding Status` bleibt vorerst separat. Erst wenn Lesen und Steuern
betriebsbereit sind, einschliesslich bestaetigtem Binding, darf der Zusatz
`(lesend)` entfallen und der Binding-Status in der UC-Anzeige aufgehen.
Bei verlorenem Binding darf diese dann keine volle Steuerbereitschaft anzeigen.

Subscription und Binding werden anhand der jeweiligen
NodeManagement-Tabellen des Geraets geprueft. `result=sent` ist nur lokaler
Versanderfolg; erst `verified: present=true` bestaetigt einen Eintrag mit
passender Client- und Serveradresse. Eine ausgelassene Geraeteadresse wird
gegen das Geraet der korrelierten Tabellenantwort aufgeloest. Explizite
Geraeteadressen, vollstaendige Entity-Pfade und Feature-IDs muessen passen;
fehlende Entity-/Feature-Felder werden nicht ergaenzt. Fehlende
Tabellenantwort ist keine bestaetigte Ablehnung.
OpenEEBus verarbeitet fuer diese Tabellen nun auch Read/Reply mit
Message-Counter-Korrelation.

Die Logs unterscheiden `cmd=notify` und `cmd=reply`. Neben den begrenzt
dekodierten Rohdaten zeigt `OSSHPCF merged:` den zusammengefuehrten Zustand
des Remote-Features, wenn sich die dekodierten Ausgaben unterscheiden oder
eine davon gekuerzt wurde. Gleiche ungekuerzte Ausgaben werden nicht doppelt
ausgegeben. Node-Eigenschaften erscheinen je Roh-/Merge-Kanal beim ersten
Auftreten, bei Aenderungen und nach einer neuen HPCF-Aktivierung erneut.
Das beruecksichtigt die Erkenntnis aus PR #77 zu Teilmeldungen.
Normale Requests, Tabellenbestaetigungen und Datenantworten stehen auf INFO;
Fehler und fehlende Registrierung auf WARN. Fuer vollstaendige Captures muss
der Laufzeit-Loglevel mindestens INFO fuer `eebus_eg` zulassen.
`feature_type` stammt aus dem Feature-Objekt (remote: Discovery), niemals aus
einer angenommenen Bedeutung der Feature-ID; unbekannte Typen bleiben `unknown`.
Die generische `SPINE data change`-Ausgabe liegt grundsaetzlich auf VERBOSE,
auch fuer Heartbeats (`fn=18`) und Messwertmeldungen (`fn=51`). Eigene
Messwert-, Status- und Fehlerlogs sowie die Verarbeitung bleiben unveraendert.
VERBOSE wird mitkompiliert und steht in `Log Level` und `Boot Log Level` zur
Auswahl. `Boot Log Level` bleibt die beim Start eingefrorene Session-Obergrenze:
Zum Freigeben von VERBOSE zuerst dort VERBOSE waehlen, neu starten und danach
`Log Level` auf VERBOSE setzen. Der aktive Log-Filter wird bei Levelwechseln
erneut angewendet. Vor dem Wiederherstellen der Auswahl startet der Logger
weiterhin mit DEBUG; die Standardauswahl beider Dropdowns bleibt WARN.
Der Capture zaehlt Requests, Tabellenbestaetigungen, Replies und Notifications
getrennt. Alte `subscribe_err=0`-Zeilen sind keine Bestaetigungen.

### Registrierungsdiagnose im Log

Die Diagnose protokolliert ereignisbezogen:

- `SPINE registration tx` (weiterhin WARN): Subscription/Binding, lokale und entfernte
  Geraeteadresse, `msg_cnt`, angeforderte Quittierung und lokaler `send_err`.
- `SPINE registration result` (weiterhin WARN): NodeManagement-Antwort mit Adressen,
  `msg_ref_present`, `msg_ref`, `error_present` und numerischem SPINE-Fehler.
  Nur bei vorhandener Referenz und Fehlernummer kann eine Antwort zugeordnet
  werden. `error=0` bedeutet Erfolg, `6` CommandNotSupported, `7`
  CommandRejected, `9` BindingIsNecessaryForThisCommand. Andere Codes bleiben
  numerisch sichtbar. Die Zeile kann auch andere NodeManagement-Calls
  betreffen; Zuordnung immer ueber Geraetepaar und Message-Counter.
- `OSSHPCF ... verified`: eine Ergebniszeile mit Eintragszahl, `msg_ref`,
  Feature-Typ und passendem Client-/Serverpaar auf INFO. Bei fehlendem Treffer
  stehen stattdessen erwartete Adressen und Array-Verfuegbarkeit auf WARN.
- `OSSHPCF ... entry[...]`: vollstaendige Tabellenvergleiche auf DEBUG,
  einschliesslich anderer LPC-/MPC-Eintraege mit `comparison=...`.
  `missing-device` unterscheidet eine relative/unvollstaendige Adresse von
  `different-device`, `different-feature` oder `different-entity`.

Tabellen mit null Eintraegen sind damit von vorhandenen, nicht zuordenbaren
Eintraegen unterscheidbar. `match-relative-device` kennzeichnet den Vergleich
nach Aufloesung einer ausgelassenen Geraeteadresse gegen den Tabellenbesitzer.
Dies gilt auch fuer Client-Adressen: ein relativer Client wird nicht implizit
dem HEMS zugeordnet. Explizit andere Geraete werden niemals ueberschrieben.
Im Log erscheint die aufgeloeste Geraeteadresse mit `(relative)`; ohne Kontext
steht `<relative-unresolved>`, bei explizit leerer Adresse `<empty>`.
Eine Quittierung allein wird weiterhin nicht als Tabellenbestaetigung gezaehlt;
ein fehlendes Result-Log beweist keinen Timeout. Es wird kein zusaetzlicher
Quittierungs-Timer oder Dauerpoll eingerichtet. Je Tabelle werden maximal
16 Eintraege ausgegeben; Adressausgaben sind begrenzt und markieren ihre
Kuerzung. Der Vergleich prueft weiterhin alle Eintraege.

Der automatische Aufbau hat maximal drei Versuche pro HPCF-Aktivierung mit
mindestens fuenf Sekunden Abstand. Laufende Reads und Tabellenantworten
werden abgewartet; bei beiden bestaetigten Eintraegen endet die Wiederholung.
Disconnect bzw. Capability-Wechsel entwerten alte Wiederholungen. Der
manuelle Leseknopf kann weiterhin fehlende Registrierungen erneut anfordern
und liest danach Tabellen und SEMP-Daten; er sendet keine Heizungsbefehle.

Die Anzeige `Aktive UC` fuehrt `OSSHPCF (lesend)`, sobald bei bestehender
Verbindung und angebotenem HPCF die Subscription in der Geraetetabelle
bestaetigt ist. Binding, eine Datenantwort oder vorhandene Alternativen
sind dafuer keine Voraussetzung. Ohne bestaetigte Subscription, bei
Disconnect oder Ruecknahme des HPCF-Angebots entfaellt der Eintrag.
Der Zusatz `(lesend)` bezeichnet die aktuelle Implementierungsgrenze;
er kann erst mit implementierter und freigegebener Schreibfunktion entfallen.

Der Capture behaelt auch die Transportzeilen. Die Adressen koennen private
Geraeteidentifikatoren enthalten: Logs bleiben unter `private/captures` und
muessen vor Veroeffentlichung anonymisiert werden. Die erweiterte Diagnose
wurde am 2026-10-18 per OTA installiert. Die Korrektur relativer Adressen
und der begrenzte Wiederanlauf sind im Live-Build `Oct 18 2026 22:52:10`
bestaetigt. Im Mittags-Build `Oct 18 2026 11:58:59` war die erweiterte
Diagnose noch nicht enthalten.

Es wird weiterhin kein vollstaendiger CEM-OHPCF-Use-Case angekuendigt und
keine Sequenz ausgewaehlt oder Start/Pause/Resume/Stop gesendet. Binding
bereitet die Berechtigung vor, ist aber kein Steuerbefehl. 835 OpenEEBus-
und drei HEMS-Hosttests, 16 Capture-Tests sowie Firmware-Build bestanden.
Hardware-Nachweis am 2026-10-18, passiver Mitschnitt 22:53:00 bis 22:54:30:
Die erste Subscription-Tabelle enthielt nur einen anderen Eintrag, Binding
war leer; diese Antworten kamen vor den HPCF-Quittungen. Automatischer
Versuch 2/3 um 22:53:38 erhielt fuer beide Registrierungen `error=0` und
anschliessend `verified: present=true` (Subscription 7, Binding 3 Eintraege).
Das passende Paar war jeweils Client Entity `[1]` / Feature `7` und Server
Entity `[1,1]` / Feature `19`, serverseitig `match-relative-device`.
Beide UI-Status blieben bis Mitschnittende bestaetigt; kein Tastendruck oder
dritter Versuch. SEMP lieferte zwei leere Replies, keine Notification.
Damit sind Registrierung und Anlauf nachgewiesen, nicht ein vorhandenes
Flexibilitaetsangebot oder die Zustellung zukuenftiger Aenderungsmeldungen.

### Manueller Read mit Antwortnachweis

Seit 2026-10-07 kann `EG1 OSSHPCF Daten lesen` den aktuellen SEMP-Zustand
ohne Neustart erneut anfordern. `EG1 OSSHPCF Read Status` unterscheidet:

- noch nicht angefordert, vorgemerkt und Warten auf Antwort,
- korrelierte Antwort mit oder ohne Alternativen,
- Timeout, lokalen Read-Fehler, getrennte Verbindung und fehlendes HPCF-Angebot.

Der initiale Read nach der Use-Case-Anmeldung verwendet denselben Callback.
OpenEEBus ordnet die Antwort ueber den Message-Counter zu und meldet den
Timeout ueber seinen bestehenden Pending-Reply-Takt. `read_err=0` bedeutet
weiterhin nur, dass der lokale Request erfolgreich war. Erst
`OSSHPCF read reply: msg_ref=...` belegt die zugehoerige Antwort. Die
dekodierten Daten erscheinen wie bisher unter `OSSHPCF data:` auf WARN.
Benachrichtigungen ohne passende Read-Referenz ersetzen diesen Nachweis nicht.

Waehrend eines vorgemerkten oder laufenden Reads wird kein zweiter manueller
Read gestartet. Disconnect und geloeschtes Pairing setzen die Diagnose
zurueck. Es gibt kein periodisches Polling, keine automatische Read-Wiederholung
und keine Sequenzauswahl oder Aenderung von Heizungs-/LPC-Sollwerten.
Eine Antwort mit Alternativen ist noch keine Freigabe zur aktiven Steuerung.

Fuer eine Betriebsaufnahme kann der lokale Task
`Capture OSSHPCF for 24 hours` verwendet werden. Ohne lokale Task-Datei:

```bash
python -u tools/diagnostics/capture_oshpcf.py --host <device-address> --duration 86400 --start-reads
```

ESPHome muss mit den benoetigten Abhaengigkeiten verfuegbar sein; bei Bedarf
`--esphome <executable-path>` angeben. Der Modus benoetigt neben dem API-Log
die HTTP-Diagnose des HEMS. Ohne `--start-reads` bleibt der Logger passiv.
Die Aufzeichnung liegt unter `private/captures/`; der Rechner muss wach und
verbunden bleiben. Leere Antworten, Timeouts und Leistung getrennt auswerten.

Live-Pruefung am 2026-10-07 mit OTA-Build `Oct 7 2026 12:08:48`:

- Initialer Read um 12:12:55, korrelierte Antwort etwa 0,55 s spaeter
  (`msg_ref=22`, Funktion 104); Status `Antwort ohne Alternativen`.
- Zwei manuelle Reads lieferten jeweils eine eigene korrelierte Antwort
  (`msg_ref=35` und `36`), ohne Neustart oder Heizungssteuerung.
- Drei gleichzeitig ausgeloeste Buttons erzeugten einen Read (`msg_ref=38`)
  und zwei Meldungen `OSSHPCF read already pending`.
- Alle Antworten enthielten `remote=true`, `single_slot_only=true`,
  `sequences_max=1`, `reselection=false` sowie null Alternativen, Sequenzen,
  Slots und Werte; Decoder `truncated=false`.
- Firmware-Build erfolgreich. Timeout-Diagnose ist an den vorhandenen
  OpenEEBus-Callback angebunden, wurde aber nicht durch einen absichtlich
  gestoerten Live-Bus getestet. Ein Heizbetriebsnachweis steht noch aus.

Privates Testprotokoll: `private/captures/semp-read-diagnostic-20261007.log`.

Die Statusanzeige prueft den lokalen Zustand weiterhin jede Sekunde, publiziert
aber nur initial und bei Aenderungen. Dadurch entfallen wiederholte identische
Textsensor-Logs und UI-Updates; echte SEMP-Telegramme bleiben unveraendert sichtbar.
Die fruehere Aussage, dass die Aenderung wegen der Aufzeichnung noch auf OTA
wartet, ist kein aktueller Deploy-Status: waehrend der Oktober-Aufzeichnung
gab es weitere Firmwarewechsel. Den tatsaechlichen Stand immer anhand der
Live-Build-Time bestimmen; keine Updates fuer eine reine Diagnose ausloesen.

### Abgeschlossene 24h-Aufzeichnung vom 7./8. Oktober

Die Aufzeichnung lief von 2026-10-07 12:14 bis 2026-10-08 12:14 und wurde
regulaer beendet. Sie enthielt elf SEMP-Snapshots, alle ohne Alternativen,
Sequenzen, Slots oder Werte und mit `truncated=false`. Zehn Antworten hatten
zusaetzlich einen protokollierten korrelierten Read-Nachweis.

Der externe HP-Zaehler erreichte 2937 W, Bosch-MPC 2899 W. Saemtliche
Snapshots lagen jedoch bei nur 16-23 W externer Leistung; waehrend der
hohen Leistungsaufnahme wurde kein neuer SEMP-Snapshot aufgezeichnet.
Daraus folgt weder, dass Bosch im laufenden Betrieb Angebote liefert,
noch, dass Bosch grundsaetzlich keine Flexibilitaet anbietet.

Die alte Zusammenfassung zaehlte 33 Logzeilen als SEMP-Meldungen, tatsaechlich
drei Zeilen je Snapshot. `logger reconnects=0` bezeichnete nur Neustarts des
Logger-Unterprozesses; im Log stehen zehn intern wiederverbundene API-Abbrueche.
Es war daher kein lueckenloser 24h-Stabilitaetsnachweis.

### Ereignisgesteuerte Messung ab 17. Oktober

Der Betreiber identifiziert den Wechsel des externen HP-Zaehlers von
<=50 W auf >50 W als Anlagenstart, nicht zwingend als Verdichterstart.
`--start-reads` plant darauf zehn Read-Versuche: sofort sowie nach jeweils
60 Sekunden bis Minute 9. Die Reihe laeuft auch bei wieder sinkender Leistung
weiter. Weitere hohe Messwerte starten keine zweite Reihe; nach einem Start
sind 60 Sekunden bei <=50 W zum erneuten Scharfschalten erforderlich.

Ein bereits hoher Anfangswert erzeugt keinen Start. Messluecken ueber
15 Sekunden oder API-Abbruch verwerfen die laufende Reihe. Ein laufender Read
oder ein nicht lesebereiter Status ueberspringt den Termin; keine nachtraeglichen
Abfragebursts. Ein HTTP-Erfolg ist kein Beleg fuer eine Bosch-Antwort:
Ausloeser, Reihennummer, Versuch und korrelierte Antwort getrennt auswerten.

Die Messung vom 2026-10-17 10:49 bis 2026-10-18 10:49 lokal ist abgeschlossen:
elf Startreihen mit je zehn Reads, 110 eindeutig korrelierte Antworten,
alle ohne Alternativen, Sequenzen, Slots oder Werte und `truncated=false`.
85 Reads erfolgten bei mehr als 500 W Anlagenleistung, maximal 2494 W bei
einem Read. Keine uebersprungenen/fehlgeschlagenen Reads, Read-Timeouts,
API-Abbrueche oder Logger-Neustarts; groesste Messluecke 2,663 Sekunden.
Damit ist die fruehere Luecke im Leistungsbetrieb geschlossen, nicht jedoch
die gesamte Betriebsartenmatrix. Das alte Log unterschied Notify/Reply
nicht und zaehlte 110 lokale Subscription-Pruefzeilen als Subscriptions;
das beweist keine angenommene Subscription und keinen Push-Betrieb.

### Vorhandene OHPCF-Aktoren und Upstream-PRs

OpenEEBus enthaelt bereits CEM- und Compressor-OHPCF-Aktoren mit Tests.
Unsere ESPHome-Anbindung nutzt bisher den direkten SEMP-Read und eigenen
Decoder, nicht den CEM-OHPCF-Controller. Ein Bibliotheksupdate allein aktiviert
keine Sequenzauswahl und erzeugt keine fehlenden Bosch-Angebote.

Am 2026-10-17 wurden citolens offene PRs geprueft:
[#75](https://github.com/NIBEGroup/openeebus/pull/75) korrigiert Zustaende,
[#76](https://github.com/NIBEGroup/openeebus/pull/76) trennt den Zustand je
Verdichter und [#77](https://github.com/NIBEGroup/openeebus/pull/77) meldet
Angebote auch nach Verbindung mit bereits laufenden Prozessen und verwendet
zusammengefuehrte Daten fuer Teilmeldungen. Draft
[#78](https://github.com/NIBEGroup/openeebus/pull/78) enthaelt diese Kette und
akzeptiert unter anderem `power` statt nur `powerMax`. Die PRs wurden nicht
integriert oder lokal ausgefuehrt. Unser Decoder erkennt beide Werttypen bereits.
Der `power`-Fallback in `max_power_w` ist ein Schaetzwert, keine garantierte
Obergrenze; diese Semantik muss ein spaeteres Budgetmodell erhalten.

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
welche Teilmenge und Semantik das konkrete Bosch-Gateway verwendet.
Aus den bisherigen Snapshots sind `remote=true`, `single_slot_only=true`,
`sequences_max=1` und `reselection=false` bekannt. Es fehlen reale Angebote
und Zustandswechsel, um folgende Eigenschaften zu belegen:

- sequenzbezogene Remote-Control-Freigaben,
- Bedeutung tatsaechlich angebotener Alternativen und Sequenzen,
- Einheit, Vorzeichen und Skalierung der Leistungs-/Energiewerte,
- absolute oder relative Zeitangaben und deren Bezugszeitpunkt,
- Mindest-/Maximaldauer sowie optionale und verpflichtende Slots,
- Verhalten bei laufendem Verdichter, Warmwasserbereitung und Heizbetrieb,
- Unterstuetzung von Neuauswahl und Preisberechnung,
- Antwort auf eine Sequenzauswahl und nachfolgende State-Transitions,
- Verhalten bei gleichzeitig aktivem LPC-Limit, Disconnect oder Neustart.

Diese Werte duerfen nicht aus Modellnamen oder anderen Herstellern abgeleitet
werden. Bis sie am realen Geraet nachgewiesen sind, bleibt die Auswahl und
Steuerung von OSSHPCF-Sequenzen deaktiviert.

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

Rohmitschnitte koennen private SKIs, Adressen und Seriennummern enthalten.
Sie bleiben im ignorierten Verzeichnis `private/captures/`. Vor einer
Veroeffentlichung muessen Auszuege explizit anonymisiert werden; der Logger
anonymisiert nicht automatisch.

### Capture-Matrix

| Datum | Zustand | Beobachtung | Ergebnis |
|---|---|---|---|
| 2026-07-13 | Idle/Standby, MPC 25 W, externer Zaehler ca. 16 W | Kontrollierter Neustart, 90 Sekunden Beobachtung mit Decoder-Build `14:59:31` | `Comp/OSSHPCF` angekuendigt, SEMP-Subscription auf Entity 2 mit `err=0`; ohne expliziten Read keine initiale Nachricht, daher kein belastbarer SEMP-Zustandsbefund |
| 2026-07-14 | Warmwasserbereitung laeuft, Bosch-MPC 2461-2569 W, externer HP-Zaehler 2505-2566 W | Reconnect mit einmaligem read-only `SmartEnergyManagementPsData`-Read | Subscription und Read `err=0`; Snapshot mit `remote=true`, `single_slot_only=true`, `sequences_max=1`, aber ohne Alternativen, Sequenzen, Slots oder Werte |
| 2026-10-07/08 | 24h mit Betriebsphasen bis 2937 W; Snapshots nur bei 16-23 W | Elf leere Snapshots, zehn API-Unterbrechungen | Kein Flexibilitaetsangebot; kein Read-Nachweis waehrend der hohen Leistungsaufnahme |
| 2026-10-17/18 | Elf Startflanken >50 W, je zehn Reads im 60-s-Abstand | 110 korrelierte leere Antworten, davon 85 bei >500 W; keine API-Abbrueche oder Read-Timeouts | Kein Flexibilitaetsangebot beobachtet; Subscription-Annahme und Push damit nicht nachgewiesen |

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
