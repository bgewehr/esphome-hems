# Konzept: §14a-Leistungsbudget-Verteilung im HEMS

Wie entscheidet der Betreiber des HEMS, welches Gerät wie viel Leistung aus dem
Budget bekommt, das der Verteilnetzbetreiber (VNB) vorgibt?

## 1. Regulatorischer Rahmen (EnWG §14a, BNetzA-Festlegungen)

Die für die Verteilung relevanten Regeln aus §14a EnWG und den
BNetzA-Festlegungen (BK6-22-300 „netzorientierte Steuerung", BK8-22/010-A
Netzentgelte):

1. **Nur steuerbare Verbrauchseinrichtungen (SteuVE) werden gedimmt.**
   SteuVE sind Wärmepumpe, Wallbox, Batteriespeicher (Netzbezug) und Klima-
   anlagen ≥ 4,2 kW Anschlussleistung. Der **unsteuerbare Haushaltsverbrauch
   ist ausgenommen** — er zählt nicht gegen das Budget und darf nicht
   eingeschränkt werden.

2. **Das Limit gilt für die *netzwirksame* Leistung der SteuVE.**
   Eigenerzeugung darf saldiert werden: Was PV (oder die entladende
   Hausbatterie) im Moment liefert, dürfen die SteuVE **zusätzlich** zum
   VNB-Limit verbrauchen. Formal:

   ```
   verfügbarer Erzeugungsüberschuss = max(0, P_PV + P_Batt,Entladung − P_Haus)
   netzwirksame SteuVE-Leistung = max(0, Σ P_SteuVE − verfügbarer Erzeugungsüberschuss)
   Bedingung:  netzwirksame SteuVE-Leistung ≤ P_Limit(VNB)
   ```

3. **Mit EMS gilt ein einziges aggregiertes Limit.**
   Bei Direktansteuerung müsste jede SteuVE einzeln auf mindestens 4,2 kW
   dimmbar sein. Steuert ein EMS (unser HEMS als EEBus-CS), schickt die
   Steuerbox des VNB **einen** Summenwert (LPC `loadControlLimitListData`).
   Wie sich dieser Wert aus Anzahl der SteuVE und Gleichzeitigkeitsfaktoren
   berechnet, ist Sache des VNB — **das HEMS verteilt nur, was ankommt.**
   Garantiert ist: das Limit beträgt nie weniger als 4,2 kW.

4. **Zeitverhalten:** Die Begrenzung muss innerhalb weniger Minuten wirksam
   sein (Festlegung: Umsetzung „unverzüglich", Toleranzband ~2 min für
   Regelabweichungen). Bei Ausfall der Steuerbox-Verbindung gilt der
   vereinbarte Failsafe-Wert (hier: 4200 W, bereits implementiert).

## 2. Ist-Zustand im esphome-hems (Stand 2026-07-19)

Der Prioritätsverteiler in `netzdienliche-steuerung.yaml` berechnet bei
aktivem CS-Limit und alle 30 Sekunden `P_Limit + 0,8 × PV-Überschuss`. Der
PV-Überschuss ist die positive Differenz aus Solarleistung und unsteuerbarem
Hausverbrauch. Anschließend sortiert der Verteiler Wärmepumpe, lokalen
EV-Ladepfad und Wallbox nach Betreiberpriorität und verteilt atomare Sockel
sowie Restbudget bis zum jeweiligen Deckel. Die Parameter Priorität, Sockel
und Maximum sind persistente ESPHome-Entities. Der aktuelle Verteilstand wird
als Textsensor ausgegeben.

Noch nicht vollständig umgesetzt sind:

- explizite Quantisierung auf zulässige ganzzahlige Ladestromstufen,
- erzwungenes Neusenden nach 60 Sekunden unabhängig von der 100-W-Hysterese,
- Closed-Loop-Prüfung anhand der gemessenen netzwirksamen SteuVE-Leistung,
- strukturierte Diagnose ungueltiger Betreiberparameter im Geraeteadapter.

Die fuer den Erzeugungszuschlag verwendeten Leistungswerte tragen echte
Quellzeitstempel: PowerFlow beziehungsweise beide MPPT-Kanaele fuer Solar sowie
AC, Grid, EV und HP fuer die abgeleitete Hausleistung. Ein Wert ist nur bei
gueltigem Zeitstempel, hoechstens 10 s Alter, endlichem Zahlenwert und
plausiblem Wertebereich verwendbar. Ein alter oder ungueltiger Solar-/Hauswert
setzt ausschliesslich den PV-Zuschlag auf 0 W; das gueltige VNB-Basislimit bleibt
wirksam. Ein ungueltiges VNB-Limit erzeugt konservativ 0 W Gesamtbudget.

`Fronius Battery Power` kombiniert die getrennten DC-Rohwerte mit dem
dokumentierten Vorzeichen `Entladung - Ladung`; positive Werte sind Entladung.
Nur wenn beide Rohwerte hoechstens 10 s alt, endlich und im Bereich 0 bis
25 kW liegen, wird die positive Entladung zum Erzeugungsueberschuss addiert.
Ein negativer Wert (Laden) und jeder ungueltige Batteriewert erhoehen das Budget
nicht. Die bestehende 80-%-Sicherheitsmarge gilt fuer den gemeinsamen
Erzeugungsueberschuss aus PV und Batterieentladung.

Der bestehende Verteiler ist damit eine funktionsfähige erste Stufe, aber
noch kein vollständig verifizierter Compliance-Regelkreis.

## 3. Budget-Modell

### PV- und Batteriepriorisierung

Stand 2026-10-07: `battery-priority.yaml` integriert den host-getesteten
Regelkern aus `components/power_distribution/solar_policy.h`. Die vier neuen
Schalter sind persistent und standardmaessig aus. Der Regelkern ist jetzt
mit Storage-Schreibpfad, Xemex-Freigabe und Budgetverteilung verbunden.
Die erste Dry-Run-Firmware wurde am 2026-10-07 um 15:23 Uhr OTA ausgerollt
(Build Time `Oct 7 2026 15:20:40`). Der aktive Korrekturbuild wurde um
16:17 Uhr OTA installiert, Build Time `Oct 7 2026 16:17:01` live bestaetigt.
Die Hardware-Abnahme ist noch nicht vollstaendig. Die Home-Assistant-Karte `Batterie (Reserva)` unter
`http://mediasafe2:8123/energie-management/uebersicht` wurde angepasst und
nach Neuladen geprueft: `Charge Battery first` steht direkt ueber
`Charge EV from Battery`, die beiden Solar-Schalter direkt darunter.
Verwendet werden die nativen ESPHome-Entities desselben Geraets, nicht die
zusaetzlich vorhandenen MQTT-Entities. Schaltzustaende wurden dabei nicht geaendert.

Berechnung und vorlaeufige Annahmen:

- `Charge EV with solar limit` wirkt nur zusammen mit eingeschaltetem
   `Charge EV with solar support only`: Nach PV-Freigabe wird die angeforderte
   Leistung auf das gemeinsame stabile Xemex-Minimum 5544 W (8 A bei der
   verwendeten 693-W/A-Umrechnung) begrenzt. Nutzerwunsch und §14a-Limit werden
   niemals angehoben; unterhalb des Minimums bleibt die bestehende Ladepause.
   Ausgeschaltet bleibt die normale Leistung aus Nutzerwunsch/§14a verfuegbar,
   nicht zwingend die Nennleistung der Wallbox. PV-Pause, Storage-Schutz und
   Ladeabbruch-Verriegelung bleiben vorrangig. Bei qualifizierter
   Low-Solar-Netzfreigabe entfaellt das Solar-Minimum auf Betreiberwunsch:
   Es gilt die normale Leistung aus Nutzerwunsch/§14a und Schutzfunktionen.
   Der eingeschaltete Low-Solar-Schalter allein reicht dafuer nicht aus.
   Im PV-Support-Modus gilt das Solar-Limit wieder, auch bei ergaenzendem
   Netzbezug. Umschalten dieses reinen
   Leistungslimits setzt die PV-Freigabezeiten nicht zurueck.
   Einheitliche 500-W-Rest-PV-Schwelle und Netzfreigabe ohne Solar-Limit:
   OTA am 2026-10-07 um 16:57 Uhr, Live-Build `Oct 7 2026 16:56:18`.
   Alle drei Host-Tests und Firmware-Build erfolgreich. Schalter und
   Nutzerlimit 11500 W erhalten; Solar-only war beim Update aus.
   Der zeitgesteuerte Low-Solar-Uebergang wurde im Host-Test geprueft,
   mit diesen Live-Einstellungen jedoch nicht ausgeloest.
   Der Schalter ist persistent, standardmaessig aus und steht in HA direkt
   unter Solar-only. OTA am 2026-10-07 um 16:30 Uhr, Live-Build 16:29:35;
   Host-Tests bestanden, HA-Konfiguration zurueckgelesen. Aktuelle Schalter
   und Nutzerlimit 5600 W unveraendert; physische Minimum-Regelguete nicht neu
   vermessen. Ein hoeherer PV-Eigenverbrauch ueber die gesamte Ladung ist
   wetter- und ladezeitabhaengig, nicht durch das Leistungslimit garantiert.
- PV-Erzeugung bleibt als separat validierte Eingangsgroesse erhalten.
   Fuer die Leistungsbilanz gilt vorlaeufig `PV_AC = 0,95 * PV_DC`.
   Nicht-EV-Last ist Haus + Waermepumpe + Ohmpilot; EV-Verbrauch wird nicht
   abgezogen und kann deshalb eine bestehende Freigabe nicht selbst aufheben.
- Tatsaechliche Batterieladung wird mit `Ladung_DC / 0,95` als AC-Aequivalent
   reserviert. Das ist eine konservative Rechenannahme, kein gemessener
   Wirkungsgrad und kein validiertes Hybridwechselrichter-Modell.
- Bei Batterieprioritaet ist das Ziel der positive PV-Rest, begrenzt durch
   eine bekannte Ladeaufnahme. Der reale Adapter kennt bisher nur den frischen
   Status FULL als Aufnahmegrenze 0 W. Sonst bleibt die Aufnahme unbekannt;
   vorsorglich wird der gesamte PV-Rest reserviert. `WChaMax` ist nur die
   Bezugsleistung fuer Prozentwerte, **keine dynamische BMS-Ladegrenze**.
- EV-Unterstuetzung ist der PV-Rest nach priorisierter Ladung. Ohne Prioritaet
   darf natuerliche Batterieladung fuer den EV-Start verdraengt werden;
   sie bleibt trotzdem in der diagnostizierten Budgetreservierung enthalten.
   Diese Reservierung ist `max(Istladung, Ladeziel)`, nicht deren Summe.
- Einheitliche Schwelle auf Betreiberwunsch: Beide Modi bewerten die
   verfuegbare EV-PV-Unterstuetzung, nicht die gesamte PV-Erzeugung.
   PV-Freigabe: ueber 500 W fuer 60 s; Pause: unter 500 W fuer 120 s.
   Low Solar: unter 500 W fuer 10 min; Rueckkehr: ueber 500 W fuer 5 min.
   Genau 500 W erhaelt bestehende Freigaben und setzt laufende
   Qualifikationszeiten zurueck. Hauslast und Batterieprioritaet koennen
   damit auch bei hoher Erzeugung die Low-Solar-Netzfreigabe ausloesen.
   Das gilt auch an dunklen Tagen, nicht nur nachts.
   Low Solar hebt allein eine PV-Pause auf, keinen
   manuellen Stopp. Netzbezug ist erlaubt, nicht als einzige Energiequelle
   vorgeschrieben; bestehende Batterie-Entladeberechtigungen bleiben relevant.
- Quellen muessen endlich, plausibel und maximal 10 s alt sein. Zeitstempel
   werden nicht durch zyklisches Wiederholen eines Wertes erneuert.
   Fehlende oder null-PV-Felder der Solar API gelten nicht als 0 W; dann kann
   nur ein frischer MPPT-Fallback eine gueltige Erzeugung liefern. Ungueltige
   Ohmpilot-Felder setzen dessen Quellzeitstempel zurueck. Der aktuelle
   Ohmpilot-Poll wurde von 10 s auf 5 s verkuerzt; das Frischefenster bleibt
   bei 10 s. Bei ungueltigen Daten bleibt Solar-only gesperrt.
- Ungueltige Daten, Moduswechsel und Ausfuehrungsluecken ueber 10 s setzen
   die Freigabequalifikation zurueck. Ohne Solar-only entsteht kein neuer Stopp.
   Der Sollwertsensor `Battery Priority Charge Target` zeigt DC-Watt,
   EV-Unterstuetzung und Budgetreservierung zeigen AC-Aequivalente.
   `Battery Priority OutWRte` zeigt die negative Prozentanforderung fuer ein
   positives Ladeziel mit frischen Metadaten, sonst unbekannt. Ohne gueltiges
   Ladeziel kehrt der Schreiber zur bestehenden Entladepolitik zurueck.
   Bei Kommunikationsfehlern wird nicht blind weitergeschrieben: der Schreiber
   verriegelt, versucht Modus 0 und benoetigt nach Pruefung einen Neustart.
   Zuvor bestaetigte Stellwerte laufen nach 15 s Schreibinaktivitaet aus.
   Ist EV-Batterieversorgung nicht erlaubt, sperrt die EV-Freigabe bei
   Schreiberfehler oder mehr als 10 s alter/fehlender Storage-Ruecklesung.
   Diese Sperre veraendert den Nutzer-Sollwert nicht. Sie ist kein garantierter
   physischer Ladestopp vor WR-Rueckfall: Die Wallbox reagiert zeitverzoegert.

Lesender Geraetecheck am 2026-10-07: Model 124 bei Adresse 40343, Laenge 24;
`WChaMax = 18176 W`, dessen SF 0, `InOutWRte_SF = -2`, SOC 72 %, `ChaSt = 4`
(CHARGING), `StorCtl_Mod = 0`, `OutWRte = 0 %`, `InWRte = 100 %`.
`InOutWRte_RvrtTms = 0`; dies beweist weder Unterstuetzung noch Verhalten eines
gesetzten Timeouts. `ChaGriSet = 1` erlaubt Netzladung auf Modbus-Seite;
die zusaetzliche Fronius-Webeinstellung wurde anschliessend unter
`/app/soc-settings` im Customer-Zugang lesend bestaetigt: "Battery charging
from other sources" ist eingeschaltet, ausgewaehlt ist "from other generators
in the home network and from public grid". Fuer diese Ansicht ist kein
Technician-Zugang erforderlich. Beide Netzladefreigaben sind damit aktiv;
eine negative `OutWRte` ist dadurch nicht automatisch auf PV-Ladung begrenzt.
Die Ansicht zeigt manuellen SoC-Modus, Minimum 5 %, Maximum 95 %, Reserve 20 %
und Warnschwelle 7 %. Ob die obere Grenze als `ChaSt = FULL` gemeldet wird,
ist noch zu pruefen; eine reine 100-%-SoC-Erkennung waere unzureichend.
WinTms und RmpTms lieferten 65535 (nicht implementiert).
Es wurden keine Register oder Webeinstellungen geaendert.

Wartungsstand nach OTA: Der bestehende Einstellwert `Battery Max Discharge
Power` wurde auf Betreiberwunsch von 18200 W auf die erneut gelesenen
18176 W gesetzt und live zurueckgelesen. Dieselbe Entity verwendet jetzt
1-W-Schritte; es gibt keinen zweiten Einstellwert. Die Bosch-Aufzeichnung
wurde durch den Neustart unterbrochen und hat sich danach wieder verbunden.

Die Firmware bietet eine manuelle Storage-Schreibpause von maximal 180 s.
Bei Ablauf, Resume oder Neustart werden Modus 0 und OutWRte 0 eingereiht.
`Automatic` bestaetigt nur den lokalen Zustand, nicht die Modbus-Quittierung;
die Pause ist kein Nachweis fuer einen autonomen WR-Rueckfall.
`tools/diagnostics/test_fronius_storage_revert.py` prueft beaufsichtigt
15 s Rueckfallzeit mit OutWRte 0 %, ohne negative Ladeanforderung, und
stellt geaenderte Testregister anschliessend mit Ruecklesung wieder her.
Bei Kommunikationsausfall kann die Wiederherstellung nicht garantiert werden.
Der Test prueft Schreibinaktivitaet bei weiterhin laufenden Modbus-Lesezugriffen,
nicht einen vollstaendigen Kommunikationsausfall.
Der Versuch am 2026-10-07 wurde bereits vor Pause und Test-Schreibbefehlen
abgebrochen: Die bestehende Regelung hatte inzwischen `StorCtl_Mod = 2`
mit `OutWRte = 0 %` gesetzt. Dieser aktive Entladeschutz wurde nicht
uebergangen. `RvrtTms` blieb 0, der Schreiber `Automatic`;
das Rueckfallverhalten war damit zunaechst unbewiesen.

Nach erneuter Freigabe wurde der beaufsichtigte Test fuer die vorhandene
Entladesperre und CHARGING/HOLDING bei SOC 20..90 % angepasst. Am 2026-10-07
15:27:07 wurde `RvrtTms = 15` geschrieben und bestaetigt. Bei 14,57 s lag
Modus 2 noch an, bei 16,64 s meldete der WR eigenstaendig Modus 0.
HEMS blieb waehrenddessen pausiert. Danach wurden Modus 2, OutWRte 0 %,
InWRte 100 % und RvrtTms 0 zurueckgelesen; um 15:27:28 war HEMS wieder
`Automatic`. Ergebnis: **Rueckfall bei Schreibinaktivitaet nachgewiesen**.
Privates Messprotokoll: `private/captures/fronius-revert-20261007-152655.jsonl`.
Das ist kein physischer Netzwerkausfall- oder ESP-Neustart-Test.

Aktiver Schreibpfad: Rueckfallzeit lesen, gegebenenfalls 15 s schreiben und
zuruecklesen; OutWRte quittieren lassen, dann Modus setzen und Modus/Rate/
Rueckfallzeit zusammen zuruecklesen. Fehler oder ausbleibende Quittierung
verriegeln den Schreiber. Solar-Pause liegt vor dem Xemex-Schutzautomaten;
Nutzerwunsch, DI1, Simulation und Schutzsperren bleiben massgeblich.
Bei §14a wird Ladung einmal vor der PV-Gutschrift reserviert; nicht durch
Erzeugung gedeckte Ladung reduziert zusaetzlich das verfuegbare Netzbudget.

Live-Fehler und Korrekturen am 2026-10-07:

- Build 16:00:25 verriegelte den Schreiber wegen abgelaufener Transaktion.
   Danach versorgte die Batterie trotz ausgeschalteter EV-Berechtigung das
   Auto mit etwa 5,6 kW. Beaufsichtigter Schutzstopp: Nutzerlimit 6000 -> 0 W,
   Stillstand und erneute Batterieladung bestaetigt.
- Die explizite Timeout-Lesung ab 40358 kollidierte mit dem Sensor-Poll im
   deduplizierenden Controller; die Rueckruffunktion kann dabei verloren gehen.
   Die Pruefung liest jetzt zwei Register ab 40357 mit eigenem Auftragsschluessel.
- Build 16:10:22 schrieb negative OutWRte und RvrtTms 15 korrekt, pausierte EV
   aber zeitweise wegen fehlender aktueller Bestaetigung: Schon kleine sinkende
   PV-Ziele brachen laufende Transaktionen ab. Build 16:17:01 bestaetigt einen
   begonnenen Ladeauftrag auch bei solchen Zielaenderungen; Abschalten, veraltete
   Daten und ungueltige Ziele brechen ihn weiterhin ab. Naechster Zyklus uebernimmt
   das neue Ziel. Host-Regressionstests und Firmware-Build bestanden.
- Die vorherigen 6000 W wurden nach negativem OutWRte/Modus-2/15-s-Readback
   wiederhergestellt. `Automatic` allein reicht als Nachweis nicht.
- Beaufsichtigte Live-Schalttests 16:19..16:20 bestanden: Batterieprioritaet,
   Prioritaet aus und wieder an, Solar-Pause bis Stillstand (16,50 s) und
   Wiederanlauf nach Abschalten von Solar-only (29,87 s). Nutzerlimit blieb
   6000 W, Ladeabbruchzaehler 0. Batterie entlud in den erfassten Samples nicht.
   Ende: Prioritaet an, Solar-only aus, Low-Solar-Ausnahme aus, EV laedt.
   Protokoll: `private/captures/solar-policy-20261007-161906.jsonl`.
   Der Test bestaetigt weder stationaere 6-kW-Regelguete noch die zeitgesteuerte
   Low-Solar-Ausnahme, BMS-Ladebegrenzung oder physischen Kommunikationsverlust.

Verbleibende Hardware-Abnahme:

1. Rueckfall bei Schreibinaktivitaet ist nachgewiesen; zusaetzliche Abnahme
   bei physischem Kommunikationsverlust und ESP-Neustart bleibt offen.
2. Wirkung der bestaetigten Netzladeberechtigungen, BMS-/Temperatur-/SOC-Grenzen und begrenzte
    Ladeaufnahme verifizieren; keine Freigabe allein aus `WChaMax` ableiten.
3. AC/DC-Modell, Messlatenz und Sicherheitsreserve unter Last pruefen.
4. Einziger Storage-Schreibpfad: neuen Sollwert vor Aktivierung setzen,
    Ruecklesung und negative Sollwerte absichern; bei Nacht oder Abschalten
    bestehende Entladeberechtigungen wiederherstellen, nicht pauschal sperren.
5. PV-Pause vor dem Xemex-Schutzautomaten anwenden, Nutzerlimit unveraendert
    lassen; Wiederanlauf darf Schutzsperren, Fahrzeugfreigabe oder §14a nicht umgehen.
6. Batterie-Ladereservierung im aktiven Budget genau einmal beruecksichtigen
    und ungenutztes EV-Budget neu verteilen; Aufhebung eines CS-Limits darf
    keine PV-Pause aufheben. Abschliessend Hardware-Abnahme aller Kombinationen.

Bei aktivem Limit berechnet das HEMS zyklisch (alle 10 s und bei jeder
Limit-/PV-Änderung):

```
PV_Überschuss = max(0, P_PV_aktuell - P_Haus)
Budget = P_Limit + 0,8 × PV_Überschuss

# Künftig zusätzlich:
PV_Überschuss = max(0, P_PV_aktuell + P_Batt_Entladung - P_Haus)
```

Messgrößen sind vorhanden: Fronius Solar Power, Battery Power, Grid Power,
WP-Leistung (EG1 MPC), Wallbox-Leistung (EG2 MPC), EV-Ladeleistung (Fronius).

## 4. Verteilungsentscheidung des Betreibers

Der Betreiber konfiguriert **pro Gerät drei Werte** in der ESPHome-UI
(NVS-persistent, wie die vorhandenen Failsafe-Numbers):

| Parameter | Bedeutung | Beispiel WP | Beispiel Wallbox | Beispiel EV (Fronius) |
|---|---|---|---|---|
| **Priorität** (1 = höchste) | Reihenfolge bei Knappheit | 1 | 3 | 2 |
| **Sockel** (optional) | atomare technische/komfortbezogene Zuteilung; reicht das Budget nicht vollständig, erhält das Gerät 0 | 4200 W¹ | 0 W | 1380 W (6 A) |
| **P_max** | Deckel, mehr wird nie zugeteilt | 9000 W | 11000 W | 11000 W |

¹ Der K40RF-Hardwaretest vom 20.07.2026 bestaetigt die Grenze: 4.200 W wurden
per LPC ACK angenommen; 4.000, 3.600, 3.200, 2.800, 2.400 und 2.000 W blieben
jeweils ohne ACK. Der im Component als `min_limit_w` hinterlegte Sockel von
4.200 W bleibt daher erforderlich.

**Verteilalgorithmus (strikte Priorität mit Sockeln — empfohlen):**

```
1. Sortiere verbundene steuerbare Geräte nach Priorität.
2. Runde 1 (Sockel): Teile jedem Gerät der Reihe nach seinen Sockel zu,
   solange Budget vorhanden. Reicht das Budget nicht einmal für alle
   Sockel, bekommen die niedrig priorisierten Geräte 0 (Wallbox pausiert,
   EV-Laden stoppt) — die WP bekommt als Prio 1 immer ihre 4200 W
   (das VNB-Limit ist nie kleiner).
3. Runde 2 (Rest): Verteile das Restbudget in Prioritätsreihenfolge
   bis P_max je Gerät.
4. Quantisierung: Wallbox/EV auf Ladestrom-Stufen abrunden
   (230 V × Phasen × ganze Ampere; unter 6 A → 0 / Pause).
5. Anwenden:  EG1.set_limit(share_WP)
              EG2.set_limit(share_Wallbox)
              EV-Leistungsbegrenzungs-Slider = share_EV

   Der vorhandene Slider-Handler ist der einzige Ort, der den EV-Leistungswert
   in Xemex-Addonwerte umrechnet. Nach Ende des §14a-Eingriffs wird der zuvor
   vom Benutzer eingestellte Sliderwert wiederhergestellt.
```

**Alternative Strategien** (per Select wählbar, wenn gewünscht):
- *Proportional*: Budget im Verhältnis der P_max verteilen — fühlt sich
  „fair" an, führt aber dazu, dass kein Gerät richtig arbeitet.
- *Zeitscheiben*: bei Dauerknappheit WP und EV-Laden abwechselnd bedienen —
  nur sinnvoll für lange Limits (> 1 h), erhöht Schaltspiele.

Strikte Priorität ist deterministisch, für den VNB-Nachweis auditierbar und
entspricht dem üblichen Komfortempfinden (Heizen vor Laden).

## 5. Regelkreis und Toleranzen

- **Nachführung:** Neue Zuteilung nur senden, wenn sich der Anteil eines
  Geräts um > 100 W ändert oder 60 s vergangen sind (LPC-Schreibrate schonen,
  ACK-Retry existiert bereits).
- **Verifikation:** `netzwirksame SteuVE-Leistung` aus Messwerten berechnen.
  Liegt sie > 2 min über `P_Limit` (z. B. weil ein Gerät sein Limit ignoriert),
  Anteile stufenweise um 10 % kürzen und Ereignis loggen.
- **PV-Einbruch:** Wolkenzug oder steigender Hausverbrauch reduziert den
   Überschuss sofort. Nur 80 % des nach Hausverbrauch verbleibenden
   PV-Überschusses werden angerechnet; eine Zeit-/Qualitätsprüfung bleibt als
   weitere Absicherung vorgesehen.
  (Sicherheitsmarge gegen Überschreitung).

## 6. Failsafe-Verhalten (Verbindung zur Steuerbox verloren)

Bereits implementiert und vom Konzept unverändert: Jede EG-Instanz kennt
ihren Failsafe (4200 W / 7200 s). Bei Heartbeat-Verlust der CS-Verbindung:

- WP → 4200 W (entspricht gesetzlichem Minimum)
- Wallbox → 0 W (Prio-3-Gerät verzichtet)
- EV-Laden → `ladebegrenzung_addon` (bestehender Mechanismus)
- Verteil-Logik pausiert, bis die Steuerbox wieder verbunden ist.

## 7. Entwicklungsplan

Die Aufgaben-IDs werden auch in der zentralen [TODO-Liste](../TODO.md)
verwendet. Eine Phase beginnt erst, wenn die Abnahmekriterien der vorherigen
Phase erfüllt sind.

### Phase 0: Vorhandene Basis

- **BD-00 (erledigt):** Prioritätsverteiler für EG1, EV und EG2 inklusive
   Betreiberparametern, 80-%-Anrechnung des PV-Überschusses nach Hausverbrauch,
   100-W-Hysterese und Statusanzeige.
- **BD-01 (erledigt):** Konfigurierbares `min_limit_w` und
   Fremdgeräte-Guard in den EEBus-EG-Instanzen.

### Phase 1: Testbarer Allokationskern

- **BD-10:** Verteilalgorithmus spaeter aus der YAML-Lambda in einen reinen
   Eingabe-/Ausgabekern extrahieren. Bis dahin bleibt die bewaehrte Verteilung
   bewusst inline in ESPHome.
- **BD-11:** Nach der Extraktion Host-Tests fuer Prioritaet, Sockel, Deckel,
   getrennte Geraete, Budgetmangel, ungueltige Parameter und deterministische
   Gleichstaende ergaenzen.

Abnahme: Derselbe Eingabesatz erzeugt auf Host und ESP dieselbe Zuteilung;
alle bisherigen Verteilfälle sind automatisiert reproduzierbar.

### Phase 2: Eingangsqualität und korrekte Zuteilung

- **BD-20 (erledigt):** Fuer VNB-Limit, PV und Hausleistung Wert,
   Quellzeitstempel, Gueltigkeit und Wertebereich auswerten. Veraltete oder
   nicht-finite Zusatzleistung kann das Budget nicht erhoehen.
- **BD-21 (erledigt):** Positive Batterieentladung mit dokumentiertem Vorzeichen,
   Quellfrische, Plausibilitaetsgrenzen und 80-%-Sicherheitsmarge in die
   Saldierung aufgenommen; Laden zaehlt nicht als Erzeugung.
- **BD-22 (erledigt):** Technische Sockelleistung atomar behandeln: Reicht das
   Budget nicht für den Sockel, erhält das Gerät 0 statt eines nicht
   ausführbaren Zwischenwertes.
- **BD-23:** EV und Wallbox auf tatsächlich unterstützte Phasen- und
   Ganzampere-Stufen abrunden. Angezeigte, angeforderte und erwartete Leistung
   müssen dieselbe quantisierte Zuteilung verwenden.

Abnahme: Stale-/NaN-Daten können das Budget nie erhöhen; kein Adapter hebt
eine Zuteilung nachträglich über das von der Verteil-Lambda vergebene Budget an.

### Phase 3: Befehls- und Rückmeldezyklus

- **BD-30:** 100-W-Hysterese beibehalten, aber unveränderte aktive Limits
   spätestens nach 60 Sekunden erneut senden.
- **BD-31:** Pro Verbraucher `requested`, `acknowledged` und `measured`
   getrennt führen; Disconnect, Timeout und Ablehnung sichtbar machen.
- **BD-32:** Closed-Loop-Wächter implementieren. Eine Überschreitung des
   netzwirksamen Limits wird zeitlich integriert und nach zwei Minuten durch
   definierte, stufenweise Reduktion beantwortet.
- **BD-33:** Rückkehr aus Degradierung und Failsafe deterministisch machen;
   Reconnect darf keine veraltete Zuteilung reaktivieren.

Abnahme: Simulationen für ACK-Verlust, ignoriertes Limit, PV-Einbruch und
Reconnect halten das VNB-Limit ein oder wechseln nachvollziehbar in den
Failsafe-Zustand.

### Phase 4: Nachweis und Betrieb

- **BD-40:** Diagnose-Entities und strukturierte Ereignisse für Budget,
   Quellenqualität, Zuteilung, Quantisierung und Compliance-Abweichung
   bereitstellen.
- **BD-41:** Szenariotests mit den Fake-Geräten sowie einen kontrollierten
   Hardwaretest für gleichzeitigen WP-/EV-/Wallbox-Betrieb dokumentieren.
- **BD-42:** Soll-/Ist-Verhalten, Reaktionszeit und Failsafe-Nachweis als
   reproduzierbares Abnahmeprotokoll ablegen.

Abnahme: Alle Szenarien aus Abschnitt 5 und 6 sind mit Zeitstempeln und
Messwerten nachvollziehbar; offene Abweichungen sind im TODO erfasst.

## 8. Beispielrechnung

VNB dimmt auf 4200 W, PV liefert 3000 W, der unsteuerbare Hausverbrauch
beträgt 1000 W und die Batterie ist idle:

```
PV-Überschuss = max(0, 3000 - 1000) = 2000 W
Budget = 4200 + 0.8 × 2000 = 5800 W
Runde 1: WP 4200 (Prio 1) → Rest 1600; EV 1380 (Prio 2, 6 A) → Rest 220;
         Wallbox 0 (Prio 3, Sockel 0)
Runde 2: WP +220 → 4420 W; Rest 0 W
Ergebnis: WP 4420 | EV 1380 | WB 0  — netzwirksam ≤ 4200 W eingehalten,
          Haushaltsverbrauch unbegrenzt zusätzlich.
```

## 9. Abgrenzung zu OSSHPCF

Die Budget-Verteilung und die PV-Optimierung der Bosch-Waermepumpe verfolgen
unterschiedliche Ziele:

- LPC und dieser Verteiler begrenzen die momentane netzwirksame Leistung.
- OSSHPCF verschiebt einen von der Waermepumpe angebotenen Verdichterablauf
   innerhalb seiner Zeit-, Leistungs- und Komfort-Constraints.

OSSHPCF darf das zugeteilte §14a-Budget nicht erhoehen. Eine ausgewaehlte
Waermepumpen-Sequenz wird daher weiterhin durch den fuer EG1 berechneten
Leistungsanteil begrenzt. Bosch-spezifische Steuerdaten und die notwendigen
Captures sind in [Bosch-Waermepumpe: OSSHPCF / SEMP](oss-hpcf-bosch.md)
dokumentiert.
