import streamlit as st
import pandas as pd
import sqlite3
import gspread
import re
import os
import time
import threading
import xml.etree.ElementTree as ET
from google.oauth2.service_account import Credentials
import base64
from io import StringIO

# Configuración
SCOPE = ['https://www.googleapis.com/auth/spreadsheets']
CREDENTIALS_FILE = "ProductoTerminado.json"

# Cache extremo para máxima velocidad
@st.cache_resource
def get_google_client():
    # Intento 1: Usar archivo local (Prioridad en local para evitar errores de JWT)
    if os.path.exists(CREDENTIALS_FILE):
        try:
            creds = Credentials.from_service_account_file(CREDENTIALS_FILE, scopes=SCOPE)
            client = gspread.authorize(creds)
            return client
        except Exception as e:
            st.error(f"❌ Error cargando archivo local {CREDENTIALS_FILE}: {e}")
    
    # Intento 2: Usar st.secrets (Para Streamlit Cloud)
    try:
        if "gcp_service_account" in st.secrets:
            creds_dict = dict(st.secrets["gcp_service_account"])
            # Asegurar que los saltos de línea se procesen correctamente
            if "private_key" in creds_dict:
                creds_dict["private_key"] = creds_dict["private_key"].replace("\\n", "\n")
            
            creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPE)
            client = gspread.authorize(creds)
            return client
    except Exception as e:
        st.error(f"❌ Error con secrets: {e}")
    
    st.error(f"❌ No se econtraron credenciales válidas")
    st.stop()

@st.cache_data(ttl=600)
def load_all_data(_client, sheet_id):
    start_time = time.time()
    
    spreadsheet = _client.open_by_key(sheet_id)
    sheet = spreadsheet.sheet1
    all_values = sheet.get_all_values()
    
    # Buscar encabezados
    header_row_index = 0
    target_headers = ['CAMION', 'PALLET INICIAL', 'PALLET FINAL', 'LISTO PARA ENTREGA']
    
    for i, row in enumerate(all_values[:10]):
        row_upper = [str(cell).upper().strip() for cell in row]
        found_headers = sum(1 for target in target_headers if any(target in cell for cell in row_upper))
        if found_headers >= 2:
            header_row_index = i
            break
    
    # Crear DataFrame con headers únicos
    headers = []
    header_count = {}
    for i, cell in enumerate(all_values[header_row_index]):
        header_str = str(cell).strip()
        if not header_str:
            header_str = f"Columna_{i+1}"
        
        if header_str in header_count:
            header_count[header_str] += 1
            header_str = f"{header_str}_{header_count[header_str]}"
        else:
            header_count[header_str] = 1
        
        headers.append(header_str)
    
    data = all_values[header_row_index + 1:]
    shipment_df = pd.DataFrame(data, columns=headers)
    
    # Mapear columnas
    column_mapping = {}
    for req_col in target_headers:
        for actual_col in shipment_df.columns:
            if req_col in actual_col.upper():
                column_mapping[req_col] = actual_col
                break
    
    for req_col, actual_col in column_mapping.items():
        if actual_col in shipment_df.columns:
            col_data = shipment_df[actual_col]
            
            if isinstance(col_data, pd.DataFrame):
                shipment_df[req_col] = col_data.iloc[:, 0]
            else:
                shipment_df[req_col] = col_data
    
    shipment_df = shipment_df[list(column_mapping.keys())].copy()
    
    for col in shipment_df.columns:
        shipment_df[col] = shipment_df[col].astype(str).str.strip()
    
    shipment_df = shipment_df[shipment_df['CAMION'] != ''].reset_index(drop=True)
    
    load_time = time.time() - start_time
    return shipment_df, header_row_index, sheet, load_time

@st.cache_data
def load_packing_data(uploaded_packing):
    packing_df = pd.read_excel(uploaded_packing, sheet_name='All number')
    
    # CORREGIDO: Reemplazar fillna(method='ffill') con ffill()
    packing_df['Box number'] = packing_df['Box number'].ffill()
    packing_df['Pallet number'] = packing_df['Pallet number'].ffill()
    packing_df['Pallet number'] = packing_df['Pallet number'].astype(str).str.strip()
    
    pallet_summary = packing_df.groupby('Pallet number').agg({
        'Serial number': ['first', 'last'],
        'Box number': 'count'
    }).reset_index()
    
    pallet_summary.columns = ['Pallet number', 'first_serial', 'last_serial', 'box_count']
    
    return packing_df, pallet_summary

# ==== NUEVAS FUNCIONES MEJORADAS PARA DETECCIÓN DE CAMIONES DISPONIBLES ====

def extraer_numero_pallet(codigo):
    """Extrae el número de pallet del código escaneado"""
    try:
        # Buscar patrones comunes en códigos de pallet
        # Ejemplo: "PALLET003", "PLT003", "003", "P003", etc.
        
        # Intentar extraer números al final del código
        match = re.search(r'(\d{2,3})$', codigo)
        if match:
            return int(match.group(1))
        
        # Intentar extraer números después de "PALLET", "PLT", "P", etc.
        match = re.search(r'(?:PALLET|PLT|P)[_-]?(\d{2,3})', codigo, re.IGNORECASE)
        if match:
            return int(match.group(1))
        
        # Si no se encuentra patrón, usar los últimos 2-3 dígitos
        if len(codigo) >= 2:
            ultimos_digitos = codigo[-3:] if codigo[-3:].isdigit() else codigo[-2:]
            if ultimos_digitos.isdigit():
                return int(ultimos_digitos)
                
        return None
    except:
        return None

def detectar_camiones_del_layout():
    """Detecta automáticamente los camiones disponibles en el layout SVG"""
    if not st.session_state.layout_locations:
        return []
    
    camiones = set()
    for location in st.session_state.layout_locations:
        match = re.match(r'C(\d+)-\d+', location)
        if match:
            camiones.add(int(match.group(1)))
    
    return sorted(camiones)

def detectar_camion_disponible(truck_packing_list):
    """Detecta el primer camión disponible basado en el layout y los camiones ya usados"""
    try:
        # Obtener camiones del layout
        camiones_layout = detectar_camiones_del_layout()
        if not camiones_layout:
            return None
        
        # Obtener camiones ya usados de la base de datos
        conn = sqlite3.connect('scans.db', check_same_thread=False)
        cursor = conn.cursor()
        cursor.execute('SELECT DISTINCT camion FROM pallet_scans WHERE camion IS NOT NULL AND camion != ""')
        camiones_usados = [int(row[0]) for row in cursor.fetchall() if row[0] and row[0].isdigit()]
        conn.close()
        
        # Si el camión del packing list ya está en uso, usar ese mismo
        if truck_packing_list and truck_packing_list.isdigit():
            truck_num = int(truck_packing_list)
            if truck_num in camiones_layout:
                return f"C{truck_num}"
        
        # Buscar el primer camión disponible en el layout que no esté usado
        for camion in camiones_layout:
            if camion not in camiones_usados:
                return f"C{camion}"
        
        # Si todos los camiones están usados, usar el primero del layout
        return f"C{camiones_layout[0]}"
        
    except Exception as e:
        print(f"Error detectando camión disponible: {e}")
        # Fallback: usar C1 si hay error
        camiones_layout = detectar_camiones_del_layout()
        return f"C{camiones_layout[0]}" if camiones_layout else "C1"

def calcular_ubicacion_pallet(numero_pallet, camion):
    """Calcula la ubicación basada en el número de pallet y el camión"""
    try:
        # Cada ubicación contiene 2 pallets
        # Pallet 1 y 2 -> C1-1
        # Pallet 3 y 4 -> C1-2
        # Pallet 5 y 6 -> C1-3
        # etc.
        
        numero_ubicacion = ((numero_pallet - 1) // 2) + 1
        return f"{camion}-{numero_ubicacion}"
        
    except Exception as e:
        print(f"Error calculando ubicación: {e}")
        return f"{camion}-1"

def parse_svg_xml(xml_content):
    """Parsea un archivo SVG/XML con el layout del almacén"""
    try:
        root = ET.fromstring(xml_content)
        
        locations = []
        shapes_data = []
        
        # Buscar todos los elementos que representen ubicaciones
        namespace = '{http://www.w3.org/2000/svg}'
        
        # Rectángulos
        for rect in root.findall(f'.//{namespace}rect'):
            ubicacion = rect.get('id') or rect.get('data-ubicacion')
            if ubicacion and re.match(r'^C\d+-\d+$', ubicacion):
                locations.append(ubicacion)
                shapes_data.append({
                    'type': 'rect',
                    'ubicacion': ubicacion,
                    'x': float(rect.get('x', 0)),
                    'y': float(rect.get('y', 0)),
                    'width': float(rect.get('width', 0)),
                    'height': float(rect.get('height', 0)),
                    'fill': rect.get('fill', '#cccccc'),
                    'stroke': rect.get('stroke', '#000000')
                })
        
        # Polígonos
        for polygon in root.findall(f'.//{namespace}polygon'):
            ubicacion = polygon.get('id') or polygon.get('data-ubicacion')
            if ubicacion and re.match(r'^C\d+-\d+$', ubicacion):
                locations.append(ubicacion)
                points = polygon.get('points', '').split()
                shapes_data.append({
                    'type': 'polygon',
                    'ubicacion': ubicacion,
                    'points': points,
                    'fill': polygon.get('fill', '#cccccc'),
                    'stroke': polygon.get('stroke', '#000000')
                })
        
        # Textos (etiquetas)
        for text in root.findall(f'.//{namespace}text'):
            ubicacion = text.get('id') or text.get('data-ubicacion')
            text_content = text.text
            if ubicacion and re.match(r'^C\d+-\d+$', ubicacion):
                locations.append(ubicacion)
                shapes_data.append({
                    'type': 'text',
                    'ubicacion': ubicacion,
                    'x': float(text.get('x', 0)),
                    'y': float(text.get('y', 0)),
                    'content': text_content,
                    'fill': text.get('fill', '#000000')
                })
        
        return locations, shapes_data
    
    except Exception as e:
        st.error(f"Error parsing SVG/XML layout: {e}")
        return [], []

def generate_enhanced_svg_layout(shapes_data, pallet_assignments, selected_truck, truck_pallets, zoom_level=1.0, pan_x=0, pan_y=0):
    """Genera SVG mejorado con dos pallets por ubicación y mejor visualización"""
    # Calcular dimensiones del viewBox
    min_x, min_y, max_x, max_y = 0, 0, 1000, 1000
    
    for shape in shapes_data:
        if shape['type'] == 'rect':
            min_x = min(min_x, shape['x'])
            min_y = min(min_y, shape['y'])
            max_x = max(max_x, shape['x'] + shape['width'])
            max_y = max(max_y, shape['y'] + shape['height'])
        elif shape['type'] == 'text':
            min_x = min(min_x, shape['x'])
            min_y = min(min_y, shape['y'])
            max_x = max(max_x, shape['x'] + 50)
            max_y = max(max_y, shape['y'] + 20)
    
    width = max_x - min_x + 100
    height = max_y - min_y + 100
    
    # Aplicar zoom y pan al viewBox
    zoom_factor = 1.0 / zoom_level
    viewbox_width = width * zoom_factor
    viewbox_height = height * zoom_factor
    viewbox_x = min_x - 50 - (viewbox_width - width) / 2 + pan_x
    viewbox_y = min_y - 50 - (viewbox_height - height) / 2 + pan_y
    
    svg_content = f'<svg width="100%" height="800" viewBox="{viewbox_x} {viewbox_y} {viewbox_width} {viewbox_height}" xmlns="http://www.w3.org/2000/svg" preserveAspectRatio="xMidYMid meet">\n'
    
    # Fondo con cuadrícula para mejor referencia
    svg_content += f'<rect x="{min_x-50}" y="{min_y-50}" width="{width}" height="{height}" fill="#f0f8ff" stroke="#b0c4de" stroke-width="1"/>\n'
    
    # Dibujar cuadrícula de referencia
    grid_spacing = 50
    for x in range(int(min_x), int(max_x) + 100, grid_spacing):
        svg_content += f'<line x1="{x}" y1="{min_y-50}" x2="{x}" y2="{max_y+50}" stroke="#d3d3d3" stroke-width="0.5" stroke-dasharray="2,2"/>\n'
    for y in range(int(min_y), int(max_y) + 100, grid_spacing):
        svg_content += f'<line x1="{min_x-50}" y1="{y}" x2="{max_x+50}" y2="{y}" stroke="#d3d3d3" stroke-width="0.5" stroke-dasharray="2,2"/>\n'
    
    # Dibujar formas principales
    for shape in shapes_data:
        ubicacion = shape['ubicacion']
        
        # Determinar color según estado
        fill_color = "#e8e8e8"
        stroke_color = "#a0a0a0"
        stroke_width = "1.5"
        opacity = "0.9"
        
        # Contar pallets en esta ubicación
        pallets_in_location = []
        if ubicacion in pallet_assignments:
            assignment = pallet_assignments[ubicacion]
            if isinstance(assignment, list):
                # Múltiples pallets en esta ubicación
                pallets_in_location = assignment
            else:
                # Un solo pallet (compatibilidad hacia atrás)
                pallets_in_location = [assignment]
        
        # Buscar información de pallets para esta ubicación
        pallets_info = []
        for assignment in pallets_in_location:
            pallet_info = None
            if not truck_pallets.empty:
                for _, pallet in truck_pallets.iterrows():
                    if str(pallet['Pallet number']) == str(assignment.get('pallet', '')):
                        pallet_info = pallet
                        break
            pallets_info.append({
                'assignment': assignment,
                'info': pallet_info
            })
        
        # Determinar color basado en los pallets
        if pallets_info:
            # Verificar si alguno de los pallets pertenece al camión seleccionado
            has_current_truck_pallet = any(
                str(p['assignment'].get('camion', '')) == str(selected_truck) 
                for p in pallets_info
            )
            
            if has_current_truck_pallet:
                # Verificar si tenemos información completa
                has_complete_info = any(p['info'] is not None for p in pallets_info)
                if has_complete_info:
                    fill_color = "#dc3545"  # Rojo - Ocupado con info completa
                    stroke_color = "#a71e2a"
                else:
                    fill_color = "#ffc107"  # Amarillo - Asignado pero info incompleta
                    stroke_color = "#d39e00"
            else:
                fill_color = "#6c757d"  # Gris - Otro camión
                stroke_color = "#495057"
        elif ubicacion.startswith(f'C{selected_truck}-'):
            fill_color = "#28a745"  # Verde - Disponible para este camión
            stroke_color = "#1e7e34"
        else:
            fill_color = "#f8f9fa"  # Gris muy claro - No disponible
            stroke_color = "#dee2e6"
            opacity = "0.7"
        
        if shape['type'] == 'rect':
            # CREAR GRUPO CON TOOLTIP PARA EL RECTÁNGULO
            svg_content += f'<g>\n'
            
            # Tooltip con información completa
            tooltip_text = f"📍 Ubicación: {ubicacion}\n"
            tooltip_text += f"📦 Capacidad: 2 pallets (estiba)\n"
            
            if pallets_info:
                tooltip_text += f"🚛 Pallets asignados: {len(pallets_info)}/2\n"
                for i, pallet_data in enumerate(pallets_info):
                    assignment = pallet_data['assignment']
                    info = pallet_data['info']
                    tooltip_text += f"\n--- Pallet {i+1} ---\n"
                    tooltip_text += f"📦 Pallet: {assignment.get('pallet', 'N/A')}\n"
                    tooltip_text += f"🚛 Camión: {assignment.get('camion', 'N/A')}\n"
                    if info is not None:
                        tooltip_text += f"🔢 Serial Inicial: {info['first_serial']}\n"
                        tooltip_text += f"🔢 Serial Final: {info['last_serial']}\n"
                        tooltip_text += f"📦 Cajas: {info['box_count']}\n"
            else:
                tooltip_text += f"✅ Disponible para camión {selected_truck}"
            
            svg_content += f'<title>{tooltip_text}</title>\n'
            
            # Dibujar rectángulo principal
            svg_content += f'<rect x="{shape["x"]}" y="{shape["y"]}" width="{shape["width"]}" height="{shape["height"]}" fill="{fill_color}" stroke="{stroke_color}" stroke-width="{stroke_width}" opacity="{opacity}" rx="3" ry="3"/>\n'
            
            # Dibujar división para dos pallets (línea horizontal en el medio)
            mid_y = shape["y"] + shape["height"] / 2
            svg_content += f'<line x1="{shape["x"]}" y1="{mid_y}" x2="{shape["x"] + shape["width"]}" y2="{mid_y}" stroke="{stroke_color}" stroke-width="1" opacity="0.7"/>\n'
            
            # Indicador de cantidad de pallets (círculos pequeños)
            if pallets_info:
                occupied_count = len(pallets_info)
                for i in range(2):
                    circle_x = shape["x"] + 10 + (i * 15)
                    circle_y = shape["y"] + shape["height"] - 10
                    circle_fill = "#dc3545" if i < occupied_count else "#28a745"
                    svg_content += f'<circle cx="{circle_x}" cy="{circle_y}" r="4" fill="{circle_fill}" stroke="#ffffff" stroke-width="1"/>\n'
            
            svg_content += '</g>\n'
            
        elif shape['type'] == 'polygon':
            # CREAR GRUPO CON TOOLTIP PARA EL POLÍGONO
            svg_content += f'<g>\n'
            
            # Tooltip con información completa
            tooltip_text = f"📍 Ubicación: {ubicacion}\n"
            tooltip_text += f"📦 Capacidad: 2 pallets (estiba)\n"
            
            if pallets_info:
                tooltip_text += f"🚛 Pallets asignados: {len(pallets_info)}/2\n"
                for i, pallet_data in enumerate(pallets_info):
                    assignment = pallet_data['assignment']
                    info = pallet_data['info']
                    tooltip_text += f"\n--- Pallet {i+1} ---\n"
                    tooltip_text += f"📦 Pallet: {assignment.get('pallet', 'N/A')}\n"
                    tooltip_text += f"🚛 Camión: {assignment.get('camion', 'N/A')}\n"
                    if info is not None:
                        tooltip_text += f"🔢 Serial Inicial: {info['first_serial']}\n"
                        tooltip_text += f"🔢 Serial Final: {info['last_serial']}\n"
                        tooltip_text += f"📦 Cajas: {info['box_count']}\n"
            else:
                tooltip_text += f"✅ Disponible para camión {selected_truck}"
            
            svg_content += f'<title>{tooltip_text}</title>\n'
            points_str = " ".join(shape['points'])
            svg_content += f'<polygon points="{points_str}" fill="{fill_color}" stroke="{stroke_color}" stroke-width="{stroke_width}" opacity="{opacity}"/>\n'
            svg_content += '</g>\n'
    
    # Dibujar textos/etiquetas con mejor contraste
    for shape in shapes_data:
        if shape['type'] == 'text':
            ubicacion = shape['ubicacion']
            
            # Determinar color de fondo para contraste
            bg_color = "#e8e8e8"
            pallets_in_location = []
            if ubicacion in pallet_assignments:
                assignment = pallet_assignments[ubicacion]
                if isinstance(assignment, list):
                    pallets_in_location = assignment
                else:
                    pallets_in_location = [assignment]
            
            if pallets_in_location:
                has_current_truck = any(str(p.get('camion', '')) == str(selected_truck) for p in pallets_in_location)
                if has_current_truck:
                    bg_color = "#dc3545"
                else:
                    bg_color = "#6c757d"
            elif ubicacion.startswith(f'C{selected_truck}-'):
                bg_color = "#28a745"
            
            # Fondo para el texto
            text_bg_x = shape["x"] - 20
            text_bg_y = shape["y"] - 12
            text_bg_width = 40
            text_bg_height = 20
            svg_content += f'<rect x="{text_bg_x}" y="{text_bg_y}" width="{text_bg_width}" height="{text_bg_height}" fill="{bg_color}" opacity="0.9" rx="2" ry="2"/>\n'
            
            # Texto
            text_color = "#ffffff" if bg_color in ["#dc3545", "#6c757d", "#28a745"] else "#000000"
            svg_content += f'<text x="{shape["x"]}" y="{shape["y"]}" fill="{text_color}" font-size="{10/zoom_level}" font-weight="bold" text-anchor="middle" dominant-baseline="middle">{shape["content"] or ubicacion}</text>\n'
    
    # Leyenda mejorada
    legend_x = min_x - 30
    legend_y = max_y + 40
    
    svg_content += f'''
    <g transform="translate({legend_x}, {legend_y})">
        <rect x="0" y="0" width="120" height="80" fill="#ffffff" stroke="#dee2e6" stroke-width="1" opacity="0.9" rx="5" ry="5"/>
        
        <rect x="10" y="10" width="15" height="15" fill="#28a745" stroke="#1e7e34" stroke-width="1"/>
        <text x="30" y="20" font-size="10" fill="#000000">Disponible</text>
        
        <rect x="10" y="30" width="15" height="15" fill="#dc3545" stroke="#a71e2a" stroke-width="1"/>
        <text x="30" y="40" font-size="10" fill="#000000">Ocupado</text>
        
        <rect x="10" y="50" width="15" height="15" fill="#ffc107" stroke="#d39e00" stroke-width="1"/>
        <text x="30" y="60" font-size="10" fill="#000000">Asignado</text>
        
        <circle cx="75" cy="15" r="4" fill="#dc3545" stroke="#ffffff" stroke-width="1"/>
        <text x="85" y="17" font-size="8" fill="#000000">Pallocupado</text>
        
        <circle cx="75" cy="30" r="4" fill="#28a745" stroke="#ffffff" stroke-width="1"/>
        <text x="85" y="32" font-size="8" fill="#000000">Pallibre</text>
    </g>
    '''
    
    svg_content += '</svg>'
    return svg_content

def extract_sheet_id(url):
    patterns = [r'/spreadsheets/d/([a-zA-Z0-9-_]+)', r'id=([a-zA-Z0-9-_]+)', r'/d/([a-zA-Z0-9-_]+)']
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    return url if len(url) > 30 else None

# Inicialización de estado de sesión
if 'scanned_pallets' not in st.session_state:
    st.session_state.scanned_pallets = set()
if 'current_truck' not in st.session_state:
    st.session_state.current_truck = None
if 'truck_pallets' not in st.session_state:
    st.session_state.truck_pallets = pd.DataFrame()
if 'last_scan_time' not in st.session_state:
    st.session_state.last_scan_time = 0
if 'scanned_count' not in st.session_state:
    st.session_state.scanned_count = 0
if 'layout_locations' not in st.session_state:
    st.session_state.layout_locations = []
if 'layout_shapes' not in st.session_state:
    st.session_state.layout_shapes = []
if 'pallet_assignments' not in st.session_state:
    st.session_state.pallet_assignments = {}
if 'current_layout_type' not in st.session_state:
    st.session_state.current_layout_type = None
if 'zoom_level' not in st.session_state:
    st.session_state.zoom_level = 1.0
if 'pan_x' not in st.session_state:
    st.session_state.pan_x = 0
if 'pan_y' not in st.session_state:
    st.session_state.pan_y = 0
if 'delivered_trucks' not in st.session_state:
    st.session_state.delivered_trucks = set()
if 'camiones_layout' not in st.session_state:
    st.session_state.camiones_layout = []
if 'camion_asignado_actual' not in st.session_state:
    st.session_state.camion_asignado_actual = None

# Aplicación principal
st.title("🗺️ Sistema de Layout SVG/XML Interactivo")
st.markdown("---")

# Obtener cliente
client = get_google_client()

# Configuración del Layout
st.sidebar.header("🗺️ Configuración de Layout SVG/XML")

# Selección del tipo de layout
layout_type = st.sidebar.radio(
    "Selecciona el tipo de layout:",
    ["🖼️ SVG/XML", "📝 Texto", "🔄 Usar Layout Actual"],
    index=2 if st.session_state.current_layout_type else 0
)

if layout_type == "🖼️ SVG/XML":
    st.sidebar.subheader("Cargar Layout SVG/XML")
    
    uploaded_xml = st.sidebar.file_uploader(
        "Sube tu archivo SVG/XML",
        type=['svg', 'xml'],
        help="Archivo SVG con formas geométricas que tengan IDs como C1-1, C1-2, etc."
    )
    
    if uploaded_xml:
        if st.sidebar.button("🔄 Cargar Layout SVG/XML"):
            try:
                xml_content = uploaded_xml.getvalue().decode('utf-8')
                locations, shapes_data = parse_svg_xml(xml_content)
                
                st.session_state.layout_locations = locations
                st.session_state.layout_shapes = shapes_data
                st.session_state.current_layout_type = "svg"
                
                # Detectar camiones del layout
                st.session_state.camiones_layout = detectar_camiones_del_layout()
                
                st.sidebar.success(f"✅ Layout cargado: {len(locations)} ubicaciones")
                st.sidebar.success(f"🔄 {len(shapes_data)} formas procesadas")
                st.sidebar.success(f"🚛 Camiones detectados: {', '.join([f'C{c}' for c in st.session_state.camiones_layout])}")
                
            except Exception as e:
                st.sidebar.error(f"❌ Error cargando SVG/XML: {e}")

elif layout_type == "📝 Texto":
    st.sidebar.subheader("Cargar Layout por Texto")
    layout_text = st.sidebar.text_area(
        "Pega tu layout aquí (formato CX-Y):",
        height=150,
        help="Separar ubicaciones con tabs, comas o espacios. Ejemplo: C1-1, C1-2, C1-3"
    )
    
    if st.sidebar.button("🔄 Cargar Layout desde Texto"):
        if layout_text.strip():
            locations = []
            lines = layout_text.strip().split('\n')
            
            for line in lines:
                cells = re.split(r'\t|,|\s{2,}', line.strip())
                for cell in cells:
                    cell = cell.strip()
                    if cell and re.match(r'^C\d+-\d+$', cell):
                        locations.append(cell)
            
            # Crear formas simples para el layout de texto
            shapes_data = []
            for i, ubicacion in enumerate(locations):
                shapes_data.append({
                    'type': 'rect',
                    'ubicacion': ubicacion,
                    'x': (i % 10) * 60,
                    'y': (i // 10) * 40,
                    'width': 50,
                    'height': 30,
                    'fill': '#cccccc',
                    'stroke': '#666666'
                })
            
            st.session_state.layout_locations = locations
            st.session_state.layout_shapes = shapes_data
            st.session_state.current_layout_type = "text"
            
            # Detectar camiones del layout
            st.session_state.camiones_layout = detectar_camiones_del_layout()
            
            st.sidebar.success(f"✅ Layout cargado: {len(locations)} ubicaciones")
            st.sidebar.success(f"🚛 Camiones detectados: {', '.join([f'C{c}' for c in st.session_state.camiones_layout])}")

# Mostrar estadísticas del layout actual
if st.session_state.layout_locations:
    st.sidebar.info(f"📍 Ubicaciones cargadas: {len(st.session_state.layout_locations)}")
    
    if st.session_state.camiones_layout:
        st.sidebar.info(f"🚛 Camiones en layout: {', '.join([f'C{c}' for c in st.session_state.camiones_layout])}")

# URL input
sheet_url = st.sidebar.text_input("📋 URL Google Sheets:")

if sheet_url:
    sheet_id = extract_sheet_id(sheet_url)
    
    if sheet_id:
        try:
            if 'shipment_data' not in st.session_state:
                with st.spinner("🔄 Cargando datos..."):
                    shipment_df, header_row, sheet, load_time = load_all_data(client, sheet_id)
                    st.session_state.shipment_data = shipment_df
                    st.session_state.header_row = header_row
                    st.session_state.sheet = sheet
                    st.sidebar.success(f"✅ Datos cargados en {load_time:.1f}s")
            else:
                shipment_df = st.session_state.shipment_data
                header_row = st.session_state.header_row
                sheet = st.session_state.sheet

            uploaded_packing = st.sidebar.file_uploader("📦 Packing List (Excel)", type='xlsx')
            
            if uploaded_packing:
                if 'packing_data' not in st.session_state:
                    with st.spinner("📦 Cargando packing list..."):
                        packing_df, pallet_summary = load_packing_data(uploaded_packing)
                        st.session_state.packing_data = packing_df
                        st.session_state.pallet_summary = pallet_summary
                else:
                    packing_df = st.session_state.packing_data
                    pallet_summary = st.session_state.pallet_summary

                if 'scans_db' not in st.session_state:
                    st.session_state.scans_db = set()
                    try:
                        conn = sqlite3.connect('scans.db', check_same_thread=False)
                        cursor = conn.cursor()
                        cursor.execute('''
                            CREATE TABLE IF NOT EXISTS pallet_scans (
                                id INTEGER PRIMARY KEY AUTOINCREMENT,
                                camion TEXT,
                                pallet_number TEXT,
                                first_serial TEXT,
                                last_serial TEXT,
                                ubicacion TEXT,
                                slot INTEGER DEFAULT 1,
                                scanned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                                UNIQUE(camion, pallet_number)
                            )
                        ''')
                        conn.commit()
                        
                        # Cargar datos existentes
                        existing_scans = pd.read_sql('SELECT camion, pallet_number, ubicacion, slot FROM pallet_scans', conn)
                        st.session_state.scans_db = set(zip(
                            existing_scans['camion'].astype(str), 
                            existing_scans['pallet_number'].astype(str)
                        ))
                        
                        # Cargar asignaciones de pallets (ahora soportando múltiples pallets por ubicación)
                        st.session_state.pallet_assignments = {}
                        for _, row in existing_scans.iterrows():
                            if pd.notna(row['ubicacion']):
                                ubicacion = row['ubicacion']
                                assignment = {
                                    'camion': row['camion'],
                                    'pallet': row['pallet_number'],
                                    'slot': row.get('slot', 1)
                                }
                                
                                # Si la ubicación ya tiene pallets, agregar a la lista
                                if ubicacion in st.session_state.pallet_assignments:
                                    if isinstance(st.session_state.pallet_assignments[ubicacion], list):
                                        st.session_state.pallet_assignments[ubicacion].append(assignment)
                                    else:
                                        # Convertir a lista si era un solo elemento
                                        st.session_state.pallet_assignments[ubicacion] = [st.session_state.pallet_assignments[ubicacion], assignment]
                                else:
                                    st.session_state.pallet_assignments[ubicacion] = [assignment]
                        
                        conn.close()
                    except Exception as e:
                        st.error(f"Error cargando base de datos: {e}")

                def is_pallet_scanned(truck, pallet):
                    return (str(truck), str(pallet)) in st.session_state.scans_db

                def get_pallet_location(truck, pallet):
                    for location, assignments in st.session_state.pallet_assignments.items():
                        if isinstance(assignments, list):
                            for assignment in assignments:
                                if (str(assignment.get('camion', '')) == str(truck) and 
                                    str(assignment.get('pallet', '')) == str(pallet)):
                                    return location, assignment.get('slot', 1)
                        else:
                            assignment = assignments
                            if (str(assignment.get('camion', '')) == str(truck) and 
                                str(assignment.get('pallet', '')) == str(pallet)):
                                return location, assignment.get('slot', 1)
                    return None, None

                def assign_pallet_location(truck_packing_list, pallet):
                    if not st.session_state.layout_locations:
                        return None, None
                    
                    # DETECTAR CAMIÓN DISPONIBLE AUTOMÁTICAMENTE
                    camion_actual = detectar_camion_disponible(truck_packing_list)
                    if not camion_actual:
                        st.error("❌ No hay camiones disponibles en el layout")
                        return None, None
                    
                    numero_pallet = extraer_numero_pallet(str(pallet))
                    
                    if numero_pallet is None:
                        return None, None
                    
                    # CALCULAR UBICACIÓN BASADA EN NÚMERO DE PALLET Y CAMIÓN DETECTADO
                    ubicacion = calcular_ubicacion_pallet(numero_pallet, camion_actual)
                    
                    # Verificar si la ubicación calculada existe en el layout
                    if ubicacion not in st.session_state.layout_locations:
                        # Buscar la ubicación más cercana disponible
                        ubicaciones_camion = [loc for loc in st.session_state.layout_locations if loc.startswith(f'{camion_actual}-')]
                        if not ubicaciones_camion:
                            return None, None
                        
                        # Ordenar ubicaciones y tomar la primera disponible
                        ubicaciones_camion.sort(key=lambda x: int(x.split('-')[1]))
                        ubicacion = ubicaciones_camion[0]
                    
                    # Verificar si hay espacio en la ubicación (máximo 2 pallets)
                    current_assignments = []
                    if ubicacion in st.session_state.pallet_assignments:
                        if isinstance(st.session_state.pallet_assignments[ubicacion], list):
                            current_assignments = st.session_state.pallet_assignments[ubicacion]
                        else:
                            current_assignments = [st.session_state.pallet_assignments[ubicacion]]
                    
                    # Verificar si hay espacio (máximo 2 pallets por ubicación)
                    if len(current_assignments) < 2:
                        # Encontrar slot disponible
                        used_slots = {assig.get('slot', 1) for assig in current_assignments}
                        available_slot = 1 if 1 not in used_slots else 2
                        
                        new_assignment = {
                            'camion': str(truck_packing_list),  # Guardamos el camión del packing list
                            'pallet': str(pallet),
                            'slot': available_slot
                        }
                        
                        # Actualizar asignaciones
                        if ubicacion in st.session_state.pallet_assignments:
                            if isinstance(st.session_state.pallet_assignments[ubicacion], list):
                                st.session_state.pallet_assignments[ubicacion].append(new_assignment)
                            else:
                                st.session_state.pallet_assignments[ubicacion] = [st.session_state.pallet_assignments[ubicacion], new_assignment]
                        else:
                            st.session_state.pallet_assignments[ubicacion] = [new_assignment]
                        
                        return ubicacion, available_slot
                    
                    return None, None

                def register_pallet_scan(truck_packing_list, pallet, first_serial, last_serial):
                    try:
                        ubicacion, slot = assign_pallet_location(truck_packing_list, pallet)
                        
                        def save_to_db():
                            conn = sqlite3.connect('scans.db', check_same_thread=False)
                            cursor = conn.cursor()
                            cursor.execute(
                                'INSERT OR IGNORE INTO pallet_scans (camion, pallet_number, first_serial, last_serial, ubicacion, slot) VALUES (?, ?, ?, ?, ?, ?)',
                                (str(truck_packing_list), str(pallet), str(first_serial), str(last_serial), ubicacion, slot)
                            )
                            conn.commit()
                            conn.close()
                        
                        thread = threading.Thread(target=save_to_db)
                        thread.daemon = True
                        thread.start()
                        
                        st.session_state.scans_db.add((str(truck_packing_list), str(pallet)))
                        return True, ubicacion, slot
                        
                    except Exception:
                        return False, None, None

                def update_shipment_status_async(truck, status="Listo"):
                    def update_async():
                        try:
                            time.sleep(1)
                            truck_cells = sheet.findall(str(truck))
                            for cell in truck_cells:
                                if cell.row > header_row:
                                    sheet.update_cell(cell.row, 19, status)
                                    break
                        except Exception:
                            pass
                    
                    thread = threading.Thread(target=update_async)
                    thread.daemon = True
                    thread.start()

                def get_truck_pallets(truck_data, pallet_summary):
                    """CORREGIDO: Maneja correctamente la comparación de pallets sin errores de Serie"""
                    try:
                        pallet_start = str(truck_data['PALLET INICIAL']).strip()
                        pallet_end = str(truck_data['PALLET FINAL']).strip()
                        
                        # Crear una lista para almacenar los pallets que coinciden
                        matching_pallets = []
                        
                        for _, pallet_row in pallet_summary.iterrows():
                            pallet_num = str(pallet_row['Pallet number'])
                            
                            # Intentar comparar como números si es posible
                            try:
                                pallet_num_float = float(pallet_num)
                                start_float = float(pallet_start)
                                end_float = float(pallet_end)
                                
                                if start_float <= pallet_num_float <= end_float:
                                    matching_pallets.append(pallet_row)
                            except (ValueError, TypeError):
                                # Si no se pueden convertir a números, comparar como strings
                                if pallet_start <= pallet_num <= pallet_end:
                                    matching_pallets.append(pallet_row)
                        
                        if matching_pallets:
                            return pd.DataFrame(matching_pallets)
                        else:
                            return pd.DataFrame(columns=pallet_summary.columns)
                    
                    except Exception as e:
                        st.error(f"Error en get_truck_pallets: {e}")
                        return pd.DataFrame()

                def deliver_truck(truck):
                    """Liberar todas las ubicaciones de un camión entregado"""
                    try:
                        # Eliminar de la base de datos
                        conn = sqlite3.connect('scans.db', check_same_thread=False)
                        cursor = conn.cursor()
                        cursor.execute('DELETE FROM pallet_scans WHERE camion = ?', (str(truck),))
                        conn.commit()
                        conn.close()
                        
                        # Liberar asignaciones en memoria
                        locations_to_remove = []
                        for ubicacion, assignments in st.session_state.pallet_assignments.items():
                            if isinstance(assignments, list):
                                # Filtrar solo los assignments que no son del camión
                                remaining_assignments = [a for a in assignments if str(a.get('camion', '')) != str(truck)]
                                if remaining_assignments:
                                    st.session_state.pallet_assignments[ubicacion] = remaining_assignments
                                else:
                                    locations_to_remove.append(ubicacion)
                            else:
                                if str(assignments.get('camion', '')) == str(truck):
                                    locations_to_remove.append(ubicacion)
                        
                        for ubicacion in locations_to_remove:
                            del st.session_state.pallet_assignments[ubicacion]
                        
                        # Actualizar scans_db
                        st.session_state.scans_db = {scan for scan in st.session_state.scans_db if scan[0] != str(truck)}
                        
                        # Marcar como entregado
                        st.session_state.delivered_trucks.add(str(truck))
                        
                        # Actualizar Google Sheets
                        update_shipment_status_async(truck, "Entregado")
                        
                        return True
                    except Exception as e:
                        st.error(f"Error al entregar camión: {e}")
                        return False

                # Interfaz principal con pestañas
                available_trucks = shipment_df.copy()
                
                if 'ESTATUS' in shipment_df.columns:
                    # CORREGIDO: Evitar el error de verdad ambigua de Series
                    mask = (
                        shipment_df['ESTATUS'].isna() | 
                        (shipment_df['ESTATUS'] == '') |
                        (shipment_df['ESTATUS'] == 'None')
                    )
                    # Agregar condición para LISTO por separado
                    try:
                        listo_mask = shipment_df['ESTATUS'].str.contains('LISTO', case=False, na=False)
                        mask = mask | ~listo_mask
                    except Exception as e:
                        st.error(f"Error procesando máscara LISTO: {e}")
                    
                    available_trucks = shipment_df[mask]
                
                if len(available_trucks) == 0:
                    st.success("🎉 Todos los camiones listos!")
                else:
                    selected_truck = st.selectbox(
                        "🚛 Selecciona camión (Packing List):",
                        available_trucks['CAMION'].values,
                        key="truck_selector"
                    )

                    if st.session_state.current_truck != selected_truck:
                        st.session_state.current_truck = selected_truck
                        truck_data = available_trucks[available_trucks['CAMION'] == selected_truck].iloc[0]
                        st.session_state.truck_pallets = get_truck_pallets(truck_data, pallet_summary)
                        st.session_state.scanned_count = sum(
                            1 for _, row in st.session_state.truck_pallets.iterrows() 
                            if is_pallet_scanned(selected_truck, row['Pallet number'])
                        )
                        
                        # DETECTAR CAMIÓN DISPONIBLE PARA ESTE TRUCK
                        st.session_state.camion_asignado_actual = detectar_camion_disponible(selected_truck)

                    truck_pallets = st.session_state.truck_pallets
                    total_pallets = len(truck_pallets)
                    scanned_count = st.session_state.scanned_count

                    # Crear pestañas
                    tab1, tab2, tab3 = st.tabs(["📊 Escaneo y Control", "🗺️ Layout del Almacén", "🚚 Entregar Embarques"])

                    with tab1:
                        # Mostrar tabla de pallets del camión seleccionado
                        st.subheader("📋 Información del Camión Seleccionado")
                        
                        # Crear tabla con información del camión
                        truck_info = available_trucks[available_trucks['CAMION'] == selected_truck].iloc[0]
                        
                        info_cols = st.columns(4)
                        with info_cols[0]:
                            st.metric("🚛 Camión (Packing List)", selected_truck)
                        with info_cols[1]:
                            st.metric("📦 Pallets Inicial", truck_info['PALLET INICIAL'])
                        with info_cols[2]:
                            st.metric("📦 Pallets Final", truck_info['PALLET FINAL'])
                        with info_cols[3]:
                            st.metric("📊 Estatus", truck_info.get('ESTATUS', 'Pendiente'))
                        
                        # INFORMACIÓN MEJORADA DE ASIGNACIÓN AUTOMÁTICA
                        st.subheader("🎯 Asignación Automática de Camión")
                        
                        col1, col2, col3 = st.columns(3)
                        with col1:
                            st.info(f"**📋 Camión Packing List:**\n# {selected_truck}")
                        with col2:
                            if st.session_state.camion_asignado_actual:
                                st.success(f"**🏗️ Camión en Layout:**\n# {st.session_state.camion_asignado_actual}")
                            else:
                                st.error("**❌ No hay camión disponible**")
                        with col3:
                            if st.session_state.camiones_layout:
                                st.info(f"**🗺️ Camiones en Layout:**\n{', '.join([f'C{c}' for c in st.session_state.camiones_layout])}")
                        
                        # Métricas de progreso
                        st.subheader("📊 Progreso de Escaneo")
                        col1, col2, col3 = st.columns(3)
                        with col1:
                            st.metric("📦 Pallets Escaneados", f"{scanned_count}/{total_pallets}")
                        with col2:
                            st.metric("⏳ Pendientes", total_pallets - scanned_count)
                        with col3:
                            progress = scanned_count / total_pallets if total_pallets > 0 else 0
                            st.metric("📊 % Completado", f"{progress:.1%}")
                        
                        st.progress(progress)

                        # Tabla detallada de pallets
                        st.subheader("📋 Tabla de Pallets del Camión")
                        
                        if not truck_pallets.empty:
                            # Preparar datos para la tabla
                            pallet_table_data = []
                            for _, pallet in truck_pallets.iterrows():
                                pallet_number = pallet['Pallet number']
                                is_scanned = is_pallet_scanned(selected_truck, pallet_number)
                                location, slot = get_pallet_location(selected_truck, pallet_number)
                                
                                # CALCULAR UBICACIÓN ESPERADA DINÁMICAMENTE
                                numero_pallet = extraer_numero_pallet(str(pallet_number))
                                ubicacion_esperada = ""
                                if numero_pallet and st.session_state.camion_asignado_actual:
                                    ubicacion_esperada = calcular_ubicacion_pallet(numero_pallet, st.session_state.camion_asignado_actual)
                                
                                pallet_table_data.append({
                                    'Pallet': pallet_number,
                                    'Primer Serial': pallet['first_serial'],
                                    'Último Serial': pallet['last_serial'],
                                    'Cajas': pallet['box_count'],
                                    'Estatus': '✅ Escaneado' if is_scanned else '⏳ Pendiente',
                                    'Ubicación Actual': f"{location} (Slot {slot})" if location else 'No asignada',
                                    '📍 Ubicación Esperada': ubicacion_esperada if ubicacion_esperada else 'N/A'
                                })
                            
                            pallet_df = pd.DataFrame(pallet_table_data)
                            st.dataframe(pallet_df, width='stretch')
                        else:
                            st.warning("No se encontraron pallets para este camión en el rango especificado.")

                        # ESCANEO POR PALLET
                        st.subheader("🔍 Escaneo por Pallet")
                        
                        with st.form(key='scan_form', clear_on_submit=True):
                            col1, col2 = st.columns(2)
                            with col1:
                                first_serial_input = st.text_input(
                                    "Primer Serial del Pallet:",
                                    key="first_serial_input"
                                )
                            with col2:
                                last_serial_input = st.text_input(
                                    "Último Serial del Pallet:",
                                    key="last_serial_input"
                                )
                            
                            submitted = st.form_submit_button("✅ Registrar Pallet Completo")
                        
                        if submitted and first_serial_input.strip() and last_serial_input.strip():
                            first_serial = first_serial_input.strip()
                            last_serial = last_serial_input.strip()
                            current_time = time.time()
                            
                            if current_time - st.session_state.last_scan_time < 0.5:
                                st.warning("⏳ Espera un momento...")
                            else:
                                st.session_state.last_scan_time = current_time
                                
                                matching_pallet = None
                                for _, pallet in truck_pallets.iterrows():
                                    if (str(pallet['first_serial']) == first_serial and 
                                        str(pallet['last_serial']) == last_serial):
                                        matching_pallet = pallet
                                        break
                                
                                if matching_pallet is not None:
                                    pallet_number = matching_pallet['Pallet number']
                                    
                                    if not is_pallet_scanned(selected_truck, pallet_number):
                                        success, ubicacion, slot = register_pallet_scan(
                                            selected_truck, pallet_number, first_serial, last_serial
                                        )
                                        
                                        if success:
                                            st.session_state.scanned_count += 1
                                            st.success(f"✅ Pallet {pallet_number} escaneado!")
                                            if ubicacion:
                                                st.success(f"📍 Ubicación asignada: {ubicacion} (Slot {slot})")
                                            
                                            # CALCULAR UBICACIÓN ESPERADA PARA COMPARAR
                                            numero_pallet = extraer_numero_pallet(str(pallet_number))
                                            ubicacion_esperada = ""
                                            if numero_pallet and st.session_state.camion_asignado_actual:
                                                ubicacion_esperada = calcular_ubicacion_pallet(numero_pallet, st.session_state.camion_asignado_actual)
                                            
                                            if ubicacion == ubicacion_esperada:
                                                st.success(f"🎯 **Ubicación correcta:** Coincide con la esperada ({ubicacion_esperada})")
                                            else:
                                                st.warning(f"⚠️ **Ubicación diferente:** Esperada {ubicacion_esperada}, Asignada {ubicacion}")
                                            
                                            if st.session_state.scanned_count >= total_pallets:
                                                st.balloons()
                                                update_shipment_status_async(selected_truck)
                                                st.success("🎉 ¡Camión completado!")
                                            
                                            # Forzar actualización
                                            st.rerun()
                                        else:
                                            st.error("❌ Error al registrar")
                                    else:
                                        ubicacion, slot = get_pallet_location(selected_truck, pallet_number)
                                        if ubicacion:
                                            st.warning(f"⚠️ Este pallet ya fue escaneado y está en {ubicacion} (Slot {slot})")
                                        else:
                                            st.warning("⚠️ Este pallet ya fue escaneado")
                                else:
                                    st.error("❌ Los serials no coinciden con ningún pallet del camión")

                        # Información de ubicaciones ocupadas
                        occupied_locations = [
                            loc for loc, assignments in st.session_state.pallet_assignments.items()
                            if any(str(a.get('camion', '')) == str(selected_truck) for a in (assignments if isinstance(assignments, list) else [assignments]))
                        ]
                        
                        if occupied_locations:
                            st.subheader("📍 Ubicaciones Ocupadas - Detalles")
                            occupied_df = []
                            for location in occupied_locations:
                                assignments = st.session_state.pallet_assignments[location]
                                if not isinstance(assignments, list):
                                    assignments = [assignments]
                                
                                for assignment in assignments:
                                    if str(assignment.get('camion', '')) == str(selected_truck):
                                        pallet_info = None
                                        for _, pallet in truck_pallets.iterrows():
                                            if str(pallet['Pallet number']) == str(assignment.get('pallet', '')):
                                                pallet_info = pallet
                                                break
                                        
                                        if pallet_info is not None:
                                            occupied_df.append({
                                                'Ubicación': location,
                                                'Slot': assignment.get('slot', 1),
                                                'Pallet': assignment.get('pallet', 'N/A'),
                                                'Primer Serial': pallet_info['first_serial'],
                                                'Último Serial': pallet_info['last_serial'],
                                                'Cajas': pallet_info['box_count']
                                            })
                            
                            if occupied_df:
                                st.dataframe(pd.DataFrame(occupied_df), width='stretch')

                    with tab2:
                        # VISUALIZACIÓN SVG INTERACTIVA EN PESTAÑA SEPARADA
                        if st.session_state.layout_locations and st.session_state.layout_shapes:
                            st.subheader("🗺️ Mapa SVG Interactivo del Almacén")
                            
                            # Mostrar información del camión detectado
                            if st.session_state.camion_asignado_actual:
                                st.success(f"🎯 **Camión asignado automáticamente:** {st.session_state.camion_asignado_actual}")
                            
                            # Controles de zoom y pan
                            col1, col2, col3, col4 = st.columns([1, 1, 1, 1])
                            
                            with col1:
                                zoom_level = st.slider(
                                    "🔍 Zoom",
                                    min_value=0.1,
                                    max_value=5.0,
                                    value=st.session_state.zoom_level,
                                    step=0.1,
                                    key="zoom_slider"
                                )
                                st.session_state.zoom_level = zoom_level
                            
                            with col2:
                                pan_x = st.slider(
                                    "↔️ Pan Horizontal",
                                    min_value=-500,
                                    max_value=500,
                                    value=st.session_state.pan_x,
                                    step=10,
                                    key="pan_x_slider"
                                )
                                st.session_state.pan_x = pan_x
                            
                            with col3:
                                pan_y = st.slider(
                                    "↕️ Pan Vertical",
                                    min_value=-500,
                                    max_value=500,
                                    value=st.session_state.pan_y,
                                    step=10,
                                    key="pan_y_slider"
                                )
                                st.session_state.pan_y = pan_y
                            
                            with col4:
                                st.write("")  # Espacio
                                st.write("")  # Espacio
                                if st.button("🔄 Reset Vista"):
                                    st.session_state.zoom_level = 1.0
                                    st.session_state.pan_x = 0
                                    st.session_state.pan_y = 0
                                    st.rerun()
                            
                            st.info(f"🔍 **Zoom:** {zoom_level:.1f}x | 🎯 **Pan:** X={pan_x}, Y={pan_y}")
                            
                            # Información sobre tooltips
                            st.info("ℹ️ **Pasa el cursor sobre cada ubicación para ver la información completa de los pallets**")
                            
                            # Generar SVG mejorado con zoom y pan
                            svg_content = generate_enhanced_svg_layout(
                                st.session_state.layout_shapes,
                                st.session_state.pallet_assignments,
                                st.session_state.camion_asignado_actual if st.session_state.camion_asignado_actual else selected_truck,
                                truck_pallets,
                                st.session_state.zoom_level,
                                st.session_state.pan_x,
                                st.session_state.pan_y
                            )
                            
                            # Mostrar SVG con contenedor más grande
                            st.components.v1.html(
                                f"""
                                <div style="border: 2px solid #dee2e6; border-radius: 5px; padding: 10px; background: white; overflow: auto; height: 800px;">
                                    {svg_content}
                                </div>
                                """,
                                height=800
                            )
                            
                            # Instrucciones de navegación
                            with st.expander("🎮 Instrucciones de Navegación y Leyenda"):
                                st.markdown("""
                                **🔍 Zoom:**
                                - Usa el slider de Zoom para acercar/alejar
                                - Rango: 0.1x (muy alejado) a 5.0x (muy cercano)
                                
                                **🎯 Pan/Navegación:**
                                - **Pan Horizontal:** Mueve el layout izquierda/derecha
                                - **Pan Vertical:** Mueve el layout arriba/abajo
                                - **Reset Vista:** Vuelve a la vista original
                                
                                **🏗️ Estructura de Ubicaciones:**
                                - Cada ubicación tiene capacidad para **2 pallets** (estiba)
                                - Línea divisoria horizontal indica los dos slots
                                - Círculos en la parte inferior muestran ocupación:
                                  - 🔴 **Rojo**: Slot ocupado
                                  - 🟢 **Verde**: Slot disponible
                                
                                **🎨 Código de Colores:**
                                - 🟢 **Verde**: Disponible para este camión
                                - 🔴 **Rojo**: Ocupado por este camión (con info)
                                - 🟡 **Amarillo**: Asignado (info incompleta)
                                - ⚫ **Gris oscuro**: Ocupado por otro camión
                                - ⚪ **Gris claro**: No disponible
                                
                                **ℹ️ Tooltips:**
                                - **Pasa el cursor** sobre cualquier ubicación para ver información detallada
                                - **Información mostrada:**
                                  - 📍 Ubicación
                                  - 📦 Capacidad (2 pallets)
                                  - 🚛 Pallets asignados (cuántos de 2)
                                  - Detalles de cada pallet (número, seriales, cajas)
                                """)
                            
                        else:
                            st.warning("⚠️ No hay layout cargado. Usa la barra lateral para cargar un layout SVG/XML.")
                            st.info("💡 Puedes cargar un layout desde la barra lateral usando:")
                            st.markdown("- 🖼️ **SVG/XML**: Sube un archivo SVG con el diseño del almacén")
                            st.markdown("- 📝 **Texto**: Pega una lista de ubicaciones (C1-1, C1-2, etc.)")

                    with tab3:
                        st.subheader("🚚 Entregar Embarques a Almacén")
                        st.info("Entrega camiones completados para liberar sus ubicaciones en el layout")
                        
                        # Listar camiones listos para entregar (completados pero no entregados)
                        completed_trucks = []
                        for truck in shipment_df['CAMION'].unique():
                            if str(truck) in st.session_state.delivered_trucks:
                                continue
                                
                            truck_data = shipment_df[shipment_df['CAMION'] == truck].iloc[0]
                            truck_pallets_for_delivery = get_truck_pallets(truck_data, pallet_summary)
                            total_pallets_for_delivery = len(truck_pallets_for_delivery)
                            scanned_count_for_delivery = sum(
                                1 for _, row in truck_pallets_for_delivery.iterrows() 
                                if is_pallet_scanned(truck, row['Pallet number'])
                            )
                            
                            if scanned_count_for_delivery >= total_pallets_for_delivery and total_pallets_for_delivery > 0:
                                # Verificar si tiene ubicaciones asignadas
                                has_assignments = any(
                                    any(str(a.get('camion', '')) == str(truck) for a in (assignments if isinstance(assignments, list) else [assignments]))
                                    for assignments in st.session_state.pallet_assignments.values()
                                )
                                
                                if has_assignments:
                                    completed_trucks.append({
                                        'camion': truck,
                                        'pallets_escaneados': scanned_count_for_delivery,
                                        'total_pallets': total_pallets_for_delivery
                                    })
                        
                        if not completed_trucks:
                            st.success("🎉 No hay camiones listos para entregar.")
                            st.info("Los camiones aparecerán aquí cuando estén completados (todos los pallets escaneados)")
                        else:
                            st.subheader("📋 Camiones Listos para Entregar")
                            
                            for truck_info in completed_trucks:
                                with st.container():
                                    col1, col2, col3 = st.columns([2, 1, 1])
                                    with col1:
                                        st.write(f"**🚛 Camión {truck_info['camion']}**")
                                        st.write(f"📦 Pallets: {truck_info['pallets_escaneados']}/{truck_info['total_pallets']}")
                                    
                                    with col2:
                                        # Mostrar ubicaciones asignadas
                                        locations_count = 0
                                        for ubicacion, assignments in st.session_state.pallet_assignments.items():
                                            if isinstance(assignments, list):
                                                truck_assignments = [a for a in assignments if str(a.get('camion', '')) == str(truck_info['camion'])]
                                                locations_count += 1 if truck_assignments else 0
                                            else:
                                                if str(assignments.get('camion', '')) == str(truck_info['camion']):
                                                    locations_count += 1
                                        st.write(f"📍 Ubicaciones: {locations_count}")
                                    
                                    with col3:
                                        if st.button(f"📦 Entregar", key=f"deliver_{truck_info['camion']}"):
                                            if deliver_truck(truck_info['camion']):
                                                st.success(f"✅ Camión {truck_info['camion']} entregado exitosamente!")
                                                st.rerun()
                                            else:
                                                st.error(f"❌ Error al entregar camión {truck_info['camion']}")
                                    
                                    st.divider()
                            
                            # Estadísticas de entregas
                            st.subheader("📊 Estadísticas de Entregas")
                            col1, col2 = st.columns(2)
                            with col1:
                                st.metric("🚛 Camiones Entregados", len(st.session_state.delivered_trucks))
                            with col2:
                                st.metric("📦 Camiones Pendientes", len(completed_trucks))

        except Exception as e:
            st.error(f"❌ Error: {str(e)}")
            st.info("💡 Si el error persiste, intenta recargar la página o limpiar la base de datos desde la barra lateral.")

# Botones de utilidad
st.sidebar.header("🔧 Utilidades")

col1, col2 = st.sidebar.columns(2)
with col1:
    if st.button("🔄 Recargar Todo"):
        st.cache_data.clear()
        st.cache_resource.clear()
        for key in list(st.session_state.keys()):
            del st.session_state[key]
        st.rerun()

with col2:
    if st.button("🗑️ Limpiar DB"):
        try:
            conn = sqlite3.connect('scans.db')
            cursor = conn.cursor()
            cursor.execute('DELETE FROM pallet_scans')
            conn.commit()
            conn.close()
            st.session_state.scans_db = set()
            st.session_state.scanned_count = 0
            st.session_state.pallet_assignments = {}
            st.session_state.delivered_trucks = set()
            st.sidebar.success("Base limpiada")
            st.rerun()
        except Exception as e:
            st.sidebar.error(f"Error: {str(e)}")

# Plantilla de ejemplo SVG
with st.sidebar.expander("📥 Plantilla SVG"):
    st.markdown("**Ejemplo de archivo SVG:**")
    
    example_svg = '''<svg width="800" height="600" xmlns="http://www.w3.org/2000/svg">
    <!-- Rectángulos para ubicaciones -->
    <rect id="C1-1" x="50" y="50" width="100" height="80" fill="#cccccc" stroke="#666666"/>
    <rect id="C1-2" x="160" y="50" width="100" height="80" fill="#cccccc" stroke="#666666"/>
    <rect id="C1-3" x="270" y="50" width="100" height="80" fill="#cccccc" stroke="#666666"/>
    
    <rect id="C2-1" x="50" y="150" width="100" height="80" fill="#cccccc" stroke="#666666"/>
    <rect id="C2-2" x="160" y="150" width="100" height="80" fill="#cccccc" stroke="#666666"/>
    <rect id="C2-3" x="270" y="150" width="100" height="80" fill="#cccccc" stroke="#666666"/>
    
    <!-- Textos para etiquetas -->
    <text x="100" y="95" text-anchor="middle" fill="#000000" font-weight="bold">C1-1</text>
    <text x="210" y="95" text-anchor="middle" fill="#000000" font-weight="bold">C1-2</text>
    <text x="320" y="95" text-anchor="middle" fill="#000000" font-weight="bold">C1-3</text>
    
    <text x="100" y="195" text-anchor="middle" fill="#000000" font-weight="bold">C2-1</text>
    <text x="210" y="195" text-anchor="middle" fill="#000000" font-weight="bold">C2-2</text>
    <text x="320" y="195" text-anchor="middle" fill="#000000" font-weight="bold">C2-3</text>
</svg>'''
    
    st.code(example_svg, language='xml')
    
    st.download_button(
        label="📥 Descargar Plantilla SVG",
        data=example_svg,
        file_name="plantilla_layout.svg",
        mime="image/svg+xml"
    )

# Información de ayuda
with st.sidebar.expander("ℹ️ Ayuda SVG/XML"):
    st.markdown("""
    **🖼️ Formato SVG/XML:**
    - **Rectángulos**: `<rect id="C1-1" x="50" y="50" width="100" height="80"/>`
    - **Polígonos**: `<polygon id="C1-2" points="50,50 100,50 100,100 50,100"/>`
    - **Textos**: `<text data-ubicacion="C1-3" x="100" y="100">C1-3</text>`
    
    **🏗️ Estructura de Almacén:**
    - Cada ubicación tiene capacidad para **2 pallets** (estiba)
    - Los pallets se asignan automáticamente a slots disponibles
    - Visualización mejorada con división de slots e indicadores de ocupación
    
    **🎨 Código de Colores Mejorado:**
    - 🟢 **Verde**: Disponible para este camión
    - 🔴 **Rojo**: Ocupado por este camión (con info)
    - 🟡 **Amarillo**: Asignado (info incompleta)
    - ⚫ **Gris oscuro**: Ocupado por otro camión
    - ⚪ **Gris claro**: No disponible
    
    **🚀 NUEVO SISTEMA INTELIGENTE DE ASIGNACIÓN:**
    - **Detección automática** de camiones en el layout SVG
    - **Asignación inteligente** del primer camión disponible
    - **Gestión dinámica** de múltiples camiones
    - **Compatibilidad** entre camión packing list y camión layout
    - **Reutilización inteligente** de camiones ya asignados
    
    **🎯 CÓMO FUNCIONA:**
    1. **Carga tu layout SVG** con ubicaciones (C1-1, C2-1, etc.)
    2. **Selecciona un camión** del packing list
    3. **El sistema detecta automáticamente** el primer camión disponible en el layout
    4. **Asigna pallets** al camión detectado (2 pallets por ubicación)
    5. **Si el camión ya estaba en uso**, reutiliza el mismo camión
    
    **📊 EJEMPLO:**
    - Layout con camiones: C1, C2, C3
    - Camión 4 del packing list → Se asigna a C1 (primer disponible)
    - Camión 5 del packing list → Se asigna a C2 (siguiente disponible)
    - Camión 1 del packing list → Se asigna a C1 (ya estaba en uso)
    """)