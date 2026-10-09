"""Publicação de status no WhatsApp — imagem + legenda, no ato.

Síncrono de propósito: o painel precisa dizer na hora se saiu ou não. Não há
digitação simulada aqui — status não é conversa, ninguém "digita" um status.

A imagem nunca toca o disco: vem no multipart, vira base64 em memória e segue
para a Evolution. Nada é armazenado deste lado.
"""
import base64
import logging

from api import evolution_client
from config import config

log = logging.getLogger(__name__)

TIPOS_ACEITOS = {
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
}

LIMITE_LEGENDA = 700


def validar_imagem(arquivo) -> tuple[bytes | None, str | None]:
    """Lê o upload respeitando MAX_IMAGEM_MB. Retorna (bytes, erro)."""
    if arquivo is None or not arquivo.filename:
        return None, "selecione uma imagem"

    tipo = (arquivo.mimetype or "").lower()
    if tipo not in TIPOS_ACEITOS:
        return None, "formato não aceito (use JPG, PNG ou WebP)"

    limite = config.max_imagem_bytes
    # lê 1 byte além do limite para detectar estouro sem carregar o arquivo todo
    dados = arquivo.read(limite + 1)
    if not dados:
        return None, "arquivo vazio"
    if len(dados) > limite:
        return None, f"imagem acima de {config.MAX_IMAGEM_MB} MB"
    return dados, None


def validar_legenda(legenda: str) -> tuple[str, str | None]:
    legenda = (legenda or "").strip()
    if len(legenda) > LIMITE_LEGENDA:
        return "", f"legenda acima de {LIMITE_LEGENDA} caracteres"
    return legenda, None


def publicar(instancia: str, imagem: bytes, legenda: str = "") -> dict:
    """Publica a imagem no status. Levanta EvolutionError se a Evolution recusar."""
    conteudo = base64.b64encode(imagem).decode("ascii")
    resposta = evolution_client.publicar_status(
        instancia, conteudo, legenda=legenda, tipo="image", todos_contatos=True
    )
    log.info(
        "Status publicado por %s (%d KB, legenda %d chars)",
        instancia, len(imagem) // 1024, len(legenda),
    )
    chave = resposta.get("key") if isinstance(resposta, dict) else None
    return {
        "sucesso": True,
        "id_whatsapp": chave.get("id") if isinstance(chave, dict) else None,
    }
