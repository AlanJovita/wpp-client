"""Tabela client_instancias — a única coisa que precisa sobreviver a um redeploy
para a instância do cliente continuar 24h online."""
import secrets

from api import database

STATUS_VALIDOS = ("criando", "conectando", "conectada", "desconectada")

_CAMPOS = (
    "id, id_cliente, cnpj, empresa, nome_instancia, numero, status, token_api, "
    "criado_em, atualizado_em"
)


def gerar_token() -> str:
    """Token da API de envio: 48 hex (24 bytes) — cabe no CHAR(48) do schema."""
    return secrets.token_hex(24)


def nome_para_cliente(id_cliente: int) -> str:
    """Prefixo cli_ reserva o namespace deste projeto na Evolution compartilhada."""
    return f"cli_{int(id_cliente)}"


def por_cliente(id_cliente: int) -> dict | None:
    return database.consultar_um(
        f"SELECT {_CAMPOS} FROM client_instancias WHERE id_cliente = %s",
        (int(id_cliente),),
    )


def por_nome(nome_instancia: str) -> dict | None:
    return database.consultar_um(
        f"SELECT {_CAMPOS} FROM client_instancias WHERE nome_instancia = %s",
        (nome_instancia,),
    )


def por_token(token: str) -> dict | None:
    return database.consultar_um(
        f"SELECT {_CAMPOS} FROM client_instancias WHERE token_api = %s", (token,)
    )


def todas() -> list[dict]:
    return database.consultar(
        f"SELECT {_CAMPOS} FROM client_instancias ORDER BY id_cliente"
    )


def criar(id_cliente: int, cnpj: str, empresa: str, nome_instancia: str) -> dict:
    """Insere (ou reaproveita) a linha da instância e devolve o registro."""
    database.executar(
        """INSERT INTO client_instancias
               (id_cliente, cnpj, empresa, nome_instancia, status, token_api)
           VALUES (%s, %s, %s, %s, 'conectando', %s)
           ON DUPLICATE KEY UPDATE
               cnpj = VALUES(cnpj),
               empresa = VALUES(empresa),
               nome_instancia = VALUES(nome_instancia),
               status = 'conectando'""",
        (int(id_cliente), cnpj, empresa, nome_instancia, gerar_token()),
    )
    return por_cliente(id_cliente)


def atualizar_status(nome_instancia: str, status: str) -> None:
    if status not in STATUS_VALIDOS:
        raise ValueError(f"status inválido: {status!r}")
    database.executar(
        "UPDATE client_instancias SET status = %s WHERE nome_instancia = %s",
        (status, nome_instancia),
    )


def atualizar_numero(nome_instancia: str, numero: str | None) -> None:
    database.executar(
        "UPDATE client_instancias SET numero = %s WHERE nome_instancia = %s",
        (numero, nome_instancia),
    )


def regerar_token(id_cliente: int) -> str:
    token = gerar_token()
    database.executar(
        "UPDATE client_instancias SET token_api = %s WHERE id_cliente = %s",
        (token, int(id_cliente)),
    )
    return token


def remover(id_cliente: int) -> None:
    database.executar(
        "DELETE FROM client_instancias WHERE id_cliente = %s", (int(id_cliente),)
    )
