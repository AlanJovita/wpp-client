"""Cliente HTTP da Evolution API v2 — instâncias, envio e status.

Toda chamada à Evolution passa por aqui. Sessão requests reutilizada
(keep-alive), retry com backoff só em 5xx/timeout.

A instância deste projeto é a MESMA que o wpp-premio usa. O isolamento entre os
dois é o webhook: cada instância carrega a própria URL de callback, definida na
criação. Por isso todo nome de instância criado aqui leva o prefixo `cli_`
(ver services/instance_service) e nenhuma rotina aqui lista ou apaga instância
que não seja desse prefixo.
"""
import logging
import time

import requests

from config import config

log = logging.getLogger(__name__)

EVENTOS_WEBHOOK = ["MESSAGES_UPSERT", "QRCODE_UPDATED", "CONNECTION_UPDATE"]

_session = requests.Session()

TIMEOUT = 30  # segundos
MAX_TENTATIVAS = 3


class EvolutionError(Exception):
    """Erro de comunicação com a Evolution API."""

    def __init__(self, mensagem, status=None, corpo=None):
        super().__init__(mensagem)
        self.status = status
        self.corpo = corpo


def _request(metodo: str, caminho: str, json: dict | None = None, retry: bool = False):
    url = f"{config.EVOLUTION_URL}{caminho}"
    tentativas = MAX_TENTATIVAS if retry else 1
    ultimo_erro = None

    for tentativa in range(tentativas):
        if tentativa > 0:
            time.sleep(2 ** (tentativa - 1))  # 1s, 2s
        try:
            resposta = _session.request(
                metodo,
                url,
                json=json,
                timeout=TIMEOUT,
                headers={"apikey": config.EVOLUTION_API_KEY},
            )
        except requests.RequestException as exc:
            ultimo_erro = EvolutionError(f"Falha de rede em {metodo} {caminho}: {exc}")
            log.warning("Evolution: %s (tentativa %d)", ultimo_erro, tentativa + 1)
            continue

        if resposta.status_code >= 500:
            ultimo_erro = EvolutionError(
                f"Erro {resposta.status_code} em {metodo} {caminho}",
                status=resposta.status_code,
                corpo=resposta.text[:500],
            )
            log.warning("Evolution: %s (tentativa %d)", ultimo_erro, tentativa + 1)
            continue

        if resposta.status_code >= 400:
            raise EvolutionError(
                f"Erro {resposta.status_code} em {metodo} {caminho}: {resposta.text[:300]}",
                status=resposta.status_code,
                corpo=resposta.text[:500],
            )

        try:
            return resposta.json()
        except ValueError:
            return {}

    raise ultimo_erro


def _config_webhook(instancia: str) -> dict:
    """Webhook apontando para ESTE app — o que separa wpp-client de wpp-premio."""
    return {
        "enabled": True,
        "url": f"{config.PANEL_BASE_URL}/webhook/{instancia}",
        "byEvents": False,
        "base64": True,  # QR chega em base64 no payload
        "headers": {"x-webhook-token": config.WEBHOOK_SECRET},
        "events": EVENTOS_WEBHOOK,
    }


# ---------------------------------------------------------------- instâncias

def criar_instancia(instancia: str) -> dict:
    """POST /instance/create — cria já com o webhook deste app configurado."""
    return _request(
        "POST",
        "/instance/create",
        json={
            "instanceName": instancia,
            "integration": "WHATSAPP-BAILEYS",
            "qrcode": True,
            "webhook": _config_webhook(instancia),
        },
    )


def conectar(instancia: str) -> dict:
    """GET /instance/connect/<instancia> — inicia conexão e devolve o QR."""
    return _request("GET", f"/instance/connect/{instancia}")


def estado_conexao(instancia: str) -> dict:
    """GET /instance/connectionState/<instancia>."""
    return _request("GET", f"/instance/connectionState/{instancia}")


def reiniciar(instancia: str) -> dict:
    """POST /instance/restart/<instancia>."""
    return _request("POST", f"/instance/restart/{instancia}")


def desconectar(instancia: str) -> dict:
    """DELETE /instance/logout/<instancia> — derruba a sessão, mantém a instância."""
    return _request("DELETE", f"/instance/logout/{instancia}")


def deletar_instancia(instancia: str) -> dict:
    """DELETE /instance/delete/<instancia>."""
    return _request("DELETE", f"/instance/delete/{instancia}")


def reconfigurar_webhook(instancia: str) -> dict:
    """POST /webhook/set/<instancia> — reaponta o webhook para este app."""
    return _request(
        "POST", f"/webhook/set/{instancia}", json={"webhook": _config_webhook(instancia)}
    )


def consultar_webhook(instancia: str) -> dict:
    """GET /webhook/find/<instancia> — webhook efetivo na Evolution."""
    return _request("GET", f"/webhook/find/{instancia}")


# --------------------------------------------------------------------- envio

def enviar_presenca(
    instancia: str, numero: str, presence: str = "composing", delay_ms: int = 1200
) -> dict:
    """POST /chat/sendPresence/<instancia> — "digitando…" no aparelho do contato.

    Best-effort, sem retry: falha de presença nunca deve abortar o envio.
    Atenção: algumas builds da Evolution só respondem depois do delay — quem
    chama precisa descontar o tempo gasto (ver services/typing_service).
    """
    return _request(
        "POST",
        f"/chat/sendPresence/{instancia}",
        json={"number": numero, "presence": presence, "delay": delay_ms},
    )


def enviar_texto(instancia: str, numero: str, texto: str) -> dict:
    """POST /message/sendText/<instancia> — com retry em 5xx/timeout."""
    return _request(
        "POST",
        f"/message/sendText/{instancia}",
        json={"number": numero, "text": texto},
        retry=True,
    )


def enviar_midia(
    instancia: str,
    numero: str,
    midia: str,
    tipo: str = "image",
    legenda: str = "",
    nome_arquivo: str = "",
) -> dict:
    """POST /message/sendMedia/<instancia> — `midia` é URL ou base64."""
    payload = {
        "number": numero,
        "mediatype": tipo,  # image | video | document | audio
        "media": midia,
        "caption": legenda,
    }
    if nome_arquivo:
        payload["fileName"] = nome_arquivo
    return _request(
        "POST", f"/message/sendMedia/{instancia}", json=payload, retry=True
    )


# -------------------------------------------------------------------- status

def publicar_status(
    instancia: str,
    conteudo: str,
    legenda: str = "",
    tipo: str = "image",
    todos_contatos: bool = True,
    destinatarios: list[str] | None = None,
) -> dict:
    """POST /message/sendStatus/<instancia> — publica no status do WhatsApp.

    Status no WhatsApp não é broadcast aberto: vai para uma lista de
    destinatários. Com `todos_contatos=True` a Evolution monta essa lista a
    partir dos contatos da própria instância — nenhum contato é armazenado aqui.

    `conteudo` é URL ou base64 da imagem. Sem retry: republicar um status que
    talvez tenha saído duplicaria a publicação no aparelho do cliente.
    """
    payload = {
        "type": tipo,
        "content": conteudo,
        "caption": legenda,
        "allContacts": todos_contatos,
    }
    if destinatarios:
        payload["statusJidList"] = destinatarios
        payload["allContacts"] = False
    return _request("POST", f"/message/sendStatus/{instancia}", json=payload)
