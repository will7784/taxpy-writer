FROM python:3.11-slim

WORKDIR /app

# Dependencias del sistema para playwright + compilación Python
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libglib2.0-0 \
    libnss3 \
    libnspr4 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libdbus-1-3 \
    libxkbcommon0 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libpango-1.0-0 \
    libcairo2 \
    libasound2 \
    && rm -rf /var/lib/apt/lists/*

# Copiar requirements primero para aprovechar cache de Docker
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Instalar browsers de Playwright (requerido por notebooklm-py)
RUN python -m playwright install chromium
RUN python -m playwright install-deps chromium

# Copiar todo el código
COPY . .

# Puerto para healthcheck / frontend
EXPOSE 8000

# Comando de inicio
CMD ["bash", "start.sh"]
