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

## 2. Ist-Zustand im esphome-hems (Stand 2026-07-13)

Der Prioritätsverteiler in `netzdienliche-steuerung.yaml` berechnet bei
aktivem CS-Limit und alle 30 Sekunden `P_Limit + 0,8 × PV-Überschuss`. Der
PV-Überschuss ist die positive Differenz aus Solarleistung und unsteuerbarem
Hausverbrauch. Anschließend sortiert der Verteiler Wärmepumpe, lokalen
EV-Ladepfad und Wallbox nach Betreiberpriorität und verteilt atomare Sockel
sowie Restbudget bis zum jeweiligen Deckel. Die Parameter Priorität, Sockel
und Maximum sind persistente ESPHome-Entities. Der aktuelle Verteilstand wird
als Textsensor ausgegeben.

Noch nicht vollständig umgesetzt sind:

- Batterieentladung und Messwertalter/-qualität in der Budgetformel,
- explizite Quantisierung auf zulässige ganzzahlige Ladestromstufen,
- erzwungenes Neusenden nach 60 Sekunden unabhängig von der 100-W-Hysterese,
- Closed-Loop-Prüfung anhand der gemessenen netzwirksamen SteuVE-Leistung,
- Trennung von Allokationskern und YAML-/Geräteadaptern sowie automatisierte
   Tests der Verteilregeln.

Der bestehende Verteiler ist damit eine funktionsfähige erste Stufe, aber
noch kein vollständig verifizierter Compliance-Regelkreis.

## 3. Budget-Modell

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

¹ Die WP ignoriert Limits < 4200 W ohnehin (Geräteverhalten, im Component als
`min_limit_w` hinterlegt) — ihr Sockel entspricht also dem gesetzlichen Minimum.

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

- **BD-10:** Verteilalgorithmus aus der YAML-Lambda in eine kleine lokale
   Komponente mit reinem Eingabe-/Ausgabemodell extrahieren. Gerätezugriffe
   bleiben in separaten Adaptern; das Verhalten wird dabei nicht geändert.
- **BD-11:** Tests für Priorität, Sockel, Deckel, getrennte Geräte,
   Budgetmangel, ungültige Parameter und deterministische Gleichstände
   ergänzen.

Abnahme: Derselbe Eingabesatz erzeugt auf Host und ESP dieselbe Zuteilung;
alle bisherigen Verteilfälle sind automatisiert reproduzierbar.

### Phase 2: Eingangsqualität und korrekte Zuteilung

- **BD-20:** Für Limit, PV, Batterie und Geräteleistungen Wert, Zeitstempel,
   Gültigkeit und Qualitätsstatus gemeinsam auswerten. Veraltete oder
   nicht-finite Zusatzleistung darf das Budget nicht erhöhen.
- **BD-21:** Positive Batterieentladung mit dokumentiertem Vorzeichen und
   Sicherheitsmarge in die Saldierung aufnehmen; Laden zählt nicht als
   Erzeugung.
- **BD-22 (erledigt):** Technische Sockelleistung atomar behandeln: Reicht das
   Budget nicht für den Sockel, erhält das Gerät 0 statt eines nicht
   ausführbaren Zwischenwertes.
- **BD-23:** EV und Wallbox auf tatsächlich unterstützte Phasen- und
   Ganzampere-Stufen abrunden. Angezeigte, angeforderte und erwartete Leistung
   müssen dieselbe quantisierte Zuteilung verwenden.

Abnahme: Stale-/NaN-Tests können das Budget nie erhöhen; kein Adapter hebt
eine Zuteilung nachträglich über das vom Allokationskern vergebene Budget an.

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
