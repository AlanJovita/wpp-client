"""Configuração do wpp-client — tudo por variável de ambiente (python-dotenv).

Nenhum segredo no código: a apikey da Evolution, a credencial do MariaDB e o
WEBHOOK_SECRET vêm do ambiente e nunca são expostos em template ou resposta JSON.
"""
import os

from dotenv import load_dotenv

load_dotenv()


def _texto(nome: str, padrao: str = "") -> str:
    return (os.getenv(nome) or padrao).strip()


def _inteiro(nome: str, padrao: int) -> int:
    try:
        return int(_texto(nome) or padrao)
    except ValueError:
        return padrao


def _decimal(nome: str, padrao: float) -> float:
    try:
        return float(_texto(nome) or padrao)
    except ValueError:
        return padrao


def _booleano(nome: str, padrao: bool) -> bool:
    valor = _texto(nome)
    if not valor:
        return padrao
    return valor.lower() in ("1", "true", "sim", "s", "on")


class Config:
    # ---------------------------------------------------------- Evolution API
    EVOLUTION_URL = _texto("EVOLUTION_URL").rstrip("/")
    EVOLUTION_API_KEY = _texto("EVOLUTION_API_KEY")

    # ------------------------------------------------------------- api-master
    MASTER_API_URL = _texto("MASTER_API_URL", "https://api.premiosistemas.com.br/v1").rstrip("/")

    # ---------------------------------------------------------------- MariaDB
    DB_HOST = _texto("DB_HOST", "mariadb")
    DB_PORT = _inteiro("DB_PORT", 3306)
    DB_USER = _texto("DB_USER")
    DB_PASSWORD = _texto("DB_PASSWORD")
    DB_NAME = _texto("DB_NAME", "wpp_premio")
    DB_POOL_SIZE = _inteiro("DB_POOL_SIZE", 3)

    # ------------------------------------------------------------------ painel
    APP_SECRET_KEY = _texto("APP_SECRET_KEY", "troque-esta-chave")
    PANEL_BASE_URL = _texto("PANEL_BASE_URL", "http://localhost:2002").rstrip("/")
    WEBHOOK_SECRET = _texto("WEBHOOK_SECRET")

    # ------------------------------------------------------ digitação simulada
    SIMULAR_DIGITACAO = _booleano("SIMULAR_DIGITACAO", True)
    DIGITACAO_CHARS_POR_SEGUNDO = _decimal("DIGITACAO_CHARS_POR_SEGUNDO", 12.0)
    DIGITACAO_MIN_S = _decimal("DIGITACAO_MIN_S", 1.5)
    DIGITACAO_MAX_S = _decimal("DIGITACAO_MAX_S", 25.0)
    DIGITACAO_EXTRA_MIN_S = _decimal("DIGITACAO_EXTRA_MIN_S", 3.0)
    DIGITACAO_EXTRA_MAX_S = _decimal("DIGITACAO_EXTRA_MAX_S", 12.0)
    # A presença "composing" expira no WhatsApp; é reenviada neste passo
    PRESENCA_PASSO_S = _decimal("PRESENCA_PASSO_S", 8.0)

    # --------------------------------------------------- saudação / ausência
    SAUDACAO_JANELA_HORAS = _inteiro("SAUDACAO_JANELA_HORAS", 12)
    SAUDACAO_MAX_NUMEROS = _inteiro("SAUDACAO_MAX_NUMEROS", 20000)
    ATENDIMENTO_TZ = _texto("ATENDIMENTO_TZ", "America/Sao_Paulo")

    # ----------------------------------------------------------- fila de envio
    FILA_WORKERS = _inteiro("FILA_WORKERS", 12)
    FILA_MAX_PENDENTES = _inteiro("FILA_MAX_PENDENTES", 500)
    FILA_RESULTADO_TTL_S = _inteiro("FILA_RESULTADO_TTL_S", 900)
    RATE_LIMIT_POR_SEGUNDO = _decimal("RATE_LIMIT_POR_SEGUNDO", 1.0)

    # --------------------------------------------------------------- monitor
    MONITOR_ATIVO = _booleano("MONITOR_ATIVO", True)
    MONITOR_INTERVALO_S = _inteiro("MONITOR_INTERVALO_S", 300)

    # ---------------------------------------------------------------- limites
    MAX_IMAGEM_MB = _inteiro("MAX_IMAGEM_MB", 5)
    LOGIN_TENTATIVAS_MAX = _inteiro("LOGIN_TENTATIVAS_MAX", 10)

    FLASK_DEBUG = _booleano("FLASK_DEBUG", False)

    @property
    def max_imagem_bytes(self) -> int:
        return self.MAX_IMAGEM_MB * 1024 * 1024


config = Config()
