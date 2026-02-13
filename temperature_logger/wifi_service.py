import socket
import threading
import time
import random
import re

class MockWifi:
    """Simulates a TCP connection for testing."""
    def __init__(self, ip, port, timeout=1):
        self.is_open = True
        self.ip = ip
        self.port = port
        self.buffer = []
        self._simulate_data()

    def _simulate_data(self):
        """Generates fake temperature data in a background thread."""
        def run():
            while self.is_open:
                # Format: Raw: 26.50 °C	Corr: 26.30 °C	CJ: 27.00 °C	Fault: 0
                # Adding some random fluctuation
                base_temp = 350.0 # soldering iron temp approx
                variation = random.uniform(-1, 1)
                temp = base_temp + variation
                raw = temp + 0.5
                line = f"Raw: {raw:.2f} °C\tCorr: {temp:.2f} °C\tCJ: 27.00 °C\tFault: 0\r\n"
                self.buffer.append(line.encode('utf-8'))
                time.sleep(0.7) # Arduino delay is 700ms
        
        t = threading.Thread(target=run, daemon=True)
        t.start()

    def recv(self, bufsize):
        if self.buffer:
            return self.buffer.pop(0)
        time.sleep(0.1)
        return b""

    def close(self):
        self.is_open = False

class WifiService:
    def __init__(self, ip="192.168.1.100", port=8080, mock=False):
        self.ip = ip
        self.port = int(port)
        self.mock = mock
        self.sock = None
        self.is_running = False
        self.latest_temp = None # Latest valid temperature
        self.collecting = False
        self.collected_temps = []
        self.thread = None

    def connect(self):
        try:
            if self.mock:
                print(f"Connecting to MOCK WiFi {self.ip}:{self.port}...")
                self.sock = MockWifi(self.ip, self.port)
            else:
                print(f"Connecting to real WiFi {self.ip}:{self.port}...")
                self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                self.sock.settimeout(2)
                self.sock.connect((self.ip, self.port))
            
            self.is_running = True
            self.thread = threading.Thread(target=self._read_loop, daemon=True)
            self.thread.start()
            return True
        except Exception as e:
            print(f"Error connecting to WiFi: {e}")
            self.sock = None
            return False

    def disconnect(self):
        self.is_running = False
        if self.sock:
            try:
                self.sock.close()
            except:
                pass
            self.sock = None

    def update_ip(self, ip):
        self.ip = ip

    def _read_loop(self):
        while self.is_running and self.sock:
            try:
                # Basic line reading from socket
                # Note: This is a simplified reader. In production networks, 
                # you'd want a proper buffer handling for partial packets.
                # For this specific ESP32 firmware that sends \r\n, this usually works "okay" 
                # if packets arrive complete or we buffer slightly.
                # Let's use a small buffer and accumulate.
                
                # However, for simplicity and since we control the sender (ESP32 `client.println`), 
                # a small buffer read loop mimicking readline is safest.
                line_str = self._socket_readline()
                if line_str:
                     self._parse_line(line_str)
            except Exception as e:
                # print(f"Read error: {e}") # Verbose
                time.sleep(1)

    def _socket_readline(self):
        """Helper to read a line from socket."""
        line = b''
        while self.is_running and self.sock:
            try:
                if self.mock:
                    chunk = self.sock.recv(1024)
                    if not chunk: return None
                    return chunk.decode('utf-8').strip()
                else:
                    self.sock.settimeout(1.0)
                    chunk = self.sock.recv(1)
                    if not chunk:
                         return None
                    line += chunk
                    if line.endswith(b'\n'):
                        return line.decode('utf-8').strip()
            except socket.timeout:
                continue
            except Exception:
                return None
        return None

    def _parse_line(self, line):
        # Format 1 (Old): Raw: 26.50 °C	Corr: 26.30 °C	CJ: 27.00 °C	Fault: 0
        # Format 2 (New): 26.30
        
        if not line: return
        
        # Try simple float first (New format)
        try:
            temp = float(line.strip())
            self.latest_temp = temp
            if self.collecting:
                self.collected_temps.append(temp)
            return
        except ValueError:
            pass
            
        # Try Regex (Old format)
        match = re.search(r"Corr:\s*([0-9\.]+)", line)
        if match:
            try:
                temp = float(match.group(1))
                self.latest_temp = temp
                if self.collecting:
                    self.collected_temps.append(temp)
            except ValueError:
                pass

    def start_measurement(self):
        self.collected_temps = []
        self.collecting = True

    def stop_measurement(self):
        self.collecting = False
        if not self.collected_temps:
            return 0.0
        return sum(self.collected_temps) / len(self.collected_temps)

    def get_latest_temp(self):
        return self.latest_temp
