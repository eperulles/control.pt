import sqlite3
import datetime
import os

class DataManager:
    def __init__(self, db_name="temperature_logs.db"):
        self.db_name = db_name
        self.init_db()

    def init_db(self):
        """Initialize the database table if it doesn't exist."""
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS measurements (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT,
                line TEXT,
                code_source TEXT,
                code_handle TEXT,
                temperature REAL
            )
        ''')
        conn.commit()
        conn.close()

    def add_measurement(self, line, code_source, code_handle, temperature):
        """Add a new measurement record."""
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cursor.execute('''
            INSERT INTO measurements (timestamp, line, code_source, code_handle, temperature)
            VALUES (?, ?, ?, ?, ?)
        ''', (timestamp, line, code_source, code_handle, temperature))
        conn.commit()
        conn.close()

    def get_recent_measurements(self, limit=50):
        """Retrieve recent measurements."""
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        cursor.execute('''
            SELECT timestamp, line, code_source, code_handle, temperature 
            FROM measurements 
            ORDER BY id DESC 
            LIMIT ?
        ''', (limit,))
        rows = cursor.fetchall()
        conn.close()
        return rows

    def get_filtered_measurements(self, line=None, code_source=None, code_handle=None):
        """Retrieve measurements for dashboard with filters."""
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        
        query = "SELECT timestamp, temperature FROM measurements"
        conditions = []
        params = []
        
        if line and line != "Todas":
            conditions.append("line = ?")
            params.append(line)
        if code_source:
             conditions.append("code_source LIKE ?")
             params.append(f"%{code_source}%")
        if code_handle:
             conditions.append("code_handle LIKE ?")
             params.append(f"%{code_handle}%")
             
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
            
        query += " ORDER BY id ASC" # Ascending for charts
        
        cursor.execute(query, tuple(params))
        rows = cursor.fetchall()
        conn.close()
        return rows

    def get_unique_codes(self):
        """Get unique sources and handles for dropdowns."""
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        
        cursor.execute("SELECT DISTINCT code_source FROM measurements WHERE code_source IS NOT NULL AND code_source != ''")
        sources = [row[0] for row in cursor.fetchall()]
        
        cursor.execute("SELECT DISTINCT code_handle FROM measurements WHERE code_handle IS NOT NULL AND code_handle != ''")
        handles = [row[0] for row in cursor.fetchall()]
        
        conn.close()
        return sources, handles
