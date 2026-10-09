"""Login por CNPJ (via api-master) e autenticação da API de envio por token.

O acesso é negado só quando o cadastro está bloqueado ou cancelado — é a regra
pedida. Como CNPJ não é um segredo (consta em nota fiscal, site e na Receita),
há um limitador de tentativas por IP para que a tela não sirva de varredura de
CNPJs; ele não substitui um segundo fator.
"""
import hmac
import logging
import threading
import time
from functools import wraps

from flask import g, jsonify, redirect, request, session, url_for

from api import master_client
from config import config
from models import instancia as instancia_model

log = logging.getLogger(__name__)

JANELA_TENTATIVAS_S = 300

_tentativas: dict[str, list[float]] = {}
_tentativas_lock = threading.Lock()


class AcessoNegado(Exception):
    """Cadastro bloqueado, cancelado ou inexistente."""


# ------------------------------------------------- limitador de tentativas

def _ip_do_pedido() -> str:
    encaminhado = request.headers.get("X-Forwarded-For", "")
    if encaminhado:
        return encaminhado.split(",")[0].strip()
    return request.remote_addr or "desconhecido"


def registrar_tentativa() -> bool:
    """Carimba uma tentativa. False se o IP estourou o limite da janela."""
    agora = time.monotonic()
    ip = _ip_do_pedido()
    with _tentativas_lock:
        for chave in [c for c, marcas in _tentativas.items()
                      if not marcas or agora - marcas[-1] > JANELA_TENTATIVAS_S]:
            del _tentativas[chave]
        marcas = [m for m in _tentativas.get(ip, []) if agora - m <= JANELA_TENTATIVAS_S]
        marcas.append(agora)
        _tentativas[ip] = marcas
        if len(marcas) > config.LOGIN_TENTATIVAS_MAX:
            log.warning("Login bloqueado por excesso de tentativas: ip=%s", ip)
            return False
        return True


# -------------------------------------------------------------------- login

def autenticar(cnpj: str) -> dict:
    """Valida o CNPJ na api-master. Levanta AcessoNegado ou MasterError."""
    cliente = master_client.obter_cliente(cnpj)
    if cliente is None:
        raise AcessoNegado("CNPJ não encontrado no cadastro.")
    if cliente["cancelado"]:
        raise AcessoNegado("Cadastro cancelado. Fale com a Prêmio Sistemas.")
    if cliente["bloqueado"]:
        raise AcessoNegado("Cadastro bloqueado. Fale com a Prêmio Sistemas.")
    return cliente


def abrir_sessao(cliente: dict) -> None:
    session.clear()
    session["id_cliente"] = cliente["id"]
    session["cnpj"] = cliente["cnpj_cpf"]
    session["empresa"] = cliente["empresa"] or cliente["nome"]
    session.permanent = True


def encerrar_sessao() -> None:
    session.clear()


def cliente_logado() -> dict | None:
    id_cliente = session.get("id_cliente")
    if not id_cliente:
        return None
    return {
        "id": id_cliente,
        "cnpj": session.get("cnpj", ""),
        "empresa": session.get("empresa", ""),
    }


def requer_login(funcao):
    """Painel: redireciona para /login; chamadas /api/* recebem 401 JSON."""
    @wraps(funcao)
    def protegida(*args, **kwargs):
        cliente = cliente_logado()
        if cliente is None:
            if request.path.startswith("/api/"):
                return jsonify({"sucesso": False, "mensagem": "Sessão expirada"}), 401
            return redirect(url_for("auth.login"))
        g.cliente = cliente
        return funcao(*args, **kwargs)

    return protegida


# ------------------------------------------------------- API de envio (token)

def requer_token(funcao):
    """Autentica pelo header Authorization: Bearer <token_api>.

    Põe em g.instancia o registro do dono do token. Token inválido e instância
    desconectada são erros diferentes — quem integra precisa distinguir.
    """
    @wraps(funcao)
    def protegida(*args, **kwargs):
        cabecalho = request.headers.get("Authorization", "")
        partes = cabecalho.split(None, 1)
        token = partes[1].strip() if len(partes) == 2 and partes[0].lower() == "bearer" else ""

        if not token:
            return jsonify({
                "sucesso": False,
                "erro": "token_ausente",
                "mensagem": "Envie o header Authorization: Bearer <seu-token>",
            }), 401

        registro = instancia_model.por_token(token)
        # compara em tempo constante mesmo tendo achado pelo índice: evita que
        # a diferença de tempo entre "achou" e "não achou" vire oráculo
        if registro is None or not hmac.compare_digest(registro["token_api"], token):
            return jsonify({
                "sucesso": False,
                "erro": "token_invalido",
                "mensagem": "Token não reconhecido",
            }), 401

        if registro["status"] != "conectada":
            return jsonify({
                "sucesso": False,
                "erro": "instancia_desconectada",
                "mensagem": "O WhatsApp desta conta não está conectado",
                "status": registro["status"],
            }), 409

        g.instancia = registro
        return funcao(*args, **kwargs)

    return protegida
