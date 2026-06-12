# QuanshengDock – Funktionsübersicht

> Extrahiert aus dem QuanshengDock Wiki von [nicsure](https://github.com/nicsure/QuanshengDock/wiki)
> Stand: Juni 2026 | QuanshengDock Version 0.32.22q

Dieses Dokument dient als Referenz für die in QuanshengDock (Windows-Desktop-App) verfügbaren Funktionen,
um diese schrittweise in Q-Remote V3 (Web-Interface) zu übertragen.

---

## 1. Hauptfunktionen (Features)

| # | Funktion | Beschreibung | Q-Remote V3 Status |
|---|----------|-------------|-------------------|
| 1 | LCD-Display-Clone | Radio-LCD wird 1:1 auf dem PC angezeigt (Font & Farben wählbar) | ✅ Vorhanden |
| 2 | Channel Editor | Kanäle bearbeiten, mehrere gleichzeitig. Read/Write EEPROM | ✅ Vorhanden (Stations Editor) |
| 3 | Spectrum Analyzer | Spektrumanzeige mit Monitor-Modus | ✅ Vorhanden |
| 4 | Waterfall Display | Wasserfall-Darstellung neben dem Spektrum | ✅ Vorhanden |
| 5 | Audio Passthrough | Audio vom Radio durch den PC schleifen | ✅ Vorhanden (RX/TX Audio) |
| 6 | Enhanced Scanner | Verbesserter Kanal-/Preset-Scanner | ❌ Nicht vorhanden |
| 7 | Hardware VFO (XVFO) | Experimenteller Hardware-Level VFO | ❌ Nicht vorhanden |
| 8 | RepeaterBook Integration | Repeater direkt aus RepeaterBook importieren | ❌ Nicht vorhanden |

---

## 2. XVFO-Funktionen (erweiterter VFO-Modus)

Der XVFO ist der erweiterte Betriebsmodus von QuanshengDock mit vielen Zusatzfunktionen.

### 2.1 VFOs

- **4 VFOs** (A, B, C, D) – umschaltbar per Button oder Tab-Taste
- Jeder VFO hat eigene Frequenz, Modulation, Bandbreite etc.
- **Watch & Respond** – überwacht einen VFO und antwortet automatisch

### 2.2 Frequenz-Steuerung

- **Jog Wheel** – Drehknopf auf dem Bildschirm für Frequenzänderung
- **Mouse Wheel** – ändert Frequenz um eingestellte Schrittweite
- **Einzelne Ziffern** per Mausrad verstellbar
- **Tastatur-Eingabe** – Zahlen für direkte Frequenzeingabe

### 2.3 Modulation

| Modus | Beschreibung | TX möglich |
|-------|-------------|-----------|
| FM | Standard FM | ✅ Ja |
| AM | Amplitudenmodulation | ❌ Nein (nur RX) |
| USB | Upper Sideband | ❌ Nein (nur RX) |
| BYP | Bypass (Test-Modus) | ❌ Test |
| RAW | Raw (Test-Modus) | ❌ Test |
| CW1 | TX Träger, kein Audio, Squelch offen | ✅ Ja |
| CW2 | TX mit Ton | ✅ Ja |

- **F** – Toggle FM Wide/Narrow
- **W** – Bandbreite wechseln
- **M** – Modulationsmodus wechseln (TX immer FM)

### 2.4 Scan-Funktionen

- **Preset Scanner** – scannt durch gespeicherte Kanäle/Preset-Liste
- **RANGE Scanner** – scannt einen Frequenzbereich (RX=Start, TX=Ende, Name beginnt mit "RANGE")
  - Mehrere RANGE-Kanäle gleichzeitig scannbar
  - Bar-Graph-Darstellung für RANGE-Scans
- **Auto Squelch** – automatische Rauschsperre
- Scanner zeigt aktuelle Frequenz im Monitor-Fenster

### 2.5 TX-Steuerung

- **PTT** per Leertaste oder Bildschirm-Button
- **TX Unlock** – standardmäßig ist TX gesperrt, muss explizit freigeschaltet werden
- **TX FM-only** – Standard: kein TX wenn Modus nicht FM, CW1 oder CW2 (abschaltbar)
- **1750Hz Ton** – Taste **T** während TX sendet 1 Sekunde 1750Hz-Ton (Repeater-Aufruf)

### 2.6 Squelch & Gain

- **Q** – Squelch toggeln (open/close)
- **S** – Squelch-Stufe wechseln (SQ-1 bis SQ-4 oder -R)
- **AGC** – Automatic Gain Control (normaler Modus)
- **RF Gain** – manueller RF-Gain-Regler (overrides AGC wenn aktiv)

### 2.7 Mikrofon & Audio

- **Mic Gain** – Mikrofonverstärkung einstellbar (Taste **G**)
- **Mic Boost** – bis zu 10x Verstärkung im Passthrough-Modus
- **Mic Level Indicator** – Pegelanzeige für Mikrofon (in Settings aktivieren)
- **VOX** – Sprachgesteuerte TX, Empfindlichkeit in Settings einstellbar
  - VOX-PTT: TX-Button wird rot, "VOX" wird blau

### 2.8 CTCSS/DCS

- **RX CTCSS/DCS** – Tonruf für Empfang implementiert (XVFO)
- **DTMF Decoding** – DTMF-Töne werden dekodiert
- **DTMF TX** – Tasten 0-9, A-D, *, # senden DTMF-Töne während TX

### 2.9 Power

- **P** – Sendeleistung durchschalten

### 2.10 Logging

- **Scan-Log** im CSV-Format (in Settings aktivierbar)
- Spalten: Datum, Zeit, RX-Frequenz, Modulation, Signalstärke, Kanalname, Dauer
- Speicherort: `Documents/QuanshengDock/scanlogs/`

### 2.11 Messenger

- Eigenes Messenger-Fenster
- Callsign in Settings konfigurierbar

---

## 3. Keyboard-Shortcuts (QuanshengDock Referenz)

| Taste | Funktion |
|-------|----------|
| A/B/C/D | VFO A/B/C/D auswählen |
| Tab | Zwischen VFOs wechseln |
| PgUp/PgDown | Presets/Kanäle wechseln |
| Q | Squelch open/close |
| S | Squelch-Stufe wechseln |
| W | Bandbreite |
| F | FM Wide/Narrow |
| M | Modulation wechseln |
| G | Mic Gain |
| P | Power wechseln |
| T | 1750Hz Ton (während TX) |
| Space | PTT (TX ein/aus) / Scan Pause/Continue |
| Esc | Scan stoppen |
| Enter | Frequenz bestätigen / Menü aktivieren |
| Backspace / - | Eingegebene Ziffern löschen |
| 0-9, . | Frequenz eingeben |
| F12 | Passthrough-Bar toggle |

---

## 4. Channel Editor

- Kanäle aus Radio-EEPROM lesen (Read) und schreiben (Write)
- Mehrere Kanäle gleichzeitig bearbeiten
- Import/Export als CSV
- **Import zu XVFO Presets** – Kanäle aus dem Editor in XVFO übernehmen
- **RepeaterBook Integration** – Repeater-Daten direkt importieren
- Negatives Offset möglich (Bug wurde gefixt)

---

## 5. Netzwerk / Remote

- **QD Network Host (QDNH)** – Radio über Netzwerk steuerbar
- Von Remote-PC aus verbinden
- **CAT Control** – über seriellen Port (virtuell oder real), aktuell nur Frequenz

---

## 6. Multi-Radio

- Mehrere Radios an einem PC durch Config-Datei-Namen als Command-Line-Parameter
- Jede Instanz hat eigene Config

---

## 7. Bekannte Einschränkungen

- **CHIRP inkompatibel** – funktioniert nicht zusammen mit QuanshengDock-Firmware
  - Workaround: Menu 60 → Remote OFF, dann CHIRP nutzen, danach wieder ON
- **Externe Antenne empfohlen** – 5W TX in PC-Nähe kann Hardware zerstören (RFI/EMP)
- **XVFO + Hardware-PTT** – physischer PTT unterbricht serielle Verbindung, nur Software-PTT möglich
- **5 Sekunden Verzögerung** – nach XVFO-Exit oder Start (Synchronisation Radio↔Software)

---

## 8. Offene TODOs für Q-Remote V3

Funktionen aus QuanshengDock die potenziell für Q-Remote V3 relevant sind:

### Hochpriorität
- [ ] **Preset Scanner** – scannt gespeicherte Kanäle mit Signalanzeige
- [ ] **Range Scanner** – Frequenzbereich scannen mit Bar-Graph
- [ ] **Auto Squelch**
- [ ] **VOX** (sprachgesteuerte TX)
- [ ] **1750 Hz Ton** (Repeater-Aufruf)
- [ ] **DTMF TX** (Tasten 0-9, A-D, *, #)
- [ ] **Squelch-Stufen** (SQ-1 bis SQ-4)

### Mittlere Priorität
- [ ] **Mic Gain / Boost** – einstellbare Mikrofonverstärkung
- [ ] **Mic Level Indicator** – Pegelanzeige
- [ ] **Keyboard Shortcuts** – passende Web-Äquivalente
- [ ] **AGC/RF Gain** Steuerung
- [ ] **TX Unlock** – Sicherheitsfeature, TX standardmäßig gesperrt
- [ ] **Scan-Logging** (CSV-Export)
- [ ] **DTMF Decoding** (Anzeige empfangener DTMF-Töne)

### Niedrige Priorität
- [ ] **RepeaterBook Integration**
- [ ] **Messenger**
- [ ] **CAT Control** (virtueller serieller Port)
- [ ] **Multi-VFO** (A/B/C/D)

---

## Quellen

- Wiki: https://github.com/nicsure/QuanshengDock/wiki
- YouTube Demos: https://www.youtube.com/@nicsure/videos
- Firmware: https://github.com/nicsure/quansheng-dock-fw
- Referenz-Code: https://github.com/nicsure/QuanshengDock
