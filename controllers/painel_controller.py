"""Página única do painel.

O HTML sai com o estado já embutido (instância + configurações) para a tela não
"piscar" vazia; o JS depois só atualiza status, QR e salva configuração.
"""
import logging

from flask import Blueprint, g, render_template

from config import config
from models import configuracao as configuracao_model
from services import auth_service, instance_service

log = logging.getLogger(__name__)

painel = Blueprint("painel", __name__)


@painel.route("/")
@auth_service.requer_login
def inicio():
    cliente = g.cliente
    registro = instance_service.obter(cliente["id"])
    configuracoes = configuracao_model.por_cliente(cliente["id"])

    return render_template(
        "painel.html",
        cliente=cliente,
        instancia=registro,
        saudacao=configuracoes["saudacao"] or {},
        horario=configuracoes["horario"] or {},
        base_api=config.PANEL_BASE_URL,
        janela_padrao=config.SAUDACAO_JANELA_HORAS,
        max_imagem_mb=config.MAX_IMAGEM_MB,
    )
