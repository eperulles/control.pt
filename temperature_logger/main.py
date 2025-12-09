import flet as ft
import os

# Safe imports with fallbacks
try:
    from wifi_service import WifiService
except Exception as e:
    print(f"WiFi service not available: {e}")
    WifiService = None

try:
    from data_manager import DataManager
except Exception as e:
    print(f"DataManager not available: {e}")
    DataManager = None

try:
    import time
    import threading
except Exception as e:
    print(f"Threading not available: {e}")
    time = None
    threading = None

try:
    import socket
except Exception as e:
    print(f"Socket not available: {e}")
    socket = None

try:
    from PIL import Image
except Exception as e:
    print(f"PIL not available: {e}")
    Image = None

try:
    from datetime import datetime
except Exception as e:
    print(f"datetime not available: {e}")
    datetime = None

# Removed top-level import to prevent startup crash on Android
decode = None

def main(page: ft.Page):
    print("Iniciando aplicación Flet...")
    page.title = "Registro de Temperatura de Cautines (WiFi)"
    page.theme_mode = ft.ThemeMode.LIGHT
    page.window_width = 800
    page.window_height = 600

    # Dependencies - WiFi service will be initialized on-demand to prevent Android startup crash
    wifi_svc = None  # Created when measurement starts
    
    # Database can be initialized safely
    db_mgr = None
    if DataManager:
        try:
            db_mgr = DataManager()
        except Exception as e:
            print(f"Failed to initialize DataManager: {e}")
    
    # State Variables
    selected_line = ft.Ref[ft.Dropdown]()
    ip_address = ft.Ref[ft.TextField]()
    code_source = ft.Ref[ft.TextField]()
    code_handle = ft.Ref[ft.TextField]()
    status_text = ft.Ref[ft.Text]()
    progress_bar = ft.Ref[ft.ProgressBar]()
    current_temp_display = ft.Ref[ft.Text]()
    btn_start = ft.Ref[ft.ElevatedButton]()
    
    # History DataTable
    history_table = ft.DataTable(
        columns=[
            ft.DataColumn(ft.Text("Fecha/Hora")),
            ft.DataColumn(ft.Text("Línea")),
            ft.DataColumn(ft.Text("Fuente")),
            ft.DataColumn(ft.Text("Maneral")),
            ft.DataColumn(ft.Text("Temp. Promedio (°C)")),
        ],
        rows=[],
    )

    def load_history():
        history_table.rows.clear()
        data = db_mgr.get_recent_measurements()
        for row in data:
            history_table.rows.append(
                ft.DataRow(cells=[
                    ft.DataCell(ft.Text(row[0])),
                    ft.DataCell(ft.Text(row[1])),
                    ft.DataCell(ft.Text(row[2])),
                    ft.DataCell(ft.Text(row[3])),
                    ft.DataCell(ft.Text(f"{row[4]:.2f}")),
                ])
            )
        page.update()

    # --- DASHBOARD LOGIC ---
    dashboard_line = ft.Ref[ft.Dropdown]()
    dashboard_source = ft.Ref[ft.Dropdown]() 
    dashboard_handle = ft.Ref[ft.Dropdown]() 
    
    chart_data_points = []
    chart_series = ft.LineChartData(
        data_points=chart_data_points,
        stroke_width=3,
        color=ft.Colors.BLUE,
        curved=True,
        stroke_cap_round=True,
    )
    
    chart = ft.LineChart(
        data_series=[chart_series],
        border=ft.Border(
            bottom=ft.BorderSide(4, ft.Colors.with_opacity(0.5, ft.Colors.ON_SURFACE))
        ),
        left_axis=ft.ChartAxis(
            labels=[ft.ChartAxisLabel(value=50*i, label=ft.Text(f"{50*i}")) for i in range(11)],
            labels_size=40,
        ),
        tooltip_bgcolor=ft.Colors.with_opacity(0.8, ft.Colors.BLUE_GREY),
        min_y=0,
        max_y=500,
        expand=True,
    )
    
    def refresh_dashboard_filters():
        """Reloads unique sources and handles from DB."""
        sources, handles = db_mgr.get_unique_codes()
        
        # Preserve selection if still valid, else "Todas"
        curr_source = dashboard_source.current.value
        curr_handle = dashboard_handle.current.value
        
        dashboard_source.current.options = [ft.dropdown.Option("Todas")] + [ft.dropdown.Option(s) for s in sources]
        dashboard_handle.current.options = [ft.dropdown.Option("Todas")] + [ft.dropdown.Option(h) for h in handles]
        
        # Reset if not in new list
        if curr_source not in sources and curr_source != "Todas": dashboard_source.current.value = "Todas"
        if curr_handle not in handles and curr_handle != "Todas": dashboard_handle.current.value = "Todas"
        
        page.update()

    def update_dashboard(e=None):
        line_filter = dashboard_line.current.value
        source_filter = dashboard_source.current.value
        handle_filter = dashboard_handle.current.value
        
        if not line_filter: line_filter = "Todas"
        if not source_filter: source_filter = "Todas"
        if not handle_filter: handle_filter = "Todas"
        
        # 1. Get Data
        s_arg = source_filter if source_filter != "Todas" else None
        h_arg = handle_filter if handle_filter != "Todas" else None
        rows = db_mgr.get_filtered_measurements(line_filter if line_filter != "Todas" else None, s_arg, h_arg)
        
        # 2. Process for Chart
        points = []
        temps = []
        timestamps = []
        
        for i, row in enumerate(rows):
            # row: [timestamp, temperature]
            ts = row[0] 
            temp = row[1]
            points.append(ft.LineChartDataPoint(i, temp, tooltip=f"{ts}\n{temp:.1f}°C"))
            temps.append(temp)
            timestamps.append(ts)
            
        chart_series.data_points = points
        
        # Adjust Y Axis Scale
        if temps:
            chart.min_y = max(0, min(temps) - 50)
            chart.max_y = max(temps) + 50
        else:
            chart.min_y = 0
            chart.max_y = 500
            
        # 3. Configure X Axis (Dates)
        if timestamps:
            num_points = len(timestamps)
            # Show max 6 labels evenly distributed
            labels = []
            step = max(1, num_points // 6)
            
            for i in range(0, num_points, step):
                # Try to parse date for shorter format "DD/MM HH:MM"
                try:
                    dt = datetime.strptime(timestamps[i], "%Y-%m-%d %H:%M:%S")
                    label_text = dt.strftime("%d/%m %H:%M")
                except:
                    label_text = timestamps[i]
                    
                labels.append(ft.ChartAxisLabel(value=i, label=ft.Text(label_text, size=10, weight=ft.FontWeight.BOLD)))
            
            chart.bottom_axis.labels = labels
        else:
             chart.bottom_axis.labels = []
            
        page.update()

    # --- SCANNER LOGIC ---
    # Fix: Use CWD because Flet relative path "uploads" maps to CWD/uploads
    base_dir = os.getcwd() 
    upload_dir_name = "uploads"
    upload_dir = os.path.join(base_dir, upload_dir_name)
    
    if not os.path.exists(upload_dir):
        os.makedirs(upload_dir)
        print(f"Created upload dir: {upload_dir}")
    
    # Flet SERVER needs a relative path for the mount point usually
    page.upload_dir = upload_dir_name
    print(f"Configured page.upload_dir (Relative): {page.upload_dir} -> maps to {upload_dir}")

    scan_target = None # "source" or "handle"

    def process_image(img_path):
        """Helper to decode image from path"""
        try:
            img = Image.open(img_path)
            
            # Late import to avoid startup crash on Android if dependencies missing
            global decode
            try:
                if decode is None:
                    from pyzbar.pyzbar import decode
            except Exception as e:
                print(f"Failed to import pyzbar: {e}")
                decode = None
            
            if decode:
                decoded_objects = decode(img)
                if decoded_objects:
                    code = decoded_objects[0].data.decode("utf-8")
                    
                    if scan_target == "source":
                        code_source.current.value = code
                    elif scan_target == "handle":
                        code_handle.current.value = code
                    
                    # Close image so we can delete it
                    img.close()
                    page.snack_bar = ft.SnackBar(ft.Text(f"Código detectado: {code}"), open=True)
                else:
                    img.close()
                    page.snack_bar = ft.SnackBar(ft.Text("No se detectó código en la imagen"), open=True)
            else:
                 img.close()
                 page.snack_bar = ft.SnackBar(ft.Text("Librería de escaneo no disponible"), open=True)
        except Exception as ex:
             page.snack_bar = ft.SnackBar(ft.Text(f"Error procesando imagen: {ex}"), open=True)
        finally:
            # CLEANUP: Delete the file immediately as user requested
            try:
                if os.path.exists(img_path):
                    os.remove(img_path)
            except Exception as e:
                print(f"Error deleting temp file: {e}")
                
        page.update()

    def on_upload_complete(e: ft.FilePickerUploadEvent):
        if e.file_name:
             full_path = os.path.join(upload_dir, e.file_name)
             process_image(full_path)

    def on_scan_result(e: ft.FilePickerResultEvent):
        if not e.files: return
        
        f = e.files[0]
        if f.path:
            process_image(f.path)
        else:
            # Web/Mobile Upload
            page.snack_bar = ft.SnackBar(ft.Text("Procesando..."), open=True)
            page.update()
            file_picker.upload(e.files)

    file_picker = ft.FilePicker(on_result=on_scan_result, on_upload=on_upload_complete)
    page.overlay.append(file_picker)

    def trigger_scan_source(e):
        nonlocal scan_target
        scan_target = "source"
        file_picker.pick_files(allow_multiple=False, file_type=ft.FilePickerFileType.IMAGE)

    def trigger_scan_handle(e):
        nonlocal scan_target
        scan_target = "handle"
        file_picker.pick_files(allow_multiple=False, file_type=ft.FilePickerFileType.IMAGE)


    def start_measurement_process(e):
        # Validation
        if not selected_line.current.value:
            page.snack_bar = ft.SnackBar(ft.Text("Seleccione una Línea"), open=True)
            page.update()
            return
        if not ip_address.current.value:
            page.snack_bar = ft.SnackBar(ft.Text("Ingrese la IP del ESP32"), open=True)
            page.update()
            return
        if not code_source.current.value or not code_handle.current.value:
            page.snack_bar = ft.SnackBar(ft.Text("Escanee ambos códigos"), open=True)
            page.update()
            return

        btn_start.current.disabled = True
        page.update()

        def background_task():
            nonlocal wifi_svc
            
            # Initialize WiFi service on-demand (lazy loading to prevent Android startup crash)
            if wifi_svc is None:
                try:
                    wifi_svc = WifiService(mock=True)
                except Exception as e:
                    status_text.current.value = f"Error: No se pudo inicializar WiFi: {e}"
                    btn_start.current.disabled = False
                    page.update()
                    return
            
            # 0. Connect
            wifi_svc.update_ip(ip_address.current.value)
            if "192." in ip_address.current.value or "10." in ip_address.current.value or "172." in ip_address.current.value:
                wifi_svc.mock = False
            
            status_text.current.value = "Conectando al ESP32..."
            page.update()
            
            if not wifi_svc.connect():
                status_text.current.value = "Error: No se pudo conectar a la IP."
                btn_start.current.disabled = False
                page.update()
                return

            # 1. Countdown
            for i in range(3, 0, -1):
                status_text.current.value = f"Iniciando en {i}..."
                page.update()
                time.sleep(1)
            
            status_text.current.value = "Midiendo... Coloque el cautín."
            progress_bar.current.visible = True
            progress_bar.current.value = 0
            page.update()

            # 2. Measure
            wifi_svc.start_measurement()
            duration = 10
            steps = 20 
            for i in range(steps):
                time.sleep(duration / steps)
                progress_bar.current.value = (i + 1) / steps
                curr = wifi_svc.get_latest_temp()
                if curr is not None:
                    current_temp_display.current.value = f"Temp Actual: {curr:.2f} °C"
                page.update()
            
            avg_temp = wifi_svc.stop_measurement()
            wifi_svc.disconnect() 
            
            # 3. Save
            db_mgr.add_measurement(
                selected_line.current.value,
                code_source.current.value,
                code_handle.current.value,
                avg_temp
            )

            # 4. Result
            status_text.current.value = f"Completado. Promedio: {avg_temp:.2f} °C"
            current_temp_display.current.value = f"Temp Actual: --"
            progress_bar.current.visible = False
            btn_start.current.disabled = False
            
            load_history() 
            page.update()

        threading.Thread(target=background_task, daemon=True).start()

    # Tab 1: Register
    tab_register = ft.Container(
        padding=20,
        content=ft.Column([
            ft.Text("Registro de Medición de Temperatura (WiFi)", size=24, weight=ft.FontWeight.BOLD),
            ft.Row([
                ft.Dropdown(
                    ref=selected_line,
                    label="Seleccione Línea",
                    options=[ft.dropdown.Option(f"Línea {i}") for i in range(1, 9)],
                    width=200
                ),
                ft.TextField(ref=ip_address, label="IP del Dispositivo (ej. 192.168.1.50)", width=250),
            ], alignment=ft.MainAxisAlignment.CENTER),
            
            ft.Row([
                ft.TextField(ref=code_source, label="Código Fuente", width=300),
                ft.IconButton(icon=ft.Icons.CAMERA_ALT, tooltip="Escanear Fuente", on_click=trigger_scan_source)
            ], alignment=ft.MainAxisAlignment.CENTER),
            
            ft.Row([
                ft.TextField(ref=code_handle, label="Código Maneral", width=300),
                ft.IconButton(icon=ft.Icons.CAMERA_ALT, tooltip="Escanear Maneral", on_click=trigger_scan_handle)
            ], alignment=ft.MainAxisAlignment.CENTER),

            ft.Divider(),
            ft.ElevatedButton(
                "Empezar Medición", 
                ref=btn_start,
                on_click=start_measurement_process,
                height=50,
                width=200,
            ),
            ft.Divider(),
            ft.Text("", ref=status_text, size=20, color=ft.Colors.BLUE),
            ft.ProgressBar(ref=progress_bar, width=300, visible=False),
            ft.Text("Temp Actual: --", ref=current_temp_display, size=16),
        ], horizontal_alignment=ft.CrossAxisAlignment.CENTER)
    )

    # Tab 2: History
    def on_tab_change(e):
        if e.control.selected_index == 1:
            load_history()
        elif e.control.selected_index == 2:
            refresh_dashboard_filters() # Load latest options (including newly registered ones)
            update_dashboard()

    tab_history = ft.Container(
        padding=20,
        content=ft.Column([
            ft.Text("Historial de Mediciones", size=24, weight=ft.FontWeight.BOLD),
            ft.ElevatedButton("Actualizar", on_click=lambda _: load_history()),
            ft.Column([history_table], scroll=ft.ScrollMode.AUTO, height=400)
        ])
    )

    # Tab 3: Dashboard
    tab_dashboard = ft.Container(
        padding=20,
        content=ft.Column([
            ft.Text("Dashboard de Temperaturas", size=24, weight=ft.FontWeight.BOLD),
            ft.ResponsiveRow([
                ft.Dropdown(
                    col={"sm": 4},
                    ref=dashboard_line,
                    label="Línea",
                    options=[ft.dropdown.Option("Todas")] + [ft.dropdown.Option(f"Línea {i}") for i in range(1, 9)],
                    value="Todas",
                    on_change=update_dashboard
                ),
                ft.Dropdown(
                    col={"sm": 4},
                    ref=dashboard_source,
                    label="Fuente",
                    options=[ft.dropdown.Option("Todas")],
                    value="Todas",
                    on_change=update_dashboard
                ),
                ft.Dropdown(
                    col={"sm": 4},
                    ref=dashboard_handle,
                    label="Maneral",
                    options=[ft.dropdown.Option("Todas")],
                    value="Todas",
                    on_change=update_dashboard
                ),
            ]),
            ft.ElevatedButton("Actualizar Gráfica", on_click=update_dashboard),
            ft.Container(chart, height=400, padding=10),
        ])
    )

    t = ft.Tabs(
        selected_index=0,
        animation_duration=300,
        on_change=on_tab_change,
        tabs=[
            ft.Tab(text="Registro", content=tab_register),
            ft.Tab(text="Historial", content=tab_history),
            ft.Tab(text="Dashboard", content=tab_dashboard),
        ],
        expand=1,
    )

    page.add(t)
    load_history()

    def on_window_event(e):
        if e.data == "close":
            if wifi_svc:  # Only disconnect if initialized
                wifi_svc.disconnect()
            page.window_destroy()

    page.window_prevent_close = True
    page.on_window_event = on_window_event

def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except:
        return "127.0.0.1"

if __name__ == "__main__":
    local_ip = get_local_ip()
    port = 8550
    print(f"--------------------------------------------------")
    print(f"¡APLICACIÓN WEB ACTIVA!")
    print(f"Desde tu PC:      http://localhost:{port}")
    print(f"Desde tu CELULAR: http://{local_ip}:{port}")
    print(f"--------------------------------------------------")
    
    ft.app(target=main, view=ft.AppView.WEB_BROWSER, port=port, host="0.0.0.0")
