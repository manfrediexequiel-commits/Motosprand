# 🏍️ Motos Prand - Sistema de Gestión de Taller

Sistema completo de gestión para taller mecánico de motos, con control de inventario, órdenes de trabajo, reportes de ganancias y gestión de usuarios.

## 🚀 Demo en Vivo

**Railway:** [https://motosprand.com](https://motosprand.com) *(o tu URL de Railway)*

## ✨ Características

- 🔐 **Sistema de Login** con roles (Administrador/Técnico)
- 📦 **Gestión de Inventario** con alertas de stock crítico
- 📝 **Órdenes de Trabajo** con numeración automática (MP-XXXXXX-XX)
- 💰 **Reportes de Ganancias** (solo administradores)
- 👥 **Gestión de Usuarios** (crear/editar usuarios)
- 🗄️ **Base de datos PostgreSQL** (persistente en Railway)
- 📱 **Interfaz responsive** con Streamlit

## 🔑 Usuarios Predefinidos

| Usuario | Contraseña | Rol | Acceso |
|---------|-----------|-----|--------|
| **rodri** | **** | 🟠 Admin | Total |
| **lean** |****  | 🟠 Admin | Total |
| **tecnico** | *** | 🔵 Técnico | Limitado |
| **tecnico1** | ****| 🔵 Técnico | Limitado |

> **Nota:** Los técnicos no pueden ver ganancias ni crear usuarios.

## 🛠️ Tecnologías

- **Frontend:** Streamlit
- **Backend:** Python 3.9
- **Base de datos:** PostgreSQL (Railway) / SQLite (local)
- **Seguridad:** bcrypt para hashing de contraseñas
- **Deploy:** Railway.app

## 📋 Instalación Local

### Requisitos
- Python 3.9+
- Git

### Pasos

1. **Clonar repositorio**
```bash
git clone https://github.com/TU_USUARIO/motos-prand.git
cd motos-prand
