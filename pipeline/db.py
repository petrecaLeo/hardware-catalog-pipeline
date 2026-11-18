"""Banco SQLite do pipeline (data/catalog.db, fora do git).

Uma ficha vira no máximo um produto, e o produto tem o id da ficha. Listas e objetos ficam em
colunas TEXT, como JSON. Grave sempre dentro de `with conn:`, que faz o commit ou desfaz tudo.
"""

import hashlib
import json
import sqlite3
from pathlib import Path

from pipeline.config import DB_FILE
from pipeline.schemas import SCHEMA_VERSION

SCHEMA = """
CREATE TABLE IF NOT EXISTS fichas (
    id INTEGER PRIMARY KEY,
    hash TEXT NOT NULL UNIQUE,          -- sha256 do texto: identifica a ficha
    texto TEXT NOT NULL,
    conjunto TEXT NOT NULL,             -- 'dev' ou 'holdout' (com gabarito no jsonl) ou 'inbox'
    id_origem TEXT NOT NULL,            -- id no jsonl ('dev-001') ou nome do .txt
    status TEXT NOT NULL DEFAULT 'pendente',  -- 'pendente' até ser processada; depois 'processada'
    criado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Cada execução do processamento. Um lote retomado continua a rodada em que foi criado.
CREATE TABLE IF NOT EXISTS rodadas (
    id INTEGER PRIMARY KEY,
    modo TEXT NOT NULL,                 -- 'direct' ou 'batch' (as chaves de config.PRICES)
    criado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Cada chamada ao modelo.
CREATE TABLE IF NOT EXISTS tentativas (
    id INTEGER PRIMARY KEY,
    ficha_id INTEGER NOT NULL REFERENCES fichas (id),
    rodada_id INTEGER NOT NULL REFERENCES rodadas (id),
    etapa TEXT NOT NULL,                -- 'classify' ou 'extract'
    numero INTEGER NOT NULL,            -- 1 = primeira resposta; depois, as correções
    resposta TEXT,                      -- JSON: o input da ferramenta (null se a chamada falhou)
    erro TEXT,                          -- erro de validação ou da API (null se passou)
    tokens_entrada INTEGER NOT NULL,    -- só a parte sem cache; 0 se a chamada falhou sem resposta
    tokens_saida INTEGER NOT NULL,
    tokens_cache_escrita INTEGER NOT NULL DEFAULT 0,  -- entrada gravada no cache (1,25× o preço)
    tokens_cache_leitura INTEGER NOT NULL DEFAULT 0,  -- entrada lida do cache (0,1× o preço)
    custo REAL NOT NULL,                -- US$, já com o preço do modo da rodada
    criado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS produtos (
    ficha_id INTEGER PRIMARY KEY REFERENCES fichas (id),
    categoria TEXT NOT NULL,
    campos TEXT NOT NULL,               -- JSON: os campos de PRODUCT_MODELS[categoria]
    duvidas TEXT NOT NULL,              -- JSON: [{campo, nivel, motivo}]
    -- Depois da extração: 'valido' (passou no esquema e nas regras) ou 'reprovado' (continuou com
    -- erro depois das tentativas). O roteamento troca por catálogo, fila de revisão ou rejeitado.
    status TEXT NOT NULL,
    motivos TEXT NOT NULL,              -- JSON: por que foi para a revisão ([] se não foi); no
                                        -- 'reprovado', os erros [{campo, mensagem}] que sobraram
    auditoria INTEGER NOT NULL DEFAULT 0,  -- 1 se caiu no sorteio da auditoria
    versao_esquema INTEGER NOT NULL,    -- versão dos campos com que foi extraído
    atualizado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Cada decisão do humano na tela.
CREATE TABLE IF NOT EXISTS revisoes (
    id INTEGER PRIMARY KEY,
    ficha_id INTEGER NOT NULL REFERENCES produtos (ficha_id),
    acao TEXT NOT NULL,                 -- aprovar ou rejeitar
    antes TEXT NOT NULL,                -- JSON: os campos antes
    depois TEXT,                        -- JSON: os campos depois (null se não mudou nada)
    criado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Os lotes enviados à API, para retomar um que ainda não terminou.
CREATE TABLE IF NOT EXISTS lotes (
    id TEXT PRIMARY KEY,                -- id do lote na API
    rodada_id INTEGER NOT NULL REFERENCES rodadas (id),
    etapa TEXT NOT NULL,                -- 'classify' ou 'extract'
    custom_ids TEXT NOT NULL,           -- JSON: lista dos custom_id enviados
    status TEXT NOT NULL,               -- pendente até os resultados serem salvos
    criado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


def connect(path: Path | str = DB_FILE) -> sqlite3.Connection:
    """Abre o banco e cria a pasta e as tabelas que faltarem. Os testes usam um banco temporário.

    Na API, cada pedido abre a sua conexão e fecha no fim. O FastAPI pode rodar a dependência e o
    endpoint em threads diferentes, por isso `check_same_thread=False`. Não divida uma conexão
    entre pedidos.
    """
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    return conn


def add_sheets(conn: sqlite3.Connection, conjunto: str, sheets: list[tuple[str, str]]) -> int:
    """Guarda as fichas `(id_origem, texto)` que ainda não estão no banco e diz quantas eram novas.

    O texto é guardado sem os espaços das pontas, e o hash dele identifica a ficha: o mesmo texto,
    vindo de outro arquivo, conta como ficha que já existia e fica com o conjunto da primeira vez.
    """
    rows = []
    for id_origem, texto in sheets:
        texto = texto.strip()
        rows.append((hashlib.sha256(texto.encode()).hexdigest(), texto, conjunto, id_origem))
    with conn:
        cursor = conn.executemany(
            "INSERT INTO fichas (hash, texto, conjunto, id_origem) VALUES (?, ?, ?, ?) "
            "ON CONFLICT (hash) DO NOTHING",
            rows,
        )
    return cursor.rowcount


def pending_sheets(
    conn: sqlite3.Connection, conjunto: str | None = None, limit: int | None = None
) -> list[sqlite3.Row]:
    """As fichas ainda não processadas, na ordem em que entraram no banco."""
    sql = "SELECT id, id_origem, texto FROM fichas WHERE status = 'pendente'"
    params = []
    if conjunto is not None:
        sql += " AND conjunto = ?"
        params.append(conjunto)
    sql += " ORDER BY id"
    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)
    return conn.execute(sql, params).fetchall()


def start_round(conn: sqlite3.Connection, modo: str) -> int:
    """Cria a rodada de processamento ('direct' ou 'batch') e devolve o id dela."""
    with conn:
        return conn.execute("INSERT INTO rodadas (modo) VALUES (?)", (modo,)).lastrowid


# As duas funções abaixo não fazem commit: quem chama junta as tentativas e o produto de uma ficha
# num mesmo `with conn:`.


def add_attempt(
    conn: sqlite3.Connection,
    ficha_id: int,
    rodada_id: int,
    etapa: str,
    numero: int,
    resposta: dict | None,
    erro: str | None,
    usage,
    custo: float,
) -> None:
    """Guarda uma chamada ao modelo. `usage` é o `message.usage` da resposta."""
    conn.execute(
        "INSERT INTO tentativas (ficha_id, rodada_id, etapa, numero, resposta, erro, "
        "tokens_entrada, tokens_saida, tokens_cache_escrita, tokens_cache_leitura, custo) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            ficha_id,
            rodada_id,
            etapa,
            numero,
            None if resposta is None else json.dumps(resposta, ensure_ascii=False),
            erro,
            usage.input_tokens,
            usage.output_tokens,
            usage.cache_creation_input_tokens or 0,
            usage.cache_read_input_tokens or 0,
            custo,
        ),
    )


def save_product(
    conn: sqlite3.Connection,
    ficha_id: int,
    categoria: str,
    campos: dict,
    duvidas: list,
    status: str,
    motivos: list,
) -> None:
    """Grava o produto da ficha (ou troca o que já existia) e tira a ficha das pendentes.

    `ON CONFLICT ... DO UPDATE` atualiza a linha no lugar. `INSERT OR REPLACE` apagaria a linha, e
    com as revisões apontando para ela a chave estrangeira falharia.
    """
    conn.execute(
        "INSERT INTO produtos (ficha_id, categoria, campos, duvidas, status, motivos, "
        "versao_esquema) VALUES (?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT (ficha_id) DO UPDATE SET categoria = excluded.categoria, "
        "campos = excluded.campos, duvidas = excluded.duvidas, status = excluded.status, "
        "motivos = excluded.motivos, versao_esquema = excluded.versao_esquema, "
        "atualizado_em = CURRENT_TIMESTAMP",
        (
            ficha_id,
            categoria,
            json.dumps(campos, ensure_ascii=False),
            json.dumps(duvidas, ensure_ascii=False),
            status,
            json.dumps(motivos, ensure_ascii=False),
            SCHEMA_VERSION,
        ),
    )
    conn.execute("UPDATE fichas SET status = 'processada' WHERE id = ?", (ficha_id,))
