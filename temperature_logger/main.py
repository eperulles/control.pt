import flet as ft
import os
import time
import threading

# Safe imports
try:
    from wifi_service import WifiService
except:
    WifiService = None

# SUPABASE CONNECTION
try:
    from data_manager_supabase import DataManager
except:
    DataManager = None

SUPABASE_URL = "https://vbejrmebpumgutawrggy.supabase.co"
SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InZiZWpybWVicHVtZ3V0YXdyZ2d5Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3NjUzODIwODUsImV4cCI6MjA4MDk1ODA4NX0.nzvnzc6J_yicBZcG0JOi3OZ6NBOLOUYdm5Ekj1GSpXo"

try:
    from PIL import Image
    from pyzbar.pyzbar import decode
except:
    Image = None
    decode = None

ADMIN_PASSWORD = "Procesos69"

def main(page: ft.Page):
    page.title = "Sistema de Control de Temperatura (NUBE)"
    page.theme_mode = ft.ThemeMode.LIGHT
    page.padding = 0
    page.bgcolor = "#f0f2f5"

    # --- STATE ---
    wifi_svc = None 
    db_mgr = None
    if DataManager:
        try:
            db_mgr = DataManager(SUPABASE_URL, SUPABASE_KEY)
            print("Conectado a Supabase CLOUD")
        except Exception as e:
            print(f"Error conectando a Nube: {e}")


    # --- TAB 1: OPERATOR ---
    # Controls
    dd_line = ft.Dropdown(
        label="Línea",
        options=[ft.dropdown.Option(f"Línea {i}") for i in range(1, 9)], # 8 Lines
        value="Línea 1",
        filled=True,
        bgcolor="white",
    )
    tf_ip = ft.TextField(label="IP Dispositivo", value="10.130.99.187", filled=True, bgcolor="white")

    tf_source = ft.TextField(label="Fuente de Poder", expand=True, filled=True, bgcolor="white", text_size=16)
    tf_handle = ft.TextField(label="Maneral", expand=True, filled=True, bgcolor="white", text_size=16)

    txt_display = ft.Text(value="-- °C", size=50, weight=ft.FontWeight.BOLD, color=ft.Colors.BLUE_GREY_800)
    txt_status = ft.Text(value="Listo para medir", size=16, italic=True)
    pb = ft.ProgressBar(width=300, color="orange", visible=False)
    
    btn_start = ft.ElevatedButton(
        text="INICIAR PROCESO",
        icon=ft.Icons.PLAY_ARROW,
        style=ft.ButtonStyle(
            color="white",
            bgcolor="#1976d2",
            padding=20,
            shape=ft.RoundedRectangleBorder(radius=8),
        ),
        width=250
    )

    # Scanner Logic
    base_dir = os.getcwd()
    upload_dir = os.path.join(base_dir, "uploads")
    if not os.path.exists(upload_dir):
        os.makedirs(upload_dir)
    page.upload_dir = "uploads"
    
    scan_target = None

    def process_scan(path):
        try:
            if Image and decode:
                img = Image.open(path)
                objs = decode(img)
                if objs:
                    code = objs[0].data.decode("utf-8")
                    if scan_target == "source":
                        tf_source.value = code
                    elif scan_target == "handle":
                        tf_handle.value = code
                    page.snack_bar = ft.SnackBar(ft.Text("Código detectado"))
                    page.snack_bar.open = True
                img.close()
        except:
            pass
        finally:
            try:
                if os.path.exists(path): os.remove(path)
            except:
                pass
            page.update()

    def on_upload(e: ft.FilePickerUploadEvent):
        if e.file_name:
            process_scan(os.path.join(upload_dir, e.file_name))

    picker = ft.FilePicker(on_upload=on_upload)
    page.overlay.append(picker)

    def scan_s(e):
        nonlocal scan_target
        scan_target = "source"
        picker.pick_files(allow_multiple=False, file_type=ft.FilePickerFileType.IMAGE)

    def scan_h(e):
        nonlocal scan_target
        scan_target = "handle"
        picker.pick_files(allow_multiple=False, file_type=ft.FilePickerFileType.IMAGE)

    # Measurement Logic
    def run_measurement(e):
        nonlocal wifi_svc
        
        # Validation
        if not dd_line.value or not tf_source.value or not tf_handle.value:
             page.snack_bar = ft.SnackBar(ft.Text("⚠️ Error: Selecciona Línea, Fuente y Maneral para iniciar."), bgcolor="red")
             page.snack_bar.open = True
             page.update()
             return

        target_ip = tf_ip.value
        
        # Connect
        if not wifi_svc:
            try:
                wifi_svc = WifiService(ip=target_ip, port=8080)
            except:
                pass
        
        if wifi_svc: wifi_svc.update_ip(target_ip)

        btn_start.disabled = True
        page.update()

        def worker():
            # 1. 3-Second Countdown
            for i in range(3, 0, -1):
                txt_status.value = f"Iniciando en {i}..."
                page.update()
                time.sleep(1)
            
            # 2. Connect
            txt_status.value = "Conectando..."
            page.update()
            
            if wifi_svc and wifi_svc.connect():
                # 3. Measure (10s)
                txt_status.value = "Midiendo..."
                pb.visible = True
                page.update()
                
                wifi_svc.start_measurement()
                steps = 20
                for i in range(steps):
                    time.sleep(10 / steps)
                    pb.value = (i+1)/steps
                    val = wifi_svc.get_latest_temp()
                    if val:
                        txt_display.value = f"{val:.2f} °C"
                    page.update()
                
                final = wifi_svc.stop_measurement()
                wifi_svc.disconnect()
                
                # 4. Save
                if db_mgr:
                    db_mgr.add_measurement(dd_line.value, tf_source.value, tf_handle.value, final)
                
                txt_status.value = f"Finalizado: {final:.2f} °C"
                txt_display.value = f"{final:.2f} °C"
            else:
                txt_status.value = "Error de Conexión"
            
            pb.visible = False
            btn_start.disabled = False
            # Clear inputs
            tf_source.value = ""
            tf_handle.value = ""
            page.update()

        threading.Thread(target=worker, daemon=True).start()

    btn_start.on_click = run_measurement

    # UI Construction - Tab 1
    view_operator = ft.Container(
        padding=30,
        content=ft.Column([
            ft.Card(
                elevation=5,
                content=ft.Container(
                    padding=20,
                    content=ft.Column([
                        ft.Text("Configuración", size=20, weight="bold"),
                        ft.Row([tf_ip, dd_line], alignment="spaceBetween"),
                    ])
                )
            ),
            ft.Container(height=20),
            ft.Card(
                elevation=5,
                content=ft.Container(
                    padding=20,
                    content=ft.Column([
                        ft.Text("Identificación", size=20, weight="bold"),
                        ft.Row([tf_source, ft.IconButton(ft.Icons.CAMERA_ALT, icon_size=30, on_click=scan_s)]),
                        ft.Row([tf_handle, ft.IconButton(ft.Icons.CAMERA_ALT, icon_size=30, on_click=scan_h)]),
                    ])
                )
            ),
            ft.Container(height=20),
            ft.Card(
                elevation=5,
                color="#e3f2fd",
                content=ft.Container(
                    padding=30,
                    content=ft.Column([
                        ft.Text("Medición", size=20, weight="bold"),
                        txt_display,
                        pb,
                        txt_status,
                        btn_start
                    ], horizontal_alignment="center")
                )
            )
        ], scroll="auto")
    )

    # --- TAB 2: DASHBOARD ---
    # Filters
    f_line = ft.Dropdown(label="Filtro Línea", options=[ft.dropdown.Option("Todas")] + [ft.dropdown.Option(f"Línea {i}") for i in range(1,9)], value="Todas", expand=True)
    f_source = ft.Dropdown(label="Filtro Fuente", options=[], expand=True) # Populated dynamically
    f_handle = ft.Dropdown(label="Filtro Maneral", options=[], expand=True)

    chart = ft.LineChart(
        data_series=[],
        bottom_axis=ft.ChartAxis(labels_interval=1),
        left_axis=ft.ChartAxis(labels_size=50, title=ft.Text("Temp °C")),
        height=250,
        min_y=0,
        max_y=400, # Assuming standard soldering range, adjustable
        tooltip_bgcolor=ft.Colors.with_opacity(0.8, ft.Colors.BLUE_GREY),
    )

    table = ft.DataTable(
        columns=[
            ft.DataColumn(ft.Text("ID")),
            ft.DataColumn(ft.Text("Fecha")),
            ft.DataColumn(ft.Text("Línea")),
            ft.DataColumn(ft.Text("Fuente")),
            ft.DataColumn(ft.Text("Maneral")),
            ft.DataColumn(ft.Text("Temp")),
            ft.DataColumn(ft.Text("Acción")),
        ],
        rows=[]
    )

    # Function to actual delete
    def delete_item(rid):
        print(f"DEBUG: delete_item logic for ID {rid}")
        if db_mgr:
            db_mgr.execute_query("DELETE FROM measurements WHERE id=?", (rid,))
            page.snack_bar = ft.SnackBar(ft.Text("Registro eliminado"))
            page.snack_bar.open = True
            refresh_dashboard(None)
        else:
            print("db_mgr is None")

    def ask_delete(e, rid):
        print(f"DEBUG: Trash Icon Clicked for ID {rid}")
        
        # Define controls LOCALLY to avoid reuse issues
        tf_pass = ft.TextField(label="Contraseña", password=True, autofocus=True)
        
        def on_conf(e):
            print(f"DEBUG: Confirm clicked. Value: {tf_pass.value}")
            if tf_pass.value == ADMIN_PASSWORD:
                # Close Dialog manually
                dlg.open = False
                page.update()
                # Run Logic
                delete_item(rid)
            else:
                tf_pass.error_text = "Incorrecta"
                page.update()

        dlg = ft.AlertDialog(
            title=ft.Text("Confirmar Borrado"),
            content=tf_pass,
            actions=[ft.TextButton("Confirmar", on_click=on_conf)]
        )
        
        # USE PAGE.OPEN (Modern Flet)
        try:
            page.open(dlg)
        except AttributeError:
            # Fallback for older Flet
            page.dialog = dlg
            dlg.open = True
            page.update()
            
        print("DEBUG: Dialog Open command sent VIA page.open/dialog")

    def refresh_dashboard(e):
        # 1. Update Filters Options
        if db_mgr:
            srcs, hndls = db_mgr.get_unique_codes()
            f_source.options = [ft.dropdown.Option("Todas")] + [ft.dropdown.Option(s) for s in srcs]
            f_handle.options = [ft.dropdown.Option("Todas")] + [ft.dropdown.Option(h) for h in hndls]
        
        # 2. Build Query
        query = "SELECT id, timestamp, line, code_source, code_handle, temperature FROM measurements WHERE 1=1"
        params = []
        if f_line.value and f_line.value != "Todas":
            query += " AND line = ?"
            params.append(f_line.value)
        if f_source.value and f_source.value != "Todas":
            query += " AND code_source = ?"
            params.append(f_source.value)
        if f_handle.value and f_handle.value != "Todas":
            query += " AND code_handle = ?"
            params.append(f_handle.value)
        
        query += " ORDER BY timestamp DESC LIMIT 50"
        
        # 3. Execute & Fill Table
        table.rows.clear()
        chart_data_pts = [] # For Line Chart
        if db_mgr:
            rows = db_mgr.execute_query(query, tuple(params))
            for i, r in enumerate(rows):
                rid = r[0]
                temp = r[5]
                table.rows.append(ft.DataRow(cells=[
                    ft.DataCell(ft.Text(str(r[0]))),
                    ft.DataCell(ft.Text(str(r[1]))),
                    ft.DataCell(ft.Text(str(r[2]))),
                    ft.DataCell(ft.Text(str(r[3]))),
                    ft.DataCell(ft.Text(str(r[4]))),
                    ft.DataCell(ft.Text(f"{temp:.2f}", weight="bold")),
                    ft.DataCell(ft.IconButton(ft.Icons.DELETE, icon_color="red", on_click=lambda e, rid=rid: ask_delete(e, rid))),
                ]))
                # Collect data for chart (reversed logic handled below)
                chart_data_pts.append(temp)

        # 4. Fill Chart (Limit to last 10, reversed for chronological: Left=Old, Right=New)
        # chart_data_pts currently has newest first (DESC). We need oldest first for X axis 0->N.
        recent_data = list(reversed(chart_data_pts[:10]))
        
        points = []
        for i, val in enumerate(recent_data):
            points.append(ft.LineChartDataPoint(i, val))
            
        chart.data_series = [
            ft.LineChartData(
                data_points=points,
                stroke_width=3,
                color=ft.Colors.BLUE,
                curved=True,
                stroke_cap_round=True,
            )
        ]
        
        # Set max_y dynamically for better view if needed, or keep static
        if recent_data:
             chart.max_y = max(recent_data) * 1.2
        
        page.update()

    # Triggers for filters
    f_line.on_change = refresh_dashboard
    f_source.on_change = refresh_dashboard
    f_handle.on_change = refresh_dashboard
    
    view_dashboard = ft.Container(
        padding=30,
        content=ft.Column([
             ft.Card(
                elevation=3,
                content=ft.Container(
                    padding=15,
                    content=ft.Row([f_line, f_source, f_handle, ft.IconButton(ft.Icons.REFRESH, on_click=refresh_dashboard)])
                )
            ),
            ft.Card(
                elevation=3,
                content=ft.Container(
                    padding=20,
                    content=chart
                )
            ),
            ft.Card(
                elevation=3,
                content=ft.Container(
                    padding=0,
                    content=ft.Column([table], scroll="auto")
                )
            )
        ], scroll="auto")
    )

    refresh_dashboard(None) # Initial load

    # --- MAIN LAYOUT ---
    t = ft.Tabs(
        selected_index=0,
        animation_duration=300,
        indicator_color="#1976d2",
        label_color="#1976d2",
        unselected_label_color="grey",
        tabs=[
            ft.Tab(text="REGISTRO", icon=ft.Icons.ADD_CIRCLE_OUTLINE, content=view_operator),
            ft.Tab(text="DASHBOARD", icon=ft.Icons.INSERT_CHART_OUTLINED, content=view_dashboard),
        ],
        expand=True
    )

    page.add(t)

# --- SERVER ---
import uvicorn
from fastapi import FastAPI
import flet.fastapi as flet_fastapi

app = FastAPI()
flet_app = flet_fastapi.app(main)
app.mount("/", flet_app)

if __name__ == "__main__":
    print("Iniciando Puerto 8550...")
    ft.app(target=main, view=ft.WEB_BROWSER, port=8550, host="0.0.0.0")
