# Musicload – Leitfaden für KI-Agenten

## Zweck

Musicload ist eine selbstgehostete Web-App und CLI zum Suchen, Vorschauen und
Herunterladen von Musik in eine Navidrome-Bibliothek. Die tägliche Bedienung
erfolgt über die installierbare PWA; Navidrome scannt anschließend den
gemeinsam eingebundenen Musikordner.

## Architektur

- `musicload/web/app.py`: FastAPI-App, Routen, Middleware und Web-API.
- `musicload/web/static/` und `musicload/web/templates/`: PWA-Oberfläche.
- `musicload/search.py`, `download.py`, `queue.py`, `tagging.py`: Suche,
  Download-Warteschlange und Metadaten.
- `musicload/config.py`: fasst Umgebungsvariablen und gespeicherte
  Web-Einstellungen zusammen.
- `musicload/settings.py`: speichert Web-Einstellungen atomar in
  `/data/settings.json`.
- `musicload/web/auth.py`: Navidrome-Anmeldung und signierte Browser-Sitzungen.

## Persistente Daten und Docker

`docker-compose.yml` bindet `./.musicload` nach `/data` ein. Dieser Mount ist
Teil der Funktionalität, nicht bloß ein Cache: Er enthält Einstellungen,
Sitzungsschlüssel, Warteschlangen-/Bibliotheksdaten, Logs und optionale
`cookies.txt`. Änderungen dürfen diesen Mount nicht stillschweigend löschen
oder durch einen flüchtigen Pfad ersetzen.

Der Login wird ausschließlich als signiertes, `HttpOnly` Browser-Cookie
gespeichert; Passwörter werden nicht persistiert. Der Schlüssel
`session_secret` muss im persistenten `/data/settings.json` oder als stabile
Umgebungsvariable erhalten bleiben. Eine erfolgreiche Sitzung wird bei jeder
authentifizierten Antwort erneuert und ist für zehn Jahre gültig. Sie soll
Container-Neustarts überstehen. Nicht umgehen: explizites Logout, gelöschte
Browserdaten, geänderter Sitzungsschlüssel oder Browser-Richtlinien können
eine erneute Anmeldung erforderlich machen.

## Arbeitsregeln

- Bestehende öffentliche API-Endpunkte, PWA-Installierbarkeit und die
  `/downloads`-Mount-Semantik bewahren.
- Bei Änderungen an Authentifizierung Cookies `HttpOnly`, `SameSite=Lax` und
  bei HTTPS `Secure` beibehalten. Keine Passwörter oder Tokens in Logs,
  Antworten oder Client-Speicher schreiben.
- Einstellungen, die beim App-Start/Middleware-Aufbau gebraucht werden,
  gehören in `RESTART_REQUIRED_SETTINGS` in `musicload/settings.py`.
- Änderungen an gespeicherten Einstellungen müssen den persistenten
  `data_dir` verwenden und atomar gespeichert werden.
- Bei JavaScript-Änderungen die PWA und mobile Nutzung mitdenken. Der Service
  Worker ist absichtlich network-first.
- Prüfe nach Python-Änderungen mindestens die Syntax mit
  `python -m compileall musicload`. Wenn Tests ergänzt werden, keine externen
  Dienste (Navidrome/YouTube) voraussetzen.
