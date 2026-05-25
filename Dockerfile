FROM python:3.11-slim

WORKDIR /app

# Instalar dependencias del sistema si las necesita alguna librería
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Copiar requirements primero para aprovechar cache de Docker
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copiar todo el código
COPY . .

# Puerto para healthcheck / frontend
EXPOSE 8000

# Comando de inicio
CMD ["bash", "start.sh"]
