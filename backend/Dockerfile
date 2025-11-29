FROM python:3.11-slim

# Evitar pyc y forzar logs sin buffer
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Actualizar pip
RUN pip install --upgrade pip

# Instalar dependencias
COPY requirements.txt .
RUN pip install -r requirements.txt

# Copiar el código
COPY . .

# Comando de arranque (ajusta 'app.main:app' según tu estructura)
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
