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
- **SYS-15:** OpenEEBus auf den aktuellen Upstream-Stand bringen und die
  bestehende HEMS-Funktionalitaet erhalten. Lokale Anpassungen vorab erfassen,
  bereits upstream enthaltene Aenderungen abgleichen und verbleibende
  Anpassungen gezielt portieren. Abnahme: OpenEEBus- und HEMS-Host-Tests,
  ESPHome-Build sowie Regressionen fuer CS/EG, LPC, OSSHPCF und Reconnect;
  hardwareabhaengige Nachweise separat dokumentieren. Den geprueften
  Submodul-Commit pinnen und den bisherigen Stand fuer Rollback festhalten.
- **SYS-16:** Erkenntnisse aus der HEMS-/OpenEEBus-Integration auf geeignete,
  allgemein nutzbare Upstream-Beitraege pruefen und bei belastbaren Kandidaten
  gezielte PRs erstellen. Vorher aktuellen Upstream, offene Issues/PRs,
  CONTRIBUTING, PR-Vorlagen, CI und konkrete Maintainer-Rueckmeldungen pruefen.
  Neue Features vorab gemaess CONTRIBUTING abstimmen; keine reinen
  Kosmetik- oder installationsspezifischen Patches einreichen. Kleine,
  eigenstaendige Aenderungen mit Reproduktion und Regressionstest, verlangter
  clang-format-Version und Repository-Formatierung liefern. PR-Beschreibung
  mit Problem, Loesung, Testnachweisen und verbleibenden Einschraenkungen nach
  Maintainer-Vorgaben verfassen; als Draft beginnen, wenn noch Nachweise fehlen.
  Abnahme: geeignete PRs samt Links dokumentiert oder begruendet festgehalten,
  warum sich aktuell kein Beitrag anbietet.

Abnahme: Ein frischer Checkout inklusive Submodule kann alle nicht-hardware-
gebundenen Pruefungen mit einem dokumentierten Befehl lokal und in CI
ausfuehren.

### OpenEEBus-Integrationsstand 2026-10-07

- Eingearbeiteter Upstream: `c31acf5ef36bf966d11600f74f62fbdafb3a7470`.
  Ausgangsstand des lokalen Submoduls:
  `1c7dd53d5f21122a3e39d21e54ce644fbd07f50d`; bisheriger Gitlink im
  Hauptrepository: `41b6b7e567d5ba5f314188a8ab6767b3f533fd2a`.
- Merge-Commit: `89ee840` auf dem Fork-Branch `hems`; mit ESP-IDF-Headerfix
  `f714766` im Hauptrepository gepinnt. Commit, Push, OTA und Tests wurden am 2026-10-07
  vom Betreiber freigegeben. Ausgangsrevisionen fuer Rollback erhalten;
  kein automatischer Reset im Arbeitsbaum mit uncommitteten Aenderungen.
- Upstreams geraetebezogener Event-Manager ersetzt die lokale Eventbus-
  Filterung; CS/EG-Mehrinstanzen bleiben isoliert. DeviceDiagnosis-Szenario 3,
  Pairing-Fenster und SHIP-ID-Weitergabe sind weiterhin vorhanden.
- Bewusst lokal erhalten: ESP32-mDNS nur als Advertisement mit eigenen
  Diensteintraegen im gemeinsamen Daemon, SPINE-Thread mit 12 KB Stack und
  Heartbeat alle 45 Sekunden bei angekuendigtem Timeout von 60 Sekunden.
- Pairing-Abbruch: Verbindung bis zum regulaeren Close-Ereignis im Container
  halten, keine Retries und keine nachtraegliche Handshake-Bestaetigung;
  erst nach Stop und Disconnect-Meldung freigeben. Zwei neue Unit-Tests
  pruefen den Verbindungszustand, zwei weitere die Heartbeat-Sendemarge.
- EV-Demo: eigener mDNS-Dienstname verhindert die reproduzierte Kollision
  mit dem HEMS-Demo. Das betrifft die Software-Testdemos, nicht unsere
  bereits getrennt benannten ESP32-Instanzen.
- Lokal bestanden: 833/833 OpenEEBus-CTest, 2/2 HEMS-Host-CTest
  (OSSHPCF-SEMP-Decoder und Xemex), acht Offline-Wallbox-Fixtures,
  ESPHome-Clean-Build plus abschliessender Build (19,2 % RAM, 16,1 % Flash).
  Nach Upstream-Updates mit neuen C-Quellen den Task `Clean ESPHome build`
  vor `Validate ESPHome firmware` ausfuehren, damit CMake neue Wrapper erfasst.
- Wiederholbare Software-Integration: Task
  `Validate OpenEEBus integration in Docker`; Skript
  `tools/test_openeebus.sh` baut temporaere Demo-Kopien im isolierten
  Docker-Netz. Avahi und `libnss-mdns` sind erforderlich. Berichte unter
  `build/openeebus-integration/`, Unit-Testberichte unter
  `build/openeebus-unit/`. Kein Host-Netz und keine realen Geraetezugriffe.
  Ergebnis: 32/32 bestanden, inklusive LPC/LPP, MPC/MGCP, EV-Disconnect bei
  weiterlaufender HP-Regelung, drei Drei-Geraete-Stressdurchlaeufen,
  drei Shutdown-Pruefungen und 20 SIMOPEN-Durchlaeufen.
- Eigene C/C++-Aenderungen mit clang-format 18 formatiert; eigener Diff gegen
  Upstream whitespace-sauber. Bereits upstream enthaltene Markdown-
  Zeilenumbrueche und Leerzeilen bleiben unveraendert.
- Linux-CI deckte die falsche cJSON-Headerauswahl ohne `__freertos__` auf:
  `ESP_PLATFORM` waehlt jetzt in den drei JSON-Dateien ebenfalls `cJSON.h`.
  Der Task `Validate ESP-IDF JSON headers in Docker` prueft diese Auswahl
  auf einem case-sensitiven Dateisystem, ohne die Speicherverwaltung umzuschalten.
- Erster Live-Lauf zeigte nach Bosch-SPINE-Discovery einen PC-0-Absturz
  (`InstrFetchProhibited`). Upstream ruft vorhandene CS-/EG-Listener ohne
  NULL-Pruefung auf. Integrationsfix `d643778` ersetzt drei ungenutzte
  NULL-Callbacks durch typisierte No-op-Funktionen; ESPHome-Build bestanden.
- OTA am 2026-10-07 um 10:57 erfolgreich. Live-Build `Oct 7 2026 10:56:20`
  auch um 11:08 bestaetigt; Bosch verbunden mit `CS/LPC | MU/MPC`, seit OTA
  kein weiterer Neustart beobachtet. GitHub-CI fuer `d643778` erfolgreich
  (Run `37597225852`); wiederholte Software-Integration ebenfalls 32/32.
  Reale CLS-Limits, ESP32-Pairing/Abbruch/Reconnect und vollstaendige
  Xemex-Hardwareabnahme bleiben separat zu pruefen. Ein erfolgreicher
  Startlauf ersetzt keinen Langzeitnachweis. Vor OTA isoliert neu bauen
  und die tatsaechliche Build-Time-Entity pruefen.

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
