"""Monitor de conexão — mantém a instância do cliente 24h online.

Thread daemon única que, a cada MONITOR_INTERVALO_S, para cada instância
registrada:

  1. consulta o estado real na Evolution e grava (autocura quando o
     connection.update se perdeu);
  2. se o webhook registrado lá divergir do PANEL_BASE_URL, reaponta — é o que
     salva as instâncias depois de uma troca de domínio;
  3. se caiu, tenta reabrir a conexão. Só funciona enquanto a sessão Baileys
     continua válida no servidor; se o cliente removeu o aparelho ou a sessão
     expirou, o painel passa a mostrar "desconectada" e ele precisa ler um QR
     novo — não há como evitar isso sem o aparelho dele.

Custo: uma requisição HTTP por instância a cada 5 minutos, numa thread que
dorme o resto do tempo.
"""
import logging
import threading
import time

from api import evolution_client
from config import config
from models import instancia as instancia_model
from services import instance_service

log = logging.getLogger(__name__)

_thread: threading.Thread | None = None
_parar = threading.Event()


def _webhook_esperado(nome: str) -> str:
    return f"{config.PANEL_BASE_URL}/webhook/{nome}"


def _conferir_webhook(nome: str) -> None:
    try:
        atual = evolution_client.consultar_webhook(nome)
    except evolution_client.EvolutionError as exc:
        log.debug("Webhook de %s não consultável: %s", nome, exc)
        return

    bruto = atual.get("webhook") if isinstance(atual, dict) else None
    url = (bruto or atual or {}).get("url") or ""
    if url != _webhook_esperado(nome):
        log.warning(
            "Webhook de %s divergente (%r) — reapontando para este app", nome, url
        )
        evolution_client.reconfigurar_webhook(nome)


def _verificar(registro: dict) -> None:
    nome = registro["nome_instancia"]
    status = instance_service.sincronizar_status(registro)

    if status == "conectada":
        _conferir_webhook(nome)
        return

    if status == "desconectada":
        log.info("Instância %s caída — tentando reabrir a conexão", nome)
        try:
            instance_service.conectar(registro)
        except evolution_client.EvolutionError as exc:
            log.warning("Reconexão de %s falhou: %s", nome, exc)


def _ciclo() -> None:
    try:
        registros = instancia_model.todas()
    except Exception as exc:
        log.error("Monitor não conseguiu listar instâncias: %s", exc)
        return

    for registro in registros:
        if _parar.is_set():
            return
        try:
            _verificar(registro)
        except Exception as exc:
            log.error(
                "Monitor falhou em %s: %s", registro.get("nome_instancia"), exc
            )


def _laco() -> None:
    # espera um intervalo antes do primeiro ciclo: na subida a Evolution pode
    # ainda estar reconectando as sessões e responderia "close" para todas
    while not _parar.wait(config.MONITOR_INTERVALO_S):
        inicio = time.monotonic()
        _ciclo()
        log.debug("Ciclo do monitor em %.1fs", time.monotonic() - inicio)


def iniciar() -> None:
    """Sobe a thread do monitor (idempotente)."""
    global _thread
    if not config.MONITOR_ATIVO:
        log.info("Monitor de conexão desligado (MONITOR_ATIVO=0)")
        return
    if _thread is not None and _thread.is_alive():
        return
    _parar.clear()
    _thread = threading.Thread(target=_laco, name="monitor", daemon=True)
    _thread.start()
    log.info("Monitor de conexão ativo (cada %ds)", config.MONITOR_INTERVALO_S)


def encerrar() -> None:
    _parar.set()
