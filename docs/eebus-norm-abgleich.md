# EEBUS: Normabgleich und Implementierungsgrenzen

Stand: 2026-11-05. Diese Zuordnung ist ein erster quellcodegestuetzter
Abgleich der aktiven Implementierungen, keine Zertifizierung und kein
vollstaendiger Test aller Normanforderungen.

## Normbestand und Vorgehen

Der lokale Bestand unter `docs/eebus-specifications` umfasst 57 PDFs mit
3.866 Seiten, sieben XLSX-Parametersheets, 77 XSDs, 29 XML-Dateien sowie
eine TXT- und eine XPR-Datei. SHIP 1.0.1 und 1.1.0 sowie SPINE 1.3.0 sind
parallel vorhanden. Die SPINE-Testdokumentation enthaelt auch eine Diff-Version.
Diese Dateien sind nicht unterschiedliche implementierte Use Cases.

Die Inventur umfasst den gesamten Bestand. Inhaltlich wurden fuer die
nachfolgenden Korrekturen LPC TS 1.0.0 und LPC IG 1.1.0 sowie relevante
Abschnitte von MPC TS 1.0.0 und OSSHPCF TS 1.0.0 ausgewertet.
Parametersheets, XML/XSD und die vollstaendigen SHIP-/SPINE-Testkataloge
sind noch nicht feldweise gegen die Implementierung geprueft.

Fuer Anforderungen sind TS, Implementierungsleitfaden und Testspecifikation
zusammen zu betrachten. SHALL/MUST, SHOULD und MAY werden nicht gleichgesetzt.
Insbesondere macht ein MAY zum Verlassen eines Sicherheitszustands das
Beibehalten dieses Zustands nicht automatisch zu einem Normverstoss.

## Zuordnung der Normfamilien

| Normfamilie | Bezug im Projekt | Implementierungsstand / Konsequenz |
| --- | --- | --- |
| SHIP Transport, Connectivity, Pairing, Installation; Versionen 1.0.1/1.1.0 | OpenEEBus SHIP, ESP32-WebSocket/TLS-Port, CS-/EG-Pairing und NVS | Vorhandener Transport. Kein Nachweis vollstaendiger SHIP-1.1.0-Unterstuetzung. Pairing Service ist nicht allein durch einen Pairing-Knopf implementiert. |
| SPINE Protocol/Resources 1.3.0, IG, Testspecifikation 1.1.0 | OpenEEBus Modell, Feature/NodeManagement, ESPHome-Wrapper und SEMP-Dekoder | Vorhandene Bibliotheksunterstuetzung plus eigene Anbindung. Version, Serialisierung, partielle Aenderungen und Ressourcenpflichtfelder muessen je angebotenem Szenario geprueft werden. |
| LPC TS 1.0.0, IG 1.1.0, High-Level-Tests 1.0.2, Parametersheet | `components/eebus_cs`, `components/eebus_eg`, Budgetverteilung | Aktiver CS-/EG-Pfad. Konkrete Korrekturen und verbleibende Luecken unten. |
| LPP TS/IG 1.0.0, High-Level-Tests/Parametersheet 1.0.2 | OpenEEBus enthaelt CS-/EG-LPP-Code | Von unseren ESPHome-Komponenten nicht als eigener Use Case erzeugt. Keine normierte Einspeisebegrenzung allein durch vorhandene Fronius-Steuerung. |
| MPC TS 1.0.0, High-Level-Tests/Parametersheet 1.0.2 | `MaMpcUseCaseCreate`, WP-Gesamtleistungsmessung | MA/MPC angebunden. Wertzustand wird in OpenEEBus gefiltert; Partnerpruefung im ESPHome-Listener korrigiert. Weitere Messszenarien sind nicht durch die Anzeige eines Gesamtleistungswerts nachgewiesen. |
| MGCP TS 1.0.0, High-Level-Tests/Parametersheet 1.0.2 | Lokale Netz-/Modbusmessung; MGCP-Code in OpenEEBus | Keine `GcpMgcpUseCaseCreate`-/`MaMgcpUseCaseCreate`-Anbindung in den CS-/EG-Komponenten. Lokale Netzleistung ist kein MGCP-Angebot. |
| OSSHPCF TS 1.0.0 | SEMP-Client, Capability-Gate, Subscription/Binding und Diagnosen in `eebus_eg` | Lesender Vorbereitungs-/Diagnosepfad. Keine vollstaendige CEM-Szenario-2-Steuerung und kein solcher Konformitaetsanspruch. |
| CEV, EVCEM, EVCS, EVCC, EVSECC, OSEV, OPEV | Lokale Wallbox, Fake-Xemex, lokale Ladehistorie | Sachlich relevant fuer eine zukuenftige EEBUS-EV-Anbindung. Modbus-Regelung und Abrechnung implementieren diese Use Cases nicht. |
| Control/Monitoring of Battery, Monitoring of Inverter/PV String, aggregierte Batterie-/PV-Daten | Fronius GEN24, Batterieprioritaet, lokale Sensoren | Physische Daten vorhanden, aber keine entsprechend angekuendigten EEBUS-Actors/Ressourcen. Aggregierte Darstellungen liegen teilweise nur als RC1 vor. |
| Incentive Table, Time of Use Tariff, Power Demand Forecast, Power Envelope | Lokale Energieoptimierung und Budgetverteilung | Nicht durch lokale Optimierungsheuristiken erfuellt. Erfordern eigene Ressourcen, zeitliche Modelle und Szenariokommunikation. |
| Node Identification | SHIP-/SPINE-Geraeteinformationen | Transportidentitaet und Node-Identification-Use-Case sind gesondert zu pruefen; nicht gleichsetzen. |
| HVAC SystemFunction und Temperature; Heating Area Name | Bosch-WP, lokale 1-Wire-Sensoren | Temperatur-/Heizungsdaten allein implementieren keine HVAC-Use-Cases. Diese liegen als getrennte Monitoring-/Configuration-Szenarien vor. |

## LPC: konkrete Anforderungen und Uebertragung

Quellen:

- [LPC Technical Specification 1.0.0](eebus-specifications/EEBus_UC_TS_LimitationOfPowerConsumption_V1.0.0_public.pdf)
- [LPC Implementation Guide 1.1.0](eebus-specifications/EEBus_UC_IG_LimitationOfPowerConsumption_V1.1.0.pdf)
- [LPC High-Level Test Specification 1.0.2](eebus-specifications/EEBus_UC_HighLevel_TestSpecification_LPC_V1.0.2.pdf)

| Anforderung | Fundstelle | Uebertragung |
| --- | --- | --- |
| Ohne EG-Heartbeat fuer mindestens 120 s: von limited/unlimited-controlled nach Failsafe; unabhaengig von SHIP-Verbindungszustand | LPC-911/912; TS S. 12-13; IG Abschnitt 2.17, S. 19-20 | Firmware nutzt jetzt `LpcState` statt separatem 60-s-Watchdog. Disconnect hebt das Limit nicht auf und setzt den Timer nicht zurueck. |
| In Init/Failsafe erst Heartbeat, dann Limit-Schreiben innerhalb 60 s; andere Konfiguration erst nach dieser Sequenz | LPC-914; TS S. 13; IG Abschnitt 2.14, S. 16-17 | Schreibfreigeber prueft Zustand und Heartbeat vor Annahme. Limit-Schreiben und Reconnect zaehlen nicht als Heartbeat. |
| Heartbeat allein hebt Failsafe nicht unmittelbar auf | LPC-916 und LPC-919/920; TS S. 13 | Heartbeat aktualisiert nur die Kommunikationshistorie. Ein zulaessiges Limit-Schreiben beendet den Failsafe-Zustand. |
| Ein aktives Limit von 0 W ist nicht die Deaktivierung des Limits | LPC-011; TS Ressourcentabelle S. 44-45 | CS behaelt `isLimitActive` getrennt vom Leistungswert. EG-API-Luecke siehe unten. |
| Ablauf einer befristeten Begrenzung wechselt von limited nach unlimited-controlled | LPC-908; TS S. 12 | C-Listener reicht die Dauer an den Zustand weiter. Laufzeitpruefung hebt die physisch angewandte Begrenzung bei Ablauf auf. |
| Beim Ueberschreiben eines befristeten Limits mit einem unbefristeten Schreiben muss die alte `endTime` explizit geloescht werden | TS Abschnitt 3.4.1.4, S. 65-66 | EG setzt `LoadLimit.delete_duration=true`; vorhandene OpenEEBus-Serialisierung erzeugt den passenden Delete-Filter. |
| Abgewiesene Limits: result/NACK mit Fehlernummer 7 | LPC-003/1; TS Abschnitt 3.4.1.4, S. 65; IG 2.14 | Vorhandener OpenEEBus-Approver wird verwendet; ungueltige Werte/Fristen/Partner werden vor Commit abgelehnt. |
| Failsafe-Leistung muss vorkonfiguriert und beschreibbar sein | LPC-021/1; TS S. 21; IG 2.15, S. 17-18 | Konfigurierter CS-Failsafe wird auch in der SPINE-Ressource initialisiert; `isValueChangeable=true`. |
| Vorkonfigurierte Failsafe-Dauer und EG-Werte im Bereich 2-24 h | LPC-022/1-3; TS S. 21 | Zwei Stunden Anfangswert, beschreibbar; ungueltige eingehende Bereiche werden abgelehnt. Maximal akzeptierter Wert ist 24 h. |
| Zusaetzliche Pflichten bei Ablehnung oberhalb eines niedrigeren Herstellermaximums | LPC-022/4-5; TS S. 21-22 | Kein niedrigeres Herstellermaximum eingefuehrt. Sonderfall und Ergebnis-/Notify-Sequenz trotzdem im Integrationstest abdecken. |

`LpcState` ist fuer Empfangsfristen, aktive Nullgrenze, Dauerablauf und
Millisekunden-Ueberlauf mit Hosttests abgesichert. Das ist kein Test der
gesamten SHIP/SPINE-Nachrichtensequenz oder einer echten Steuerbox.

### Bewusste Optionen und noch offene LPC-Punkte

- Init nach 120 s autonom freigeben ist ein MAY (LPC-906, TS S. 11).
  Der Zustand bleibt derzeit konservativ in Init, bis ein zulaessiges
  Heartbeat-/Limit-Paar verarbeitet wird.
- Das automatische Verlassen von Failsafe nach Ablauf der Minimum-Dauer
  ist keine Pflicht. Der konfigurierte Wert ist im Protokoll beschreibbar;
  die aktuelle Betriebsstrategie bleibt bis zur gueltigen Wiederaufnahme
  begrenzt. Ein automatischer Autonomiebetrieb ist nicht implementiert.
- Der Start des Zustandautomaten erfolgt noch am SHIP-Beziehungsaufbau.
  Init und physischer Failsafe nach jedem Geraeteneustart sind getrennt
  zu pruefen, insbesondere bei gespeicherter SKI und abwesender Steuerbox.
- EG `set_limit(0)` bedeutet historisch Freigabe. Fuer ein normiertes
  aktives 0-W-Limit braucht der EG-Pfad eine explizite Aktivkennung und
  eine entsprechende Anpassung der aufrufenden Budgetverteilung.
- Die bestehende EG-Anhebung auf eine geraetespezifische Mindestleistung
  ist eine lokale Kompatibilitaetsstrategie, keine allgemeine LPC-Regel.
  Eine Verpflichtungsgrenze darf nicht stillschweigend als eingehalten
  gelten, wenn stattdessen ein hoeherer Wert gesendet wurde.
- EG-Reconnect muss zuerst den aktuell gueltigen zugeteilten Budgetwert
  nach Heartbeat senden. Die bisherigen YAML-Reconnect-Lambdas senden den
  Gesamt-CS-Wert je EG; das ist nicht der Nachweis einer korrekten
  aggregierten Budgetverteilung. Kein vorsorgliches Freigeben vor einem
  weiterhin erforderlichen Limit (IG Abschnitt 2.13, S. 16).
- EG-Failsafe-Schreiben werden bislang anhand des lokalen API-Erfolgs
  als konfiguriert markiert. ACK/NACK und anschliessender Ressourcenwert
  muessen fuer einen belastbaren Konfigurationsnachweis korreliert werden.
- Physischer Zustand und publizierte LoadControl-Ressource sind bei
  Dauerablauf/Failsafe-Umschaltung noch zusammen zu testen. Ein lokal
  freigegebenes Relais allein beweist keine richtige Read/Notify-Antwort.
- Partielle Writes und kombinierte Delete/Partial-Filter muessen mit
  der kompletten Nachricht getestet werden. Einem Feld- oder Listen-Update
  darf keine ungepruefte Zweitposition folgen.
- Calendar-Dauern mit Jahren/Monaten und nicht darstellbare Laufzeiten
  werden derzeit abgelehnt. Unterstuetzte relative Dauern werden ohne
  Millisekundenverlust verarbeitet. Zulaessige, aber technisch nicht
  anwendbare Befehle sind separat von syntaktisch ungueltigen Befehlen
  zu pruefen, einschliesslich der Uebergaenge nach LPC-918.

## MPC und OSSHPCF

[MPC TS 1.0.0](eebus-specifications/EEBus_UC_TS_MonitoringOfPowerConsumption_V1.0.0_public.pdf):
MPC-001 definiert die Verbrauchervorzeichenkonvention (S. 14). MPC-003
verlangt, `outOfRange` und `error` nicht als gueltige Werte zu verwenden
(S. 15 und 29). MPC-011 ordnet Gesamtleistung ueber Measurement-ID, Typ,
Commodity, Einheit W und Scope `acPowerTotal` zu (S. 31-32).

OpenEEBus `ma_measurement_base.c` prueft den Wertzustand vor der Ausgabe.
Der ESPHome-MPC-Listener verwendet nun auch die vorhandene Partnerpruefung
und verwirft nicht-endliche dekodierte Werte. Erhalten bleiben normale
Nullwerte und das Vorzeichen; ein ungueltiger Messwert wird nicht in
einen scheinbar gueltigen 0-W-Wert umgewandelt. Geraete-/Entity-Hierarchie,
Einheiten, veraltete Werte und alle angebotenen Szenarien bleiben
Gegenstand der Integrationstests.

[OSSHPCF TS 1.0.0](eebus-specifications/EEBus_UC_TS_OptimizationOfSelfConsumptionByHeatPumpCompressorFlexibility_V1.0.0_public.pdf):
Szenario 1 beobachtet Flexibilitaet (Abschnitt 3.4.1, S. 49-51),
Szenario 2 steuert sie auf dieser Basis und braucht Binding
(Abschnitt 3.4.2, S. 52-53). Ein erfolgreiches Binding allein steuert
keine Waermepumpe. PowerSequence-Zustand, Schedule, Constraints und Slots
stehen in den Ressourcentabellen S. 42-45. Partielle Notifications sind
fuer Szenario 1 Pflicht (S. 51).

Der bestehende Capability-/Registrierungspfad und die lesende Anzeige
bleiben erhalten. Keine Start-/Pause-/Resume-/Stop-Kommandos, keine
vollstaendige CEM-OSSHPCF-Ankuendigung und keine Komfort-/Temperatursteuerung
wurden aus diesem Abgleich heraus aktiviert. Siehe auch
[Bosch OSSHPCF](oss-hpcf-bosch.md).

## Validierung dieses Aenderungsstands

- ESPHome 2026.5.3 / ESP-IDF 5.5.4: Firmware kompiliert und gelinkt,
  bestaetigter Build 2026-11-05 08:32:26 +0100, `config_hash=0xd7ca935f`.
  Nach Bereinigung der alten Buildartefakte endete der beobachtete
  Docker-Build mit SUCCESS.
- Auf ausdruecklichen Auftrag anschliessend sauber neu gebaut und per OTA
  auf `192.168.178.24` uebertragen. Build-Info: 2026-11-05 08:38:12 +0100,
  gleicher Konfigurationshash. Das laufende HEMS bestaetigte am 2026-11-05
  um 08:43:58 den C++-Build-Zeitstempel `Nov 5 2026 08:42:12`.
  Build-Info bei Generierung und C++-Kompilierzeit sind verschiedene Werte.
  EG1 war bei diesem ersten Statusabruf noch im mDNS-Wiederanlauf;
  dies ist kein Hardware-Konformitaetsnachweis.
- Vier Hosttests bestanden: `lpc_state`, `semp_decoder`, `solar_policy`,
  `xemex_control`. Der LPC-Test wurde mit den erweiterten Grenzen neu gebaut.
- Die Hosttests ersetzen keine SPINE-Nachrichtentests der neuen
  CS-Approver-Anbindung. Der Firmware-Build prueft deren Typen und Linkage,
  nicht die ACK/NACK-Reihenfolge zur Laufzeit.

## Naechste Nachweise

1. CS/EG-Nachrichtenpfade im Integrationstest
   pruefen: NACK vor Commit, Doppel-Callbacks fuer Failsafe-Writes,
   Herkunftspruefung, Dauer, Read/Notify und Heartbeat-Recovery.
2. EG-Wiederanlauf, aktive Nullgrenze und aggregierte Budgetverteilung
   vor einer Hardwarefreigabe korrigieren und testen.
3. Parametersheets feldweise ausfuellen; Szenario, optionale Elemente,
   Herstellergrenzen und tatsaechliche Versionen verzeichnen.
4. SHIP 1.1.0 gegen aktuelle Zustandsmaschine/ESP32-TLS-Port und SPINE
   1.3.0 gegen Modell/Serialisierung/XSD und die jeweiligen Tests pruefen.
5. LPC-/MPC-High-Level-Tests mit echtem Gegenueber ausfuehren. Neue
   Use Cases nur mit passenden Actors, Ressourcen und physischem
   Mess-/Steuervertrag anbieten.

Keine Normdatei wurde geaendert, kein neues Geraet gepairt, keine neue
EEBUS-Use-Case-Steuerfunktion aktiviert. Die korrigierte Firmware wurde
auf anschliessenden ausdruecklichen Auftrag per OTA installiert; die
offenen Laufzeit- und Hardware-Nachweise bleiben davon unberuehrt.