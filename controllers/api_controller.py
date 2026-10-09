"""API pública de envio — autenticada pelo token do cliente.

Contrato: a chamada responde **202 na hora** com um `id_envio`; a mensagem sai
depois, respeitando a digitação simulada (15 a 40 segundos). Consulte o
resultado em GET /api/v1/envio/<id_envio>.

A apikey da Evolution nunca aparece aqui — o cliente só conhece o token dele.

    curl -X POST https://wpp.premiosistemas.com.br/api/v1/mensagem \
      -H "Authorization: Bearer SEU_TOKEN" \
      -H "Content-Type: application/json" \
      -d '{"numero":"5544999998888","mensagem":"Seu pedido saiu para entrega!"}'
"""
import base64
import binascii
import logging

from flask import Blueprint, g, request

import resposta
from config import config
from services import auth_service, fila_envio, messaging_service

log = logging.getLogger(__name__)

api_v1 = Blueprint("api_v1", __name__, url_prefix="/api/v1")

LIMITE_TEXTO = 4096  # limite prático do WhatsApp para mensagem de texto
LIMITE_LEGENDA = 1024


def _numero_do_pedido(dados: dict):
    bruto = dados.get("numero") or dados.get("telefone") or ""
    numero = messaging_service.normalizar_numero(str(bruto))
    if not numero:
        return None, resposta.falha(
            "Número inválido. Use DDI+DDD+número, ex.: 5544999998888.",
            400, erro="numero_invalido",
        )
    return numero, None


def _typing(dados: dict) -> bool:
    valor = dados.get("typing")
    return True if valor is None else bool(valor)


def _enfileirar(funcao, *args, **kwargs):
    try:
        id_envio = fila_envio.enfileirar(funcao, *args, **kwargs)
    except fila_envio.FilaCheia as exc:
        log.warning("Fila cheia: %s", exc)
        return resposta.falha(
            "Muitos envios na fila. Tente novamente em instantes.",
            429, erro="fila_cheia",
        )
    return resposta.ok(
        {"id_envio": id_envio, "status": fila_envio.NA_FILA},
        "Envio aceito. Consulte o status em /api/v1/envio/<id_envio>.",
        202,
    )


# -------------------------------------------------------------------- texto

@api_v1.route("/mensagem", methods=["POST"])
@auth_service.requer_token
def enviar_mensagem():
    """JSON: {"numero": "...", "mensagem": "...", "typing": true}"""
    dados = request.get_json(silent=True) or {}

    numero, erro = _numero_do_pedido(dados)
    if erro:
        return erro

    texto = str(dados.get("mensagem") or dados.get("texto") or "").strip()
    if not texto:
        return resposta.falha("Campo 'mensagem' obrigatório.", 400, erro="mensagem_vazia")
    if len(texto) > LIMITE_TEXTO:
        return resposta.falha(
            f"Mensagem acima de {LIMITE_TEXTO} caracteres.", 400, erro="mensagem_longa"
        )

    return _enfileirar(
        messaging_service.enviar_texto,
        g.instancia["nome_instancia"],
        numero,
        texto,
        typing=_typing(dados),
    )


# ------------------------------------------------------------------- imagem

@api_v1.route("/imagem", methods=["POST"])
@auth_service.requer_token
def enviar_imagem():
    """JSON: {"numero", "imagem" (URL ou base64), "legenda", "nome_arquivo", "typing"}"""
    dados = request.get_json(silent=True) or {}

    numero, erro = _numero_do_pedido(dados)
    if erro:
        return erro

    imagem = str(dados.get("imagem") or dados.get("url") or "").strip()
    if not imagem:
        return resposta.falha(
            "Campo 'imagem' obrigatório (URL pública ou base64).",
            400, erro="imagem_ausente",
        )

    legenda = str(dados.get("legenda") or "").strip()
    if len(legenda) > LIMITE_LEGENDA:
        return resposta.falha(
            f"Legenda acima de {LIMITE_LEGENDA} caracteres.", 400, erro="legenda_longa"
        )

    if not imagem.lower().startswith(("http://", "https://")):
        erro_base64 = _validar_base64(imagem)
        if erro_base64:
            return erro_base64

    return _enfileirar(
        messaging_service.enviar_imagem,
        g.instancia["nome_instancia"],
        numero,
        imagem,
        legenda,
        str(dados.get("nome_arquivo") or ""),
        typing=_typing(dados),
    )


def _validar_base64(imagem: str):
    """Rejeita base64 malformado ou acima do limite antes de ocupar a fila."""
    conteudo = imagem.split(",", 1)[1] if imagem.startswith("data:") else imagem
    # 4 caracteres de base64 = 3 bytes
    if len(conteudo) // 4 * 3 > config.max_imagem_bytes:
        return resposta.falha(
            f"Imagem acima de {config.MAX_IMAGEM_MB} MB.", 400, erro="imagem_grande"
        )
    try:
        base64.b64decode(conteudo, validate=True)
    except (binascii.Error, ValueError):
        return resposta.falha(
            "Imagem inválida: envie uma URL pública ou base64 válido.",
            400, erro="imagem_invalida",
        )
    return None


# ----------------------------------------------------------------- consulta

@api_v1.route("/envio/<id_envio>", methods=["GET"])
@api_v1.route("/mensagem/<id_envio>", methods=["GET"])
@auth_service.requer_token
def consultar_envio(id_envio: str):
    """Situação de um envio. 404 depois do TTL (FILA_RESULTADO_TTL_S)."""
    registro = fila_envio.consultar(id_envio)
    if registro is None:
        return resposta.falha(
            "Envio não encontrado (ou já expirou).", 404, erro="envio_desconhecido"
        )
    return resposta.ok(registro)


# -------------------------------------------------------------------- conta

@api_v1.route("/conta", methods=["GET"])
@auth_service.requer_token
def conta():
    """Confere o token e mostra o estado da conexão — útil no setup da integração."""
    return resposta.ok({
        "empresa": g.instancia["empresa"],
        "numero": g.instancia.get("numero"),
        "status": g.instancia["status"],
        "envios_pendentes": fila_envio.pendentes(),
    })
