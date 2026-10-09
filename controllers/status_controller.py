"""Publicação de status no WhatsApp — imagem + legenda, no ato."""
import logging

from flask import Blueprint, g, request

import resposta
from api import evolution_client
from services import auth_service, instance_service, status_service

log = logging.getLogger(__name__)

status_bp = Blueprint("status", __name__)


@status_bp.route("/api/status", methods=["POST"])
@auth_service.requer_login
def publicar():
    """multipart/form-data: campo `imagem` (arquivo) + `legenda` (texto)."""
    registro = instance_service.obter(g.cliente["id"])
    if not registro:
        return resposta.falha("Nenhuma instância criada.", 404)
    if registro["status"] != "conectada":
        return resposta.falha("Conecte o WhatsApp antes de publicar um status.", 409)

    imagem, erro = status_service.validar_imagem(request.files.get("imagem"))
    if erro:
        return resposta.falha(erro, 400)

    legenda, erro = status_service.validar_legenda(request.form.get("legenda"))
    if erro:
        return resposta.falha(erro, 400)

    try:
        publicado = status_service.publicar(
            registro["nome_instancia"], imagem, legenda
        )
    except evolution_client.EvolutionError as exc:
        log.error("Status não publicado (cliente=%s): %s", g.cliente["id"], exc)
        # 404 aqui costuma ser build da Evolution sem a rota de status
        if exc.status == 404:
            return resposta.falha(
                "Esta versão da Evolution API não suporta publicar status.", 501
            )
        return resposta.falha(f"O WhatsApp recusou a publicação: {exc}", 502)

    return resposta.ok(publicado, "Status publicado com sucesso.")
