import streamlit as st
import pandas as pd
import sqlite3
from fpdf import FPDF
from datetime import datetime

# --- 1. CONFIGURACIÓN DE BASE DE DATOS ---
conn = sqlite3.connect('gestion_taller.db', check_same_thread=False)
c = conn.cursor()

def crear_tablas():
    c.execute('''CREATE TABLE IF NOT EXISTS inventario 
                 (id INTEGER PRIMARY KEY, item TEXT, cantidad INTEGER, minimo INTEGER, precio_venta REAL)''')
    c.execute('''CREATE TABLE IF NOT EXISTS reparaciones 
                 (id INTEGER PRIMARY KEY, cliente TEXT, equipo TEXT, falla TEXT, estado TEXT, mano_obra REAL)''')
    c.execute('''CREATE TABLE IF NOT EXISTS historial_consumo 
                 (id INTEGER PRIMARY KEY, id_reparacion INTEGER, repuesto TEXT, precio_pagado REAL, fecha TEXT)''')
    conn.commit()

crear_tablas()

# --- 2. FUNCIONES AUXILIARES ---
def generar_pdf(cliente, equipo, repuesto, precio_rep, mano_obra):
    total = precio_rep + mano_obra
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Arial", 'B', 16)
    pdf.cell(200, 10, "TICKET DE SERVICIO TÉCNICO", ln=True, align='C')
    pdf.ln(10)
    pdf.set_font("Arial", size=12)
    pdf.cell(200, 10, f"Cliente: {cliente}", ln=True)
    pdf.cell(200, 10, f"Equipo: {equipo}", ln=True)
    pdf.ln(5)
    pdf.cell(100, 10, "Concepto", border=1)
    pdf.cell(50, 10, "Precio", border=1, ln=True)
    pdf.cell(100, 10, f"Repuesto: {repuesto}", border=1)
    pdf.cell(50, 10, f"${precio_rep:.2f}", border=1, ln=True)
    pdf.cell(100, 10, "Mano de Obra", border=1)
    pdf.cell(50, 10, f"${mano_obra:.2f}", border=1, ln=True)
    pdf.set_font("Arial", 'B', 12)
    pdf.cell(100, 10, "TOTAL", border=1)
    pdf.cell(50, 10, f"${total:.2f}", border=1, ln=True)
    return pdf.output(dest='S').encode('latin-1')

# --- 3. SISTEMA DE SEGURIDAD (LOGIN) ---
st.set_page_config(page_title="Gestión de Taller Pro", layout="wide")

if "autenticado" not in st.session_state:
    st.session_state["autenticado"] = False

if not st.session_state["autenticado"]:
    st.title("🔐 Acceso al Sistema")
    user = st.text_input("Usuario")
    pw = st.text_input("Contraseña", type="password")
    if st.button("Ingresar"):
        if user == "admin" and pw == "12345": # Cambiar por seguridad
            st.session_state["autenticado"] = True
            st.rerun()
        else:
            st.error("Credenciales incorrectas")
    st.stop()

# --- 4. INTERFAZ PRINCIPAL ---
st.sidebar.title("🛠️ Menú SAT")
opcion = st.sidebar.radio("Navegación", ["Tablero de Control", "Inventario", "Nueva Reparación", "Gestión de Estados", "Historial"])

if st.sidebar.button("Cerrar Sesión"):
    st.session_state["autenticado"] = False
    st.rerun()

# --- MÓDULO: TABLERO DE CONTROL ---
if opcion == "Tablero de Control":
    st.header("📊 Resumen de Negocio")
    col1, col2, col3 = st.columns(3)
    
    pendientes = pd.read_sql_query("SELECT COUNT(*) as total FROM reparaciones WHERE estado != 'Listo / Finalizado'", conn).iloc[0]['total']
    ventas = pd.read_sql_query("SELECT SUM(precio_pagado) as total FROM historial_consumo", conn).iloc[0]['total'] or 0
    mano_obra_total = pd.read_sql_query("SELECT SUM(mano_obra) as total FROM reparaciones WHERE estado = 'Listo / Finalizado'", conn).iloc[0]['total'] or 0

    col1.metric("Reparaciones Pendientes", pendientes)
    col2.metric("Ingresos por Repuestos", f"${ventas:.2f}")
    col3.metric("Ingresos Mano de Obra", f"${mano_obra_total:.2f}")

    st.subheader("⚠️ Alertas de Stock Crítico")
    alertas = pd.read_sql_query("SELECT item, cantidad FROM inventario WHERE cantidad <= minimo", conn)
    if not alertas.empty:
        st.warning("Los siguientes repuestos necesitan reposición urgente:")
        st.table(alertas)
    else:
        st.success("Inventario saludable.")

# --- MÓDULO: INVENTARIO ---
elif opcion == "Inventario":
    st.header("📦 Gestión de Inventario")
    with st.expander("➕ Añadir Nuevo Repuesto"):
        with st.form("inv_form"):
            nombre = st.text_input("Nombre de la pieza")
            stock = st.number_input("Stock Inicial", min_value=0)
            limite = st.number_input("Stock Mínimo", min_value=1)
            precio = st.number_input("Precio de Venta ($)", min_value=0.0)
            if st.form_submit_button("Registrar"):
                c.execute("INSERT INTO inventario (item, cantidad, minimo, precio_venta) VALUES (?,?,?,?)", (nombre, stock, limite, precio))
                conn.commit()
                st.success("Registrado correctamente")
    
    df_inv = pd.read_sql_query("SELECT * FROM inventario", conn)
    st.dataframe(df_inv, use_container_width=True)

# --- MÓDULO: NUEVA REPARACIÓN ---
elif opcion == "Nueva Reparación":
    st.header("📝 Ingreso de Equipo")
    with st.form("rep_form"):
        cl = st.text_input("Nombre del Cliente")
        eq = st.text_input("Equipo / Modelo")
        fa = st.text_area("Falla Reportada")
        mo = st.number_input("Mano de Obra Estimada ($)", min_value=0.0)
        if st.form_submit_button("Crear Orden"):
            c.execute("INSERT INTO reparaciones (cliente, equipo, falla, estado, mano_obra) VALUES (?,?,?,?,?)", (cl, eq, fa, "Recibido", mo))
            conn.commit()
            st.success("Orden de servicio creada.")

# --- MÓDULO: GESTIÓN DE ESTADOS (DESCUENTO AUTOMÁTICO Y PDF) ---
elif opcion == "Gestión de Estados":
    st.header("🔄 Actualización y Facturación")
    reps = pd.read_sql_query("SELECT * FROM reparaciones WHERE estado != 'Listo / Finalizado'", conn)
    
    if not reps.empty:
        opciones_rep = {f"{row['id']} - {row['equipo']}": row for _, row in reps.iterrows()}
        seleccion = st.selectbox("Seleccione Reparación", opciones_rep.keys())
        datos_rep = opciones_rep[seleccion]
        
        nuevo_est = st.selectbox("Cambiar Estado a:", ["En Proceso", "Listo / Finalizado"])
        
        items_inv = pd.read_sql_query("SELECT item, precio_venta FROM inventario WHERE cantidad > 0", conn)
        repuesto_usado = st.selectbox("Repuesto utilizado", ["Ninguno"] + list(items_inv['item']))
        
        if st.button("Confirmar y Generar Factura"):
            precio_p = 0
            if nuevo_est == "Listo / Finalizado":
                if repuesto_usado != "Ninguno":
                    # Descontar stock
                    c.execute("UPDATE inventario SET cantidad = cantidad - 1 WHERE item = ?", (repuesto_usado,))
                    # Obtener precio
                    precio_p = items_inv[items_inv['item'] == repuesto_usado]['precio_venta'].values[0]
                    # Guardar historial
                    c.execute("INSERT INTO historial_consumo (id_reparacion, repuesto, precio_pagado, fecha) VALUES (?,?,?,?)", 
                              (datos_rep['id'], repuesto_usado, precio_p, datetime.now().strftime("%Y-%m-%d")))
                
                c.execute("UPDATE reparaciones SET estado = ? WHERE id = ?", (nuevo_est, datos_rep['id']))
                conn.commit()
                
                # Generar PDF
                pdf_bytes = generar_pdf(datos_rep['cliente'], datos_rep['equipo'], repuesto_usado, precio_p, datos_rep['mano_obra'])
                st.download_button("📥 Descargar Factura PDF", pdf_bytes, file_name=f"factura_{datos_rep['id']}.pdf")
                st.success("Proceso completado.")
                st.rerun()
    else:
        st.info("No hay reparaciones activas.")

# --- MÓDULO: HISTORIAL ---
elif opcion == "Historial":
    st.header("📜 Historial de Movimientos")
    hist = pd.read_sql_query('''SELECT h.fecha, r.cliente, r.equipo, h.repuesto, h.precio_pagado 
                                FROM historial_consumo h JOIN reparaciones r ON h.id_reparacion = r.id''', conn)
    st.dataframe(hist, use_container_width=True)
