"""Envio de mensagens — digitação simulada + rate-limit por instância.

Nenhuma mensagem é persistida: o retorno vai para quem chamou (fila da API ou
saudação) e morre ali.
"""
import logging
import re
import threading
import time

from api import evolution_client
from config import config
from services import typing_service

log = logging.getLogger(__name__)

# Rate-limit por instância, em memória: um lock e o instante do último envio.
# Dois clientes diferentes nunca disputam o mesmo lock (chave = instância).
_locks: dict[str, threading.Lock] = {}
_ultimo_envio: dict[str, float] = {}
_registro_lock = threading.Lock()


def _lock_da(instancia: str) -> threading.Lock:
    with _registro_lock:
        lock = _locks.get(instancia)
        if lock is None:
            lock = _locks[instancia] = threading.Lock()
        return lock


def _aguardar_rate_limit(instancia: str) -> None:
    """Garante o intervalo mínimo entre dois envios da MESMA instância.

    O lock é mantido só durante a espera e o carimbo — não durante a chamada à
    Evolution — para não serializar instâncias diferentes nem segurar thread
    enquanto a rede trabalha.
    """
    minimo = 1.0 / max(config.RATE_LIMIT_POR_SEGUNDO, 0.01)
    with _lock_da(instancia):
        anterior = _ultimo_envio.get(instancia)
        agora = time.monotonic()
        if anterior is not None:
            espera = minimo - (agora - anterior)
            if espera > 0:
                time.sleep(espera)
                agora = time.monotonic()
        _ultimo_envio[instancia] = agora


def normalizar_numero(numero: str) -> str | None:
    """Reduz a dígitos e valida o tamanho de um telefone brasileiro com DDI.

    Aceita 55 + DDD + 8/9 dígitos (12 ou 13 dígitos). Sem DDI, assume 55.
    Devolve None se não parecer um número discável.
    """
    digitos = re.sub(r"\D", "", numero or "")
    if not digitos:
        return None
    if len(digitos) in (10, 11):  # DDD + número, sem DDI
        digitos = "55" + digitos
    if len(digitos) not in (12, 13):
        return None
    return digitos


def _id_whatsapp(resposta: dict | None) -> str | None:
    if not isinstance(resposta, dict):
        return None
    chave = resposta.get("key")
    if isinstance(chave, dict):
        return chave.get("id")
    return None


def enviar_texto(instancia: str, numero: str, texto: str, typing: bool = True) -> dict:
    """Envia texto. Devolve {"sucesso", "id_whatsapp"} ou levanta EvolutionError."""
    if typing:
        typing_service.simular(instancia, numero, texto)
    _aguardar_rate_limit(instancia)
    resposta = evolution_client.enviar_texto(instancia, numero, texto)
    return {"sucesso": True, "id_whatsapp": _id_whatsapp(resposta)}


def enviar_imagem(
    instancia: str,
    numero: str,
    imagem: str,
    legenda: str = "",
    nome_arquivo: str = "",
    typing: bool = True,
) -> dict:
    """Envia imagem (URL ou base64) com legenda opcional.

    A digitação é calculada sobre a legenda: imagem sem legenda ganha só o
    tempo mínimo + o adicional randômico, que é o ritmo de quem anexa um arquivo.
    """
    if typing:
        typing_service.simular(instancia, numero, legenda or "")
    _aguardar_rate_limit(instancia)
    resposta = evolution_client.enviar_midia(
        instancia, numero, imagem, tipo="image", legenda=legenda, nome_arquivo=nome_arquivo
    )
    return {"sucesso": True, "id_whatsapp": _id_whatsapp(resposta)}
