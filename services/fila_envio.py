"""Fila de envios da API pública.

Com digitação simulada um envio leva de 15 a 40 segundos — prender a conexão do
cliente por isso seria um contrato ruim. A API responde 202 na hora com um
id_envio e o trabalho acontece aqui, num ThreadPoolExecutor.

Threads são a escolha certa para esta carga: elas passam quase todo o tempo
dormindo (time.sleep da digitação) ou esperando rede, sem consumir CPU. Os
resultados ficam num dicionário com TTL e teto de tamanho, então a RAM não
cresce com o volume de envios.
"""
import logging
import threading
import time
import uuid
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor

from config import config

log = logging.getLogger(__name__)

NA_FILA = "na_fila"
ENVIANDO = "enviando"
ENVIADA = "enviada"
ERRO = "erro"

_executor: ThreadPoolExecutor | None = None
_executor_lock = threading.Lock()

_resultados: "OrderedDict[str, dict]" = OrderedDict()
_resultados_lock = threading.Lock()
_pendentes = 0


class FilaCheia(Exception):
    """Fila no limite — o chamador deve responder 429."""


def _obter_executor() -> ThreadPoolExecutor:
    global _executor
    with _executor_lock:
        if _executor is None:
            _executor = ThreadPoolExecutor(
                max_workers=max(1, config.FILA_WORKERS), thread_name_prefix="envio"
            )
        return _executor


def _podar(agora: float) -> None:
    """Remove resultados vencidos e respeita o teto de registros.

    Chamado sempre com _resultados_lock tomado.
    """
    ttl = config.FILA_RESULTADO_TTL_S
    while _resultados:
        chave, registro = next(iter(_resultados.items()))
        if agora - registro["criado_em"] <= ttl:
            break
        _resultados.popitem(last=False)
    # teto rígido: 4x o número de pendentes permitidos
    teto = max(100, config.FILA_MAX_PENDENTES * 4)
    while len(_resultados) > teto:
        _resultados.popitem(last=False)


def _registrar(id_envio: str, **campos) -> None:
    with _resultados_lock:
        registro = _resultados.get(id_envio)
        if registro is None:
            return
        registro.update(campos)


def enfileirar(funcao, *args, **kwargs) -> str:
    """Agenda `funcao(*args, **kwargs)` e devolve o id_envio para consulta."""
    global _pendentes

    agora = time.monotonic()
    with _resultados_lock:
        if _pendentes >= config.FILA_MAX_PENDENTES:
            raise FilaCheia(
                f"{_pendentes} envios pendentes (limite {config.FILA_MAX_PENDENTES})"
            )
        _podar(agora)
        id_envio = uuid.uuid4().hex
        _resultados[id_envio] = {
            "status": NA_FILA,
            "criado_em": agora,
            "id_whatsapp": None,
            "erro": None,
        }
        _pendentes += 1

    def tarefa():
        global _pendentes
        _registrar(id_envio, status=ENVIANDO)
        try:
            resultado = funcao(*args, **kwargs) or {}
            _registrar(
                id_envio, status=ENVIADA, id_whatsapp=resultado.get("id_whatsapp")
            )
        except Exception as exc:
            log.error("Envio %s falhou: %s", id_envio, exc)
            _registrar(id_envio, status=ERRO, erro=str(exc)[:300])
        finally:
            with _resultados_lock:
                _pendentes -= 1

    _obter_executor().submit(tarefa)
    return id_envio


def consultar(id_envio: str) -> dict | None:
    """Situação de um envio, ou None se nunca existiu / já venceu o TTL."""
    with _resultados_lock:
        _podar(time.monotonic())
        registro = _resultados.get(id_envio)
        if registro is None:
            return None
        return {
            "id_envio": id_envio,
            "status": registro["status"],
            "id_whatsapp": registro["id_whatsapp"],
            "erro": registro["erro"],
        }


def pendentes() -> int:
    with _resultados_lock:
        return _pendentes


def encerrar() -> None:
    """Aguarda os envios em andamento (usado no shutdown)."""
    global _executor
    with _executor_lock:
        if _executor is not None:
            _executor.shutdown(wait=True)
            _executor = None
