"""Tabela client_configuracoes — saudação e horário/ausência por cliente.

As duas colunas são JSON. O conector pode devolver JSON como str (depende da
versão/driver), então a leitura sempre normaliza antes de entregar dict.
"""
import json
import logging

from api import database

log = logging.getLogger(__name__)


def _carregar_json(valor):
    if valor is None:
        return None
    if isinstance(valor, (dict, list)):
        return valor
    if isinstance(valor, (bytes, bytearray)):
        valor = valor.decode("utf-8", "replace")
    try:
        return json.loads(valor)
    except (TypeError, ValueError):
        log.warning("JSON inválido no banco, tratado como vazio: %.80s", valor)
        return None


def por_cliente(id_cliente: int) -> dict:
    """Sempre devolve as duas chaves — ausência de linha = ambas None."""
    linha = database.consultar_um(
        "SELECT saudacao, horario FROM client_configuracoes WHERE id_cliente = %s",
        (int(id_cliente),),
    )
    if not linha:
        return {"saudacao": None, "horario": None}
    return {
        "saudacao": _carregar_json(linha.get("saudacao")),
        "horario": _carregar_json(linha.get("horario")),
    }


def _salvar(id_cliente: int, coluna: str, valor: dict | None) -> None:
    # coluna vem de chamador interno (nunca de request) — valida de todo modo
    if coluna not in ("saudacao", "horario"):
        raise ValueError(f"coluna inválida: {coluna!r}")
    bruto = json.dumps(valor, ensure_ascii=False) if valor is not None else None
    database.executar(
        f"""INSERT INTO client_configuracoes (id_cliente, {coluna})
            VALUES (%s, %s)
            ON DUPLICATE KEY UPDATE {coluna} = VALUES({coluna})""",
        (int(id_cliente), bruto),
    )


def salvar_saudacao(id_cliente: int, saudacao: dict | None) -> None:
    _salvar(id_cliente, "saudacao", saudacao)


def salvar_horario(id_cliente: int, horario: dict | None) -> None:
    _salvar(id_cliente, "horario", horario)


def remover(id_cliente: int) -> None:
    database.executar(
        "DELETE FROM client_configuracoes WHERE id_cliente = %s", (int(id_cliente),)
    )
