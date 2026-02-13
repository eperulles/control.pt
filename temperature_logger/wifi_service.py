import socket
import time

class WifiService:
    def __init__(self, ip="192.168.1.15", port=8080):
        self.ip = ip
        self.port = port
        self.sock = None

    def update_ip(self, new_ip):
        self.ip = new_ip

    def connect(self):
        try:
            print(f"Connecting to {self.ip}:{self.port}...")
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sock.settimeout(5)
            self.sock.connect((self.ip, self.port))
            print("Connected!")
            return True
        except Exception as e:
            print(f"Connection failed: {e}")
            self.sock = None
            return False

    def start_measurement(self):
        # The ESP32 sends data continuously once connected, 
        # but we can flush the buffer to ensure fresh data
        pass

    def get_latest_temp(self):
        if not self.sock:
            return None
        try:
            data = self.sock.recv(1024).decode('utf-8').strip()
            if data:
                # The ESP sends strings like "25.50"
                # If multiple arrive "25.50\r\n25.51", take the last one
                lines = data.split('\n')
                last_line = lines[-1].strip()
                return float(last_line)
        except Exception as e:
            print(f"Read error: {e}")
            return None
        return None

    def stop_measurement(self):
        # Determine average or just return last read
        # For this simple implementation, we just disconnect.
        # But main.py logic expects an average.
        # We'll rely on get_latest_temp loop in main.py to calculate average there 
        # OR main.py expects this method to return the final average?
        # Checking main.py:
        # avg = wifi_svc.stop_measurement() ? No, main.py calculates avg?
        # Let's check main.py logic in a second.
        # For now, let's implement a simple average tracker inside here if needed.
        # Actually, main.py loop:
        # for i in range(steps): ... val = wifi_svc.get_latest_temp() ...
        # final = wifi_svc.stop_measurement()
        # So stop_measurement SHOULD return the final value (or average).
        
        # Let's act smart: We will capture the last valid temp in get_latest_temp
        pass
        return 0.0 # Placeholder, main.py seems to use the returned value as FINAL.

    def disconnect(self):
        if self.sock:
            try:
                self.sock.close()
            except:
                pass
            self.sock = None

# Refined Logic to match main.py expectations
# main.py expects get_latest_temp() to return a float to update UI
# and stop_measurement() to return the FINAL average.

class WifiService:
    def __init__(self, ip, port=8080):
        self.ip = ip
        self.port = port
        self.sock = None
        self.readings = []

    def update_ip(self, new_ip):
        self.ip = new_ip

    def connect(self):
        self.readings = []
        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sock.settimeout(3)
            self.sock.connect((self.ip, int(self.port)))
            return True
        except Exception as e:
            print(f"WifiService Connect Error: {e}")
            return False

    def start_measurement(self):
        self.readings = []

    def get_latest_temp(self):
        if not self.sock: return None
        try:
            # Non-blocking read or short timeout
            self.sock.settimeout(1.0) 
            data = self.sock.recv(256)
            if not data: return None
            
            text = data.decode(errors='ignore').strip()
            # Handle piled up data "23.5\r\n23.6"
            lines = text.split()
            if not lines: return None
            
            # Take last valid float
            val = None
            for l in reversed(lines):
                try:
                    val = float(l)
                    self.readings.append(val)
                    break
                except:
                    continue
            return val
        except:
            return None

    def stop_measurement(self):
        # Calculate Average
        if not self.readings:
            return 0.0
        avg = sum(self.readings) / len(self.readings)
        return avg

    def disconnect(self):
        if self.sock:
            self.sock.close()
            self.sock = None
