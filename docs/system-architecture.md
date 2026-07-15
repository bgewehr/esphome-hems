# Zielarchitektur und Entwicklungsplan

## Ziel

Das Projekt entwickelt sich von einer ESPHome-Automation zu einem
eingebetteten Energiemanagementsystem. Die Protokoll- und Geraeteadapter sind
bereits sinnvoll getrennt. Die naechste Ausbaustufe formalisiert den
Regelkreis, ohne stabile OpenEEBus- oder Drittanbieterkomponenten mit lokaler
Policy zu vermischen.

```mermaid
flowchart LR
    Sources[EEBus, Fronius, Zaehler] --> Quality[Messwerte und Qualitaet]
    Config[Betreiberparameter] --> Core[Policy- und Regelkern]
    Quality --> Core
    Core --> Allocator[Budget und Flexibilitaet]
    Allocator --> Adapters[Geraeteadapter]
    Adapters --> Devices[WP, EV, Wallbox, Batterie]
    Devices --> Monitor[Quittierung und Messung]
    Monitor --> Core
```

## Architekturregeln

1. YAML beschreibt ESPHome-Entities, Verdrahtung und Betreiberkonfiguration;
   komplexe Entscheidungen liegen in kleinen testbaren C++-Modulen.
2. Protokolladapter uebersetzen zwischen dem Regelkern und einem Geraet. Sie
   treffen keine globale Verteil- oder Optimierungsentscheidung.
3. Jeder Eingang besteht aus Wert, Einheit, Zeitstempel und Qualitaet.
   Fehlende oder alte Zusatzleistung darf niemals ein groesseres Budget
   erzeugen.
4. Jeder Befehl wird als `requested`, `acknowledged` und `measured`
   betrachtet. Ein positives Protokollergebnis ersetzt keine Wirkkontrolle.
5. Gesetzliche Limits, technische Geraetegrenzen und Failsafe haben Vorrang
   vor Komfort-, Eigenverbrauchs-, Prognose- und Tarifoptimierung.
6. Lokale Policy bleibt im Hauptprojekt. Aenderungen am OpenEEBus-Submodul
   werden nur vorgenommen, wenn die generische Bibliotheksabstraktion fehlt.

## Entwicklungsplan

Die Aufgaben-IDs werden auch in der zentralen [TODO-Liste](../TODO.md)
verwendet. Fachliche Details stehen im
[§14a-Verteilungskonzept](power-distribution-concept.md) und im
[Bosch-OSSHPCF-Konzept](oss-hpcf-bosch.md).

### Phase 0: Dokumentierte Zielarchitektur

- **SYS-00 (erledigt):** Verantwortungsgrenzen, Prioritaeten und fachliche
  Entwicklungsplaene dokumentieren.
- **SYS-01 (erledigt):** Zentrale, ID-basierte TODO-Liste als Statusquelle
  anlegen.

### Phase 1: Reproduzierbare Qualitaetssicherung

- **SYS-10:** Root-CI einrichten. Sie validiert mindestens ESPHome-
  Konfiguration, Python-Syntax, lokale Host-Tests und Submodule-Pinning.
- **SYS-11 (erledigt):** Ein gemeinsames CMake/CTest-Host-Testziel fuer reine
  Regelkernmodule und Protokoll-Fixtures ist unter `tests/` verfuegbar. Der
  erste Test deckt den read-only OSSHPCF-Decoder ab; GitHub Actions und ein
  lokaler Docker-Task fuehren dasselbe Ziel aus.
- **SYS-12:** Fake-Steuerbox und Fake-Wallbox als reproduzierbare
  Szenariotests fuer Limit, Disconnect, Reconnect und fehlerhafte Antworten
  in die Qualitaetssicherung aufnehmen.

Abnahme: Ein frischer Checkout inklusive Submodule kann alle nicht-hardware-
gebundenen Pruefungen mit einem dokumentierten Befehl lokal und in CI
ausfuehren.

### Phase 2: Betriebsmodell und Diagnose

- **SYS-20:** Einheitliche Systemzustaende `normal`, `limited`, `degraded`
  und `failsafe` definieren. Ursache, Eintrittszeit und Rueckkehrbedingung
  werden sichtbar gemacht.
- **SYS-21:** Strukturierte Diagnose fuer Messwertalter, Verbindungen,
  Befehlsstatus, Regelabweichungen, Neustartursache und freien Speicher
  bereitstellen.
- **SYS-22:** Konfigurationswerte beim Start und bei Aenderung gegen
  Geraetefaehigkeiten validieren; ungueltige Kombinationen werden abgelehnt
  statt still geklemmt.

Abnahme: Der Betreiber kann fuer jede aktive Begrenzung erkennen, welche
Quelle, Entscheidung, Anforderung, Quittierung und Messwirkung vorliegt.

### Phase 3: Netzwerk- und Zugangsschutz

- **SYS-30:** Bedrohungsmodell fuer ESPHome API, OTA, Webserver, MQTT,
  Modbus TCP und lokale Simulatoren dokumentieren. Benoetigte Dienste und
  erreichbare Netze werden explizit festgelegt.
- **SYS-31:** Webzugriff und MQTT entsprechend dem Einsatznetz absichern oder
  deaktivieren; Passwoerter, Zertifikate und API-Schluessel bleiben in
  Secrets und erhalten einen dokumentierten Rotationsweg.
- **SYS-32:** Update- und Recovery-Verfahren inklusive Konfigurationsbackup,
  bekannt gutem Firmwarestand und lokalem Flash-Fallback testen.

Abnahme: Kein nicht benoetigter Steuerzugang ist offen; ein fehlgeschlagenes
Update kann ohne Verlust der Betreiberparameter zurueckgerollt werden.

### Phase 4: Optimierung oberhalb des sicheren Regelkreises

Diese Phase startet erst nach Abschluss von BD-42 und OHP-51.

- **SYS-40:** Einheitliche Prognoseschnittstelle fuer PV, Grundlast,
  Waermebedarf und Abfahrtszeit definieren. Prognosen tragen Horizont,
  Erstellzeit und Unsicherheit.
- **SYS-41:** Eigenverbrauchsoptimierung gegen den Dry-run-Planer auswerten,
  bevor sie aktive Entscheidungen beeinflusst.
- **SYS-42:** Optional dynamische Tarife als nachrangiges Optimierungsziel
  einfuehren. Tarifoptimierung darf Compliance- und Komfortgrenzen nicht
  veraendern.

Abnahme: Jede Optimierungsentscheidung ist mit Eingangsprognose, Constraints
und erwarteter Wirkung reproduzierbar; bei fehlender Prognose bleibt das
sichere reaktive Verhalten unveraendert.
