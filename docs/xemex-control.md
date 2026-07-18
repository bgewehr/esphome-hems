# Xemex CSMB EV-Regelung

Stand: 2026-07-17

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
- Firmware-Clean-Build und OTA erfolgreich; live verifizierte Build-Zeit:
	`Jul 17 2026 22:38:03`
- OpenEEBUS-Submodul: Branch `hems`

## Hardware-Abnahme

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
