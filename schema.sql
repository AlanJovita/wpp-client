-- DDL do wpp-client — MariaDB (InnoDB, utf8mb4)
-- Convive no MESMO schema do wpp-premio, isolado pelo prefixo client_.
-- Aplicado de forma idempotente na subida (api/database.executar_schema).
--
-- Guarda só o indispensável: a instância conectada (para ficar 24h online) e as
-- configurações de saudação, horário e ausência. Mensagem de cliente NUNCA é
-- gravada aqui -- o webhook processa e descarta.

CREATE TABLE IF NOT EXISTS client_instancias (
    id INT UNSIGNED NOT NULL AUTO_INCREMENT,
    id_cliente INT UNSIGNED NOT NULL,
    cnpj VARCHAR(20) NOT NULL,
    empresa VARCHAR(150) NOT NULL DEFAULT '',
    nome_instancia VARCHAR(100) NOT NULL,
    numero VARCHAR(20) NULL,
    status ENUM('criando', 'conectando', 'conectada', 'desconectada') NOT NULL DEFAULT 'criando',
    token_api CHAR(48) NOT NULL,
    criado_em DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    atualizado_em DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_client_inst_cliente (id_cliente),
    UNIQUE KEY uq_client_inst_nome (nome_instancia),
    UNIQUE KEY uq_client_inst_token (token_api)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Configurações por cliente, uma linha só.
-- saudacao: JSON com ativo, mensagem e janela_horas
-- horario:  JSON com ativo, dias (0=seg..6=dom), inicio, fim,
--           intervalo_inicio, intervalo_fim e msg_fora -- mesmo formato do
--           wpp-premio (services/horario_service), NULL = sempre online
CREATE TABLE IF NOT EXISTS client_configuracoes (
    id_cliente INT UNSIGNED NOT NULL,
    saudacao JSON NULL,
    horario JSON NULL,
    atualizado_em DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id_cliente)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
