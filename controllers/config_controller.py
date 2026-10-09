"""Configurações de saudação e de horário/ausência."""
import logging

from flask import Blueprint, g, request

import resposta
from models import configuracao as configuracao_model
from services import auth_service, horario_service, saudacao_service

log = logging.getLogger(__name__)

configuracoes = Blueprint("configuracoes", __name__)


@configuracoes.route("/api/configuracoes", methods=["GET"])
@auth_service.requer_login
def obter():
    atuais = configuracao_model.por_cliente(g.cliente["id"])
    return resposta.ok({
        "saudacao": atuais["saudacao"],
        "horario": atuais["horario"],
        "aberto_agora": horario_service.esta_aberto(atuais["horario"]),
    })


@configuracoes.route("/api/configuracoes/saudacao", methods=["PUT"])
@auth_service.requer_login
def salvar_saudacao():
    dados = request.get_json(silent=True) or {}
    validada, erro = saudacao_service.validar(dados.get("saudacao", dados))
    if erro:
        return resposta.falha(erro, 400)

    configuracao_model.salvar_saudacao(g.cliente["id"], validada)
    log.info("Saudação salva: cliente=%s ativo=%s", g.cliente["id"], validada["ativo"])
    return resposta.ok({"saudacao": validada}, "Saudação salva.")


@configuracoes.route("/api/configuracoes/horario", methods=["PUT"])
@auth_service.requer_login
def salvar_horario():
    dados = request.get_json(silent=True) or {}
    validado, erro = horario_service.validar(dados.get("horario", dados))
    if erro:
        return resposta.falha(erro, 400)

    configuracao_model.salvar_horario(g.cliente["id"], validado)
    log.info("Horário salvo: cliente=%s ativo=%s", g.cliente["id"], validado["ativo"])
    return resposta.ok(
        {
            "horario": validado,
            "aberto_agora": horario_service.esta_aberto(validado),
            # devolve a mensagem efetiva para o painel mostrar o que o cliente
            # vai receber quando o campo foi deixado em branco
            "msg_fora_efetiva": horario_service.mensagem_fora(validado),
        },
        "Horário salvo.",
    )
