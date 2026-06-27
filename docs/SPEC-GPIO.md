# 7. Spezifikation der GPIO-Matrix (Setup-Tab & Sicherheits-Logik)

Das GPIO-Management wird als tabellarische Matrix in einem eigenen Reiter ("GPIO") im Setup-Menü realisiert. Jede Zeile repräsentiert einen physischen Pin und ist strikt in folgende Spalten unterteilt:

## Spalte 1: GPIO-Auswahl (ID)
- Eine Liste aller physisch nutzbaren GPIO-Pins des Raspberry Pi.
- System-Pins (z. B. aktive I2C-Leitungen für Temperatursensoren oder serielle UART-Schnittstellen) werden vom Backend automatisch gefiltert und für allgemeine Schaltaufgaben blockiert.

## Spalte 2: Elektrische Charakteristik
Dropdown-Optionen zur elektrischen Konfiguration des Pins:
- **Richtung:** `OUTPUT` (Standard für Peripherie-Aktoren).
- **Logik-Pegel:** `Active-High` (+3,3V bei Aktivierung) oder `Active-Low` (Masse/0V bei Aktivierung – optimiert für Standard-Relaisplatinen).
- **Ausgangs-Modus:** `Push-Pull` (Standard) oder `Open-Drain`.

## Spalte 3: Trigger & Bedingungen
Dropdown-Optionen, welches Systemereignis die Schaltung primär auslöst:
- `Bei TX (PTT)` -> Kopplung an den Sendezustand des Transceivers.
- `Kopfzeilen-Button 1` -> Manuelle Schaltung über den ersten Button in der Top-Bar.
- `Kopfzeilen-Button 2` -> Manuelle Schaltung über den zweiten Button in der Top-Bar.
- `Band-Decoder (CAT)` -> Automatisierte frequenzabhängige Schaltung via Hamlib.
- `Temperatur-Schwellenwert` -> Automatisierte Lüftersteuerung gekoppelt an einen I2C-Sensor.

## Spalte 4: Parameter & Kombinations-Abhängigkeiten
Ein dynamisches Feld, dessen Eingabeoptionen vom gewählten Trigger (Spalte 3) abhängt:
- **Bei Kopfzeilen-Button 1/2:** Freitextfeld für das UI-Label (beschriftet den Button in der Kopfzeile; ist kein Pin zugewiesen, bleibt der Button `hidden`).
- **Bei PTT:**
  - Numerisches Eingabefeld für das Sequencer-Delay (`sequencer_delay_ms`).
  - *Kombinations-Abhängigkeit (Dropdown):* Optionale Verknüpfung mit einem Kopfzeilen-Button (z. B. Schaltung erfolgt nur, wenn `TX aktiv` UND `Kopfzeilen-Button 1 [PA Scharf]` eingeschaltet ist).
- **Bei Band-Decoder:** Dropdown zur Amateurband-Auswahl (z. B. 2m, 70cm).
- **Bei Temperatur-Schwellenwert:** Numerische Felder für Ein- und Ausschalttemperaturen (Hysterese-Schutz gegen Relais-Flattern).

## Spalte 5: Fail-Safe & Session-Sicherheit (Checkboxen)
Zusätzliche Sicherheits-Flags zur Absicherung des Remote-Shacks:
- **Checkbox `Session-Bound`:** Ist dieses Flag aktiv, wird der Pin permanent durch einen Software-Watchdog (Heartbeat-Überwachung zwischen Web-Frontend und Backend) überwacht. Reißt die Netzwerkverbindung ab oder loggt sich der User aus, fällt der Pin augenblicklich in seinen sicheren, inaktiven Zustand zurück.
- **Fail-Safe-Initialisierung:** Das Backend erzwingt bei jedem Service-Start, System-Reboot oder unvorhergesehenen Software-Crash (via GPIO-Cleanup-Routinen) die sofortige und strikte Rücksetzung aller deklarierten Pins in den sicheren Ruhezustand (`OFF`).

---
> **Frontend:** Tab im Setup-Menü – folgt später.
> **Erstellt:** 2026-06-24
