"""Login por CNPJ e logout."""
import logging

from flask import Blueprint, redirect, render_template, request, url_for

from api import master_client
from services import auth_service

log = logging.getLogger(__name__)

auth = Blueprint("auth", __name__)


@auth.route("/login", methods=["GET", "POST"])
def login():
    if auth_service.cliente_logado():
        return redirect(url_for("painel.inicio"))

    if request.method == "GET":
        return render_template("login.html")

    cnpj = (request.form.get("cnpj") or "").strip()
    if not master_client.apenas_digitos(cnpj):
        return render_template("login.html", erro="Informe o CNPJ.", cnpj=cnpj), 400

    if not auth_service.registrar_tentativa():
        return render_template(
            "login.html",
            erro="Muitas tentativas. Aguarde alguns minutos e tente de novo.",
            cnpj=cnpj,
        ), 429

    try:
        cliente = auth_service.autenticar(cnpj)
    except auth_service.AcessoNegado as exc:
        return render_template("login.html", erro=str(exc), cnpj=cnpj), 403
    except master_client.MasterError as exc:
        log.error("Login indisponível: %s", exc)
        return render_template(
            "login.html",
            erro="Não foi possível validar o cadastro agora. Tente novamente em instantes.",
            cnpj=cnpj,
        ), 503

    auth_service.abrir_sessao(cliente)
    log.info("Login: cliente=%s empresa=%r", cliente["id"], cliente["empresa"])
    return redirect(url_for("painel.inicio"))


@auth.route("/logout")
def logout():
    auth_service.encerrar_sessao()
    return redirect(url_for("auth.login"))
