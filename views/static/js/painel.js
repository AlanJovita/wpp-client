/* Painel do wpp-client.
 *
 * Sem framework e sem WebSocket: o estado da conexão e o QR vêm por polling de
 * GET /api/instancia, que só roda enquanto a instância NÃO está conectada.
 * Conectada, o polling para — nada de requisição de fundo para sempre. */

(function () {
    "use strict";

    const INTERVALO_POLL_MS = 3000;

    const el = (id) => document.getElementById(id);

    const seloStatus = el("selo-status");
    const textoConexao = el("texto-conexao");
    const numeroEl = el("numero");
    const qrArea = el("qr-area");
    const qrSlot = el("qr-slot");

    let temporizador = null;
    let tokenVisivel = false;

    // --------------------------------------------------------------- avisos

    function avisar(idAviso, mensagem, tipo) {
        const alvo = el(idAviso);
        if (!alvo) return;
        alvo.textContent = mensagem;
        alvo.className = "aviso " + (tipo || "neutro") + (mensagem ? " visivel" : "");
    }

    function ocupado(botao, estaOcupado, rotuloOcupado) {
        if (!botao) return;
        if (estaOcupado) {
            botao.dataset.rotulo = botao.textContent;
            botao.textContent = rotuloOcupado || "Aguarde…";
            botao.disabled = true;
        } else {
            if (botao.dataset.rotulo) botao.textContent = botao.dataset.rotulo;
            botao.disabled = false;
        }
    }

    // ----------------------------------------------------------- requisições

    async function chamar(caminho, metodo, corpo) {
        const opcoes = { method: metodo || "GET", headers: {} };
        if (corpo instanceof FormData) {
            opcoes.body = corpo;
        } else if (corpo !== undefined) {
            opcoes.headers["Content-Type"] = "application/json";
            opcoes.body = JSON.stringify(corpo);
        }

        const resposta = await fetch(caminho, opcoes);
        if (resposta.status === 401) {
            window.location.href = "/login";
            throw new Error("Sessão expirada");
        }

        let dados = {};
        try {
            dados = await resposta.json();
        } catch (_) {
            throw new Error("Resposta inesperada do servidor");
        }
        if (!resposta.ok || dados.sucesso === false) {
            throw new Error(dados.mensagem || "Não foi possível concluir a ação");
        }
        return dados.dados || {};
    }

    // ------------------------------------------------------------ renderização

    const ROTULOS = {
        conectada: "conectada",
        conectando: "conectando",
        criando: "criando",
        desconectada: "desconectada",
        ausente: "sem número"
    };

    function mostrarQr(base64) {
        if (!base64) {
            qrArea.hidden = true;
            qrSlot.textContent = "";
            return;
        }
        let img = qrSlot.firstElementChild;
        if (!img) {
            img = document.createElement("img");
            img.alt = "QR Code para conectar o WhatsApp";
            qrSlot.appendChild(img);
        }
        // a Evolution manda com o prefixo data: em algumas builds e sem ele noutras
        img.src = base64.startsWith("data:") ? base64 : "data:image/png;base64," + base64;
        qrArea.hidden = false;
    }

    function alternarConfiguracoes(conectada) {
        ["cartao-saudacao", "cartao-horario", "cartao-status", "cartao-api"]
            .forEach((id) => el(id) && el(id).classList.toggle("oculto", !conectada));
    }

    function renderizar(estado) {
        const instancia = estado.instancia;
        const status = instancia ? instancia.status : "ausente";
        const conectada = status === "conectada";

        if (seloStatus) {
            seloStatus.textContent = ROTULOS[status] || status;
            seloStatus.className = "selo " + (instancia ? status : "");
        }

        if (numeroEl) {
            const numero = instancia && instancia.numero;
            numeroEl.textContent = numero ? formatarNumero(numero) : "";
            numeroEl.hidden = !numero;
        }

        if (textoConexao) {
            textoConexao.textContent = conectada
                ? "Seu WhatsApp está conectado e respondendo."
                : "Leia o QR Code para conectar o WhatsApp da empresa.";
        }

        if (el("btn-conectar")) el("btn-conectar").hidden = conectada;
        if (el("btn-desconectar")) el("btn-desconectar").hidden = !conectada;

        mostrarQr(conectada ? null : estado.qrcode);
        alternarConfiguracoes(conectada);

        if (conectada) {
            pararPolling();
            avisar("aviso-conexao", "", "ok");
        }
    }

    function formatarNumero(numero) {
        // 5544999998888 → +55 44 99999-8888
        const d = String(numero).replace(/\D/g, "");
        if (d.length < 12) return numero;
        const ddi = d.slice(0, 2), ddd = d.slice(2, 4), resto = d.slice(4);
        const meio = resto.length > 8 ? resto.slice(0, 5) : resto.slice(0, 4);
        return `+${ddi} ${ddd} ${meio}-${resto.slice(meio.length)}`;
    }

    // ---------------------------------------------------------------- polling

    async function atualizar() {
        try {
            renderizar(await chamar("/api/instancia"));
        } catch (erro) {
            console.warn("Status indisponível:", erro.message);
        }
    }

    function iniciarPolling() {
        if (temporizador) return;
        temporizador = setInterval(atualizar, INTERVALO_POLL_MS);
    }

    function pararPolling() {
        if (temporizador) {
            clearInterval(temporizador);
            temporizador = null;
        }
    }

    // o QR expira; com a aba em segundo plano não faz sentido continuar pedindo
    document.addEventListener("visibilitychange", () => {
        if (document.hidden) {
            pararPolling();
        } else if (window.ESTADO_INICIAL.status !== "conectada") {
            atualizar();
            iniciarPolling();
        }
    });

    // ----------------------------------------------------------- conexão

    function ligar(id, manipulador) {
        const botao = el(id);
        if (botao) botao.addEventListener("click", manipulador);
    }

    ligar("btn-criar", async (ev) => {
        const botao = ev.currentTarget;
        ocupado(botao, true, "Criando…");
        avisar("aviso-conexao", "", "neutro");
        try {
            await chamar("/api/instancia", "POST");
            // esta tela foi renderizada sem instância, então não tem os botões
            // de conexão nem o bloco do número. Recarregar monta a página certa
            // e não custa o QR: ele fica no cache do servidor (TTL 90s) e sai
            // no próximo GET /api/instancia
            window.location.reload();
        } catch (erro) {
            avisar("aviso-conexao", erro.message, "erro");
            ocupado(botao, false);
        }
    });

    ligar("btn-conectar", async (ev) => {
        const botao = ev.currentTarget;
        ocupado(botao, true, "Gerando…");
        try {
            const dados = await chamar("/api/instancia/conectar", "POST");
            if (dados.qrcode) {
                mostrarQr(dados.qrcode);
                avisar("aviso-conexao", "Leia o QR Code no WhatsApp do seu celular.", "ok");
                iniciarPolling();
            } else {
                avisar("aviso-conexao", "Este número já está conectado.", "ok");
                atualizar();
            }
        } catch (erro) {
            avisar("aviso-conexao", erro.message, "erro");
        } finally {
            ocupado(botao, false);
        }
    });

    ligar("btn-reiniciar", async (ev) => {
        const botao = ev.currentTarget;
        ocupado(botao, true, "Reiniciando…");
        try {
            await chamar("/api/instancia/reiniciar", "POST");
            avisar("aviso-conexao", "Conexão reiniciada.", "ok");
            iniciarPolling();
        } catch (erro) {
            avisar("aviso-conexao", erro.message, "erro");
        } finally {
            ocupado(botao, false);
        }
    });

    ligar("btn-desconectar", async (ev) => {
        if (!window.confirm(
            "Desconectar o WhatsApp? As mensagens automáticas param de funcionar " +
            "até você ler um QR Code novo. Suas configurações são mantidas."
        )) return;

        const botao = ev.currentTarget;
        ocupado(botao, true, "Desconectando…");
        try {
            await chamar("/api/instancia/desconectar", "POST");
            avisar("aviso-conexao", "WhatsApp desconectado.", "neutro");
            window.ESTADO_INICIAL.status = "desconectada";
            await atualizar();
            iniciarPolling();
        } catch (erro) {
            avisar("aviso-conexao", erro.message, "erro");
        } finally {
            ocupado(botao, false);
        }
    });

    // ----------------------------------------------------------- saudação

    ligar("btn-salvar-saudacao", async (ev) => {
        const botao = ev.currentTarget;
        ocupado(botao, true, "Salvando…");
        try {
            await chamar("/api/configuracoes/saudacao", "PUT", {
                saudacao: {
                    ativo: el("saudacao-ativo").checked,
                    mensagem: el("saudacao-mensagem").value.trim(),
                    janela_horas: parseInt(el("saudacao-janela").value, 10) || 12
                }
            });
            avisar("aviso-saudacao", "Saudação salva.", "ok");
        } catch (erro) {
            avisar("aviso-saudacao", erro.message, "erro");
        } finally {
            ocupado(botao, false);
        }
    });

    // ------------------------------------------------------------- horário

    ligar("btn-salvar-horario", async (ev) => {
        const botao = ev.currentTarget;
        ocupado(botao, true, "Salvando…");
        try {
            const dados = await chamar("/api/configuracoes/horario", "PUT", {
                horario: {
                    ativo: el("horario-ativo").checked,
                    dias: Array.from(document.querySelectorAll("[data-dia]"))
                        .filter((c) => c.checked)
                        .map((c) => parseInt(c.dataset.dia, 10)),
                    inicio: el("horario-inicio").value,
                    fim: el("horario-fim").value,
                    intervalo_inicio: el("horario-intervalo-inicio").value || null,
                    intervalo_fim: el("horario-intervalo-fim").value || null,
                    msg_fora: el("horario-msg").value.trim()
                }
            });
            const situacao = dados.horario && dados.horario.ativo
                ? (dados.aberto_agora ? " Agora você está aberto." : " Agora você está fechado.")
                : "";
            avisar("aviso-horario", "Horário salvo." + situacao, "ok");
        } catch (erro) {
            avisar("aviso-horario", erro.message, "erro");
        } finally {
            ocupado(botao, false);
        }
    });

    // -------------------------------------------------------- publicar status

    const campoImagem = el("status-imagem");
    if (campoImagem) {
        campoImagem.addEventListener("change", () => {
            const arquivo = campoImagem.files[0];
            const previa = el("status-previa");
            const nome = el("status-arquivo-nome");

            if (!arquivo) {
                previa.style.display = "none";
                previa.textContent = "";
                return;
            }
            nome.innerHTML = "<strong>" + arquivo.name + "</strong> — trocar imagem";

            let img = previa.firstElementChild;
            if (!img) {
                img = document.createElement("img");
                img.alt = "Prévia do status";
                previa.appendChild(img);
            }
            // objectURL é liberado ao trocar/limpar para não vazar memória na aba
            if (img.dataset.url) URL.revokeObjectURL(img.dataset.url);
            img.dataset.url = URL.createObjectURL(arquivo);
            img.src = img.dataset.url;
            previa.style.display = "block";
        });
    }

    ligar("btn-publicar", async (ev) => {
        const arquivo = campoImagem && campoImagem.files[0];
        if (!arquivo) {
            avisar("aviso-status", "Escolha uma imagem para publicar.", "erro");
            return;
        }

        const botao = ev.currentTarget;
        ocupado(botao, true, "Publicando…");
        avisar("aviso-status", "Enviando para o WhatsApp…", "neutro");

        const formulario = new FormData();
        formulario.append("imagem", arquivo);
        formulario.append("legenda", el("status-legenda").value.trim());

        try {
            await chamar("/api/status", "POST", formulario);
            avisar("aviso-status", "Status publicado com sucesso.", "ok");
        } catch (erro) {
            avisar("aviso-status", "Não publicou: " + erro.message, "erro");
        } finally {
            ocupado(botao, false);
        }
    });

    // ------------------------------------------------------------- token

    async function carregarToken() {
        const dados = await chamar("/api/instancia/token");
        el("api-token").value = dados.token;
        tokenVisivel = true;
        el("btn-ver-token").textContent = "Ocultar";
    }

    ligar("btn-ver-token", async () => {
        if (tokenVisivel) {
            el("api-token").value = "••••••••••••••••••••••••";
            tokenVisivel = false;
            el("btn-ver-token").textContent = "Mostrar";
            return;
        }
        try {
            await carregarToken();
            avisar("aviso-api", "", "neutro");
        } catch (erro) {
            avisar("aviso-api", erro.message, "erro");
        }
    });

    ligar("btn-copiar-token", async () => {
        try {
            if (!tokenVisivel) await carregarToken();
            const valor = el("api-token").value;
            if (navigator.clipboard) {
                await navigator.clipboard.writeText(valor);
            } else {
                el("api-token").select();
                document.execCommand("copy");
            }
            avisar("aviso-api", "Token copiado.", "ok");
        } catch (erro) {
            avisar("aviso-api", "Não foi possível copiar: " + erro.message, "erro");
        }
    });

    ligar("btn-regerar-token", async (ev) => {
        if (!window.confirm(
            "Gerar um token novo? O token atual para de funcionar na hora e seu " +
            "sistema vai precisar ser atualizado."
        )) return;

        const botao = ev.currentTarget;
        ocupado(botao, true, "Gerando…");
        try {
            const dados = await chamar("/api/instancia/token", "POST");
            el("api-token").value = dados.token;
            tokenVisivel = true;
            el("btn-ver-token").textContent = "Ocultar";
            avisar("aviso-api", "Token novo gerado. Atualize seu sistema.", "ok");
        } catch (erro) {
            avisar("aviso-api", erro.message, "erro");
        } finally {
            ocupado(botao, false);
        }
    });

    // ---------------------------------------------------------------- início

    if (window.ESTADO_INICIAL.temInstancia && window.ESTADO_INICIAL.status !== "conectada") {
        atualizar();
        iniciarPolling();
    }
})();
