FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p logs

EXPOSE 2002

# UM worker, várias threads — obrigatório.
#
# O estado em memória (cache do QR, janela de repetição da saudação, fila de
# envios e o monitor de conexão) é por processo. Com 2+ workers cada um teria a
# sua cópia: o QR geraria num worker e o painel consultaria noutro, a saudação
# sairia duplicada e o monitor rodaria N vezes.
#
# Threads custam pouco aqui porque elas passam o tempo dormindo (digitação
# simulada) ou esperando a Evolution — não disputam CPU.
CMD ["gunicorn", "-w", "1", "--threads", "64", "-k", "gthread", \
     "-b", "0.0.0.0:2002", "--timeout", "120", "--graceful-timeout", "30", \
     "--access-logfile", "-", "app:app"]
