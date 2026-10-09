"""Acesso ao MariaDB — única camada que toca o banco.

Pool enxuto (DB_POOL_SIZE, padrão 3) porque o app faz pouquíssimas consultas:
login, leitura/escrita de configuração e status de instância. Mensagens de
cliente não passam por aqui.

Diferente dos helpers do api-master, estas funções NÃO engolem exceção: quem
chama decide o que fazer com a falha. Toda query é parametrizada (%s).
"""
import logging
import os
import re
from contextlib import contextmanager

import mysql.connector
from mysql.connector import pooling

from config import config

log = logging.getLogger(__name__)

_pool: pooling.MySQLConnectionPool | None = None


def _obter_pool() -> pooling.MySQLConnectionPool:
    global _pool
    if _pool is None:
        _pool = pooling.MySQLConnectionPool(
            pool_name="wpp_client",
            pool_size=max(1, config.DB_POOL_SIZE),
            pool_reset_session=True,
            host=config.DB_HOST,
            port=config.DB_PORT,
            user=config.DB_USER,
            password=config.DB_PASSWORD,
            database=config.DB_NAME,
            charset="utf8mb4",
            collation="utf8mb4_unicode_ci",
            autocommit=True,
            connection_timeout=10,
        )
    return _pool


@contextmanager
def cursor(dicionario: bool = True):
    """Conexão do pool + cursor, devolvidos ao pool no fim.

    O ping reativa conexões que o MariaDB derrubou por wait_timeout — comum em
    app de baixo tráfego como este, que pode ficar horas sem consultar nada.
    """
    conexao = _obter_pool().get_connection()
    try:
        conexao.ping(reconnect=True, attempts=2, delay=1)
        cur = conexao.cursor(dictionary=dicionario)
        try:
            yield cur
        finally:
            cur.close()
    finally:
        conexao.close()  # devolve ao pool, não fecha o socket


def consultar_um(sql: str, parametros: tuple = ()) -> dict | None:
    with cursor() as cur:
        cur.execute(sql, parametros)
        return cur.fetchone()


def consultar(sql: str, parametros: tuple = ()) -> list[dict]:
    with cursor() as cur:
        cur.execute(sql, parametros)
        return cur.fetchall() or []


def executar(sql: str, parametros: tuple = ()) -> int:
    """Executa INSERT/UPDATE/DELETE. Retorna lastrowid (insert) ou rowcount."""
    with cursor(dicionario=False) as cur:
        cur.execute(sql, parametros)
        return cur.lastrowid or cur.rowcount


# ------------------------------------------------------------------- schema

_COMENTARIO = re.compile(r"--[^\n]*")


def executar_schema() -> None:
    """Aplica schema.sql (CREATE TABLE IF NOT EXISTS) na subida.

    Comentários são removidos antes de dividir por ';' — assim o DDL pode conter
    ponto e vírgula em comentário sem partir um comando ao meio.
    """
    caminho = os.path.join(os.path.dirname(os.path.dirname(__file__)), "schema.sql")
    with open(caminho, encoding="utf-8") as arquivo:
        bruto = arquivo.read()

    comandos = [c.strip() for c in _COMENTARIO.sub("", bruto).split(";")]
    with cursor(dicionario=False) as cur:
        for comando in comandos:
            if not comando:
                continue
            try:
                cur.execute(comando)
            except mysql.connector.Error as exc:
                log.error("Falha no schema: %s | comando: %.120s", exc, comando)
                raise
    log.info("Schema aplicado (tabelas client_*)")
