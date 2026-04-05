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
# CONFIGURACIÓN E INTERFAZ
# =============================================================================

st.set_page_config(
    page_title="Motos Prand - Gestión de Taller",
    page_icon="🏍️",
    layout="wide",
    initial_sidebar_state="expanded"
)

@dataclass
class Config:
    # Usamos una ruta absoluta para evitar problemas en servidores cloud
    DB_PATH: str = os.path.join(os.getcwd(), 'motos_prand.db')
    BACKUP_DIR: str = os.path.join(os.getcwd(), 'backups')
    SESSION_TIMEOUT: int = 480
    MAX_LOGIN_ATTEMPTS: int = 3
    LOCKOUT_TIME: int = 15

config = Config()
Path(config.BACKUP_DIR).mkdir(exist_ok=True)

# CSS Personalizado
st.markdown("""
<style>
    .main-header { color: #ff6b00; font-size: 2.2rem; font-weight: 700; border-bottom: 3px solid #ff6b00; }
    .stMetric { background: linear-gradient(135deg, #fff5eb 0%, #ffe4cc 100%); padding: 20px; border-radius: 12px; border: 1px solid #ffcc99; }
    [data-testid="stSidebar"] { background-color: #1a1a2e; }
    [data-testid="stSidebar"] * { color: white !important; }
    .admin-badge { background: #ff6b00; color: white; padding: 2px 8px; border-radius: 12px; font-size: 0.8em; }
    .tecnico-badge { background: #4ecdc4; color: white; padding: 2px 8px; border-radius: 12px; font-size: 0.8em; }
</style>
""", unsafe_allow_html=True)

# =============================================================================
# GESTIÓN DE BASE DE DATOS
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
            st.error(f"Error de base de datos: {e}")
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
                    locked_until TIMESTAMP
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
                    codigo TEXT UNIQUE NOT NULL, item TEXT NOT NULL, 
                    cantidad INTEGER DEFAULT 0, minimo INTEGER DEFAULT 5,
                    precio_costo REAL DEFAULT 0, precio_venta REAL DEFAULT 0,
                    ubicacion TEXT, activo INTEGER DEFAULT 1
                );
                CREATE TABLE IF NOT EXISTS reparaciones (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    numero_orden TEXT UNIQUE NOT NULL, cliente_id INTEGER NOT NULL, 
                    vehiculo_id INTEGER NOT NULL, falla TEXT NOT NULL, 
                    estado TEXT DEFAULT 'Recibido', prioridad TEXT DEFAULT 'Normal',
                    total REAL DEFAULT 0, costo_repuestos REAL DEFAULT 0,
                    fecha_ingreso TIMESTAMP DEFAULT CURRENT_TIMESTAMP, pagado INTEGER DEFAULT 0,
                    tecnico_asignado INTEGER,
                    FOREIGN KEY(cliente_id) REFERENCES clientes(id),
                    FOREIGN KEY(vehiculo_id) REFERENCES vehiculos(id)
                );
            """)
            # Crear admin por defecto si no hay usuarios
            cur.execute("SELECT count(*) as q FROM usuarios")
            if cur.fetchone()['q'] == 0:
                hashed = bcrypt.hashpw("1590".encode(), bcrypt.gensalt())
                cur.execute("INSERT INTO usuarios (username, password_hash, nombre, rol) VALUES (?,?,?,?)",
                           ('rodri', hashed.decode(), 'Rodrigo', 'admin'))

db = DatabaseManager()

# =============================================================================
# FUNCIONES DE LÓGICA Y SEGURIDAD
# =============================================================================

def check_password(username, password):
    with db.transaction() as cur:
        cur.execute("SELECT * FROM usuarios WHERE username=? AND activo=1", (username.lower(),))
        user = cur.fetchone()
        if user and bcrypt.checkpw(password.encode(), user['password_hash'].encode()):
            return dict(user)
    return None

def validar_patente(patente: str) -> bool:
    return bool(re.match(r'^[A-Z]{2,3}\d{3}[A-Z]{0,2}$', patente.upper()))

# =============================================================================
# VISTAS (MÓDULOS)
# =============================================================================

def show_login():
    col1, col2, col3 = st.columns([1, 1.5, 1])
    with col2:
        st.markdown("<h1 style='text-align: center; color: #ff6b00;'>🏍️ Motos Prand</h1>", unsafe_allow_html=True)
        with st.form("login"):
            u = st.text_input("Usuario")
            p = st.text_input("Contraseña", type="password")
            if st.form_submit_button("Entrar", use_container_width=True):
                user_data = check_password(u, p)
                if user_data:
                    st.session_state.auth = True
                    st.session_state.user = user_data
                    st.rerun()
                else:
                    st.error("Usuario o contraseña incorrectos")

def show_dashboard():
    st.markdown('<h1 class="main-header">📊 Panel de Control</h1>', unsafe_allow_html=True)
    with db.get_connection() as conn:
        res = pd.read_sql("SELECT count(*) as q FROM reparaciones WHERE estado != 'Entregado'", conn)
        st.metric("Órdenes Activas", res.iloc[0]['q'])

def show_nueva_orden():
    st.markdown('<h1 class="main-header">📝 Nueva Orden</h1>', unsafe_allow_html=True)
    # Aquí iría el formulario que ya tienes...
    st.write("Formulario de ingreso de motos...")

# =============================================================================
# NAVEGACIÓN PRINCIPAL
# =============================================================================

def main():
    if 'auth' not in st.session_state:
        st.session_state.auth = False

    if not st.session_state.auth:
        show_login()
    else:
        user = st.session_state.user
        with st.sidebar:
            st.title("Motos Prand")
            st.markdown(f"Usuario: **{user['nombre']}**")
            st.markdown(f"<span class='admin-badge'>{user['rol'].upper()}</span>", unsafe_allow_html=True)
            st.divider()
            
            opciones = ["Dashboard", "Nueva Orden", "Inventario", "Configuración"]
            if user['rol'] == 'admin':
                opciones += ["Usuarios", "Ganancias"]
            
            choice = st.radio("Menú", opciones)
            
            if st.button("Cerrar Sesión"):
                st.session_state.auth = False
                st.rerun()

        if choice == "Dashboard": show_dashboard()
        elif choice == "Nueva Orden": show_nueva_orden()
        elif choice == "Configuración": st.write("Ajustes de perfil...")
        elif choice == "Usuarios" and user['rol'] == 'admin': st.write("Gestión de personal...")

if __name__ == "__main__":
    main()
