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
import bcrypt  # NUEVO: pip install bcrypt
from io import BytesIO
import base64

# =============================================================================
# CONFIGURACIÓN INICIAL MEJORADA
# =============================================================================

st.set_page_config(
    page_title="SAT Pro Enterprise - Gestión de Taller",
    page_icon="🔧",
    layout="wide",
    initial_sidebar_state="expanded"
)

@dataclass
class Config:
    DB_PATH: str = 'gestion_taller_enterprise.db'
    BACKUP_DIR: str = './backups'
    SESSION_TIMEOUT: int = 480  # minutos
    MAX_LOGIN_ATTEMPTS: int = 3
    LOCKOUT_TIME: int = 15  # minutos

config = Config()
Path(config.BACKUP_DIR).mkdir(exist_ok=True)

# CSS Industrial Mejorado
st.markdown("""
<style>
    .main-header { 
        color: #1a73e8; 
        font-size: 2.2rem; 
        font-weight: 700; 
        margin-bottom: 1rem;
        border-bottom: 3px solid #1a73e8;
        padding-bottom: 0.5rem;
    }
    .stMetric { 
        background: linear-gradient(135deg, #f8f9fa 0%, #e9ecef 100%); 
        padding: 20px; 
        border-radius: 12px; 
        border: 1px solid #dee2e6;
        box-shadow: 0 2px 4px rgba(0,0,0,0.05);
    }
    [data-testid="stSidebar"] { 
        background-color: #1e293b; 
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
        box-shadow: 0 10px 25px rgba(0,0,0,0.1);
    }
</style>
""", unsafe_allow_html=True)

# =============================================================================
# BASE DE DATOS MEJORADA (Seguridad + Concurrencia)
# =============================================================================

class DatabaseManager:
    def __init__(self):
        self._local = threading.local()
        self.init_db()
        self._setup_backup_schedule()
    
    def get_connection(self):
        """Thread-safe connection con timeout para concurrencia"""
        if not hasattr(self._local, 'conn') or self._local.conn is None:
            self._local.conn = sqlite3.connect(
                config.DB_PATH, 
                check_same_thread=False,
                timeout=20.0,  # Espera 20s si está lockeada
                isolation_level=None  # Autocommit mode para evitar locks largos
            )
            self._local.conn.row_factory = sqlite3.Row
            self._local.conn.execute("PRAGMA foreign_keys = ON")
            self._local.conn.execute("PRAGMA journal_mode = WAL")  # Mejor concurrencia
        return self._local.conn

    @contextmanager
    def transaction(self):
        conn = self.get_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("BEGIN IMMEDIATE")  # Lockeo inmediato
            yield cursor
            conn.commit()
        except sqlite3.OperationalError as e:
            conn.rollback()
            st.error(f"Error de base de datos (posible concurrencia): {e}")
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
                    force_password_change INTEGER DEFAULT 1,
                    last_login TIMESTAMP,
                    failed_attempts INTEGER DEFAULT 0,
                    locked_until TIMESTAMP
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
                    tipo TEXT,  -- 'entrada', 'salida', 'ajuste'
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
            
            # Usuario Admin seguro (debe cambiar contraseña al entrar)
            cur.execute("SELECT count(*) FROM usuarios WHERE username='admin'")
            if cur.fetchone()[0] == 0:
                # Generar hash seguro con bcrypt
                hashed = bcrypt.hashpw(b"admin123", bcrypt.gensalt(rounds=12))
                cur.execute("""
                    INSERT INTO usuarios 
                    (username, password_hash, nombre, rol, force_password_change) 
                    VALUES (?,?,?,?,?)
                """, ('admin', hashed.decode(), 'Administrador Master', 'admin', 1))

    def _setup_backup_schedule(self):
        """Configura backup automático diario"""
        schedule.every().day.at("02:00").do(self._backup_db)
        def run_schedule():
            while True:
                schedule.run_pending()
                time.sleep(60)
        threading.Thread(target=run_schedule, daemon=True).start()
    
    def _backup_db(self):
        """Crea backup con timestamp"""
        try:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_path = Path(config.BACKUP_DIR) / f"backup_{timestamp}.db"
            shutil.copy(config.DB_PATH, backup_path)
            # Mantener solo últimos 7 backups
            backups = sorted(Path(config.BACKUP_DIR).glob("backup_*.db"))
            for old_backup in backups[:-7]:
                old_backup.unlink()
        except Exception as e:
            print(f"Error en backup: {e}")

db = DatabaseManager()

# =============================================================================
# SEGURIDAD MEJORADA (bcrypt + Rate Limiting)
# =============================================================================

def check_password(username: str, password: str) -> Optional[Dict]:
    """Autenticación segura con protección contra fuerza bruta"""
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
        
        # Verificar si está bloqueado
        if user['locked_until']:
            locked_until = datetime.fromisoformat(user['locked_until'])
            if datetime.now() < locked_until:
                mins_left = (locked_until - datetime.now()).seconds // 60
                st.error(f"Cuenta bloqueada. Intente en {mins_left} minutos.")
                return None
        
        # Verificar contraseña con bcrypt
        if bcrypt.checkpw(password.encode(), user['password_hash'].encode()):
            # Resetear intentos fallidos
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
            # Incrementar intentos fallidos
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

def change_password(user_id: int, new_password: str):
    """Cambio seguro de contraseña"""
    if len(new_password) < 8:
        return False, "La contraseña debe tener al menos 8 caracteres"
    
    hashed = bcrypt.hashpw(new_password.encode(), bcrypt.gensalt(rounds=12))
    with db.transaction() as cur:
        cur.execute("""
            UPDATE usuarios 
            SET password_hash=?, force_password_change=0 
            WHERE id=?
        """, (hashed.decode(), user_id))
    return True, "Contraseña actualizada"

# =============================================================================
# UTILIDADES DE VALIDACIÓN
# =============================================================================

def validar_patente(patente: str) -> bool:
    """Valida formato de patente argentina/mercosur"""
    patrones = [
        r'^[A-Z]{3}\d{3}$',           # Vieja: ABC123
        r'^[A-Z]{2}\d{3}[A-Z]{2}$'    # Mercosur: AB123CD
    ]
    return any(re.match(p, patente.upper()) for p in patrones)

def generar_numero_orden() -> str:
    """Genera número de orden único con formato ORD-YYYYMMDD-XXXX"""
    fecha = datetime.now().strftime("%Y%m%d")
    random_suffix = secrets.token_hex(2).upper()
    return f"ORD-{fecha}-{random_suffix}"

# =============================================================================
# MÓDULOS DE INTERFAZ MEJORADOS
# =============================================================================

def show_dashboard():
    st.markdown('<h1 class="main-header">📊 Panel de Control</h1>', unsafe_allow_html=True)
    
    # Métricas en tiempo real
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
        
        c_rev = pd.read_sql("""
            SELECT COALESCE(sum(total), 0) as q 
            FROM reparaciones 
            WHERE date(fecha_ingreso) = date('now') 
            AND pagado=1
        """, conn).iloc[0]['q']
        
        c_urg = pd.read_sql("""
            SELECT count(*) as q 
            FROM reparaciones 
            WHERE prioridad='Urgente' 
            AND estado NOT IN ('Entregado', 'Cancelado')
        """, conn).iloc[0]['q']

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("🔧 Órdenes Activas", c_act, delta=f"{c_urg} urgentes" if c_urg > 0 else None, delta_color="inverse")
    col2.metric("⚠️ Stock Crítico", c_crit, delta="Reponer" if c_crit > 0 else "OK", delta_color="inverse")
    col3.metric("💰 Ingresos Hoy", f"${c_rev:,.2f}")
    col4.metric("⏱️ Tiempo Resp.", "2.3 días")  # Placeholder para métrica real
    
    # Gráficos
    tab1, tab2 = st.tabs(["Estado del Taller", "Ingresos Semanales"])
    
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
        st.line_chart({"Lun": 1200, "Mar": 1900, "Mié": 1500, "Jue": 2200, "Vie": 2800, "Sáb": 800})

def show_inventario():
    st.markdown('<h1 class="main-header">📦 Inventario de Repuestos</h1>', unsafe_allow_html=True)
    
    # Alertas de stock crítico
    with db.get_connection() as conn:
        critico = pd.read_sql("""
            SELECT codigo, item, cantidad, minimo 
            FROM inventario 
            WHERE cantidad <= minimo AND activo=1
        """, conn)
    
    if not critico.empty:
        with st.expander("🚨 ALERTAS DE STOCK CRÍTICO", expanded=True):
            st.dataframe(critico, use_container_width=True)
            st.warning(f"Hay {len(critico)} items por debajo del mínimo")
    
    # Formulario de nuevo item
    with st.expander("➕ Registrar Nuevo Item"):
        with st.form("nuevo_item", clear_on_submit=True):
            c1, c2, c3 = st.columns(3)
            cod = c1.text_input("Código SKU", placeholder="EJ: FIL-ACE-001")
            nom = c2.text_input("Nombre Repuesto", placeholder="Filtro de Aceite")
            cat = c3.number_input("Cantidad Inicial", min_value=0, value=0)
            
            c4, c5, c6 = st.columns(3)
            pc = c4.number_input("Costo $", min_value=0.0, value=0.0, step=0.01)
            pv = c5.number_input("Venta $", min_value=0.0, value=0.0, step=0.01)
            min_s = c6.number_input("Mínimo Alerta", min_value=1, value=5)
            
            ubic = st.text_input("Ubicación en Almacén", placeholder="Estante A-12")
            
            if st.form_submit_button("💾 Guardar Item", use_container_width=True):
                try:
                    with db.transaction() as cur:
                        cur.execute("""
                            INSERT INTO inventario 
                            (codigo, item, cantidad, minimo, precio_costo, precio_venta, ubicacion) 
                            VALUES (?,?,?,?,?,?,?)
                        """, (cod.upper(), nom, cat, min_s, pc, pv, ubic))
                    st.success(f"✅ Item {cod} registrado correctamente")
                except sqlite3.IntegrityError:
                    st.error("❌ El código SKU ya existe")

    # Tabla de inventario con filtros
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
    st.markdown('<h1 class="main-header">📝 Registro de Entrada</h1>', unsafe_allow_html=True)
    
    # Verificar si hay stock crítico antes de crear orden
    with db.get_connection() as conn:
        stock_crit = pd.read_sql("""
            SELECT item, cantidad FROM inventario 
            WHERE cantidad <= minimo AND activo=1 LIMIT 3
        """, conn)
    
    if not stock_crit.empty:
        with st.warning("⚠️ Hay items con stock crítico que podrían afectar esta reparación"):
            for _, row in stock_crit.iterrows():
                st.write(f"- {row['item']}: {row['cantidad']} unidades")
    
    with st.form("orden_form", clear_on_submit=True):
        st.subheader("Datos del Cliente")
        col1, col2 = st.columns(2)
        cliente = col1.text_input("Nombre Completo*", placeholder="Juan Pérez")
        dni = col2.text_input("DNI", placeholder="12345678")
        tel = col1.text_input("Teléfono*", placeholder="11-1234-5678")
        email = col2.text_input("Email", placeholder="cliente@email.com")
        
        st.subheader("Datos del Vehículo")
        col3, col4, col5 = st.columns(3)
        patente = col3.text_input("Patente*", placeholder="ABC123 o AB123CD").upper()
        marca = col4.text_input("Marca*", placeholder="Toyota")
        modelo = col5.text_input("Modelo*", placeholder="Corolla")
        year = col3.number_input("Año", min_value=1980, max_value=datetime.now().year, value=2020)
        
        st.subheader("Detalles de la Reparación")
        falla = st.text_area("Descripción del Problema*", placeholder="Describe los síntomas...", height=100)
        prioridad = st.selectbox("Prioridad", ["Normal", "Urgente", "Express"])
        
        col6, col7 = st.columns(2)
        fecha_est = col6.date_input("Fecha Estimada Entrega", 
                                   value=datetime.now() + timedelta(days=3))
        
        if st.form_submit_button("🚀 Generar Orden de Trabajo", use_container_width=True):
            errores = []
            if not cliente or not tel:
                errores.append("Nombre y teléfono son obligatorios")
            if not validar_patente(patente):
                errores.append("Formato de patente inválido (ABC123 o AB123CD)")
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
                        # Insertar cliente
                        cur.execute("""
                            INSERT INTO clientes (nombre, dni, telefono, email) 
                            VALUES (?,?,?,?)
                        """, (cliente, dni, tel, email))
                        c_id = cur.lastrowid
                        
                        # Insertar vehículo
                        cur.execute("""
                            INSERT INTO vehiculos (cliente_id, patente, marca, modelo, year) 
                            VALUES (?,?,?,?,?)
                        """, (c_id, patente, marca, modelo, year))
                        v_id = cur.lastrowid
                        
                        # Insertar orden
                        cur.execute("""
                            INSERT INTO reparaciones 
                            (numero_orden, cliente_id, vehiculo_id, falla, estado, 
                             prioridad, fecha_estimada) 
                            VALUES (?,?,?,?,?,?,?)
                        """, (num_orden, c_id, v_id, falla, 'Recibido', 
                              prioridad, fecha_estimada.isoformat()))
                    
                    st.success(f"✅ Orden **{num_orden}** creada correctamente")
                    st.balloons()
                    
                    # Mostrar resumen
                    with st.expander("📋 Ver Resumen de la Orden", expanded=True):
                        st.json({
                            "Orden": num_orden,
                            "Cliente": cliente,
                            "Vehículo": f"{marca} {modelo} ({patente})",
                            "Prioridad": prioridad,
                            "Entrega Estimada": fecha_estimada.strftime("%d/%m/%Y")
                        })
                        
                except sqlite3.IntegrityError as e:
                    st.error(f"❌ Error: Posiblemente la patente ya existe en el sistema")

def show_cambio_password():
    """Forzar cambio de contraseña en primer login"""
    st.markdown('<h1 class="main-header">🔐 Cambio de Contraseña Requerido</h1>', unsafe_allow_html=True)
    st.warning("Debe cambiar su contraseña temporal antes de continuar")
    
    with st.form("cambio_pass"):
        new_pass = st.text_input("Nueva Contraseña", type="password")
        confirm_pass = st.text_input("Confirmar Contraseña", type="password")
        
        if st.form_submit_button("Actualizar"):
            if new_pass != confirm_pass:
                st.error("Las contraseñas no coinciden")
            elif len(new_pass) < 8:
                st.error("Mínimo 8 caracteres")
            else:
                success, msg = change_password(st.session_state.user['id'], new_pass)
                if success:
                    st.session_state.user['force_password_change'] = 0
                    st.success(msg)
                    time.sleep(1)
                    st.rerun()
                else:
                    st.error(msg)

# =============================================================================
# NAVEGACIÓN PRINCIPAL
# =============================================================================

def main():
    # Inicialización de estado
    if 'auth' not in st.session_state:
        st.session_state.auth = False
        st.session_state.user = None

    if not st.session_state.auth:
        # Login Screen
        col1, col2, col3 = st.columns([1, 2, 1])
        with col2:
            st.markdown('<div class="login-container">', unsafe_allow_html=True)
            st.image("https://cdn-icons-png.flaticon.com/512/3062/3062539.png", width=80)
            st.title("SAT Pro Enterprise")
            st.caption("Sistema de Gestión de Taller")
            
            with st.form("login_form"):
                user = st.text_input("Usuario", placeholder="admin")
                pw = st.text_input("Contraseña", type="password", placeholder="••••••••")
                
                if st.form_submit_button("Iniciar Sesión", use_container_width=True):
                    res = check_password(user, pw)
                    if res:
                        st.session_state.auth = True
                        st.session_state.user = res
                        st.rerun()
                    else:
                        st.error("Credenciales inválidas o cuenta bloqueada")
            
            st.markdown('</div>', unsafe_allow_html=True)
    
    else:
        # Verificar cambio de contraseña obligatorio
        if st.session_state.user.get('force_password_change'):
            show_cambio_password()
            return
        
        # Sidebar de navegación
        with st.sidebar:
            st.markdown("### 🔧 SAT Pro")
            st.write(f"**{st.session_state.user['nombre']}**")
            st.caption(f"Rol: {st.session_state.user['rol'].title()}")
            st.divider()
            
            menu_items = {
                "Dashboard": "📊",
                "Nueva Orden": "📝",
                "Taller": "🔧",
                "Inventario": "📦",
                "Clientes": "👥",
                "Reportes": "📈",
                "Configuración": "⚙️"
            }
            
            menu = st.radio(
                "Navegación",
                list(menu_items.keys()),
                format_func=lambda x: f"{menu_items[x]} {x}"
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
        elif menu == "Taller":
            st.info("🔧 Módulo de Gestión de Órdenes en desarrollo")
        elif menu == "Clientes":
            st.info("👥 Módulo de Clientes en desarrollo")
        elif menu == "Reportes":
            st.info("📈 Módulo de Reportes en desarrollo")
        elif menu == "Configuración":
            st.info("⚙️ Módulo de Configuración en desarrollo")

if __name__ == "__main__":
    main()
