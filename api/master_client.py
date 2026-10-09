"""Consulta de cliente na api-master — base do login por CNPJ.

Endpoint usado: GET {MASTER_API_URL}/cliente/<cnpj>, que devolve o envelope
{success, message, data} com o objeto Cliente (inclui `bloqueado` e `cancelado`).

Duas defesas contra o estado real da api-master:

1. O CNPJ é reduzido a dígitos aqui. O BLL de lá monta o SQL por interpolação
   sem quotes (`cnpj_cpf={}`), então qualquer caractere não numérico vira erro
   de SQL — ou pior.
2. Se a rota v1 falhar com 5xx, cai para a rota legado na raiz (`/cliente/<cnpj>`),
   que devolve o objeto cru. O legado manda `bloqueado` como "sim"/"nao", daí o
   parser aceitar ambos os formatos.
"""
import logging
import re

import requests

from config import config

log = logging.getLogger(__name__)

TIMEOUT = 15

_session = requests.Session()


class MasterError(Exception):
    """Falha ao consultar a api-master."""


def apenas_digitos(valor: str) -> str:
    return re.sub(r"\D", "", valor or "")


def _para_bool(valor) -> bool:
    """Aceita booleano JSON e as strings do desktop VB.NET ("sim"/"nao")."""
    if isinstance(valor, bool):
        return valor
    if isinstance(valor, str):
        return valor.strip().lower() in ("sim", "s", "true", "1")
    return bool(valor)


def _normalizar(bruto: dict) -> dict:
    return {
        "id": int(bruto.get("id") or 0),
        "cnpj_cpf": str(bruto.get("cnpj_cpf") or ""),
        "empresa": str(bruto.get("empresa") or "").strip(),
        "nome": str(bruto.get("nome") or "").strip(),
        "bloqueado": _para_bool(bruto.get("bloqueado")),
        "cancelado": _para_bool(bruto.get("cancelado")),
    }


def _buscar(url: str) -> dict | None:
    resposta = _session.get(url, timeout=TIMEOUT)
    if resposta.status_code == 404:
        return None
    resposta.raise_for_status()
    corpo = resposta.json()

    # v1 responde envelopado; o legado responde o objeto cru
    if isinstance(corpo, dict) and "data" in corpo and "success" in corpo:
        if not corpo.get("success"):
            return None
        corpo = corpo.get("data")

    if not isinstance(corpo, dict) or not corpo.get("id"):
        return None
    return _normalizar(corpo)


def obter_cliente(cnpj: str) -> dict | None:
    """Cliente por CNPJ/CPF, ou None se não existir. Levanta MasterError na falha.

    Distinguir "não existe" de "api-master fora do ar" importa: a primeira é
    erro do usuário na tela de login, a segunda não pode virar "CNPJ inválido".
    """
    digitos = apenas_digitos(cnpj)
    if not digitos:
        return None

    try:
        return _buscar(f"{config.MASTER_API_URL}/cliente/{digitos}")
    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else 0
        if status < 500:
            raise MasterError(f"api-master respondeu {status}") from exc
        log.warning("api-master v1 falhou (%s) — tentando rota legado", status)
    except requests.RequestException as exc:
        raise MasterError(f"api-master inacessível: {exc}") from exc

    # fallback: rota legado na raiz (sem o /v1)
    raiz = config.MASTER_API_URL.rsplit("/v1", 1)[0]
    try:
        return _buscar(f"{raiz}/cliente/{digitos}")
    except requests.RequestException as exc:
        raise MasterError(f"api-master inacessível: {exc}") from exc
