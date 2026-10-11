# Musicload 0.25.0 – Analyse und Bereinigung

Stand: 11. Oktober 2026.

## Ergänzung 0.25.1: Library und Design

- Design nach dem bereitgestellten Apple-Design-Skill verfeinert:
  Systemschrift, klare Tab-Auswahl, direkte Rückmeldung und ausgerichtete
  Player-Symbole. Reduzierte Transparenz und höherer Kontrast werden beachtet.
- „Clean up“ öffnet „Duplicates“ und „Empty folders“. Erst „Start scan“
  durchsucht die Bibliothek. Löschen bleibt eine bestätigte Einzelaktion.
- Duplikatkandidaten verwenden bereinigte Namen, unscharfe Vergleiche,
  Künstler-/Laufzeitprüfung und SHA-256 für identische Dateiinhalte.
  Ähnliche Namen sind keine Garantie für identische Musik; ohne ähnliche
  Metadaten werden verschiedene Kodierungen nicht akustisch erkannt.
- Track-Löschen entfernt zugehörige Texte und danach leere Verzeichnisse.
  Weitere Songs, gemeinsam verwendete Texte und unbekannte Dateien bleiben.
- Die Ordneransicht findet auch musikfreie Unterbäume mit Restdateien.
  Unbekannte Dateien oder Verknüpfungen blockieren deren Löschung; unmittelbar
  vor jeder Löschung wird der Inhalt erneut geprüft.
- 22 Offline-Tests sowie Python-/JavaScript-Syntaxprüfung bestanden.
  Mobile Ansicht, manuelle Scans und lokale Wiedergabe mit Testdateien geprüft.

## Technische Basis

Musicload verwendet Python/FastAPI für HTTP-APIs, Server-Sent Events für
Warteschlangenänderungen, `ytmusicapi` für die YouTube-Music-Suche und
`yt-dlp` für die Medienextraktion. Deno verarbeitet YouTubes JavaScript-
Challenges; FFmpeg erzeugt MP3-Vorschauen und konvertiert Downloads.
Die PWA verwendet HTML, CSS und JavaScript ohne zusätzliches UI-Framework.
Navidrome liest den gemeinsam eingebundenen Musikordner.

## Befunde und Änderungen

- Die aktive Seite enthielt über 3.000 Zeilen eingebettetes JavaScript.
  Daneben lagen abweichende, ungenutzte JavaScript-Kopien. Die tatsächlich
  verwendete Logik wurde in die vorhandenen Dateien übernommen; alle
  Initialisierungen laufen nach dem Laden ihrer Abhängigkeiten.
- Unbenutzte Router, CLI-, Download- und Metadatenmodule duplizierten aktive
  Funktionen. Sie wurden nach Prüfung der Importverbindungen entfernt.
  Alle 46 öffentlichen API-Routen bleiben vorhanden.
- Ein ungenutzter JavaScript-Altstand verwies auf nicht vorhandene
  Wiedergabe-Endpunkte. Die aktive Oberfläche verwendete bereits den
  funktionierenden Server-Preview-Endpunkt; der Altstand wurde beseitigt.
- Zwei Stylesheets und mehrere frühere Designstände überlagerten sich.
  Es gibt jetzt eine Stylesheet-Datei; überstimmte Deklarationen wurden
  entfernt. Farben, Fokus, Dialoge und Bewegungen sind vereinheitlicht.
  Teure Hintergrundunschärfe und pauschale Übergänge wurden reduziert.
- Metadaten laufen bei Überlänge mit gleicher Geschwindigkeit im Kreis.
  Nicht sichtbare Texte und Hintergrundseiten pausieren die Animation.
  Bei reduzierter Bewegung werden Namen vollständig umgebrochen.
- Identische Warteschlangenstände erzeugen keine neuen Karten; die Zuordnung
  von Downloadknöpfen verwendet Indizes statt wiederholter linearer Suchen.
- Fehlende Cover verschoben Text in die Bildspalte. Feste Grid-Zuordnungen
  und das vorhandene Logo als Ersatzbild halten die Zeilen stabil.
- Suchlinks ohne expliziten Tab fielen nach dem Neuladen zurück auf Explore.
  Ein vorhandener Suchbegriff stellt jetzt die Songs-Ansicht wieder her.
- Vorschauen besitzen eine begrenzte Vorbereitung und beenden FFmpeg auch
  bei Abbruch während des Starts. Wiedergabezustände folgen Browserereignissen.
  Vorschauen schreiben weiterhin keine Musikdateien in die Bibliothek.
- Der unveränderte Logostern wurde als SVG und hochauflösende PNGs umgesetzt.
  Die alten großen README-Bilder wurden durch aktuelle JPEGs ersetzt.

## Aktualisierungen

`uv.lock` wurde vollständig neu aufgelöst: unter anderem `yt-dlp`
2026.10.10.233141.dev0, `ytmusicapi` 1.12.3, FastAPI 0.143.0,
Uvicorn 0.54.0 und Mutagen 1.48.1. Der bisherige Nightly-Kanal von
`yt-dlp` bleibt erhalten. Docker verwendet aktuelle, durch Digest fixierte
Python-3.14-, Deno-2.9.7- und uv-0.13.0-Images.

Die offiziellen GitHub-Actions-Versionen wurden ebenfalls geprüft und
fixiert. Vor dem Veröffentlichen laufen Syntax- und Regressionstests.
Pull Requests führen Prüfungen aus, veröffentlichen aber kein Image.
Dependabot schlägt wöchentlich Aktualisierungen vor.

Start und Shutdown verwenden FastAPIs aktuellen Lifespan-Mechanismus.
Siehe [FastAPI-Lifespan-Dokumentation](https://fastapi.tiangolo.com/advanced/events/),
[yt-dlp EJS](https://github.com/yt-dlp/yt-dlp/wiki/EJS) und
[uv mit Dependabot](https://docs.astral.sh/uv/guides/integration/dependabot/).

## Umfang und Prüfung

Der Vergleich der versionierten Dateien vor der Bereinigung und der
auszuliefernden Dateien danach ergibt rund **4,3 MB weniger**. Das ist eine
Reduktion der Repository-Dateien, keine gemessene Docker-Image-Ersparnis;
Dokumentationsbilder waren bereits vom Docker-Build ausgeschlossen.
Git behält gelöschte Bilder in der bisherigen Historie.

- Elf Offline-Regressionstests bestehen mit Python 3.12 und 3.14.
- Python- und JavaScript-Syntax geprüft; Sperrdatei konsistent.
- Python-Quellpaket und Wheel erfolgreich gebaut; alle PWA-Dateien enthalten.
- Desktop und 390-Pixel-Handyansicht mit lokalen Beispieldaten geprüft:
  Suche, Formatwahl, Dialog, Wiedergabe, Pause/Stop und lange Namen.
  Kein horizontaler Überlauf in der geprüften Handyansicht.
- `/data`, `/downloads`, Sitzungsschlüssel, Cookie-Sicherheit und
  network-first-Service-Worker bleiben erhalten.

Docker ist auf diesem Arbeitsplatz nicht verfügbar. Ein echter Docker-Build
für AMD64/ARM64 und YouTube-/FFmpeg-Wiedergabe auf dem Server wurden deshalb
nicht lokal getestet. Ein Paketupdate allein garantiert keine dauerhaft
funktionierende YouTube-Extraktion; auch Quelländerungen und erforderliche
Cookies können Einfluss haben.

Nach dem Push den Workflow prüfen, das neue Image beziehen und auf dem
Server Suche, Vorschau, Download, lokale Wiedergabe und den bestehenden
Login nach einem Container-Neustart testen. Die vorhandenen Volumes behalten.
