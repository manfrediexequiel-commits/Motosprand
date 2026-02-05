import streamlit as st
import pandas as pd
import sqlite3
from fpdf import FPDF
from datetime import datetime

# --- 1. CONFIGURACIÓN DE BASE DE DATOS ---
def conectar_db():
    conn = sqlite3.connect('gestion_taller.db', check_same_thread=False)
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn

conn = conectar_db()
c = conn.cursor()

def crear_tablas():
    c.execute('''CREATE TABLE IF NOT EXISTS inventario 
                 (id INTEGER PRIMARY KEY, item TEXT, cantidad INTEGER, minimo INTEGER, precio_venta REAL)''')
    c.execute('''CREATE TABLE IF NOT EXISTS reparaciones 
                 (id INTEGER PRIMARY KEY, cliente TEXT, equipo TEXT, falla TEXT, estado TEXT, mano_obra REAL)''')
    c.execute('''CREATE TABLE IF NOT EXISTS historial_consumo 
                 (id INTEGER PRIMARY KEY, id_reparacion INTEGER, repuesto TEXT, precio_pagado REAL, fecha TEXT,
                  FOREIGN KEY(id_reparacion) REFERENCES reparaciones(id) ON DELETE CASCADE)''')
    conn.commit()

crear_tablas()

# --- 2. FUNCIONES DE PDF ---
def generar_pdf(cliente, equipo, lista_repuestos, precio_total_rep, mano_obra):
    total_general = precio_total_rep + mano_obra
    pdf = FPDF()
    pdf.add_page()
    def clean(text): return str(text).encode('latin-1', 'ignore').decode('latin-1')

    pdf.set_font("Arial", 'B', 16)
    pdf.cell(200, 10, clean("TICKET DE SERVICIO TÉCNICO"), ln=True, align='C')
    pdf.ln(10)
    pdf.set_font("Arial", size=12)
    pdf.cell(200, 10, f"Cliente: {clean(cliente)}", ln=True)
    pdf.cell(200, 10, f"Vehículo/Equipo: {clean(equipo)}", ln=True)
    pdf.ln(5)
    
    pdf.set_fill_color(230, 230, 230)
    pdf.cell(100, 10, "Repuesto / Servicio", border=1, fill=True)
    pdf.cell(50, 10, "Precio", border=1, ln=True, fill=True)
    
    for nombre, precio in lista_repuestos:
        pdf.cell(100, 10, clean(nombre), border=1)
        pdf.cell(50, 10, f"${precio:.2f}", border=1, ln=True)
    
    pdf.cell(100, 10, "Mano de Obra", border=1)
    pdf.cell(50, 10, f"${mano_obra:.2f}", border=1, ln=True)
    pdf.set_font("Arial", 'B', 12)
    pdf.cell(100, 10, "TOTAL FINAL", border=1)
    pdf.cell(50, 10, f"${total_general:.2f}", border=1, ln=True)
    return pdf.output(dest='S').encode('latin-1')

# --- 3. SEGURIDAD ---
st.set_page_config(page_title="SAT Pro", layout="wide")
USUARIOS = {"Rodri": "1590", "Lean": "3588"}

if "autenticado" not in st.session_state:
    st.session_state["autenticado"] = False

if not st.session_state["autenticado"]:
    st.title("🔐 Acceso")
    u, p = st.text_input("Usuario"), st.text_input("Clave", type="password")
    if st.button("Entrar"):
        if u in USUARIOS and USUARIOS[u] == p:
            st.session_state["autenticado"], st.session_state["user"] = True, u
            st.rerun()
        else: st.error("Acceso denegado")
    st.stop()

# --- 4. MENÚ ---
st.sidebar.title(f"🛠️ {st.session_state['user']}")
menu = st.sidebar.radio("Navegación", ["Panel", "Inventario", "Ingresos", "Cierre de Orden", "Buscador de Historial"])

if st.sidebar.button("Cerrar Sesión"):
    st.session_state["autenticado"] = False
    st.rerun()

# --- MÓDULOS ---
if menu == "Panel":
    st.header("📊 Resumen")
    c1, c2 = st.columns(2)
    # [span_0](start_span)Ejemplo de datos de socios si necesitas integrar con tu lista 'Miembros stvp'[span_0](end_span)
    st.info("Sistema listo para operar.")

elif menu == "Inventario":
    st.header("📦 Stock")
    with st.form("inv"):
        n, s, m, p = st.text_input("Nombre"), st.number_input("Stock", 0), st.number_input("Mínimo", 1), st.number_input("Precio", 0.0)
        if st.form_submit_button("Guardar"):
            c.execute("INSERT INTO inventario (item, cantidad, minimo, precio_venta) VALUES (?,?,?,?)", (n, s, m, p))
            conn.commit()
            st.success("Guardado")
    st.dataframe(pd.read_sql_query("SELECT * FROM inventario", conn), use_container_width=True)

elif menu == "Ingresos":
    st.header("📝 Nueva Orden")
    with st.form("rep"):
        cl, eq, fa, mo = st.text_input("Cliente"), st.text_input("Vehículo/Patente"), st.text_area("Falla"), st.number_input("Mano de Obra ($)", 0.0)
        if st.form_submit_button("Ingresar"):
            c.execute("INSERT INTO reparaciones (cliente, equipo, falla, estado, mano_obra) VALUES (?,?,?,?,?)", (cl, eq, fa, "Recibido", mo))
            conn.commit()
            st.success("Ingresado")

elif menu == "Cierre de Orden":
    st.header("🔄 Finalizar Trabajo")
    activas = pd.read_sql_query("SELECT * FROM reparaciones WHERE estado != 'Listo / Finalizado'", conn)
    if not activas.empty:
        opcs = {f"ID {r['id']} - {r['cliente']}": r for _, r in activas.iterrows()}
        sel = st.selectbox("Orden:", opcs.keys())
        dato = opcs[sel]
        
        reps_inv = pd.read_sql_query("SELECT item, precio_venta FROM inventario WHERE cantidad > 0", conn)
        usados = st.multiselect("Repuestos:", reps_inv['item'])
        
        if st.button("Finalizar y PDF"):
            lista_pdf, suma_r = [], 0
            for r_nom in usados:
                pr = reps_inv[reps_inv['item'] == r_nom]['precio_venta'].values[0]
                suma_r += pr
                lista_pdf.append((r_nom, pr))
                c.execute("UPDATE inventario SET cantidad = cantidad - 1 WHERE item = ?", (r_nom,))
                c.execute("INSERT INTO historial_consumo (id_reparacion, repuesto, precio_pagado, fecha) VALUES (?,?,?,?)", 
                          (dato['id'], r_nom, pr, datetime.now().strftime("%Y-%m-%d")))
            c.execute("UPDATE reparaciones SET estado = 'Listo / Finalizado' WHERE id = ?", (dato['id'],))
            conn.commit()
            pdf = generar_pdf(dato['cliente'], dato['equipo'], lista_pdf, suma_r, dato['mano_obra'])
            st.download_button("📥 Descargar Ticket", pdf, file_name=f"orden_{dato['id']}.pdf")
            st.rerun()

elif menu == "Buscador de Historial":
    st.header("🔍 Historial por Vehículo / Cliente")
    busqueda = st.text_input("Ingrese patente o nombre del cliente")
    
    if busqueda:
        # Buscamos todas las reparaciones que coincidan
        reps_encontradas = pd.read_sql_query(f'''SELECT * FROM reparaciones 
                                                WHERE cliente LIKE "%{busqueda}%" 
                                                OR equipo LIKE "%{busqueda}%"''', conn)
        
        if not reps_encontradas.empty:
            for _, r in reps_encontradas.iterrows():
                with st.expander(f"📅 {r['id']} | {r['equipo']} - {r['cliente']} ({r['estado']})"):
                    st.write(f"**Falla reportada:** {r['falla']}")
                    st.write(f"**Mano de obra:** ${r['mano_obra']}")
                    
                    # Buscamos los repuestos de esa reparación específica
                    det = pd.read_sql_query(f"SELECT repuesto, precio_pagado, fecha FROM historial_consumo WHERE id_reparacion = {r['id']}", conn)
                    if not det.empty:
                        st.table(det)
                    else:
                        st.info("No se utilizaron repuestos en esta intervención.")
        else:
            st.warning("No se encontraron registros.")
