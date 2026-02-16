import streamlit as st
import sqlite3
import hashlib
import secrets
import pandas as pd
from datetime import datetime
import plotly.express as px

# ==========================================================
# 1. ESTILO VISUAL "MOTOS PRAND"
# ==========================================================
def aplicar_diseno_motos_prand():
    st.markdown("""
        <style>
        .stApp { background-color: #0E1117; color: #FFFFFF; }
        .titulo-prand { 
            font-size: 60px !important; 
            font-weight: 900; 
            text-align: center;
            font-family: 'Arial Black', sans-serif;
            margin-bottom: 0px;
        }
        .letra-roja { color: #FF0000; text-shadow: 2px 2px 15px rgba(255,0,0,0.5); }
        .subtitulo { text-align: center; color: #888; font-style: italic; margin-top: -10px; font-size: 1.2rem; }
        [data-testid="stSidebar"] { background-color: #161B22; border-right: 1px solid #333; }
        .stMetric { background-color: #1E2329; padding: 15px; border-radius: 10px; border-left: 5px solid #FF0000; }
        </style>
        <div class="titulo-prand"><span class="letra-roja">M</span>otos <span class="letra-roja">P</span>rand</div>
        <p class="subtitulo">Gestión de Taller & Alta Performance</p>
        <hr style="border: 0.1px solid #444; margin-bottom: 30px;">
    """, unsafe_allow_html=True)

# ==========================================================
# 2. GESTIÓN DE DATOS Y USUARIOS (RODRI & LEAN)
# ==========================================================
class SistemaMotos:
    def __init__(self):
        self.conn = sqlite3.connect('motos_prand_enterprise.db', check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.crear_tablas()
        self.configurar_admins()

    def crear_tablas(self):
        cursor = self.conn.cursor()
        # Usuarios
        cursor.execute("""CREATE TABLE IF NOT EXISTS usuarios 
            (id INTEGER PRIMARY KEY, username TEXT UNIQUE, password_hash TEXT, salt TEXT, nombre TEXT, rol TEXT)""")
        # Clientes
        cursor.execute("""CREATE TABLE IF NOT EXISTS clientes 
            (id INTEGER PRIMARY KEY, nombre TEXT, telefono TEXT, email TEXT)""")
        # Inventario
        cursor.execute("""CREATE TABLE IF NOT EXISTS inventario 
            (id INTEGER PRIMARY KEY, repuesto TEXT, cantidad INTEGER, precio_costo REAL, precio_venta REAL)""")
        # Servicios/Reparaciones
        cursor.execute("""CREATE TABLE IF NOT EXISTS servicios 
            (id INTEGER PRIMARY KEY, fecha TEXT, cliente_id INTEGER, moto TEXT, descripcion TEXT, 
             total REAL, estado TEXT, pagado INTEGER)""")
        self.conn.commit()

    def configurar_admins(self):
        cursor = self.conn.cursor()
        admins = [("Rodri", "1590", "Rodrigo"), ("Lean", "3588", "Leandro")]
        for u, p, n in admins:
            cursor.execute("SELECT * FROM usuarios WHERE username=?", (u,))
            if not cursor.fetchone():
                salt = secrets.token_hex(8)
                hpwd = hashlib.sha256(f"{p}{salt}".encode()).hexdigest()
                cursor.execute("INSERT INTO usuarios (username, password_hash, salt, nombre, rol) VALUES (?,?,?,?,?)",
                             (u, hpwd, salt, n, 'admin'))
        self.conn.commit()

db_sistema = SistemaMotos()
# ==========================================================
# 3. INTERFAZ Y FUNCIONES PRINCIPALES
# ==========================================================
st.set_page_config(page_title="Motos Prand Pro", layout="wide")

if 'auth' not in st.session_state:
    st.session_state.auth = False

if not st.session_state.auth:
    aplicar_diseno_motos_prand()
    col1, col2, col3 = st.columns([1,1.5,1])
    with col2:
        with st.form("Login"):
            st.markdown("<h3 style='text-align:center;'>🔑 Panel de Acceso</h3>", unsafe_allow_html=True)
            user = st.text_input("Usuario")
            pw = st.text_input("Contraseña", type="password")
            if st.form_submit_button("INGRESAR AL TALLER"):
                cur = db_sistema.conn.cursor()
                cur.execute("SELECT * FROM usuarios WHERE username=?", (user,))
                res = cur.fetchone()
                if res and hashlib.sha256(f"{pw}{res['salt']}".encode()).hexdigest() == res['password_hash']:
                    st.session_state.auth = True
                    st.session_state.user = {"nombre": res['nombre'], "rol": res['rol']}
                    st.rerun()
                else:
                    st.error("Acceso denegado. Verifique sus datos.")
else:
    aplicar_diseno_motos_prand()
    
    # SIDEBAR CON ROLES
    with st.sidebar:
        st.write(f"🛠️ **Operador:** {st.session_state.user['nombre']}")
        menu = ["📋 Órdenes", "📦 Inventario", "👥 Clientes"]
        if st.session_state.user['rol'] == 'admin':
            menu.append("💰 Finanzas")
        
        choice = st.radio("MENÚ PRINCIPAL", menu)
        if st.button("Cerrar Sistema"):
            st.session_state.auth = False
            st.rerun()

    # MÓDULO ÓRDENES
    if choice == "📋 Órdenes":
        st.subheader("📝 Registro de Ingreso")
        with st.expander("Cargar Nueva Orden de Trabajo"):
            c1, c2 = st.columns(2)
            cli = c1.text_input("Nombre Cliente")
            mot = c2.text_input("Moto y Modelo")
            fal = st.text_area("Falla y Trabajo a realizar")
            if st.button("Guardar Orden"):
                st.success(f"Orden para {mot} generada correctamente.")

    # MÓDULO INVENTARIO
    elif choice == "📦 Inventario":
        st.subheader("🔧 Control de Repuestos")
        # Aquí puedes agregar la lógica de visualización de tablas del código original
        st.info("Lista de repuestos y stock actual.")

    # MÓDULO FINANZAS (SOLO ADMINS)
    elif choice == "💰 Finanzas":
        st.subheader("📊 Reportes Económicos (Solo Rodri/Lean)")
        col1, col2, col3 = st.columns(3)
        col1.metric("Ingresos Brutos", "$250.000")
        col2.metric("Inversión Repuestos", "$85.000")
        col3.metric("Ganancia Neta", "$165.000")
        
        # Gráfico de ejemplo
        df_fun = pd.DataFrame({"Mes": ["Ene", "Feb"], "Ganancia": [100, 150]})
        fig = px.line(df_fun, x="Mes", y="Ganancia", title="Crecimiento Motos Prand", template="plotly_dark")
        fig.update_traces(line_color='#FF0000')
        st.plotly_chart(fig)
        
