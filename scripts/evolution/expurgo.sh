#!/usr/bin/env bash
#
# expurgo.sh — retenção diferenciada de mensagens no Postgres da Evolution API.
#
# Política (padrão):
#   instâncias internas (suporte, financeiro) → 30 dias
#   todas as outras, inclusive as cli_* do wpp-client → 1 dia
#
# RODA NO HOST DA VPS, não dentro do container do wpp-client. Ele limpa dados da
# Evolution, que é um serviço compartilhado — a rotina pertence a ela, não ao app
# que a consome. Ver README.md desta pasta para a instalação.
#
# Desenho:
#   - apaga só de "Message": "MessageUpdate" e "Media" saem por ON DELETE CASCADE
#     (confirmado no schema), e "MessageUpdate" nem tem coluna de data para filtrar
#   - em lotes, com lock_timeout e statement_timeout, para nunca segurar as
#     escritas da Evolution (que estão no caminho do webhook)
#   - DRY-RUN POR PADRÃO: só apaga com --aplicar
#   - trava de volume: aborta se o expurgo for remover mais que MAX_PCT% da
#     tabela, salvo --forcar. O primeiro disparo remove ~86%, intencionalmente
#   - nunca faz VACUUM FULL. Ele toma ACCESS EXCLUSIVE (derruba a Evolution),
#     duplica a tabela em disco e dá pico de memória — num host sem swap é
#     convite ao OOM killer. O autovacuum recicla o espaço internamente e a
#     tabela estabiliza; o espaço não volta ao filesystem, e isso está certo.
#
# A senha do Postgres nunca passa pela linha de comando do host: o psql roda
# dentro do container e lê $POSTGRES_PASSWORD do ambiente dele.

set -euo pipefail

# ------------------------------------------------------------- parâmetros

# Nome do container do Postgres da Evolution. O padrão vem do host em
# DATABASE_CONNECTION_URI da Evolution. CONFIRME com `docker ps` antes do
# primeiro uso — o script valida e falha com mensagem clara se não existir.
PG_CONTAINER="${PG_CONTAINER:-hy3d3h30dj6552bd9hdkp7ik}"

# Política de retenção. Note o "-" em vez de ":-": só a variável AUSENTE cai no
# padrão. Definida e vazia é erro, não padrão silencioso — num parâmetro que
# decide o que é apagado, o operador não pode pedir uma coisa e receber outra.
RETENCAO_INTERNA_DIAS="${RETENCAO_INTERNA_DIAS-30}"
RETENCAO_CLIENTE_DIAS="${RETENCAO_CLIENTE_DIAS-1}"
INSTANCIAS_INTERNAS="${INSTANCIAS_INTERNAS-suporte,financeiro}"

LOTE="${LOTE:-5000}"
PAUSA_S="${PAUSA_S:-1}"
MAX_PCT="${MAX_PCT:-50}"
LOG="${LOG:-/var/log/expurgo-evolution.log}"

APLICAR=0
FORCAR=0

ajuda() {
    cat <<'FIM'
expurgo.sh — retenção diferenciada de mensagens na Evolution API

  --aplicar   executa os DELETE (sem esta opção é só relatório)
  --forcar    ignora a trava de volume (MAX_PCT)
  --ajuda     esta mensagem

Variáveis de ambiente:
  PG_CONTAINER            container do Postgres da Evolution
  RETENCAO_INTERNA_DIAS   dias para as instâncias internas (padrão 30)
  RETENCAO_CLIENTE_DIAS   dias para as demais (padrão 1)
  INSTANCIAS_INTERNAS     nomes separados por vírgula (padrão suporte,financeiro)
  LOTE                    linhas por DELETE (padrão 5000)
  PAUSA_S                 pausa entre lotes (padrão 1)
  MAX_PCT                 teto de % da tabela por execução (padrão 50)
  LOG                     arquivo de log

Exemplos:
  ./expurgo.sh                        # relatório, não apaga nada
  ./expurgo.sh --aplicar              # execução normal (cron)
  ./expurgo.sh --aplicar --forcar     # primeiro disparo, que remove ~86%
FIM
}

while [ $# -gt 0 ]; do
    case "$1" in
        --aplicar) APLICAR=1 ;;
        --forcar)  FORCAR=1 ;;
        --ajuda|-h|--help) ajuda; exit 0 ;;
        *) echo "Opção desconhecida: $1" >&2; ajuda >&2; exit 2 ;;
    esac
    shift
done

# ------------------------------------------------------------------- log

if ! touch "$LOG" 2>/dev/null; then
    LOG="/tmp/expurgo-evolution.log"
    touch "$LOG"
fi

registrar() {
    printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" | tee -a "$LOG"
}

# -------------------------------------------------------------- validação

for dias in "$RETENCAO_INTERNA_DIAS" "$RETENCAO_CLIENTE_DIAS"; do
    case "$dias" in
        ''|*[!0-9]*) registrar "ERRO: retenção inválida: ${dias@Q}"; exit 2 ;;
    esac
    [ "$dias" -ge 1 ] || { registrar "ERRO: retenção mínima é 1 dia"; exit 2; }
done

case "$LOTE" in ''|*[!0-9]*) registrar "ERRO: LOTE inválido"; exit 2 ;; esac
case "$MAX_PCT" in ''|*[!0-9]*) registrar "ERRO: MAX_PCT inválido"; exit 2 ;; esac

# Monta a lista SQL a partir dos nomes configurados. Nome de instância na
# Evolution é [a-zA-Z0-9_-]; qualquer outro caractere é recusado em vez de
# escapado, porque aqui não há motivo legítimo para ele.
LISTA_SQL=""
IFS=',' read -r -a _nomes <<< "$INSTANCIAS_INTERNAS"
for nome in "${_nomes[@]}"; do
    nome="$(echo "$nome" | tr -d '[:space:]')"
    [ -n "$nome" ] || continue
    case "$nome" in
        *[!a-zA-Z0-9_-]*)
            registrar "ERRO: nome de instância inválido em INSTANCIAS_INTERNAS: ${nome@Q}"
            exit 2 ;;
    esac
    LISTA_SQL="${LISTA_SQL:+$LISTA_SQL, }'$nome'"
done
if [ -z "$LISTA_SQL" ]; then
    registrar "ERRO: INSTANCIAS_INTERNAS está vazio."
    registrar "  Se a intenção é aplicar a mesma retenção a todas as instâncias,"
    registrar "  defina RETENCAO_INTERNA_DIAS igual a RETENCAO_CLIENTE_DIAS — não esvazie a lista."
    exit 2
fi

if ! docker inspect "$PG_CONTAINER" >/dev/null 2>&1; then
    registrar "ERRO: container ${PG_CONTAINER@Q} não existe. Confirme com: docker ps --format '{{.Names}}'"
    exit 3
fi

# ------------------------------------------------------------------ psql

# -qtAX: silencioso, tuplas only, não alinhado, ignora psqlrc.
# A senha é resolvida DENTRO do container (aspas simples no sh -c), então não
# aparece em ps, nem no histórico, nem no log do cron.
psql_exec() {
    docker exec -i "$PG_CONTAINER" sh -c \
        'PGPASSWORD="$POSTGRES_PASSWORD" exec psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -qtAX -v ON_ERROR_STOP=1 -f -'
}

CORTE_INTERNA="extract(epoch from now() - interval '${RETENCAO_INTERNA_DIAS} days')::int"
CORTE_CLIENTE="extract(epoch from now() - interval '${RETENCAO_CLIENTE_DIAS} days')::int"

# O corte é calculado pelo now() do BANCO, não do host: evita divergência de
# fuso/relógio entre a VPS e o container.
COND_INTERNA="i.name IN (${LISTA_SQL}) AND m.\"messageTimestamp\" < ${CORTE_INTERNA}"
COND_CLIENTE="i.name NOT IN (${LISTA_SQL}) AND m.\"messageTimestamp\" < ${CORTE_CLIENTE}"

contar() {
    local cond="$1"
    psql_exec <<SQL | tail -n 1
SELECT count(*) FROM "Message" m
  JOIN "Instance" i ON i.id = m."instanceId"
 WHERE ${cond};
SQL
}

detalhar() {
    local cond="$1"
    psql_exec <<SQL
SELECT i.name, count(*) FROM "Message" m
  JOIN "Instance" i ON i.id = m."instanceId"
 WHERE ${cond}
 GROUP BY i.name ORDER BY 2 DESC;
SQL
}

# Um lote. A CTE devolve quantas linhas saíram, que é como o laço sabe parar.
# O DELETE usa "id IN (...)" com LIMIT na subconsulta para o lock ficar restrito
# ao lote e não à tabela toda.
apagar_lote() {
    local cond="$1"
    psql_exec <<SQL | tail -n 1
SET lock_timeout = '5s';
SET statement_timeout = '60s';
WITH alvo AS (
    SELECT m.id FROM "Message" m
      JOIN "Instance" i ON i.id = m."instanceId"
     WHERE ${cond}
     LIMIT ${LOTE}
), apagadas AS (
    DELETE FROM "Message" WHERE id IN (SELECT id FROM alvo) RETURNING 1
)
SELECT count(*) FROM apagadas;
SQL
}

expurgar() {
    local rotulo="$1" cond="$2" total=0 n
    while :; do
        if ! n="$(apagar_lote "$cond" 2>>"$LOG")"; then
            registrar "  ERRO no lote de ${rotulo} (lock_timeout/statement_timeout?) — interrompendo este grupo"
            break
        fi
        n="${n:-0}"
        case "$n" in ''|*[!0-9]*) registrar "  resposta inesperada do psql em ${rotulo}: ${n@Q}"; break ;; esac
        total=$((total + n))
        [ "$n" -lt "$LOTE" ] && break
        sleep "$PAUSA_S"
    done
    registrar "  ${rotulo}: ${total} mensagem(ns) apagada(s)"
}

# ---------------------------------------------------------------- execução

registrar "=== expurgo início · internas(${INSTANCIAS_INTERNAS})=${RETENCAO_INTERNA_DIAS}d · demais=${RETENCAO_CLIENTE_DIAS}d · modo=$([ "$APLICAR" -eq 1 ] && echo aplicar || echo relatorio)"

TOTAL_TABELA="$(psql_exec <<<'SELECT count(*) FROM "Message";' | tail -n 1)"
ALVO_INTERNA="$(contar "$COND_INTERNA")"
ALVO_CLIENTE="$(contar "$COND_CLIENTE")"
ALVO_TOTAL=$((ALVO_INTERNA + ALVO_CLIENTE))

registrar "tabela: ${TOTAL_TABELA} linha(s) · a expurgar: ${ALVO_TOTAL} (internas ${ALVO_INTERNA}, demais ${ALVO_CLIENTE})"

if [ "$ALVO_TOTAL" -gt 0 ]; then
    registrar "por instância:"
    { detalhar "$COND_INTERNA"; detalhar "$COND_CLIENTE"; } | while IFS='|' read -r nome qtd; do
        [ -n "${nome:-}" ] && registrar "  ${nome}: ${qtd}"
    done
fi

# Mensagem com timestamp no futuro nunca casa com o filtro "< corte" e ficaria
# para sempre. Causa provável: relógio errado no aparelho. É só RELATADO, não
# apagado — não dá para distinguir com certeza de mensagem recém-chegada, e
# apagar por heurística é pior que manter.
FUTURAS="$(psql_exec <<<'SELECT count(*) FROM "Message" WHERE "messageTimestamp" > extract(epoch from now() + interval '"'"'2 days'"'"')::int;' | tail -n 1)"
if [ "${FUTURAS:-0}" -gt 0 ]; then
    registrar "AVISO: ${FUTURAS} mensagem(ns) com timestamp no futuro — não casam com nenhum corte (relógio do aparelho?). Apenas relatado."
fi

if [ "$APLICAR" -ne 1 ]; then
    registrar "=== relatório apenas. Para executar: $0 --aplicar"
    exit 0
fi

if [ "$TOTAL_TABELA" -gt 0 ] && [ "$FORCAR" -ne 1 ]; then
    PCT=$((ALVO_TOTAL * 100 / TOTAL_TABELA))
    if [ "$PCT" -gt "$MAX_PCT" ]; then
        registrar "ABORTADO: o expurgo removeria ${PCT}% da tabela (teto ${MAX_PCT}%)."
        registrar "  Isto é esperado no PRIMEIRO disparo (~86%). Confirme que existe backup do Postgres"
        registrar "  e então rode: $0 --aplicar --forcar"
        exit 4
    fi
fi

expurgar "internas (${RETENCAO_INTERNA_DIAS}d)" "$COND_INTERNA"
expurgar "demais (${RETENCAO_CLIENTE_DIAS}d)" "$COND_CLIENTE"

RESTANTE="$(psql_exec <<<'SELECT count(*) FROM "Message";' | tail -n 1)"
registrar "=== expurgo fim · tabela agora com ${RESTANTE} linha(s)"
