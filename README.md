# wpp-client — WhatsApp do cliente sobre a Evolution API

Painel onde o **cliente final da Prêmio Sistemas** conecta o WhatsApp da própria
empresa, configura saudação, horário de funcionamento com mensagem de ausência,
publica status e recebe um token para **enviar mensagens pela API** do próprio
sistema.

- **Stack:** Flask + Jinja + `mysql-connector-python` (sem ORM), Gunicorn, Docker.
- **Motor de WhatsApp:** Evolution API v2 (a mesma instância que o `wpp-premio` usa).
- **Login:** CNPJ, validado na `api-master`.
- **Porta interna:** **2002** (`api-master` 2000, `wpp-premio` 2001).
- **Domínio:** `wpp.premiosistemas.com.br`.

> Projeto **independente** do `wpp-premio`: não compartilha código, rotas nem
> processo. A única coisa em comum é a Evolution e o banco MariaDB (ver
> [Isolamento](#isolamento-em-relação-ao-wpp-premio)).

---

## O que é armazenado (e o que não é)

Por exigência de projeto, a pegada é mínima:

| Guardado | Onde |
|---|---|
| A instância conectada (para ficar 24h online) | `client_instancias` |
| Saudação, horário e mensagem de ausência | `client_configuracoes` |

**Nada mais.** Mensagem de cliente **não é gravada por este app**: o webhook lê o
payload, decide se cabe saudação e descarta. Não há histórico, não há tabela de
contatos, não há mídia em disco — a imagem do status vai do multipart direto
para base64 em memória e segue para a Evolution.

> ⚠️ **A Evolution, porém, grava — e isso foi aceito conscientemente.**
>
> O container compartilhado está com `DATABASE_SAVE_DATA_NEW_MESSAGE=true` (mais
> `_MESSAGE_UPDATE`, `_CONTACTS`, `_CHATS`, `_HISTORIC`), então **ela** persiste
> toda mensagem no Postgres dela antes de o webhook chegar aqui. Medido em
> 09/10/2026: duas instâncias de uso interno acumularam **76.823 mensagens /
> 80 MB** na tabela `Message` (~1 KB por mensagem, o que indica o payload Baileys
> inteiro, com o texto da conversa — inferido do tamanho médio, não lido).
>
> Nenhuma configuração deste projeto evita isso; é flag da Evolution. A decisão
> de projeto (09/10/2026) foi **compartilhar a Evolution e aceitar a gravação**,
> em vez de subir uma instância dedicada. Duas consequências que ficam de pé:
>
> 1. **Retenção.** Resolvida por rotina de expurgo em
>    [`scripts/evolution/`](scripts/evolution/README.md): 1 dia para as instâncias
>    `cli_*` deste app, 30 dias para `suporte` e `financeiro`. É infraestrutura —
>    **roda no host da VPS**, não neste container, e limpa dados que também são do
>    `wpp-premio`. Mora neste repositório só para ficar versionada.
>    ⚠️ Antes do primeiro disparo, leia o pré-requisito de backup: o Postgres da
>    Evolution **não tem backup nenhum** hoje, e o primeiro expurgo remove ~86%
>    da tabela.
> 2. **LGPD.** O conteúdo das conversas dos clientes *dos clientes* fica
>    armazenado em banco operado pela Prêmio Sistemas, sem necessidade funcional.
>    Trate como dado pessoal de terceiro ao definir retenção e acesso.
>
> Se a decisão for revista, migrar custa **duas linhas de `.env`**
> (`EVOLUTION_URL`, `EVOLUTION_API_KEY`) e nenhuma mudança de código. A
> configuração correta para a instância dedicada seria `_NEW_MESSAGE=false`,
> `_MESSAGE_UPDATE=false`, `_HISTORIC=false`, mas mantendo **`_INSTANCE=true`**
> (sem isso a sessão não sobrevive a restart) e **`_CONTACTS=true`** (a audiência
> do status via `allContacts` sai da tabela `Contact` da Evolution).

O que vive **em memória** (e se perde num redeploy, de propósito):

| Estado | Custo de perder |
|---|---|
| Cache do QR Code (TTL 90s) | o painel pede um QR novo |
| Janela de repetição da saudação | no pior caso, uma saudação repetida |
| Fila de envios + resultados (TTL 15min) | envios em trânsito; o resultado deixa de ser consultável |
| Rate-limit por instância | nada |

É essa escolha que dispensa Redis e mantém o consumo de RAM baixo.

---

## Estrutura

```
app.py            factory Flask + blueprints + schema idempotente + /health
config.py         variáveis de ambiente (python-dotenv)
resposta.py       envelope JSON único {sucesso, mensagem, dados}
log_config.py     logging rotativo + stdout
schema.sql        DDL das tabelas client_*
api/              SOMENTE conexões: database, evolution_client, master_client
models/           instancia, configuracao (SQL parametrizado)
services/         auth, instance, saudacao, horario, typing, messaging,
                  fila_envio, status, monitor
controllers/      blueprints: auth, painel, instancia, config, status,
                  webhook, api_controller (API pública /api/v1)
views/            templates Jinja + static (css/js)
scripts/testes.py suíte de testes sem banco
scripts/evolution/ expurgo de mensagens na Evolution — INFRA, roda no host da VPS
```

## Execução local

```bash
pip install -r requirements.txt
cp .env.example .env        # preencha as variáveis
python app.py               # http://localhost:2002

python scripts/testes.py    # 69 verificações, sem banco e sem Evolution

bash scripts/evolution/testes_expurgo.sh   # 28 verificações da rotina de expurgo
```

Para o webhook funcionar em dev, exponha a porta 2002 num túnel (ex.: cloudflared)
e aponte `PANEL_BASE_URL` para a URL pública — é ela que vira o webhook das
instâncias.

## Deploy no Coolify

1. Repositório git próprio para este diretório, push.
2. Coolify: **New Resource → Dockerfile**.
3. Variáveis do `.env.example` no painel do Coolify (nunca no git).
4. Porta exposta **2002**; Traefik roteia `wpp.premiosistemas.com.br` com SSL.
5. `PANEL_BASE_URL=https://wpp.premiosistemas.com.br`.
6. Healthcheck: `GET /health`. O Dockerfile já declara um `HEALTHCHECK` em
   stdlib do Python — **a imagem slim não tem curl nem wget**, então um
   healthcheck baseado neles falha por binário ausente. Se ligar o healthcheck
   do Coolify, aponte o path para `/health`, não `/`.

### Se o domínio responder "no available server"

Isso é 503 do Traefik: há rota para o domínio, mas nenhum backend saudável.
Quase sempre significa que o container está em loop de reinício, e a causa mais
provável é o banco — `criar_app()` **aborta de propósito** se não aplicar o
schema, então credencial ou endereço errados matam o worker no boot.

```bash
docker inspect <container> --format '{{.RestartCount}} {{.State.ExitCode}}'
docker logs --tail 50 <container> | grep -i schema
```

`2003 (HY000): Can't connect` é **endereço** errado (ninguém escutando);
`1045 Access denied` é **credencial**. Veja a nota do `DB_HOST` no
`.env.example`: tem de ser o nome do container na rede `coolify`, nunca o IP
público — o MariaDB publica `1999->3306`, logo `<ip-público>:3306` não existe.

> ⚠️ **Um worker, várias threads.** O `CMD` do Dockerfile usa
> `gunicorn -w 1 --threads 64 -k gthread`. Todo o estado acima é por processo:
> com 2+ workers o QR geraria num worker e o painel consultaria noutro, a
> saudação sairia duplicada e o monitor de conexão rodaria N vezes. Se precisar
> escalar, a saída é mover esse estado para Redis — não aumentar `-w`.

---

## Fluxo: cliente conecta o WhatsApp

```
/login  → CNPJ → api-master GET /v1/cliente/<cnpj>
            bloqueado ou cancelado → acesso negado
            ok → sessão (id_cliente, cnpj, empresa)

/  → tem instância?
     não → botão "Criar instância"
             POST /api/instancia
             → Evolution POST /instance/create  { instanceName: cli_<id>,
                 integration: WHATSAPP-BAILEYS, qrcode: true,
                 webhook: { url: PANEL_BASE_URL/webhook/cli_<id>,
                            headers: { x-webhook-token: WEBHOOK_SECRET },
                            events: [MESSAGES_UPSERT, QRCODE_UPDATED,
                                     CONNECTION_UPDATE] } }
             → linha em client_instancias (status=conectando) + token_api
             → QR devolvido na resposta
     sim → mostra o status da conexão

QR ao vivo: Evolution dispara QRCODE_UPDATED → webhook → cache em memória
            → painel faz polling de GET /api/instancia a cada 3s
cliente escaneia → CONNECTION_UPDATE state=open → webhook
            → status=conectada → o polling PARA e as configurações aparecem
```

As configurações (saudação, horário, status, API) só aparecem com a instância
**conectada**.

## Fluxo: mensagem recebida

```
WhatsApp → Evolution → POST /webhook/cli_<id>
    valida x-webhook-token (comparação constante) + instância registrada
    ignora fromMe, @g.us, @broadcast, @newsletter, @lid
    fora do horário?      → mensagem de ausência
    senão, saudação ativa → mensagem de saudação
    número já respondido dentro da janela? → nada
    → enfileira o envio e responde 200 IMEDIATAMENTE
         (o payload é descartado aqui: nada é gravado)
    fila → typing_service.simular()  "digitando…" por 15 a 40s
         → rate-limit da instância (1 msg/s)
         → Evolution POST /message/sendText/cli_<id>
```

O 200 imediato não é detalhe: a Evolution reentrega — e chega a desabilitar — um
webhook que demora.

---

## Digitação simulada

Todo envio (saudação, ausência e API) passa por `services/typing_service`:

```
proporcional = len(texto) / DIGITACAO_CHARS_POR_SEGUNDO    (padrão 12 c/s)
proporcional = limitado a [DIGITACAO_MIN_S, DIGITACAO_MAX_S]  (1,5s a 25s)
total        = proporcional + random.uniform(3, 12)
```

Uma mensagem de 120 caracteres leva de **13 a 22 segundos**; uma mensagem longa
chega a 37s. A presença `composing` expira sozinha no WhatsApp, então é
reenviada a cada `PRESENCA_PASSO_S` (8s) até o tempo acabar — sem isso o
"digitando…" apaga no meio.

`SIMULAR_DIGITACAO=0` desliga tudo, e a API aceita `"typing": false` por chamada.

---

## API de envio

Autenticação: `Authorization: Bearer <token_api>`, um token por cliente, visível
e regerável no painel. A apikey da Evolution **nunca** sai do servidor.

Por causa da digitação simulada, o envio é **assíncrono**: a chamada responde
`202` em milissegundos e a mensagem sai depois.

### `POST /api/v1/mensagem`

```json
{ "numero": "5544999998888", "mensagem": "Seu pedido saiu para entrega!", "typing": true }
```

```json
HTTP 202
{ "sucesso": true,
  "mensagem": "Envio aceito. Consulte o status em /api/v1/envio/<id_envio>.",
  "dados": { "id_envio": "8f3a…", "status": "na_fila" } }
```

### `POST /api/v1/imagem`

```json
{ "numero": "5544999998888", "imagem": "https://.../foto.jpg",
  "legenda": "Chegou!", "nome_arquivo": "foto.jpg", "typing": true }
```

`imagem` aceita URL pública ou base64 (limite `MAX_IMAGEM_MB`).

### `GET /api/v1/envio/<id_envio>`

Também atende em `/api/v1/mensagem/<id_envio>`.

```json
{ "sucesso": true, "dados": { "id_envio": "8f3a…", "status": "enviada",
                              "id_whatsapp": "3EB0…", "erro": null } }
```

`status`: `na_fila` · `enviando` · `enviada` · `erro`. Consultável por
`FILA_RESULTADO_TTL_S` (15min); depois disso, `404`.

### `GET /api/v1/conta`

Confere o token e devolve empresa, número, status da conexão e envios pendentes.

### Códigos de erro

| HTTP | `erro` | Significado |
|---|---|---|
| 401 | `token_ausente` / `token_invalido` | header faltando ou token desconhecido |
| 409 | `instancia_desconectada` | o WhatsApp do cliente não está conectado |
| 400 | `numero_invalido` | use DDI+DDD+número (12 ou 13 dígitos) |
| 400 | `mensagem_vazia` / `mensagem_longa` | texto ausente ou acima de 4096 caracteres |
| 400 | `imagem_ausente` / `imagem_invalida` / `imagem_grande` | problema na imagem |
| 429 | `fila_cheia` | `FILA_MAX_PENDENTES` atingido |
| 404 | `envio_desconhecido` | id inexistente ou vencido |

Números são normalizados: `44 99999-8888`, `+55 44 99999-8888` e `5544999998888`
são o mesmo destino.

---

## Publicar status

`POST /api/status` (painel, `multipart/form-data`: `imagem` + `legenda`),
**síncrono** — a tela diz na hora se saiu ou não.

Status no WhatsApp não é broadcast aberto: vai para uma lista de destinatários.
Este projeto usa `allContacts: true`, então a **Evolution** monta a lista a
partir dos contatos da própria instância conectada. Nenhum contato é armazenado
aqui.

> A rota `/message/sendStatus` depende da build da Evolution. Se ela não existir,
> o painel responde `501` com mensagem clara em vez de erro genérico.

---

## Horário de funcionamento

Mesmo formato do `wpp-premio` (`services/horario_service`), copiado de propósito
para que as duas telas se comportem igual:

```json
{ "ativo": true, "dias": [0,1,2,3,4], "inicio": "08:00",
  "intervalo_inicio": "12:00", "intervalo_fim": "13:30",
  "fim": "18:00", "msg_fora": "" }
```

`dias`: 0 = segunda … 6 = domingo. `msg_fora` em branco gera a mensagem padrão
descrevendo os dias e horários configurados. `ativo: false` ou coluna `NULL` =
sempre aberto. Comparações no fuso `ATENDIMENTO_TZ`.

## Saudação

```json
{ "ativo": true, "mensagem": "Olá! 👋 …", "janela_horas": 12 }
```

`janela_horas` é o mínimo entre duas saudações para o **mesmo contato** (1 a
720). Sem ela, cada mensagem do cliente geraria uma resposta automática.

---

## Log remoto na api-master

Erros e **motivos de mensagem não enviada** vão para `POST {MASTER_API_URL}/erros`
com `id_software=21`. Campos: `id_cliente`, `descricao`, `versao`, `classe`,
`metodo`, `linha`, `qtd`, `classificacao`, `data`.

`classificacao` distingue os dois tipos:

| Valor | Significado |
|---|---|
| `0` | erro técnico (exceção, recusa da Evolution, banco fora) |
| `1` | mensagem não enviada, com o motivo |

Motivos registrados em `descricao` (`mensagem nao enviada: <motivo> \| instancia=… \| numero=…`):

| Motivo | Quando |
|---|---|
| `saudacao_desligada` | dentro do horário, mas a saudação está desativada |
| `janela_de_repeticao` | o número já foi saudado dentro de `janela_horas` |
| `origem_ignorada` | grupo, lista de transmissão, canal |
| `numero_invalido` | remetente que não é telefone discável |
| `sem_remetente` | payload sem `remoteJid` |
| `fila_cheia` | `FILA_MAX_PENDENTES` atingido |
| `falha_no_envio` | a decisão foi enviar e a Evolution recusou |

Os motivos "legítimos" são registrados de propósito: sem eles, *"o bot não
respondeu"* é indistinguível de *"o bot decidiu não responder"*.

**Logar não custa caro aqui**, e isso é deliberado — o `syscon_app` dispara um
POST síncrono a cada `log()`, que é um problema conhecido do ecossistema. Neste
app o envio é assíncrono, com fila limitada (descarta em vez de crescer),
deduplicação por janela (5 ocorrências iguais = 1 requisição com `qtd=5`) e
disjuntor (api-master fora do ar pausa as tentativas).

Todo `log.error(...)` do app é espelhado automaticamente por um
`logging.Handler`, sem precisar de chamada extra em cada ponto. Onde o cliente
importa, chame `log_remoto.erro(..., id_cliente=...)` direto, porque o handler
não tem como saber de quem é o erro.

> ⚠️ `POST /v1/erros` responde **200 "Erro registrado" sempre**, mesmo com
> payload inválido — `LogBLL.setErroJson` engole a exceção e devolve `None`. Um
> 200 prova o transporte, não a gravação.

`GET /diagnostico` mostra o estado (fila, descartes, disjuntor) sem expor dado
de cliente. `LOG_REMOTO_ATIVO=0` desliga; `LOG_REMOTO_NAO_ENVIADAS=0` mantém só
os erros técnicos.

## Monitor de conexão

Thread única, a cada `MONITOR_INTERVALO_S` (300s), por instância:

1. consulta o estado real na Evolution e grava — autocura quando o
   `connection.update` se perdeu e o painel ficaria preso em "conectando";
2. se o webhook registrado lá divergir de `PANEL_BASE_URL`, reaponta (salva as
   instâncias depois de uma troca de domínio);
3. se caiu, tenta reabrir a conexão.

O passo 3 só funciona enquanto a sessão Baileys continua válida no servidor. Se o
cliente removeu o aparelho ou a sessão expirou, o painel passa a mostrar
**desconectada** e ele precisa ler um QR novo — não há como evitar isso sem o
aparelho dele.

`MONITOR_ATIVO=0` desliga.

---

## Isolamento em relação ao `wpp-premio`

**Evolution compartilhada.** O que separa os dois é o webhook: cada instância
carrega a própria URL de callback, definida na criação. As instâncias deste
projeto usam o prefixo **`cli_`** e nenhuma rotina aqui lista ou apaga instância
fora desse prefixo.

⚠️ A apikey global da Evolution é tudo-ou-nada, e pior do que parecia: o
container roda com `AUTHENTICATION_EXPOSE_IN_FETCH_INSTANCES=true`, então quem
tem a key global lê, via `GET /instance/fetchInstances`, a **apikey de todas as
instâncias** — inclusive `suporte` e `financeiro`, do atendimento interno. Este
app deliberadamente **não implementa `fetchInstances`** e nunca apaga instância
fora do prefixo `cli_`, mas isso é disciplina de código, não barreira técnica: a
key no `.env` deste app (o exposto ao cliente final) abre aquela porta.

**Banco compartilhado.** As tabelas `client_*` ficam dentro do schema
`wpp_premio`, por decisão de projeto. Os dois apps usam a **mesma credencial de
MySQL** — e o `maria-premio` está publicado em `0.0.0.0:1999`, alcançável da
internet. Se a credencial deste app vazar, as conversas e a base de conhecimento
do `wpp-premio` ficam atingíveis de qualquer lugar, não só de dentro da rede
Docker. A credencial nunca aparece em template, resposta JSON ou log.

**Raio de falha.** O host não tem swap (`Swap: 0 B`, verificado em 09/10/2026).
Um pico de memória — vários clientes pareando ao mesmo tempo, por exemplo —
aciona o OOM killer e mata o container da Evolution inteiro, derrubando as
sessões do `suporte` e do `financeiro` junto com as dos clientes. Teto prudente
nesta máquina: **20 a 30 instâncias** (4 vCPU, 11 GiB livres, ~80–150 MB por
sessão Baileys em regime).

Uma Evolution dedicada resolveria persistência, exposição de apikeys e raio de
falha de uma vez. A decisão de projeto foi compartilhar — os três itens acima
são, portanto, riscos aceitos e documentados, não pendências de código.

---

## Segurança

- **Login só por CNPJ**, como especificado. CNPJ não é segredo (consta em nota
  fiscal, site e na Receita), então quem souber o CNPJ de um cliente entra no
  painel dele: lê as configurações, publica status e pega o token da API.
  Mitigação parcial: limitador de **`LOGIN_TENTATIVAS_MAX` tentativas por IP a
  cada 5 minutos**, para a tela não servir de varredura de CNPJs. Não substitui
  um segundo fator.
- Webhook validado por `x-webhook-token` (comparação em tempo constante) +
  instância registrada no banco.
- Token da API comparado com `hmac.compare_digest`; não vai no HTML do painel,
  só sob pedido em `GET /api/instancia/token`.
- Cookie de sessão `HttpOnly`, `SameSite=Lax`, e `Secure` quando
  `PANEL_BASE_URL` é HTTPS. Sessão de 12h.
- Todo SQL é **parametrizado** (`%s`) — nenhuma f-string de query.
- O CNPJ é reduzido a dígitos antes de ir para a `api-master`, cujo BLL monta o
  SQL por interpolação sem quotes (`cnpj_cpf={}`).
- Segredos só por variável de ambiente.
- `MAX_CONTENT_LENGTH` limita o upload; `413` tem resposta própria.

## Risco de plataforma

Baileys é WhatsApp **não oficial**. Enviando para a base de clientes *dos seus
clientes*, é o número **deles** que é banido em caso de abuso. O rate-limit de
1 msg/s e a digitação simulada reduzem o risco, mas não o eliminam: vale deixar
isso explícito no produto (opt-in, limite de disparo), não só no código.
