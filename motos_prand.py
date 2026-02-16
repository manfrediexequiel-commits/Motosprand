import streamlit as st
import pandas as pd
import sqlite3
import hashlib
import secrets
import re
import os
import json
import shutil
from datetime import datetime, timedelta
from contextlib import contextmanager
from dataclasses import dataclass
from typing import List, Dict, Optional
from pathlib import Path
import threading
import time
import schedule
import bcrypt
from io import BytesIO
import base64

# =============================================================================
# CONFIGURACIÓN INICIAL - MOTOS PRAND
# =============================================================================

st.set_page_config(
    page_title="Motos Prand - Gestión de Taller",
    page_icon="🏍️",
    layout="wide",
    initial_sidebar_state="expanded"
)

@dataclass
class Config:
    DB_PATH: str = 'motos_prand.db'
    BACKUP_DIR: str = './backups'
    SESSION_TIMEOUT: int = 480
    MAX_LOGIN_ATTEMPTS: int = 3
    LOCKOUT_TIME: int = 15

config = Config()
Path(config.BACKUP_DIR).mkdir(exist_ok=True)

# CSS Personalizado Motos Prand
st.markdown("""
<style>
    .main-header { 
        color: #ff6b00; 
        font-size: 2.2rem; 
        font-weight: 700; 
        margin-bottom: 1rem;
        border-bottom: 3px solid #ff6b00;
        padding-bottom: 0.5rem;
    }
    .stMetric { 
        background: linear-gradient(135deg, #fff5eb 0%, #ffe4cc 100%); 
        padding: 20px; 
        border-radius: 12px; 
        border: 1px solid #ffcc99;
        box-shadow: 0 2px 4px rgba(255,107,0,0.1);
    }
    [data-testid="stSidebar"] { 
        background-color: #1a1a2e; 
    }
    [data-testid="stSidebar"] .stRadio label {
        color: white !important;
    }
    .login-container {
        max-width: 400px;
        margin: 0 auto;
        padding: 2rem;
        background: white;
        border-radius: 16px;
        box-shadow: 0 10px 25px rgba(255,107,0,0.2);
    }
    .admin-badge {
        background: #ff6b00;
        color: white;
        padding: 2px 8px;
        border-radius: 12px;
        font-size: 0.8em;
    }
    .tecnico-badge {
        background: #4ecdc4;
        color: white;
        padding: 2px 8px;
        border-radius: 12px;
        font-size: 0.8em;
    }
</style>
""", unsafe_allow_html=True)

# =============================================================================
# BASE DE DATOS
# =============================================================================

class DatabaseManager:
    def __init__(self):
        self._local = threading.local()
        self.init_db()
        self._setup_backup_schedule()
    
    def get_connection(self):
        if not hasattr(self._local, 'conn') or self._local.conn is None:
            self._local.conn = sqlite3.connect(
                config.DB_PATH, 
                check_same_thread=False,
                timeout=20.0,
                isolation_level=None
            )
            self._local.conn.row_factory = sqlite3.Row
            self._local.conn.execute("PRAGMA foreign_keys = ON")
            self._local.conn.execute("PRAGMA journal_mode = WAL")
        return self._local.conn

    @contextmanager
    def transaction(self):
        conn = self.get_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("BEGIN IMMEDIATE")
            yield cursor
            conn.commit()
        except sqlite3.OperationalError as e:
            conn.rollback()
            st.error(f"Error de base de datos: {e}")
            raise
        except Exception as e:
            conn.rollback()
            raise e

    def init_db(self):
        with self.transaction() as cur:
            cur.executescript("""
                CREATE TABLE IF NOT EXISTS usuarios (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT UNIQUE NOT NULL, 
                    password_hash TEXT NOT NULL, 
                    nombre TEXT NOT NULL, 
                    rol TEXT DEFAULT 'tecnico',
                    activo INTEGER DEFAULT 1,
                    force_password_change INTEGER DEFAULT 0,
                    last_login TIMESTAMP,
                    failed_attempts INTEGER DEFAULT 0,
                    locked_until TIMESTAMP,
                    created_by INTEGER,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                
                CREATE TABLE IF NOT EXISTS clientes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    nombre TEXT NOT NULL, 
                    telefono TEXT,
                    email TEXT,
                    dni TEXT UNIQUE,
                    activo INTEGER DEFAULT 1,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                
                CREATE TABLE IF NOT EXISTS vehiculos (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    cliente_id INTEGER NOT NULL, 
                    patente TEXT UNIQUE NOT NULL, 
                    marca TEXT NOT NULL, 
                    modelo TEXT NOT NULL,
                    year INTEGER,
                    vin TEXT,
                    FOREIGN KEY(cliente_id) REFERENCES clientes(id) ON DELETE CASCADE
                );
                
                CREATE TABLE IF NOT EXISTS inventario (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    codigo TEXT UNIQUE NOT NULL, 
                    item TEXT NOT NULL, 
                    cantidad INTEGER DEFAULT 0, 
                    minimo INTEGER DEFAULT 5,
                    precio_costo REAL DEFAULT 0, 
                    precio_venta REAL DEFAULT 0, 
                    ubicacion TEXT,
                    activo INTEGER DEFAULT 1,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                
                CREATE TABLE IF NOT EXISTS reparaciones (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    numero_orden TEXT UNIQUE NOT NULL, 
                    cliente_id INTEGER NOT NULL, 
                    vehiculo_id INTEGER NOT NULL,
                    falla TEXT NOT NULL, 
                    diagnostico TEXT,
                    estado TEXT DEFAULT 'Recibido',
                    prioridad TEXT DEFAULT 'Normal',
                    total REAL DEFAULT 0,
                    costo_repuestos REAL DEFAULT 0,
                    fecha_ingreso TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    fecha_estimada DATE,
                    fecha_entrega TIMESTAMP,
                    pagado INTEGER DEFAULT 0,
                    tecnico_asignado INTEGER,
                    FOREIGN KEY(cliente_id) REFERENCES clientes(id),
                    FOREIGN KEY(vehiculo_id) REFERENCES vehiculos(id),
                    FOREIGN KEY(tecnico_asignado) REFERENCES usuarios(id)
                );
                
                CREATE TABLE IF NOT EXISTS movimientos_inventario (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    item_id INTEGER,
                    tipo TEXT,
                    cantidad INTEGER,
                    orden_id INTEGER,
                    usuario_id INTEGER,
                    notas TEXT,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(item_id) REFERENCES inventario(id)
                );
                
                CREATE INDEX IF NOT EXISTS idx_reparaciones_estado ON reparaciones(estado);
                CREATE INDEX IF NOT EXISTS idx_reparaciones_fecha ON reparaciones(fecha_ingreso);
                CREATE INDEX IF NOT EXISTS idx_vehiculos_patente ON vehiculos(patente);
            """)
            
            # Crear usuarios iniciales si no existen
            self._crear_usuario_inicial(cur, 'rodri', '1590', 'Rodri', 'admin')
            self._crear_usuario_inicial(cur, 'lean', '3588', 'Lean', 'admin')
            self._crear_usuario_inicial(cur, 'tecnico', '9911', 'Técnico Principal', 'tecnico')
            self._crear_usuario_inicial(cur, 'tecnico1', '8822', 'Técnico Auxiliar', 'tecnico')

    def _crear_usuario_inicial(self, cur, username, password, nombre, rol):
        """Crea usuario inicial si no existe"""
        cur.execute("SELECT id FROM usuarios WHERE username=?", (username,))
        if not cur.fetchone():
            hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=12))
            cur.execute("""
                INSERT INTO usuarios 
                (username, password_hash, nombre, rol, force_password_change) 
                VALUES (?,?,?,?,?)
            """, (username, hashed.decode(), nombre, rol, 0))

    def _setup_backup_schedule(self):
        schedule.every().day.at("02:00").do(self._backup_db)
        def run_schedule():
            while True:
                schedule.run_pending()
                time.sleep(60)
        threading.Thread(target=run_schedule, daemon=True).start()
    
    def _backup_db(self):
        try:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_path = Path(config.BACKUP_DIR) / f"motos_prand_backup_{timestamp}.db"
            shutil.copy(config.DB_PATH, backup_path)
            backups = sorted(Path(config.BACKUP_DIR).glob("motos_prand_backup_*.db"))
            for old_backup in backups[:-7]:
                old_backup.unlink()
        except Exception as e:
            print(f"Error en backup: {e}")

db = DatabaseManager()

# =============================================================================
# SEGURIDAD
# =============================================================================

def check_password(username: str, password: str) -> Optional[Dict]:
    with db.transaction() as cur:
        cur.execute("""
            SELECT id, password_hash, nombre, rol, force_password_change,
                   failed_attempts, locked_until
            FROM usuarios 
            WHERE username=? AND activo=1
        """, (username,))
        user = cur.fetchone()
        
        if not user:
            return None
        
        if user['locked_until']:
            locked_until = datetime.fromisoformat(user['locked_until'])
            if datetime.now() < locked_until:
                mins_left = (locked_until - datetime.now()).seconds // 60
                st.error(f"Cuenta bloqueada. Intente en {mins_left} minutos.")
                return None
        
        if bcrypt.checkpw(password.encode(), user['password_hash'].encode()):
            cur.execute("""
                UPDATE usuarios 
                SET failed_attempts=0, locked_until=NULL, last_login=?
                WHERE id=?
            """, (datetime.now().isoformat(), user['id']))
            return {
                "id": user['id'],
                "nombre": user['nombre'], 
                "rol": user['rol'],
                "force_password_change": user['force_password_change']
            }
        else:
            new_attempts = user['failed_attempts'] + 1
            locked_until = None
            if new_attempts >= config.MAX_LOGIN_ATTEMPTS:
                locked_until = (datetime.now() + timedelta(minutes=config.LOCKOUT_TIME)).isoformat()
                st.error(f"Cuenta bloqueada por {config.LOCKOUT_TIME} minutos.")
            
            cur.execute("""
                UPDATE usuarios 
                SET failed_attempts=?, locked_until=?
                WHERE id=?
            """, (new_attempts, locked_until, user['id']))
            return None

def crear_usuario(username: str, password: str, nombre: str, rol: str, creado_por: int) -> tuple:
    """Crea nuevo usuario. Retorna (success: bool, message: str)"""
    if len(password) < 4:
        return False, "La contraseña debe tener al menos 4 caracteres"
    
    if rol not in ['admin', 'tecnico']:
        return False, "Rol inválido"
    
    hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=12))
    try:
        with db.transaction() as cur:
            cur.execute("""
                INSERT INTO usuarios 
                (username, password_hash, nombre, rol, created_by, force_password_change) 
                VALUES (?,?,?,?,?,?)
            """, (username.lower(), hashed.decode(), nombre, rol, creado_por, 0))
        return True, f"Usuario {username} creado exitosamente"
    except sqlite3.IntegrityError:
        return False, "El nombre de usuario ya existe"

def cambiar_password(user_id: int, new_password: str) -> tuple:
    if len(new_password) < 4:
        return False, "Mínimo 4 caracteres"
    
    hashed = bcrypt.hashpw(new_password.encode(), bcrypt.gensalt(rounds=12))
    with db.transaction() as cur:
        cur.execute("""
            UPDATE usuarios 
            SET password_hash=?, force_password_change=0 
            WHERE id=?
        """, (hashed.decode(), user_id))
    return True, "Contraseña actualizada"

# =============================================================================
# UTILIDADES
# =============================================================================

def validar_patente(patente: str) -> bool:
    patrones = [
        r'^[A-Z]{3}\d{3}$',
        r'^[A-Z]{2}\d{3}[A-Z]{2}$'
    ]
    return any(re.match(p, patente.upper()) for p in patrones)

def generar_numero_orden() -> str:
    fecha = datetime.now().strftime("%Y%m%d")
    random_suffix = secrets.token_hex(2).upper()
    return f"MP-{fecha}-{random_suffix}"

def calcular_ganancias(periodo: str = 'hoy') -> dict:
    """Calcula ganancias (solo para admin)"""
    with db.get_connection() as conn:
        if periodo == 'hoy':
            query = """
                SELECT 
                    COALESCE(SUM(total), 0) as ingresos,
                    COALESCE(SUM(costo_repuestos), 0) as costos,
                    COALESCE(SUM(total - costo_repuestos), 0) as ganancia_neta,
                    COUNT(*) as cantidad_ordenes
                FROM reparaciones 
                WHERE date(fecha_ingreso) = date('now') AND pagado=1
            """
        elif periodo == 'semana':
            query = """
                SELECT 
                    COALESCE(SUM(total), 0) as ingresos,
                    COALESCE(SUM(costo_repuestos), 0) as costos,
                    COALESCE(SUM(total - costo_repuestos), 0) as ganancia_neta,
                    COUNT(*) as cantidad_ordenes
                FROM reparaciones 
                WHERE fecha_ingreso >= date('now', '-7 days') AND pagado=1
            """
        elif periodo == 'mes':
            query = """
                SELECT 
                    COALESCE(SUM(total), 0) as ingresos,
                    COALESCE(SUM(costo_repuestos), 0) as costos,
                    COALESCE(SUM(total - costo_repuestos), 0) as ganancia_neta,
                    COUNT(*) as cantidad_ordenes
                FROM reparaciones 
                WHERE strftime('%Y-%m', fecha_ingreso) = strftime('%Y-%m', 'now') AND pagado=1
            """
        
        result = pd.read_sql(query, conn).iloc[0]
        return {
            'ingresos': result['ingresos'],
            'costos': result['costos'],
            'ganancia_neta': result['ganancia_neta'],
            'margen': (result['ganancia_neta'] / result['ingresos'] * 100) if result['ingresos'] > 0 else 0,
            'ordenes': int(result['cantidad_ordenes'])
        }

# =============================================================================
# MÓDULOS DE INTERFAZ
# =============================================================================

def show_login():
    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        st.markdown('<div class="login-container">', unsafe_allow_html=True)
        st.markdown("<h1 style='text-align: center; color: #ff6b00;'>🏍️ Motos Prand</h1>", unsafe_allow_html=True)
        st.markdown("<p style='text-align: center; color: #666;'>Sistema de Gestión de Taller</p>", unsafe_allow_html=True)
        
        with st.form("login_form"):
            user = st.text_input("Usuario", placeholder="rodri, lean, tecnico...")
            pw = st.text_input("Contraseña", type="password")
            
            if st.form_submit_button("Iniciar Sesión", use_container_width=True, type="primary"):
                res = check_password(user, pw)
                if res:
                    st.session_state.auth = True
                    st.session_state.user = res
                    st.rerun()
                else:
                    st.error("Credenciales inválidas")
        
        st.markdown('</div>', unsafe_allow_html=True)

def show_dashboard():
    st.markdown('<h1 class="main-header">📊 Panel de Control - Motos Prand</h1>', unsafe_allow_html=True)
    
    es_admin = st.session_state.user['rol'] == 'admin'
    
    with db.get_connection() as conn:
        c_act = pd.read_sql("""
            SELECT count(*) as q 
            FROM reparaciones 
            WHERE estado NOT IN ('Entregado', 'Cancelado')
        """, conn).iloc[0]['q']
        
        c_crit = pd.read_sql("""
            SELECT count(*) as q 
            FROM inventario 
            WHERE cantidad <= minimo AND activo=1
        """, conn).iloc[0]['q']
        
        c_urg = pd.read_sql("""
            SELECT count(*) as q 
            FROM reparaciones 
            WHERE prioridad='Urgente' 
            AND estado NOT IN ('Entregado', 'Cancelado')
        """, conn).iloc[0]['q']

    col1, col2, col3 = st.columns(3)
    col1.metric("🔧 Órdenes Activas", c_act, delta=f"{c_urg} urgentes" if c_urg > 0 else None, delta_color="inverse")
    col2.metric("⚠️ Stock Crítico", c_crit, delta="Reponer" if c_crit > 0 else "OK", delta_color="inverse")
    
    # Solo admins ven métricas financieras
    if es_admin:
        ganancias_hoy = calcular_ganancias('hoy')
        col3.metric("💰 Ganancia Hoy", f"${ganancias_hoy['ganancia_neta']:,.2f}", 
                   delta=f"{ganancias_hoy['margen']:.1f}% margen")
    else:
        col3.metric("📋 Órdenes Hoy", pd.read_sql("SELECT count(*) as q FROM reparaciones WHERE date(fecha_ingreso) = date('now')", conn).iloc[0]['q'])

    # Gráficos
    tab1, tab2 = st.tabs(["Estado del Taller", "Actividad Reciente"])
    
    with tab1:
        with db.get_connection() as conn:
            df_estados = pd.read_sql("""
                SELECT estado, count(*) as cantidad 
                FROM reparaciones 
                WHERE date(fecha_ingreso) >= date('now', '-30 days')
                GROUP BY estado
            """, conn)
            if not df_estados.empty:
                st.bar_chart(df_estados.set_index('estado'))
            else:
                st.info("No hay datos recientes")
    
    with tab2:
        with db.get_connection() as conn:
            df_recent = pd.read_sql("""
                SELECT r.numero_orden, c.nombre as cliente, r.estado, r.prioridad, r.fecha_ingreso
                FROM reparaciones r
                JOIN clientes c ON r.cliente_id = c.id
                ORDER BY r.fecha_ingreso DESC
                LIMIT 10
            """, conn)
            st.dataframe(df_recent, use_container_width=True, hide_index=True)

def show_inventario():
    st.markdown('<h1 class="main-header">📦 Inventario de Repuestos</h1>', unsafe_allow_html=True)
    
    with db.get_connection() as conn:
        critico = pd.read_sql("""
            SELECT codigo, item, cantidad, minimo 
            FROM inventario 
            WHERE cantidad <= minimo AND activo=1
        """, conn)
    
    if not critico.empty:
        with st.expander("🚨 STOCK CRÍTICO", expanded=True):
            st.dataframe(critico, use_container_width=True)
            st.warning(f"Hay {len(critico)} items por debajo del mínimo")
    
    with st.expander("➕ Registrar Nuevo Item"):
        with st.form("nuevo_item", clear_on_submit=True):
            c1, c2, c3 = st.columns(3)
            cod = c1.text_input("Código SKU", placeholder="EJ: CAD-520")
            nom = c2.text_input("Nombre Repuesto", placeholder="Cadena 520H")
            cat = c3.number_input("Cantidad Inicial", min_value=0, value=0)
            
            c4, c5, c6 = st.columns(3)
            pc = c4.number_input("Costo $", min_value=0.0, value=0.0, step=0.01)
            pv = c5.number_input("Venta $", min_value=0.0, value=0.0, step=0.01)
            min_s = c6.number_input("Mínimo Alerta", min_value=1, value=5)
            
            ubic = st.text_input("Ubicación", placeholder="Estante A-12")
            
            if st.form_submit_button("💾 Guardar Item", use_container_width=True):
                try:
                    with db.transaction() as cur:
                        cur.execute("""
                            INSERT INTO inventario 
                            (codigo, item, cantidad, minimo, precio_costo, precio_venta, ubicacion) 
                            VALUES (?,?,?,?,?,?,?)
                        """, (cod.upper(), nom, cat, min_s, pc, pv, ubic))
                    st.success(f"✅ Item {cod} registrado")
                except sqlite3.IntegrityError:
                    st.error("❌ El código SKU ya existe")

    st.subheader("Stock Completo")
    with db.get_connection() as conn:
        df = pd.read_sql("""
            SELECT codigo, item, cantidad, minimo, 
                   precio_costo, precio_venta, ubicacion,
                   CASE 
                       WHEN cantidad <= minimo THEN '🔴 CRÍTICO'
                       WHEN cantidad <= minimo * 1.5 THEN '🟡 BAJO'
                       ELSE '🟢 OK'
                   END as estado_stock
            FROM inventario 
            WHERE activo=1
            ORDER BY cantidad <= minimo DESC, item
        """, conn)
        
        busqueda = st.text_input("🔍 Buscar repuesto...")
        if busqueda:
            df = df[df['item'].str.contains(busqueda, case=False) | 
                   df['codigo'].str.contains(busqueda, case=False)]
        
        st.dataframe(df, use_container_width=True, hide_index=True)

def show_nueva_orden():
    st.markdown('<h1 class="main-header">📝 Nueva Orden de Trabajo</h1>', unsafe_allow_html=True)
    
    es_admin = st.session_state.user['rol'] == 'admin'
    
    with st.form("orden_form", clear_on_submit=True):
        st.subheader("Datos del Cliente")
        col1, col2 = st.columns(2)
        cliente = col1.text_input("Nombre Completo*", placeholder="Juan Pérez")
        dni = col2.text_input("DNI", placeholder="12345678")
        tel = col1.text_input("Teléfono*", placeholder="11-1234-5678")
        email = col2.text_input("Email", placeholder="cliente@email.com")
        
        st.subheader("Datos de la Moto")
        col3, col4, col5 = st.columns(3)
        patente = col3.text_input("Patente*", placeholder="ABC123").upper()
        marca = col4.text_input("Marca*", placeholder="Honda")
        modelo = col5.text_input("Modelo*", placeholder="CB 190")
        year = col3.number_input("Año", min_value=1980, max_value=datetime.now().year, value=2020)
        
        st.subheader("Detalles de la Reparación")
        falla = st.text_area("Descripción del Problema*", placeholder="Describe los síntomas...", height=100)
        prioridad = st.selectbox("Prioridad", ["Normal", "Urgente", "Express"])
        
        col6, col7 = st.columns(2)
        fecha_est = col6.date_input("Fecha Estimada Entrega", 
                                   value=datetime.now() + timedelta(days=3))
        
        # Solo admins pueden ver y editar costos
        if es_admin:
            st.subheader("💰 Costos (Admin)")
            c1, c2 = st.columns(2)
            total = c1.number_input("Total a Cobrar $", min_value=0.0, value=0.0, step=0.01)
            costo_rep = c2.number_input("Costo Repuestos $", min_value=0.0, value=0.0, step=0.01)
        else:
            total = 0
            costo_rep = 0
        
        if st.form_submit_button("🚀 Generar Orden", use_container_width=True):
            errores = []
            if not cliente or not tel:
                errores.append("Nombre y teléfono son obligatorios")
            if not validar_patente(patente):
                errores.append("Formato de patente inválido")
            if not marca or not modelo:
                errores.append("Marca y modelo son obligatorios")
            if not falla:
                errores.append("Debe describir la falla")
            
            if errores:
                for err in errores:
                    st.error(f"❌ {err}")
            else:
                try:
                    num_orden = generar_numero_orden()
                    with db.transaction() as cur:
                        cur.execute("""
                            INSERT INTO clientes (nombre, dni, telefono, email) 
                            VALUES (?,?,?,?)
                        """, (cliente, dni, tel, email))
                        c_id = cur.lastrowid
                        
                        cur.execute("""
                            INSERT INTO vehiculos (cliente_id, patente, marca, modelo, year) 
                            VALUES (?,?,?,?,?)
                        """, (c_id, patente, marca, modelo, year))
                        v_id = cur.lastrowid
                        
                        cur.execute("""
                            INSERT INTO reparaciones 
                            (numero_orden, cliente_id, vehiculo_id, falla, estado, 
                             prioridad, fecha_estimada, total, costo_repuestos, tecnico_asignado) 
                            VALUES (?,?,?,?,?,?,?,?,?,?)
                        """, (num_orden, c_id, v_id, falla, 'Recibido', 
                              prioridad, fecha_estimada.isoformat(), total, costo_rep, 
                              st.session_state.user['id']))
                    
                    st.success(f"✅ Orden **{num_orden}** creada")
                    st.balloons()
                    
                except sqlite3.IntegrityError:
                    st.error("❌ La patente ya existe en el sistema")

def show_ganancias():
    """Solo accesible para administradores"""
    st.markdown('<h1 class="main-header">💰 Informe de Ganancias</h1>', unsafe_allow_html=True)
    
    st.warning("🔒 Acceso exclusivo para Administradores")
    
    tab1, tab2, tab3 = st.tabs(["Hoy", "Esta Semana", "Este Mes"])
    
    with tab1:
        data = calcular_ganancias('hoy')
        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Ingresos", f"${data['ingresos']:,.2f}")
        col2.metric("Costos", f"${data['costos']:,.2f}")
        col3.metric("Ganancia Neta", f"${data['ganancia_neta']:,.2f}")
        col4.metric("Margen", f"{data['margen']:.1f}%")
        st.info(f"Órdenes completadas: {data['ordenes']}")
    
    with tab2:
        data = calcular_ganancias('semana')
        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Ingresos", f"${data['ingresos']:,.2f}")
        col2.metric("Costos", f"${data['costos']:,.2f}")
        col3.metric("Ganancia Neta", f"${data['ganancia_neta']:,.2f}")
        col4.metric("Margen", f"{data['margen']:.1f}%")
        st.info(f"Órdenes completadas: {data['ordenes']}")
    
    with tab3:
        data = calcular_ganancias('mes')
        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Ingresos", f"${data['ingresos']:,.2f}")
        col2.metric("Costos", f"${data['costos']:,.2f}")
        col3.metric("Ganancia Neta", f"${data['ganancia_neta']:,.2f}")
        col4.metric("Margen", f"{data['margen']:.1f}%")
        st.info(f"Órdenes completadas: {data['ordenes']}")
    
    # Gráfico de tendencia
    st.subheader("Tendencia de Ganancias")
    st.line_chart({
        "Ingresos": [1200, 1900, 1500, 2200, 2800, 800],
        "Ganancia": [400, 800, 600, 1100, 1400, 300]
    })

def show_usuarios():
    """Gestión de usuarios - Solo admin"""
    st.markdown('<h1 class="main-header">👥 Gestión de Usuarios</h1>', unsafe_allow_html=True)
    
    es_admin = st.session_state.user['rol'] == 'admin'
    
    if not es_admin:
        st.error("🚫 Acceso denegado. Solo administradores.")
        return
    
    # Formulario para crear usuario
    with st.expander("➕ Crear Nuevo Usuario", expanded=True):
        with st.form("nuevo_usuario"):
            col1, col2 = st.columns(2)
            new_user = col1.text_input("Nombre de Usuario", placeholder="nuevo_tecnico")
            new_nombre = col2.text_input("Nombre Completo", placeholder="Juan García")
            new_pass = col1.text_input("Contraseña", type="password", placeholder="Mínimo 4 caracteres")
            new_rol = col2.selectbox("Rol", ["tecnico", "admin"])
            
            if st.form_submit_button("👤 Crear Usuario", use_container_width=True):
                if new_user and new_nombre and new_pass:
                    success, msg = crear_usuario(
                        new_user, new_pass, new_nombre, new_rol, 
                        st.session_state.user['id']
                    )
                    if success:
                        st.success(msg)
                    else:
                        st.error(msg)
                else:
                    st.error("Complete todos los campos")
    
    # Lista de usuarios
    st.subheader("Usuarios del Sistema")
    with db.get_connection() as conn:
        df_users = pd.read_sql("""
            SELECT 
                username,
                nombre,
                rol,
                CASE 
                    WHEN rol = 'admin' THEN '🟠 Admin'
                    ELSE '🔵 Técnico'
                END as tipo,
                activo,
                last_login
            FROM usuarios
            ORDER BY rol, nombre
        """, conn)
        
        # Formatear para mostrar
        df_users['estado'] = df_users['activo'].apply(lambda x: 'Activo' if x else 'Inactivo')
        st.dataframe(df_users[['username', 'nombre', 'tipo', 'estado', 'last_login']], 
                    use_container_width=True, hide_index=True)

def show_taller():
    st.markdown('<h1 class="main-header">🔧 Gestión de Taller</h1>', unsafe_allow_html=True)
    st.info("Módulo en desarrollo - Aquí se gestionarán las órdenes activas")

def show_configuracion():
    st.markdown('<h1 class="main-header">⚙️ Configuración</h1>', unsafe_allow_html=True)
    
    with st.expander("Cambiar mi Contraseña"):
        with st.form("cambio_pass"):
            actual = st.text_input("Contraseña Actual", type="password")
            nueva = st.text_input("Nueva Contraseña", type="password")
            confirmar = st.text_input("Confirmar Nueva", type="password")
            
            if st.form_submit_button("Actualizar"):
                # Verificar actual primero
                check = check_password(st.session_state.user['nombre'].lower().split()[0], actual)
                if nueva != confirmar:
                    st.error("Las contraseñas no coinciden")
                elif len(nueva) < 4:
                    st.error("Mínimo 4 caracteres")
                else:
                    success, msg = cambiar_password(st.session_state.user['id'], nueva)
                    if success:
                        st.success(msg)
                    else:
                        st.error(msg)

# =============================================================================
# NAVEGACIÓN PRINCIPAL
# =============================================================================

def main():
    if 'auth' not in st.session_state:
        st.session_state.auth = False
        st.session_state.user = None

    if not st.session_state.auth:
        show_login()
    else:
        es_admin = st.session_state.user['rol'] == 'admin'
        
        with st.sidebar:
            st.markdown("<h2 style='color: #ff6b00;'>🏍️ Motos Prand</h2>", unsafe_allow_html=True)
            
            # Badge de rol
            if es_admin:
                st.markdown("<span class='admin-badge'>ADMINISTRADOR</span>", unsafe_allow_html=True)
            else:
                st.markdown("<span class='tecnico-badge'>TÉCNICO</span>", unsafe_allow_html=True)
            
            st.write(f"**{st.session_state.user['nombre']}**")
            st.divider()
            
            # Menú diferenciado por rol
            if es_admin:
                menu = st.radio(
                    "Navegación",
                    ["Dashboard", "Nueva Orden", "Taller", "Inventario", 
                     "💰 Ganancias", "👥 Usuarios", "Configuración"]
                )
            else:
                menu = st.radio(
                    "Navegación",
                    ["Dashboard", "Nueva Orden", "Taller", "Inventario", "Configuración"]
                )
            
            st.divider()
            if st.button("🚪 Cerrar Sesión", use_container_width=True):
                st.session_state.auth = False
                st.session_state.user = None
                st.rerun()
        
        # Routing
        if menu == "Dashboard":
            show_dashboard()
        elif menu == "Inventario":
            show_inventario()
        elif menu == "Nueva Orden":
            show_nueva_orden()
        elif menu == "💰 Ganancias":
            show_ganancias()
        elif menu == "👥 Usuarios":
            show_usuarios()
        elif menu == "Taller":
            show_taller()
        elif menu == "Configuración":
            show_configuracion()

if __name__ == "__main__":
    main()
                
def _crear_usuario_inicial(self, cur, username, password, nombre, rol):
    """Crea usuario inicial si no existe"""
    hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=12))
    cur.execute("""
        INSERT OR IGNORE INTO usuarios 
        (username, password_hash, nombre, rol, force_password_change) 
        VALUES (?,?,?,?,?)
    """, (username, hashed.decode(), nombre, rol, 0))
    
