"""Ciclo de vida da instância do cliente na Evolution compartilhada.

Um cliente = uma instância, nomeada `cli_<id_cliente>`. O id vem da api-master;
o CNPJ não entra no nome para não vazar dado do cliente nos logs da Evolution,
que é compartilhada com o wpp-premio.

O QR não é armazenado: fica num cache em memória com TTL curto, alimentado pelo
webhook QRCODE_UPDATED e consumido pelo painel por polling. Socket.IO custaria
RAM e uma dependência que este projeto não precisa.
"""
import logging
import threading
import time

from api import evolution_client
from models import configuracao as configuracao_model
from models import instancia as instancia_model
from services import saudacao_service

log = logging.getLogger(__name__)

QR_TTL_S = 90

_qr: dict[str, tuple[str, float]] = {}
_qr_lock = threading.Lock()


# ------------------------------------------------------------------- QR cache

def guardar_qr(instancia: str, base64_png: str) -> None:
    with _qr_lock:
        _qr[instancia] = (base64_png, time.monotonic())


def obter_qr(instancia: str) -> str | None:
    agora = time.monotonic()
    with _qr_lock:
        for chave in [c for c, (_, quando) in _qr.items() if agora - quando > QR_TTL_S]:
            del _qr[chave]
        registro = _qr.get(instancia)
        return registro[0] if registro else None


def limpar_qr(instancia: str) -> None:
    with _qr_lock:
        _qr.pop(instancia, None)


# ------------------------------------------------------------------- leitura

def obter(id_cliente: int) -> dict | None:
    return instancia_model.por_cliente(id_cliente)


def _extrair_qr(resposta: dict | None) -> str | None:
    """O QR vem como `base64` na raiz ou dentro de `qrcode`, conforme a rota."""
    if not isinstance(resposta, dict):
        return None
    for origem in (resposta, resposta.get("qrcode") or {}):
        if isinstance(origem, dict):
            base64 = origem.get("base64")
            if base64:
                return base64
    return None


def _estado_para_status(estado: str | None) -> str:
    return {
        "open": "conectada",
        "connecting": "conectando",
        "close": "desconectada",
    }.get(estado or "", "desconectada")


# ------------------------------------------------------------------- escrita

def criar(cliente: dict) -> dict:
    """Cria a instância na Evolution e registra a linha local. Devolve o QR."""
    id_cliente = cliente["id"]
    nome = instancia_model.nome_para_cliente(id_cliente)

    try:
        resposta = evolution_client.criar_instancia(nome)
    except evolution_client.EvolutionError as exc:
        # 403/409 = instância já existe na Evolution (sobra de tentativa
        # anterior ou linha local apagada). Reaproveita em vez de travar o
        # cliente para sempre.
        if exc.status in (400, 403, 409):
            log.warning("Instância %s já existe na Evolution — reaproveitando", nome)
            evolution_client.reconfigurar_webhook(nome)
            resposta = evolution_client.conectar(nome)
        else:
            raise

    registro = instancia_model.criar(
        id_cliente, cliente.get("cnpj_cpf") or "", cliente.get("empresa") or "", nome
    )

    qr = _extrair_qr(resposta)
    if qr:
        guardar_qr(nome, qr)
    return {"instancia": registro, "qrcode": qr}


def conectar(registro: dict) -> str | None:
    """Reabre a conexão e devolve o QR novo (ou None se já estiver conectada)."""
    nome = registro["nome_instancia"]
    resposta = evolution_client.conectar(nome)
    qr = _extrair_qr(resposta)
    if qr:
        guardar_qr(nome, qr)
        instancia_model.atualizar_status(nome, "conectando")
    return qr


def reiniciar(registro: dict) -> None:
    evolution_client.reiniciar(registro["nome_instancia"])


def sincronizar_status(registro: dict) -> str:
    """Consulta o estado real na Evolution e grava. Autocura webhook perdido.

    Existe porque a Evolution pode não entregar um connection.update — sem esta
    checagem o painel mostraria "conectando" para sempre.
    """
    nome = registro["nome_instancia"]
    try:
        resposta = evolution_client.estado_conexao(nome)
    except evolution_client.EvolutionError as exc:
        if exc.status == 404:
            # a instância sumiu da Evolution: a linha local virou órfã
            log.warning("Instância %s não existe mais na Evolution", nome)
            instancia_model.atualizar_status(nome, "desconectada")
            return "desconectada"
        raise

    bruto = resposta.get("instance") if isinstance(resposta, dict) else None
    estado = (bruto or resposta or {}).get("state")
    status = _estado_para_status(estado)

    if status != registro.get("status"):
        instancia_model.atualizar_status(nome, status)
    if status == "conectada":
        limpar_qr(nome)
        numero = (bruto or {}).get("owner") or (bruto or {}).get("number")
        if numero:
            numero = str(numero).split("@")[0]
            if numero != registro.get("numero"):
                instancia_model.atualizar_numero(nome, numero)
    return status


def desconectar(registro: dict) -> None:
    """Derruba a sessão do WhatsApp mantendo a instância (permite novo QR)."""
    nome = registro["nome_instancia"]
    try:
        evolution_client.desconectar(nome)
    except evolution_client.EvolutionError as exc:
        if exc.status != 404:
            raise
    instancia_model.atualizar_status(nome, "desconectada")
    instancia_model.atualizar_numero(nome, None)
    limpar_qr(nome)
    saudacao_service.esquecer_instancia(nome)


def remover(registro: dict, apagar_configuracoes: bool = False) -> None:
    """Apaga a instância na Evolution e a linha local.

    Destrutivo: o cliente perde a conexão e precisa ler o QR de novo. As
    configurações de saudação e horário são preservadas por padrão, para que
    reconectar não exija reconfigurar tudo.
    """
    nome = registro["nome_instancia"]
    try:
        evolution_client.deletar_instancia(nome)
    except evolution_client.EvolutionError as exc:
        if exc.status != 404:
            raise
        log.warning("Instância %s já não existia na Evolution", nome)

    instancia_model.remover(registro["id_cliente"])
    if apagar_configuracoes:
        configuracao_model.remover(registro["id_cliente"])
    limpar_qr(nome)
    saudacao_service.esquecer_instancia(nome)
