-- Índice de apoio ao expurgo — EXECUTAR UMA VEZ, antes do primeiro disparo.
--
-- Hoje "Message" tem apenas dois índices: a PK em (id) e um em ("instanceId").
-- Não existe índice em "messageTimestamp", então o filtro do expurgo faria
-- varredura sequencial da tabela inteira a cada execução. Tolerável nas 77 mil
-- linhas atuais, caro com N instâncias de cliente.
--
-- O índice é composto e nesta ordem de propósito: o expurgo sempre filtra por
-- instância (política diferenciada) e depois por data.
--
-- CONCURRENTLY não toma lock de escrita, então pode rodar com a Evolution no ar.
-- Em troca, não pode estar dentro de bloco de transação -- execute este arquivo
-- via psql direto (autocommit), nunca dentro de BEGIN/COMMIT.
--
--   docker exec -i <container-pg> sh -c \
--     'PGPASSWORD="$POSTGRES_PASSWORD" psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -f -' \
--     < 01_indice.sql
--
-- Se a criação falhar no meio, o índice fica como INVALID e precisa ser
-- derrubado antes de tentar de novo:
--   DROP INDEX IF EXISTS "Message_instanceId_messageTimestamp_idx";

CREATE INDEX CONCURRENTLY IF NOT EXISTS "Message_instanceId_messageTimestamp_idx"
    ON "Message" ("instanceId", "messageTimestamp");
