import tkinter as tk
from tkinter import ttk, messagebox
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure
from matplotlib.patches import FancyArrow, Circle
import numpy as np
from datetime import datetime
import socket
import struct
from threading import Thread
import queue
import json
from PIL import Image, ImageTk
import os
import csv
import serial
import serial.tools.list_ports

class PacketTypes:
    """Define packet types to match ground station"""
    TELEMETRY_POSITION = 0x10
    TELEMETRY_PRESSURE = 0x11
    TELEMETRY_ORIENTATION = 0x12
    TELEMETRY_SYSTEM = 0x13
    PAYLOAD_CHANNEL_0 = 0x20
    GENERIC_ATMOSPHERIC = 0x30

class PacketParser:
    """Enhanced parser with packet type handling and serial string support"""
    
    @staticmethod
    def calculate_fletcher16_checksum(data):
        """Calculate Fletcher-16 checksum"""
        sum1 = 0
        sum2 = 0
        for byte in data:
            sum1 = (sum1 + byte) % 255
            sum2 = (sum2 + sum1) % 255
        return (sum2 << 8) | sum1
    
    @staticmethod
    def verify_checksum(packet):
        """Verify packet checksum"""
        if len(packet) < 2:
            return False
        data = packet[:-2]
        received_checksum = struct.unpack('>H', packet[-2:])[0]
        calculated_checksum = PacketParser.calculate_fletcher16_checksum(data)
        return received_checksum == calculated_checksum
    
    @staticmethod
    def parse_serial_string(serial_str):
        """
        Parse serial string format: "altitude,wind_speed,temperature,humidity"
        Example: "3496.39,0.09,15.0,0.5316"
        
        Returns parsed data dictionary or None if parsing fails
        """
        try:
            serial_str = serial_str.strip()
            values = serial_str.split(',')
            
            if len(values) != 4:
                return None, f"Invalid serial format: expected 4 values, got {len(values)}"
            
            parsed_data = {
                'data_type': 'serial_string',
                'altitude': float(values[0]),
                'wind_speed': float(values[1]),
                'temperature': float(values[2]),
                'humidity': float(values[3]) * 100.0,
            }
            
            parsed_data['pressure'] = 1013.25 * np.exp(-parsed_data['altitude'] / 8500.0)
            parsed_data['wind_direction'] = 0.0
            parsed_data['isotope'] = -8.0 - (parsed_data['altitude'] / 1000.0) * 3.5
            
            return parsed_data, "Success"
            
        except (ValueError, IndexError) as e:
            return None, f"Serial string parse error: {str(e)}"
    
    @staticmethod
    def parse_packet(packet):
        """Parse telemetry packet with type-specific handling"""
        
        if isinstance(packet, str):
            return PacketParser.parse_serial_string(packet)
        
        try:
            if isinstance(packet, bytes):
                decoded_str = packet.decode('utf-8', errors='ignore').strip()
                if ',' in decoded_str and not decoded_str.startswith('U$'):
                    return PacketParser.parse_serial_string(decoded_str)
        except:
            pass
        
        if len(packet) < 8:
            return None, "Packet too short"
        
        if packet[0] != 0x55 or packet[1] != 0x24:
            return None, "Invalid packet headers"
        
        if not PacketParser.verify_checksum(packet):
            return None, "Checksum verification failed"
        
        packet_type = packet[2]
        action = packet[3]
        size = struct.unpack('>H', packet[4:6])[0]
        data_section = packet[6:-2]
        
        if len(data_section) < 1:
            return None, "No data in packet"
        
        if packet_type == PacketTypes.TELEMETRY_POSITION:
            return PacketParser.parse_position(data_section, packet_type, action, size)
        elif packet_type == PacketTypes.TELEMETRY_PRESSURE:
            return PacketParser.parse_pressure(data_section, packet_type, action, size)
        elif packet_type == PacketTypes.TELEMETRY_SYSTEM:
            return PacketParser.parse_system(data_section, packet_type, action, size)
        elif packet_type == PacketTypes.PAYLOAD_CHANNEL_0:
            return PacketParser.parse_payload(data_section, packet_type, action, size)
        else:
            return PacketParser.parse_generic(data_section, packet_type, action, size)
    
    @staticmethod
    def parse_position(data, packet_type, action, size):
        """Parse position telemetry"""
        parsed_data = {
            'packet_type': packet_type,
            'action': action,
            'size': size,
            'data_type': 'position'
        }
        
        idx = 0
        if idx + 3 < len(data):
            try:
                parsed_data['latitude'] = struct.unpack('>f', data[idx:idx+4])[0]
            except:
                lat_raw = struct.unpack('>i', data[idx:idx+4])[0]
                parsed_data['latitude'] = lat_raw / 1e7
            idx += 4
        
        if idx + 3 < len(data):
            try:
                parsed_data['longitude'] = struct.unpack('>f', data[idx:idx+4])[0]
            except:
                lon_raw = struct.unpack('>i', data[idx:idx+4])[0]
                parsed_data['longitude'] = lon_raw / 1e7
            idx += 4
        
        if idx + 3 < len(data):
            try:
                parsed_data['altitude'] = struct.unpack('>f', data[idx:idx+4])[0]
            except:
                alt_raw = struct.unpack('>i', data[idx:idx+4])[0]
                parsed_data['altitude'] = alt_raw / 1000.0
            idx += 4
        
        return parsed_data, "Success"
    
    @staticmethod
    def parse_pressure(data, packet_type, action, size):
        """Parse pressure telemetry"""
        parsed_data = {
            'packet_type': packet_type,
            'action': action,
            'size': size,
            'data_type': 'pressure'
        }
        
        idx = 0
        if idx + 1 < len(data):
            if len(data) >= idx + 4:
                try:
                    parsed_data['pressure'] = struct.unpack('>f', data[idx:idx+4])[0]
                    idx += 4
                except:
                    pressure_raw = struct.unpack('>H', data[idx:idx+2])[0]
                    parsed_data['pressure'] = 800.0 + (pressure_raw / 1023.0) * 300.0
                    idx += 2
            else:
                pressure_raw = struct.unpack('>H', data[idx:idx+2])[0]
                parsed_data['pressure'] = 800.0 + (pressure_raw / 1023.0) * 300.0
                idx += 2
        
        if idx + 1 < len(data):
            if len(data) >= idx + 4:
                try:
                    parsed_data['temperature'] = struct.unpack('>f', data[idx:idx+4])[0]
                    idx += 4
                except:
                    temp_raw = struct.unpack('>h', data[idx:idx+2])[0]
                    parsed_data['temperature'] = temp_raw / 100.0
                    idx += 2
            else:
                temp_raw = struct.unpack('>h', data[idx:idx+2])[0]
                parsed_data['temperature'] = temp_raw / 100.0
                idx += 2
        
        if idx + 1 < len(data):
            humidity_raw = struct.unpack('>H', data[idx:idx+2])[0]
            parsed_data['humidity'] = (humidity_raw / 1023.0) * 100.0
            idx += 2
        
        return parsed_data, "Success"
    
    @staticmethod
    def parse_system(data, packet_type, action, size):
        """Parse system telemetry"""
        parsed_data = {
            'packet_type': packet_type,
            'action': action,
            'size': size,
            'data_type': 'system'
        }
        
        idx = 0
        if idx < len(data):
            parsed_data['hour'] = data[idx]
            idx += 1
        if idx < len(data):
            parsed_data['minute'] = data[idx]
            idx += 1
        if idx < len(data):
            parsed_data['seconds'] = data[idx]
            idx += 1
        
        return parsed_data, "Success"
    
    @staticmethod
    def parse_payload(data, packet_type, action, size):
        """Parse payload data"""
        parsed_data = {
            'packet_type': packet_type,
            'action': action,
            'size': size,
            'data_type': 'payload'
        }
        
        try:
            parsed_data['message'] = data.decode('utf-8', errors='ignore').strip('\x00')
        except:
            parsed_data['message'] = ' '.join(f'{b:02x}' for b in data)
        
        parsed_data['raw_data'] = list(data)
        return parsed_data, "Success"
    
    @staticmethod
    def parse_generic(data, packet_type, action, size):
        """Generic parser for unknown packet types"""
        parsed_data = {
            'packet_type': packet_type,
            'action': action,
            'size': size,
            'data_type': 'generic'
        }
        
        idx = 0
        if idx < len(data):
            parsed_data['packet_number'] = data[idx]
            idx += 1
        
        if idx + 1 < len(data):
            temp_raw = struct.unpack('>H', data[idx:idx+2])[0]
            parsed_data['temperature'] = (temp_raw / 1023.0) * 100.0 - 40.0
            idx += 2
        
        if idx + 1 < len(data):
            humidity_raw = struct.unpack('>H', data[idx:idx+2])[0]
            parsed_data['humidity'] = (humidity_raw / 1023.0) * 100.0
            idx += 2
        
        if idx + 1 < len(data):
            fan_raw = struct.unpack('>H', data[idx:idx+2])[0]
            parsed_data['fan_speed'] = fan_raw
            idx += 2
        
        remaining_data = data[idx:]
        if len(remaining_data) >= 10:
            atm_start = len(remaining_data) - 10
            if atm_start >= 0:
                atm_data = remaining_data[atm_start:]
                
                if len(atm_data) >= 2:
                    wind_speed_raw = struct.unpack('>H', atm_data[0:2])[0]
                    parsed_data['wind_speed'] = (wind_speed_raw / 1023.0) * 50.0
                
                if len(atm_data) >= 4:
                    wind_dir_raw = struct.unpack('>H', atm_data[2:4])[0]
                    parsed_data['wind_direction'] = (wind_dir_raw / 1023.0) * 360.0
                
                if len(atm_data) >= 6:
                    pressure_raw = struct.unpack('>H', atm_data[4:6])[0]
                    parsed_data['pressure'] = 800.0 + (pressure_raw / 1023.0) * 300.0
                
                if len(atm_data) >= 8:
                    altitude_raw = struct.unpack('>H', atm_data[6:8])[0]
                    parsed_data['altitude'] = (altitude_raw / 1023.0) * 5000.0
                
                if len(atm_data) >= 10:
                    isotope_raw = struct.unpack('>H', atm_data[8:10])[0]
                    parsed_data['isotope'] = -30.0 + (isotope_raw / 1023.0) * 40.0
        
        return parsed_data, "Success"
    
    @staticmethod
    def parse_alternate_format(packet):
        """Alternate parser for uint8 arrays"""
        try:
            if len(packet) >= 7:
                parsed_data = {}
                idx = 0
                
                if idx < len(packet):
                    parsed_data['temperature'] = (packet[idx] / 255.0) * 100.0 - 40.0
                    idx += 1
                if idx < len(packet):
                    parsed_data['humidity'] = (packet[idx] / 255.0) * 100.0
                    idx += 1
                if idx < len(packet):
                    parsed_data['wind_speed'] = (packet[idx] / 255.0) * 50.0
                    idx += 1
                if idx < len(packet):
                    parsed_data['wind_direction'] = (packet[idx] / 255.0) * 360.0
                    idx += 1
                if idx < len(packet):
                    parsed_data['pressure'] = 800.0 + (packet[idx] / 255.0) * 300.0
                    idx += 1
                if idx < len(packet):
                    parsed_data['altitude'] = (packet[idx] / 255.0) * 5000.0
                    idx += 1
                if idx < len(packet):
                    parsed_data['isotope'] = -30.0 + (packet[idx] / 255.0) * 40.0
                    idx += 1
                
                return parsed_data, "Success"
            
            return None, "Packet too short for alternate format"
        except Exception as e:
            return None, f"Alternate parse error: {str(e)}"

class SerialReceiver:
    """Handles Serial communication for receiving data"""
    
    def __init__(self, port=None, baudrate=9600):
        self.port = port
        self.baudrate = baudrate
        self.serial_conn = None
        self.connected = False
        self.running = False
    
    @staticmethod
    def list_ports():
        """List available serial ports"""
        ports = serial.tools.list_ports.comports()
        return [port.device for port in ports]
    
    def connect(self, port=None):
        """Connect to serial port"""
        try:
            if port:
                self.port = port
            
            if not self.port:
                return False, "No port specified"
            
            self.serial_conn = serial.Serial(
                port=self.port,
                baudrate=self.baudrate,
                timeout=1.0
            )
            self.connected = True
            return True, f"Connected to {self.port} at {self.baudrate} baud"
        except Exception as e:
            return False, f"Serial connection failed: {str(e)}"
    
    def read_line(self):
        """Read a line from serial port"""
        try:
            if self.serial_conn and self.serial_conn.in_waiting > 0:
                line = self.serial_conn.readline()
                return line
            return None
        except Exception as e:
            print(f"Serial read error: {e}")
            return None
    
    def disconnect(self):
        """Close serial connection"""
        self.running = False
        if self.serial_conn:
            try:
                self.serial_conn.close()
            except:
                pass
        self.connected = False

class WiFiReceiver:
    """Handles Wi-Fi communication with the router receiving RF data"""
    
    def __init__(self, host='0.0.0.0', port=5000):
        self.host = host
        self.port = port
        self.socket = None
        self.connected = False
        self.running = False
        
    def connect(self):
        """Establish Wi-Fi connection to the router"""
        try:
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.socket.settimeout(5.0)
            
            self.socket.bind((self.host, self.port))
            self.socket.listen(1)
            self.connected = True
            return True, f"Listening on {self.host}:{self.port}"
        except Exception as e:
            return False, f"Connection failed: {str(e)}"
    
    def accept_connection(self):
        """Accept incoming connection from router"""
        try:
            if self.socket:
                conn, addr = self.socket.accept()
                return conn, f"Router connected from {addr}"
            return None, "No socket available"
        except socket.timeout:
            return None, "Connection timeout"
        except Exception as e:
            return None, f"Accept error: {str(e)}"
    
    def receive_data(self, conn):
        """Receive and parse data from router"""
        try:
            data = conn.recv(1024)
            if not data:
                return None
            
            parsed_data, status = PacketParser.parse_packet(data)
            if parsed_data is not None:
                return parsed_data
            
            parsed_data, status = PacketParser.parse_alternate_format(data)
            if parsed_data is not None:
                return parsed_data
            
            try:
                json_data = json.loads(data.decode('utf-8'))
                return self.parse_json_telemetry(json_data)
            except json.JSONDecodeError:
                pass
            
            print(f"Failed to parse packet: {status}")
            return None
            
        except Exception as e:
            print(f"Receive error: {e}")
            return None
    
    def parse_json_telemetry(self, data):
        """Parse JSON telemetry data"""
        parsed = {}
        parsed['altitude'] = float(data.get('altitude', 0))
        parsed['temperature'] = float(data.get('temperature', 0))
        parsed['pressure'] = float(data.get('pressure', 0))
        parsed['humidity'] = float(data.get('humidity', 0))
        parsed['wind_speed'] = float(data.get('wind_speed', 0))
        parsed['wind_direction'] = float(data.get('wind_direction', 0))
        parsed['isotope'] = float(data.get('isotope', 0))
        return parsed
    
    def disconnect(self):
        """Close the connection"""
        self.running = False
        if self.socket:
            try:
                self.socket.close()
            except:
                pass
        self.connected = False

class BoundaryLayerAnalyzer:
    """Algorithm for determining boundary layer structure using equivalent potential temperature"""
    
    @staticmethod
    def calculate_mixing_ratio(temp_celsius, pressure_hpa, humidity_percent):
        """
        Calculate total water mixing ratio (r_t) and water vapour mixing ratio (r_v)
        
        Args:
            temp_celsius: Temperature in Celsius
            pressure_hpa: Pressure in hPa
            humidity_percent: Relative humidity in percent (0-100)
        
        Returns:
            r_t, r_v: Total water and water vapour mixing ratios [kg/kg]
        """
        # Convert temperature to Kelvin
        temp_k = temp_celsius + 273.15
        
        # Calculate saturation vapor pressure using Tetens formula (hPa)
        e_sat = 6.112 * np.exp((17.67 * temp_celsius) / (temp_celsius + 243.5))
        
        # Actual vapor pressure
        H = humidity_percent / 100.0  # Convert to fraction
        e = H * e_sat
        
        # Water vapor mixing ratio (kg/kg)
        epsilon = 0.622  # Ratio of molecular weights (water/dry air)
        r_v = epsilon * e / (pressure_hpa - e)
        
        # For unsaturated air, total water mixing ratio equals vapor mixing ratio
        r_t = r_v
        
        return r_t, r_v
    
    @staticmethod
    def calculate_equivalent_potential_temperature(temp_celsius, pressure_hpa, humidity_percent):
        """
        Calculate equivalent potential temperature using the provided formula:
        
        θ_e = T * (p0/p)^(R_d/(c_pd + r_t*c)) * H^(-r_v*R_v/(c_pd + r_t*c)) * exp[L_v*r_v / ((c_pd + r_t*c)*T)]
        
        Args:
            temp_celsius: Temperature in Celsius
            pressure_hpa: Pressure in hPa
            humidity_percent: Relative humidity in percent (0-100)
        
        Returns:
            theta_e: Equivalent potential temperature [K]
        """
        # Constants
        R_d = 287.05  # Specific gas constant for dry air [J/(kg·K)]
        R_v = 461.5   # Specific gas constant for water vapor [J/(kg·K)]
        c_pd = 1005.0 # Specific heat capacity of dry air at constant pressure [J/(kg·K)]
        c = 4186.0    # Specific heat capacity of liquid water [J/(kg·K)]
        L_v = 2.501e6 # Latent heat of vaporization of water [J/kg]
        p0 = 1000.0   # Reference pressure [hPa]
        
        # Convert temperature to Kelvin
        T = temp_celsius + 273.15
        
        # Convert relative humidity to fraction
        H = humidity_percent / 100.0
        
        # Avoid division by zero and invalid values
        H = np.clip(H, 0.01, 1.0)
        
        # Calculate mixing ratios
        r_t, r_v = BoundaryLayerAnalyzer.calculate_mixing_ratio(temp_celsius, pressure_hpa, humidity_percent)
        
        # Calculate each term of the formula
        # Term 1: (p0/p)^(R_d/(c_pd + r_t*c))
        exponent1 = R_d / (c_pd + r_t * c)
        term1 = (p0 / pressure_hpa) ** exponent1
        
        # Term 2: H^(-r_v*R_v/(c_pd + r_t*c))
        exponent2 = -r_v * R_v / (c_pd + r_t * c)
        term2 = H ** exponent2
        
        # Term 3: exp[L_v*r_v / ((c_pd + r_t*c)*T)]
        term3 = np.exp(L_v * r_v / ((c_pd + r_t * c) * T))
        
        # Calculate equivalent potential temperature
        theta_e = T * term1 * term2 * term3
        
        return theta_e
    
    @staticmethod
    def analyze_boundary_layer(altitude, temp, pressure, humidity, wind_speed):
        """
        Distinguishes between atmospheric layers using equivalent potential temperature:
        - Surface Roughness Layer (SRL): 0-100m
        - Transition Zone 1: 100-105m
        - Planetary Boundary Layer (PBL): 105-3000m
        - Transition Zone 2: 3000-5000m
        - Free Troposphere (FT): 5000-18000m
        """
        if len(altitude) < 10:
            return None
        
        results = {
            'srl_top': 100,
            'transition1_start': 100,
            'transition1_end': 105,
            'pbl_start': 105,
            'pbl_top': 3000,
            'transition2_start': 3000,
            'transition2_end': 5000,
            'ft_start': 5000,
            'inversion_detected': False,
            'mixing_height': None
        }
        
        # Calculate equivalent potential temperature for each data point
        theta_e = np.array([
            BoundaryLayerAnalyzer.calculate_equivalent_potential_temperature(
                temp[i], pressure[i], humidity[i]
            ) for i in range(len(altitude))
        ])
        
        # Calculate gradient of equivalent potential temperature with respect to altitude
        dtheta_e_dz = np.gradient(theta_e, altitude)
        dwind_dz = np.gradient(wind_speed, altitude)
        
        # Inversion threshold for equivalent potential temperature
        inversion_threshold = 0.005  # K/m
        
        # Look for inversion within PBL (105-3000m)
        for i in range(len(altitude)):
            if 105 < altitude[i] < 3000 and i < len(dtheta_e_dz) and dtheta_e_dz[i] > inversion_threshold:
                results['pbl_top'] = altitude[i]
                results['inversion_detected'] = True
                results['mixing_height'] = altitude[i]
                results['transition2_start'] = altitude[i]
                break
        
        if results['mixing_height'] is None:
            results['mixing_height'] = 3000
        
        return results

class SplashScreen:
    """Animated splash screen with morphing logo"""
    
    def __init__(self, parent, logo_path, duration=3000):
        self.parent = parent
        self.logo_path = logo_path
        self.duration = duration
        
        self.splash = tk.Toplevel(parent)
        self.splash.title("")
        self.splash.attributes('-fullscreen', True)
        self.splash.configure(bg='#0A0A0A')
        self.splash.overrideredirect(True)
        
        container = tk.Frame(self.splash, bg='#0A0A0A')
        container.place(relx=0.5, rely=0.5, anchor='center')
        
        self.title_label = tk.Label(container, text="FLAIR CONTROL DECK",
                                    font=('Bahnschrift', 48, 'bold'),
                                    bg='#0A0A0A', fg='#00FFFF')
        self.title_label.pack(pady=20)
        
        self.logo_label = tk.Label(container, bg='#0A0A0A')
        self.logo_label.pack(pady=20)
        
        self.status_label = tk.Label(container, text="Initializing Systems...",
                                     font=('Consolas', 14),
                                     bg='#0A0A0A', fg='#00FF00')
        self.status_label.pack(pady=10)
        
        self.progress = ttk.Progressbar(container, length=400, mode='determinate')
        self.progress.pack(pady=20)
        
        self.alpha = 0
        self.scale = 0.5
        self.rotation = 0
        self.progress_value = 0
        
        self.load_logo()
        self.animate()
        self.splash.after(duration, self.close_splash)
    
    def load_logo(self):
        """Load and prepare logo image"""
        try:
            if os.path.exists(self.logo_path):
                self.original_logo = Image.open(self.logo_path)
            else:
                self.original_logo = Image.new('RGBA', (200, 200), (0, 255, 255, 255))
        except:
            self.original_logo = Image.new('RGBA', (200, 200), (0, 255, 255, 255))
    
    def animate(self):
        """Morphing animation loop"""
        if self.alpha < 1.0:
            self.alpha += 0.02
        
        if self.scale < 1.0:
            self.scale += 0.02
        elif self.scale < 1.2:
            self.scale += 0.01
        else:
            self.scale = 1.0
        
        self.rotation = (self.rotation + 3) % 360
        
        if self.progress_value < 100:
            self.progress_value += 2
            self.progress['value'] = self.progress_value
        
        size = int(200 * self.scale)
        img = self.original_logo.resize((size, size), Image.Resampling.LANCZOS)
        img = img.rotate(self.rotation, expand=False)
        
        if self.alpha < 1.0:
            alpha_mask = Image.new('L', img.size, int(255 * self.alpha))
            img.putalpha(alpha_mask)
        
        self.logo_photo = ImageTk.PhotoImage(img)
        self.logo_label.config(image=self.logo_photo)
        
        status_texts = [
            "Initializing Systems...",
            "Loading Packet Parser...",
            "Connecting to WiFi...",
            "Preparing Telemetry...",
            "Ready for Launch!"
        ]
        status_index = min(int(self.progress_value / 20), len(status_texts) - 1)
        self.status_label.config(text=status_texts[status_index])
        
        color_value = int(128 + 127 * np.sin(self.rotation * np.pi / 180))
        title_color = f'#{color_value:02x}{255:02x}{255:02x}'
        self.title_label.config(fg=title_color)
        
        if self.progress_value < 100:
            self.splash.after(50, self.animate)
    
    def close_splash(self):
        """Close splash screen"""
        self.splash.destroy()

class FLAIRControlDeck:
    def __init__(self, root):
        self.root = root
        self.root.title("FLAIR Control Deck - Enhanced Parser + Serial Support")
        self.root.attributes('-fullscreen', True)
        self.root.bind('<Escape>', lambda e: self.toggle_fullscreen())
        self.root.bind('<F11>', lambda e: self.toggle_fullscreen())
        
        self.dark_mode = True
        
        self.data_queue = queue.Queue()
        self.altitude_data = []
        self.temp_data = []
        self.pressure_data = []
        self.humidity_data = []
        self.wind_speed_data = []
        self.wind_direction_data = []
        self.isotope_data = []
        
        self.total_packets_received = 0
        self.failed_packets = 0
        
        self.test_data_buffer = []
        self.test_data_index = 0
        self.streaming_test_data = False
        self.test_data_interval = 100
        
        self.mission_start_time = None
        self.mission_running = False
        self.start_time = datetime.now()
        
        self.wifi_receiver = WiFiReceiver(host='0.0.0.0', port=5000)
        self.wifi_connection = None
        self.wifi_thread = None
        self.wifi_connected = False
        
        self.serial_receiver = SerialReceiver(baudrate=9600)
        self.serial_thread = None
        self.serial_connected = False
        
        self.create_ui()
        self.update_data()
        self.update_time()
    
    def toggle_fullscreen(self):
        """Toggle fullscreen mode"""
        current = self.root.attributes('-fullscreen')
        self.root.attributes('-fullscreen', not current)
        
    def create_ui(self):
        self.main_frame = tk.Frame(self.root, bg='#0F0F0F')
        self.main_frame.pack(fill=tk.BOTH, expand=True)
        
        self.bg_canvas = tk.Canvas(self.main_frame, bg='#0F0F0F', highlightthickness=0)
        self.bg_canvas.place(x=0, y=0, relwidth=1, relheight=1)
        self.draw_gradient_background()
        
        content_container = tk.Frame(self.main_frame, bg='')
        content_container.place(x=0, y=0, relwidth=1, relheight=1)
        
        top_frame = tk.Frame(content_container, bg='', height=100)
        top_frame.pack(fill=tk.X, padx=10, pady=5)
        top_frame.pack_propagate(False)
        
        logo_frame = tk.Frame(top_frame, bg='', width=150)
        logo_frame.pack(side=tk.LEFT, padx=10)
        
        try:
            logo_path = r"C:\Users\adhi1\OneDrive\Desktop\CU Boulder Aerospace Books and Resources\CU Boulder Graduate Project ASEN5018\logo.png"
            if os.path.exists(logo_path):
                img = Image.open(logo_path)
                img = img.resize((120, 80), Image.Resampling.LANCZOS)
                self.logo_img = ImageTk.PhotoImage(img)
                logo_label = tk.Label(logo_frame, image=self.logo_img, bg='#1E1E1E')
                logo_label.pack()
            else:
                logo_label = tk.Label(logo_frame, text="FLAIR\nLOGO", 
                                    font=('Arial', 12, 'bold'), bg='#1E1E1E', fg='white')
                logo_label.pack()
        except:
            logo_label = tk.Label(logo_frame, text="FLAIR\nLOGO", 
                                font=('Arial', 12, 'bold'), bg='#1E1E1E', fg='white')
            logo_label.pack()
        
        title_frame = tk.Frame(top_frame, bg='')
        title_frame.pack(side=tk.LEFT, expand=True)
        
        self.title_label = tk.Label(title_frame, text="FLAIR CONTROL DECK [Enhanced Parser + Serial]", 
                              font=('Bahnschrift', 24, 'bold'), bg='#1E1E1E', fg='#00FFFF')
        self.title_label.pack()
        
        mode_frame = tk.Frame(top_frame, bg='', width=100)
        mode_frame.pack(side=tk.RIGHT, padx=10)
        
        tk.Label(mode_frame, text="MODE", font=('Segoe UI', 10, 'bold'), 
                bg='#1E1E1E', fg='#FFD700').pack()
        self.mode_toggle = tk.Button(mode_frame, text="🌙", font=('Arial', 16), 
                                     command=self.toggle_mode, width=3, bg='#404040', fg='white',
                                     relief=tk.RAISED, bd=2)
        self.mode_toggle.pack()
        
        msg_frame = tk.Frame(content_container, bg='')
        msg_frame.pack(fill=tk.X, padx=10, pady=5)
        
        tk.Label(msg_frame, text="Message:", font=('Segoe UI', 10, 'bold'), 
                bg='#1E1E1E', fg='#FFD700').pack(side=tk.LEFT)
        self.message_box = tk.Entry(msg_frame, font=('Calibri', 10), width=80, 
                                    bg='#2D2D2D', fg='white', insertbackground='white')
        self.message_box.pack(side=tk.LEFT, padx=5)
        
        content_frame = tk.Frame(content_container, bg='')
        content_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
        
        left_panel = tk.Frame(content_frame, bg='#1A1A1A', width=200, relief=tk.RAISED, bd=2)
        left_panel.pack(side=tk.LEFT, fill=tk.Y, padx=5)
        left_panel.pack_propagate(False)
        
        self.create_control_buttons(left_panel)
        
        right_panel = tk.Frame(content_frame, bg='#1E1E1E', relief=tk.RAISED, bd=2)
        right_panel.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=5)
        
        self.create_graphs(right_panel)
        
    def draw_gradient_background(self):
        """Draw gradient background on canvas"""
        self.bg_canvas.delete("gradient")
        width = self.root.winfo_width() if self.root.winfo_width() > 1 else 1400
        height = self.root.winfo_height() if self.root.winfo_height() > 1 else 900
        
        if self.dark_mode:
            for i in range(height):
                ratio = i / height
                r = int(10 + (30 - 10) * ratio)
                color = f'#{r:02x}{r:02x}{r:02x}'
                self.bg_canvas.create_line(0, i, width, i, fill=color, tags="gradient")
        else:
            for i in range(height):
                ratio = i / height
                r = int(232 + (212 - 232) * ratio)
                g = int(220 + (196 - 220) * ratio)
                b = int(200 + (176 - 200) * ratio)
                color = f'#{r:02x}{g:02x}{b:02x}'
                self.bg_canvas.create_line(0, i, width, i, fill=color, tags="gradient")
        
    def create_control_buttons(self, parent):
        button_configs = [
            ("CONNECT WiFi", self.connect_wifi, "#1E90FF", "#0066CC"),
            ("CONNECT Serial", self.connect_serial, "#9370DB", "#663399"),
            ("START", self.start_mission, "#32CD32", "#228B22"),
            ("END", self.end_mission, "#FF4500", "#CC3300"),
            ("SAVE", self.save_data, "#FFD700", "#CC9900"),
        ]
        
        for text, command, color1, color2 in button_configs:
            btn_frame = tk.Frame(parent, bg=color1, relief=tk.RAISED, bd=3)
            btn_frame.pack(fill=tk.X, pady=8, padx=10)
            
            btn = tk.Button(btn_frame, text=text, command=command, 
                          font=('Courier New', 10, 'bold'), 
                          bg=color1, fg='white', activebackground=color2,
                          activeforeground='white', relief=tk.FLAT, bd=0, height=2)
            btn.pack(fill=tk.BOTH, expand=True)
        
        tk.Label(parent, text="Time (UTC)", font=('Segoe UI', 10, 'bold'), 
                bg='#1A1A1A', fg='#FFD700').pack(pady=(15, 5))
        self.time_label = tk.Label(parent, text="--:--:--", font=('Consolas', 12, 'bold'), 
                                   bg='#0D0D0D', fg='#00FF00', relief=tk.SUNKEN, bd=3)
        self.time_label.pack(fill=tk.X, pady=5, padx=10)
        
        tk.Label(parent, text="Mission Time", font=('Segoe UI', 10, 'bold'), 
                bg='#1A1A1A', fg='#FFD700').pack(pady=(15, 5))
        self.mission_time_label = tk.Label(parent, text="00:00:00", font=('Consolas', 12, 'bold'), 
                                          bg='#0D0D0D', fg='#00FF00', relief=tk.SUNKEN, bd=3)
        self.mission_time_label.pack(fill=tk.X, pady=5, padx=10)
        
        tk.Label(parent, text="Packets", font=('Segoe UI', 10, 'bold'), 
                bg='#1A1A1A', fg='#FFD700').pack(pady=(15, 5))
        self.packet_label = tk.Label(parent, text="RX: 0\nFail: 0", font=('Consolas', 10, 'bold'), 
                                     bg='#0D0D0D', fg='#00FFFF', relief=tk.SUNKEN, bd=3, justify=tk.LEFT)
        self.packet_label.pack(fill=tk.X, pady=5, padx=10)
        
        tk.Label(parent, text="Connection", font=('Segoe UI', 10, 'bold'), 
                bg='#1A1A1A', fg='#FFD700').pack(pady=(15, 5))
        self.conn_label = tk.Label(parent, text="WiFi: ❌\nSerial: ❌", font=('Consolas', 9, 'bold'), 
                                   bg='#0D0D0D', fg='#FF4500', relief=tk.SUNKEN, bd=3, justify=tk.LEFT)
        self.conn_label.pack(fill=tk.X, pady=5, padx=10)
        
    def create_graphs(self, parent):
        self.fig = Figure(figsize=(12, 8), facecolor='#2D2D2D')
        
        self.ax1 = self.fig.add_subplot(3, 3, 1)
        self.ax2 = self.fig.add_subplot(3, 3, 2)
        self.ax3 = self.fig.add_subplot(3, 3, 4)
        self.ax4 = self.fig.add_subplot(3, 3, 5)
        self.ax5 = self.fig.add_subplot(3, 3, 7)
        self.ax6 = self.fig.add_subplot(3, 3, 3)
        self.ax7 = self.fig.add_subplot(3, 3, 8)
        
        self.axes = [self.ax1, self.ax2, self.ax3, self.ax4, self.ax5, self.ax6, self.ax7]
        
        for ax in self.axes:
            ax.set_facecolor('#2D2D2D')
            ax.spines['bottom'].set_color('white')
            ax.spines['top'].set_color('white')
            ax.spines['left'].set_color('white')
            ax.spines['right'].set_color('white')
            ax.tick_params(colors='white')
        
        self.ax1.set_title('Wind Speed vs Altitude', fontsize=11, fontweight='bold', color='white')
        self.ax1.set_xlabel('Wind Speed (m/s)', fontsize=9, color='white')
        self.ax1.set_ylabel('Altitude (m)', fontsize=9, color='white')
        
        self.ax2.set_title('Temperature vs Altitude', fontsize=11, fontweight='bold', color='white')
        self.ax2.set_xlabel('Temperature (C)', fontsize=9, color='white')
        self.ax2.set_ylabel('Altitude (m)', fontsize=9, color='white')
        
        self.ax3.set_title('Pressure vs Altitude', fontsize=11, fontweight='bold', color='white')
        self.ax3.set_xlabel('Pressure (hPa)', fontsize=9, color='white')
        self.ax3.set_ylabel('Altitude (m)', fontsize=9, color='white')
        
        self.ax4.set_title('Relative Humidity vs Altitude', fontsize=11, fontweight='bold', color='white')
        self.ax4.set_xlabel('Humidity (%)', fontsize=9, color='white')
        self.ax4.set_ylabel('Altitude (m)', fontsize=9, color='white')
        
        self.ax5.set_title('Isotope (permil) vs Altitude', fontsize=11, fontweight='bold', color='white')
        self.ax5.set_xlabel('Isotope (permil)', fontsize=9, color='white')
        self.ax5.set_ylabel('Altitude (m)', fontsize=9, color='white')
        
        self.ax6.set_title('Boundary Layer Status', fontsize=11, fontweight='bold', color='white')
        self.ax6.axis('off')
        
        self.ax7.set_title('Wind Direction', fontsize=11, fontweight='bold', color='white')
        self.ax7.set_aspect('equal')
        self.ax7.axis('off')
        
        self.fig.tight_layout()
        
        self.canvas = FigureCanvasTkAgg(self.fig, parent)
        self.canvas.draw()
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)
        
    def toggle_mode(self):
        self.dark_mode = not self.dark_mode
        
        if self.dark_mode:
            bg_color = '#1E1E1E'
            fg_color = 'white'
            graph_bg = '#2D2D2D'
            time_bg = '#0D0D0D'
            time_fg = '#00FF00'
            entry_bg = '#2D2D2D'
            title_fg = '#00FFFF'
            self.mode_toggle.config(text="🌙", bg='#404040', fg='white')
            self.title_label.config(bg='#1E1E1E', fg=title_fg)
        else:
            bg_color = '#D4C4B0'
            fg_color = 'black'
            graph_bg = '#F5F5DC'
            time_bg = '#FFFACD'
            time_fg = '#006400'
            entry_bg = 'white'
            title_fg = '#000080'
            self.mode_toggle.config(text="☀", bg='#FFEB3B', fg='black')
            self.title_label.config(bg='#D4C4B0', fg=title_fg)
        
        self.draw_gradient_background()
        self.time_label.config(bg=time_bg, fg=time_fg)
        self.mission_time_label.config(bg=time_bg, fg=time_fg)
        self.packet_label.config(bg=time_bg, fg='#00FFFF' if self.dark_mode else '#0000CD')
        self.conn_label.config(bg=time_bg, fg='#00FF00' if self.dark_mode else '#006400')
        self.message_box.config(bg=entry_bg, fg=fg_color, insertbackground=fg_color)
        
        self.fig.set_facecolor(graph_bg)
        for ax in self.axes:
            ax.set_facecolor(graph_bg)
            ax.spines['bottom'].set_color(fg_color)
            ax.spines['top'].set_color(fg_color)
            ax.spines['left'].set_color(fg_color)
            ax.spines['right'].set_color(fg_color)
            ax.tick_params(colors=fg_color)
            ax.xaxis.label.set_color(fg_color)
            ax.yaxis.label.set_color(fg_color)
            ax.title.set_color(fg_color)
        
        self.canvas.draw()
    
    def connect_serial(self):
        """Connect to Serial port"""
        if self.serial_connected:
            self.message_box.delete(0, tk.END)
            self.message_box.insert(0, "Already connected to Serial")
            return
        
        ports = SerialReceiver.list_ports()
        
        if not ports:
            self.message_box.delete(0, tk.END)
            self.message_box.insert(0, "No serial ports available")
            return
        
        port_window = tk.Toplevel(self.root)
        port_window.title("Select Serial Port")
        port_window.geometry("300x200")
        port_window.configure(bg='#1E1E1E')
        
        tk.Label(port_window, text="Select COM Port:", 
                font=('Segoe UI', 11, 'bold'), bg='#1E1E1E', fg='white').pack(pady=10)
        
        port_var = tk.StringVar(value=ports[0])
        port_dropdown = ttk.Combobox(port_window, textvariable=port_var, values=ports, 
                                     state='readonly', font=('Calibri', 10))
        port_dropdown.pack(pady=10)
        
        def confirm_connect():
            selected_port = port_var.get()
            port_window.destroy()
            
            self.message_box.delete(0, tk.END)
            self.message_box.insert(0, f"Connecting to {selected_port}...")
            
            success, message = self.serial_receiver.connect(selected_port)
            
            if success:
                self.serial_connected = True
                self.message_box.delete(0, tk.END)
                self.message_box.insert(0, message)
                self.update_connection_status()
                
                self.serial_thread = Thread(target=self.serial_loop, daemon=True)
                self.serial_thread.start()
            else:
                self.message_box.delete(0, tk.END)
                self.message_box.insert(0, message)
        
        tk.Button(port_window, text="Connect", command=confirm_connect,
                 font=('Courier New', 10, 'bold'), bg='#32CD32', fg='white',
                 relief=tk.RAISED, bd=3).pack(pady=20)
        
    def serial_loop(self):
        """Main Serial receiving loop"""
        self.serial_receiver.running = True
        
        while self.serial_receiver.running and self.serial_connected:
            try:
                line = self.serial_receiver.read_line()
                
                if line:
                    try:
                        decoded_line = line.decode('utf-8').strip()
                        data, status = PacketParser.parse_serial_string(decoded_line)
                        
                        if data:
                            self.data_queue.put(data)
                            self.total_packets_received += 1
                        else:
                            self.failed_packets += 1
                            print(f"Serial parse failed: {status}")
                    except Exception as e:
                        self.failed_packets += 1
                        print(f"Serial decode error: {e}")
                        
            except Exception as e:
                print(f"Serial loop error: {e}")
        
        self.serial_receiver.disconnect()
        self.serial_connected = False
        self.update_connection_status()
        
    def connect_wifi(self):
        """Connect to WiFi router receiving RF data"""
        if self.wifi_connected:
            self.message_box.delete(0, tk.END)
            self.message_box.insert(0, "Already connected to WiFi")
            return
        
        self.message_box.delete(0, tk.END)
        self.message_box.insert(0, "Connecting to WiFi router...")
        
        success, message = self.wifi_receiver.connect()
        
        if success:
            self.wifi_connected = True
            self.message_box.delete(0, tk.END)
            self.message_box.insert(0, message)
            self.update_connection_status()
            
            self.wifi_thread = Thread(target=self.wifi_loop, daemon=True)
            self.wifi_thread.start()
        else:
            self.message_box.delete(0, tk.END)
            self.message_box.insert(0, message)
    
    def wifi_loop(self):
        """Main WiFi receiving loop"""
        self.wifi_receiver.running = True
        
        while self.wifi_receiver.running and self.wifi_connected:
            try:
                conn, msg = self.wifi_receiver.accept_connection()
                
                if conn:
                    self.wifi_connection = conn
                    self.message_box.delete(0, tk.END)
                    self.message_box.insert(0, msg)
                    
                    while self.wifi_receiver.running:
                        data = self.wifi_receiver.receive_data(conn)
                        if data:
                            self.data_queue.put(data)
                            self.total_packets_received += 1
                        else:
                            self.failed_packets += 1
                            break
                    
                    conn.close()
                    self.message_box.delete(0, tk.END)
                    self.message_box.insert(0, "Router disconnected. Waiting for reconnection...")
            except Exception as e:
                print(f"WiFi loop error: {e}")
        
        self.wifi_receiver.disconnect()
        self.wifi_connected = False
        self.update_connection_status()
    
    def update_connection_status(self):
        """Update connection status display"""
        wifi_status = "✅" if self.wifi_connected else "❌"
        serial_status = "✅" if self.serial_connected else "❌"
        
        self.conn_label.config(text=f"WiFi: {wifi_status}\nSerial: {serial_status}")
    
    def start_mission(self):
        if not self.mission_running:
            self.mission_running = True
            self.mission_start_time = datetime.now()
            self.message_box.delete(0, tk.END)
            self.message_box.insert(0, "Mission started - Receiving telemetry packets")
            
            if not self.wifi_connected and not self.serial_connected:
                self.generate_test_data()
                self.streaming_test_data = True
                self.stream_test_data()
    
    def end_mission(self):
        if self.mission_running:
            self.mission_running = False
            self.streaming_test_data = False
            self.message_box.delete(0, tk.END)
            self.message_box.insert(0, "Mission ended")
            
            self.save_data()
    
    def generate_test_data(self):
        """Generate comprehensive test data buffer"""
        self.message_box.delete(0, tk.END)
        self.message_box.insert(0, "Generating test data - Simulating live atmospheric profile")
        
        altitudes = np.arange(0, 18005, 5)
        self.test_data_buffer = []
        
        for alt in altitudes:
            data_point = {}
            data_point['altitude'] = alt
            
            base_temp = 15.0
            
            if alt < 100:
                temp = base_temp - (alt / 1000.0) * 6.5 + np.random.randn() * 0.5
            elif alt < 105:
                temp = base_temp - (alt / 1000.0) * 6.5 + np.random.randn() * 0.4
            elif alt < 3000:
                temp = base_temp - (alt / 1000.0) * 6.5
                if 1000 <= alt <= 1500:
                    temp += (alt - 1000) * 0.008
                temp += np.random.randn() * 0.4
            elif alt < 5000:
                temp = base_temp - (alt / 1000.0) * 6.5
                temp += np.random.randn() * 0.3
            else:
                temp = base_temp - (alt / 1000.0) * 6.5
                if alt > 11000:
                    temp = -56.5 + np.random.randn() * 0.2
                else:
                    temp += np.random.randn() * 0.3
            
            data_point['temperature'] = temp
            
            base_pressure = 1013.25
            pressure = base_pressure * np.exp(-alt / 8500.0)
            pressure += np.random.randn() * 0.5
            data_point['pressure'] = pressure
            
            if alt < 100:
                humidity = 70 + np.random.randn() * 5
            elif alt < 105:
                humidity = 68 + np.random.randn() * 4
            elif alt < 3000:
                humidity = 70 - (alt - 105) * 0.015 + np.random.randn() * 4
            elif alt < 5000:
                humidity = 20 - (alt - 3000) * 0.005 + np.random.randn() * 2
            else:
                humidity = 10 - (alt - 5000) * 0.0005 + np.random.randn() * 1
            humidity = np.clip(humidity, 0.5, 95)
            data_point['humidity'] = humidity
            
            if alt < 10:
                wind = 1.5 + np.random.randn() * 0.8
            elif alt < 100:
                wind = 1.5 + (alt - 10) * 0.03 + np.random.randn() * 1.0
            elif alt < 105:
                wind = 4.2 + np.random.randn() * 1.0
            elif alt < 3000:
                wind = 4.5 + (alt - 105) * 0.004 + np.random.randn() * 1.2
            elif alt < 5000:
                wind = 18 + (alt - 3000) * 0.003 + np.random.randn() * 1.5
            else:
                wind = 23 + (alt - 5000) * 0.003
                if 9000 <= alt <= 12000:
                    wind += 15 * np.exp(-((alt - 10500)**2) / (1500**2))
                wind += np.random.randn() * 2.0
            wind = max(0.1, wind)
            data_point['wind_speed'] = wind
            
            if alt < 100:
                base_direction = 45
                direction = base_direction + np.random.randn() * 25
            elif alt < 105:
                base_direction = 50
                direction = base_direction + np.random.randn() * 20
            elif alt < 3000:
                base_direction = 50 + (alt - 105) * 0.025
                direction = base_direction + np.random.randn() * 15
            elif alt < 5000:
                base_direction = 120 + (alt - 3000) * 0.02
                direction = base_direction + np.random.randn() * 10
            else:
                base_direction = 270
                direction = base_direction + 30 * np.sin(alt / 2000.0) + np.random.randn() * 8
            direction = direction % 360
            data_point['wind_direction'] = direction
            
            isotope = -8.0 - (alt / 1000.0) * 2.5
            if alt < 3000:
                isotope += np.random.randn() * 0.8
            else:
                isotope += np.random.randn() * 0.4
            if alt > 10000:
                isotope -= (alt - 10000) / 1000.0
            data_point['isotope'] = isotope
            
            self.test_data_buffer.append(data_point)
        
        self.test_data_index = 0
        self.message_box.delete(0, tk.END)
        self.message_box.insert(0, f"Ready to stream {len(self.test_data_buffer)} packets - LIVE MODE")
    
    def stream_test_data(self):
        """Stream test data one point at a time"""
        if not self.streaming_test_data or not self.mission_running:
            return
        
        if self.test_data_index < len(self.test_data_buffer):
            data_point = self.test_data_buffer[self.test_data_index]
            
            self.altitude_data.append(data_point['altitude'])
            self.temp_data.append(data_point['temperature'])
            self.pressure_data.append(data_point['pressure'])
            self.humidity_data.append(data_point['humidity'])
            self.wind_speed_data.append(data_point['wind_speed'])
            self.wind_direction_data.append(data_point['wind_direction'])
            self.isotope_data.append(data_point['isotope'])
            
            self.test_data_index += 1
            self.total_packets_received += 1
            
            progress = (self.test_data_index / len(self.test_data_buffer)) * 100
            current_alt = data_point['altitude']
            self.message_box.delete(0, tk.END)
            self.message_box.insert(0, f"Streaming: {progress:.1f}% | Alt: {current_alt:.1f}m | Pkt {self.test_data_index}/{len(self.test_data_buffer)}")
            
            self.root.after(self.test_data_interval, self.stream_test_data)
        else:
            self.streaming_test_data = False
            self.message_box.delete(0, tk.END)
            self.message_box.insert(0, f"Test data complete - {len(self.altitude_data)} packets processed")
    
    def save_data(self):
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        folder_name = f"flair_mission_{timestamp}"
        os.makedirs(folder_name, exist_ok=True)
        
        try:
            csv_configs = [
                ('wind_speed', self.wind_speed_data, 'Wind Speed (m/s)'),
                ('wind_direction', self.wind_direction_data, 'Wind Direction (deg)'),
                ('temperature', self.temp_data, 'Temperature (C)'),
                ('pressure', self.pressure_data, 'Pressure (hPa)'),
                ('humidity', self.humidity_data, 'Humidity (%)'),
                ('isotope', self.isotope_data, 'Isotope (permil)')
            ]
            
            for name, data, header in csv_configs:
                if len(data) > 0:
                    csv_filename = os.path.join(folder_name, f"{name}_data_{timestamp}.csv")
                    with open(csv_filename, 'w', newline='', encoding='utf-8') as csvfile:
                        writer = csv.writer(csvfile)
                        writer.writerow(['Altitude (m)', header])
                        for i in range(len(data)):
                            writer.writerow([self.altitude_data[i], data[i]])
            
            dashboard_filename = os.path.join(folder_name, f"complete_dashboard_{timestamp}.png")
            self.fig.savefig(dashboard_filename, dpi=300, bbox_inches='tight',
                           facecolor=self.fig.get_facecolor())
            
            self.message_box.delete(0, tk.END)
            self.message_box.insert(0, f"Data saved to: {folder_name}")
        except Exception as e:
            self.message_box.delete(0, tk.END)
            self.message_box.insert(0, f"Save error: {str(e)}")
    
    def update_data(self):
        if self.mission_running:
            while not self.data_queue.empty():
                try:
                    data = self.data_queue.get_nowait()
                    self.altitude_data.append(data.get('altitude', 0))
                    self.temp_data.append(data.get('temperature', 0))
                    self.pressure_data.append(data.get('pressure', 0))
                    self.humidity_data.append(data.get('humidity', 0))
                    self.wind_speed_data.append(data.get('wind_speed', 0))
                    self.wind_direction_data.append(data.get('wind_direction', 0))
                    self.isotope_data.append(data.get('isotope', 0))
                except queue.Empty:
                    break
            
            if len(self.altitude_data) > 0:
                self.update_plots()
        
        self.packet_label.config(text=f"RX: {self.total_packets_received}\nFail: {self.failed_packets}")
        
        self.root.after(100, self.update_data)
    
    def draw_compass(self, ax, direction):
        """Draw wind direction compass"""
        ax.clear()
        ax.set_xlim(-1.5, 1.5)
        ax.set_ylim(-1.5, 1.5)
        ax.set_aspect('equal')
        ax.axis('off')
        
        fg_color = 'white' if self.dark_mode else 'black'
        compass_color = '#00FFFF' if self.dark_mode else '#0000CD'
        arrow_color = '#FF4500'
        
        circle = Circle((0, 0), 1, fill=False, edgecolor=compass_color, linewidth=3)
        ax.add_patch(circle)
        
        cardinal_points = [
            (0, 1.2, 'N'), (1.2, 0, 'E'), (0, -1.2, 'S'), (-1.2, 0, 'W')
        ]
        for x, y, label in cardinal_points:
            ax.text(x, y, label, ha='center', va='center', 
                   fontsize=14, fontweight='bold', color=fg_color)
        
        intermediate = [
            (0.85, 0.85, 'NE'), (0.85, -0.85, 'SE'), 
            (-0.85, -0.85, 'SW'), (-0.85, 0.85, 'NW')
        ]
        for x, y, label in intermediate:
            ax.text(x, y, label, ha='center', va='center', 
                   fontsize=10, color=fg_color, alpha=0.7)
        
        angle_rad = np.radians(90 - direction)
        arrow_length = 0.8
        dx = arrow_length * np.cos(angle_rad)
        dy = arrow_length * np.sin(angle_rad)
        
        arrow = FancyArrow(0, 0, dx, dy, width=0.15, 
                          head_width=0.3, head_length=0.2,
                          color=arrow_color, zorder=10)
        ax.add_patch(arrow)
        
        ax.text(0, -1.45, f'{direction:.1f}°', ha='center', va='top',
               fontsize=12, fontweight='bold', color=arrow_color,
               bbox=dict(boxstyle='round', facecolor='black' if self.dark_mode else 'white', 
                        alpha=0.7, edgecolor=arrow_color))
        
        ax.set_title('Wind Direction', fontsize=11, fontweight='bold', color=fg_color)
    
    def update_plots(self):
        color = '#00FFFF' if self.dark_mode else '#0000FF'
        
        for ax in self.axes:
            ax.clear()
        
        boundary_results = None
        if len(self.altitude_data) > 10:
            boundary_results = BoundaryLayerAnalyzer.analyze_boundary_layer(
                np.array(self.altitude_data),
                np.array(self.temp_data),
                np.array(self.pressure_data),
                np.array(self.humidity_data),
                np.array(self.wind_speed_data)
            )
        
        # Define layer boundaries
        srl_top = 100
        transition1_start = 100
        transition1_end = 105
        pbl_start = 105
        pbl_top = 3000
        transition2_start = 3000
        transition2_end = 5000
        ft_start = 5000
        max_alt = max(self.altitude_data) if self.altitude_data else 18000
        
        if max_alt <= 0:
            max_alt = 100
        
        def add_layer_shading(ax):
            # SRL: 0-100m
            ax.axhspan(0, srl_top, alpha=0.15, color='brown', 
                      label=f'SRL (0-{srl_top}m)', zorder=1)
            # Transition Zone 1: 100-105m
            ax.axhspan(transition1_start, transition1_end, alpha=0.12, color='orange', 
                      label=f'Trans 1 ({transition1_start}-{transition1_end}m)', zorder=1)
            # PBL: 105-3000m
            ax.axhspan(pbl_start, pbl_top, alpha=0.15, color='green', 
                      label=f'PBL ({pbl_start}-{pbl_top}m)', zorder=1)
            # Transition Zone 2: 3000-5000m
            if max_alt > 3000:
                ax.axhspan(transition2_start, transition2_end, alpha=0.12, color='yellow', 
                          label=f'Trans 2 ({transition2_start}-{transition2_end}m)', zorder=1)
            # FT: 5000-18000m
            if max_alt > 5000:
                ax.axhspan(ft_start, min(max_alt * 1.1, 18000), alpha=0.15, color='blue', 
                          label=f'FT ({ft_start}-18000m)', zorder=1)
            ax.legend(loc='upper right', fontsize=6, framealpha=0.8)
        
        if len(self.wind_speed_data) > 0:
            add_layer_shading(self.ax1)
            self.ax1.plot(self.wind_speed_data, self.altitude_data, color=color, linewidth=2, zorder=5)
        self.ax1.set_title('Wind Speed vs Altitude', fontsize=11, fontweight='bold')
        self.ax1.set_xlabel('Wind Speed (m/s)', fontsize=9)
        self.ax1.set_ylabel('Altitude (m)', fontsize=9)
        self.ax1.grid(True, alpha=0.3)
        self.ax1.set_ylim(0, max(max_alt, 100))
        
        if len(self.temp_data) > 0:
            add_layer_shading(self.ax2)
            self.ax2.plot(self.temp_data, self.altitude_data, color=color, linewidth=2, zorder=5)
        self.ax2.set_title('Temperature vs Altitude', fontsize=11, fontweight='bold')
        self.ax2.set_xlabel('Temperature (C)', fontsize=9)
        self.ax2.set_ylabel('Altitude (m)', fontsize=9)
        self.ax2.grid(True, alpha=0.3)
        self.ax2.set_ylim(0, max(max_alt, 100))
        
        if len(self.pressure_data) > 0:
            add_layer_shading(self.ax3)
            self.ax3.plot(self.pressure_data, self.altitude_data, color=color, linewidth=2, zorder=5)
        self.ax3.set_title('Pressure vs Altitude', fontsize=11, fontweight='bold')
        self.ax3.set_xlabel('Pressure (hPa)', fontsize=9)
        self.ax3.set_ylabel('Altitude (m)', fontsize=9)
        self.ax3.grid(True, alpha=0.3)
        self.ax3.set_ylim(0, max(max_alt, 100))
        
        if len(self.humidity_data) > 0:
            add_layer_shading(self.ax4)
            self.ax4.plot(self.humidity_data, self.altitude_data, color=color, linewidth=2, zorder=5)
        self.ax4.set_title('Relative Humidity vs Altitude', fontsize=11, fontweight='bold')
        self.ax4.set_xlabel('Humidity (%)', fontsize=9)
        self.ax4.set_ylabel('Altitude (m)', fontsize=9)
        self.ax4.grid(True, alpha=0.3)
        self.ax4.set_ylim(0, max(max_alt, 100))
        
        if len(self.isotope_data) > 0:
            add_layer_shading(self.ax5)
            self.ax5.plot(self.isotope_data, self.altitude_data, color=color, linewidth=2, zorder=5)
        self.ax5.set_title('Isotope (permil) vs Altitude', fontsize=11, fontweight='bold')
        self.ax5.set_xlabel('Isotope (permil)', fontsize=9)
        self.ax5.set_ylabel('Altitude (m)', fontsize=9)
        self.ax5.grid(True, alpha=0.3)
        self.ax5.set_ylim(0, max(max_alt, 100))
        
        # Boundary Layer Status
        self.ax6.clear()
        self.ax6.axis('off')
        self.ax6.set_title('Boundary Layer Status', fontsize=11, fontweight='bold')
        
        fg_color = 'white' if self.dark_mode else 'black'
        
        if len(self.altitude_data) > 10:
            current_altitude = self.altitude_data[-1]
            
            layer_text = ""
            layer_color = 'green'
            
            if current_altitude < 100:
                layer_text = "SURFACE ROUGHNESS LAYER (SRL)"
                layer_color = 'brown'
                description = f"Altitude: {current_altitude:.1f} m\nSRL: 0-100 m\n\nSurface layer with turbulence\ndominated by friction and\nmechanical mixing."
            elif current_altitude < 105:
                layer_text = "TRANSITION ZONE 1"
                layer_color = 'orange'
                description = f"Altitude: {current_altitude:.1f} m\nTransition: 100-105 m\n\nTransitioning from surface\nroughness layer to\nplanetary boundary layer."
            elif current_altitude < 3000:
                layer_text = "PLANETARY BOUNDARY LAYER (PBL)"
                layer_color = 'green'
                description = f"Altitude: {current_altitude:.1f} m\nPBL: 105-3000 m\n\nMixed layer influenced by\nsurface processes and\ndiurnal heating."
            elif current_altitude < 5000:
                layer_text = "TRANSITION ZONE 2"
                layer_color = 'yellow'
                description = f"Altitude: {current_altitude:.1f} m\nTransition: 3000-5000 m\n\nTransitioning between\nboundary layer and\nfree troposphere."
            elif current_altitude >= 5000:
                layer_text = "FREE TROPOSPHERE (FT)"
                layer_color = 'blue'
                description = f"Altitude: {current_altitude:.1f} m\nFT: 5000+ m\n\nFree atmosphere above\nboundary layer influence\nwith synoptic-scale flow."
            else:
                layer_text = "ATMOSPHERIC LAYER"
                layer_color = 'green'
                description = f"Altitude: {current_altitude:.1f} m\n\nAnalyzing..."
            
            self.ax6.text(0.5, 0.75, layer_text, 
                        ha='center', va='center', fontsize=11, 
                        fontweight='bold', color=layer_color,
                        transform=self.ax6.transAxes)
            
            self.ax6.text(0.5, 0.35, description,
                        ha='center', va='center', fontsize=8,
                        transform=self.ax6.transAxes,
                        color=fg_color)
            
            if boundary_results and boundary_results['inversion_detected']:
                self.ax6.text(0.5, 0.1, "⚠ Inversion Detected",
                            ha='center', va='center', fontsize=9,
                            color='red', fontweight='bold',
                            transform=self.ax6.transAxes)
        else:
            self.ax6.text(0.5, 0.5, "Collecting data...\n\nNeed more points for\nboundary layer analysis",
                        ha='center', va='center', fontsize=10,
                        transform=self.ax6.transAxes, color=fg_color)
        
        # Wind direction compass
        if len(self.wind_direction_data) > 0:
            current_wind_dir = self.wind_direction_data[-1]
            self.draw_compass(self.ax7, current_wind_dir)
        else:
            self.ax7.clear()
            self.ax7.axis('off')
            self.ax7.text(0.5, 0.5, "Waiting for\nwind direction data",
                         ha='center', va='center', fontsize=10,
                         transform=self.ax7.transAxes, color=fg_color)
        
        # Apply color scheme
        graph_bg = '#2D2D2D' if self.dark_mode else '#F5F5DC'
        
        for ax in self.axes:
            ax.set_facecolor(graph_bg)
            ax.spines['bottom'].set_color(fg_color)
            ax.spines['top'].set_color(fg_color)
            ax.spines['left'].set_color(fg_color)
            ax.spines['right'].set_color(fg_color)
            ax.tick_params(colors=fg_color)
            if hasattr(ax, 'xaxis'):
                ax.xaxis.label.set_color(fg_color)
            if hasattr(ax, 'yaxis'):
                ax.yaxis.label.set_color(fg_color)
            ax.title.set_color(fg_color)
        
        self.fig.tight_layout()
        self.canvas.draw()
    
    def update_time(self):
        """Update time displays"""
        current_time = datetime.now().strftime("%H:%M:%S")
        self.time_label.config(text=current_time)
        
        if self.mission_running and self.mission_start_time:
            elapsed = datetime.now() - self.mission_start_time
            hours, remainder = divmod(int(elapsed.total_seconds()), 3600)
            minutes, seconds = divmod(remainder, 60)
            mission_time = f"{hours:02d}:{minutes:02d}:{seconds:02d}"
            self.mission_time_label.config(text=mission_time)
        
        self.root.after(1000, self.update_time)

if __name__ == "__main__":
    root = tk.Tk()
    root.withdraw()
    
    logo_path = r"C:\Users\adhi1\OneDrive\Desktop\CU Boulder Aerospace Books and Resources\CU Boulder Graduate Project ASEN5018\logo.png"
    splash = SplashScreen(root, logo_path, duration=3000)
    
    def show_main_window():
        root.deiconify()
        app = FLAIRControlDeck(root)
    
    root.after(3100, show_main_window)
    root.mainloop()
