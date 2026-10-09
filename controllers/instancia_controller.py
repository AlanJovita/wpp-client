"""Ciclo da instância pelo painel: criar, status/QR, reconectar, reiniciar, desconectar."""
import logging

from flask import Blueprint, g, request

import resposta
from api import evolution_client, master_client
from models import instancia as instancia_model
from services import auth_service, instance_service

log = logging.getLogger(__name__)

instancias = Blueprint("instancias", __name__)


def _serializar(registro: dict | None) -> dict | None:
    """Expõe só o que o painel precisa — o token_api sai à parte, sob pedido."""
    if not registro:
        return None
    return {
        "nome": registro["nome_instancia"],
        "numero": registro.get("numero"),
        "status": registro["status"],
        "criado_em": registro["criado_em"].isoformat() if registro.get("criado_em") else None,
    }


@instancias.route("/api/instancia", methods=["GET"])
@auth_service.requer_login
def obter():
    """Estado atual + QR pendente. É este endpoint que o painel faz polling."""
    registro = instance_service.obter(g.cliente["id"])
    if not registro:
        return resposta.ok({"instancia": None, "qrcode": None})

    # enquanto não está conectada, confere o estado real: o connection.update
    # pode ter se perdido e o painel ficaria preso em "conectando"
    if registro["status"] != "conectada":
        try:
            status = instance_service.sincronizar_status(registro)
            registro["status"] = status
        except evolution_client.EvolutionError as exc:
            log.warning("Status de %s indisponível: %s", registro["nome_instancia"], exc)

    return resposta.ok({
        "instancia": _serializar(registro),
        "qrcode": instance_service.obter_qr(registro["nome_instancia"]),
    })


@instancias.route("/api/instancia", methods=["POST"])
@auth_service.requer_login
def criar():
    """Cria a instância do cliente e devolve o primeiro QR."""
    cliente = g.cliente
    if instance_service.obter(cliente["id"]):
        return resposta.falha("Esta conta já tem um número instanciado.", 409)

    # o nome da instância precisa do id real da api-master, não do que está na
    # sessão, e o cadastro pode ter sido bloqueado desde o login
    try:
        atual = auth_service.autenticar(cliente["cnpj"])
    except auth_service.AcessoNegado as exc:
        return resposta.falha(str(exc), 403)
    except master_client.MasterError as exc:
        log.error("Cadastro não validado na criação: %s", exc)
        return resposta.falha("Não foi possível validar o cadastro agora.", 503)

    try:
        criada = instance_service.criar(atual)
    except evolution_client.EvolutionError as exc:
        log.error("Falha ao criar instância do cliente %s: %s", cliente["id"], exc)
        return resposta.falha("Não foi possível criar a conexão agora.", 502)

    log.info("Instância criada: cliente=%s", cliente["id"])
    return resposta.ok(
        {
            "instancia": _serializar(criada["instancia"]),
            "qrcode": criada["qrcode"],
        },
        "Leia o QR Code no WhatsApp do seu celular.",
        201,
    )


@instancias.route("/api/instancia/conectar", methods=["POST"])
@auth_service.requer_login
def conectar():
    """Pede um QR novo (instância criada mas não conectada, ou caída)."""
    registro = instance_service.obter(g.cliente["id"])
    if not registro:
        return resposta.falha("Nenhuma instância criada.", 404)

    try:
        qr = instance_service.conectar(registro)
    except evolution_client.EvolutionError as exc:
        log.error("Falha ao conectar %s: %s", registro["nome_instancia"], exc)
        return resposta.falha("Não foi possível gerar o QR Code agora.", 502)

    if not qr:
        return resposta.ok({"qrcode": None}, "Este número já está conectado.")
    return resposta.ok({"qrcode": qr}, "Leia o QR Code no WhatsApp do seu celular.")


@instancias.route("/api/instancia/reiniciar", methods=["POST"])
@auth_service.requer_login
def reiniciar():
    registro = instance_service.obter(g.cliente["id"])
    if not registro:
        return resposta.falha("Nenhuma instância criada.", 404)
    try:
        instance_service.reiniciar(registro)
    except evolution_client.EvolutionError as exc:
        return resposta.falha(f"Não foi possível reiniciar: {exc}", 502)
    return resposta.ok(mensagem="Conexão reiniciada.")


@instancias.route("/api/instancia/desconectar", methods=["POST"])
@auth_service.requer_login
def desconectar():
    """Derruba a sessão. O cliente pode reconectar lendo um QR novo."""
    registro = instance_service.obter(g.cliente["id"])
    if not registro:
        return resposta.falha("Nenhuma instância criada.", 404)
    try:
        instance_service.desconectar(registro)
    except evolution_client.EvolutionError as exc:
        return resposta.falha(f"Não foi possível desconectar: {exc}", 502)
    log.info("Instância desconectada: cliente=%s", g.cliente["id"])
    return resposta.ok(mensagem="WhatsApp desconectado.")


@instancias.route("/api/instancia", methods=["DELETE"])
@auth_service.requer_login
def remover():
    """Apaga a instância. Exige ?confirmar=sim — é irreversível."""
    if (request.args.get("confirmar") or "").lower() != "sim":
        return resposta.falha(
            "Confirmação obrigatória para remover a conexão (confirmar=sim).", 400
        )

    registro = instance_service.obter(g.cliente["id"])
    if not registro:
        return resposta.falha("Nenhuma instância criada.", 404)

    try:
        instance_service.remover(registro)
    except evolution_client.EvolutionError as exc:
        return resposta.falha(f"Não foi possível remover: {exc}", 502)

    log.info("Instância removida: cliente=%s", g.cliente["id"])
    return resposta.ok(mensagem="Conexão removida. Suas configurações foram mantidas.")


# ----------------------------------------------------------- token da API

@instancias.route("/api/instancia/token", methods=["GET"])
@auth_service.requer_login
def obter_token():
    registro = instance_service.obter(g.cliente["id"])
    if not registro:
        return resposta.falha("Nenhuma instância criada.", 404)
    return resposta.ok({"token": registro["token_api"]})


@instancias.route("/api/instancia/token", methods=["POST"])
@auth_service.requer_login
def regerar_token():
    """Gera um token novo. Invalida o anterior na hora."""
    registro = instance_service.obter(g.cliente["id"])
    if not registro:
        return resposta.falha("Nenhuma instância criada.", 404)
    token = instancia_model.regerar_token(g.cliente["id"])
    log.info("Token da API regerado: cliente=%s", g.cliente["id"])
    return resposta.ok(
        {"token": token}, "Token novo gerado. O anterior deixou de funcionar."
    )
