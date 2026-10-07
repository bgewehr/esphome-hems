# Xemex CSMB EV-Regelung

Stand: 2026-10-07

## Ziel

Die emulierten CSMB-Stroeme sollen die Wallbox auf einen vorgegebenen
EV-Leistungssollwert fuehren, ohne zwischen Ladepause und maximaler Leistung zu
pendeln. Der Fronius-EV-Zaehler wird dazu mit 1 s aktualisiert. Die Wallbox liest
die CSMB-Register laut Referenzimplementierung etwa alle 2 s; dies ist die
Untergrenze fuer eine sichtbare Reaktion der Wallbox.

Referenz: [thomase1234/esphome-fake-xemex-csmb](https://github.com/thomase1234/esphome-fake-xemex-csmb)

## Messbefund

Der lokale Hardware-Capture
`private/captures/ev-response-20260715-232334.csv` enthaelt 180 s bei 11,5 kW
und anschliessend 180 s bei 6 kW. Alle 360 Sekundenwerte wurden ohne
Lesefehler erfasst.

Vor dem Sollwertsprung war die Ladung stabil:

- Leistung: 10,53 kW
- Phasenstroeme: 15,89 A / 15,38 A / 15,40 A
- Standardabweichung je Phasenstrom: hoechstens 0,006 A

Die direkte, ungedaempfte Korrektur stabilisierte sich nach dem Sprung nicht:

- 47 s Ladepause unter 500 W
- 81 s oberhalb 8 kW
- nur 15 s im Zielband 5,5 bis 6,5 kW
- wiederholte Ladepausen von 10 bis 13 s
- Messbereich nach dem Sprung: 6 W bis 10,60 kW

Der Istwert ist vor dem Sprung sehr stabil. Waehrend der Regelung traten jedoch
einzelne Faktor-10-Fehlpaarungen zwischen Rohwert und Scale Factor auf: Statt
etwa 8 bis 16 A wurden kurzzeitig etwa 1,2 A beziehungsweise 94 bis 107 A
publiziert. Diese Werte erzeugten extreme CSMB-Stellwerte und verstaerkten das
Schwingen.

Der lokale Capture
`private/captures/ev-response-plausibility-filter-20260717.csv` prueft die
Referenzregelung mit einem unverzoegerten Plausibilitaetsfilter. Alle 360
Samples waren fehlerfrei, und es gelangte kein unplausibler Phasenstrom bis zur
CSMB-Regelung. Gegenueber dem ungefilterten Lauf verbesserte sich das Verhalten,
bestand die Abnahme aber noch nicht:

- Ladepause von 47 s auf 22 s reduziert
- Anteil im Zielband der letzten 120 s von 10,0 % auf 23,1 % erhoeht
- weiterhin 72 s oberhalb 8 kW
- sichtbare Reaktion der Wallbox erst nach etwa 30 s

## Regelgesetz

Die Referenzimplementierung korrigiert asymmetrisch:

- Ladeleistung erhoehen: voller Regelfehler
- Ladeleistung reduzieren: halber Regelfehler

Mit `I_limit = 47,4 A`, dem gewuenschten Ladestrom `I_soll` und dem gemessenen
Ladestrom `I_ist` gilt:

```text
Fehler = I_soll - I_ist

Fehler > 0:  I_CSMB = I_limit - Fehler
Fehler <= 0: I_CSMB = I_limit - Fehler / 2
```

In der bestehenden Addon-Darstellung ist der direkte Zielwert
`I_direkt = I_ist + Addon`. Daraus folgt aequivalent:

```text
I_direkt <= 47,4 A: I_CSMB = I_direkt
I_direkt >  47,4 A: I_CSMB = 47,4 A + (I_direkt - 47,4 A) / 2
```

Beim gemessenen Sprung von 11,5 kW auf 6 kW reduziert dies den ersten
CSMB-Stellwert auf Phase A von 54,64 A auf 51,02 A. Die Berechnung verwendet
den aktuellen Messwert und nicht rekursiv den vorherigen CSMB-Wert.

Fuer die vorliegende Wallbox ist Faktor 0,5 wegen der rund 30 s langen Totzeit
zu aggressiv. Eine symmetrische proportionale Rueckkopplung mit `Kp = 0,2`
wurde deshalb getestet:

```text
I_CSMB = 47,4 A + (I_ist - I_soll) * 0,2
```

Unbegrenzter Betrieb und die explizite Ladepause werden weiterhin direkt
geschaltet. Der lokale Capture
`private/captures/ev-response-gain-0.2-calibrated-20260717.csv` belegt die
Stabilisierung:

- keine Ladepause und keine Stromausreisser
- Zielband erstmals nach 42 s fuer mindestens 5 s erreicht
- letzte 30 s stabil bei 5,44 kW mit nur 9 W Standardabweichung
- 73,6 % der letzten 120 s im Zielband

Der verbleibende Fehler ist eine diskrete Stromstufe und keine lineare
Messabweichung. Mit 0,65 A Vorsteuerung blieb die Wallbox auf 8 A und lieferte
in den letzten 120 s 5,45 kW. Mit 2,81 A sprang sie bis auf 10 A und lieferte
zuletzt etwa 6,56 kW. Aus den dabei gemessenen CSMB-Umschaltpunkten liegt das
9-A-Fenster fuer die Vorsteuerung zwischen etwa 2,32 und 2,58 A. Der finale
Mittelwert ist 2,40 A beziehungsweise rund 1,66 kW. Die 8-A-Mindeststrom- und
Unbegrenzt-Schwellen beziehen sich weiterhin auf den unverfaelschten
Benutzersollwert.

## Implementierungsstand

- Fronius-EV-Polling: 1 s
- Medianfilter der drei EV-Phasenstroeme entfernt
- unverzoegerter Plausibilitaetsfilter gegen Faktor-10-Ausreisser implementiert
- proportionale Rueckkopplung mit `Kp = 0,2` fuer alle drei Phasen implementiert
- 2,40-A-Vorsteuerung im gemessenen 9-A-Fenster implementiert
- Wiederanlaufmodus unter 5 kW: ungedaempfter CSMB-Stellwert bis zur stabilen
	Mindestladestufe
- Sollwerte unter 8 A werden sicher auf AUS abgebildet; damit fuehrt das
	§14a-Maximum von 4.200 W deterministisch zum Ladestopp
- Firmware-Clean-Build und OTA erfolgreich; aktuelle live verifizierte Build-Zeit:
	`Oct 7 2026 10:56:20` (Integrationsfix `d643778`)
- OpenEEBUS-Submodul: Branch `hems`

## Hardware-Abnahme

Die Oktober-Firmware ist seit 2026-10-07, 10:57 auf dem Geraet. Die vollstaendige
Hardwareabnahme bleibt offen; Softwaretests und erfolgreicher OTA-Start sind
kein Ersatz fuer die nachstehenden Kriterien.

### Messung vom 2026-10-07

Capture `private/captures/ev-6000-20261007.csv`, Firmware `Oct 7 2026 10:56:20`:

- 180 s bei 11.500 W Vorgabe, danach 180 s bei 6.000 W; 360 Samples,
  keine Lesefehler und keine Ladepause.
- Nach dem Sprung 6.010 bis 10.506 W; 27 Sekundenwerte ueber 8 kW.
- Erstes zusammenhaengendes Zielbandfenster ab etwa 88 s statt geforderter 60 s.
- 76,0 % der letzten 120 s im Zielband 5,5 bis 6,5 kW statt geforderter 90 %.
- Bestehender Analyzer meldet daher FAIL fuer Einschwingzeit und Zielbandanteil.
  Die Kalibrierung wurde nicht veraendert; kein Abnahme-PASS fuer die Regelung.

Stopp-/Wiederanlaufversuch mit derselben Firmware:

- `private/captures/ev-stop-20261007.csv`: 4.200 W Vorgabe wird wie vorgesehen
	als Stopp behandelt. Nach etwa 12 s nur noch 7,3 W, danach bis zum Ende
	der 100-s-Messung etwa 6 bis 7 W; 100 Samples ohne Lesefehler.
- `private/captures/ev-restart-20261007.csv`: Rueckkehr zu 11.500 W Vorgabe;
	nach etwa 20 s ueber 5 kW, nach etwa 28 s wieder 10,47 kW. Alle 190 Samples
	ohne Lesefehler; letzte 120 s bei 10.458 bis 10.505 W (Mittel 10.484 W).
	Abschliessende Vorgabe bleibt 11.500 W.
- Der absichtliche Stopp erzeugte keinen Ladeabbruchzaehler-Eintrag.
	Ein erfolgreicher Zyklus belegt nicht die Behebung aller frueheren
	Session-/Fahrzeug-Nichtstarts. Weitere Fehler- und Grenzfalltests bleiben offen.

### Frueherer Kalibrierstand

Hardware-Ergebnisse des vorigen Kalibrierstands:

- `11,5 kW -> 6 kW`: keine Ladepause; mit 2,81 A Vorsteuerung stationaer etwa
	6,56 kW auf der pfadabhaengigen 10-A-Stufe
- `6 kW -> 4,2 kW`: sicher AUS; letzte 60 von 60 Sekunden bei etwa 6 W und
	CSMB exakt 47,4 A
- `4,2 kW/AUS -> 6 kW`: innerhalb 179 s kein Wiederanlauf, obwohl der
	Startmodus CSMB konstant auf 36,0 A absenkte
- anschliessend `unbegrenzt`: innerhalb weiterer 120 s kein Wiederanlauf,
	obwohl CSMB konstant 1,0 A meldete

Der verbleibende Nichtstart kann damit nicht durch zu wenig CSMB-Spielraum
verursacht sein. Die Wallbox-Ladesession oder das Fahrzeug fordert nach dem
0-A-Stopp keine neue Ladung an. Auch ein HEMS-Neustart mit CSMB-Reconnect
aenderte innerhalb weiterer 120 s nichts. Das Geraet wurde fuer die weitere
Diagnose auf `11,5 kW` beziehungsweise CSMB `1,0 A` belassen.

Akzeptanzkriterien:

- keine Ladepause nach dem Sollwertsprung
- Zielband 5,5 bis 6,5 kW spaetestens nach 60 s erreicht
- mindestens 90 % der letzten 120 s im Zielband
- kein periodischer Wechsel zwischen Ladepause und mehr als 8 kW

## Absicherung nach TeeNet-Vergleich (2026-10-06)

Referenz: [TeeNet 738e753, Version 1.7](https://github.com/stetastic/TeeNet/tree/738e753e6eaf7f5b8b52e274c2960ebd2ad2dcab).
Die bestehende Kalibrierung, drei unabhaengige Strommessungen, 8-A-Grenze,
Vorsteuerung und Kp bleiben unveraendert. Es gibt keine Phasenumschaltung,
keinen zusaetzlichen Relaispfad und keine automatische Offsetkalibrierung.

- `components/modbus_server/xemex_control.h` berechnet alle Stellwerte.
	`xemex_update` ist der einzige Schreiber der sechs CSMB-Stromregister.
	Benutzergrenze, EEBus-Anteil sowie DI1/Simulation werden dort gemeinsam
	ausgewertet. DI1 und Simulation erzwingen den gleichen Stopp wie 0 W.
- Leistung und alle drei akzeptierten Strommessungen duerfen maximal 5 s alt
	sein. Ein 1-s-Takt prueft auch ohne neue Messwerte; die Reaktionszeit ist
	damit maximal etwa 6 s bei laufendem ESPHome-Loop. Boot, fehlende Messungen
	und ungueltige Vorgaben melden 80 A auf allen CSMB-Phasen. Das ist eine
	konservative synthetische Last, kein erlaubter Ladestrom. Bei frischen
	Daten bleibt der gemessene Stopppfad `I_ist + 47,4 A` erhalten.
- Die bisherigen CT-Number-Entities behalten Namen und IDs zur Diagnose.
	Direkte Schreibversuche werden auf den berechneten Wert zurueckgefuehrt;
	sie koennen den Watchdog oder ein Limit nicht umgehen. Stellgroesse fuer
	den Benutzer bleibt `EV Leistungsbegrenzung`. Modbus-Schreibbefehle an die
	emulierte CSMB-Konfiguration werden abgewiesen.
- Benutzerlimits bleiben auch waehrend eines EEBus-Limits persistent;
	ausschliesslich interne Budgetzuweisungen setzen das Override-Flag.
- Ein Ladeabbruch wird nur nach vorher mindestens 8 s mit mindestens 6,5 A
	und anschliessend 20 s unter 1 A erkannt. Danach bleiben 30 s Stopp gesetzt.
	Fuenf bestaetigte Abbrueche innerhalb 5 min verriegeln die Freigabe; die
	Sperre wird mit ESPHome-Preferences persistent gespeichert. Ein nie
	ladendes Fahrzeug zaehlt nicht als Abbruch. Nach 180 s ohne bestaetigten
	Ladestart erscheint eine Diagnose; es wird keine neue Ladesession erzeugt.
- `EV Sperre zuruecksetzen` verlangt Benutzerlimit 0 W, frische Messwerte und
	weniger als 1 A auf jeder Phase. Entsperren startet keine Ladung. Ein
	angeforderter Stopp ohne gemessenen Stillstand wird nach 90 s angezeigt.
- Diagnose trennt effektiven Sollwert, Messwertalter, letzte CSMB-Abfrage,
	ausgegebene Stromantwort samt Alter, CRC-Fehler und Abbruchzaehler. Eine
	ausgegebene UART-Antwort ist kein ACK und kein physischer Abschaltnachweis.

Ein Busausfall oder ein stillstehender Controller kann nicht durch eine
synthetische Last sicher abgeschaltet werden. Die physische Wirkung von 80 A,
Messwertausfall/Wiederkehr, DI1/Simulation, ueberlappenden EEBus-Limits und
Wiederanlaeufen muss vor produktiver Freigabe am realen Geraet geprueft werden.
Das bekannte Session-/Fahrzeugproblem nach Ladepause gilt nicht als geloest.

Abnahmestand 2026-10-07: OpenEEBus-Upstream wurde zunaechst lokal vorgezogen.
Inzwischen ist das Fahrzeug angeschlossen; vor OTA wurden 10,43 kW und
15,2 bis 15,6 A pro Phase bei 11.500 W Sollwert gemessen. Der Betreiber hat
Tests, Commit, Push und OTA freigegeben. Der oben dokumentierte 6-kW-Test
erfuellt die Abnahme noch nicht; die Softwaretests ersetzen sie nicht.

Die lokale Abschlusspruefung hat eine Beobachtungsluecke im Abbruchwaechter
behoben: Liegen mehr als 5 s zwischen Regleraufrufen, beginnen Lade- und
Stillstandsbeobachtung neu. Eine unbeobachtete Zeitspanne bestaetigt weder
Ladung noch Ladeabbruch. Vorhandene Abbruchhistorie, Wiederanlaufwartezeit und
Sperre bleiben erhalten. Regressionstests verwenden fuer kontinuierliche
Beobachtungen Einsekundenmessungen und pruefen lange Luecken separat.

### Lesender Grenzwertabgleich

`tools/diagnostics/check_wallbox_limits.ps1 -WallboxHost <IP>` liest einmalig
`http://<IP>:12800/user/status`. Ausgewertet werden nur `maxLimit.current` und
`connectors[id=1].max.current`; `chargingRate` ist keine Regelquelle. Es gibt
keinen Scan, keine Schreibbefehle und keine automatische Konfigurationsaenderung.
Die Vergleichsannahmen 50 A Netzgrenze und 16 A Ladestrom lassen sich ueber
`-ExpectedGridLimitA` und `-ExpectedChargeLimitA` angeben. Die empirische
Reglerschwelle 47,4 A ist davon zu unterscheiden; Abweichungen erfordern
Bewertung und gegebenenfalls neue Kalibrierung, keine blinde Konstantenersetzung.

### Lokale Pruefung

Die CMake/CTest-Suite enthaelt `xemex_control`: Kalibrierung, Grenzwertprioritaet,
Boot und Messwertalter, Abbruchschutz, Timer-Ueberlauf und sichere Entsperrung.
`tests/wallbox_limits_test.ps1` prueft den HTTP-JSON-Parser offline mit acht
Fixtures. Der ESPHome-Task `Validate ESPHome firmware` kompiliert die Anbindung,
laedt aber nichts hoch. Die Hardware-Abnahme BD-24 bleibt offen.
