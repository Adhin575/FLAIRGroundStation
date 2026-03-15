FLAIR Control Deck (an INSTAAR Lab Initiative) is a comprehensive ground station software for real-time atmospheric telemetry visualization and boundary layer analysis. This system processes data from high-altitude balloon or UAV missions, providing live visualization of atmospheric parameters including temperature, pressure, humidity, wind speed/direction, and isotope ratios.

Features
Multi-Source Data Reception

WiFi Connection: Receives RF telemetry data via WiFi router (TCP/IP on port 5000)
Serial Communication: Direct serial port connection at 9600 baud
Multiple Packet Formats:

Structured binary packets with Fletcher-16 checksum
Comma-separated serial strings
JSON format (fallback)


Simultaneous Connections: Can receive data from both WiFi and Serial simultaneously

Supported Packet Types

TELEMETRY_POSITION (0x10): GPS data (latitude, longitude, altitude)

TELEMETRY_PRESSURE (0x11): Atmospheric data (pressure, temperature, humidity)

TELEMETRY_SYSTEM (0x13): System time data

PAYLOAD_CHANNEL_0 (0x20): Custom payload messages

GENERIC_ATMOSPHERIC (0x30): Combined atmospheric parameters

Real-Time Visualization

7 Live Graphs:

Wind Speed vs Altitude

Temperature vs Altitude

Pressure vs Altitude

Relative Humidity vs Altitude

Isotope (δ¹⁸O) vs Altitude

Boundary Layer Status Display

Wind Direction Compass



Atmospheric Layer Detection
Automatically identifies and color-codes atmospheric layers:

SRL (Surface Roughness Layer): 10-100m - Orange

PBL (Planetary Boundary Layer): 100-4000m - Green

Transition Zone: 4000-5000m - Yellow

FT (Free Troposphere): 5000-18000m - Blue

Advanced Analysis

Real-time boundary layer height detection
Temperature inversion identification
Wind shear analysis
Mixing height calculation
Potential temperature gradient computation

Data Input Formats
1. Structured Binary Packet Format
[0]      HDR0: 0x55 ("U")
[1]      HDR1: 0x24 ("$")
[2]      Type: 0x00-0xFF
[3]      Action: 0x00-0xFF
[4-5]    Size: uint16 (big-endian)
[6...n-2] Data
[n-1, n]  Fletcher-16 Checksum
2. Serial String Format (9600 baud)
"altitude,wind_speed,temperature,humidity\n"
Example:
3496.39,0.09,15.0,0.5316
Fields:

Field 1: Altitude (meters)
Field 2: Wind Speed (m/s)
Field 3: Temperature (°C)
Field 4: Humidity (0-1 range, auto-converted to %)

3. JSON Format (Fallback)
json{
  "altitude": 3496.39,
  "temperature": 15.0,
  "pressure": 850.5,
  "humidity": 53.16,
  "wind_speed": 0.09,
  "wind_direction": 45.0,
  "isotope": -8.5
}
🎮 User Interface
Control Buttons

CONNECT WiFi: Establish WiFi connection (listens on port 5000)
CONNECT Serial: Select and connect to serial port
START: Begin mission and data collection
END: Stop mission and auto-save data
SAVE: Manually save current data

Display Panels

Time (UTC): Current system time
Mission Time: Elapsed time since mission start
Packets: Received/Failed packet statistics
Connection: WiFi and Serial connection status (✅/❌)

Themes

Dark Mode (default): 🌙 Optimized for low-light environments
Light Mode: ☀️ High-contrast daytime viewing

Toggle with the MODE button in the top-right corner.
📊 Data Export
Data is automatically saved when ending a mission or can be manually saved at any time.
Output Structure
flair_mission_YYYYMMDD_HHMMSS/
├── wind_speed_data_YYYYMMDD_HHMMSS.csv
├── wind_direction_data_YYYYMMDD_HHMMSS.csv
├── temperature_data_YYYYMMDD_HHMMSS.csv
├── pressure_data_YYYYMMDD_HHMMSS.csv
├── humidity_data_YYYYMMDD_HHMMSS.csv
├── isotope_data_YYYYMMDD_HHMMSS.csv
└── complete_dashboard_YYYYMMDD_HHMMSS.png
CSV Format
Each CSV file contains two columns:
csvAltitude (m), Parameter Value
0.0, 15.2
5.0, 15.1
10.0, 15.0
...
🧪 Test Mode
If no WiFi or Serial connection is available, the system automatically generates synthetic test data when START is pressed.
Test Data Specifications

Altitude Range: 0-18,000m
Resolution: 5m steps (3,601 data points)
Update Rate: 100ms per point
Realistic Profiles:

Standard atmospheric lapse rate
Boundary layer inversion (1000-1500m)
Jet stream simulation (9000-12000m)
Tropopause effects (>11,000m)
Wind veering with altitude
Isotope depletion trends



🔬 Atmospheric Layer Analysis
Detection Algorithm
The system uses multiple methods to identify atmospheric layers:

Potential Temperature Gradient: Detects inversions (∂θ/∂z > 0.005 K/m)
Wind Shear Analysis: Identifies layer boundaries via velocity gradients
Default Thresholds: Falls back to standard atmospheric heights

Layer Definitions
LayerAltitude RangeCharacteristicsSRL10-100mSurface friction, mechanical turbulencePBL100-4000mMixed layer, diurnal variationTransition4000-5000mGradual change between PBL and FTFT5000-18000mFree atmosphere, minimal surface influence
⚙️ Configuration
Network Settings
python# WiFi Configuration
host = '0.0.0.0'  # Listen on all interfaces
port = 5000       # TCP port

# Modify in WiFiReceiver class initialization
self.wifi_receiver = WiFiReceiver(host='0.0.0.0', port=5000)
Serial Settings
python# Serial Configuration
baudrate = 9600   # Baud rate
timeout = 1.0     # Read timeout (seconds)

# Modify in SerialReceiver class initialization
self.serial_receiver = SerialReceiver(baudrate=9600)
Test Data Settings
python# Test data streaming interval
self.test_data_interval = 100  # milliseconds per data point

# Altitude range and resolution
altitudes = np.arange(0, 18005, 5)  # 0-18000m in 5m steps
🐛 Troubleshooting
Serial Port Not Found

Windows: Check Device Manager for COM port number
Linux/Mac: Check /dev/ttyUSB* or /dev/ttyACM*
Install serial drivers if necessary

WiFi Connection Issues

Ensure router is configured to forward data to port 5000
Check firewall settings (allow incoming TCP on port 5000)
Verify network connectivity

Missing Dependencies
bash# If matplotlib not found
pip install matplotlib

# If PIL/Pillow not found  
pip install Pillow

# If serial not found
pip install pyserial
Display Issues

Fullscreen: Press F11 or Escape to toggle
Graph not updating: Check if mission is started
Logo not loading: Update logo path or remove logo loading code

📝 Packet Structure Details
Fletcher-16 Checksum Algorithm
pythondef calculate_fletcher16_checksum(data):
    sum1 = 0
    sum2 = 0
    for byte in data:
        sum1 = (sum1 + byte) % 255
        sum2 = (sum2 + sum1) % 255
    return (sum2 << 8) | sum1
Data Parsing Priority

Serial string format (comma-separated)
Structured binary packet (with headers 0x55, 0x24)
JSON format (UTF-8 encoded)
Alternate binary format (uint8 arrays)

🎯 Use Cases

Atmospheric Research: Boundary layer studies, temperature inversions
Meteorology: Wind profiling, humidity gradients
High-Altitude Balloons: Real-time telemetry monitoring
UAV Missions: Atmospheric data collection
Climate Studies: Isotope ratio analysis
Educational: Teaching atmospheric physics
