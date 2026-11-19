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
    stop_reason TEXT,                   -- da resposta ('tool_use', 'max_tokens'...); null se não
                                        -- houve resposta (pedido do lote com errored ou expired)
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
    versao_esquema INTEGER NOT NULL,    -- versão dos campos com que foi extraído
    -- O resultado da extração, gravado por ela:
    extracao TEXT NOT NULL,             -- 'valido' (passou no esquema e nas regras) ou 'reprovado'
                                        -- (continuou com erro depois das tentativas)
    erros TEXT NOT NULL,                -- JSON: os erros que sobraram, [{campo, mensagem}] ([] no
                                        -- válido). O campo pode não ser do produto: 'categoria' e
                                        -- 'categoria_detalhe' (da classificação), 'max_tokens'
                                        -- (cortada), 'tool_use' e 'lote' (sem resposta)
    -- O destino, gravado pelo roteamento (route.py). Uma nova extração volta o destino para null.
    destino TEXT,                       -- null até o roteamento; depois 'catalogo' ou 'revisao'. A
                                        -- decisão humana troca 'revisao' por 'catalogo' (aprovado)
                                        -- ou 'rejeitado', e o histórico fica em `revisoes`
    motivos TEXT NOT NULL DEFAULT '[]', -- JSON: por que o roteamento mandou para a revisão
                                        -- ('reprovado', 'duvida_baixa', 'auditoria'); [] no
                                        -- catálogo. Não muda com a decisão humana
    auditoria INTEGER NOT NULL DEFAULT 0,  -- 1 se já caiu no sorteio da auditoria: não entra em
                                           -- outro sorteio, nem depois de uma nova extração
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

-- Os lotes enviados à API, para retomar um que ainda não terminou. Um lote pode misturar as
-- etapas: o custom_id ('f042-extract-1') diz a ficha, a etapa e a tentativa de cada pedido.
CREATE TABLE IF NOT EXISTS lotes (
    id TEXT PRIMARY KEY,                -- id do lote na API
    rodada_id INTEGER NOT NULL REFERENCES rodadas (id),
    custom_ids TEXT NOT NULL,           -- JSON: lista dos custom_id enviados
    -- 'pendente' até os resultados serem salvos; depois 'concluido'. 'perdido' se a API não tem
    -- mais o lote (404): os pedidos dele vão de novo num lote novo.
    status TEXT NOT NULL,
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
    stop_reason: str | None,
    erro: str | None,
    usage,
    custo: float,
) -> None:
    """Guarda uma chamada ao modelo. `usage` é o `message.usage` da resposta."""
    conn.execute(
        "INSERT INTO tentativas (ficha_id, rodada_id, etapa, numero, resposta, stop_reason, erro, "
        "tokens_entrada, tokens_saida, tokens_cache_escrita, tokens_cache_leitura, custo) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            ficha_id,
            rodada_id,
            etapa,
            numero,
            None if resposta is None else json.dumps(resposta, ensure_ascii=False),
            stop_reason,
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
    extracao: str,
    erros: list,
) -> None:
    """Grava o produto da ficha (ou troca o que já existia) e tira a ficha das pendentes.

    O produto fica sem destino, à espera do roteamento. `auditoria` não muda: quem já foi
    sorteado não é sorteado de novo.

    `ON CONFLICT ... DO UPDATE` atualiza a linha no lugar. `INSERT OR REPLACE` apagaria a linha, e
    com as revisões apontando para ela a chave estrangeira falharia.
    """
    conn.execute(
        "INSERT INTO produtos (ficha_id, categoria, campos, duvidas, versao_esquema, extracao, "
        "erros) VALUES (?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT (ficha_id) DO UPDATE SET categoria = excluded.categoria, "
        "campos = excluded.campos, duvidas = excluded.duvidas, "
        "versao_esquema = excluded.versao_esquema, extracao = excluded.extracao, "
        "erros = excluded.erros, destino = NULL, motivos = '[]', "
        "atualizado_em = CURRENT_TIMESTAMP",
        (
            ficha_id,
            categoria,
            json.dumps(campos, ensure_ascii=False),
            json.dumps(duvidas, ensure_ascii=False),
            SCHEMA_VERSION,
            extracao,
            json.dumps(erros, ensure_ascii=False),
        ),
    )
    conn.execute("UPDATE fichas SET status = 'processada' WHERE id = ?", (ficha_id,))


# --- Lotes.


def round_attempts(conn: sqlite3.Connection, rodada_id: int, ficha_id: int) -> list[sqlite3.Row]:
    """As tentativas de uma ficha numa rodada, na ordem em que foram feitas."""
    return conn.execute(
        "SELECT * FROM tentativas WHERE rodada_id = ? AND ficha_id = ? ORDER BY id",
        (rodada_id, ficha_id),
    ).fetchall()


def add_batch(conn: sqlite3.Connection, lote_id: str, rodada_id: int, custom_ids: list) -> None:
    """Guarda um lote recém-enviado, como 'pendente'."""
    with conn:
        conn.execute(
            "INSERT INTO lotes (id, rodada_id, custom_ids, status) VALUES (?, ?, ?, 'pendente')",
            (lote_id, rodada_id, json.dumps(custom_ids)),
        )


def set_batch_status(conn: sqlite3.Connection, lote_id: str, status: str) -> None:
    """Troca o status do lote. Não faz commit: vai junto com os resultados que foram salvos."""
    conn.execute("UPDATE lotes SET status = ? WHERE id = ?", (status, lote_id))


def pending_batch(conn: sqlite3.Connection, rodada_id: int) -> str | None:
    """O id do lote da rodada que foi enviado e ainda não teve os resultados salvos, ou None."""
    row = conn.execute(
        "SELECT id FROM lotes WHERE rodada_id = ? AND status = 'pendente'", (rodada_id,)
    ).fetchone()
    return None if row is None else row["id"]


def last_batch_round(conn: sqlite3.Connection) -> tuple[int | None, list[str]]:
    """A última rodada que enviou lotes e todos os custom_id enviados nela."""
    rows = conn.execute(
        "SELECT rodada_id, custom_ids FROM lotes "
        "WHERE rodada_id = (SELECT MAX(rodada_id) FROM lotes) ORDER BY rowid"
    ).fetchall()
    if not rows:
        return None, []
    return rows[0]["rodada_id"], [cid for row in rows for cid in json.loads(row["custom_ids"])]


def round_totals(conn: sqlite3.Connection, rodada_id: int) -> sqlite3.Row:
    """Os números de uma rodada: fichas, válidas, reprovadas, chamadas, novas tentativas e custo."""
    return conn.execute(
        "SELECT COUNT(DISTINCT t.ficha_id) AS fichas, "
        "COUNT(DISTINCT CASE WHEN p.extracao = 'valido' THEN t.ficha_id END) AS validas, "
        "COUNT(DISTINCT CASE WHEN p.extracao = 'reprovado' THEN t.ficha_id END) AS reprovadas, "
        "COUNT(*) AS chamadas, TOTAL(t.numero > 1) AS novas_tentativas, TOTAL(t.custo) AS custo "
        "FROM tentativas t LEFT JOIN produtos p ON p.ficha_id = t.ficha_id WHERE t.rodada_id = ?",
        (rodada_id,),
    ).fetchone()


def rounds(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """As rodadas, da primeira à última: id, modo e quando começou (hora local)."""
    return conn.execute(
        "SELECT id, modo, datetime(criado_em, 'localtime') AS criado_em FROM rodadas ORDER BY id"
    ).fetchall()


# --- Roteamento.


def unrouted_products(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Os produtos ainda sem destino (os que acabaram de ser extraídos), na ordem das fichas."""
    return conn.execute(
        "SELECT ficha_id, categoria, duvidas, extracao, auditoria FROM produtos "
        "WHERE destino IS NULL ORDER BY ficha_id"
    ).fetchall()


def set_route(
    conn: sqlite3.Connection, ficha_id: int, destino: str, motivos: list, audited: bool
) -> None:
    """Grava o destino e os motivos de um produto, sem commit. A auditoria só passa de 0 para 1."""
    conn.execute(
        "UPDATE produtos SET destino = ?, motivos = ?, auditoria = MAX(auditoria, ?) "
        "WHERE ficha_id = ?",
        (destino, json.dumps(motivos), int(audited), ficha_id),
    )


def product_routes(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """O roteamento de todos os produtos (categoria, destino, motivos e auditoria), para as
    contagens e para a cota da auditoria."""
    return conn.execute(
        "SELECT ficha_id, categoria, destino, motivos, auditoria FROM produtos"
    ).fetchall()


# --- Avaliação.


def evaluated_products(conn: sqlite3.Connection, conjunto: str) -> list[sqlite3.Row]:
    """Os produtos das fichas de um conjunto, com a última resposta da extração.

    A avaliação mede o modelo: a resposta dele fica em `tentativas`, e `produtos.campos` pode ter
    sido editado na revisão humana. A categoria e os motivos do roteamento vêm do produto.
    """
    return conn.execute(
        "SELECT f.id_origem, p.categoria, p.destino, p.motivos, t.resposta "
        "FROM fichas f JOIN produtos p ON p.ficha_id = f.id "
        "LEFT JOIN tentativas t ON t.id = ("
        "  SELECT MAX(id) FROM tentativas WHERE ficha_id = f.id AND etapa = 'extract') "
        "WHERE f.conjunto = ? ORDER BY f.id",
        (conjunto,),
    ).fetchall()


def costs_by_mode(conn: sqlite3.Connection, conjunto: str) -> list[sqlite3.Row]:
    """O custo das fichas de um conjunto, por modo: fichas processadas (a mesma ficha em duas
    rodadas conta duas vezes), chamadas, custo e o dia (hora local) da última chamada."""
    return conn.execute(
        "SELECT r.modo, COUNT(DISTINCT t.rodada_id || '-' || t.ficha_id) AS fichas, "
        "COUNT(*) AS chamadas, TOTAL(t.custo) AS custo, "
        "date(MAX(t.criado_em), 'localtime') AS dia "
        "FROM tentativas t JOIN rodadas r ON r.id = t.rodada_id "
        "JOIN fichas f ON f.id = t.ficha_id WHERE f.conjunto = ? GROUP BY r.modo ORDER BY r.modo",
        (conjunto,),
    ).fetchall()
