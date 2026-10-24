# Konzept: §14a-Leistungsbudget-Verteilung im HEMS

Code- und Dokumentationsabgleich: 2026-10-17. Datierte Hardwaretests unten
bleiben auf ihren jeweiligen Firmwarestand begrenzt.

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
   Der konfigurierte Geraetesockel ist dabei nicht mit einer universellen
   Untergrenze fuer jeden empfangenen Protokollwert gleichzusetzen.

4. **Zeitverhalten:** Verbindliche Reaktions- und Rueckfallvorgaben sind fuer
   die konkrete Installation mit VNB und Geraetedokumentation abzugleichen.
   Die unten vorgeschlagenen Zwei-Minuten-Regeln sind Entwurfsparameter,
   keine hier nachgewiesene regulatorische Toleranz. Ein konfigurierter
   Failsafe ersetzt keinen gemessenen Ausfalltest.

## 2. Ist-Zustand im esphome-hems (Codeabgleich 2026-10-17)

Der Prioritätsverteiler in `netzdienliche-steuerung.yaml` berechnet bei
aktivem CS-Limit und alle 30 Sekunden das Netzbudget mit 80 % des gueltigen
Erzeugungsueberschusses. PV und Batterieentladung werden als AC-Aequivalente
beruecksichtigt, Batterieladung wird zuvor reserviert; nicht durch Erzeugung
gedeckte Ladung reduziert das Netzbudget. Die genaue Formel steht unten.
Anschließend sortiert der Verteiler Wärmepumpe, lokalen
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
setzt den Erzeugungszuschlag auf 0 W. Die verbleibende Ladereservierung kann
das Netzbudget weiter reduzieren. Ein ungueltiges VNB-Limit oder eine
ungueltige Ladereservierung erzeugt konservativ 0 W Gesamtbudget.

`Fronius Battery Power` kombiniert die getrennten DC-Rohwerte mit dem
dokumentierten Vorzeichen `Entladung - Ladung`; positive Werte sind Entladung.
Nur wenn beide Rohwerte hoechstens 10 s alt, endlich und im Bereich 0 bis
25 kW liegen, wird die positive Entladung zum Erzeugungsueberschuss addiert.
Ein negativer Wert (Laden) erhoeht das Budget nicht, sondern geht in die
Ladereservierung ein. Ungueltige Batteriedaten machen diese Reservierung
ungueltig und fuehren im aktuellen Regelkern zu 0 W Budget. Die
80-%-Sicherheitsmarge gilt fuer den verbleibenden gemeinsamen Ueberschuss.

Der bestehende Verteiler ist damit eine funktionsfähige erste Stufe, aber
noch kein vollständig verifizierter Compliance-Regelkreis.

**Offener Adapterkonflikt:** Bei EG-Zuteilung 0 W sendet die Lambda 1 W,
weil `set_limit(0)` das Limit loescht. Der Geraeteadapter kann diesen positiven
Wert durch `min_limit_w` wieder auf 4200 W anheben. Rechnerisch atomare
Sockel garantieren deshalb noch keinen physischen Stopp bei Budgetmangel.
Auch zu kleine positive Sockel sind gegen den Geraetemindestwert zu validieren.
Dies bleibt Teil von BD-22/BD-33 und der Hardwareabnahme; keine nachgewiesene
Budgettreue aus der Statusanzeige ableiten.

## 3. Budget-Modell

### PV- und Batteriepriorisierung

Implementiert seit Oktober 2026: `battery-priority.yaml` integriert den host-getesteten
Regelkern aus `components/power_distribution/solar_policy.h`. Die vier neuen
Schalter sind persistent und standardmaessig aus. Der Regelkern ist jetzt
mit Storage-Schreibpfad, Xemex-Freigabe und Budgetverteilung verbunden.
Die erste Dry-Run-Firmware wurde am 2026-10-07 um 15:23 Uhr OTA ausgerollt
(Build Time `Oct 7 2026 15:20:40`). Der aktive Korrekturbuild wurde um
16:17 Uhr OTA installiert, Build Time `Oct 7 2026 16:17:01` live bestaetigt.
Die Hardware-Abnahme ist noch nicht vollstaendig. Die lokale Home-Assistant-Karte
`Batterie (Reserva)` wurde angepasst und
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
   und das damalige Nutzerlimit 5600 W unveraendert; physische Minimum-Regelguete nicht neu
   vermessen. Ein hoeherer PV-Eigenverbrauch ueber die gesamte Ladung ist
   wetter- und ladezeitabhaengig, nicht durch das Leistungslimit garantiert.
- PV-Erzeugung bleibt als separat validierte Eingangsgroesse erhalten.
   Fuer die Leistungsbilanz gilt vorlaeufig `PV_AC = 0,95 * PV_DC`.
   Nicht-EV-Last ist Haus + Waermepumpe; EV- und Ohmpilot-Verbrauch werden nicht
   abgezogen und koennen deshalb eine bestehende Freigabe nicht selbst aufheben.
- Tatsaechliche Batterieladung wird mit `Ladung_DC / 0,95` als AC-Aequivalent
   reserviert. Das ist eine konservative Rechenannahme, kein gemessener
   Wirkungsgrad und kein validiertes Hybridwechselrichter-Modell.
- Bei Batterieprioritaet ist das Ziel der positive PV-Rest, begrenzt durch
   eine bekannte Ladeaufnahme. Der reale Adapter kennt bisher nur den frischen
   Status FULL als Aufnahmegrenze 0 W. Sonst bleibt die Aufnahme unbekannt;
   vorsorglich wird der gesamte PV-Rest reserviert. `WChaMax` ist nur die
   Bezugsleistung fuer Prozentwerte, **keine dynamische BMS-Ladegrenze**.
- Zwangsladung ist von dieser Reservierung getrennt: Ein negatives OutWRte
   wird nur angefordert, wenn mindestens ein fuer Batterieversorgung gesperrter
   Verbraucher (Haus, WP oder EV) mit frischer, plausibler Messung mehr als
   30 W abnimmt. Standby oder ein nicht ladendes Auto allein loesen keine
   Zwangsladung aus; eine weiterhin aktive gesperrte WP oder Hauslast schon.
   Ohne solche Last gilt wieder die bestehende Entladepolitik, nicht ein
   negativer Ladebefehl. Deren Modbus-Schreiber und Ruecklese-Schutz bleiben
   aktiv. Die Reservierung fuer Batterieprioritaet bleibt ebenfalls erhalten.
   Gemessene Last ist kein unabhaengiges Signal fuer den Ladewunsch des Autos.
   OTA am 2026-10-07 um 17:24 Uhr erfolgreich, Live-Build `Oct 7 2026 17:21:59`.
   Clean-Firmware-Build und alle drei Host-Tests bestanden. Nach Neustart:
   EV-Stillstand bestaetigt bei 6,5 W Standby, keine negative Ladeanforderung,
   normale Entladepolitik, Storage-Schreiber `Automatic`, Batterie laedt mit
   rund 93 W. Nutzerlimit 11500 W und alle fuenf Ladeschalter unveraendert.
   Solar-only und Low-Solar-Netzfreigabe sind an; die zehnminuetige
   Freigabequalifikation beginnt durch den Neustart erneut.
- Der Ohmpilot regelt laut Betreiber selbst ausschliesslich auf den
   Einspeiseueberschuss. Sein Nachrang gegenueber anderen Verbrauchern und
   Batterieladung benoetigt deshalb keine eigene HEMS-Steuerung. Die gewuenschte
   Prioritaet lautet Haus, WP, Batterie, Auto, Ohmpilot, Einspeisung.
   Sein Verbrauch wird weder vom Batterie-PV-Rest noch von der EV-PV-Unterstuetzung
   abgezogen. Der Solaradapter benoetigt keinen Ohmpilot-Quellzeitstempel mehr.
   Nur zur Ermittlung des reinen Hausverbrauchs wird er aus AC + Netz - EV - WP
   herausgerechnet, damit er nicht indirekt als Hauslast in die Regelung eingeht.
   Messwerte bleiben fuer die Anzeige erhalten. Diese Korrektur vom 2026-10-08
   ist per OTA um 07:25 Uhr installiert; Live-Build `Oct 8 2026 07:17:00`.
   Firmware-Build und alle drei Host-Tests bestanden. Nach Neustart:
   Storage-Schreiber `Automatic`, EV-Stillstand bei 6,5 W und effektivem
   Sollwert 0 W (PV-Pause). Nutzerlimit 11500 W und alle fuenf Ladeschalter
   unveraendert; die Low-Solar-Freigabequalifikation beginnt erneut.
- EV-Unterstuetzung ist der PV-Rest nach priorisierter Ladung. Ohne Prioritaet
   darf natuerliche Batterieladung fuer den EV-Start verdraengt werden;
   sie bleibt trotzdem in der diagnostizierten Budgetreservierung enthalten.
   Diese Reservierung ist `max(Istladung, Ladeziel)`, nicht deren Summe.
- Bisheriger Firmwarestand vor der Restprognose-Umstellung: Beide Modi bewerten die
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
- Modbus-Skalierung vom 2026-10-24: Alle Fronius-SunSpec-Messwerte verwenden
   ausschliesslich bestaetigte Skalierungsfaktoren. Gueltige Faktoren duerfen
   gecacht werden; nach Neustart sind sie unbekannt. Ein ungueltiger SF macht
   den jeweiligen Cache wieder ungueltig. Ohne gueltigen Faktor oder bei
   SunSpec-Fehlwerten unterdrueckt der Sensorfilter die Veroeffentlichung und
   aktualisiert weder Messwertzustand noch Folgeverarbeitung. Batterie-SOC
   wird zusaetzlich auf 0 bis 100 Prozent geprueft, nicht auf diese Grenzen
   geklemmt. Host-Test und ESPHome-Konfigurationspruefung bestanden; noch
   nicht auf das Geraet geladen.
- Restprognose-Umstellung vom 2026-10-24: Policy-Kern und Host-Tests sind
   vorbereitet, aber die Fronius-Datenanbindung fehlt; nicht geflasht.
   Die Netzfreigabe soll bei eingeschaltetem Low-Solar-Schalter sofort gelten,
   wenn die gueltige Erzeugungsprognose fuer den restlichen lokalen Tag unter
   0,5 kWh liegt. Keine zehnminuetige Wartezeit und keine Ableitung aus
   voruebergehend geringer PV-Leistung. Fehlende, abgelaufene oder ungueltige
   Prognosen erlauben keinen Netz-Fallback. PV-Freigabe und Schutzsperren
   bleiben davon getrennt; nur eine gueltige Low-Solar-Freigabe hebt das
   feste Solar-Leistungslimit auf.
   HEMS darf dafuer nicht von Home Assistant abhaengen. Die voruebergehende
   HA-Prognoseanbindung und ihr Template-Helfer wurden entfernt.
   Die lokale GEN24 Solar API lieferte beim lesenden Check keine Prognose.
   Verifiziert ist der Cloud-Endpunkt
   `GET https://api.solarweb.com/swqapi/pvsystems/{pvSystemId}/weather/energyforecast`
   mit `EnergyExpected` in Wh je 15-Minuten-Intervall. Die
   [Solar.web Query API](https://api.solarweb.com/swqapi/)
   verlangt `AccessKeyId` und `AccessKeyValue`; Zugriff fuer diese Anlage
   und Prognoseberechtigung sind noch nicht nachgewiesen.
   Ein direkter Abruf durch HEMS waere HA-unabhaengig, aber nicht
   internetunabhaengig. Diese Abhaengigkeit muss vor Umsetzung geklaert werden.
   Aktuell bleiben die Prognoseeingaben im Firmwareadapter ungueltig;
   diesen Zwischenstand nicht als fertige Prognosefunktion ausrollen.
   In HA steht die Box `Solar Charging Optimization` direkt unter
   `Wallbox & Škoda Enyaq` mit den vier vorhandenen Optimierungsschaltern.
   Der Anzeigename nutzt jetzt `grid` statt `net`; die bestehende HA-Entity-ID
   wurde beibehalten. Die Entity-Identitaet nach Firmware-Umbenennung ist
   vor einer OTA noch zu pruefen.
- Quellen muessen endlich, plausibel und maximal 10 s alt sein. Zeitstempel
   werden nicht durch zyklisches Wiederholen eines Wertes erneuert.
   Fehlende oder null-PV-Felder der Solar API gelten nicht als 0 W; dann kann
   nur ein frischer MPPT-Fallback eine gueltige Erzeugung liefern. Ungueltige
   Ohmpilot-Felder setzen dessen Quellzeitstempel zurueck. Der aktuelle
   Ohmpilot-Poll wurde von 10 s auf 5 s verkuerzt. Die Hauslastbereinigung nutzt
   dessen Messwert bis 20 s Alter, sonst den bisherigen Ersatzwert 0 W;
   dann kann Ohmpilot-Verbrauch konservativ als Hauslast erscheinen.
   Die Solarfreigabe prueft Ohmpilot-Daten nicht separat. Bei ungueltigen
   benoetigten Solar-Eingangsdaten bleibt Solar-only gesperrt.
- Ungueltige Daten, Moduswechsel und Ausfuehrungsluecken ueber 10 s setzen
   die Freigabequalifikation zurueck. Ohne Solar-only entsteht kein neuer Stopp.
   Der Sollwertsensor `Battery Priority Charge Target` zeigt DC-Watt,
   EV-Unterstuetzung und Budgetreservierung zeigen AC-Aequivalente.
   `Battery Priority OutWRte` zeigt die negative Prozentanforderung fuer ein
   positives Ladeziel mit frischen Metadaten und aktiver gesperrter Last,
   sonst unbekannt. Ohne gueltiges
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

Quellcode-Korrektur 2026-10-23 (noch nicht geflasht): Die Schutzpause versucht
nach 30 s automatisch einen vollstaendigen Schreib-/Pruefzyklus. Weitere
Fehlschlaege verlaengern die Wartezeit auf 60, 120, 240 und maximal 300 s;
die Versuche werden ohne Neustart fortgesetzt. Alte Transaktionen werden
invalidiert. Die EV-Speichersperre bleibt waehrend des Wiederanlaufs aktiv und
wird erst nach gemeinsam bestaetigtem Modus, Sollwert und 15-s-Rueckfallzeit
aufgehoben. Erfolgreiche Bestaetigung setzt die Wartezeit zurueck, die letzte
Fehlerursache bleibt gespeichert. Nutzerlimit, Solar- und andere Schutzgates
werden nicht uebergangen. Hosttests fuer Backoff, Zeitueberlauf und EV-Sperre
bestanden; ESPHome-Konfigurationspruefung, Firmware-Build und OTA sind durch
Windows-Anwendungssteuerung blockiert. Hardware-Wiederanlauf noch ungeprueft.

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

Bei aktivem Limit berechnet die Verteilung alle 30 s und bei expliziten
Aufrufen von `distribute_budget` folgenden Kern (`solar_grid_budget`):

```
Erzeugung_AC = 0,95 * P_PV_DC + 0,95 * max(0, P_Batt_DC)
Ladereservierung_AC = max(0, -P_Batt_DC) / 0,95
# Bei Batterieprioritaet: Maximum aus Istladung und frischer Policy-Reservierung
Netzladung = max(0, Ladereservierung_AC - Erzeugung_AC)
Ueberschuss = max(0, Erzeugung_AC - Ladereservierung_AC - P_Haus)
Budget = max(0, P_Limit - Netzladung + 0,8 * Ueberschuss)
```

Die Formel setzt gueltige Eingaben voraus; Fehlerbehandlung siehe Abschnitt 2.
Die Faktoren 0,95 sind Modellannahmen, keine dynamisch gemessenen Wirkungsgrade.

Messgrößen sind vorhanden: Fronius Solar Power, Battery Power, Grid Power,
WP-Leistung (EG1 MPC), Wallbox-Leistung (EG2 MPC), EV-Ladeleistung (Fronius).

## 4. Verteilungsentscheidung des Betreibers

Der Betreiber konfiguriert **pro Gerät drei Werte** in der ESPHome-UI
(NVS-persistent, wie die vorhandenen Failsafe-Numbers):

| Parameter | Bedeutung | Beispiel WP | Beispiel Wallbox | Beispiel EV (Fronius) |
|---|---|---|---|---|
| **Priorität** (1 = höchste) | Reihenfolge bei Knappheit | 1 | 3 | 2 |
| **Sockel** (optional) | atomare technische/komfortbezogene Zuteilung; reicht das Budget nicht vollständig, erhält das Gerät rechnerisch 0 | 4200 W¹ | 0 W | 5544 W (lokaler 8-A-Ladepfad) |
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
   EV-Laden stoppt). Auch Prio 1 erhaelt nur bei ausreichendem Budget einen
   Sockel; die physische Umsetzung von 0 W im EG-Adapter ist noch offen.
3. Runde 2 (Rest): Verteile das Restbudget in Prioritätsreihenfolge
   bis P_max je Gerät.
4. Geplant (BD-23): Wallbox/EV auf tatsaechlich unterstuetzte Ladestrom-Stufen
   abrunden. Der lokale Xemex-Pfad benoetigt mindestens 8 A beziehungsweise
   5544 W; ein allgemeiner 6-A-Sockel ist hier kein stabiler Betriebspunkt.
5. Anwenden:  EG1.set_limit(share_WP)
              EG2.set_limit(share_Wallbox)
              EV-Leistungsbegrenzungs-Slider = share_EV

   Der vorhandene Slider-Handler ist der einzige Ort, der den EV-Leistungswert
   in Xemex-Addonwerte umrechnet. Nach Ende des §14a-Eingriffs wird der zuvor
   vom Benutzer eingestellte Sliderwert wiederhergestellt.
```

Der interne EV-Budget-Override darf dabei den persistenten Nutzerwunsch
nicht ueberschreiben. Physischer Stopp und erneuter Ladestart sind getrennt
zu messen; eine interne 0-W-Zuteilung allein ist kein Abschaltnachweis.

**Alternative Strategien** (per Select wählbar, wenn gewünscht):
- *Proportional*: Budget im Verhältnis der P_max verteilen — fühlt sich
  „fair" an, führt aber dazu, dass kein Gerät richtig arbeitet.
- *Zeitscheiben*: bei Dauerknappheit WP und EV-Laden abwechselnd bedienen —
  nur sinnvoll für lange Limits (> 1 h), erhöht Schaltspiele.

Strikte Priorität ist deterministisch, für den VNB-Nachweis auditierbar und
entspricht dem üblichen Komfortempfinden (Heizen vor Laden).

## 5. Geplanter Regelkreis und Toleranzen

BD-30 bis BD-33 sind noch nicht vollstaendig umgesetzt oder abgenommen.
Die folgenden Reaktionen sind Ziele, keine Beschreibung eines bereits
verifizierten Closed-Loop-Waechters.

- **Nachführung:** Neue Zuteilung nur senden, wenn sich der Anteil eines
  Geräts um > 100 W ändert oder 60 s vergangen sind (LPC-Schreibrate schonen,
  ACK-Retry existiert bereits).
- **Verifikation:** `netzwirksame SteuVE-Leistung` aus Messwerten berechnen.
  Liegt sie > 2 min über `P_Limit` (z. B. weil ein Gerät sein Limit ignoriert),
  Anteile stufenweise um 10 % kürzen und Ereignis loggen.
- **PV-Einbruch:** Wolkenzug oder steigender Hausverbrauch reduziert den
   Überschuss sofort. Nur 80 % des nach Hausverbrauch verbleibenden
   Erzeugungsueberschusses werden angerechnet. Die Zeit-/Qualitaetspruefung
   ist bereits implementiert, ersetzt aber nicht die physische Wirkkontrolle.

## 6. Failsafe-Verhalten (Verbindung zur Steuerbox verloren)

Die beiden Kommunikationsstrecken muessen getrennt betrachtet werden:
VNB/CLS zur HEMS-CS-Instanz und HEMS-EG zum jeweiligen Verbraucher. Die
EG-Instanzen konfigurieren fuer ihre Geraete Failsafe-Werte von 4200 W /
7200 s; damit ist noch keine globale Ersatzverteilung bei CLS-Ausfall belegt.

Der lokale Xemex-Pfad hat eigene Messwert-Watchdogs und Stopplogik. Weder
deren synthetischer CSMB-Stellwert noch ein EEBus-Failsafeparameter garantiert
ohne Hardwaretest einen physischen Stopp. Fuer BD-33/BD-41 sind beide
Verbindungsabbrueche, verbleibende Limits, Budgetverteilung und Wiederanlauf
getrennt zu pruefen. Die fruehere pauschale Zusage "WP 4200 W, Wallbox 0 W,
Verteilung pausiert" ist kein nachgewiesener Systemzustand.

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
- **BD-22 (Allokation implementiert, Adapterabnahme offen):** Technische Sockelleistung atomar behandeln: Reicht das
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

VNB dimmt auf 4200 W, PV liefert rechnerisch 3000 W AC, der unsteuerbare
Hausverbrauch betraegt 1000 W und die Batterie ist idle. Beispielparameter:
WP-Sockel 4200 W, WP-Deckel mindestens 5800 W, EV-Sockel 5544 W:

```
PV-Überschuss = max(0, 3000 - 1000) = 2000 W
Budget = 4200 + 0.8 × 2000 = 5800 W
Runde 1: WP 4200 (Prio 1) → Rest 1600; EV 0 (Sockel 5544 passt nicht);
         Wallbox 0 (Prio 3, Sockel 0)
Runde 2: WP +1600 → 5800 W; Rest 0 W
Rechnerisches Ergebnis: WP 5800 | EV 0 | WB 0.
5800 - 2000 = 3800 W netzwirksame SteuVE-Leistung, falls die Geraete folgen.
```

Das Beispiel ist eine Budgetrechnung, kein Hardware-Nachweis. Insbesondere
der 0-W-Pfad zu einer verbundenen EG-Wallbox muss den Adapterkonflikt aus
Abschnitt 2 loesen; Haushaltslast und lokale Erzeugung bleiben separat bilanziert.

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
