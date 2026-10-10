"""Testes do wpp-client — lógica pura, rotas e webhook, sem MariaDB e sem Evolution.

    python scripts/testes.py

Não precisa de banco nem de instância: o schema é um no-op e os acessos a
MariaDB/Evolution são substituídos por stubs. Sai com código 1 na primeira
falha acumulada, para servir de gate em CI.
"""
import os
import sys
import time

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJ)
os.chdir(PROJ)

# As mensagens padrão têm emoji; sem isto o console cp1252 (Git Bash, cmd, e
# alguns runners de CI) quebra com UnicodeEncodeError em vez de reportar o teste
for _fluxo in (sys.stdout, sys.stderr):
    try:
        _fluxo.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

os.environ.update({
    "MONITOR_ATIVO": "0",
    "APP_SECRET_KEY": "teste",
    "WEBHOOK_SECRET": "segredo-webhook",
    "PANEL_BASE_URL": "http://localhost:2002",
    "EVOLUTION_URL": "http://evolution.invalido",
    "EVOLUTION_API_KEY": "k",
    "DIGITACAO_EXTRA_MIN_S": "0",
    "DIGITACAO_EXTRA_MAX_S": "0",
    "SIMULAR_DIGITACAO": "0",
})

falhas = []


def checar(condicao, rotulo):
    print(("  ok   " if condicao else "  FALHA") + " " + rotulo)
    if not condicao:
        falhas.append(rotulo)


# ------------------------------------------------------------ lógica pura
from services import horario_service, messaging_service, saudacao_service, typing_service  # noqa: E402
from config import config  # noqa: E402

print("\n[typing_service]")
# com extra zerado, sobra só o proporcional entre MIN e MAX
checar(abs(typing_service.calcular_segundos("") - config.DIGITACAO_MIN_S) < 0.01,
       "texto vazio cai no piso (%.1fs)" % config.DIGITACAO_MIN_S)
checar(abs(typing_service.calcular_segundos("a" * 120) - 10.0) < 0.01,
       "120 chars / 12 cps = 10s")
checar(abs(typing_service.calcular_segundos("a" * 10000) - config.DIGITACAO_MAX_S) < 0.01,
       "texto enorme respeita o teto (%.0fs)" % config.DIGITACAO_MAX_S)

# agora com o adicional randômico real de 3 a 12s
config.DIGITACAO_EXTRA_MIN_S, config.DIGITACAO_EXTRA_MAX_S = 3.0, 12.0
amostras = [typing_service.calcular_segundos("a" * 120) for _ in range(300)]
checar(all(13.0 <= s <= 22.0 for s in amostras), "120 chars fica entre 13s e 22s")
checar(max(amostras) - min(amostras) > 5, "o adicional varia de verdade")

print("\n[messaging_service.normalizar_numero]")
casos = [
    ("5544999998888", "5544999998888"),
    ("44 99999-8888", "5544999998888"),
    ("(44) 9999-8888", "554499998888"),
    ("+55 44 99999-8888", "5544999998888"),
    ("999", None),
    ("", None),
    ("5544999998888999", None),
]
for entrada, esperado in casos:
    obtido = messaging_service.normalizar_numero(entrada)
    checar(obtido == esperado, f"{entrada!r} -> {obtido!r} (esperado {esperado!r})")

print("\n[horario_service]")
valido, erro = horario_service.validar({
    "ativo": True, "dias": [0, 1, 2, 3, 4], "inicio": "08:00", "fim": "18:00",
    "intervalo_inicio": "12:00", "intervalo_fim": "13:30", "msg_fora": "",
})
checar(erro is None and valido["dias"] == [0, 1, 2, 3, 4], "horário completo valida")

_, erro = horario_service.validar({"dias": [], "inicio": "08:00", "fim": "18:00"})
checar(erro is not None, "sem dias é recusado")
_, erro = horario_service.validar({"dias": [0], "inicio": "18:00", "fim": "08:00"})
checar(erro is not None, "fim antes do início é recusado")
_, erro = horario_service.validar({"dias": [0], "inicio": "08:00", "fim": "18:00",
                                   "intervalo_inicio": "12:00"})
checar(erro is not None, "intervalo pela metade é recusado")
_, erro = horario_service.validar({"dias": [0], "inicio": "8:00", "fim": "18:00"})
checar(erro is not None, "hora sem zero à esquerda é recusada")

from datetime import datetime  # noqa: E402
from zoneinfo import ZoneInfo  # noqa: E402

tz = ZoneInfo(config.ATENDIMENTO_TZ)
seg10h = datetime(2026, 10, 5, 10, 0, tzinfo=tz)   # segunda
seg12h30 = datetime(2026, 10, 5, 12, 30, tzinfo=tz)
sab10h = datetime(2026, 10, 10, 10, 0, tzinfo=tz)  # sábado
checar(horario_service.esta_aberto(valido, seg10h), "segunda 10h = aberto")
checar(not horario_service.esta_aberto(valido, seg12h30), "segunda 12h30 (intervalo) = fechado")
checar(not horario_service.esta_aberto(valido, sab10h), "sábado = fechado")
checar(horario_service.esta_aberto(None), "sem config = sempre aberto")
checar(horario_service.esta_aberto({"ativo": False, "dias": [0]}, sab10h),
       "controle desligado = sempre aberto")

padrao = horario_service.mensagem_fora(valido)
checar("de seg a sex" in padrao and "08:00" in padrao and "12:00" in padrao,
       "mensagem padrão descreve dias, expediente e intervalo")
checar(horario_service.mensagem_fora({"msg_fora": "Volto amanhã"}) == "Volto amanhã",
       "mensagem personalizada tem precedência")

print("\n[saudacao_service]")
cfg, erro = saudacao_service.validar({"ativo": True, "mensagem": "Oi!", "janela_horas": 6})
checar(erro is None and cfg["janela_horas"] == 6, "saudação válida")
_, erro = saudacao_service.validar({"ativo": True, "mensagem": "  "})
checar(erro is not None, "ativar sem mensagem é recusado")
_, erro = saudacao_service.validar({"ativo": True, "mensagem": "Oi", "janela_horas": 0})
checar(erro is not None, "janela 0 é recusada")

conf_aberto = {"saudacao": {"ativo": True, "mensagem": "Olá!", "janela_horas": 12},
               "horario": valido}
# dentro do horário: saudação, uma vez só
texto1, motivo1 = saudacao_service.preparar("cli_1", "5544999998888", conf_aberto)
texto2, motivo2 = saudacao_service.preparar("cli_1", "5544999998888", conf_aberto)
esperado = "Olá!" if horario_service.esta_aberto(valido) else horario_service.mensagem_fora(valido)
checar(texto1 == esperado and motivo1 is None, f"1a mensagem responde ({texto1!r})")
checar(texto2 is None, "2a mensagem do mesmo número na janela não responde")
checar(motivo2 == "janela_de_repeticao", f"e diz o motivo ({motivo2!r})")
checar(saudacao_service.preparar("cli_1", "5544777776666", conf_aberto)[0] is not None,
       "outro número responde normalmente")

saudacao_service.esquecer_instancia("cli_1")
checar(saudacao_service.preparar("cli_1", "5544999998888", conf_aberto)[0] is not None,
       "esquecer_instancia libera a janela")

# fora do horário manda SÓ a ausência, nunca a saudação
conf_fechado = {"saudacao": {"ativo": True, "mensagem": "Olá!", "janela_horas": 12},
                "horario": {"ativo": True, "dias": [0], "inicio": "08:00", "fim": "09:00",
                            "intervalo_inicio": None, "intervalo_fim": None,
                            "msg_fora": "Estamos fechados."}}
fora = saudacao_service.texto_para_responder(conf_fechado)[0]
checar(fora == "Estamos fechados." or horario_service.esta_aberto(conf_fechado["horario"]),
       "fora do horário responde a ausência, não a saudação")

# saudação desligada e dentro do horário = silêncio, COM motivo
conf_mudo = {"saudacao": {"ativo": False, "mensagem": "Olá!"}, "horario": None}
mudo_texto, _, mudo_motivo = saudacao_service.texto_para_responder(conf_mudo)
checar(mudo_texto is None, "saudação desligada e aberto = não responde")
checar(mudo_motivo == "saudacao_desligada", f"e diz o motivo ({mudo_motivo!r})")

print("\n[status_service]")
from services import status_service  # noqa: E402


class ArquivoFalso:
    def __init__(self, nome, tipo, dados):
        self.filename, self.mimetype, self._dados = nome, tipo, dados

    def read(self, n=-1):
        return self._dados[:n] if n and n > 0 else self._dados


dados, erro = status_service.validar_imagem(ArquivoFalso("a.jpg", "image/jpeg", b"x" * 100))
checar(erro is None and dados == b"x" * 100, "JPG pequeno aceito")
_, erro = status_service.validar_imagem(ArquivoFalso("a.gif", "image/gif", b"x"))
checar(erro is not None, "GIF recusado")
_, erro = status_service.validar_imagem(None)
checar(erro is not None, "sem arquivo recusado")
_, erro = status_service.validar_imagem(
    ArquivoFalso("g.png", "image/png", b"x" * (config.max_imagem_bytes + 10)))
checar(erro is not None and "MB" in erro, "imagem acima do limite recusada")

# --------------------------------------------------------------- rotas
print("\n[app / rotas]")
from api import database  # noqa: E402

database.executar_schema = lambda: None  # sem MariaDB no teste

from models import instancia as instancia_model  # noqa: E402

TOKEN = "t" * 48
REGISTRO = {
    "id": 1, "id_cliente": 42, "cnpj": "00000000000000", "empresa": "TESTE LTDA",
    "nome_instancia": "cli_42", "numero": "5544999998888", "status": "conectada",
    "token_api": TOKEN, "criado_em": None, "atualizado_em": None,
}
instancia_model.por_token = lambda t: REGISTRO if t == TOKEN else None
instancia_model.por_nome = lambda n: REGISTRO if n == "cli_42" else None

import app as app_module  # noqa: E402

cliente_http = app_module.app.test_client()

r = cliente_http.get("/health")
checar(r.status_code == 200 and r.get_json()["status"] == "ok", "GET /health = 200")

r = cliente_http.get("/login")
checar(r.status_code == 200 and "CNPJ" in r.get_data(as_text=True), "GET /login renderiza")

r = cliente_http.get("/")
checar(r.status_code == 302 and "/login" in r.headers["Location"],
       "GET / sem sessão redireciona para /login")

r = cliente_http.get("/api/instancia")
checar(r.status_code == 401, "GET /api/instancia sem sessão = 401 JSON")

# ---- API pública
r = cliente_http.post("/api/v1/mensagem", json={"numero": "5544999998888", "mensagem": "oi"})
checar(r.status_code == 401 and r.get_json()["erro"] == "token_ausente", "sem token = 401")

r = cliente_http.post("/api/v1/mensagem", json={"numero": "5544999998888", "mensagem": "oi"},
                      headers={"Authorization": "Bearer errado"})
checar(r.status_code == 401 and r.get_json()["erro"] == "token_invalido", "token errado = 401")

AUTH = {"Authorization": f"Bearer {TOKEN}"}

r = cliente_http.get("/api/v1/conta", headers=AUTH)
checar(r.status_code == 200 and r.get_json()["dados"]["empresa"] == "TESTE LTDA",
       "GET /api/v1/conta com token = 200")

r = cliente_http.post("/api/v1/mensagem", json={"numero": "123", "mensagem": "oi"}, headers=AUTH)
checar(r.status_code == 400 and r.get_json()["erro"] == "numero_invalido", "número inválido = 400")

r = cliente_http.post("/api/v1/mensagem", json={"numero": "5544999998888"}, headers=AUTH)
checar(r.status_code == 400 and r.get_json()["erro"] == "mensagem_vazia", "sem mensagem = 400")

r = cliente_http.post("/api/v1/mensagem",
                      json={"numero": "5544999998888", "mensagem": "a" * 5000}, headers=AUTH)
checar(r.status_code == 400 and r.get_json()["erro"] == "mensagem_longa", "mensagem longa = 400")

r = cliente_http.post("/api/v1/imagem", json={"numero": "5544999998888"}, headers=AUTH)
checar(r.status_code == 400 and r.get_json()["erro"] == "imagem_ausente", "sem imagem = 400")

r = cliente_http.post("/api/v1/imagem",
                      json={"numero": "5544999998888", "imagem": "nao!!!base64"}, headers=AUTH)
checar(r.status_code == 400 and r.get_json()["erro"] == "imagem_invalida", "base64 inválido = 400")

# envio de verdade, com a Evolution trocada por um stub
enviados = []
messaging_service.enviar_texto = lambda inst, num, txt, typing=True: (
    enviados.append((inst, num, txt, typing)) or {"sucesso": True, "id_whatsapp": "WA1"})

r = cliente_http.post("/api/v1/mensagem",
                      json={"numero": "44 99999-8888", "mensagem": "Pedido saiu!"},
                      headers=AUTH)
corpo = r.get_json()
checar(r.status_code == 202 and corpo["dados"]["status"] == "na_fila",
       "envio aceito com 202 + id_envio")
id_envio = corpo["dados"]["id_envio"]

for _ in range(50):
    if enviados:
        break
    time.sleep(0.05)
checar(enviados == [("cli_42", "5544999998888", "Pedido saiu!", True)],
       f"fila executou o envio normalizando o número: {enviados}")

r = cliente_http.get(f"/api/v1/envio/{id_envio}", headers=AUTH)
checar(r.status_code == 200 and r.get_json()["dados"]["status"] == "enviada",
       "consulta devolve status 'enviada'")
checar(r.get_json()["dados"]["id_whatsapp"] == "WA1", "consulta devolve o id do WhatsApp")

r = cliente_http.get(f"/api/v1/mensagem/{id_envio}", headers=AUTH)
checar(r.status_code == 200, "alias /api/v1/mensagem/<id> funciona")

r = cliente_http.get("/api/v1/envio/inexistente", headers=AUTH)
checar(r.status_code == 404, "envio desconhecido = 404")

# instância desconectada bloqueia a API
REGISTRO["status"] = "desconectada"
r = cliente_http.post("/api/v1/mensagem", json={"numero": "5544999998888", "mensagem": "oi"},
                      headers=AUTH)
checar(r.status_code == 409 and r.get_json()["erro"] == "instancia_desconectada",
       "instância desconectada = 409")
REGISTRO["status"] = "conectada"

# ---- webhook
print("\n[webhook]")
r = cliente_http.post("/webhook/cli_42", json={"event": "connection.update", "data": {}})
checar(r.status_code == 401, "webhook sem token = 401")

WH = {"x-webhook-token": "segredo-webhook"}
r = cliente_http.post("/webhook/cli_desconhecida",
                      json={"event": "connection.update", "data": {"state": "open"}}, headers=WH)
checar(r.status_code == 200, "webhook de instância desconhecida = 200 (não desabilita)")

from services import instance_service  # noqa: E402

r = cliente_http.post("/webhook/cli_42", json={
    "event": "qrcode.updated", "data": {"qrcode": {"base64": "QRBASE64"}}}, headers=WH)
checar(r.status_code == 200 and instance_service.obter_qr("cli_42") == "QRBASE64",
       "QRCODE_UPDATED guarda o QR em memória")

# mensagem de grupo / fromMe / própria: nada é enfileirado
from models import configuracao as configuracao_model  # noqa: E402

configuracao_model.por_cliente = lambda _id: {
    "saudacao": {"ativo": True, "mensagem": "Bem-vindo!", "janela_horas": 12},
    "horario": None,
}
enviados.clear()
saudacao_service.esquecer_instancia("cli_42")

for rotulo, dados_wh in [
    ("grupo", {"key": {"remoteJid": "123@g.us", "fromMe": False}}),
    ("fromMe", {"key": {"remoteJid": "5544999998888@s.whatsapp.net", "fromMe": True}}),
    ("broadcast", {"key": {"remoteJid": "status@broadcast", "fromMe": False}}),
]:
    cliente_http.post("/webhook/cli_42",
                      json={"event": "messages.upsert", "data": dados_wh}, headers=WH)
checar(enviados == [], "grupo, fromMe e broadcast não geram saudação")

# mensagem direta: saudação uma vez só
for _ in range(3):
    cliente_http.post("/webhook/cli_42", json={
        "event": "MESSAGES_UPSERT",
        "data": {"key": {"remoteJid": "5544777776666@s.whatsapp.net", "fromMe": False},
                 "message": {"conversation": "oi"}}}, headers=WH)

for _ in range(50):
    if enviados:
        break
    time.sleep(0.05)
checar(len(enviados) == 1 and enviados[0][2] == "Bem-vindo!",
       f"3 mensagens seguidas = 1 saudação ({len(enviados)} envio(s))")

# ---- painel (renderização do Jinja: erro de template só aparece em runtime)
print("\n[painel]")


def logar():
    with cliente_http.session_transaction() as sessao:
        sessao["id_cliente"] = 42
        sessao["cnpj"] = "00000000000000"
        sessao["empresa"] = "TESTE LTDA"


logar()

# 1) sem instância criada
instancia_model.por_cliente = lambda _id: None
r = cliente_http.get("/")
html = r.get_data(as_text=True)
checar(r.status_code == 200, "painel sem instância = 200")
checar("Criar instância" in html, "mostra o botão 'Criar instância'")
checar('id="cartao-saudacao" class' not in html and 'class="cartao oculto" id="cartao-saudacao"' in html,
       "configurações ficam ocultas sem instância")

# 2) instância conectada
instancia_model.por_cliente = lambda _id: REGISTRO
configuracao_model.por_cliente = lambda _id: {
    "saudacao": {"ativo": True, "mensagem": "Bem-vindo!", "janela_horas": 8},
    "horario": valido,
}
r = cliente_http.get("/")
html = r.get_data(as_text=True)
checar(r.status_code == 200, "painel com instância conectada = 200")
checar("Criar instância" not in html, "não oferece criar instância de novo")
checar("Bem-vindo!" in html, "saudação salva aparece preenchida")
checar("TESTE LTDA" in html, "empresa aparece no topo")
checar('value="08:00"' in html and 'value="12:00"' in html,
       "expediente e intervalo aparecem preenchidos")
checar(html.count('class="cartao oculto"') == 0,
       "com a instância conectada nenhuma seção fica oculta")
checar("/api/v1/mensagem" in html, "exemplo da API de envio aparece")
checar(REGISTRO["token_api"] not in html,
       "o token NÃO vai no HTML (só sob pedido em /api/instancia/token)")

# 3) instância criada mas desconectada: configurações ocultas
REGISTRO["status"] = "desconectada"
r = cliente_http.get("/")
html = r.get_data(as_text=True)
checar('class="cartao oculto" id="cartao-status"' in html,
       "instância desconectada mantém as configurações ocultas")
REGISTRO["status"] = "conectada"

# 4) token sob pedido
r = cliente_http.get("/api/instancia/token")
checar(r.status_code == 200 and r.get_json()["dados"]["token"] == REGISTRO["token_api"],
       "GET /api/instancia/token devolve o token com sessão")

# 5) remover exige confirmação explícita
r = cliente_http.delete("/api/instancia")
checar(r.status_code == 400, "DELETE /api/instancia sem confirmar=sim = 400")

# ---- log remoto na api-master
print("\n[log remoto]")
from api import log_remoto  # noqa: E402

enviados_log = []
log_remoto._enviar = lambda carga: (enviados_log.append(carga) or True)
log_remoto.iniciar()


def drenar(esperado=1, limite=60):
    for _ in range(limite):
        if len(enviados_log) >= esperado and log_remoto._fila.qsize() == 0:
            break
        time.sleep(0.05)


# O app real já subiu antes neste teste e instalou o ManipuladorMaster, então a
# fila carrega os erros legítimos dos casos anteriores. Drena e zera para medir
# só o que este bloco produz.
time.sleep(0.4)
enviados_log.clear()

log_remoto.erro("banco fora do ar", id_cliente=42, classe="database", metodo="pool",
                linha=28)
drenar()
checar(len(enviados_log) == 1, f"erro enfileirado e enviado ({len(enviados_log)})")
if enviados_log:
    c = enviados_log[0]
    checar(c["id_software"] == 21, f"id_software = 21 (obtido {c['id_software']})")
    checar(c["id_cliente"] == 42, "id_cliente vai no payload")
    checar(c["descricao"] == "banco fora do ar", "descricao preservada")
    checar(c["classe"] == "database" and c["metodo"] == "pool" and c["linha"] == 28,
           "classe, metodo e linha preenchidos")
    checar(c["versao"] == config.VERSAO, "versao do app no payload")
    checar(c["qtd"] == 1, "primeira ocorrência tem qtd=1")
    checar(set(c) >= {"id_cliente", "id_software", "descricao", "versao", "classe",
                      "metodo", "linha", "qtd", "status", "classificacao", "data"},
           "payload tem todos os campos que a api-master consome")

# deduplicação: a mesma ocorrência repetida não vira N requisições
enviados_log.clear()
for _ in range(5):
    log_remoto.erro("erro repetido", id_cliente=42, classe="x", metodo="y")
drenar()
checar(len(enviados_log) == 1, f"5 ocorrências iguais = 1 envio ({len(enviados_log)})")

# motivo de não envio
enviados_log.clear()
log_remoto.nao_enviada("janela_de_repeticao", id_cliente=42, instancia="cli_42",
                       numero_mascarado="5544…8888")
drenar()
checar(len(enviados_log) == 1, "motivo de não envio é registrado")
if enviados_log:
    d = enviados_log[0]["descricao"]
    checar("mensagem nao enviada: janela_de_repeticao" in d, f"descricao do motivo ({d!r})")
    checar("cli_42" in d and "5544…8888" in d, "instância e número mascarado no texto")
    checar(enviados_log[0]["classificacao"] == 1,
           "não-envio usa classificacao=1 (distingue de erro técnico)")

# o handler do logging padrão espelha log.error automaticamente
enviados_log.clear()
import logging as _logging  # noqa: E402

_h = log_remoto.ManipuladorMaster(level=_logging.ERROR)
_h.setFormatter(_logging.Formatter("%(message)s"))
_logging.getLogger("teste.espelho").addHandler(_h)
_logging.getLogger("teste.espelho").error("falha espelhada")
drenar()
checar(len(enviados_log) == 1, "log.error é espelhado para a api-master")

# desligado = nada sai
enviados_log.clear()
config.LOG_REMOTO_ATIVO = False
log_remoto.erro("nao deve sair", id_cliente=1, classe="z", metodo="z")
drenar(esperado=0, limite=10)
checar(len(enviados_log) == 0, "LOG_REMOTO_ATIVO=0 não envia nada")
config.LOG_REMOTO_ATIVO = True

# api-master fora do ar não pode derrubar nem travar o app
enviados_log.clear()
log_remoto._enviar = lambda carga: (_ for _ in ()).throw(RuntimeError("api fora"))
log_remoto.erro("com api fora", id_cliente=7, classe="a", metodo="b")
time.sleep(0.4)
checar(log_remoto._thread.is_alive(), "thread do log sobrevive a falha do envio")

print("\n" + "=" * 60)
if falhas:
    print(f"{len(falhas)} FALHA(S):")
    for f in falhas:
        print("  - " + f)
    sys.exit(1)
print("Todos os testes passaram.")
