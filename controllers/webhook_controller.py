"""Webhook da Evolution — POST /webhook/<instancia>.

Responde 200 imediatamente, sempre. A Evolution reentrega e desabilita webhook
que demora ou falha, e a saudação leva de 15 a 40 segundos por causa da
digitação simulada: o envio vai para a fila, nunca para dentro do request.

**Nenhuma mensagem é gravada.** O payload é lido, usado para decidir se cabe
saudação e descartado. Deste lado ficam só o número (em memória, na janela de
repetição) e o status da conexão.
"""
import hmac
import logging

from flask import Blueprint, request

from config import config
from models import configuracao as configuracao_model
from models import instancia as instancia_model
from services import (
    fila_envio,
    instance_service,
    messaging_service,
    saudacao_service,
)

log = logging.getLogger(__name__)

webhook = Blueprint("webhook", __name__)

ESTADOS = {"open": "conectada", "connecting": "conectando", "close": "desconectada"}

# JIDs que nunca recebem saudação
SUFIXOS_IGNORADOS = ("@g.us", "@broadcast", "@newsletter", "@lid")


def _token_valido() -> bool:
    enviado = request.headers.get("x-webhook-token", "")
    if not config.WEBHOOK_SECRET:
        log.error("WEBHOOK_SECRET vazio — webhook recusado")
        return False
    return hmac.compare_digest(enviado, config.WEBHOOK_SECRET)


def _normalizar_evento(nome: str) -> str:
    """"MESSAGES_UPSERT" e "messages.upsert" chegam conforme a build."""
    return (nome or "").strip().lower().replace("_", ".")


# ------------------------------------------------------------------ eventos

def _qrcode_atualizado(nome_instancia: str, dados: dict) -> None:
    qr = dados.get("qrcode") if isinstance(dados.get("qrcode"), dict) else dados
    base64 = (qr or {}).get("base64")
    if base64:
        instance_service.guardar_qr(nome_instancia, base64)


def _conexao_atualizada(registro: dict, dados: dict) -> None:
    nome = registro["nome_instancia"]
    status = ESTADOS.get(dados.get("state") or "")
    if not status:
        return

    if status != registro.get("status"):
        instancia_model.atualizar_status(nome, status)
        log.info("Conexão de %s: %s", nome, status)

    if status == "conectada":
        instance_service.limpar_qr(nome)
        numero = dados.get("wuid") or dados.get("owner") or dados.get("number")
        if numero:
            numero = str(numero).split("@")[0]
            if numero != registro.get("numero"):
                instancia_model.atualizar_numero(nome, numero)


def _mensagem_recebida(registro: dict, dados: dict) -> None:
    """Decide a saudação/ausência e joga o envio na fila. Não grava nada."""
    chave = dados.get("key") or {}
    if chave.get("fromMe"):
        return

    jid = str(chave.get("remoteJid") or "")
    if not jid or jid.endswith(SUFIXOS_IGNORADOS):
        return  # grupo, lista de transmissão, canal

    numero = messaging_service.normalizar_numero(jid.split("@")[0])
    if not numero:
        return

    nome = registro["nome_instancia"]
    configuracoes = configuracao_model.por_cliente(registro["id_cliente"])

    # consome a janela de repetição aqui, síncrono: duas mensagens seguidas do
    # mesmo contato não podem gerar duas saudações
    texto = saudacao_service.preparar(nome, numero, configuracoes)
    if not texto:
        return

    try:
        fila_envio.enfileirar(
            messaging_service.enviar_texto, nome, numero, texto, typing=True
        )
    except fila_envio.FilaCheia as exc:
        log.warning("Saudação descartada em %s: %s", nome, exc)


# ------------------------------------------------------------------- rota

@webhook.route("/webhook/<instancia>", methods=["POST"])
def receber(instancia: str):
    if not _token_valido():
        log.warning("Webhook com token inválido para %r", instancia)
        return "", 401

    registro = instancia_model.por_nome(instancia)
    if not registro:
        # instância que não é deste app (ou linha local removida): 200 para a
        # Evolution não marcar o webhook como defeituoso e desabilitá-lo
        log.warning("Webhook de instância desconhecida: %r", instancia)
        return "", 200

    corpo = request.get_json(silent=True) or {}
    evento = _normalizar_evento(corpo.get("event"))
    dados = corpo.get("data") if isinstance(corpo.get("data"), dict) else {}

    try:
        if evento == "qrcode.updated":
            _qrcode_atualizado(instancia, dados)
        elif evento == "connection.update":
            _conexao_atualizada(registro, dados)
        elif evento == "messages.upsert":
            _mensagem_recebida(registro, dados)
    except Exception as exc:
        # 200 mesmo assim: reentrega não conserta erro nosso, só multiplica
        log.error("Falha ao tratar %s de %s: %s", evento, instancia, exc, exc_info=True)

    return "", 200
