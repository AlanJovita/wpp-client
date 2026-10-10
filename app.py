"""wpp-client — painel do cliente + API de envio, sobre a Evolution API v2.

Porta interna 2002 (api-master 2000, wpp-premio 2001).

Projeto independente: só referencia a Evolution como motor de WhatsApp e a
api-master para validar o CNPJ no login. Não compartilha código nem rotas com o
wpp-premio — a separação em runtime é o webhook, que cada instância carrega.
"""
import logging

from flask import Flask

import log_config
from api import database, log_remoto
from config import config
from controllers.api_controller import api_v1
from controllers.auth_controller import auth
from controllers.config_controller import configuracoes
from controllers.instancia_controller import instancias
from controllers.painel_controller import painel
from controllers.status_controller import status_bp
from controllers.webhook_controller import webhook
from services import monitor_service

log = logging.getLogger(__name__)


def criar_app() -> Flask:
    log_config.configurar(config.FLASK_DEBUG)

    app = Flask(
        __name__,
        template_folder="views/templates",
        static_folder="views/static",
    )
    app.secret_key = config.APP_SECRET_KEY
    # base64 infla ~33%, mais a borda do multipart
    app.config["MAX_CONTENT_LENGTH"] = config.max_imagem_bytes * 2 + 1024 * 1024
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    # a API de envio é chamada por servidor, não pelo navegador: o cookie de
    # sessão do painel pode (e deve) exigir HTTPS em produção
    app.config["SESSION_COOKIE_SECURE"] = config.PANEL_BASE_URL.startswith("https://")
    app.config["PERMANENT_SESSION_LIFETIME"] = 60 * 60 * 12  # 12h

    for blueprint in (auth, painel, instancias, configuracoes, status_bp, webhook, api_v1):
        app.register_blueprint(blueprint)

    @app.route("/health")
    def health():
        return {"status": "ok"}, 200

    @app.route("/diagnostico")
    def diagnostico():
        """Estado operacional, sem dado de cliente — para checar de fora se a
        fila de envios e o log remoto estão sadios."""
        from services import fila_envio

        return {
            "versao": config.VERSAO,
            "envios_pendentes": fila_envio.pendentes(),
            "log_remoto": log_remoto.diagnostico(),
        }, 200

    @app.errorhandler(404)
    def nao_encontrado(_erro):
        return {"sucesso": False, "mensagem": "Rota não encontrada"}, 404

    @app.errorhandler(413)
    def grande_demais(_erro):
        return {
            "sucesso": False,
            "mensagem": f"Arquivo acima do limite de {config.MAX_IMAGEM_MB} MB.",
        }, 413

    @app.errorhandler(500)
    def erro_interno(erro):
        log.error("Erro não tratado: %s", erro, exc_info=True)
        return {"sucesso": False, "mensagem": "Erro interno"}, 500

    # o log remoto sobe ANTES do schema: se o banco falhar, é justamente esse
    # erro que precisa chegar na api-master
    log_remoto.iniciar()
    if config.LOG_REMOTO_ATIVO:
        espelho = log_remoto.ManipuladorMaster(level=logging.ERROR)
        espelho.setFormatter(logging.Formatter("%(message)s"))
        logging.getLogger().addHandler(espelho)

    try:
        database.executar_schema()
    except Exception as exc:
        # sem schema o app não serve para nada — falhe na subida, não no 1o uso
        log.critical("Não foi possível aplicar o schema: %s", exc)
        log_remoto.erro(f"schema nao aplicado na subida: {exc}", classe="app",
                        metodo="criar_app")
        raise

    monitor_service.iniciar()
    log.info("wpp-client pronto — painel em %s", config.PANEL_BASE_URL)
    return app


app = criar_app()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=2002, debug=config.FLASK_DEBUG)
