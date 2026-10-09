# Expurgo de mensagens da Evolution API

Rotina de retenção para o Postgres da Evolution API compartilhada.

> **Isto é infraestrutura, não parte do `wpp-client`.** Mora neste repositório
> para ficar versionado e achável, mas **roda no host da VPS** e limpa dados que
> também são do `wpp-premio`. O container do `wpp-client` nunca executa isto.

## Por que existe

O `wpp-client` não grava mensagem nenhuma, mas a **Evolution** grava: o container
compartilhado está com `DATABASE_SAVE_DATA_NEW_MESSAGE=true` e persiste todo
payload no Postgres dela. Decisão de projeto (09/10/2026) foi compartilhar a
Evolution e aceitar isso, com retenção como contrapartida.

Medido em 09/10/2026: **76.847 linhas / 80 MB** em `"Message"`, de duas
instâncias de uso interno.

## Política

| Instâncias | Retenção |
|---|---|
| `suporte`, `financeiro` (internas) | **30 dias** |
| todas as outras, inclusive as `cli_*` do `wpp-client` | **1 dia** |

A regra é "está na lista de internas → 30 dias; senão → 1 dia", e esse sentido é
deliberado: uma instância interna nova criada no futuro (`vendas`, por exemplo)
cai no balde conservador de 30 dias em vez de ser varrida em 24h sem ninguém
notar. Ajuste `INSTANCIAS_INTERNAS` ao criar uma.

**1 dia não significa 24 horas.** O agendamento escolhido é **diário às 05:00**,
e um job diário apagando "mais velho que 1 dia" deixa os dados vivendo entre
**24 e 48 horas**, dependendo da hora em que a mensagem chegou. Isso foi aceito
conscientemente; se um dia o teto real de ~24h passar a importar (exigência
contratual, por exemplo), basta trocar o cron para 6 em 6 horas — ver
[Agendamento](#agendamento). O script não muda.

## Fatos do schema que o script explora

Verificados no Postgres 16.14 em 09/10/2026 — não presuma, releia se a Evolution
for atualizada:

- `"Message"."messageTimestamp"` é **`integer` (unix epoch em segundos)**, não
  `timestamp`. **Não existe `createdAt`** na tabela, então o corte usa o
  timestamp que o WhatsApp manda, não o da gravação.
- `"MessageUpdate"` e `"Media"` têm FK para `"Message"` com **`ON DELETE
  CASCADE`**, então basta apagar de `"Message"`. `"MessageUpdate"` não tem coluna
  de data alguma — só dá para expurgá-la pelo pai.
- `"Message"."instanceId"` é `NOT NULL` com FK CASCADE para `"Instance"(id)`,
  logo **mensagem órfã é impossível** — não há caso de borda a tratar.
- `"Instance"."name"` é `varchar(255)` **UNIQUE**, seguro como chave de negócio.
- **Não há índice em `"messageTimestamp"`** — daí o `01_indice.sql`.
- **Nem `pg_cron` nem `pg_repack` existem** na imagem `postgres:16-alpine`: o
  agendamento tem de ser externo e não há como devolver espaço ao disco sem
  downtime.

## Consequência do timestamp ser o do WhatsApp

Dois efeitos que você deve conhecer antes do primeiro disparo:

1. **Histórico importado no pareamento morre na hora.** Quando um cliente conecta
   e o WhatsApp sincroniza conversas antigas, essas linhas entram já "velhas" e o
   corte de 1 dia as remove na primeira execução. Para o objetivo (não guardar
   conversa de terceiro) isso é desejável, mas é bom não ser surpresa.
2. **Mensagem com timestamp no futuro nunca é apagada.** Relógio errado no
   aparelho produz linha que nenhum corte `< agora - N` alcança. O script
   **relata** essas linhas (`AVISO: N mensagem(ns) com timestamp no futuro`) e
   **não as apaga** — não há como distinguir com certeza de mensagem recém-chegada,
   e apagar por heurística é pior que manter. Se o número crescer, trate à mão.

## Antes do primeiro disparo

**1. Faça backup do Postgres da Evolution. Hoje não existe nenhum.**

Verificado: o único backup configurado no Coolify é semanal, do MariaDB, apenas
do schema `premio_db`, apenas local, sem retenção. O `evolution-postgres` está
**totalmente sem backup**. O primeiro expurgo remove ~86% de `"Message"`.

```bash
# dump pontual, guardado FORA da VPS depois
docker exec <container-pg> sh -c \
  'PGPASSWORD="$POSTGRES_PASSWORD" pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' \
  > evolution-$(date +%Y%m%d).dump
```

E configure o backup recorrente no Coolify para esse banco.

**2. Descubra o nome do container do Postgres.**

```bash
docker ps --format '{{.Names}}\t{{.Image}}' | grep postgres
```

O padrão do script (`hy3d3h30dj6552bd9hdkp7ik`) vem do host em
`DATABASE_CONNECTION_URI` da Evolution. Confirme e exporte `PG_CONTAINER` se
divergir — o script valida e falha com mensagem clara.

**3. Crie o índice** (uma vez, pode ser com a Evolution no ar):

```bash
docker exec -i <container-pg> sh -c \
  'PGPASSWORD="$POSTGRES_PASSWORD" psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -f -' \
  < 01_indice.sql
```

**4. Veja o relatório sem apagar nada** (é o modo padrão):

```bash
./expurgo.sh
```

**5. Primeiro disparo**, que bate na trava de volume de propósito:

```bash
./expurgo.sh --aplicar            # aborta com exit 4, mostrando o percentual
./expurgo.sh --aplicar --forcar   # executa
```

Das execuções seguintes em diante, `--aplicar` sozinho basta: em regime o volume
diário fica muito abaixo do teto de 50%.

## Agendamento

Não há `pg_cron`, então o agendamento é externo. **Horário importa:** os clientes
são food service e delivery, cujo pico é **18h–23h**. A janela certa é de
madrugada tardia, não meia-noite.

### Opção recomendada: cron no host

Mais robusto que a Scheduled Task do Coolify porque não depende de o recurso
estar íntegro no painel, e o nome do container da Evolution é fixo no compose.

```cron
# /etc/cron.d/expurgo-evolution
# Diário às 05:00 — frequência escolhida em 09/10/2026.
# Consequência aceita: dados de cliente vivem entre 24 e 48 horas, conforme a
# hora de chegada da mensagem (ver "1 dia não significa 24 horas" acima).
0 5 * * * root /opt/syscon/expurgo.sh --aplicar >/dev/null 2>&1

# Alternativa, se um dia o teto real de ~24-30h passar a importar:
# 0 */6 * * * root /opt/syscon/expurgo.sh --aplicar >/dev/null 2>&1
```

As 05:00 não são arbitrárias: os clientes são food service e delivery, com pico
entre 18h e 23h. Meia-noite cairia em cima do movimento; 05:00 é o vale.

O script já escreve em `/var/log/expurgo-evolution.log` (cai para `/tmp` se não
tiver permissão), por isso o `>/dev/null` no cron.

### Opção alternativa: Scheduled Task do Coolify

Duas pegadinhas da versão 4.4.3, verificadas no banco do Coolify:

- **`command` é `varchar(255)`** — SQL de expurgo com retenção diferenciada não
  cabe. Tem de chamar um script, nunca SQL inline.
- **`timeout` é 300s por padrão** — o primeiro disparo pode passar disso. Suba o
  timeout ou rode o primeiro à mão e deixe só o regime agendado.

Como a Evolution é um *Service* (não Application) no Coolify, a task se prende a
`service_id` com `container = evolution-api-w49p53ncphmnsiybyoadsxbp`. Não havia
nenhuma Scheduled Task configurada no host, então não existe exemplo local para
copiar.

## Impacto na VPS

| Aspecto | Efeito |
|---|---|
| **Lock de escrita** | Lotes de 5.000 com `lock_timeout=5s` e `statement_timeout=60s`. Um lote nunca segura as escritas da Evolution (que estão no caminho do webhook); se estourar o timeout, o grupo é interrompido e logado. |
| **Disco durante** | `DELETE` escreve WAL por linha, então o uso de disco **sobe** antes de cair. Com 8% de 193 GB usados há folga de sobra. |
| **Disco depois** | O espaço **não volta ao filesystem** — fica reutilizável internamente pelo Postgres. Isso é o comportamento desejado: a tabela estabiliza num patamar em vez de crescer. |
| **Autovacuum** | Entra após os deletes em massa, consumindo CPU e I/O. Daí o agendamento de madrugada. |
| **RAM** | Container do Postgres em ~152 MB; lotes não movem isso. |
| **`wpp-premio`** | Nenhum impacto. O único consumidor do histórico é o fallback de mídia (`getBase64FromMediaMessage`), que roda segundos após a chegada — 1 dia já é ordens de grandeza mais que o necessário. |

### O que a rotina nunca faz

**`VACUUM FULL`.** Toma `ACCESS EXCLUSIVE` (derruba a Evolution inteira),
duplica a tabela em disco e dá pico de memória. O host tem **swap 0**, então é
convite ao OOM killer — que mataria o container e derrubaria `suporte`,
`financeiro` e as sessões de todos os clientes de uma vez. `pg_repack`, que faria
isso sem downtime, não está disponível na imagem. Autovacuum resolve o caso de uso.

## Códigos de saída

| Código | Significado |
|---|---|
| 0 | sucesso (ou relatório, no modo padrão) |
| 2 | parâmetro inválido |
| 3 | container do Postgres não encontrado |
| 4 | trava de volume: removeria mais que `MAX_PCT`% da tabela |

## Testes

```bash
bash scripts/evolution/testes_expurgo.sh
```

28 verificações com um `docker` falso que imita o psql — sem VPS, Docker,
Postgres ou rede. Cobre validação de parâmetros, recusa de nome de instância com
SQL embutido, o dry-run não apagando nada, a trava de volume, o laço de lotes e a
execução de regime sem alvo.
