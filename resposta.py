"""Envelope JSON único do app: {sucesso, mensagem, dados}.

Em português para casar com o resto do ecossistema. A API pública de envio usa
o mesmo formato, acrescentando `erro` com um código estável para o integrador
tratar em código (ver controllers/api_controller).
"""
from flask import jsonify


def ok(dados=None, mensagem: str = "", status: int = 200):
    return jsonify({"sucesso": True, "mensagem": mensagem, "dados": dados}), status


def falha(mensagem: str, status: int = 400, erro: str | None = None, dados=None):
    corpo = {"sucesso": False, "mensagem": mensagem, "dados": dados}
    if erro:
        corpo["erro"] = erro
    return jsonify(corpo), status
