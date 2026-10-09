"""Horário de funcionamento + mensagem de ausência.

Formato do JSON (idêntico ao do wpp-premio, services/horario_service):
  {"ativo": bool, "dias": [0..6] (0 = segunda), "inicio": "HH:MM",
   "intervalo_inicio": "HH:MM" | None, "intervalo_fim": "HH:MM" | None,
   "fim": "HH:MM", "msg_fora": str}

NULL / ativo=False = sem controle → sempre dentro do horário.
Comparações no fuso config.ATENDIMENTO_TZ.
"""
import logging
import re
from datetime import datetime
from zoneinfo import ZoneInfo

from config import config

log = logging.getLogger(__name__)

HORA_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
DIAS_NOMES = ["seg", "ter", "qua", "qui", "sex", "sáb", "dom"]

MSG_FORA_PADRAO = (
    "Estamos fora do nosso horário de atendimento. 🕗 Atendemos {dias}, "
    "das {inicio} às {fim}{intervalo}. Deixe sua mensagem e retornaremos "
    "assim que possível. 🙂"
)

LIMITE_MSG = 1000


# ---------------------------------------------------------------- validação

def validar(dados) -> tuple[dict | None, str | None]:
    """Normaliza e valida o payload do painel; retorna (horario, erro)."""
    if not isinstance(dados, dict):
        return None, "formato de horário inválido"

    dias = dados.get("dias")
    if (
        not isinstance(dias, list)
        or not dias
        or not all(isinstance(d, int) and 0 <= d <= 6 for d in dias)
    ):
        return None, "selecione ao menos um dia de funcionamento"

    inicio, fim = dados.get("inicio"), dados.get("fim")
    for rotulo, valor in (("início", inicio), ("fim", fim)):
        if not isinstance(valor, str) or not HORA_RE.match(valor):
            return None, f"horário de {rotulo} inválido (use HH:MM)"

    # HH:MM zero-padded compara certo como string ("08:00" < "12:30")
    ii = dados.get("intervalo_inicio") or None
    ifim = dados.get("intervalo_fim") or None
    if (ii is None) != (ifim is None):
        return None, "preencha o início E o fim do intervalo (ou deixe os dois em branco)"
    if ii is not None:
        for rotulo, valor in (("início do intervalo", ii), ("fim do intervalo", ifim)):
            if not isinstance(valor, str) or not HORA_RE.match(valor):
                return None, f"horário de {rotulo} inválido (use HH:MM)"
        if not (inicio < ii < ifim < fim):
            return None, "horários fora de ordem (início < intervalo < fim do expediente)"
    elif not inicio < fim:
        return None, "o fim do expediente deve ser depois do início"

    msg_fora = str(dados.get("msg_fora") or "").strip()
    if len(msg_fora) > LIMITE_MSG:
        return None, f"mensagem de ausência acima de {LIMITE_MSG} caracteres"

    return {
        "ativo": bool(dados.get("ativo", True)),
        "dias": sorted(set(dias)),
        "inicio": inicio,
        "intervalo_inicio": ii,
        "intervalo_fim": ifim,
        "fim": fim,
        "msg_fora": msg_fora,
    }, None


# ------------------------------------------------------------------ consulta

def agora_local() -> datetime:
    try:
        return datetime.now(ZoneInfo(config.ATENDIMENTO_TZ))
    except Exception:
        log.warning(
            "Fuso inválido em ATENDIMENTO_TZ (%r) — usando UTC", config.ATENDIMENTO_TZ
        )
        return datetime.now(ZoneInfo("UTC"))


def esta_aberto(horario: dict | None, agora: datetime | None = None) -> bool:
    """True se está dentro do horário de funcionamento (ou sem controle)."""
    if not horario or not horario.get("ativo"):
        return True
    momento = agora or agora_local()
    if momento.weekday() not in (horario.get("dias") or []):
        return False
    hora = momento.strftime("%H:%M")
    ii, ifim = horario.get("intervalo_inicio"), horario.get("intervalo_fim")
    if ii and ifim and ii <= hora < ifim:
        return False
    return horario["inicio"] <= hora < horario["fim"]


def mensagem_fora(horario: dict | None) -> str:
    """A mensagem de ausência personalizada, ou a padrão montada com os horários."""
    horario = horario or {}
    personalizada = (horario.get("msg_fora") or "").strip()
    if personalizada:
        return personalizada
    intervalo = ""
    if horario.get("intervalo_inicio") and horario.get("intervalo_fim"):
        intervalo = (
            f", com intervalo das {horario['intervalo_inicio']}"
            f" às {horario['intervalo_fim']}"
        )
    return MSG_FORA_PADRAO.format(
        dias=_dias_texto(horario.get("dias") or []),
        inicio=horario.get("inicio", ""),
        fim=horario.get("fim", ""),
        intervalo=intervalo,
    )


def _dias_texto(dias: list[int]) -> str:
    """[0,1,2,3,4] → 'de seg a sex'; não contíguos → 'seg, qua e sex'."""
    dias = sorted(set(dias))
    if len(dias) >= 2 and dias == list(range(dias[0], dias[-1] + 1)):
        return f"de {DIAS_NOMES[dias[0]]} a {DIAS_NOMES[dias[-1]]}"
    nomes = [DIAS_NOMES[d] for d in dias]
    if len(nomes) == 1:
        return nomes[0]
    return ", ".join(nomes[:-1]) + " e " + nomes[-1]
