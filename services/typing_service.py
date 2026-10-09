"""Digitação simulada — "digitando…" proporcional ao texto + adicional randômico.

Cálculo: len(texto) / DIGITACAO_CHARS_POR_SEGUNDO, limitado a
[DIGITACAO_MIN_S, DIGITACAO_MAX_S], mais um sorteio uniforme entre
DIGITACAO_EXTRA_MIN_S e DIGITACAO_EXTRA_MAX_S (padrão 3 a 12s).

A presença "composing" expira sozinha no WhatsApp em poucos segundos, então ela
é reenviada a cada PRESENCA_PASSO_S até o tempo total acabar — sem isso o
"digitando…" apaga e volta a mensagem fria.
"""
import logging
import random
import time

from api import evolution_client
from config import config

log = logging.getLogger(__name__)


def calcular_segundos(texto: str) -> float:
    """Tempo total de digitação simulada para `texto`, em segundos."""
    caracteres = len(texto or "")
    cps = config.DIGITACAO_CHARS_POR_SEGUNDO or 12.0
    proporcional = min(
        max(caracteres / cps, config.DIGITACAO_MIN_S), config.DIGITACAO_MAX_S
    )
    extra = random.uniform(config.DIGITACAO_EXTRA_MIN_S, config.DIGITACAO_EXTRA_MAX_S)
    return proporcional + extra


def simular(instancia: str, numero: str, texto: str) -> float:
    """Mostra "digitando…" e bloqueia pelo tempo calculado. Retorna o tempo gasto.

    Best-effort: falha de presença só vira log, o envio segue. O tempo de espera
    é cumprido mesmo se a presença falhar — o ritmo humano é o objetivo, não o
    indicador na tela.
    """
    if not config.SIMULAR_DIGITACAO:
        return 0.0

    total = calcular_segundos(texto)
    passo = max(1.0, config.PRESENCA_PASSO_S)
    comeco = time.monotonic()
    restante = total

    while restante > 0.05:
        trecho = min(restante, passo)
        antes = time.monotonic()
        try:
            evolution_client.enviar_presenca(
                instancia, numero, "composing", int(trecho * 1000)
            )
        except Exception as exc:  # inclui EvolutionError e falha de rede
            log.warning("Presença falhou em %s: %s", instancia, exc)
        # algumas builds só respondem depois do delay — desconta o já gasto
        # para não esperar em dobro
        espera = trecho - (time.monotonic() - antes)
        if espera > 0:
            time.sleep(espera)
        restante -= trecho

    gasto = time.monotonic() - comeco
    log.debug("Digitação simulada em %s: %.1fs (%d chars)", instancia, gasto, len(texto or ""))
    return gasto
