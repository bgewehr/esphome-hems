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
   netzwirksame SteuVE-Leistung = max(0, Σ P_SteuVE − P_PV − P_Batt,Entladung)
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

## 2. Ist-Zustand im esphome-hems (Stand 2026-07-08)

Heute wird das CS-Limit `x` **parallel und ungeteilt** an alle Verbraucher
weitergereicht (Trigger-Merge über die Packages):

| Verbraucher | Mechanismus | erhält heute |
|---|---|---|
| EG1 — Wärmepumpe (Bosch) | `hems_eg1.set_limit(x)` via EEBus LPC | volles Limit |
| EG2 — Wallbox (EEBus, künftig) | `hems_eg2.set_limit(x)` via EEBus LPC | volles Limit |
| EV-Laden (Fronius/CT-Addon) | `ct1..3addon = 45` + `relay_1` | Festwert |
| Status/Events | LED, Zähler, Textsensor | — |

Problem: Bei `x = 4200 W` dürfen WP **und** Wallbox **und** EV-Laden je bis
4200 W ziehen — in Summe ein Vielfaches des Budgets. Das erfüllt die
§14a-Anforderung nur zufällig (wenn nie zwei Geräte gleichzeitig laufen).

## 3. Budget-Modell

Bei aktivem Limit berechnet das HEMS zyklisch (alle 10 s und bei jeder
Limit-/PV-Änderung):

```
Budget = P_Limit                     # vom VNB via EEBus LPC
       + max(0, P_PV_aktuell)        # Fronius Solar Power (Saldierung erlaubt)
       + max(0, P_Batt_Entladung)    # Batterie-Entladung wirkt wie Erzeugung
```

Messgrößen sind vorhanden: Fronius Solar Power, Battery Power, Grid Power,
WP-Leistung (EG1 MPC), Wallbox-Leistung (EG2 MPC), EV-Ladeleistung (Fronius).

## 4. Verteilungsentscheidung des Betreibers

Der Betreiber konfiguriert **pro Gerät drei Werte** in der ESPHome-UI
(NVS-persistent, wie die vorhandenen Failsafe-Numbers):

| Parameter | Bedeutung | Beispiel WP | Beispiel Wallbox | Beispiel EV (Fronius) |
|---|---|---|---|---|
| **Priorität** (1 = höchste) | Reihenfolge bei Knappheit | 1 | 3 | 2 |
| **P_min** (Komfort-Sockel) | bekommt das Gerät zuerst, wenn Budget reicht | 4200 W¹ | 0 W | 1380 W (6 A) |
| **P_max** | Deckel, mehr wird nie zugeteilt | 9000 W | 11000 W | 11000 W |

¹ Die WP ignoriert Limits < 4200 W ohnehin (Geräteverhalten, im Component als
`min_limit_w` hinterlegt) — ihr Sockel entspricht also dem gesetzlichen Minimum.

**Verteilalgorithmus (strikte Priorität mit Sockeln — empfohlen):**

```
1. Sortiere verbundene steuerbare Geräte nach Priorität.
2. Runde 1 (Sockel): Teile jedem Gerät der Reihe nach sein P_min zu,
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
              ct1..3addon = share_EV / (230 × Phasen)
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
- **PV-Einbruch:** Wolkenzug reduziert das Budget sofort — deshalb PV-Anteil
  mit 60-s-Mittelwert glätten und nur 80 % der PV-Leistung anrechnen
  (Sicherheitsmarge gegen Überschreitung).

## 6. Failsafe-Verhalten (Verbindung zur Steuerbox verloren)

Bereits implementiert und vom Konzept unverändert: Jede EG-Instanz kennt
ihren Failsafe (4200 W / 7200 s). Bei Heartbeat-Verlust der CS-Verbindung:

- WP → 4200 W (entspricht gesetzlichem Minimum)
- Wallbox → 0 W (Prio-3-Gerät verzichtet)
- EV-Laden → `ladebegrenzung_addon` (bestehender Mechanismus)
- Verteil-Logik pausiert, bis die Steuerbox wieder verbunden ist.

## 7. Umsetzungsschritte im Repo

1. **`budget_distributor`-Logik** (Lambda-Interval oder kleines Custom
   Component in `eebus-cs.yaml`): Budget-Formel + Runden 1/2 + Quantisierung.
   Ersetzt die heutigen direkten `set_limit(x)`-Weiterleitungen in
   `eebus-eg-1.yaml` / `eebus-eg-2.yaml` / `esphome-hems.yaml`.
2. **UI-Entities je Gerät**: `number` P_min/P_max + `select` Priorität
   (analog zu den vorhandenen Failsafe-Numbers, `restore_value: true`).
3. **Statusanzeige**: Textsensor „Budget-Verteilung" (z. B.
   `4200 → WP 4200 | WB 0 | EV 0 (PV +1200)`), Ereigniszähler wiederverwenden.
4. **Closed-Loop-Wächter**: Interval 30 s, vergleicht Messwerte gegen Limit,
   kürzt bei Überschreitung (Abschnitt 5).
5. Voraussetzung (erledigt 2026-07-08): EG2-Fremdgeräte-Guard und
   konfigurierbares `min_limit_w`, damit Zuteilungen < 4200 W die Wallbox
   erreichen statt aufgerundet zu werden.

## 8. Beispielrechnung

VNB dimmt auf 4200 W, PV liefert 3000 W, Batterie idle:

```
Budget = 4200 + 0.8 × 3000 = 6600 W
Runde 1: WP 4200 (Prio 1) → Rest 2400; EV 1380 (Prio 2, 6 A) → Rest 1020;
         Wallbox 0 (Prio 3, Sockel 0)
Runde 2: EV +920 → 2300 W → quantisiert 2070 W (9 A); Rest 330 W → Wallbox 0
Ergebnis: WP 4200 | EV 2070 | WB 0  — netzwirksam ≤ 4200 W eingehalten,
          Haushaltsverbrauch unbegrenzt zusätzlich.
```
