"""Log remoto na api-master — POST /v1/erros com id_software configurável (21).

Registra duas coisas:

  erro(...)        falha técnica (exceção, recusa da Evolution, banco fora)
  nao_enviada(...) mensagem que NÃO saiu, e o motivo — inclusive os motivos
                   legítimos (fora do horário, saudação desligada, janela de
                   repetição ativa). Sem isto, "o bot não respondeu" é
                   indistinguível de "o bot decidiu não responder".

**Logar não pode custar caro.** O `syscon_app` faz um POST síncrono a cada
`log()` e isso é um problema conhecido do ecossistema; aqui o envio é
assíncrono, com:

  - fila limitada (LOG_REMOTO_FILA_MAX): cheia, descarta e conta o descarte
  - thread única de envio: o webhook responde 200 sem esperar HTTP nenhum
  - deduplicação por janela: a mesma ocorrência repetida não vira N requisições,
    vira uma com `qtd` acumulado
  - disjuntor: após LOG_REMOTO_FALHAS_MAX falhas seguidas, para de tentar por
    LOG_REMOTO_PAUSA_S — api-master fora do ar não pode virar fila infinita

Atenção ao contrato de lá: `POST /v1/erros` responde **200 "Erro registrado"
sempre**, mesmo com payload inválido (`LogBLL.setErroJson` engole a exceção e
devolve None). Ou seja, 200 não prova que gravou. O que validamos aqui é só o
transporte.
"""
import logging
import queue
import threading
import time

import requests

from config import config

log = logging.getLogger(__name__)

TIMEOUT = 10

_fila: "queue.Queue[dict]" = queue.Queue(maxsize=max(10, config.LOG_REMOTO_FILA_MAX))
_thread: threading.Thread | None = None
_lock = threading.Lock()
_session = requests.Session()

# chave de dedup -> (instante do último envio, repetições suprimidas)
_vistos: dict[tuple, tuple[float, int]] = {}
_vistos_lock = threading.Lock()

_descartados = 0
_falhas_seguidas = 0
_pausado_ate = 0.0

LIMITE_DESCRICAO = 1000


# ------------------------------------------------------------------ envio

def _enviar(carga: dict) -> bool:
    url = f"{config.MASTER_API_URL}/erros"
    try:
        resposta = _session.post(url, json=carga, timeout=TIMEOUT)
    except requests.RequestException as exc:
        log.debug("Log remoto falhou (rede): %s", exc)
        return False
    if resposta.status_code >= 400:
        log.debug("Log remoto recusado: %s %s", resposta.status_code, resposta.text[:200])
        return False
    return True


def _trabalhador() -> None:
    global _falhas_seguidas, _pausado_ate
    while True:
        carga = _fila.get()
        try:
            if time.monotonic() < _pausado_ate:
                continue  # disjuntor aberto: descarta sem tentar
            if _enviar(carga):
                _falhas_seguidas = 0
                continue
            _falhas_seguidas += 1
            if _falhas_seguidas >= config.LOG_REMOTO_FALHAS_MAX:
                _pausado_ate = time.monotonic() + config.LOG_REMOTO_PAUSA_S
                _falhas_seguidas = 0
                log.warning(
                    "Log remoto pausado por %ds após falhas seguidas na api-master",
                    config.LOG_REMOTO_PAUSA_S,
                )
        except Exception as exc:
            # a thread de log não pode morrer, ou o app perde observabilidade
            log.debug("Log remoto: erro inesperado no envio: %s", exc)
        finally:
            _fila.task_done()


def iniciar() -> None:
    """Sobe a thread de envio (idempotente)."""
    global _thread
    if not config.LOG_REMOTO_ATIVO:
        log.info("Log remoto desligado (LOG_REMOTO_ATIVO=0)")
        return
    with _lock:
        if _thread is not None and _thread.is_alive():
            return
        _thread = threading.Thread(target=_trabalhador, name="log-remoto", daemon=True)
        _thread.start()
    log.info(
        "Log remoto ativo -> %s/erros (id_software=%s)",
        config.MASTER_API_URL, config.LOG_REMOTO_ID_SOFTWARE,
    )


# ------------------------------------------------------------- deduplicação

def _consumir_dedup(chave: tuple) -> int | None:
    """Quantas ocorrências enviar agora, ou None se deve suprimir.

    Primeira vez na janela → 1 (ou 1 + o que ficou suprimido antes).
    Repetição dentro da janela → None, e o contador sobe.
    """
    agora = time.monotonic()
    janela = config.LOG_REMOTO_DEDUP_S
    with _vistos_lock:
        for k in [k for k, (quando, _) in _vistos.items() if agora - quando > janela * 2]:
            del _vistos[k]

        registro = _vistos.get(chave)
        if registro is None:
            _vistos[chave] = (agora, 0)
            return 1

        quando, suprimidas = registro
        if agora - quando < janela:
            _vistos[chave] = (quando, suprimidas + 1)
            return None

        _vistos[chave] = (agora, 0)
        return suprimidas + 1


# ------------------------------------------------------------------- API

def _registrar(
    descricao: str,
    id_cliente: int = 0,
    classe: str = "",
    metodo: str = "",
    linha: int = 0,
    classificacao: int = 0,
) -> None:
    global _descartados

    if not config.LOG_REMOTO_ATIVO:
        return

    descricao = (descricao or "").strip()[:LIMITE_DESCRICAO]
    if not descricao:
        return

    qtd = _consumir_dedup((id_cliente, classe, metodo, descricao))
    if qtd is None:
        return

    carga = {
        "id_cliente": int(id_cliente or 0),
        "id_software": config.LOG_REMOTO_ID_SOFTWARE,
        "descricao": descricao,
        "versao": config.VERSAO,
        "classe": classe or "",
        "metodo": metodo or "",
        "linha": int(linha or 0),
        "qtd": qtd,
        "status": 0,
        "classificacao": classificacao,
        "origem": 0,
        "data": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }

    try:
        _fila.put_nowait(carga)
    except queue.Full:
        _descartados += 1
        if _descartados % 50 == 1:
            log.warning("Fila do log remoto cheia — %d descarte(s)", _descartados)


def erro(descricao: str, id_cliente: int = 0, classe: str = "", metodo: str = "",
         linha: int = 0) -> None:
    """Falha técnica."""
    _registrar(descricao, id_cliente, classe, metodo, linha, classificacao=0)


def nao_enviada(motivo: str, detalhe: str = "", id_cliente: int = 0,
                instancia: str = "", numero_mascarado: str = "") -> None:
    """Mensagem que não saiu, com o motivo.

    Chamada também nos casos legítimos (fora do horário, saudação desligada),
    porque é justamente a diferença entre "quebrou" e "decidiu não responder"
    que não dá para enxergar de fora.
    """
    if not config.LOG_REMOTO_NAO_ENVIADAS:
        return
    partes = [f"mensagem nao enviada: {motivo}"]
    if instancia:
        partes.append(f"instancia={instancia}")
    if numero_mascarado:
        partes.append(f"numero={numero_mascarado}")
    if detalhe:
        partes.append(detalhe)
    _registrar(
        " | ".join(partes), id_cliente,
        classe="envio", metodo=motivo, classificacao=1,
    )


def diagnostico() -> dict:
    """Estado do log remoto, para a rota de diagnóstico do painel."""
    return {
        "ativo": config.LOG_REMOTO_ATIVO,
        "id_software": config.LOG_REMOTO_ID_SOFTWARE,
        "destino": f"{config.MASTER_API_URL}/erros",
        "na_fila": _fila.qsize(),
        "descartados": _descartados,
        "pausado": time.monotonic() < _pausado_ate,
    }


# ------------------------------------------------- handler do logging padrão

class ManipuladorMaster(logging.Handler):
    """Espelha ERROR/CRITICAL do logging padrão para a api-master.

    Assim todo `log.error(...)` já existente no app vira registro remoto sem
    precisar de chamada extra em cada ponto. O `id_cliente` fica 0 porque o
    logging não sabe de quem é — onde o cliente importa, chame `erro()` direto.
    """

    def emit(self, registro: logging.LogRecord) -> None:
        try:
            if registro.name.startswith("api.log_remoto"):
                return  # evita laço: falha do log remoto logando no log remoto
            erro(
                self.format(registro),
                classe=registro.module,
                metodo=registro.funcName,
                linha=registro.lineno,
            )
        except Exception:
            pass  # logging nunca pode derrubar quem chamou
