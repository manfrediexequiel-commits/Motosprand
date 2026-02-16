FROM python:3.9-slim

WORKDIR /app

# Instalar dependencias del sistema
RUN apt-get update && apt-get install -y \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Copiar archivos
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Puerto
EXPOSE 8501

# Comando
CMD ["streamlit", "run", "motos_prand.py", "--server.port=8501", "--server.address=0.0.0.0", "--server.headless=true"]
