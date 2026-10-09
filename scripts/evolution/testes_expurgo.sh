#!/usr/bin/env bash
#
# testes_expurgo.sh — exercita expurgo.sh com um "docker" falso que imita o
# psql da Evolution. Não precisa de VPS, Docker, Postgres nem rede.
#
#   bash scripts/evolution/testes_expurgo.sh
#
# Os números simulam o estado medido em 09/10/2026 (76.847 linhas em "Message",
# 66.157 acima do corte de 30 dias), para cobrir o caso do primeiro disparo —
# que remove 86% da tabela e precisa bater na trava de volume.
set -uo pipefail

AQUI="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT="$AQUI/expurgo.sh"
BASE="$(mktemp -d)"
SHIM="$BASE/bin"
mkdir -p "$SHIM"
export LOG="$BASE/expurgo.log"
export PG_CONTAINER="pg-falso"

echo 66157 > "$BASE/restam_internas"
echo 0 > "$BASE/restam_clientes"
echo 76847 > "$BASE/total"

cat > "$SHIM/docker" <<'SHIMFIM'
#!/usr/bin/env bash
BASE="$TESTE_BASE"
case "$1" in
  inspect)
    [ "$2" = "pg-falso" ] && exit 0 || exit 1 ;;
  exec)
    sql="$(cat)"   # o script manda o SQL por stdin
    if [[ "$sql" == *"DELETE FROM"* ]]; then
      if [[ "$sql" == *"NOT IN"* ]]; then arq="$BASE/restam_clientes"; else arq="$BASE/restam_internas"; fi
      restam="$(cat "$arq")"
      lote=5000
      n=$(( restam < lote ? restam : lote ))
      echo $(( restam - n )) > "$arq"
      total="$(cat "$BASE/total")"; echo $(( total - n )) > "$BASE/total"
      echo "$n"
      exit 0
    fi
    if [[ "$sql" == *"interval '2 days'"* ]]; then echo 3; exit 0; fi
    if [[ "$sql" == *"GROUP BY i.name"* ]]; then
      if [[ "$sql" == *"NOT IN"* ]]; then exit 0; fi
      echo "suporte|43409"; echo "financeiro|22748"; exit 0
    fi
    if [[ "$sql" == *"count(*) FROM \"Message\" m"* ]]; then
      if [[ "$sql" == *"NOT IN"* ]]; then cat "$BASE/restam_clientes"; else cat "$BASE/restam_internas"; fi
      exit 0
    fi
    if [[ "$sql" == *'count(*) FROM "Message";'* ]]; then cat "$BASE/total"; exit 0; fi
    echo "SQL inesperado no shim: $sql" >&2; exit 9 ;;
esac
SHIMFIM
chmod +x "$SHIM/docker"
export TESTE_BASE="$BASE"
export PATH="$SHIM:$PATH"

falhas=0
checar() { # checar <rotulo> <esperado> <obtido>
    if [ "$2" = "$3" ]; then printf '  ok    %s\n' "$1"
    else printf '  FALHA %s (esperado %s, obtido %s)\n' "$1" "$2" "$3"; falhas=$((falhas+1)); fi
}

echo "[validação de argumentos]"
"$SCRIPT" --ajuda >/dev/null 2>&1; checar "--ajuda sai 0" 0 $?
"$SCRIPT" --zzz >/dev/null 2>&1; checar "opção desconhecida sai 2" 2 $?

echo
echo "[validação de parâmetros]"
RETENCAO_CLIENTE_DIAS=abc "$SCRIPT" >/dev/null 2>&1; checar "retenção não numérica sai 2" 2 $?
RETENCAO_CLIENTE_DIAS=0 "$SCRIPT" >/dev/null 2>&1; checar "retenção 0 sai 2" 2 $?
LOTE=xyz "$SCRIPT" >/dev/null 2>&1; checar "LOTE inválido sai 2" 2 $?
INSTANCIAS_INTERNAS="suporte'; DROP TABLE x --" "$SCRIPT" >/dev/null 2>&1
checar "nome com SQL recusado (anti-injeção) sai 2" 2 $?
INSTANCIAS_INTERNAS="" "$SCRIPT" >/dev/null 2>&1; checar "lista vazia sai 2 (não vira padrão)" 2 $?
PG_CONTAINER=nao-existe "$SCRIPT" >/dev/null 2>&1; checar "container inexistente sai 3" 3 $?

echo
echo "[dry-run]"
saida="$("$SCRIPT" 2>&1)"; rc=$?
checar "dry-run sai 0" 0 $rc
grep -q "modo=relatorio" <<<"$saida"; checar "anuncia modo relatório" 0 $?
grep -q "a expurgar: 66157 (internas 66157, demais 0)" <<<"$saida"; checar "soma o alvo corretamente" 0 $?
grep -q "suporte: 43409" <<<"$saida"; checar "detalha por instância" 0 $?
grep -q "AVISO: 3 mensagem(ns) com timestamp no futuro" <<<"$saida"; checar "relata timestamps futuros" 0 $?
grep -q "Para executar" <<<"$saida"; checar "ensina como aplicar" 0 $?
checar "dry-run NÃO apagou nada" 66157 "$(cat "$BASE/restam_internas")"

echo
echo "[trava de volume]"
saida="$("$SCRIPT" --aplicar 2>&1)"; rc=$?
checar "--aplicar com 86% sai 4" 4 $rc
grep -q "ABORTADO" <<<"$saida"; checar "aborta com ABORTADO" 0 $?
grep -q "86% da tabela" <<<"$saida"; checar "informa o percentual real" 0 $?
checar "trava NÃO apagou nada" 66157 "$(cat "$BASE/restam_internas")"

echo
echo "[execução forçada em lotes]"
saida="$("$SCRIPT" --aplicar --forcar 2>&1)"; rc=$?
checar "--aplicar --forcar sai 0" 0 $rc
grep -q "66157 mensagem(ns) apagada(s)" <<<"$saida"; checar "apagou o total das internas" 0 $?
checar "estado zerado nas internas" 0 "$(cat "$BASE/restam_internas")"
checar "tabela restante = 76847-66157" 10690 "$(cat "$BASE/total")"
grep -q "tabela agora com 10690" <<<"$saida"; checar "reporta o restante final" 0 $?

echo
echo "[execução de regime: nada a fazer]"
saida="$("$SCRIPT" --aplicar 2>&1)"; rc=$?
checar "execução sem alvo sai 0" 0 $rc
grep -q "a expurgar: 0 " <<<"$saida"; checar "reporta alvo zero" 0 $?

echo
echo "[log]"
[ -s "$LOG" ]; checar "log foi escrito" 0 $?
grep -q "expurgo início" "$LOG"; checar "log tem marca de início" 0 $?

echo
echo "============================================================"
if [ "$falhas" -gt 0 ]; then echo "$falhas FALHA(S)"; rm -rf "$BASE"; exit 1; fi
echo "Todos os testes do expurgo passaram."
rm -rf "$BASE"
