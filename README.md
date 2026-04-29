# FLAIR Control Deck — Payload Ground Station

> **Iteration 15 — Final Release**
> A full-featured ground station GUI for receiving, decoding, visualising, and commanding a high-altitude UAV/balloon atmospheric payload over Wi-Fi or serial link.

---

## Table of Contents

1. [Overview](#overview)
2. [System Architecture](#system-architecture)
3. [Hardware Requirements](#hardware-requirements)
4. [Software Requirements & Installation](#software-requirements--installation)
5. [Running the Ground Station](#running-the-ground-station)
6. [Connecting to the Payload](#connecting-to-the-payload)
   - [Wi-Fi (TCP) Connection](#wi-fi-tcp-connection)
   - [Serial Connection — USB Cable (Text Mode)](#serial-connection--usb-cable-text-mode)
   - [Serial Connection — DB9 / Radio Adapter (Binary Mode)](#serial-connection--db9--radio-adapter-binary-mode)
7. [User Interface Walkthrough](#user-interface-walkthrough)
   - [Title Bar & Status Bar](#title-bar--status-bar)
   - [Left Control Panel](#left-control-panel)
   - [Graph Dashboard](#graph-dashboard)
   - [Packet Monitor](#packet-monitor)
8. [Fan Control (Uplink)](#fan-control-uplink)
9. [Mission Workflow](#mission-workflow)
10. [Post-Flight File Loading](#post-flight-file-loading)
11. [Test / Simulation Mode](#test--simulation-mode)
12. [Saving Mission Data](#saving-mission-data)
13. [Packet Protocol Reference](#packet-protocol-reference)
    - [Downlink — Channel 0 (232)](#downlink--channel-0-232)
    - [Uplink — Channel 1 (233)](#uplink--channel-1-233)
    - [Reading a Raw Packet Line](#reading-a-raw-packet-line)
    - [Checksum — Fletcher-16](#checksum--fletcher-16)
14. [Sensor Conversion Formulas](#sensor-conversion-formulas)
15. [Atmospheric Science Background](#atmospheric-science-background)
16. [Troubleshooting](#troubleshooting)
17. [Known Limitations & Future Work](#known-limitations--future-work)
18. [Change Log (Key Fixes)](#change-log-key-fixes)

---

## Overview

The **FLAIR Control Deck** is a Python/Tkinter desktop application that acts as the ground station for the FLAIR atmospheric research payload. It:

- **Receives** binary or ASCII telemetry packets from the payload Arduino over Wi-Fi (TCP) or serial (USB / DB9 radio adapter).
- **Decodes** BST-framed 42-byte downlink packets (`PAYLOAD_DATA_CHANNEL_0 = 232`) containing temperature, humidity, laser spectroscopy data, fan speed, timing, and loop counters.
- **Visualises** all sensor streams in a live 3×3 matplotlib dashboard with atmospheric layer shading, isotope plots, and laser signal panels.
- **Commands** the payload fan speed by sending uplink packets (`PAYLOAD_DATA_CHANNEL_1 = 233`) back to the Arduino.
- **Logs** every received packet to an in-app hex monitor and can save all mission data to CSV files and a PNG dashboard snapshot.
- **Replays** saved binary, text, or CSV log files in post-flight analysis mode.
- **Simulates** a full 0–12 km atmospheric sounding with physically accurate synthetic data when no hardware is connected.

---

## System Architecture

```
┌──────────────────────────────────────────────────────────────────────┐
│                         PAYLOAD (Arduino)                            │
│  Sensors: HMP60 (T/RH), TDLAS laser (H₂O mixing ratio), fan PWM    │
│  Serial ports:                                                        │
│    Serial (USB/UART)  → ASCII debug lines: packetNum=N, packet=[…]  │
│    serialDB9 (NeoSWSerial, pins 3/4) → RAW 42-byte binary packets   │
│  Wi-Fi module → RAW 42-byte binary over TCP                          │
└──────────────┬───────────────────────────────┬──────────────────────┘
               │ USB cable                     │ DB9 / Wi-Fi
               ▼                               ▼
   TextModePacketParser              BinaryPacketBuffer
   (parses hex from ASCII lines)     (finds 0x55 0x24 header)
               │                               │
               └─────────────┬─────────────────┘
                             ▼
                       PacketParser
                  (decode 42-byte struct)
                             │
                             ▼
                        data_queue
                             │
                             ▼
                    FLAIRControlDeck UI
              (plots, hex monitor, fan control)
```

---

## Hardware Requirements

| Component | Details |
|-----------|---------|
| Payload Arduino | Running `payload_code_v5.ino` |
| Connection path | USB cable **or** DB9 serial adapter **or** Wi-Fi TCP module |
| Wi-Fi network | Ground station PC must be on the **SWIFT STATION** Wi-Fi network (router IP: `192.168.0.47`) |
| Logo image | Optional `logo.png` file in the same directory as the script — displays in the splash screen and top bar |

---

## Software Requirements & Installation

**Python version:** 3.10 or later (uses `str | None` union type hints)

### Install dependencies

```bash
pip install pyserial pillow matplotlib numpy
```

All other imports (`tkinter`, `socket`, `struct`, `re`, `threading`, `queue`, `os`, `csv`) are part of the Python standard library.

### Verify your installation

```bash
python -c "import tkinter, serial, PIL, matplotlib, numpy; print('All OK')"
```

### File layout

```
your_project_folder/
├── Grad_project_Code_iteration_15_-_FInal.py   ← Ground station script
└── logo.png                                     ← Optional splash/header logo
```

---

## Running the Ground Station

```bash
python "Grad_project_Code_iteration_15_-_FInal.py"
```

On launch, the application will:

1. Print a detailed **Packet Structure Guide** to the terminal (colour-coded, shows every byte field).
2. Display a **fullscreen splash screen** with an animated logo and progress bar for ~3 seconds.
3. Open the main **FLAIR Control Deck** window in fullscreen.

Press `Escape` or `F11` at any time to toggle fullscreen mode.

---

## Connecting to the Payload

### Wi-Fi (TCP) Connection

The ground station operates as a **TCP server** and waits for the payload Wi-Fi module to connect to it.

1. Ensure the ground station PC is connected to the **SWIFT STATION** Wi-Fi network.
2. Click **CONNECT WiFi** in the left panel.
3. The dialog will auto-detect your PC's LAN IP. Leave the Bind IP as auto-detected or type it manually.
4. Confirm the port (default: `55555`) matches the Arduino Wi-Fi module's target port.
5. Click **CONNECT** — the server starts listening.
6. Power on the payload. When the Arduino's Wi-Fi module connects, the connection status updates to `WiFi: ✅ (payload ✅)`.

**Troubleshooting Wi-Fi:**

- `WinError 10049` — Your PC is not on the SWIFT STATION Wi-Fi network. Connect first.
- `WinError 10013` — Port blocked by Windows Firewall. Add an inbound rule for TCP port 55555, or try a different port.

The Wi-Fi path receives **raw binary packets** (42 bytes, framed with `0x55 0x24`).

---

### Serial Connection — USB Cable (Text Mode)

When the Arduino is connected by USB cable, it outputs human-readable debug lines on its hardware UART:

```
packetNum=042, packet=[55,24,E8,00,22,00,A1,0F,00,00,2A,40,...]
```

The ground station **auto-detects** this format. No manual mode switching is needed.

1. Connect the Arduino to the PC with a USB cable.
2. Click **CONNECT SERIAL**.
3. Select the correct COM port from the dropdown (click **↻ Refresh Ports** if the port is not listed).
4. Set baud rate to **9600** (matches `serial_baud` in the Arduino sketch).
5. Click **CONNECT**.

The **Serial mode** indicator in the status bar will show `Serial: TEXT mode (USB)` once the first packet arrives.

---

### Serial Connection — DB9 / Radio Adapter (Binary Mode)

When the payload is airborne and communicating via a DB9 radio link, the Arduino's `serialDB9` port (NeoSWSerial, pins 3/4) sends **raw 42-byte binary packets**.

Follow the same steps as USB above, but:
- Select the COM port mapped to the DB9 / radio adapter.
- Use baud rate **9600**.

The auto-detector will see `0x55 0x24` as the first two bytes and switch to binary mode. The status bar will show `Serial: BINARY mode (DB9)`.

---

## User Interface Walkthrough

### Title Bar & Status Bar

| Element | Description |
|---------|-------------|
| Title label | Shows `FLAIR CONTROL DECK · Payload Ground Station [RX CH0=232 | TX CH1=233]` |
| Status entry | Last status message (connection events, save confirmations, errors) |
| Serial mode indicator | `Serial: TEXT mode (USB)` / `Serial: BINARY mode (DB9)` / `Serial: —` |
| 🌙 / ☀ button | Toggles dark/light theme |

### Left Control Panel

| Button | Action |
|--------|--------|
| **CONNECT WiFi** | Opens Wi-Fi settings dialog and starts TCP server |
| **CONNECT SERIAL** | Opens serial settings dialog and starts receive thread |
| **START MISSION** | Begins mission timer; if no connection, starts synthetic test data stream |
| **END MISSION** | Stops mission timer and triggers auto-save |
| **SAVE DATA** | Saves all received data to CSV files + PNG dashboard snapshot |
| **📂 LOAD PACKETS** | Opens a file to replay in post-flight analysis mode |

Below the buttons you will find:

- **Connection status** — live indicator for Wi-Fi and Serial connections
- **UTC clock** — current UTC time, updated every second
- **Mission timer** — elapsed time since mission start
- **Packet stats** — total received packets and failed-parse count
- **Fan control** — described in the next section

### Graph Dashboard

The 3×3 matplotlib grid shows the following panels:

| Position | Panel | Description |
|----------|-------|-------------|
| Top-left | Wind Speed vs Altitude | Sounding profile with atmospheric layer shading |
| Top-centre | Temperature vs Altitude | HMP60 temperature profile |
| Top-right | Payload Status | Latest decoded field values, connection/fan state |
| Mid-left | Pressure vs Altitude | Barometric pressure profile |
| Mid-centre | Rel. Humidity vs Altitude | HMP60 humidity profile |
| Mid-right | Laser — Mixing Ratio | H₂O mixing ratio (g/kg) vs packet number |
| Bottom-left | δ¹⁸O Isotope vs Altitude | Rayleigh distillation estimate vs altitude |
| Bottom-centre | UTC & Mission Clock | Large-text clocks updated every second |
| Bottom-right | Laser — Background & Peak | Detector baseline and absorption peak signal |

**Altitude profile vs. time-series mode:** When the altitude data spans less than 50 m (e.g. ground-level CSV logs), the X and Y axes automatically swap to a time-series view (packet index on X, sensor value on Y). This is detected automatically — no configuration needed.

**Atmospheric layer shading** (altitude-profile mode only):

| Layer | Altitude | Colour |
|-------|----------|--------|
| Surface Roughness Layer (SRL) | 0–100 m | Orange |
| Planetary Boundary Layer (PBL) | 100 m – PBL top | Green |
| Transition / Residual Layer | PBL top – ~5 km | Yellow |
| Free Troposphere (FT) | ~5 km – 18 km | Royal blue |

The PBL top is estimated dynamically by the `BoundaryLayerAnalyzer` using the potential temperature gradient and wind shear from live data.

Plots refresh at most every **500 ms** to keep the UI responsive during high-rate reception.

### Packet Monitor

The green-on-black scrolling panel at the bottom of the right column shows every received packet in real time:

- **Timestamp** and internal packet counter
- **Raw hex dump** (16 bytes per row with ASCII sidebar)
- **Decoded fields** — loop timing, fan PWM, temperature, humidity, laser MR/bkg/peak
- Failed packets are highlighted in orange with the parse error message

The monitor is automatically trimmed to the last 500 lines to prevent unbounded memory growth. Click **CLEAR** to reset it manually.

---

## Fan Control (Uplink)

The fan control section commands the payload fan via `PAYLOAD_DATA_CHANNEL_1 = 233` uplink packets. The Arduino's `readDB9()` handler reads `buffer[6]` as the new PWM duty cycle.

| Control | Description |
|---------|-------------|
| **▶ START FAN / ■ STOP FAN** | Toggle fan on/off; sends 0 PWM when stopped |
| **Power Level display** | Shows current power percentage |
| **Preset buttons** | One-click presets: 10%, 25%, 50%, 75%, 100% |
| **Fine adjust slider** | Drag to set any value 0–100% |
| **Last TX indicator** | Shows last sent PWM value and path (WiFi/Serial/offline) |

Fan commands are sent over **Wi-Fi first** (if connected); Serial is used as fallback. The ground station avoids re-sending the same PWM value twice in a row.

In **post-flight file mode**, all fan controls are disabled (greyed out) because there is no live uplink path.

---

## Mission Workflow

A typical mission follows this sequence:

```
1. Launch ground station
2. Connect WiFi or Serial
3. Click START MISSION
4. Monitor telemetry in real time
5. Adjust fan speed as needed
6. Click END MISSION  →  auto-saves data
```

If neither Wi-Fi nor Serial is connected when you click **START MISSION**, the application automatically starts streaming **synthetic test data** (see [Test / Simulation Mode](#test--simulation-mode)).

---

## Post-Flight File Loading

Click **📂 LOAD PACKETS** to open a previously saved packet file. The application auto-detects the format:

| Format | Detection Rule | Parser Used |
|--------|---------------|-------------|
| **CSV** | First non-blank line has ≥ 8 comma-separated tokens and no `packet=[` text | `CsvPacketParser` |
| **Text** | First few lines contain `packet=[HH,HH,…]` | `TextModePacketParser` |
| **Binary** | All other cases | `BinaryPacketBuffer` |

### CSV column layout (Payload_Data.txt format)

| Column | Field | Notes |
|--------|-------|-------|
| 0 | `packet_number` | Integer counter |
| 1 | `t0_loop_ms` | Arduino loop start millis |
| 2 | `fan_speed_pwm` | 0–255 |
| 3 | `temperature_C` | Already converted to °C |
| 4 | `pressure_hPa` | Altitude derived via barometric formula |
| 5 | `laser_predata0` | Laser internal millis (not used for altitude) |
| 6 | `humidity_pct` | % RH |
| 7 | `tf_loop_ms` | Arduino loop end millis |
| 8 | `laser_mixing_ratio` | g/kg (float or `nan`) |
| 9–11 | `laser_signal_a/b/c` | Raw instrument units |
| 12 | `laser_background` | 0–1 AU |
| 13 | `laser_packet_num` | Integer |
| 14 | `n_valid_samples` | WMS 2f waveform point count |
| 15+ | WMS 2f waveform | AC-coupled bipolar samples |

All parsed packets are injected directly into the data lists and `_update_plots()` is called once for immediate visualisation.

The **δ¹⁸O isotope** estimate for CSV files uses the Rayleigh distillation model, anchored to `q_ref` — the median mixing ratio of the first 100 stable readings (MR > 2 g/kg).

---

## Test / Simulation Mode

When **START MISSION** is clicked with no hardware connected, the application generates a physically accurate synthetic atmospheric sounding from 0 to 12 km in 5 m steps (~2 400 data points) and streams packets at ~80 ms intervals.

The synthetic profile models:

| Variable | Physical Model |
|----------|---------------|
| Temperature | ISA lapse rate (−6.5 K/km) + random surface offset + temperature inversion layer at 800–1 800 m |
| Pressure | Hypsometric equation derived layer-by-layer from the temperature profile |
| Humidity | Exponential decrease with altitude + Gaussian moist layer (cloud base) |
| Wind speed | Four-layer power-law profile (surface calm → boundary layer → free troposphere → jet approach) |
| Wind direction | Ekman spiral veering with altitude |
| δ¹⁸O isotope | Rayleigh distillation: −2 to −2.5 ‰/km depletion rate |
| Laser mixing ratio | Derived from T + RH via the Tetens/Clausius–Clapeyron equation |
| Laser background | Slow correlated random walk (detector drift) |
| Laser peak | Background + Beer–Lambert absorption depth ∝ mixing ratio |

Each run uses a fresh random seed, so every test looks different.

---

## Saving Mission Data

Click **SAVE DATA** (or it triggers automatically at **END MISSION**) to write:

```
flair_mission_YYYYMMDD_HHMMSS/
├── temperature_*.csv
├── humidity_*.csv
├── pressure_*.csv
├── wind_speed_*.csv
├── wind_direction_*.csv
├── isotope_*.csv
├── laser_mr_*.csv
├── laser_bkg_*.csv
├── laser_peak_*.csv
├── fan_speed_rx_*.csv
└── dashboard_*.png          ← 300 DPI screenshot of the full graph panel
```

Altitude-dependent channels (temperature, humidity, pressure, wind, isotope) are saved with an `Altitude (m)` column. Laser and fan channels are saved with a `Packet #` column.

---

## Packet Protocol Reference

All packets use **BST (Blue Sky Technologies) framing**: every packet starts with header bytes `0x55 0x24`.

### Downlink — Channel 0 (232)

Sent by the Arduino every loop (~1 second). **42 bytes total.**

```
Bytes   Field              Type        Value / Meaning
[0]     HDR[0]             uint8       0x55 = 'U'  — packet start marker
[1]     HDR[1]             uint8       0x24 = '$'  — packet start marker
[2]     type               uint8       232  = PAYLOAD_DATA_CHANNEL_0
[3]     action             uint8       0    = PKT_ACTION_STATUS
[4–5]   size               uint16 LE   34   = bytes in the data section

── Data section (bytes 6–39, 34 bytes) ──────────────────────────────────
[6–9]   t0_loop            uint32 LE   millis() at loop start [ms]
[10]    packetNum          uint8       Rolling counter 0→255→0
[11]    fan_speed          uint8       Current fan PWM [0=off, 255=full]
[12–13] temperature        uint16 LE   HMP60 raw ADC reading [0–1023]
[14–15] relhumidity        uint16 LE   HMP60 raw ADC reading [0–1023]
[16–19] laser_predata[0]   uint32 LE   Laser sensor internal millis [ms]
[20–23] laser_predata[1]   uint32 LE   Laser internal packet counter
[24–27] laser_data[0]      float32 LE  Mixing ratio (H₂O, g/kg)
[28–31] laser_data[1]      float32 LE  Background signal (AU)
[32–35] laser_data[2]      float32 LE  Peak signal (AU)
[36–39] tf_loop            uint32 LE   millis() at loop end [ms]
──────────────────────────────────────────────────────────────────────────
[40–41] CRC16              uint16 BE   Fletcher-16 over bytes [6–39]
```

### Uplink — Channel 1 (233)

Sent by the ground station to set fan speed. **9 bytes total.**

```
Bytes   Field              Type        Value / Meaning
[0]     HDR[0]             uint8       0x55
[1]     HDR[1]             uint8       0x24
[2]     type               uint8       233 = PAYLOAD_DATA_CHANNEL_1
[3]     action             uint8       2   = PKT_ACTION_COMMAND
[4–5]   size               uint16 LE   1   = one data byte follows
[6]     fan_pwm            uint8       Target fan duty [0–255]
[7–8]   CRC16              uint16 BE   Fletcher-16 over byte [6] only
```

### Reading a Raw Packet Line

Every received packet prints one terminal line in this format:

```
[WiFi RAW]        pkt#42  42B | 55 24 E8 00 22 00 A1 0F 00 00 2A 40 ...
[Serial BIN RAW]  pkt#7   42B | 55 24 E8 00 22 00 ...
[Serial TEXT RAW] pkt#1   42B | 55 24 E8 00 22 00 ...
[TEST RAW]        pkt#99  42B | 55 24 E8 00 22 00 ...
```

Breakdown of `55 24 E8 00 22 00 A1 0F 00 00 2A 40 ...`:

```
55        → HDR[0]
24        → HDR[1]
E8        → type = 0xE8 = 232 (CH0 downlink)
00        → action = 0 (STATUS)
22 00     → size = 0x0022 = 34 (LE)
A1 0F 00 00 → t0_loop = 0x00000FA1 = 4001 ms
2A        → packetNum = 42
40        → fan_speed = 64 (≈25% PWM)
...
```

The `packetNum` field (byte [10]) rolls 0→255→0. Use it to detect missed packets: if packet N is followed by packet N+2, one packet was dropped in transit.

### Checksum — Fletcher-16

```python
def fletcher16(data: bytes) -> int:
    s1, s2 = 0, 0
    for b in data:
        s1 = (s1 + b) & 0xFF
        s2 = (s2 + s1) & 0xFF
    return (s2 << 8) | s1
```

- Computed over **bytes [6–39]** (the 34-byte data section).
- Stored **big-endian** at bytes [40–41].

---

## Sensor Conversion Formulas

### HMP60 Temperature

```
T (°C) = -40.0 + (raw_adc / 1023.0) × 100.0
```

ADC range: 0–1023 → Temperature range: −40 °C to +60 °C.

### HMP60 Relative Humidity

```
RH (%) = (raw_adc / 1023.0) × 100.0
```

ADC range: 0–1023 → RH range: 0% to 100%.

### Fan Speed

```
fan_pct (%) = fan_pwm / 255.0 × 100.0
```

### Altitude from Pressure (CSV files)

```
altitude (m) = 44330 × (1 − (P_hPa / 1013.25)^0.19029)
```

---

## Atmospheric Science Background

### δ¹⁸O Isotope Estimation (Rayleigh Distillation)

The ground station estimates δ¹⁸O (‰ VSMOW) from the TDLAS mixing ratio and temperature using the Majoube (1971) liquid–vapour equilibrium fractionation model:

```
α_eq(T) = exp( 1137/T_K² − 0.4156/T_K − 2.0667×10⁻³ )
ε_eq    = (α_eq − 1) × 1000                              [‰]
δ¹⁸O    = δ₀ + ε_eq × ln(q / q_ref)                     [‰ VSMOW]
```

- `T_K` — temperature in Kelvin
- `q` — measured mixing ratio (g/kg)
- `q_ref` — reference surface mixing ratio (median of first 100 stable readings with MR > 2 g/kg)
- `δ₀` = −8.0 ‰ VSMOW (surface reference)
- Packets with MR < 0.5 g/kg return `nan` (instrument not yet locked)

### WMS 2f Waveform Analysis (CSV mode)

For CSV files containing WMS (Wavelength-Modulation Spectroscopy) 2nd-harmonic waveforms, the ground station computes summary features:

| Feature | Description |
|---------|-------------|
| `wf_amplitude` | Peak-to-peak signal (should ≈ mixing ratio) |
| `wf_centroid` | Amplitude-weighted centroid index (line-centre proxy) |
| `wf_rms_width` | Amplitude-weighted RMS width (Voigt linewidth proxy) |
| `wf_skewness` | 3rd standardised moment of |waveform| (lineshape asymmetry) |

### Boundary Layer Detection

The `BoundaryLayerAnalyzer` estimates the Planetary Boundary Layer (PBL) top from altitude profile data using:

1. **Primary method:** First altitude where the potential temperature gradient exceeds 0.005 K/m (capping inversion).
2. **Fallback:** Altitude of maximum wind shear (largest `dU/dz`).

If the altitude range is less than 50 m (flat / CSV data), the analysis returns safe defaults and the profile panels switch to time-series mode automatically.

---

## Troubleshooting

**No telemetry displayed after connecting via USB:**
- Ensure the baud rate is **9600** (matches the Arduino `serial_baud` variable).
- The ground station needs to receive a line containing `packet=[HH,HH,…]` to enter text mode. Check the Arduino is printing debug output.
- Watch the terminal for `[Serial] 🔍 Auto-detected: TEXT mode` — if this does not appear, bytes are not arriving.

**No telemetry displayed after connecting via DB9:**
- Binary mode requires the raw packet bytes `0x55 0x24` at the start of the stream. Check the DB9/radio adapter is passing raw binary, not converting to ASCII.
- Watch for `[DRAIN/Serial] ⚠ No 55 24 header` in the terminal — this means data is arriving but the framing is wrong.

**Plots do not update:**
- Plots only refresh when `mission_running` is True. Click **START MISSION**.
- Plots are rate-limited to once per 500 ms. For file loads, the plot is drawn once after all packets are processed.

**Wi-Fi server fails to bind:**
- Confirm you are on the SWIFT STATION Wi-Fi network (the auto-detected IP must be on `192.168.0.x`).
- Try a different port if 55555 is blocked by a firewall.

**CRC mismatch errors in terminal:**
- A few mismatches during connection establishment are normal (partial first packet).
- Persistent mismatches indicate data corruption on the radio link or a baud rate mismatch.

**`FileNotFoundError: logo.png`:**
- Not critical — the application falls back to a placeholder text label.
- Place a `logo.png` file (any size; it is auto-resized to 120×80 px) in the same directory as the script.

---

## Known Limitations & Future Work

- Wind speed and wind direction are **not present in the downlink packet** (Channel 0). They appear in plots only when loaded from a CSV that includes these fields, or when using synthetic test data.
- The `TELEMETRY_PRESSURE` packet type (99) is currently handled by the Channel 0 parser as a fallback — a dedicated parser is not yet implemented.
- Packet channels 2–7 (`PAYLOAD_DATA_CHANNEL_2` through `PAYLOAD_DATA_CHANNEL_7`) are defined but not parsed.
- The file-load replay does not animate packets in real time (they are applied all at once). A timed replay mode could be added in a future iteration.

---

## Change Log (Key Fixes)

| Fix | Description |
|-----|-------------|
| **FIX 1** | **Dual-mode serial parser.** The receiver now auto-detects binary (DB9/radio) vs. text (USB) format. Previously, binary-only parsing meant USB connections never displayed data. |
| **FIX 2** | Terminal hex dump now works for both serial paths; text-mode packets extract and print the same hex bytes as binary mode. |
| **FIX 3** | Uplink fan command uses `PAYLOAD_DATA_CHANNEL_1 = 233` (was incorrectly 239). Matches the Arduino `case PAYLOAD_DATA_CHANNEL_1` handler. |
| **FIX 4** | `SerialReceiver` uses `in_waiting` poll instead of `readline()`. `readline()` silently blocks on binary data. |
| **FIX 5** | `drain_packets()` routes by packet type byte. Previously it called the Channel 0 parser for every packet regardless of type. |
| **FIX 6** | Test-data queue items are now `(raw_bytes, parsed_dict, status)` tuples — same format as live data — so `_monitor_log` displays them correctly. |
| **FIX 7** | **CONNECT SERIAL** button added. Serial was never started in earlier iterations. |
| **FIX 8** | Default baud rate set to 9600 in the serial dialog, matching `serial_baud` in the Arduino sketch. |
| **FIX 9** | Packets captured during mode auto-detection are no longer discarded. They are stored in `_pending_text_packets` and flushed immediately after mode resolves. |