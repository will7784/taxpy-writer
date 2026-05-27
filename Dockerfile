FROM python:3.11-slim

WORKDIR /app

# Dependencias del sistema: ffmpeg para procesamiento de voz + build tools para psycopg2
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    ffmpeg \
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
