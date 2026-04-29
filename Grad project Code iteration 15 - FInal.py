"""
FLAIR Control Deck - Ground Station
Iteration 13 — PACKET NUMBER IN RAW PRINT + STARTUP PACKET GUIDE

═══════════════════════════════════════════════════════════════════
ROOT CAUSE OF "no telemetry displayed" — AND ALL FIXES:
═══════════════════════════════════════════════════════════════════

FIX 1 ★★★ CRITICAL — DUAL-MODE SERIAL PARSER (was broken since iter 1)
  The Arduino has TWO separate serial ports:
    • Serial (USB hardware UART) → sends HUMAN-READABLE TEXT, e.g.:
        packetNum=001, packet=[55,24,E8,00,22,00,...]
    • serialDB9 (NeoSWSerial, pins 3/4, DB9 connector) → sends RAW BINARY 42 bytes
  Old code: BinaryPacketBuffer ONLY looks for raw 0x55 0x24 bytes.
  If you connect via USB cable, you receive ASCII text — 0x55 0x24 NEVER
  appears as raw bytes, so nothing was ever parsed and nothing displayed.
  Fix: SerialReceiver now AUTO-DETECTS the incoming format:
    • Binary mode  → first bytes are 0x55 0x24  (DB9/adapter path)
    • Text mode    → line contains "packet=[HH,HH,…]"  (USB path)
  Both paths feed the same PacketParser → same plots, same hex terminal dump.

FIX 2 ★★★ CRITICAL — Terminal hex dump now works for BOTH serial paths
  In text mode the raw hex bytes extracted from the Arduino debug line are
  printed to the terminal exactly as they would appear in binary mode.

FIX 3 — Uplink fan command uses PAYLOAD_DATA_CHANNEL_1 = 233 (was 239)
  Matches Arduino readDB9() switch-case: case PAYLOAD_DATA_CHANNEL_1 (233)

FIX 4 — SerialReceiver: proper binary-buffered receive loop, not readline()
  readline() silently blocks on binary data; replaced with in_waiting poll.

FIX 5 — drain_packets() routes by type byte (was calling channel-0 parser
  for every packet type regardless of the type byte).

FIX 6 — Test-data queue items are now proper (raw_bytes, parsed_dict, status)
  tuples — same format as live data — so _monitor_log shows them correctly.

FIX 7 — "CONNECT SERIAL" button added (serial was never started before).

FIX 8 — Baud rate note: Arduino sets serial_baud = 9600 for BOTH Serial and
  serialDB9. The serial dialog defaults to 9600 to match.

FIX 9 ★ REAL BUG (detection-phase text packets silently discarded)
  During MODE_UNKNOWN sniffing, recv_into_buffer() called
  _txt_parser.feed_bytes(accumulated) but threw away the returned list.
  Fix: store results in self._pending_text_packets; _serial_loop drains
  them immediately after mode resolves so no packets are lost at startup.

═══════════════════════════════════════════════════════════════════
Channels (payload_code_v5.ino):
  PAYLOAD_DATA_CHANNEL_0 = 232  → downlink FROM payload  (telemetry)
  PAYLOAD_DATA_CHANNEL_1 = 233  → uplink   TO   payload  (fan commands)

Payload Packet Structure (PAYLOAD_DATA_CHANNEL_0 = 232, 42 bytes total):
  [0-1]  HDR         0x55 0x24
  [2]    type        232 (PAYLOAD_DATA_CHANNEL_0)
  [3]    action      0   (PKT_ACTION_STATUS)
  [4-5]  size        uint16 LE = 34  (data section length)
  ── Data section (bytes 6–39, 34 bytes) ──────────────────────────
  [6-9]   t0_loop           uint32 LE   loop start  (ms)
  [10]    packetNum         uint8       rolling 0-255
  [11]    fan_speed         uint8       current PWM
  [12-13] temperature       uint16 LE   HMP60 ADC raw
  [14-15] relhumidity       uint16 LE   HMP60 ADC raw
  [16-19] laser_predata[0]  uint32 LE   laser millis()
  [20-23] laser_predata[1]  uint32 LE   laser packet counter
  [24-27] laser_data[0]     float  LE   mixing ratio (mr)
  [28-31] laser_data[1]     float  LE   background  (bkg)
  [32-35] laser_data[2]     float  LE   peak
  [36-39] tf_loop           uint32 LE   loop end   (ms)
  ── End data ──────────────────────────────────────────────────────
  [40-41] CRC16 BE   Fletcher-16 over bytes 6–39

Uplink Fan Control Packet (PAYLOAD_DATA_CHANNEL_1 = 233, 9 bytes):
  [0-1] 0x55 0x24
  [2]   233  (PAYLOAD_DATA_CHANNEL_1)
  [3]   2    (PKT_ACTION_COMMAND)
  [4-5] 0x0001  (size = 1 byte)
  [6]   fan PWM  uint8  [0-255]
  [7-8] CRC16 BE
═══════════════════════════════════════════════════════════════════
"""

import tkinter as tk
from tkinter import ttk, messagebox
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure
from matplotlib.patches import FancyArrow, Circle
import numpy as np
from datetime import datetime
import socket
import select
import struct
import re
from threading import Thread
import queue
import os
import csv
import serial
import serial.tools.list_ports
from PIL import Image, ImageTk


# ──────────────────────────────────────────────
# Startup packet structure guide
# ──────────────────────────────────────────────
def print_packet_guide():
    """
    Printed once to the terminal when the ground station starts.
    Explains every byte of every packet so operators can read raw hex dumps.
    """
    B = "\033[96m"   # bright cyan
    G = "\033[92m"   # bright green
    Y = "\033[93m"   # yellow
    R = "\033[91m"   # red
    W = "\033[97m"   # white
    D = "\033[2m"    # dim
    X = "\033[0m"    # reset
    H = "\033[1m"    # bold

    print()
    print(H + "=" * 72 + X)
    print(H + "  FLAIR GROUND STATION — PACKET STRUCTURE GUIDE" + X)
    print(H + "=" * 72 + X)

    print()
    print(H + Y + "  OVERVIEW" + X)
    print(W + """
  All packets follow the BST (Blue Sky Technologies) framing format.
  Every packet starts with header bytes 0x55 0x24 ('U' '$') so the
  receiver can find the start of a packet in a raw byte stream.

  There are TWO packet directions:
    DOWNLINK  CH0 = 232  Arduino → Ground Station   (telemetry data)
    UPLINK    CH1 = 233  Ground Station → Arduino   (fan speed command)

  The ground station auto-detects whether the Arduino is sending raw
  binary bytes (DB9 / radio) or ASCII text lines (USB cable).
""" + X)

    print(H + "=" * 72 + X)
    print()
    print(H + G + "  DOWNLINK PACKET  —  PAYLOAD_DATA_CHANNEL_0 = 232" + X)
    print(D + "  Sent by the Arduino every loop (~1 s).  42 bytes total." + X)
    print()
    print(B + "  ┌─ HEADER (6 bytes) ──────────────────────────────────────────┐" + X)
    rows = [
        ("Bytes",  "Field",          "Type",        "Value / Meaning"),
        ("-" * 7,  "-" * 20,         "-" * 14,      "-" * 30),
        ("[0]",    "HDR[0]",         "uint8",        "0x55  = 'U'  — always this value, marks packet start"),
        ("[1]",    "HDR[1]",         "uint8",        "0x24  = '$'  — always this value, marks packet start"),
        ("[2]",    "type",           "uint8",        "232   = PAYLOAD_DATA_CHANNEL_0  (downlink telemetry)"),
        ("[3]",    "action",         "uint8",        "0     = PKT_ACTION_STATUS  (Arduino reporting its state)"),
        ("[4-5]",  "size",           "uint16 LE",    "34    = number of bytes in the data section (not total)"),
    ]
    for r in rows:
        print("  {:<8} {:<22} {:<16} {}".format(*r))
    print()
    print(B + "  ├─ DATA SECTION (34 bytes, offsets 6–39) ────────────────────┤" + X)
    rows2 = [
        ("Bytes",    "Field",              "Type",        "Meaning / Units"),
        ("-" * 7,    "-" * 22,             "-" * 14,      "-" * 32),
        ("[6-9]",    "t0_loop",            "uint32 LE",   "millis() at start of Arduino loop  [ms]"),
        ("[10]",     "packetNum",          "uint8",        "Rolling counter 0→255→0  (detect missed pkts)"),
        ("[11]",     "fan_speed",          "uint8",        "PWM duty cycle sent to fan  [0=off, 255=full]"),
        ("[12-13]",  "temperature",        "uint16 LE",    "HMP60 raw ADC reading  [0–1023]"),
        ("",         "",                   "",             "  Convert: T(°C) = -40 + (raw/1023) × 100"),
        ("[14-15]",  "relhumidity",        "uint16 LE",    "HMP60 raw ADC reading  [0–1023]"),
        ("",         "",                   "",             "  Convert: RH(%) = (raw/1023) × 100"),
        ("[16-19]",  "laser_predata[0]",   "uint32 LE",    "Laser sensor internal millis()  [ms]"),
        ("[20-23]",  "laser_predata[1]",   "uint32 LE",    "Laser packet counter (from laser PCB)"),
        ("[24-27]",  "laser_data[0]",      "float32 LE",   "Mixing ratio  (H₂O concentration proxy)"),
        ("[28-31]",  "laser_data[1]",      "float32 LE",   "Background signal  (laser baseline)"),
        ("[32-35]",  "laser_data[2]",      "float32 LE",   "Peak signal  (max laser return per cycle)"),
        ("[36-39]",  "tf_loop",            "uint32 LE",    "millis() at end of Arduino loop  [ms]"),
        ("",         "",                   "",             "  loop_duration = tf_loop − t0_loop  [ms]"),
    ]
    for r in rows2:
        print("  {:<9} {:<22} {:<14} {}".format(*r))
    print()
    print(B + "  └─ CHECKSUM (2 bytes, offsets 40–41) ───────────────────────┘" + X)
    rows3 = [
        ("Bytes",    "Field",              "Type",        "Meaning"),
        ("-" * 7,    "-" * 22,             "-" * 14,      "-" * 32),
        ("[40-41]",  "fletcherChecksum",   "uint16 BE",   "Fletcher-16 over bytes [6–39]"),
        ("",         "",                   "",             "  sum1=Σbytes mod 256,  sum2=Σsum1 mod 256"),
        ("",         "",                   "",             "  stored as [sum2, sum1]  (big-endian)"),
    ]
    for r in rows3:
        print("  {:<9} {:<22} {:<14} {}".format(*r))

    print()
    print(H + "=" * 72 + X)
    print()
    print(H + R + "  UPLINK PACKET  —  PAYLOAD_DATA_CHANNEL_1 = 233" + X)
    print(D + "  Sent by the ground station to command fan speed.  9 bytes total." + X)
    print()
    rows4 = [
        ("Bytes",   "Field",              "Type",        "Value / Meaning"),
        ("-" * 7,   "-" * 20,             "-" * 14,      "-" * 32),
        ("[0]",     "HDR[0]",             "uint8",        "0x55  = 'U'"),
        ("[1]",     "HDR[1]",             "uint8",        "0x24  = '$'"),
        ("[2]",     "type",               "uint8",        "233   = PAYLOAD_DATA_CHANNEL_1"),
        ("[3]",     "action",             "uint8",        "2     = PKT_ACTION_COMMAND"),
        ("[4-5]",   "size",               "uint16 LE",    "1     = one data byte follows"),
        ("[6]",     "fan_pwm",            "uint8",        "Target fan duty  [0=off … 255=full speed]"),
        ("[7-8]",   "fletcherChecksum",   "uint16 BE",    "Fletcher-16 over byte [6] only"),
    ]
    for r in rows4:
        print("  {:<8} {:<22} {:<16} {}".format(*r))

    print()
    print(H + "=" * 72 + X)
    print()
    print(H + Y + "  HOW TO READ A RAW PACKET LINE" + X)
    print(W + """
  Every received packet prints one line to this terminal:

    [WiFi RAW]       pkt#42  42B | 55 24 E8 00 22 00 A1 0F 00 00 2A 40 ...
    [Serial BIN RAW] pkt#7   42B | 55 24 E8 00 22 00 ...
    [Serial TEXT RAW]pkt#1   42B | 55 24 E8 00 22 00 ...
    [TEST RAW]       pkt#99  42B | 55 24 E8 00 22 00 ...

  Breaking down:  55 24 E8 00 22 00 A1 0F 00 00 2A 40 ...
                  │  │  │  │  │──│  │─────────────────
                  │  │  │  │  └size=0x0022=34 (LE)
                  │  │  │  └action=0 (STATUS)
                  │  │  └type=0xE8=232 (CH0 downlink)
                  │  └HDR[1]=0x24='$'
                  └HDR[0]=0x55='U'

  Bytes [6-9]  = t0_loop  (4 bytes LE)  → e.g. A1 0F 00 00 = 0x00000FA1 = 4001 ms
  Byte  [10]   = packetNum              → e.g. 2A = 42
  Byte  [11]   = fan_speed PWM          → e.g. 40 = 64 (≈25%)
  Bytes [12-13]= temperature ADC raw    → e.g. 00 02 = 0x0200 = 512
  Bytes [14-15]= humidity ADC raw       → e.g. 00 01 = 0x0100 = 256
  Bytes [16-23]= laser_predata[0],[1]   (two uint32 LE)
  Bytes [24-35]= laser_data[0],[1],[2]  (three float32 LE)
  Bytes [36-39]= tf_loop (4 bytes LE)
  Bytes [40-41]= CRC16 Fletcher-16 (big-endian)
""" + X)

    print(H + "=" * 72 + X)
    print()
    print(H + Y + "  PACKET NUMBER FIELD  (byte [10])" + X)
    print(W + """
  packetNum rolls 0 → 255 → 0 → … every loop on the Arduino.
  Use it to detect missed packets:  if pkt#N is followed by pkt#N+2
  then one packet was dropped in transit.
  All raw print lines include  pkt#NNN  so you can track this easily.
""" + X)

    print(H + "=" * 72 + X)
    print()
    print(H + Y + "  DATA TYPE REFERENCE" + X)
    print(W + """
  uint8    = 1 byte,  unsigned,  range 0–255
  uint16   = 2 bytes, unsigned,  range 0–65535,   stored little-endian (LE)
  uint32   = 4 bytes, unsigned,  range 0–4294967295, stored little-endian (LE)
  float32  = 4 bytes, IEEE-754 single precision,  stored little-endian (LE)
  uint16BE = 2 bytes, unsigned,  stored big-endian (BE) — CRC only

  Little-endian (LE): least-significant byte first.
    Example:  value 1000 = 0x000003E8 → stored as E8 03 00 00
  Big-endian (BE): most-significant byte first.
    Example:  CRC  0x1A2B              → stored as 1A 2B
""" + X)

    print(H + "=" * 72 + X)
    print()



# ──────────────────────────────────────────────
# Packet type constants (mirrors payload_code_v5.ino C enum)
# ──────────────────────────────────────────────
class PacketTypes:
    TELEMETRY_HEARTBEAT   = 96
    TELEMETRY_POSITION    = 97
    TELEMETRY_ORIENTATION = 98
    TELEMETRY_PRESSURE    = 99
    TELEMETRY_CONTROL     = 100
    TELEMETRY_SYSTEM      = 101

    PAYLOAD_DATA_CHANNEL_0 = 232   # Downlink FROM payload (telemetry)
    PAYLOAD_DATA_CHANNEL_1 = 233   # Uplink TO payload (fan control) [FIX 3]
    PAYLOAD_DATA_CHANNEL_2 = 234
    PAYLOAD_DATA_CHANNEL_3 = 235
    PAYLOAD_DATA_CHANNEL_4 = 236
    PAYLOAD_DATA_CHANNEL_5 = 237
    PAYLOAD_DATA_CHANNEL_6 = 238
    PAYLOAD_DATA_CHANNEL_7 = 239


class PacketAction:
    STATUS  = 0
    REQUEST = 1
    COMMAND = 2
    ACK     = 3
    NACK    = 4
    LOAD    = 5


# ──────────────────────────────────────────────
# Hex dump helper — always prints to terminal
# ──────────────────────────────────────────────
def hex_dump(label: str, data: bytes, note: str = ""):
    """Print a labelled hex + ASCII dump to the terminal."""
    rows = []
    for row_i in range(0, len(data), 16):
        row = data[row_i:row_i + 16]
        hex_part   = ' '.join(f'{b:02X}' for b in row)
        ascii_part = ''.join(chr(b) if 32 <= b < 127 else '.' for b in row)
        rows.append(f"  {row_i:04X}  {hex_part:<48}  {ascii_part}")
    extra = f"  [{note}]" if note else ""
    print(f"\n[{label}] {len(data)} bytes{extra}")
    print('\n'.join(rows))


# ──────────────────────────────────────────────
# Packet parser / builder
# ──────────────────────────────────────────────
class PacketParser:
    """
    Parses binary packets from the payload Arduino.
    Packet layout matches payload_code_v5.ino exactly (verified field-by-field).
    """

    # HMP60 conversion constants
    HMP60_TEMP_MIN = -40.0   # °C at ADC = 0
    HMP60_TEMP_MAX =  60.0   # °C at ADC = 1023
    HMP60_RH_MIN   =   0.0
    HMP60_RH_MAX   = 100.0

    @staticmethod
    def fletcher16(data: bytes) -> int:
        """Fletcher-16 checksum — byte-for-byte matches Arduino CRC16()."""
        s1, s2 = 0, 0
        for b in data:
            s1 = (s1 + b) & 0xFF
            s2 = (s2 + s1) & 0xFF
        return (s2 << 8) | s1

    @staticmethod
    def verify(packet: bytes) -> bool:
        if len(packet) < 8:
            return False
        size = struct.unpack_from('<H', packet, 4)[0]
        if len(packet) < 6 + size + 2:
            return False
        data_section = packet[6: 6 + size]
        recv_crc     = struct.unpack_from('>H', packet, 6 + size)[0]
        return PacketParser.fletcher16(data_section) == recv_crc

    @staticmethod
    def parse_payload_channel0(packet: bytes):
        """
        Parse a PAYLOAD_DATA_CHANNEL_0 (232) downlink packet.
        Returns (dict, 'OK') on success, (None, error_string) on failure.

        Field offsets verified against payload_code_v5.ino struct layout:
          HDR[2] + type[1] + action[1] + size[2] = 6 header bytes
          data section (34 bytes):
            t0_loop[4] packetNum[1] fan_speed[1] temp[2] relh[2]
            laser_predata[4][4] laser_data[4][4][4] tf_loop[4]
          CRC[2] big-endian Fletcher-16
        """
        if len(packet) < 8:
            return None, "Packet too short"
        if packet[0] != 0x55 or packet[1] != 0x24:
            return None, f"Bad header: {packet[0]:02X} {packet[1]:02X}"

        pkt_type = packet[2]
        action   = packet[3]
        size     = struct.unpack_from('<H', packet, 4)[0]

        if not PacketParser.verify(packet):
            data_section = packet[6: 6 + size]
            calc_crc     = PacketParser.fletcher16(data_section)
            recv_crc     = struct.unpack_from('>H', packet, 6 + size)[0] if len(packet) >= 6+size+2 else 0
            return None, (f"CRC16 mismatch: recv=0x{recv_crc:04X} "
                          f"calc=0x{calc_crc:04X} size={size} pkt_len={len(packet)}")

        ds = packet[6: 6 + size]   # data section

        out = {
            'packet_type': pkt_type,
            'action':      action,
            'size':        size,
            'channel':     f'PAYLOAD_DATA_CHANNEL_{pkt_type - 232}',
        }

        idx = 0

        def take(fmt):
            nonlocal idx
            n = struct.calcsize(fmt)
            if idx + n > len(ds):
                return None
            val = struct.unpack_from(fmt, ds, idx)
            idx += n
            return val[0] if len(val) == 1 else val

        t0 = take('<L')
        if t0 is not None: out['t0_loop'] = t0

        pn = take('B')
        if pn is not None: out['packet_number'] = pn

        fs = take('B')
        if fs is not None:
            out['fan_speed_pwm'] = fs
            out['fan_speed_pct'] = round(fs / 255.0 * 100.0, 1)

        raw_t = take('<H')
        if raw_t is not None:
            out['temperature_raw'] = raw_t
            out['temperature'] = PacketParser.HMP60_TEMP_MIN + (raw_t / 1023.0) * (
                PacketParser.HMP60_TEMP_MAX - PacketParser.HMP60_TEMP_MIN)

        raw_h = take('<H')
        if raw_h is not None:
            out['humidity_raw'] = raw_h
            out['humidity']     = (raw_h / 1023.0) * 100.0

        lp0 = take('<L')
        lp1 = take('<L')
        if lp0 is not None: out['laser_timestamp_ms'] = lp0
        if lp1 is not None: out['laser_packet_num']   = lp1

        mr   = take('<f')
        bkg  = take('<f')
        peak = take('<f')
        if mr   is not None: out['laser_mixing_ratio'] = mr
        if bkg  is not None: out['laser_background']   = bkg
        if peak is not None: out['laser_peak']         = peak
        if all(x is not None for x in (mr, bkg, peak)):
            out['laser_data'] = [mr, bkg, peak]

        tf = take('<L')
        if tf is not None:
            out['tf_loop']          = tf
            out['loop_duration_ms'] = tf - out.get('t0_loop', 0)

        # Placeholders for fields not in this packet type
        out.setdefault('altitude',       0.0)
        out.setdefault('pressure',    1013.25)
        out.setdefault('wind_speed',     0.0)
        out.setdefault('wind_direction', 0.0)
        out.setdefault('isotope',        -8.0)

        return out, "OK"

    @staticmethod
    def parse_by_type(packet: bytes):
        """Route packet to the correct parser based on type byte."""
        if len(packet) < 3:
            return None, "Packet too short to check type"
        pkt_type = packet[2]
        if pkt_type == PacketTypes.PAYLOAD_DATA_CHANNEL_0:
            return PacketParser.parse_payload_channel0(packet)
        elif pkt_type == PacketTypes.TELEMETRY_PRESSURE:
            # Handle if needed — attempt channel-0 parse for now
            return PacketParser.parse_payload_channel0(packet)
        else:
            size = struct.unpack_from('<H', packet, 4)[0] if len(packet) >= 6 else 0
            return None, f"Unhandled packet type={pkt_type} size={size}"

    # ── Uplink: fan control ──────────────────────────────────────────────────
    @staticmethod
    def build_fan_control_packet(fan_pwm: int) -> bytes:
        """
        Build a PAYLOAD_DATA_CHANNEL_1 (233) uplink command packet. [FIX 3]
        Arduino readDB9() handles: case PAYLOAD_DATA_CHANNEL_1: d.dc0.data.fan_speed = buffer[6];
        """
        pkt = bytearray([0x55, 0x24,
                         PacketTypes.PAYLOAD_DATA_CHANNEL_1,   # 233
                         PacketAction.COMMAND])
        pkt += struct.pack('<H', 1)           # size = 1 byte
        pkt.append(int(fan_pwm) & 0xFF)       # fan PWM value
        crc = PacketParser.fletcher16(pkt[6:])
        pkt += struct.pack('>H', crc)
        print(f"[PKT BUILD] Fan cmd (CH1=233): {' '.join(f'{b:02X}' for b in pkt)}")
        return bytes(pkt)


# ──────────────────────────────────────────────
# Binary packet framing buffer
# ──────────────────────────────────────────────
class BinaryPacketBuffer:
    """
    Accumulates raw bytes and drains complete BST-style packets.
    Header = 0x55 0x24.  Size field at bytes 4-5 (uint16 LE).
    Total packet = 6 + size + 2 bytes.
    """
    HEADER = b'\x55\x24'

    def __init__(self, source_label: str):
        self._buf   = bytearray()
        self._label = source_label

    def feed(self, data: bytes):
        """Add raw bytes and print hex dump to terminal."""
        self._buf.extend(data)
        hex_dump(f"RECV/{self._label}", data, f"buf_total={len(self._buf)}B")

    def drain(self) -> list:
        """
        Parse all complete packets. Routes by type byte.
        Returns list of (raw_bytes, parsed_dict_or_None, status_str).
        """
        results = []
        while True:
            if len(self._buf) < 6:
                break

            idx = self._buf.find(self.HEADER)
            if idx == -1:
                print(f"[DRAIN/{self._label}] ⚠ No 55 24 header in {len(self._buf)}B — discarding")
                last = self._buf[-1:]
                self._buf.clear()
                self._buf.extend(last)
                break

            if idx > 0:
                print(f"[DRAIN/{self._label}] ⚠ Skipping {idx} junk byte(s) before 55 24")
                del self._buf[:idx]

            if len(self._buf) < 6:
                break

            size_field = struct.unpack_from('<H', self._buf, 4)[0]
            total_len  = 6 + size_field + 2

            if len(self._buf) < total_len:
                print(f"[DRAIN/{self._label}] ⏳ Partial: have {len(self._buf)}B need {total_len}B")
                break

            raw = bytes(self._buf[:total_len])
            del self._buf[:total_len]

            pkt_type       = raw[2] if len(raw) > 2 else 0xFF
            parsed, status = PacketParser.parse_by_type(raw)

            if parsed is not None:
                print(f"[DRAIN/{self._label}] ✓ type={pkt_type}"
                      f"  pkt#{parsed.get('packet_number','?'):>3}"
                      f"  temp={parsed.get('temperature', float('nan')):.2f}°C"
                      f"  hum={parsed.get('humidity', float('nan')):.1f}%"
                      f"  fan={parsed.get('fan_speed_pwm','?')} PWM"
                      f"  MR={parsed.get('laser_mixing_ratio', float('nan')):.5f}"
                      f"  bkg={parsed.get('laser_background', float('nan')):.4f}"
                      f"  peak={parsed.get('laser_peak', float('nan')):.4f}")
            else:
                raw_hex = ' '.join(f'{b:02X}' for b in raw[:24])
                print(f"[DRAIN/{self._label}] ✗ PARSE FAILED type={pkt_type}: {status}"
                      f"  raw[0:24]= {raw_hex}{'…' if len(raw) > 24 else ''}")

            results.append((raw, parsed, status))
        return results


# ──────────────────────────────────────────────
# [FIX 1] TEXT-MODE serial parser
# Handles Arduino USB Serial debug output format:
#   "packetNum=001, packet=[55,24,E8,00,22,00,...]"
# ──────────────────────────────────────────────
_TEXT_PKT_RE = re.compile(r'packet=\[([0-9A-Fa-f,]+)\]')


class TextModePacketParser:
    """
    Parses lines of Arduino debug text output into packet dicts.
    This is what you receive when connected via USB cable to the Arduino.
    The Arduino prints: packetNum=NNN, packet=[HH,HH,...]
    We extract the hex bytes, reconstruct the binary packet, and parse it normally.
    """

    def __init__(self, source_label: str):
        self._label   = source_label
        self._linebuf = b''

    def feed_bytes(self, data: bytes) -> list:
        """
        Accumulate bytes into line buffer.  Each complete line that contains
        a 'packet=[...]' segment is parsed and returned as
        (raw_bytes, parsed_dict_or_None, status_str).
        """
        self._linebuf += data
        results = []

        # Split on newlines
        while b'\n' in self._linebuf:
            line_bytes, self._linebuf = self._linebuf.split(b'\n', 1)
            try:
                line = line_bytes.decode('ascii', errors='replace').strip()
            except Exception:
                continue

            # Print every line to terminal so operator can see raw output
            print(f"[TEXT/{self._label}] >>> {line}")

            m = _TEXT_PKT_RE.search(line)
            if not m:
                continue   # startup messages, blank lines, etc.

            try:
                hex_bytes = [int(x, 16) for x in m.group(1).split(',')]
            except ValueError as e:
                print(f"[TEXT/{self._label}] ✗ hex parse error: {e}")
                continue

            raw = bytes(hex_bytes)
            # Print hex dump to terminal exactly as binary mode does
            hex_dump(f"RECV/{self._label}/TEXT", raw, "extracted from Arduino debug line")

            parsed, status = PacketParser.parse_by_type(raw)
            if parsed is not None:
                print(f"[TEXT/{self._label}] ✓ type={raw[2] if len(raw)>2 else '?'}"
                      f"  pkt#{parsed.get('packet_number','?'):>3}"
                      f"  temp={parsed.get('temperature', float('nan')):.2f}°C"
                      f"  hum={parsed.get('humidity', float('nan')):.1f}%"
                      f"  fan={parsed.get('fan_speed_pwm','?')} PWM"
                      f"  MR={parsed.get('laser_mixing_ratio', float('nan')):.5f}"
                      f"  bkg={parsed.get('laser_background', float('nan')):.4f}"
                      f"  peak={parsed.get('laser_peak', float('nan')):.4f}")
            else:
                print(f"[TEXT/{self._label}] ✗ PARSE FAILED: {status}")

            results.append((raw, parsed, status))

        return results



# ──────────────────────────────────────────────────────────────────
# CSV Packet Parser
# Handles the ground-station CSV log format written by the payload:
#
#   col 0  : packet_number   (integer counter)
#   col 1  : t0_loop_ms      (Arduino loop start millis)
#   col 2  : fan_speed_pwm   (0-255)
#   col 3  : temperature_C   (°C, already converted)
#   col 4  : pressure_hPa    (hPa)
#   col 5  : laser_predata0  (laser internal millis — ignored for altitude)
#   col 6  : humidity_pct    (% RH)
#   col 7  : tf_loop_ms      (Arduino loop end millis)
#   col 8  : laser_mixing_ratio  (float or nan)
#   col 9  : laser_signal_a  (raw instrument unit)
#   col 10 : laser_signal_b  (raw instrument unit)
#   col 11 : laser_signal_c  (raw instrument unit)
#   col 12 : laser_background (0–1 AU)
#   col 13 : laser_packet_num (integer)
#   col 14 : n_valid_samples  (integer — how many waveform points follow)
#   col 15+: WMS 2f waveform  (AC-coupled bipolar samples, one per col)
#
# The WMS 2f waveform is the 2nd-harmonic wavelength-modulation-spectroscopy
# signal from the TDLAS sensor.  Its peak-to-peak amplitude equals the
# laser_mixing_ratio (already decoded in col 8).  The waveform shape also
# encodes the Voigt linewidth, which depends on temperature and pressure.
#
# δ¹⁸O is derived from the waveform via the Rayleigh distillation model
# (Majoube 1971 equilibrium fractionation):
#
#   α_eq(T)  = exp( 1137/T_K² − 0.4156/T_K − 2.0667×10⁻³ )
#   ε_eq     = ( α_eq − 1 ) × 1000          [‰]
#   δ¹⁸O     = δ₀ + ε_eq × ln( q / q_ref )  [‰ VSMOW]
#
# where q is the per-packet mixing ratio (g/kg), q_ref is the median of the
# first 100 stable readings (MR > 2 g/kg), and δ₀ = −8.0 ‰ VSMOW.
# Packets where MR < 0.5 g/kg (instrument not yet locked) return nan.
#
# Altitude is derived from pressure via the barometric formula since
# the regular telemetry rows carry no direct altitude field.
#
# Lines with more than 100 columns are sounding/profile records with
# a different structure; they are skipped for now.
# ──────────────────────────────────────────────────────────────────
class CsvPacketParser:
    """Parse the payload CSV log file into the standard parsed-dict format."""

    @staticmethod
    def _pressure_to_altitude(p_hpa: float) -> float:
        """Barometric formula: altitude (m) from pressure (hPa)."""
        if p_hpa <= 0:
            return 0.0
        return 44330.0 * (1.0 - (p_hpa / 1013.25) ** 0.19029)

    @staticmethod
    def _safe(val: str):
        """Parse a single CSV token; return None for 'nan' or blank."""
        v = val.strip()
        if v.lower() == 'nan' or v == '':
            return None
        try:
            return float(v)
        except ValueError:
            return None

    # ── Isotope constants ─────────────────────────────────────────────────────
    # Majoube (1971) liquid–vapour equilibrium fractionation for ¹⁸O/¹⁶O.
    # Used in the Rayleigh distillation model to derive δ¹⁸O from MR + T.
    _MJB_A  =  1137.0    # K²  coefficient
    _MJB_B  =    -0.4156 # K   coefficient
    _MJB_C  =    -2.0667e-3  # dimensionless
    _DELTA0 =    -8.0    # ‰ VSMOW  — reference δ¹⁸O at the surface
    _MR_LOCK_MIN = 0.5   # g/kg — below this MR the instrument is not locked

    @staticmethod
    def _parse_waveform(cols: list, start: int):
        """
        Extract the WMS 2f waveform beginning at *start* in *cols*.

        col[start]   = n_valid_samples  (integer count; may be 'nan' if absent)
        col[start+1] = first waveform sample
        …

        Returns (n_valid, waveform_list).  Samples that are 'nan' or empty are
        skipped so the returned list may be shorter than n_valid.
        """
        if start >= len(cols):
            return 0, []
        raw_n = cols[start].strip()
        try:
            n_valid = int(float(raw_n)) if raw_n not in ('', 'nan') else 0
        except ValueError:
            n_valid = 0
        waveform = []
        for v in cols[start + 1:]:
            v = v.strip()
            if v in ('', 'nan'):
                continue
            try:
                waveform.append(float(v))
            except ValueError:
                pass
        return n_valid, waveform

    @staticmethod
    def _waveform_features(waveform: list):
        """
        Compute summary features of a WMS 2f waveform.

        Returns a dict with:
          wf_amplitude  — peak-to-peak signal level (should equal MR, sanity check)
          wf_centroid   — amplitude-weighted centroid index (line-centre proxy)
          wf_rms_width  — amplitude-weighted RMS width (Voigt linewidth proxy)
          wf_skewness   — 3rd standardised moment of |waveform| (lineshape asymmetry)
          wf_n_samples  — number of samples used
        """
        n = len(waveform)
        if n < 4:
            return dict(wf_amplitude=float('nan'), wf_centroid=float('nan'),
                        wf_rms_width=float('nan'), wf_skewness=float('nan'),
                        wf_n_samples=n)
        amp       = max(waveform) - min(waveform)
        abs_vals  = [abs(v) for v in waveform]
        abs_sum   = sum(abs_vals)
        if abs_sum == 0:
            return dict(wf_amplitude=amp, wf_centroid=float('nan'),
                        wf_rms_width=float('nan'), wf_skewness=float('nan'),
                        wf_n_samples=n)
        centroid  = sum(i * abs_vals[i] for i in range(n)) / abs_sum
        variance  = sum((i - centroid) ** 2 * abs_vals[i] for i in range(n)) / abs_sum
        rms_w     = variance ** 0.5 if variance > 0 else 0.0
        if rms_w > 0:
            skewness = (sum(((i - centroid) / rms_w) ** 3 * abs_vals[i]
                           for i in range(n)) / abs_sum)
        else:
            skewness = 0.0
        return dict(wf_amplitude=amp, wf_centroid=centroid,
                    wf_rms_width=rms_w, wf_skewness=skewness,
                    wf_n_samples=n)

    @classmethod
    def _rayleigh_d18O(cls, mr_g_kg: float, T_celsius: float, q_ref: float) -> float:
        """
        Estimate δ¹⁸O (‰ VSMOW) from mixing ratio and temperature via
        the Rayleigh distillation model with Majoube (1971) fractionation.

        Parameters
        ----------
        mr_g_kg   : measured water-vapour mixing ratio  [g/kg]
        T_celsius : in-situ temperature                 [°C]
        q_ref     : reference (surface) mixing ratio    [g/kg]
                    (median of the first stable MR readings)

        Returns
        -------
        δ¹⁸O in ‰ VSMOW, or nan if the instrument is not locked (MR < threshold).

        Physics
        -------
        The liquid–vapour equilibrium fractionation factor for ¹⁸O/¹⁶O is
        (Majoube 1971):
            α_eq(T) = exp( 1137/T_K² − 0.4156/T_K − 2.0667×10⁻³ )
        The isotopic enrichment relative to reference (Rayleigh equation):
            δ¹⁸O = δ₀ + ε_eq × ln( q / q_ref )
        where ε_eq = (α_eq − 1) × 1000  [‰].
        """
        import math
        if mr_g_kg < cls._MR_LOCK_MIN:
            return float('nan')
        T_K      = T_celsius + 273.15
        alpha_eq = math.exp(cls._MJB_A / T_K**2
                            + cls._MJB_B / T_K
                            + cls._MJB_C)
        eps_eq   = (alpha_eq - 1.0) * 1000.0          # ‰
        q        = max(mr_g_kg, 1e-9)                  # guard against log(0)
        return cls._DELTA0 + eps_eq * math.log(q / q_ref)

    @classmethod
    def _build_q_ref(cls, lines: list) -> float:
        """
        Pre-scan all lines and return the median MR of the first 100 stable
        readings (MR > 2 g/kg).  Falls back to 2.67 g/kg if none found.
        Used to anchor the Rayleigh distillation reference point.
        """
        stable = []
        for raw_line in lines:
            cols = raw_line.strip().split(',')
            if len(cols) > 100 or len(cols) < 9:
                continue
            mr = cls._safe(cols[8])
            if mr is not None and mr > 2.0:
                stable.append(mr)
                if len(stable) >= 100:
                    break
        if not stable:
            return 2.67
        stable.sort()
        mid = len(stable) // 2
        return stable[mid] if len(stable) % 2 else (stable[mid - 1] + stable[mid]) / 2.0

    @classmethod
    def parse_file(cls, text: str) -> list:
        """
        Parse the entire CSV text.
        Returns list of (b'', parsed_dict, status_str) tuples compatible
        with the rest of the pipeline (raw_bytes is empty — no binary form).
        """
        all_lines = text.splitlines()
        q_ref = cls._build_q_ref(all_lines)
        print(f"[CSV] δ¹⁸O reference MR q_ref = {q_ref:.4f} g/kg  "
              f"(median of first 100 stable readings)")

        results = []
        for lineno, raw_line in enumerate(all_lines, start=1):
            line = raw_line.strip()
            if not line:
                continue
            cols = line.split(',')
            # Skip sounding/profile records (>100 columns) and header lines
            if len(cols) > 100 or len(cols) < 13:
                continue
            try:
                d = {}
                d['packet_number']    = int(float(cols[0]))
                d['t0_loop']          = int(float(cols[1]))
                d['fan_speed_pwm']    = int(float(cols[2]))
                d['fan_speed_pct']    = round(int(float(cols[2])) / 255.0 * 100.0, 1)
                d['temperature']      = float(cols[3])
                p_hpa                 = float(cols[4])
                d['pressure']         = p_hpa
                d['altitude']         = cls._pressure_to_altitude(p_hpa)
                d['laser_timestamp_ms'] = int(float(cols[5]))
                d['humidity']         = float(cols[6])
                d['tf_loop']          = int(float(cols[7]))
                d['loop_duration_ms'] = d['tf_loop'] - d['t0_loop']

                mr  = cls._safe(cols[8])
                bkg = cls._safe(cols[12])
                d['laser_mixing_ratio'] = mr  if mr  is not None else 0.0
                d['laser_background']   = bkg if bkg is not None else 0.0

                # laser_peak: use signal_c (col 11) scaled down if available,
                # otherwise leave as 0
                sig_c = cls._safe(cols[11])
                d['laser_peak'] = (sig_c / 2000.0) if sig_c is not None else 0.0

                d['laser_packet_num'] = int(float(cols[13])) if cls._safe(cols[13]) is not None else 0
                d['laser_data']       = [d['laser_mixing_ratio'],
                                         d['laser_background'],
                                         d['laser_peak']]

                # ── WMS 2f waveform (col 14 = n_valid_samples, col 15+ = samples) ──
                n_valid, waveform = cls._parse_waveform(cols, start=14)
                d['wf_n_valid']  = n_valid
                d['wf_samples']  = waveform
                d.update(cls._waveform_features(waveform))

                # ── δ¹⁸O via Rayleigh distillation (Majoube 1971) ─────────────────
                # α_eq(T) = exp(1137/T_K² − 0.4156/T_K − 2.0667×10⁻³)
                # δ¹⁸O   = δ₀ + (α_eq − 1)×1000 × ln(q / q_ref)
                d['isotope'] = cls._rayleigh_d18O(
                    d['laser_mixing_ratio'], d['temperature'], q_ref)

                # Placeholders for fields not in CSV
                d['wind_speed']     = 0.0
                d['wind_direction'] = 0.0
                d['channel']        = 'CSV_FILE'

                iso_s = f"{d['isotope']:.3f}" if d['isotope'] == d['isotope'] else 'nan'
                print(f"[CSV] pkt#{d['packet_number']:>4}  "
                      f"T={d['temperature']:.1f}°C  "
                      f"P={d['pressure']:.1f}hPa  "
                      f"alt={d['altitude']:.0f}m  "
                      f"RH={d['humidity']:.1f}%  "
                      f"MR={d['laser_mixing_ratio']:.5f}  "
                      f"δ¹⁸O={iso_s}‰  "
                      f"fan={d['fan_speed_pwm']}PWM")

                results.append((b'', d, 'OK (CSV)'))

            except Exception as e:
                print(f"[CSV] ✗ line {lineno}: {e}  →  {line[:60]}")
                results.append((b'', None, f'CSV parse error line {lineno}: {e}'))

        return results


# ──────────────────────────────────────────────
# Wi-Fi transceiver (TCP server on ground station)
# ──────────────────────────────────────────────
class WiFiReceiver:
    HEADER       = b'\x55\x24'
    DEFAULT_PORT = 55555
    ROUTER_IP    = '192.168.0.47'

    def __init__(self, host='auto', port=DEFAULT_PORT):
        self.host             = host
        self.port             = port
        self.server_socket    = None
        self.client_conn      = None
        self.client_addr      = None
        self.connected        = False
        self.client_connected = False
        self.running          = False
        self._pkt_buf         = BinaryPacketBuffer("WiFi")

    @staticmethod
    def detect_local_ip(router_ip: str = ROUTER_IP) -> str | None:
        try:
            probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            probe.settimeout(1.0)
            probe.connect((router_ip, 80))
            local_ip = probe.getsockname()[0]
            probe.close()
            return local_ip
        except Exception:
            return None

    def start_server(self):
        if self.host == 'auto':
            detected = WiFiReceiver.detect_local_ip(self.ROUTER_IP)
            if detected:
                bind_addr = detected
            else:
                return (False,
                        "Cannot reach router — make sure this PC is on SWIFT STATION Wi-Fi first.")
        else:
            bind_addr = self.host

        try:
            self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.server_socket.settimeout(5.0)
            self.server_socket.bind((bind_addr, self.port))
            self.server_socket.listen(1)
            self.connected = True
            return True, f"Wi-Fi server listening on {bind_addr}:{self.port}"
        except OSError as e:
            if getattr(e, 'winerror', None) == 10049:
                return (False, f"WinError 10049 — '{bind_addr}' not on this PC. Connect to SWIFT STATION Wi-Fi.")
            if getattr(e, 'winerror', None) == 10013:
                return (False, f"WinError 10013 — port {self.port} blocked by Windows Firewall.")
            return False, f"Server start failed: {e}"
        except Exception as e:
            return False, f"Server start failed: {e}"

    def accept_connection(self):
        try:
            conn, addr = self.server_socket.accept()
            conn.setblocking(False)
            self.client_conn      = conn
            self.client_addr      = addr
            self.client_connected = True
            self._pkt_buf         = BinaryPacketBuffer("WiFi")
            return conn, f"Payload connected from {addr[0]}:{addr[1]}"
        except socket.timeout:
            return None, "Waiting for payload connection..."
        except Exception as e:
            return None, f"Accept error: {e}"

    def recv_into_buffer(self) -> bool:
        ready, _, _ = select.select([self.client_conn], [], [], 0.05)
        if not ready:
            return False
        chunk = self.client_conn.recv(4096)
        if not chunk:
            self.client_connected = False
            raise ConnectionError("Payload closed the connection")
        self._pkt_buf.feed(chunk)
        return True

    def drain_packets(self) -> list:
        return self._pkt_buf.drain()

    def send_fan_control(self, fan_pwm: int) -> bool:
        if not self.client_connected or self.client_conn is None:
            return False
        try:
            pkt = PacketParser.build_fan_control_packet(fan_pwm)
            self.client_conn.sendall(pkt)
            return True
        except Exception as e:
            print(f"[WiFi] Send error: {e}")
            self.client_connected = False
            return False

    def disconnect(self):
        self.running = False
        self.client_connected = False
        for s in [self.client_conn, self.server_socket]:
            if s:
                try: s.close()
                except: pass
        self.client_conn   = None
        self.server_socket = None
        self.connected     = False


# ──────────────────────────────────────────────
# [FIX 1 + 4] Serial receiver — DUAL-MODE (binary OR text)
# ──────────────────────────────────────────────
class SerialReceiver:
    """
    Dual-mode serial receiver.

    MODE AUTO-DETECTION (runs once on first received bytes):
      • Binary mode: first two bytes are 0x55 0x24  →  BinaryPacketBuffer
      • Text mode  : first bytes are printable ASCII  →  TextModePacketParser
        (this is what you get from the Arduino USB serial debug output)

    Both modes produce identical (raw_bytes, parsed_dict, status) tuples
    fed into the shared data_queue, so the UI and plots work exactly the same.

    Can also send uplink fan command packets back to the payload.
    """

    MODE_UNKNOWN = 'unknown'
    MODE_BINARY  = 'binary'
    MODE_TEXT    = 'text'

    def __init__(self, port=None, baudrate=9600):
        self.port        = port
        self.baudrate    = baudrate
        self.serial_conn = None
        self.connected   = False
        self.running     = False
        self._mode                 = self.MODE_UNKNOWN
        self._bin_buf              = BinaryPacketBuffer("Serial")
        self._txt_parser           = TextModePacketParser("Serial")
        self._detect_buf           = bytearray()   # used during MODE_UNKNOWN sniffing
        self._pending_text_packets = []            # [FIX 9] packets flushed during mode detection

    @staticmethod
    def list_ports():
        return [p.device for p in serial.tools.list_ports.comports()]

    def connect(self, port=None, baudrate=None):
        try:
            if port:     self.port     = port
            if baudrate: self.baudrate = baudrate
            if not self.port:
                return False, "No COM port specified"
            self.serial_conn = serial.Serial(
                self.port, self.baudrate,
                timeout=0.1,
                write_timeout=2.0
            )
            self.connected             = True
            self._mode                 = self.MODE_UNKNOWN
            self._bin_buf              = BinaryPacketBuffer("Serial")
            self._txt_parser           = TextModePacketParser("Serial")
            self._detect_buf           = bytearray()
            self._pending_text_packets = []
            return True, f"Serial: {self.port} @ {self.baudrate} baud"
        except Exception as e:
            return False, f"Serial error: {e}"

    def _detect_mode(self, chunk: bytes) -> str:
        """
        Sniff the first bytes to decide binary vs text mode.
        Returns MODE_BINARY, MODE_TEXT, or MODE_UNKNOWN (need more data).
        """
        self._detect_buf.extend(chunk)

        # If we see 0x55 0x24 anywhere in the first 8 bytes → binary
        if len(self._detect_buf) >= 2:
            if b'\x55\x24' in self._detect_buf[:8]:
                print(f"[Serial] 🔍 Auto-detected: BINARY mode (raw packet header 55 24 found)")
                return self.MODE_BINARY

        # If we see a newline or lots of printable ASCII → text mode
        if len(self._detect_buf) >= 8:
            printable = sum(1 for b in self._detect_buf[:16] if 32 <= b < 128 or b in (10, 13))
            if printable >= 6:
                print(f"[Serial] 🔍 Auto-detected: TEXT mode (Arduino USB debug output)")
                return self.MODE_TEXT
            # Non-printable and no 55 24 header → probably still binary, try anyway
            if len(self._detect_buf) >= 16:
                print(f"[Serial] 🔍 Assuming BINARY mode (no clear text indicators)")
                return self.MODE_BINARY

        return self.MODE_UNKNOWN

    def recv_into_buffer(self) -> bool:
        """[FIX 4] Read available bytes as binary. Returns True if new data arrived."""
        if not self.serial_conn or not self.connected:
            return False
        try:
            waiting = self.serial_conn.in_waiting
            if waiting == 0:
                return False
            chunk = self.serial_conn.read(waiting)
            if not chunk:
                return False

            # [FIX 1] Auto-detect mode on first data
            if self._mode == self.MODE_UNKNOWN:
                self._mode = self._detect_mode(chunk)
                if self._mode == self.MODE_UNKNOWN:
                    return True   # still sniffing, no complete packets yet
                # Flush accumulated detection bytes into the right parser
                accumulated = bytes(self._detect_buf)
                self._detect_buf.clear()
                if self._mode == self.MODE_BINARY:
                    self._bin_buf.feed(accumulated)
                else:
                    # [FIX 9] Save packets from detection flush — previously discarded
                    flushed = self._txt_parser.feed_bytes(accumulated)
                    self._pending_text_packets.extend(flushed)
                return True

            # Steady-state: feed into correct parser
            if self._mode == self.MODE_BINARY:
                self._bin_buf.feed(chunk)
            else:
                self._txt_parser.feed_bytes(chunk)
            return True

        except Exception as e:
            print(f"[Serial] Read error: {e}")
            self.connected = False
            return False

    def drain_packets(self) -> list:
        """Return all complete parsed packets from whichever mode is active."""
        if self._mode == self.MODE_BINARY:
            return self._bin_buf.drain()
        elif self._mode == self.MODE_TEXT:
            # TextModePacketParser drains during feed_bytes(), not separately
            # We return nothing extra here; packets were already returned by feed_bytes
            return []
        return []

    # [FIX 1] Text mode packets are returned directly from recv_into_buffer
    # via _txt_parser.feed_bytes(). We need a way to get them out.
    # We'll store them in an internal list and drain here.
    def _flush_text_packets(self) -> list:
        return []   # see _serial_loop below for how text packets are collected

    def send_fan_control(self, fan_pwm: int) -> bool:
        """Send fan uplink packet (CH1=233) over serial to payload."""
        if not self.connected or self.serial_conn is None:
            return False
        try:
            pkt = PacketParser.build_fan_control_packet(fan_pwm)
            self.serial_conn.write(pkt)
            return True
        except Exception as e:
            print(f"[Serial] Send error: {e}")
            self.connected = False
            return False

    def disconnect(self):
        self.running = False
        if self.serial_conn:
            try: self.serial_conn.close()
            except: pass
        self.connected = False


# ──────────────────────────────────────────────
# Atmospheric boundary layer analyser
# ──────────────────────────────────────────────
class BoundaryLayerAnalyzer:
    @staticmethod
    def analyze(altitude, temp, pressure, humidity, wind_speed):
        if len(altitude) < 10:
            return None

        # Guard: if altitude is flat (constant-pressure CSV data with no real
        # vertical profile), np.gradient(f, x) will divide by zero because all
        # dx values are zero.  Return a safe default instead of crashing.
        alt_range = float(np.max(altitude) - np.min(altitude))
        if alt_range < 50.0:   # less than 50 m total spread → not a sounding
            return {
                'surface_layer_top':      100,
                'pbl_top':                4000,
                'free_troposphere_start': 5000,
                'inversion_detected':     False,
                'mixing_height':          None,
            }

        res = {
            'surface_layer_top':      100,
            'pbl_top':                None,
            'free_troposphere_start': 5000,
            'inversion_detected':     False,
            'mixing_height':          None,
        }
        theta = temp * (1000.0 / pressure) ** 0.286

        # Suppress divide-by-zero / invalid-value warnings that numpy raises
        # when spacing values are very small or repeated.
        with np.errstate(divide='ignore', invalid='ignore'):
            dtheta = np.gradient(theta,      altitude)
            dwind  = np.gradient(wind_speed, altitude)

        # Replace any NaN/Inf produced by the gradient with 0
        dtheta = np.nan_to_num(dtheta, nan=0.0, posinf=0.0, neginf=0.0)
        dwind  = np.nan_to_num(dwind,  nan=0.0, posinf=0.0, neginf=0.0)

        threshold = 0.005

        for i, alt in enumerate(altitude):
            if 100 < alt < 4000 and dtheta[i] > threshold:
                res['pbl_top']            = alt
                res['inversion_detected'] = True
                res['mixing_height']      = alt
                break

        if res['pbl_top'] is None:
            idxs = [i for i, a in enumerate(altitude) if 100 < a < 4000]
            if idxs:
                shear = [abs(dwind[i]) for i in idxs if i < len(dwind)]
                if shear:
                    best = idxs[int(np.argmax(shear))]
                    res['pbl_top']       = altitude[best]
                    res['mixing_height'] = altitude[best]

        if res['pbl_top'] is None or res['pbl_top'] < 100:
            res['pbl_top'] = 4000

        return res


# ──────────────────────────────────────────────
# Splash screen
# ──────────────────────────────────────────────
class SplashScreen:
    def __init__(self, parent, logo_path, duration=3000):
        self.parent    = parent
        self.logo_path = logo_path
        self.splash    = tk.Toplevel(parent)
        self.splash.attributes('-fullscreen', True)
        self.splash.configure(bg='#0A0A0A')
        self.splash.overrideredirect(True)

        c = tk.Frame(self.splash, bg='#0A0A0A')
        c.place(relx=0.5, rely=0.5, anchor='center')

        self.title_label = tk.Label(c, text="FLAIR CONTROL DECK",
                                    font=('Bahnschrift', 48, 'bold'),
                                    bg='#0A0A0A', fg='#00FFFF')
        self.title_label.pack(pady=20)

        self.logo_label = tk.Label(c, bg='#0A0A0A')
        self.logo_label.pack(pady=20)

        self.status_label = tk.Label(c, text="Initializing Systems...",
                                     font=('Consolas', 14), bg='#0A0A0A', fg='#00FF00')
        self.status_label.pack(pady=10)

        self.progress = ttk.Progressbar(c, length=400, mode='determinate')
        self.progress.pack(pady=20)

        self.alpha = 0.0; self.scale = 0.5; self.rotation = 0; self.progress_value = 0
        self._load_logo()
        self._animate()
        self.splash.after(duration, self.splash.destroy)

    def _load_logo(self):
        try:
            p = self.logo_path
            self.original_logo = Image.open(p) if os.path.exists(p) else Image.new('RGBA', (200, 200), (0, 255, 255, 255))
        except:
            self.original_logo = Image.new('RGBA', (200, 200), (0, 255, 255, 255))

    def _animate(self):
        self.alpha          = min(1.0, self.alpha + 0.02)
        self.scale          = min(1.0, self.scale + 0.02) if self.scale < 1.0 else 1.0
        self.rotation       = (self.rotation + 3) % 360
        self.progress_value = min(100, self.progress_value + 2)
        self.progress['value'] = self.progress_value

        msgs = ["Initializing Systems...", "Loading Packet Parser...",
                "Connecting to Wi-Fi...", "Preparing Telemetry...", "Ready for Launch!"]
        self.status_label.config(text=msgs[min(int(self.progress_value / 20), 4)])

        cv = int(128 + 127 * np.sin(self.rotation * np.pi / 180))
        self.title_label.config(fg=f'#{cv:02x}{255:02x}{255:02x}')

        sz  = int(200 * self.scale)
        img = self.original_logo.resize((sz, sz), Image.Resampling.LANCZOS).rotate(self.rotation)
        if self.alpha < 1.0:
            img.putalpha(Image.new('L', img.size, int(255 * self.alpha)))
        self.logo_photo = ImageTk.PhotoImage(img)
        self.logo_label.config(image=self.logo_photo)

        if self.progress_value < 100:
            self.splash.after(50, self._animate)


# ──────────────────────────────────────────────
# Helper: smooth correlated noise for test data
# ──────────────────────────────────────────────
def _smooth(rng, n: int, sigma: float, width: int = 20) -> np.ndarray:
    """
    Return a length-n array of spatially correlated noise with standard
    deviation ≈ sigma.  Uses a simple box-filter convolution so there is no
    scipy dependency.  'width' is the half-window size in samples.
    """
    raw  = rng.standard_normal(n) * sigma
    kern = np.ones(max(1, width * 2 + 1))
    kern /= kern.sum()
    return np.convolve(raw, kern, mode='same')


# ──────────────────────────────────────────────
# Main application
# ──────────────────────────────────────────────
class FLAIRControlDeck:

    LAYERS = [
        ('SRL',   0,      100,    'orange',     0.18, 'Surface Roughness Layer'),
        ('PBL',   100,   4000,    'green',      0.15, 'Planetary Boundary Layer'),
        ('TRANS', 4000,  5000,    'yellow',     0.12, 'Transition / Residual Layer'),
        ('FT',    5000, 18000,    'royalblue',  0.15, 'Free Troposphere'),
    ]

    def __init__(self, root):
        self.root = root
        self.root.title("FLAIR Control Deck — Payload Ground Station v13")
        self.root.attributes('-fullscreen', True)
        self.root.bind('<Escape>', lambda e: self._toggle_fullscreen())
        self.root.bind('<F11>',   lambda e: self._toggle_fullscreen())

        self.dark_mode = True

        # ── Data stores ────────────────────────────────────────────
        self.data_queue          = queue.Queue()
        self.altitude_data       = []
        self.temp_data           = []
        self.pressure_data       = []
        self.humidity_data       = []
        self.wind_speed_data     = []
        self.wind_direction_data = []
        self.isotope_data        = []

        self.packet_numbers      = []
        self.fan_speed_pwm_rx    = []
        self.laser_mr_data       = []
        self.laser_bkg_data      = []
        self.laser_peak_data     = []
        self.laser_data_history  = []

        self.total_rx  = 0
        self.failed_rx = 0

        # Fan control
        self.fan_enabled    = False
        self.fan_power_pct  = 25
        self._last_sent_pwm = -1

        # Mission
        self.mission_running    = False
        self.mission_start_time = None

        # Test data
        self.test_data_buffer = []
        self.test_data_index  = 0
        self.streaming_test   = False
        self.test_interval_ms = 80

        # Wi-Fi
        self.wifi           = WiFiReceiver(host='auto', port=WiFiReceiver.DEFAULT_PORT)
        self.wifi_thread    = None
        self.wifi_server_up = False
        self.wifi_client_up = False

        # Serial
        self.serial_rx     = SerialReceiver(baudrate=9600)
        self.serial_thread = None
        self.serial_up     = False

        # Post-flight file load mode
        self.file_load_mode   = False   # True while replaying a saved packet file
        self.file_load_thread = None

        self._build_ui()
        self._update_data()
        self._update_clock()

    def _toggle_fullscreen(self):
        self.root.attributes('-fullscreen', not self.root.attributes('-fullscreen'))

    def _msg(self, text: str):
        self.message_box.delete(0, tk.END)
        self.message_box.insert(0, text)

    def _fan_pwm(self) -> int:
        return int(self.fan_power_pct / 100.0 * 255) if self.fan_enabled else 0

    # ── UI construction ─────────────────────────────────────────────────────
    def _build_ui(self):
        self.main_frame = tk.Frame(self.root, bg='#0F0F0F')
        self.main_frame.pack(fill=tk.BOTH, expand=True)

        self.bg_canvas = tk.Canvas(self.main_frame, bg='#0F0F0F', highlightthickness=0)
        self.bg_canvas.place(x=0, y=0, relwidth=1, relheight=1)
        self._draw_gradient()

        cc = tk.Frame(self.main_frame, bg='')
        cc.place(x=0, y=0, relwidth=1, relheight=1)

        # ── Top bar ──
        top = tk.Frame(cc, bg='', height=100)
        top.pack(fill=tk.X, padx=10, pady=5)
        top.pack_propagate(False)

        logo_f = tk.Frame(top, bg='', width=150)
        logo_f.pack(side=tk.LEFT, padx=10)
        try:
            if os.path.exists("logo.png"):
                img = Image.open("logo.png").resize((120, 80), Image.Resampling.LANCZOS)
                self.logo_img = ImageTk.PhotoImage(img)
                tk.Label(logo_f, image=self.logo_img, bg='#1E1E1E').pack()
            else:
                raise FileNotFoundError
        except:
            tk.Label(logo_f, text="FLAIR\nLOGO", font=('Arial', 12, 'bold'),
                     bg='#1E1E1E', fg='white').pack()

        title_f = tk.Frame(top, bg='')
        title_f.pack(side=tk.LEFT, expand=True)
        self.title_label = tk.Label(
            title_f,
            text="FLAIR CONTROL DECK  ·  Payload Ground Station  [RX CH0=232 | TX CH1=233]",
            font=('Bahnschrift', 20, 'bold'), bg='#1E1E1E', fg='#00FFFF')
        self.title_label.pack()

        mode_f = tk.Frame(top, bg='', width=100)
        mode_f.pack(side=tk.RIGHT, padx=10)
        tk.Label(mode_f, text="MODE", font=('Segoe UI', 10, 'bold'),
                 bg='#1E1E1E', fg='#FFD700').pack()
        self.mode_btn = tk.Button(mode_f, text="🌙", font=('Arial', 16),
                                  command=self._toggle_mode, width=3,
                                  bg='#404040', fg='white', relief=tk.RAISED, bd=2)
        self.mode_btn.pack()

        # ── Message bar ──
        msg_f = tk.Frame(cc, bg='')
        msg_f.pack(fill=tk.X, padx=10, pady=3)
        tk.Label(msg_f, text="Status:", font=('Segoe UI', 10, 'bold'),
                 bg='#1E1E1E', fg='#FFD700').pack(side=tk.LEFT)
        self.message_box = tk.Entry(msg_f, font=('Calibri', 10), width=90,
                                    bg='#2D2D2D', fg='white', insertbackground='white')
        self.message_box.pack(side=tk.LEFT, padx=5)

        # Serial mode indicator label
        self.serial_mode_label = tk.Label(msg_f, text="Serial: —",
                                          font=('Consolas', 9), bg='#1E1E1E', fg='#888888')
        self.serial_mode_label.pack(side=tk.LEFT, padx=8)

        # ── Content area ──
        content = tk.Frame(cc, bg='')
        content.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        left = tk.Frame(content, bg='#1A1A1A', width=240, relief=tk.RAISED, bd=2)
        left.pack(side=tk.LEFT, fill=tk.Y, padx=5)
        left.pack_propagate(False)
        self._build_left_panel(left)

        right_col = tk.Frame(content, bg='#1E1E1E')
        right_col.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=5)

        graph_frame = tk.Frame(right_col, bg='#1E1E1E', relief=tk.RAISED, bd=2)
        graph_frame.pack(fill=tk.BOTH, expand=True)
        self._build_graphs(graph_frame)

        self._build_packet_monitor(right_col)

    def _build_left_panel(self, parent):
        btns = [
            ("CONNECT WiFi",   self._connect_wifi,   "#1E90FF", "#0066CC"),
            ("CONNECT SERIAL", self._connect_serial, "#9932CC", "#6A0DAD"),
            ("START MISSION",  self._start_mission,  "#32CD32", "#228B22"),
            ("END MISSION",    self._end_mission,    "#FF4500", "#CC3300"),
            ("SAVE DATA",      self._save_data,      "#FFD700", "#CC9900"),
        ]
        for text, cmd, c1, c2 in btns:
            f = tk.Frame(parent, bg=c1, relief=tk.RAISED, bd=3)
            f.pack(fill=tk.X, pady=5, padx=10)
            tk.Button(f, text=text, command=cmd, font=('Courier New', 9, 'bold'),
                      bg=c1, fg='white', activebackground=c2, activeforeground='white',
                      relief=tk.FLAT, bd=0, height=2).pack(fill=tk.BOTH, expand=True)

        # ── Post-flight / backup packet file loader ──
        sep_file = tk.Frame(parent, bg='#888844', height=2)
        sep_file.pack(fill=tk.X, padx=10, pady=(8, 2))

        tk.Label(parent, text="▼  BACKUP / POST-FLIGHT",
                 font=('Segoe UI', 8, 'bold'),
                 bg='#1A1A1A', fg='#BBBB55').pack(pady=(0, 2))

        file_f = tk.Frame(parent, bg='#3A3A1A', relief=tk.RAISED, bd=3)
        file_f.pack(fill=tk.X, pady=3, padx=10)
        self.load_file_btn = tk.Button(
            file_f, text="📂  LOAD PACKETS\n(FILE / POST-FLIGHT)",
            command=self._load_packets_from_file,
            font=('Courier New', 8, 'bold'),
            bg='#5C5C1A', fg='#EEEE88',
            activebackground='#7A7A22', activeforeground='white',
            relief=tk.FLAT, bd=0, height=2)
        self.load_file_btn.pack(fill=tk.BOTH, expand=True)

        self.file_load_label = tk.Label(
            parent, text="File mode: inactive",
            font=('Consolas', 7), bg='#1A1A1A', fg='#666633',
            wraplength=200, justify=tk.LEFT)
        self.file_load_label.pack(fill=tk.X, padx=10, pady=(0, 4))

        # ── Fan control ──
        sep = tk.Frame(parent, bg='#FF6B35', height=2)
        sep.pack(fill=tk.X, padx=10, pady=5)

        fan_f = tk.Frame(parent, bg='#141414', relief=tk.GROOVE, bd=2)
        fan_f.pack(fill=tk.X, padx=10, pady=4)
        self.fan_frame = fan_f   # kept so we can mute it in file-load mode

        fan_title_f = tk.Frame(fan_f, bg='#141414')
        fan_title_f.pack(fill=tk.X, pady=(4, 0))
        tk.Label(fan_title_f, text="⟳  FAN CONTROL  [TX→CH1=233]",
                 font=('Segoe UI', 10, 'bold'),
                 bg='#141414', fg='#FF6B35').pack(side=tk.LEFT, padx=6, pady=(2, 0))
        self.fan_filemode_label = tk.Label(
            fan_title_f, text="",
            font=('Consolas', 7, 'italic'), bg='#141414', fg='#886600')
        self.fan_filemode_label.pack(side=tk.LEFT, padx=4)

        self.fan_toggle_btn = tk.Button(
            fan_f, text="▶  START FAN",
            command=self._toggle_fan,
            font=('Courier New', 10, 'bold'),
            bg='#8B0000', fg='white', activebackground='#006400',
            relief=tk.RAISED, bd=3, height=2)
        self.fan_toggle_btn.pack(fill=tk.X, padx=6, pady=4)

        tk.Label(fan_f, text="Power Level:", font=('Segoe UI', 9),
                 bg='#141414', fg='#AAAAAA').pack()
        self.fan_pwr_disp = tk.Label(fan_f, text="25 %",
                                     font=('Consolas', 16, 'bold'),
                                     bg='#0D0D0D', fg='#00FF00',
                                     relief=tk.SUNKEN, bd=2, width=8)
        self.fan_pwr_disp.pack(padx=6, pady=3)

        preset_f = tk.Frame(fan_f, bg='#141414')
        preset_f.pack(fill=tk.X, padx=6, pady=2)
        for pct in (10, 25, 50, 75, 100):
            tk.Button(preset_f, text=f"{pct}%",
                      command=lambda p=pct: self._set_fan_pct(p),
                      font=('Consolas', 8, 'bold'),
                      bg='#2D2D2D', fg='white', relief=tk.RAISED, bd=1,
                      width=4).pack(side=tk.LEFT, padx=1)

        self.fan_slider = tk.Scale(fan_f, from_=0, to=100, orient=tk.HORIZONTAL,
                                   command=self._slider_moved,
                                   bg='#2D2D2D', fg='white',
                                   activebackground='#FF6B35',
                                   troughcolor='#404040',
                                   highlightthickness=0, relief=tk.FLAT,
                                   label='Fine adjust (%)')
        self.fan_slider.set(25)
        self.fan_slider.pack(fill=tk.X, padx=6, pady=4)

        self.fan_tx_label = tk.Label(fan_f, text="Last TX: —",
                                     font=('Consolas', 8), bg='#141414', fg='#888888')
        self.fan_tx_label.pack(pady=(0, 4))

        sep2 = tk.Frame(parent, bg='#444444', height=1)
        sep2.pack(fill=tk.X, padx=10, pady=5)

        # ── Clocks ──
        for lbl_text, attr in [("Time (UTC)", 'time_label'), ("Mission Time", 'mission_time_label')]:
            tk.Label(parent, text=lbl_text, font=('Segoe UI', 10, 'bold'),
                     bg='#1A1A1A', fg='#FFD700').pack(pady=(8, 2))
            lbl = tk.Label(parent,
                           text="--:--:--" if 'UTC' in lbl_text else "00:00:00",
                           font=('Consolas', 11, 'bold'),
                           bg='#0D0D0D', fg='#00FF00', relief=tk.SUNKEN, bd=3)
            lbl.pack(fill=tk.X, padx=10, pady=3)
            setattr(self, attr, lbl)

        # ── Packet stats ──
        tk.Label(parent, text="Packets", font=('Segoe UI', 10, 'bold'),
                 bg='#1A1A1A', fg='#FFD700').pack(pady=(8, 2))
        self.pkt_stat_label = tk.Label(parent, text="RX: 0\nFail: 0",
                                       font=('Consolas', 9, 'bold'),
                                       bg='#0D0D0D', fg='#00FFFF',
                                       relief=tk.SUNKEN, bd=3, justify=tk.LEFT)
        self.pkt_stat_label.pack(fill=tk.X, padx=10, pady=3)

        # ── Connection status ──
        tk.Label(parent, text="Connection", font=('Segoe UI', 10, 'bold'),
                 bg='#1A1A1A', fg='#FFD700').pack(pady=(8, 2))
        self.conn_label = tk.Label(parent, text="WiFi: ❌  Serial: ❌",
                                   font=('Consolas', 8, 'bold'),
                                   bg='#0D0D0D', fg='#FF4500',
                                   relief=tk.SUNKEN, bd=3, justify=tk.LEFT)
        self.conn_label.pack(fill=tk.X, padx=10, pady=3)

    # ── Fan callbacks ────────────────────────────────────────────────────────
    def _toggle_fan(self):
        self.fan_enabled = not self.fan_enabled
        if self.fan_enabled:
            self.fan_toggle_btn.config(text="■  STOP FAN", bg='#006400')
            self._msg(f"Fan STARTED at {self.fan_power_pct}% ({self._fan_pwm()} PWM) → CH1=233")
        else:
            self.fan_toggle_btn.config(text="▶  START FAN", bg='#8B0000')
            self._msg("Fan STOPPED → 0 PWM sent to CH1=233")
        self._send_fan_command()

    def _set_fan_pct(self, pct: int):
        self.fan_power_pct = pct
        self.fan_slider.set(pct)
        self.fan_pwr_disp.config(text=f"{pct} %")
        if self.fan_enabled:
            self._msg(f"Fan power → {pct}% ({self._fan_pwm()} PWM)")
            self._send_fan_command()

    def _slider_moved(self, value):
        pct = int(float(value))
        self.fan_power_pct = pct
        self.fan_pwr_disp.config(text=f"{pct} %")
        if self.fan_enabled:
            self._send_fan_command()

    def _send_fan_command(self):
        """Send fan uplink on PAYLOAD_DATA_CHANNEL_1 = 233. WiFi first, then Serial."""
        pwm = self._fan_pwm()
        if pwm == self._last_sent_pwm:
            return
        self._last_sent_pwm = pwm

        ok = False
        if self.wifi_client_up:
            ok = self.wifi.send_fan_control(pwm)
            path = "WiFi"
        elif self.serial_up:
            ok = self.serial_rx.send_fan_control(pwm)
            path = "Serial"
        else:
            path = "offline"

        status = (f"TX fan {'ON' if self.fan_enabled else 'OFF'} {pwm} PWM "
                  f"→ CH1=233 via {path} ({'✓' if ok else '✗ FAIL'})")
        self.fan_tx_label.config(
            text=f"Last TX: {pwm} PWM via {path} {'✓' if ok else '✗'}",
            fg='#00FF00' if ok else '#FF8800')
        self._msg(status)
        print(f"[FAN] {status}")

    # ── Graph construction ──────────────────────────────────────────────────
    def _build_graphs(self, parent):
        self.fig = Figure(figsize=(12, 8), facecolor='#2D2D2D')

        self.ax_wind     = self.fig.add_subplot(3, 3, 1)
        self.ax_temp     = self.fig.add_subplot(3, 3, 2)
        self.ax_status   = self.fig.add_subplot(3, 3, 3)
        self.ax_pres     = self.fig.add_subplot(3, 3, 4)
        self.ax_hum      = self.fig.add_subplot(3, 3, 5)
        self.ax_laser_mr = self.fig.add_subplot(3, 3, 6)
        self.ax_iso      = self.fig.add_subplot(3, 3, 7)
        self.ax_clocks  = self.fig.add_subplot(3, 3, 8)
        self.ax_laser_bp = self.fig.add_subplot(3, 3, 9)

        self.axes = [self.ax_wind, self.ax_temp, self.ax_status,
                     self.ax_pres, self.ax_hum,  self.ax_laser_mr,
                     self.ax_iso,  self.ax_clocks, self.ax_laser_bp]

        titles = [
            'Wind Speed vs Altitude',  'Temperature vs Altitude',  'Payload Status (CH0=232)',
            'Pressure vs Altitude',    'Rel. Humidity vs Altitude', 'Laser — Mixing Ratio',
            'δ¹⁸O Isotope vs Altitude','UTC & Mission Clock',       'Laser — Background & Peak',
        ]
        xlabels = [
            'Wind Speed (m/s)', 'Temperature (°C)', '',
            'Pressure (hPa)',   'Humidity (%)',      'Packet #',
            'Isotope (‰)',      '',                   'Packet #',
        ]
        ylabels = [
            'Altitude (m)', 'Altitude (m)', '',
            'Altitude (m)', 'Altitude (m)', 'Mixing Ratio',
            'Altitude (m)', '',             'Signal',
        ]

        for ax, t, xl, yl in zip(self.axes, titles, xlabels, ylabels):
            ax.set_facecolor('#2D2D2D')
            for sp in ax.spines.values(): sp.set_color('white')
            ax.tick_params(colors='white')
            ax.set_title(t,  fontsize=9,  fontweight='bold', color='white')
            ax.set_xlabel(xl, fontsize=7, color='white')
            ax.set_ylabel(yl, fontsize=7, color='white')

        self.ax_status.axis('off')
        self.ax_clocks.axis('off')

        self.fig.tight_layout(pad=1.2)
        self.canvas = FigureCanvasTkAgg(self.fig, parent)
        self.canvas.draw()
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)

    # ── Packet monitor panel ────────────────────────────────────────────────
    def _build_packet_monitor(self, parent):
        mon_frame = tk.Frame(parent, bg='#0A0A0A', relief=tk.SUNKEN, bd=2, height=180)
        mon_frame.pack(fill=tk.X, pady=(4, 0))
        mon_frame.pack_propagate(False)

        hdr = tk.Frame(mon_frame, bg='#1A1A1A')
        hdr.pack(fill=tk.X)
        tk.Label(hdr,
                 text="  PACKET MONITOR — RX CH0=232 | TX fan→CH1=233 | raw hex + decoded  [FIX 1: auto binary/text mode]",
                 font=('Consolas', 9, 'bold'), bg='#1A1A1A', fg='#00FFFF').pack(side=tk.LEFT, pady=2)
        tk.Button(hdr, text="CLEAR", font=('Consolas', 8), bg='#333333', fg='white',
                  relief=tk.FLAT, bd=1,
                  command=lambda: self.pkt_monitor.delete('1.0', tk.END)).pack(side=tk.RIGHT, padx=4)

        txt_frame = tk.Frame(mon_frame, bg='#0A0A0A')
        txt_frame.pack(fill=tk.BOTH, expand=True)

        self.pkt_monitor = tk.Text(
            txt_frame, bg='#0A0A0A', fg='#00FF41',
            font=('Consolas', 8), wrap=tk.NONE,
            state=tk.DISABLED, relief=tk.FLAT)
        self.pkt_monitor.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        vsb = tk.Scrollbar(txt_frame, orient=tk.VERTICAL, command=self.pkt_monitor.yview)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        self.pkt_monitor.config(yscrollcommand=vsb.set)

        hsb = tk.Scrollbar(mon_frame, orient=tk.HORIZONTAL, command=self.pkt_monitor.xview)
        hsb.pack(fill=tk.X)
        self.pkt_monitor.config(xscrollcommand=hsb.set)

        self.pkt_monitor.tag_config('hdr',   foreground='#FFD700')
        self.pkt_monitor.tag_config('field', foreground='#00FFFF')
        self.pkt_monitor.tag_config('warn',  foreground='#FF4500')
        self.pkt_monitor.tag_config('raw',   foreground='#888888')
        self.pkt_monitor.tag_config('good',  foreground='#00FF41')

        self._pkt_monitor_count = 0

    def _monitor_log(self, raw_bytes: bytes, parsed, status: str):
        self._pkt_monitor_count += 1
        w  = self.pkt_monitor
        ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        w.config(state=tk.NORMAL)

        if parsed is None:
            w.insert(tk.END, '[%s] PKT #%04d  ' % (ts, self._pkt_monitor_count), 'hdr')
            w.insert(tk.END, 'PARSE FAILED: %s\n' % status, 'warn')
            if raw_bytes:
                hex_str = ' '.join('%02X' % b for b in raw_bytes)
                w.insert(tk.END, '  RAW (%d B): %s\n\n' % (len(raw_bytes), hex_str), 'raw')
        else:
            pnum = parsed.get('packet_number', '?')
            ch   = parsed.get('channel', 'type=%s' % parsed.get('packet_type', '?'))
            w.insert(tk.END,
                     '[%s] PKT #%04d  payload_pkt=%s  %s  (%d B)\n'
                     % (ts, self._pkt_monitor_count, pnum, ch, len(raw_bytes)), 'hdr')
            for row_i in range(0, len(raw_bytes), 16):
                row = raw_bytes[row_i:row_i + 16]
                hex_part   = ' '.join('%02X' % b for b in row)
                ascii_part = ''.join(chr(b) if 32 <= b < 127 else '.' for b in row)
                w.insert(tk.END, '  %04X  %-48s  %s\n' % (row_i, hex_part, ascii_part), 'raw')
            fields = [
                ('t0_loop',  parsed.get('t0_loop'),           'ms'),
                ('tf_loop',  parsed.get('tf_loop'),           'ms'),
                ('loop_dur', parsed.get('loop_duration_ms'),  'ms'),
                ('fan_PWM',  parsed.get('fan_speed_pwm'),     '/255'),
                ('fan_%',    parsed.get('fan_speed_pct'),     '%'),
                ('temp',     parsed.get('temperature'),       'C'),
                ('humid',    parsed.get('humidity'),          '%RH'),
                ('laser_ts', parsed.get('laser_timestamp_ms'),'ms'),
                ('laser_pk', parsed.get('laser_packet_num'),  ''),
                ('MR',       parsed.get('laser_mixing_ratio'),''),
                ('bkg',      parsed.get('laser_background'),  ''),
                ('peak',     parsed.get('laser_peak'),        ''),
            ]
            parts = []
            for name, val, unit in fields:
                if val is None:
                    continue
                fval = ('%.4f' % val) if isinstance(val, float) else str(val)
                parts.append('%s=%s%s' % (name, fval, unit))
            w.insert(tk.END, '  ' + '  '.join(parts) + '\n\n', 'field')

        w.see(tk.END)
        w.config(state=tk.DISABLED)

        # Trim old lines to prevent unbounded growth
        if self._pkt_monitor_count % 50 == 0:
            total_lines = int(w.index(tk.END).split('.')[0])
            if total_lines > 600:
                w.config(state=tk.NORMAL)
                w.delete('1.0', '%d.0' % (total_lines - 500))
                w.config(state=tk.DISABLED)

    # ── Layer shading ────────────────────────────────────────────────────────
    def _add_layers(self, ax, max_alt, results=None):
        srl_top   = 100
        pbl_top   = (results['pbl_top']              if results else None) or 4000
        trans_top = (results['free_troposphere_start'] if results else None) or 5000
        ft_top    = min(max_alt * 1.05, 18000)

        layer_bounds = [
            ('SRL',   10,        srl_top,   'orange',    0.18, 'Surface Roughness\nLayer (SRL)'),
            ('PBL',   srl_top,   pbl_top,   'green',     0.15, f'Planetary Boundary\nLayer (PBL)\n~{pbl_top:.0f} m'),
            ('TRANS', pbl_top,   trans_top, 'yellow',    0.12, 'Transition /\nResidual Layer'),
            ('FT',    trans_top, ft_top,    'royalblue', 0.15, 'Free Troposphere\n(FT)'),
        ]

        for _, bot, top, color, alpha, label in layer_bounds:
            if top <= bot or bot >= max_alt:
                continue
            top_clipped = min(top, max_alt)
            ax.axhspan(bot, top_clipped, alpha=alpha, color=color, zorder=1)
            mid = (bot + top_clipped) / 2.0
            if mid <= max_alt:
                ax.text(0.01, mid, label,
                        transform=ax.get_yaxis_transform(),
                        fontsize=5.5, color=color, alpha=0.85,
                        va='center', ha='left', zorder=3,
                        bbox=dict(boxstyle='round,pad=0.1', facecolor='black',
                                  alpha=0.35, edgecolor='none'))

    # ── Plot update ─────────────────────────────────────────────────────────
    def _update_plots(self):
        color    = '#00FFFF' if self.dark_mode else '#0000FF'
        fg_color = 'white'   if self.dark_mode else 'black'
        graph_bg = '#2D2D2D' if self.dark_mode else '#F5F5DC'

        for ax in self.axes:
            ax.clear()
            ax.set_facecolor(graph_bg)
            for sp in ax.spines.values(): sp.set_color(fg_color)
            ax.tick_params(colors=fg_color)

        results = None
        if len(self.altitude_data) > 10:
            results = BoundaryLayerAnalyzer.analyze(
                np.array(self.altitude_data),  np.array(self.temp_data),
                np.array(self.pressure_data),  np.array(self.humidity_data),
                np.array(self.wind_speed_data))

        max_alt = max(self.altitude_data) if self.altitude_data else 500
        if max_alt < 100: max_alt = 500

        # Detect whether we have a real vertical sounding (altitude varies > 50 m)
        # or flat ground-station / CSV data.  In the flat case, swap to time-series
        # plots (packet index on X, value on Y) so the data is actually visible.
        alt_arr   = np.array(self.altitude_data) if self.altitude_data else np.array([0.0])
        alt_range = float(alt_arr.max() - alt_arr.min()) if len(alt_arr) > 1 else 0.0
        flat_data = alt_range < 50.0   # True for CSV / ground-level logs

        profile_specs = [
            (self.ax_wind, self.wind_speed_data,   'Wind Speed',    'Wind Speed (m/s)',  '#00FFFF'),
            (self.ax_temp, self.temp_data,          'Temperature',   'Temperature (°C)',  '#FF6B35'),
            (self.ax_pres, self.pressure_data,      'Pressure',      'Pressure (hPa)',    '#FFD700'),
            (self.ax_hum,  self.humidity_data,      'Rel. Humidity', 'Humidity (%)',      '#32CD32'),
            (self.ax_iso,  self.isotope_data,       'δ¹⁸O Isotope',  'Isotope (‰)',       '#DA70D6'),
        ]
        for ax, data, base_title, ylabel_val, lc in profile_specs:
            if flat_data:
                # ── Time-series mode (flat / CSV data) ──────────────────────
                title  = base_title + ' vs Packet #'
                xlabel = 'Packet #'
                ylabel = ylabel_val
                if data:
                    xs = list(range(len(data)))
                    ax.plot(xs, data, color=lc, linewidth=1.5, zorder=5)
                ax.set_title(title,  fontsize=9, fontweight='bold', color=fg_color)
                ax.set_xlabel(xlabel, fontsize=7, color=fg_color)
                ax.set_ylabel(ylabel, fontsize=7, color=fg_color)
                ax.grid(True, alpha=0.25)
            else:
                # ── Altitude profile mode (sounding / balloon data) ──────────
                title  = base_title + ' vs Altitude'
                xlabel = ylabel_val
                self._add_layers(ax, max_alt, results)
                if data:
                    ax.plot(data, self.altitude_data[:len(data)], color=lc, linewidth=2, zorder=5)
                ax.set_title(title,   fontsize=9, fontweight='bold', color=fg_color)
                ax.set_xlabel(xlabel, fontsize=7, color=fg_color)
                ax.set_ylabel('Altitude (m)', fontsize=7, color=fg_color)
                ax.set_ylim(0, max_alt * 1.05)
                ax.grid(True, alpha=0.25)

        # Laser mixing ratio
        self.ax_laser_mr.set_title('Laser — Mixing Ratio  (CH0=232)',
                                   fontsize=8, fontweight='bold', color=fg_color)
        self.ax_laser_mr.set_xlabel('Packet #',     fontsize=7, color=fg_color)
        self.ax_laser_mr.set_ylabel('Mixing Ratio', fontsize=7, color=fg_color)
        if self.laser_mr_data:
            xs = list(range(len(self.laser_mr_data)))
            self.ax_laser_mr.plot(xs, self.laser_mr_data, color='#FF6B35', linewidth=1.5)
            self.ax_laser_mr.grid(True, alpha=0.25)

        # Laser bkg & peak
        self.ax_laser_bp.set_title('Laser — Background & Peak  (CH0=232)',
                                   fontsize=8, fontweight='bold', color=fg_color)
        self.ax_laser_bp.set_xlabel('Packet #', fontsize=7, color=fg_color)
        self.ax_laser_bp.set_ylabel('Signal',   fontsize=7, color=fg_color)
        if self.laser_bkg_data:
            xs = list(range(len(self.laser_bkg_data)))
            self.ax_laser_bp.plot(xs, self.laser_bkg_data,  color='#1E90FF', linewidth=1.5, label='Bkg')
            self.ax_laser_bp.plot(xs, self.laser_peak_data, color='#FFD700', linewidth=1.5, label='Peak')
            self.ax_laser_bp.legend(fontsize=6, loc='upper left', framealpha=0.6)
            self.ax_laser_bp.grid(True, alpha=0.25)

        # Status panel
        self.ax_status.axis('off')
        self.ax_status.set_title('Payload Status  (CH0=232 RX | CH1=233 TX)',
                                 fontsize=8, fontweight='bold', color=fg_color)
        lines = []
        if self.packet_numbers:
            lines.append(f"Pkt #   : {self.packet_numbers[-1]}")
            lines.append(f"Total RX: {self.total_rx}")
            lines.append(f"Failed  : {self.failed_rx}")
            lines.append("")
        if self.fan_speed_pwm_rx:
            pwm_rx = self.fan_speed_pwm_rx[-1]
            lines.append(f"Fan PWM (RX CH0): {pwm_rx}  ({pwm_rx/255*100:.1f}%)")
        lines.append(f"Fan Cmd (TX CH1): {self._fan_pwm()} PWM  ({'ON' if self.fan_enabled else 'OFF'})")
        lines.append("")
        if self.temp_data:
            lines.append(f"Temp      : {self.temp_data[-1]:.2f} °C")
        if self.humidity_data:
            lines.append(f"Humidity  : {self.humidity_data[-1]:.1f} %")
        if self.laser_mr_data:
            lines.append("")
            lines.append(f"Laser MR  : {self.laser_mr_data[-1]:.4f}")
        if self.laser_bkg_data:
            lines.append(f"Laser Bkg : {self.laser_bkg_data[-1]:.4f}")
        if self.laser_peak_data:
            lines.append(f"Laser Peak: {self.laser_peak_data[-1]:.4f}")

        # Serial mode hint
        if self.serial_up:
            mode = self.serial_rx._mode
            lines.append("")
            lines.append(f"Serial mode: {mode}")

        if not lines:
            lines = ["Waiting for payload data…", "", "Connect WiFi or Serial",
                     "(USB cable → text mode  auto-detected)",
                     "(DB9 adapter → binary mode auto-detected)"]

        self.ax_status.text(0.05, 0.95, "\n".join(lines),
                            ha='left', va='top', fontsize=8.5,
                            transform=self.ax_status.transAxes,
                            color=fg_color, family='monospace')

        # UTC & Mission clock panel
        self._draw_clock_panel(self.ax_clocks, fg_color, graph_bg)

        for ax in self.axes:
            ax.set_facecolor(graph_bg)
            for sp in ax.spines.values(): sp.set_color(fg_color)
            ax.tick_params(colors=fg_color)
            ax.xaxis.label.set_color(fg_color)
            ax.yaxis.label.set_color(fg_color)
            ax.title.set_color(fg_color)

        self.fig.set_facecolor(graph_bg)
        self.fig.tight_layout(pad=1.0)
        self.canvas.draw()

    # ── UTC & Mission Clock panel ────────────────────────────────────────────
    def _draw_clock_panel(self, ax, fg_color, bg_color):
        """
        Render the UTC clock and mission elapsed time into a matplotlib axes panel.
        Called every second from _update_clock and on every plot refresh.
        """
        ax.clear()
        ax.set_xlim(0, 1)
        ax.set_ylim(0, 1)
        ax.axis('off')
        ax.set_facecolor(bg_color)
        ax.set_title('UTC & Mission Clock', fontsize=9, fontweight='bold', color=fg_color)

        utc_str = datetime.utcnow().strftime("%H:%M:%S")
        if self.mission_running and self.mission_start_time:
            el = datetime.now() - self.mission_start_time
            h, r = divmod(int(el.total_seconds()), 3600)
            m, s = divmod(r, 60)
            mission_str = f"{h:02d}:{m:02d}:{s:02d}"
        else:
            mission_str = "00:00:00"

        date_str = datetime.utcnow().strftime("%Y-%m-%d")

        accent   = '#00FFFF' if self.dark_mode else '#0000CD'
        orange   = '#FF6B35'
        dim      = '#888888' if self.dark_mode else '#666666'
        box_bg   = '#0D0D0D' if self.dark_mode else '#E8E8E8'
        box_edge = '#333333' if self.dark_mode else '#AAAAAA'

        # ── UTC block ────────────────────────────────────────────────
        ax.text(0.5, 0.90, "TIME  (UTC)", ha='center', va='top',
                fontsize=8, fontweight='bold', color=dim,
                transform=ax.transAxes, family='monospace')
        ax.text(0.5, 0.72, utc_str, ha='center', va='top',
                fontsize=22, fontweight='bold', color=accent,
                transform=ax.transAxes, family='monospace',
                bbox=dict(boxstyle='round,pad=0.3', facecolor=box_bg,
                          edgecolor=box_edge, linewidth=1.5))
        ax.text(0.5, 0.52, date_str, ha='center', va='top',
                fontsize=9, color=dim,
                transform=ax.transAxes, family='monospace')

        # ── Divider ──────────────────────────────────────────────────
        ax.axhline(0.44, xmin=0.08, xmax=0.92, color=box_edge, linewidth=0.8)

        # ── Mission elapsed block ────────────────────────────────────
        ax.text(0.5, 0.40, "MISSION  ELAPSED", ha='center', va='top',
                fontsize=8, fontweight='bold', color=dim,
                transform=ax.transAxes, family='monospace')
        mission_color = orange if self.mission_running else dim
        ax.text(0.5, 0.22, mission_str, ha='center', va='top',
                fontsize=22, fontweight='bold', color=mission_color,
                transform=ax.transAxes, family='monospace',
                bbox=dict(boxstyle='round,pad=0.3', facecolor=box_bg,
                          edgecolor=box_edge, linewidth=1.5))

        # ── Status dot ───────────────────────────────────────────────
        status_txt  = "● MISSION ACTIVE" if self.mission_running else "○ STANDBY"
        status_col  = '#00FF41' if self.mission_running else dim
        ax.text(0.5, 0.04, status_txt, ha='center', va='bottom',
                fontsize=8, fontweight='bold', color=status_col,
                transform=ax.transAxes, family='monospace')

    # ── WiFi ─────────────────────────────────────────────────────────────────
    def _connect_wifi(self):
        if self.wifi_server_up:
            self._msg("Wi-Fi server already running — waiting for payload to connect")
            return

        dlg = tk.Toplevel(self.root)
        dlg.title("Wi-Fi Connection Settings")
        dlg.configure(bg='#1A1A1A')
        dlg.resizable(False, False)
        dlg.grab_set()

        detected_ip = WiFiReceiver.detect_local_ip(WiFiReceiver.ROUTER_IP) or '(not detected)'

        tk.Label(dlg, text="SWIFT STATION — Connection Settings",
                 font=('Segoe UI', 11, 'bold'), bg='#1A1A1A', fg='#00FFFF').grid(
                 row=0, column=0, columnspan=2, padx=16, pady=(12, 6))

        for i, line in enumerate([
            f"Detected LAN IP : {detected_ip}",
            "Leave Bind IP blank or 'auto' to auto-detect.",
            "RX channel: PAYLOAD_DATA_CHANNEL_0 = 232",
            "TX channel: PAYLOAD_DATA_CHANNEL_1 = 233  (fan cmd)",
        ]):
            tk.Label(dlg, text=line, font=('Consolas', 9),
                     bg='#1A1A1A', fg='#AAAAAA').grid(row=1+i, column=0, columnspan=2, padx=16, sticky='w')

        tk.Label(dlg, text="Bind IP:", font=('Segoe UI', 10),
                 bg='#1A1A1A', fg='white').grid(row=5, column=0, padx=16, pady=(10,4), sticky='e')
        auto_ip = detected_ip if detected_ip != '(not detected)' else 'auto'
        ip_var  = tk.StringVar(value=auto_ip)
        ip_entry = tk.Entry(dlg, textvariable=ip_var, width=18,
                            font=('Consolas', 10), bg='#2D2D2D', fg='white',
                            insertbackground='white')
        ip_entry.grid(row=5, column=1, padx=16, pady=(10,4), sticky='w')

        tk.Label(dlg, text="Port:", font=('Segoe UI', 10),
                 bg='#1A1A1A', fg='white').grid(row=6, column=0, padx=16, pady=(4,4), sticky='e')
        port_var   = tk.StringVar(value=str(self.wifi.port))
        port_entry = tk.Entry(dlg, textvariable=port_var, width=10,
                              font=('Consolas', 10), bg='#2D2D2D', fg='white',
                              insertbackground='white')
        port_entry.grid(row=6, column=1, padx=16, pady=(4,4), sticky='w')

        status_lbl = tk.Label(dlg, text="", font=('Consolas', 9),
                              bg='#1A1A1A', fg='#FF8800', wraplength=340, justify='left')
        status_lbl.grid(row=7, column=0, columnspan=2, padx=16, pady=4)

        def _do_connect():
            try:
                self.wifi.port = int(port_var.get())
            except ValueError:
                status_lbl.config(text="Invalid port number.", fg='#FF4500')
                return
            raw_ip = ip_var.get().strip()
            self.wifi.host = 'auto' if (not raw_ip or raw_ip.lower() == 'auto') else raw_ip
            status_lbl.config(text="Trying to bind…", fg='#FFD700')
            dlg.update_idletasks()
            ok, msg = self.wifi.start_server()
            if ok:
                self.wifi_server_up = True
                dlg.destroy()
                self._msg(msg)
                self._update_conn_status()
                self.wifi.running = True
                self.wifi_thread  = Thread(target=self._wifi_loop, daemon=True)
                self.wifi_thread.start()
            else:
                status_lbl.config(text=msg, fg='#FF4500')

        btn_f = tk.Frame(dlg, bg='#1A1A1A')
        btn_f.grid(row=8, column=0, columnspan=2, pady=12)
        tk.Button(btn_f, text="  CONNECT  ", command=_do_connect,
                  font=('Courier New', 10, 'bold'),
                  bg='#1E90FF', fg='white', activebackground='#0066CC',
                  relief=tk.RAISED, bd=2).pack(side=tk.LEFT, padx=6)
        tk.Button(btn_f, text="  CANCEL  ", command=dlg.destroy,
                  font=('Courier New', 10, 'bold'),
                  bg='#444444', fg='white', relief=tk.RAISED, bd=2).pack(side=tk.LEFT, padx=6)

        dlg.update_idletasks()
        x = self.root.winfo_x() + (self.root.winfo_width()  - dlg.winfo_width())  // 2
        y = self.root.winfo_y() + (self.root.winfo_height() - dlg.winfo_height()) // 2
        dlg.geometry(f"+{x}+{y}")

    def _wifi_loop(self):
        while self.wifi.running:
            conn, msg = self.wifi.accept_connection()
            if not conn:
                continue

            self.wifi_client_up = True
            self._msg(msg)
            print(f"\n[WiFi] ✅ {msg}")
            self.root.after(0, self._update_conn_status)

            try:
                while self.wifi.running:
                    try:
                        got_data = self.wifi.recv_into_buffer()
                    except ConnectionError as ce:
                        print(f"[WiFi] 🔌 {ce}")
                        break

                    if not got_data:
                        import time; time.sleep(0.02)
                        continue

                    for raw_bytes, parsed, status in self.wifi.drain_packets():
                        # ── RAW PACKET PRINT ──────────────────────────
                        _pnum = parsed.get("packet_number", "?") if parsed else "?"
                        print("[WiFi RAW] pkt#{} {}B | {}".format(
                            _pnum, len(raw_bytes),
                            " ".join("{:02X}".format(b) for b in raw_bytes)))
                        if parsed is not None:
                            self.data_queue.put((raw_bytes, parsed, status))
                            self.total_rx += 1
                        else:
                            self.data_queue.put((raw_bytes, None, status))
                            self.failed_rx += 1

            except Exception as e:
                print(f"[WiFi] ❌ Receive loop error: {e}")

            self.wifi.client_connected = False
            self.wifi_client_up = False
            self.root.after(0, self._update_conn_status)
            self._msg("Payload disconnected — waiting for reconnect…")
            print("[WiFi] Client disconnected — back to accept()")

    # ── Serial ───────────────────────────────────────────────────────────────
    def _connect_serial(self):
        if self.serial_up:
            self._msg("Serial already connected")
            return

        dlg = tk.Toplevel(self.root)
        dlg.title("Serial Connection Settings")
        dlg.configure(bg='#1A1A1A')
        dlg.resizable(False, False)
        dlg.grab_set()

        tk.Label(dlg, text="Serial — Connect to Payload Arduino",
                 font=('Segoe UI', 11, 'bold'), bg='#1A1A1A', fg='#9932CC').grid(
                 row=0, column=0, columnspan=2, padx=16, pady=(12, 6))

        for i, line in enumerate([
            "RX: PAYLOAD_DATA_CHANNEL_0 = 232  (telemetry)",
            "TX: PAYLOAD_DATA_CHANNEL_1 = 233  (fan cmd)",
            "Arduino baud: 9600 (matches setup() serial_baud)",
            "",
            "★ AUTO-DETECTS:  USB cable (text mode)  OR  DB9 adapter (binary mode)",
            "  USB/text:   Serial.print('packetNum=..., packet=[HH,HH,...]')",
            "  DB9/binary: serialDB9.write(packet, 42)  raw bytes",
        ]):
            fg = '#00FF88' if '★' in line else ('#888888' if not line else '#AAAAAA')
            tk.Label(dlg, text=line, font=('Consolas', 9),
                     bg='#1A1A1A', fg=fg).grid(row=1+i, column=0, columnspan=2, padx=16, sticky='w')

        ports = SerialReceiver.list_ports()
        tk.Label(dlg, text="COM Port:", font=('Segoe UI', 10),
                 bg='#1A1A1A', fg='white').grid(row=9, column=0, padx=16, pady=(10,4), sticky='e')
        port_var   = tk.StringVar(value=ports[0] if ports else "")
        port_combo = ttk.Combobox(dlg, textvariable=port_var, values=ports, width=14,
                                  font=('Consolas', 10))
        port_combo.grid(row=9, column=1, padx=16, pady=(10,4), sticky='w')

        tk.Label(dlg, text="Baud Rate:", font=('Segoe UI', 10),
                 bg='#1A1A1A', fg='white').grid(row=10, column=0, padx=16, pady=(4,4), sticky='e')
        baud_var   = tk.StringVar(value="9600")
        baud_combo = ttk.Combobox(dlg, textvariable=baud_var,
                                  values=["9600","19200","38400","57600","115200"], width=14,
                                  font=('Consolas', 10))
        baud_combo.grid(row=10, column=1, padx=16, pady=(4,4), sticky='w')

        def _refresh():
            ps = SerialReceiver.list_ports()
            port_combo['values'] = ps
            if ps: port_var.set(ps[0])
        tk.Button(dlg, text="↻ Refresh Ports", command=_refresh,
                  font=('Consolas', 8), bg='#333333', fg='white',
                  relief=tk.RAISED, bd=1).grid(row=11, column=0, columnspan=2, pady=4)

        status_lbl = tk.Label(dlg, text="", font=('Consolas', 9),
                              bg='#1A1A1A', fg='#FF8800', wraplength=380, justify='left')
        status_lbl.grid(row=12, column=0, columnspan=2, padx=16, pady=4)

        def _do_connect():
            port = port_var.get().strip()
            try: baud = int(baud_var.get())
            except ValueError: baud = 9600
            if not port:
                status_lbl.config(text="Please select a COM port.", fg='#FF4500')
                return
            status_lbl.config(text=f"Connecting to {port}…", fg='#FFD700')
            dlg.update_idletasks()
            ok, msg = self.serial_rx.connect(port, baud)
            if ok:
                self.serial_up           = True
                self.serial_rx.running   = True
                dlg.destroy()
                self._msg(msg + "  [auto-detecting text vs binary mode…]")
                self._update_conn_status()
                self.serial_thread = Thread(target=self._serial_loop, daemon=True)
                self.serial_thread.start()
            else:
                status_lbl.config(text=msg, fg='#FF4500')

        btn_f = tk.Frame(dlg, bg='#1A1A1A')
        btn_f.grid(row=13, column=0, columnspan=2, pady=12)
        tk.Button(btn_f, text="  CONNECT  ", command=_do_connect,
                  font=('Courier New', 10, 'bold'),
                  bg='#9932CC', fg='white', relief=tk.RAISED, bd=2).pack(side=tk.LEFT, padx=6)
        tk.Button(btn_f, text="  CANCEL  ", command=dlg.destroy,
                  font=('Courier New', 10, 'bold'),
                  bg='#444444', fg='white', relief=tk.RAISED, bd=2).pack(side=tk.LEFT, padx=6)

        dlg.update_idletasks()
        x = self.root.winfo_x() + (self.root.winfo_width()  - dlg.winfo_width())  // 2
        y = self.root.winfo_y() + (self.root.winfo_height() - dlg.winfo_height()) // 2
        dlg.geometry(f"+{x}+{y}")

    def _serial_loop(self):
        """
        [FIX 1 + 4] Dedicated serial receive thread.
        Polls for incoming bytes every 20 ms.
        For BINARY mode: feeds BinaryPacketBuffer → drain_packets()
        For TEXT mode:   feeds TextModePacketParser → packets returned from feed_bytes()
        Both paths produce (raw_bytes, parsed, status) tuples → data_queue.
        """
        print(f"[Serial] ▶ Thread started on {self.serial_rx.port} @ {self.serial_rx.baudrate}  [mode: auto-detect]")
        import time

        while self.serial_rx.running and self.serial_rx.connected:
            try:
                # TEXT mode: need to intercept packets returned by feed_bytes
                if self.serial_rx._mode == SerialReceiver.MODE_TEXT:
                    # Read raw bytes and parse text directly
                    if not self.serial_rx.serial_conn or not self.serial_rx.connected:
                        break
                    try:
                        waiting = self.serial_rx.serial_conn.in_waiting
                    except Exception as e:
                        print(f"[Serial] Read error: {e}")
                        self.serial_rx.connected = False
                        break

                    if waiting > 0:
                        chunk = self.serial_rx.serial_conn.read(waiting)
                        if chunk:
                            # Print hex dump of raw incoming bytes
                            hex_dump(f"RECV/Serial/RAW", chunk, "raw bytes before text decode")
                            # Parse via text mode parser
                            results = self.serial_rx._txt_parser.feed_bytes(chunk)
                            for raw_bytes, parsed, status in results:
                                # ── RAW PACKET PRINT ──────────────────
                                _pnum = parsed.get("packet_number", "?") if parsed else "?"
                                print("[Serial TEXT RAW] pkt#{} {}B | {}".format(
                                    _pnum, len(raw_bytes),
                                    " ".join("{:02X}".format(b) for b in raw_bytes)))
                                if parsed is not None:
                                    self.data_queue.put((raw_bytes, parsed, status))
                                    self.total_rx += 1
                                    # Update mode label on UI thread
                                    self.root.after(0, lambda: self.serial_mode_label.config(
                                        text="Serial: TEXT mode (USB)",
                                        fg='#00FF88'))
                                else:
                                    self.data_queue.put((raw_bytes, None, status))
                                    self.failed_rx += 1
                    else:
                        time.sleep(0.02)

                elif self.serial_rx._mode == SerialReceiver.MODE_BINARY:
                    got_data = self.serial_rx.recv_into_buffer()
                    if got_data:
                        for raw_bytes, parsed, status in self.serial_rx.drain_packets():
                            # ── RAW PACKET PRINT ──────────────────────
                            _pnum = parsed.get("packet_number", "?") if parsed else "?"
                            print("[Serial BIN RAW] pkt#{} {}B | {}".format(
                                _pnum, len(raw_bytes),
                                " ".join("{:02X}".format(b) for b in raw_bytes)))
                            if parsed is not None:
                                self.data_queue.put((raw_bytes, parsed, status))
                                self.total_rx += 1
                                self.root.after(0, lambda: self.serial_mode_label.config(
                                    text="Serial: BINARY mode (DB9)",
                                    fg='#00FFFF'))
                            else:
                                self.data_queue.put((raw_bytes, None, status))
                                self.failed_rx += 1
                    else:
                        time.sleep(0.02)

                else:  # MODE_UNKNOWN — still sniffing
                    got_data = self.serial_rx.recv_into_buffer()
                    if not got_data:
                        time.sleep(0.02)
                    else:
                        # Mode might have been set now; update label
                        mode = self.serial_rx._mode
                        if mode != SerialReceiver.MODE_UNKNOWN:
                            label_text = (f"Serial: detecting… ({mode})")
                            self.root.after(0, lambda t=label_text: self.serial_mode_label.config(
                                text=t, fg='#FFD700'))
                        # [FIX 9] Drain any text packets captured during detection flush
                        pending = self.serial_rx._pending_text_packets
                        if pending:
                            self.serial_rx._pending_text_packets = []
                            for raw_bytes, parsed, status in pending:
                                if parsed is not None:
                                    self.data_queue.put((raw_bytes, parsed, status))
                                    self.total_rx += 1
                                else:
                                    self.data_queue.put((raw_bytes, None, status))
                                    self.failed_rx += 1

            except Exception as e:
                print(f"[Serial] ❌ Loop error: {e}")
                break

        self.serial_up = False
        self.root.after(0, self._update_conn_status)
        self.root.after(0, lambda: self.serial_mode_label.config(text="Serial: —", fg='#888888'))
        print("[Serial] ■ Receive thread stopped")

    # ── Post-flight / backup packet file loader ─────────────────────────────
    def _set_fan_widgets_state(self, state: str):
        """
        Enable or disable all interactive fan-control widgets.
        state = 'normal'   → fully interactive (live mission)
        state = 'disabled' → greyed out (file-load / post-flight replay mode)
        """
        note_txt = "[ N/A — file mode ]" if state == "disabled" else ""
        try:
            self.fan_filemode_label.config(text=note_txt)
            self.fan_toggle_btn.config(state=state)
            self.fan_slider.config(state=state)
            for child in self.fan_frame.winfo_children():
                if isinstance(child, (type(self.fan_toggle_btn),)):
                    child.config(state=state)
                # Disable preset buttons inside nested frames
                if hasattr(child, 'winfo_children'):
                    for sub in child.winfo_children():
                        try: sub.config(state=state)
                        except: pass
        except Exception:
            pass

    def _load_packets_from_file(self):
        """
        Open a file-chooser dialog, then parse every packet in the chosen file
        and feed them through the same data_queue → _update_data → _update_plots
        pipeline as live telemetry.

        Supported file formats
        ──────────────────────
        Binary  (.bin / .dat / no extension):
          Raw byte stream — same framing as DB9/WiFi reception.
          Parsed by BinaryPacketBuffer (header 0x55 0x24).

        Text    (.txt / .log):
          Arduino USB debug lines containing  packet=[HH,HH,…]
          Parsed by TextModePacketParser.

        The fan control panel is muted (disabled, but stays visible) while
        file-load mode is active, because there is no live uplink to send to.
        """
        from tkinter import filedialog

        # If a previous file-load is still running, abort it first
        if self.file_load_mode:
            self.file_load_mode = False
            self._msg("Previous file-load cancelled — select a new file")
            self._set_fan_widgets_state("normal")
            self.file_load_label.config(text="File mode: inactive", fg="#666633")
            return

        filepath = filedialog.askopenfilename(
            title="Select Packet File (binary or text)",
            filetypes=[
                ("All packet files", "*.bin *.dat *.txt *.log *"),
                ("Binary packet files", "*.bin *.dat"),
                ("Text / log files",    "*.txt *.log"),
                ("All files",           "*.*"),
            ]
        )
        if not filepath:
            return   # user cancelled

        # ── Auto-detect file format ──────────────────────────────────────────
        # Three modes:
        #   'csv'    — comma-separated decimal values (your Payload_Data.txt format)
        #   'text'   — Arduino USB debug lines:  packet=[HH,HH,…]
        #   'binary' — raw 0x55 0x24 framed packets (DB9 / WiFi capture)
        #
        # Detection order:
        #   1. Try to read as UTF-8 text.
        #   2. If first non-blank line looks like CSV numbers → csv mode.
        #   3. If a line contains  packet=[  → text mode.
        #   4. Otherwise → binary mode.
        ext = os.path.splitext(filepath)[1].lower()

        try:
            with open(filepath, 'rb') as fh:
                raw_file_bytes = fh.read()
        except Exception as e:
            messagebox.showerror("File Load Error",
                                 "Could not read file:\n" + filepath + "\n\n" + str(e))
            return

        if len(raw_file_bytes) == 0:
            messagebox.showwarning("Empty File", "The selected file contains no data.")
            return

        # Sniff format
        file_mode = 'binary'
        try:
            text_content = raw_file_bytes.decode('utf-8', errors='replace')
            first_lines = [l.strip() for l in text_content.splitlines() if l.strip()][:5]
            # CSV check: first token is an integer and second is a large ms timestamp
            if first_lines:
                parts = first_lines[0].split(',')
                if (len(parts) >= 8
                        and parts[0].strip().lstrip('-').isdigit()
                        and 'packet=[' not in first_lines[0]):
                    file_mode = 'csv'
                elif any('packet=[' in l for l in first_lines):
                    file_mode = 'text'
        except Exception:
            file_mode = 'binary'

        # Activate file-load mode
        self.file_load_mode = True
        self.mission_running = True
        if self.mission_start_time is None:
            self.mission_start_time = datetime.now()

        # Mute fan controls — no live uplink during post-flight replay
        self._set_fan_widgets_state("disabled")

        short_name = os.path.basename(filepath)
        mode_str   = file_mode.upper()
        self.file_load_label.config(
            text="File mode: ACTIVE\n" + short_name + "\n(" + mode_str + " parse)",
            fg="#EEEE55")
        self._msg("Loading packets from file (" + mode_str + "): " + short_name)
        print("\n[FILE LOAD] ▶ " + filepath + "  (" + str(len(raw_file_bytes)) + " bytes, mode=" + mode_str + ")")

        # Run parse + replay in a background thread so the UI stays responsive
        self.file_load_thread = Thread(
            target=self._file_load_loop,
            args=(raw_file_bytes, file_mode, short_name),
            daemon=True)
        self.file_load_thread.start()

    def _file_load_loop(self, raw_file_bytes: bytes, file_mode: str, label: str):
        """
        Background thread: parse the file instantly then apply all data in one
        UI-thread callback so plots update immediately.

        BUGS FIXED
        ──────────
        BUG 1 — "no plots": old code pushed to data_queue and hoped _update_data
          would catch it, but the 500ms plot-gate and missing mission_running flag
          meant _update_plots was never called with complete data.
          Fix: bypass data_queue — populate data lists directly on the UI thread
          via root.after(0, _apply_all), then call _update_plots once.

        BUG 2 — "too slow": time.sleep(0.02) × N packets + full hex_dump of
          the entire file to stdout.
          Fix: no sleep, binary feed uses silent=True.

        BUG 3 (NEW) — "no packets found": file was CSV decimal format, not
          Arduino hex/binary. TextModePacketParser looks for  packet=[HH,…]
          and found nothing.
          Fix: file_mode='csv' branch uses CsvPacketParser which understands
          the comma-separated decimal column layout.
        """
        total_bytes = len(raw_file_bytes)
        ok_count    = 0
        fail_count  = 0

        self.root.after(0, self._msg, "Parsing packet file (" + file_mode.upper() + ")…")

        # ── Parse according to detected format ───────────────────────────────
        if file_mode == 'csv':
            text = raw_file_bytes.decode('utf-8', errors='replace')
            results = CsvPacketParser.parse_file(text)

        elif file_mode == 'text':
            parser = TextModePacketParser("FILE/TEXT")
            results = parser.feed_bytes(raw_file_bytes)

        else:  # binary
            buf = BinaryPacketBuffer("FILE/BIN")
            buf.feed(raw_file_bytes, silent=True)   # suppress hex_dump storm
            results = buf.drain()

        total_pkts = len(results)
        print("[FILE LOAD] Parsed " + str(total_pkts) + " packets from " + str(total_bytes) + " bytes  (mode=" + file_mode + ")")

        if total_pkts == 0:
            self.file_load_mode = False
            def _none():
                self._msg("File load: no valid packets found — check file format")
                self._set_fan_widgets_state("normal")
                self.file_load_label.config(
                    text="File mode: done\n" + label + "\n0 packets found",
                    fg="#FF8800")
            self.root.after(0, _none)
            return

        # ── Separate good and failed results ─────────────────────────────────
        good_items = []
        for raw_bytes, parsed, status in results:
            if parsed is not None:
                good_items.append((raw_bytes, parsed, status))
                ok_count += 1
            else:
                fail_count += 1

        self.file_load_mode = False
        finish_msg = ("File loaded: " + str(ok_count) + " packets OK, "
                      + str(fail_count) + " failed  (" + label + ")")
        print("[FILE LOAD] ■ " + finish_msg)

        # ── Apply everything on the UI thread in one shot ─────────────────────
        def _apply_all():
            for raw_bytes, d, status in good_items:
                self._monitor_log(raw_bytes, d, status)
                if 'packet_number'      in d: self.packet_numbers.append(d['packet_number'])
                if 'fan_speed_pwm'      in d: self.fan_speed_pwm_rx.append(d['fan_speed_pwm'])
                if 'laser_mixing_ratio' in d: self.laser_mr_data.append(d['laser_mixing_ratio'])
                if 'laser_background'   in d: self.laser_bkg_data.append(d['laser_background'])
                if 'laser_peak'         in d: self.laser_peak_data.append(d['laser_peak'])
                if 'laser_data'         in d: self.laser_data_history.append(d['laser_data'])
                self.altitude_data.append(       d.get('altitude',        0.0))
                self.temp_data.append(           d.get('temperature',     0.0))
                self.pressure_data.append(       d.get('pressure',     1013.25))
                self.humidity_data.append(       d.get('humidity',        0.0))
                self.wind_speed_data.append(     d.get('wind_speed',      0.0))
                self.wind_direction_data.append( d.get('wind_direction',  0.0))
                self.isotope_data.append(        d.get('isotope',         -8.0))

            self.total_rx  += ok_count
            self.failed_rx += fail_count
            self.pkt_stat_label.config(text="RX: " + str(self.total_rx) + "\nFail: " + str(self.failed_rx))
            self._msg(finish_msg)
            self._set_fan_widgets_state("normal")
            self.file_load_label.config(
                text="File mode: done\n" + label + "\n" + str(ok_count) + " OK / " + str(fail_count) + " fail",
                fg="#88CC44")
            if ok_count > 0:
                self._update_plots()

        self.root.after(0, _apply_all)

    def _update_conn_status(self):
        parts = []
        if self.wifi_server_up:
            parts.append(f"WiFi: ✅{'(payload✅)' if self.wifi_client_up else '(wait…)'}")
        else:
            parts.append("WiFi: ❌")
        parts.append(f"Serial: {'✅ ' + self.serial_rx.port if self.serial_up else '❌'}")
        fg = '#00FF00' if (self.wifi_client_up or self.serial_up) else \
             ('#FFD700' if self.wifi_server_up else '#FF4500')
        self.conn_label.config(text="  ".join(parts), fg=fg)

    # ── Mission ──────────────────────────────────────────────────────────────
    def _start_mission(self):
        if self.mission_running:
            return
        self.mission_running    = True
        self.mission_start_time = datetime.now()

        if self.wifi_server_up or self.serial_up:
            self._msg("Mission started — receiving live telemetry (CH0=232)")
        else:
            self._msg("Mission started — no connection, streaming synthetic test data")
            self._generate_test_data()
            self.streaming_test = True
            self._stream_test_data()

    def _end_mission(self):
        if not self.mission_running:
            return
        self.mission_running = False
        self.streaming_test  = False
        self._msg("Mission ended")
        self._save_data()

    # ── Data update loop ─────────────────────────────────────────────────────
    def _update_data(self):
        new_data = False
        while not self.data_queue.empty():
            try:
                item = self.data_queue.get_nowait()
            except queue.Empty:
                break

            # [FIX 6] Normalise to tuple; test data puts plain dicts
            if isinstance(item, tuple):
                raw_bytes, d, status = item
            else:
                raw_bytes, d, status = b'', item, 'OK'

            self._monitor_log(raw_bytes, d, status)

            if d is None:
                continue

            new_data = True
            if 'packet_number'      in d: self.packet_numbers.append(d['packet_number'])
            if 'fan_speed_pwm'      in d: self.fan_speed_pwm_rx.append(d['fan_speed_pwm'])
            if 'laser_mixing_ratio' in d: self.laser_mr_data.append(d['laser_mixing_ratio'])
            if 'laser_background'   in d: self.laser_bkg_data.append(d['laser_background'])
            if 'laser_peak'         in d: self.laser_peak_data.append(d['laser_peak'])
            if 'laser_data'         in d: self.laser_data_history.append(d['laser_data'])
            self.altitude_data.append(       d.get('altitude',        0.0))
            self.temp_data.append(           d.get('temperature',     0.0))
            self.pressure_data.append(       d.get('pressure',     1013.25))
            self.humidity_data.append(       d.get('humidity',        0.0))
            self.wind_speed_data.append(     d.get('wind_speed',      0.0))
            self.wind_direction_data.append( d.get('wind_direction',  0.0))
            self.isotope_data.append(        d.get('isotope',         -8.0))

        self.pkt_stat_label.config(text=f"RX: {self.total_rx}\nFail: {self.failed_rx}")

        if new_data:
            if not hasattr(self, '_last_plot_ms'):
                self._last_plot_ms = 0
            now_ms = datetime.now().timestamp() * 1000
            if (now_ms - self._last_plot_ms) >= 500:
                self._last_plot_ms = now_ms
                self._update_plots()

        self.root.after(100, self._update_data)

    # ── Clock ────────────────────────────────────────────────────────────────
    def _update_clock(self):
        self.time_label.config(text=datetime.utcnow().strftime("%H:%M:%S"))
        if self.mission_running and self.mission_start_time:
            el = datetime.now() - self.mission_start_time
            h, r = divmod(int(el.total_seconds()), 3600)
            m, s = divmod(r, 60)
            self.mission_time_label.config(text=f"{h:02d}:{m:02d}:{s:02d}")
        # Also refresh the in-figure clock panel every second
        try:
            fg_color = 'white' if self.dark_mode else 'black'
            bg_color = '#2D2D2D' if self.dark_mode else '#F5F5DC'
            self._draw_clock_panel(self.ax_clocks, fg_color, bg_color)
            self.canvas.draw_idle()
        except Exception:
            pass
        self.root.after(1000, self._update_clock)

    # ── Theme ─────────────────────────────────────────────────────────────────
    def _draw_gradient(self):
        self.bg_canvas.delete("gradient")
        W = self.root.winfo_width()  or 1400
        H = self.root.winfo_height() or 900
        if self.dark_mode:
            for i in range(H):
                v = int(10 + 20 * (i / H))
                self.bg_canvas.create_line(0, i, W, i, fill=f'#{v:02x}{v:02x}{v:02x}', tags="gradient")
        else:
            for i in range(H):
                r = int(232 - 20*(i/H)); g = int(220 - 24*(i/H)); b = int(200 - 24*(i/H))
                self.bg_canvas.create_line(0, i, W, i, fill=f'#{r:02x}{g:02x}{b:02x}', tags="gradient")

    def _toggle_mode(self):
        self.dark_mode = not self.dark_mode
        if self.dark_mode:
            self.mode_btn.config(text="🌙", bg='#404040', fg='white')
            self.title_label.config(bg='#1E1E1E', fg='#00FFFF')
        else:
            self.mode_btn.config(text="☀", bg='#FFEB3B', fg='black')
            self.title_label.config(bg='#D4C4B0', fg='#000080')
        self._draw_gradient()
        if self.altitude_data:
            self._update_plots()
        else:
            fg = 'white' if self.dark_mode else 'black'
            bg = '#2D2D2D' if self.dark_mode else '#F5F5DC'
            self.fig.set_facecolor(bg)
            for ax in self.axes:
                ax.set_facecolor(bg)
                for sp in ax.spines.values(): sp.set_color(fg)
                ax.tick_params(colors=fg)
                ax.xaxis.label.set_color(fg)
                ax.yaxis.label.set_color(fg)
                ax.title.set_color(fg)
            self.canvas.draw()

    # ── Save ──────────────────────────────────────────────────────────────────
    def _save_data(self):
        ts     = datetime.now().strftime("%Y%m%d_%H%M%S")
        folder = f"flair_mission_{ts}"
        os.makedirs(folder, exist_ok=True)
        try:
            csvs = [
                ('temperature',    self.temp_data,           'Temperature (°C)'),
                ('humidity',       self.humidity_data,        'Humidity (%)'),
                ('pressure',       self.pressure_data,        'Pressure (hPa)'),
                ('wind_speed',     self.wind_speed_data,      'Wind Speed (m/s)'),
                ('wind_direction', self.wind_direction_data,  'Wind Direction (°)'),
                ('isotope',        self.isotope_data,         'Isotope (‰)'),
                ('laser_mr',       self.laser_mr_data,        'Mixing Ratio'),
                ('laser_bkg',      self.laser_bkg_data,       'Background'),
                ('laser_peak',     self.laser_peak_data,      'Peak'),
                ('fan_speed_rx',   self.fan_speed_pwm_rx,     'Fan PWM (from payload)'),
            ]
            for name, data, hdr in csvs:
                if not data: continue
                fn = os.path.join(folder, f"{name}_{ts}.csv")
                with open(fn, 'w', newline='', encoding='utf-8') as f:
                    w = csv.writer(f)
                    if name in ('laser_mr','laser_bkg','laser_peak','fan_speed_rx'):
                        w.writerow(['Packet #', hdr])
                        for i, v in enumerate(data): w.writerow([i, v])
                    else:
                        w.writerow(['Altitude (m)', hdr])
                        for i, v in enumerate(data):
                            alt = self.altitude_data[i] if i < len(self.altitude_data) else ''
                            w.writerow([alt, v])

            self.fig.savefig(os.path.join(folder, f"dashboard_{ts}.png"),
                             dpi=300, bbox_inches='tight',
                             facecolor=self.fig.get_facecolor())
            self._msg(f"Saved to: {folder}/")
        except Exception as e:
            self._msg(f"Save error: {e}")

    # ── Test data ─────────────────────────────────────────────────────────────
    def _generate_test_data(self):  # noqa: C901
        """
        Generate a scientifically accurate synthetic atmospheric sounding profile
        from 0 to 12 km, covering the troposphere and the tropopause.

        Physical models used
        ────────────────────
        TEMPERATURE  — ISA (ICAO Standard Atmosphere) piecewise lapse rates:
                         0–11 000 m : −6.5 K/km  (troposphere)
                        11 000 m    : −56.5 °C   (tropopause / lower stratosphere)
                       A realistic surface offset ±3 °C is drawn once per run so
                       each test looks slightly different from the last.
                       A shallow temperature-inversion layer (warm cap) is injected
                       at a randomly chosen altitude between 800 and 1 800 m, where
                       temperature rises 1.5–3 °C over ~300 m before resuming the
                       normal lapse rate — common in the lower troposphere.

        PRESSURE     — Hypsometric / barometric formula:
                         P(z) = P0 · (T0/(T0+L·z))^(g·M/(R·L))
                       with g = 9.80665 m s⁻², M = 0.028964 kg/mol,
                       R = 8.31446 J/(mol·K), L = −0.0065 K/m.
                       Above 11 km the isothermal formula is used:
                         P(z) = P11 · exp(−g·M·(z−z11)/(R·T11))

        HUMIDITY     — Relative humidity follows a realistic exponential decrease
                       from a surface value (55–80 %) using a scale height of
                       2 200 m (representative of the mid-latitude troposphere).
                       A moist layer ±15 % RH is optionally injected at cloud base
                       (~900–2 500 m).  Above 8 km RH is clipped to ≤ 10 %.

        WIND SPEED   — Four-layer power-law / linear model:
                         0–100 m   : surface layer, 1–4 m/s
                         100–2000 m: boundary-layer growth ~0.005 m/s per metre
                         2–6 km    : free troposphere, 8–15 m/s linear increase
                         6–12 km   : upper troposphere / jet approach,
                                     20–35 m/s with a small jet-stream peak
                                     near 10–12 km

        WIND DIRECTION — Veers (rotates clockwise) with altitude following
                         Ekman-spiral physics: backing near surface then veering
                         through the boundary layer and aligning with the
                         geostrophic wind above ~2 km.

        ISOTOPE (δD / δ¹⁸O proxy in ‰ VSMOW)
                       — Rayleigh distillation model: as an air parcel rises,
                         condensation preferentially removes heavy isotopes.
                         δ(z) = δ_surface + slope · z   where slope ≈ −2.0 to
                         −2.5 ‰/km for δ¹⁸O (Craig–Gordon framework).
                         Surface value −4 to −8 ‰, reaching −28 to −36 ‰ at 12 km.

        LASER MIXING RATIO (H₂O volume mixing ratio, mol/mol)
                       — Derived from temperature and relative humidity via the
                         Clausius–Clapeyron equation:
                           e_sat(T) = 6.1078 · exp(17.269·T / (T + 237.3)) hPa
                           e_actual = RH/100 · e_sat
                           w        = 0.622 · e_actual / (P − e_actual)
                                    [kg/kg]  → convert to mol/mol × 1.608
                         This gives physically consistent mixing ratios that
                         track both the temperature and humidity profiles.

        LASER BACKGROUND — Slowly drifting baseline (0.42–0.55) with correlated
                           1/f noise (Perlin-style random walk) to mimic real
                           detector drift.

        LASER PEAK       — Peak = background + absorption_depth, where
                           absorption_depth ∝ mixing_ratio (Beer–Lambert scaling).
        """
        self._msg("Generating test profile for UAV sounding (0–12 km, HMP60 + TDLAS)…")

        # ── Altitude grid: 5 m steps, 0–12 km ─────────────────────────────────
        alts = np.arange(0, 12005, 5, dtype=float)
        n    = len(alts)
        rng  = np.random.default_rng()   # fresh seed every run → different profile each time

        # ══════════════════════════════════════════════════════════════════════
        # TEMPERATURE  (HMP60 range: −40 to +60 °C)
        # ISA standard lapse rate −6.5 °C/km through the troposphere.
        # Surface offset ±4 °C keeps values realistic for mid-latitude launches.
        # A mild inversion layer is inserted at a random boundary-layer height
        # (800–1 800 m) — this is the most common feature a UAV sounding reveals.
        # Tropopause at 11 km: temperature levels off at ≈ −56 °C.
        # ══════════════════════════════════════════════════════════════════════
        T_surface  = rng.uniform(10.0, 25.0)   # realistic launch-day surface temp (°C)
        inv_base   = rng.uniform(800,  1800)   # inversion base altitude (m)
        inv_top    = inv_base + rng.uniform(150, 350)   # inversion top
        inv_dT     = rng.uniform(1.5,  3.5)    # temperature gain through inversion (°C)

        temps = np.empty(n)
        for i, z in enumerate(alts):
            if z <= 11000:
                T = T_surface - 6.5 * z / 1000.0          # ISA lapse rate
            else:
                T = T_surface - 6.5 * 11.0                 # isothermal tropopause
            # Inversion bump (triangle shape between inv_base and inv_top)
            if inv_base < z < inv_top:
                frac = (z - inv_base) / (inv_top - inv_base)
                T   += inv_dT * (1.0 - abs(2 * frac - 1.0))
            temps[i] = T

        # Add smooth sensor noise (HMP60 accuracy ±0.2 °C typical)
        temps += _smooth(rng, n, sigma=0.15, width=20)
        # Hard-clip to HMP60 hardware range so ADC conversion is always valid
        temps  = np.clip(temps, -40.0, 60.0)

        # ══════════════════════════════════════════════════════════════════════
        # PRESSURE  (barometric formula; cross-checks temp profile)
        # P(z) = P0 · exp(−z / H_eff)  where H_eff varies with layer temperature.
        # Using the hypsometric equation layer by layer matches temps[] above.
        # ══════════════════════════════════════════════════════════════════════
        P0   = rng.uniform(1005, 1025)    # surface pressure hPa (launch-day variation)
        pres = np.empty(n)
        pres[0] = P0
        dz = 5.0   # step size metres
        for i in range(1, n):
            T_K     = temps[i - 1] + 273.15
            H_layer = (8.314 * T_K) / (0.028964 * 9.80665)   # scale height (m)
            pres[i] = pres[i - 1] * np.exp(-dz / H_layer)

        pres += _smooth(rng, n, sigma=0.3, width=15)   # sensor noise ±0.3 hPa
        pres  = np.clip(pres, 1.0, 1100.0)

        # ══════════════════════════════════════════════════════════════════════
        # RELATIVE HUMIDITY  (HMP60 range: 0–100 %)
        # Tropospheric RH decreases roughly exponentially from the surface.
        # Scale height 2–3 km is representative of mid-latitude conditions.
        # A moist layer (cloud base) is injected at 1–3 km to simulate the
        # typical high-RH band a UAV passes through on ascent.
        # Above 8 km RH is < 10 % (upper troposphere is very dry).
        # ══════════════════════════════════════════════════════════════════════
        RH_sfc      = rng.uniform(40.0, 75.0)    # surface RH at launch
        RH_scale_h  = rng.uniform(2000, 3000)    # e-folding height (m)
        cloud_alt   = rng.uniform(1000, 3000)    # moist-layer centre (m)
        cloud_amp   = rng.uniform(10.0, 25.0)    # RH enhancement in cloud (%)
        cloud_width = rng.uniform(300,  700)     # half-width (m)

        hums = np.empty(n)
        for i, z in enumerate(alts):
            RH = RH_sfc * np.exp(-z / RH_scale_h)
            # Moist layer (Gaussian bump)
            RH += cloud_amp * np.exp(-0.5 * ((z - cloud_alt) / cloud_width) ** 2)
            # Hard dry cap above 8 km (upper troposphere / lower stratosphere)
            if z > 8000:
                cap = 10.0 * np.exp(-(z - 8000) / 1500.0)
                RH  = min(RH, cap)
            hums[i] = RH

        hums += _smooth(rng, n, sigma=1.5, width=25)   # HMP60 RH noise ±1.5 %
        hums  = np.clip(hums, 0.5, 99.5)               # stay within HMP60 range

        # ══════════════════════════════════════════════════════════════════════
        # WIND SPEED  (m/s)
        # Surface layer 0–200 m: 1–5 m/s calm/light winds typical for UAV ops.
        # Boundary layer 200 m – 2 km: moderate increase with height.
        # Free troposphere 2–8 km: 8–20 m/s, steadily increasing.
        # Upper troposphere 8–12 km: 15–35 m/s, approaching jet-stream speeds.
        # ══════════════════════════════════════════════════════════════════════
        W_sfc  = rng.uniform(1.0, 5.0)     # surface wind speed (m/s)

        winds = np.empty(n)
        for i, z in enumerate(alts):
            if z < 200:
                w = W_sfc + z / 200.0 * 1.5
            elif z < 2000:
                w = W_sfc + 1.5 + (z - 200) / 1800.0 * 6.0
            elif z < 8000:
                w = W_sfc + 7.5 + (z - 2000) / 6000.0 * 12.0
            else:
                w = W_sfc + 19.5 + (z - 8000) / 4000.0 * 12.0
            winds[i] = w

        winds += _smooth(rng, n, sigma=0.8, width=30)
        winds  = np.clip(winds, 0.0, 50.0)

        # ══════════════════════════════════════════════════════════════════════
        # WIND DIRECTION  (degrees, 0–360)
        # Ekman-spiral veering: direction rotates clockwise with altitude
        # from the surface value by roughly 20–40° through the boundary layer,
        # then stays roughly constant in the free troposphere with slow drift.
        # ══════════════════════════════════════════════════════════════════════
        D_sfc  = rng.uniform(180, 360)      # surface wind from S/SW/W (most common)
        D_veer = rng.uniform(20,  45)       # total veering through boundary layer

        dirs = np.empty(n)
        for i, z in enumerate(alts):
            if z < 2000:
                dirs[i] = D_sfc + D_veer * (z / 2000.0)
            else:
                dirs[i] = D_sfc + D_veer + (z - 2000) / 1000.0 * 1.5
            dirs[i] %= 360

        dirs += _smooth(rng, n, sigma=5.0, width=20)
        dirs  %= 360

        # ══════════════════════════════════════════════════════════════════════
        # WATER-VAPOUR ISOTOPE RATIO  δ¹⁸O or δD  (‰ VSMOW)
        # The isotope field displayed is the Rayleigh distillation proxy —
        # the same quantity your TDLAS system resolves from the mixing ratio.
        # Typical mid-latitude tropospheric values:
        #   Surface:          −5 to −10 ‰  (δ¹⁸O)   /  −30 to −70 ‰  (δD)
        #   At 12 km:        −30 to −40 ‰  (δ¹⁸O)   /  −230 to −280 ‰ (δD)
        # Gradient ≈ −2 to −2.5 ‰/km for δ¹⁸O; code below uses δ¹⁸O scale.
        # ══════════════════════════════════════════════════════════════════════
        iso_sfc   = rng.uniform(-10.0,  -5.0)    # surface δ¹⁸O (‰)
        iso_slope = rng.uniform(-2.5,   -2.0)    # ‰ per km altitude (negative: depletion with altitude)

        isos = np.empty(n)
        for i, z in enumerate(alts):
            isos[i] = iso_sfc + iso_slope * (z / 1000.0)

        isos += _smooth(rng, n, sigma=0.4, width=15)   # TDLAS precision ±0.3 ‰ typ.

        # ══════════════════════════════════════════════════════════════════════
        # LASER MIXING RATIO  (H₂O volume mixing ratio, units match your TDLAS)
        # Derived directly from the temperature + RH above via Tetens formula.
        # This gives physically self-consistent values — the plot should show
        # the same exponential decrease you'd expect from a radiosonde sounding.
        #
        # Tetens saturation vapour pressure:
        #   e_sat(T) = 6.1078 · exp(17.269·T / (T + 237.3))   [hPa]
        #   e_act    = (RH/100) · e_sat
        #   w        = 0.622 · e_act / (P − e_act)             [kg/kg]
        #            → typical surface value ~10 g/kg, drops to <0.1 g/kg at 12 km
        # ══════════════════════════════════════════════════════════════════════
        mrs = np.empty(n)
        for i, z in enumerate(alts):
            T_c   = float(temps[i])
            RH_pc = float(hums[i])
            P_hpa = float(pres[i])
            e_sat = 6.1078 * np.exp(17.269 * T_c / (T_c + 237.3))
            e_act = (RH_pc / 100.0) * e_sat
            # mixing ratio kg/kg, guarded against near-zero pressure
            w_kgkg = 0.622 * e_act / max(P_hpa - e_act, 0.1)
            # convert to g/kg for a human-readable TDLAS output
            mrs[i] = w_kgkg * 1000.0

        # TDLAS precision noise: ±0.5 % of reading (typical OA-ICOS / CRDS spec)
        mrs *= (1.0 + _smooth(rng, n, sigma=0.003, width=10))
        mrs  = np.clip(mrs, 1e-4, 30.0)    # physical bounds: 0 → ~30 g/kg surface max

        # ══════════════════════════════════════════════════════════════════════
        # LASER BACKGROUND  (detector baseline signal, arbitrary instrument units)
        # This is the laser intensity seen when no absorption occurs — it drifts
        # slowly with temperature and pressure on the optics.
        # Typical range for a compact TDLAS unit: 0.40–0.60 AU.
        # Modelled as a slow random walk correlated with altitude (pressure-driven).
        # ══════════════════════════════════════════════════════════════════════
        bkg_base  = rng.uniform(0.44, 0.54)
        bkg_drift = np.cumsum(rng.standard_normal(n) * 0.0006)
        bkg_drift -= bkg_drift[0]
        bkgs = np.clip(bkg_base + bkg_drift, 0.38, 0.62)

        # ══════════════════════════════════════════════════════════════════════
        # LASER PEAK  (absorption peak signal, AU)
        # Peak = background + absorption depth.
        # Absorption depth ∝ mixing ratio × path length (Beer–Lambert law).
        # Normalised so that at the typical surface mixing ratio (~12 g/kg) the
        # peak sits ≈ 0.70 AU above the background — matching a realistic
        # on-resonance absorption fraction for a 1-cm path cell.
        # At 12 km (mr ≈ 0.01 g/kg) absorption collapses nearly to background.
        # ══════════════════════════════════════════════════════════════════════
        MR_REF      = 12.0    # g/kg  reference mixing ratio (surface)
        PEAK_AT_REF = 0.70    # AU    absorption depth at MR_REF
        peaks = np.empty(n)
        for i in range(n):
            abs_depth = PEAK_AT_REF * (mrs[i] / MR_REF)
            abs_depth = float(np.clip(abs_depth, 0.0005, 1.2))
            peak_noise = rng.standard_normal() * 0.008
            peaks[i] = float(bkgs[i] + abs_depth + peak_noise)

        # ── Assemble packet dicts ───────────────────────────────────────────────
        buf = []
        t0_ms = 0
        for i, alt in enumerate(alts):
            mr  = float(mrs[i])
            bkg = float(bkgs[i])
            pk  = float(peaks[i])
            d = {
                'altitude':          float(alt),
                'temperature':       float(temps[i]),
                'pressure':          float(pres[i]),
                'humidity':          float(hums[i]),
                'wind_speed':        float(winds[i]),
                'wind_direction':    float(dirs[i]),
                'isotope':           float(isos[i]),
                'laser_mixing_ratio': mr,
                'laser_background':   bkg,
                'laser_peak':         pk,
                'laser_data':         [mr, bkg, pk],
                'packet_number':      int(len(buf) % 256),
                # Fan runs at 25 % PWM below 2 km, ramps to 100 % above 8 km
                # to compensate for lower air density at altitude
                'fan_speed_pwm':      int(np.clip(64 + (alt / 12000.0) * 191, 64, 255)),
                't0_loop':            t0_ms,
                'tf_loop':            t0_ms + 980,
                'laser_timestamp_ms': t0_ms,
                'laser_packet_num':   i,
                'channel':            'TEST_DATA',
            }
            t0_ms += 1000
            buf.append(d)

        self.test_data_buffer = buf
        self.test_data_index  = 0
        self._msg(f"Test data ready: {len(buf)} packets  "
                  f"(T: {temps[0]:.1f}→{temps[-1]:.1f} °C | "
                  f"MR: {mrs[0]:.2f}→{mrs[-1]:.4f} g/kg | "
                  f"δ¹⁸O: {isos[0]:.1f}→{isos[-1]:.1f} ‰)")

    @staticmethod
    def _build_fake_raw(d: dict) -> bytes:
        """
        Reconstruct a plausible binary packet from a synthetic test dict so
        the RAW PACKET PRINT line looks exactly like a real CH0=232 packet.
        Field order mirrors parse_payload_channel0() exactly.
        """
        t0    = int(d.get("t0_loop",           0))   & 0xFFFFFFFF
        pnum  = int(d.get("packet_number",     0))   & 0xFF
        fan   = int(d.get("fan_speed_pwm",     64))  & 0xFF
        # temperature / humidity stored as raw ADC (reverse the HMP60 scaling)
        temp_c = d.get("temperature", 20.0)
        raw_t  = int(((temp_c - PacketParser.HMP60_TEMP_MIN) /
                      (PacketParser.HMP60_TEMP_MAX - PacketParser.HMP60_TEMP_MIN)) * 1023)
        raw_t  = max(0, min(1023, raw_t))
        hum_pc = d.get("humidity", 50.0)
        raw_h  = int((hum_pc / 100.0) * 1023)
        raw_h  = max(0, min(1023, raw_h))
        lp0   = int(d.get("laser_timestamp_ms", 0)) & 0xFFFFFFFF
        lp1   = int(d.get("laser_packet_num",   0)) & 0xFFFFFFFF
        mr    = float(d.get("laser_mixing_ratio", 0.0))
        bkg   = float(d.get("laser_background",  0.0))
        peak  = float(d.get("laser_peak",        0.0))
        tf    = int(d.get("tf_loop",             0))  & 0xFFFFFFFF
        data_section = struct.pack(
            "<LBBHHLLfffL",
            t0, pnum, fan, raw_t, raw_h, lp0, lp1, mr, bkg, peak, tf
        )
        size = len(data_section)   # should be 34
        crc  = PacketParser.fletcher16(data_section)
        header = struct.pack("<BBBBH",
            0x55, 0x24,
            PacketTypes.PAYLOAD_DATA_CHANNEL_0,  # 232
            PacketAction.STATUS,                  # 0
            size)
        return header + data_section + struct.pack(">H", crc)

    def _stream_test_data(self):
        if not self.streaming_test or not self.mission_running:
            return
        if self.test_data_index < len(self.test_data_buffer):
            d = self.test_data_buffer[self.test_data_index]
            # Build a real binary representation so the raw-print matches live packets
            try:
                raw_bytes = FLAIRControlDeck._build_fake_raw(d)
            except Exception:
                raw_bytes = b''
            # ── RAW PACKET PRINT (test data) ─────────────────────────
            _pnum = d.get("packet_number", "?")
            print("[TEST RAW] pkt#{} {}B | {}".format(
                _pnum, len(raw_bytes),
                " ".join("{:02X}".format(b) for b in raw_bytes)))
            self.data_queue.put((raw_bytes, d, 'OK (test)'))
            self.total_rx        += 1
            self.test_data_index += 1
            pct = self.test_data_index / len(self.test_data_buffer) * 100
            self._msg(f"Test stream: {pct:.1f}%  pkt {self.test_data_index}/{len(self.test_data_buffer)}")
            self.root.after(self.test_interval_ms, self._stream_test_data)
        else:
            self.streaming_test = False
            self._msg(f"Test data complete — {self.total_rx} packets processed")


# ──────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────
if __name__ == "__main__":
    print_packet_guide()
    root = tk.Tk()
    root.withdraw()

    splash = SplashScreen(root, "logo.png", duration=3000)

    def _show():
        root.deiconify()
        FLAIRControlDeck(root)

    root.after(3100, _show)
    root.mainloop()
