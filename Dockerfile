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

# Healthcheck próprio, em stdlib: a imagem slim NÃO tem curl nem wget, então um
# healthcheck baseado neles (o padrão do Coolify) falharia por binário ausente e
# marcaria o container como unhealthy mesmo com o app respondendo.
# start-period cobre a aplicação do schema na subida.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request as u, sys; sys.exit(0 if u.urlopen('http://127.0.0.1:2002/health', timeout=4).status == 200 else 1)"

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
