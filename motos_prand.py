import streamlit as st
import pandas as pd
import sqlite3
import secrets
import re
import os
import shutil
import bcrypt
import threading
import time
import schedule
from datetime import datetime, timedelta
from contextlib import contextmanager
from dataclasses import dataclass
from typing import List, Dict, Optional
from pathlib import Path

# =============================================================================
# CONFIGURACIÓN INICIAL
# =============================================================================

st.set_page_config(
    page_title="Motos Prand - Gestión de Taller",
    page_icon="🏍️",
    layout="wide",
    initial_sidebar_state="expanded"
)

@dataclass
class Config:
    DB_PATH: str = os.path.join(os.getcwd(), 'motos_prand.db')
    BACKUP_DIR: str = os.path.join(os.getcwd(), 'backups')
    SESSION_TIMEOUT: int = 480
    MAX_LOGIN_ATTEMPTS: int = 3
    LOCKOUT_TIME: int = 15

config = Config()
Path(config.BACKUP_DIR).mkdir(exist_ok=True)

# CSS Personalizado Motos Prand
st.markdown("""
<style>
    .main-header { color: #ff6b00; font-size: 2.2rem; font-weight: 700; margin-bottom: 1rem; border-bottom: 3px solid #ff6b00; padding-bottom: 0.5rem; }
    .stMetric { background: linear-gradient(135deg, #fff5eb 0%, #ffe4cc 100%); padding: 20px; border-radius: 12px; border: 1px solid #ffcc99; box-shadow: 0 2px 4px rgba(255,107,0,0.1); }
    [data-testid="stSidebar"] { background-color: #1a1a2e; }
    [data-testid="stSidebar"] * { color: white !important; }
    .admin-badge { background: #ff6b00; color: white; padding: 2px 8px; border-radius: 12px; font-size: 0.8em; }
    .tecnico-badge { background: #4ecdc4; color: white; padding: 2px 8px; border-radius: 12px; font-size: 0.8em; }
    .login-container { max-width: 400px; margin: 0 auto; padding: 2rem; background: white; border-radius: 16px; box-shadow: 0 10px 25px rgba(255,107,0,0.2); }
</style>
""", unsafe_allow_html=True)

# =============================================================================
# BASE DE DATOS
# =============================================================================

class DatabaseManager:
    def __init__(self):
        self._local = threading.local()
        self.init_db()
    
    def get_connection(self):
        if not hasattr(self._local, 'conn') or self._local.conn is None:
            self._local.conn = sqlite3.connect(config.DB_PATH, check_same_thread=False, timeout=20.0)
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
        except Exception as e:
            conn.rollback()
            st.error(f"Error: {e}")
            raise e

    def init_db(self):
        with self.transaction() as cur:
            cur.executescript("""
                CREATE TABLE IF NOT EXISTS usuarios (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL, 
                    nombre TEXT NOT NULL, rol TEXT DEFAULT 'tecnico', activo INTEGER DEFAULT 1,
                    last_login TIMESTAMP, failed_attempts INTEGER DEFAULT 0, locked_until TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS clientes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    nombre TEXT NOT NULL, telefono TEXT, email TEXT, dni TEXT UNIQUE,
                    activo INTEGER DEFAULT 1, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS vehiculos (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    cliente_id INTEGER NOT NULL, patente TEXT UNIQUE NOT NULL, 
                    marca TEXT NOT NULL, modelo TEXT NOT NULL, year INTEGER,
                    FOREIGN KEY(cliente_id) REFERENCES clientes(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS inventario (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    codigo TEXT UNIQUE NOT NULL, item TEXT NOT NULL, cantidad INTEGER DEFAULT 0, 
                    minimo INTEGER DEFAULT 5, precio_costo REAL DEFAULT 0, precio_venta REAL DEFAULT 0,
                    ubicacion TEXT, activo INTEGER DEFAULT 1
                );
                CREATE TABLE IF NOT EXISTS reparaciones (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    numero_orden TEXT UNIQUE NOT NULL, cliente_id INTEGER NOT NULL, 
                    vehiculo_id INTEGER NOT NULL, falla TEXT NOT NULL, estado TEXT DEFAULT 'Recibido', 
                    prioridad TEXT DEFAULT 'Normal', total REAL DEFAULT 0, costo_repuestos REAL DEFAULT 0,
                    fecha_ingreso TIMESTAMP DEFAULT CURRENT_TIMESTAMP, fecha_estimada DATE, pagado INTEGER DEFAULT 0,
                    tecnico_asignado INTEGER,
                    FOREIGN KEY(cliente_id) REFERENCES clientes(id),
                    FOREIGN KEY(vehiculo_id) REFERENCES vehiculos(id)
                );
            """)
            # Usuarios iniciales
            self._crear_usuario_inicial(cur, 'rodri', '1590', 'Rodri', 'admin')
            self._crear_usuario_inicial(cur, 'lean', '3588', 'Lean', 'admin')

    def _crear_usuario_inicial(self, cur, username, password, nombre, rol):
        cur.execute("SELECT id FROM usuarios WHERE username=?", (username,))
        if not cur.fetchone():
            hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt())
            cur.execute("INSERT INTO usuarios (username, password_hash, nombre, rol) VALUES (?,?,?,?)", 
                       (username, hashed.decode(), nombre, rol))

db = DatabaseManager()

# =============================================================================
# SEGURIDAD Y UTILIDADES
# =============================================================================

def check_password(username, password):
    with db.transaction() as cur:
        cur.execute("SELECT * FROM usuarios WHERE username=? AND activo=1", (username.lower(),))
        user = cur.fetchone()
        if user and bcrypt.checkpw(password.encode(), user['password_hash'].encode()):
            return dict(user)
    return None

def validar_patente(patente: str) -> bool:
    patrones = [r'^[A-Z]{3}\d{3}$', r'^[A-Z]{2}\d{3}[A-Z]{2}$']
    return any(re.match(p, patente.upper()) for p in patrones)

def generar_numero_orden() -> str:
    return f"MP-{datetime.now().strftime('%Y%m%d')}-{secrets.token_hex(2).upper()}"

def calcular_ganancias(periodo: str = 'hoy') -> dict:
    with db.get_connection() as conn:
        if periodo == 'hoy':
            query = "SELECT SUM(total) as ingresos, SUM(costo_repuestos) as costos, COUNT(*) as ordenes FROM reparaciones WHERE date(fecha_ingreso) = date('now') AND pagado=1"
        elif periodo == 'mes':
            query = "SELECT SUM(total) as ingresos, SUM(costo_repuestos) as costos, COUNT(*) as ordenes FROM reparaciones WHERE strftime('%Y-%m', fecha_ingreso) = strftime('%Y-%m', 'now') AND pagado=1"
        else: # semana
            query = "SELECT SUM(total) as ingresos, SUM(costo_repuestos) as costos, COUNT(*) as ordenes FROM reparaciones WHERE fecha_ingreso >= date('now', '-7 days') AND pagado=1"
        
        res = pd.read_sql(query, conn).fillna(0).iloc[0]
        ingresos = res['ingresos']
        costos = res['costos']
        ganancia = ingresos - costos
        margen = (ganancia / ingresos * 100) if ingresos > 0 else 0
        return {'ingresos': ingresos, 'costos': costos, 'ganancia_neta': ganancia, 'margen': margen, 'ordenes': int(res['ordenes'])}

# =============================================================================
# VISTAS DE LA INTERFAZ
# =============================================================================

def show_login():
    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        st.markdown('<div class="login-container">', unsafe_allow_html=True)
        st.markdown("<h1 style='text-align: center; color: #ff6b00;'>🏍️ Motos Prand</h1>", unsafe_allow_html=True)
        with st.form("login_form"):
            user = st.text_input("Usuario")
            pw = st.text_input("Contraseña", type="password")
            if st.form_submit_button("Iniciar Sesión", use_container_width=True):
                res = check_password(user, pw)
                if res:
                    st.session_state.auth = True
                    st.session_state.user = res
                    st.rerun()
                else:
                    st.error("Credenciales inválidas")
        st.markdown('</div>', unsafe_allow_html=True)

def show_dashboard():
    st.markdown('<h1 class="main-header">📊 Panel de Control</h1>', unsafe_allow_html=True)
    es_admin = st.session_state.user['rol'] == 'admin'
    
    with db.get_connection() as conn:
        c_act = pd.read_sql("SELECT count(*) as q FROM reparaciones WHERE estado NOT IN ('Entregado', 'Cancelado')", conn).iloc[0]['q']
        c_crit = pd.read_sql("SELECT count(*) as q FROM inventario WHERE cantidad <= minimo AND activo=1", conn).iloc[0]['q']
    
    col1, col2, col3 = st.columns(3)
    col1.metric("🔧 Órdenes Activas", c_act)
    col2.metric("⚠️ Stock Crítico", c_crit)
    
    if es_admin:
        g = calcular_ganancias('hoy')
        col3.metric("💰 Ganancia Hoy", f"${g['ganancia_neta']:,.2f}")
    else:
        col3.metric("📅 Fecha", datetime.now().strftime("%d/%m/%Y"))

def show_inventario():
    st.markdown('<h1 class="main-header">📦 Inventario</h1>', unsafe_allow_html=True)
    with st.expander("➕ Registrar Nuevo Item"):
        with st.form("nuevo_item"):
            c1, c2 = st.columns(2)
            cod = c1.text_input("Código SKU")
            nom = c2.text_input("Nombre Repuesto")
            pc = c1.number_input("Costo $", min_value=0.0)
            pv = c2.number_input("Venta $", min_value=0.0)
            stock = st.number_input("Cantidad Inicial", min_value=0)
            if st.form_submit_button("Guardar"):
                with db.transaction() as cur:
                    cur.execute("INSERT INTO inventario (codigo, item, cantidad, precio_costo, precio_venta) VALUES (?,?,?,?,?)",
                               (cod.upper(), nom, stock, pc, pv))
                st.success("Guardado")

    with db.get_connection() as conn:
        df = pd.read_sql("SELECT codigo, item, cantidad, precio_venta, ubicacion FROM inventario WHERE activo=1", conn)
        st.dataframe(df, use_container_width=True, hide_index=True)

def show_nueva_orden():
    st.markdown('<h1 class="main-header">📝 Nueva Orden</h1>', unsafe_allow_html=True)
    with st.form("orden_form"):
        col1, col2 = st.columns(2)
        cliente = col1.text_input("Cliente*")
        tel = col2.text_input("Teléfono*")
        patente = col1.text_input("Patente*").upper()
        marca = col2.text_input("Marca/Modelo*")
        falla = st.text_area("Falla Reportada*")
        
        if st.form_submit_button("Generar Orden"):
            if not cliente or not patente or not falla:
                st.error("Completa los campos obligatorios")
            elif not validar_patente(patente):
                st.error("Patente inválida")
            else:
                with db.transaction() as cur:
                    cur.execute("INSERT INTO clientes (nombre, telefono) VALUES (?,?)", (cliente, tel))
                    c_id = cur.lastrowid
                    cur.execute("INSERT INTO vehiculos (cliente_id, patente, marca, modelo) VALUES (?,?,?,?)", (c_id, patente, marca, ""))
                    v_id = cur.lastrowid
                    n_o = generar_numero_orden()
                    cur.execute("INSERT INTO reparaciones (numero_orden, cliente_id, vehiculo_id, falla, tecnico_asignado) VALUES (?,?,?,?,?)",
                               (n_o, c_id, v_id, falla, st.session_state.user['id']))
                st.success(f"Orden {n_o} Creada")

def show_ganancias():
    st.markdown('<h1 class="main-header">💰 Informe de Ganancias</h1>', unsafe_allow_html=True)
    t1, t2 = st.tabs(["Hoy", "Este Mes"])
    with t1:
        d = calcular_ganancias('hoy')
        st.metric("Ingresos Hoy", f"${d['ingresos']:,.2f}")
        st.metric("Ganancia Neta", f"${d['ganancia_neta']:,.2f}", delta=f"{d['margen']:.1f}% margen")
    with t2:
        d = calcular_ganancias('mes')
        st.metric("Ingresos Mes", f"${d['ingresos']:,.2f}")
        st.metric("Ganancia Neta", f"${d['ganancia_neta']:,.2f}")

# =============================================================================
# NAVEGACIÓN
# =============================================================================

def main():
    if 'auth' not in st.session_state:
        st.session_state.auth = False

    if not st.session_state.auth:
        show_login()
    else:
        user = st.session_state.user
        es_admin = user['rol'] == 'admin'
        
        with st.sidebar:
            st.markdown(f"### 🏍️ Motos Prand")
            st.markdown(f"Hola, **{user['nombre']}**")
            st.markdown(f"<span class='{'admin-badge' if es_admin else 'tecnico-badge'}'>{user['rol'].upper()}</span>", unsafe_allow_html=True)
            st.divider()
            
            menu = ["📊 Dashboard", "📝 Nueva Orden", "📦 Inventario"]
            if es_admin:
                menu += ["💰 Ganancias"]
            
            choice = st.sidebar.radio("Navegación", menu)
            
            st.divider()
            if st.button("🚪 Cerrar Sesión"):
                st.session_state.auth = False
                st.rerun()

        if choice == "📊 Dashboard": show_dashboard()
        elif choice == "📝 Nueva Orden": show_nueva_orden()
        elif choice == "📦 Inventario": show_inventario()
        elif choice == "💰 Ganancias": show_ganancias()

if __name__ == "__main__":
    main()
