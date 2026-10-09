"""Logging em arquivo rotativo + stdout (Coolify lê o stdout do container).

Nada de `print`. Nenhum log carrega conteúdo de mensagem de cliente — no máximo
o número mascarado, quando for indispensável para diagnosticar.
"""
import logging
import os
from logging.handlers import RotatingFileHandler

FORMATO = "%(asctime)s %(levelname)s %(name)s: %(message)s"
PASTA_LOGS = os.path.join(os.path.dirname(__file__), "logs")


def mascarar_numero(numero: str) -> str:
    """5512999993830 → 5512…3830. Identifica o caso sem registrar o contato."""
    numero = str(numero or "")
    if len(numero) < 8:
        return "…"
    return f"{numero[:4]}…{numero[-4:]}"


def configurar(debug: bool = False) -> None:
    nivel = logging.DEBUG if debug else logging.INFO
    raiz = logging.getLogger()
    if raiz.handlers:  # já configurado (reload do Flask / worker do gunicorn)
        return

    raiz.setLevel(nivel)
    formatador = logging.Formatter(FORMATO)

    console = logging.StreamHandler()
    console.setFormatter(formatador)
    raiz.addHandler(console)

    try:
        os.makedirs(PASTA_LOGS, exist_ok=True)
        arquivo = RotatingFileHandler(
            os.path.join(PASTA_LOGS, "app.log"),
            maxBytes=2 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8",
        )
        arquivo.setFormatter(formatador)
        raiz.addHandler(arquivo)

        erros = RotatingFileHandler(
            os.path.join(PASTA_LOGS, "errors.log"),
            maxBytes=1024 * 1024,
            backupCount=2,
            encoding="utf-8",
        )
        erros.setLevel(logging.ERROR)
        erros.setFormatter(formatador)
        raiz.addHandler(erros)
    except OSError as exc:
        # container com filesystem somente-leitura: stdout basta
        raiz.warning("Logs em arquivo desabilitados: %s", exc)

    # o requests/urllib3 em DEBUG despeja cada header de cada chamada
    logging.getLogger("urllib3").setLevel(logging.WARNING)
