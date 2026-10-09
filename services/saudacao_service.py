"""Saudação automática e mensagem de ausência.

Formato do JSON salvo em client_configuracoes.saudacao:
  {"ativo": bool, "mensagem": str, "janela_horas": int}

Regra de disparo:
  - fora do horário de funcionamento (horario.ativo) → só a mensagem de ausência
  - dentro do horário e saudação ligada            → a saudação
  - nos dois casos, no máximo uma vez por número dentro da janela

A janela é o que separa "saudação" de "spam": sem ela, cada mensagem que o
contato manda ganharia um "Olá!" de volta.

O controle de repetição vive em memória, não no banco: é só
(instância, número) → instante, com poda por TTL e teto de entradas. Perder
esse estado num redeploy custa, no pior caso, uma saudação repetida — barato o
bastante para não valer uma tabela nem um Redis, e coerente com a exigência de
não armazenar nada sobre quem conversou.
"""
import logging
import threading
import time

from config import config
from services import horario_service

log = logging.getLogger(__name__)

LIMITE_MSG = 1000
MSG_PADRAO = "Olá! 👋 Obrigado pelo seu contato. Já recebemos sua mensagem e vamos te responder em instantes."

_vistos: dict[tuple[str, str], float] = {}
_vistos_lock = threading.Lock()


# ---------------------------------------------------------------- validação

def validar(dados) -> tuple[dict | None, str | None]:
    """Normaliza e valida o payload do painel; retorna (saudacao, erro)."""
    if not isinstance(dados, dict):
        return None, "formato de saudação inválido"

    mensagem = str(dados.get("mensagem") or "").strip()
    ativo = bool(dados.get("ativo", False))
    if ativo and not mensagem:
        return None, "escreva a mensagem de saudação para poder ativá-la"
    if len(mensagem) > LIMITE_MSG:
        return None, f"mensagem acima de {LIMITE_MSG} caracteres"

    # `or` aqui seria errado: janela_horas=0 é um valor informado (e inválido,
    # porque significaria saudar a cada mensagem), não um campo em branco
    bruto = dados.get("janela_horas")
    if bruto is None or bruto == "":
        bruto = config.SAUDACAO_JANELA_HORAS
    try:
        janela = int(bruto)
    except (TypeError, ValueError):
        return None, "janela de repetição inválida"
    if not 1 <= janela <= 720:
        return None, "janela de repetição fora do intervalo (1 a 720 horas)"

    return {"ativo": ativo, "mensagem": mensagem, "janela_horas": janela}, None


# --------------------------------------------------- controle de repetição

def _podar(agora: float, limite_s: float) -> None:
    """Remove números vencidos; se ainda estourar o teto, descarta os mais antigos.

    Chamado sempre com _vistos_lock tomado.
    """
    vencidos = [c for c, quando in _vistos.items() if agora - quando > limite_s]
    for chave in vencidos:
        del _vistos[chave]

    excedente = len(_vistos) - config.SAUDACAO_MAX_NUMEROS
    if excedente > 0:
        mais_antigos = sorted(_vistos, key=_vistos.get)[:excedente]
        for chave in mais_antigos:
            del _vistos[chave]
        log.warning(
            "Controle de saudação no teto (%d números): %d entradas descartadas",
            config.SAUDACAO_MAX_NUMEROS, excedente,
        )


def _consumir_janela(instancia: str, numero: str, janela_horas: int) -> bool:
    """True se este número pode ser saudado agora (e carimba o instante).

    Operação atômica: duas mensagens simultâneas do mesmo contato não geram
    duas saudações.
    """
    limite_s = max(1, janela_horas) * 3600
    chave = (instancia, numero)
    agora = time.monotonic()
    with _vistos_lock:
        _podar(agora, limite_s)
        anterior = _vistos.get(chave)
        if anterior is not None and agora - anterior < limite_s:
            return False
        _vistos[chave] = agora
        return True


def esquecer_instancia(instancia: str) -> None:
    """Limpa o controle de uma instância (usado ao desconectar/deletar)."""
    with _vistos_lock:
        for chave in [c for c in _vistos if c[0] == instancia]:
            del _vistos[chave]


# -------------------------------------------------------------------- regra

def texto_para_responder(configuracoes: dict) -> tuple[str | None, int]:
    """Decide o que responder agora. Devolve (texto ou None, janela_horas).

    Fora do horário manda só a ausência — nunca saudação + ausência em sequência.
    """
    saudacao = configuracoes.get("saudacao") or {}
    horario = configuracoes.get("horario") or {}
    janela = int(saudacao.get("janela_horas") or config.SAUDACAO_JANELA_HORAS)

    if not horario_service.esta_aberto(horario):
        return horario_service.mensagem_fora(horario), janela

    if not saudacao.get("ativo"):
        return None, janela
    return (saudacao.get("mensagem") or MSG_PADRAO), janela


def preparar(instancia: str, numero: str, configuracoes: dict) -> str | None:
    """Texto a enviar para este número agora, ou None se não há o que mandar.

    Já consome a janela de repetição — chame no fluxo do webhook (síncrono),
    antes de jogar o envio na fila.
    """
    texto, janela = texto_para_responder(configuracoes)
    if not texto:
        return None
    if not _consumir_janela(instancia, numero, janela):
        return None
    return texto
