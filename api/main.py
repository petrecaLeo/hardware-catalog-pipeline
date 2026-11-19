"""API da revisão humana: a fila, a ficha com a extração, aprovar (com edições) ou rejeitar, o
catálogo e o resumo.

O `{id}` das rotas é o id da ficha no banco (`fichas.id`), que também é o id do produto. O
`id_origem` ('dev-030', 'fonte.txt') vem junto nas respostas só para mostrar: ele não é único.

A decisão do revisor só vale para um produto da fila (`destino = 'revisao'`; fora dela, 409):
aprovar troca o destino para 'catalogo', rejeitar troca para 'rejeitado'. Os motivos não mudam,
e cada decisão vira uma linha em `revisoes`. O catálogo é sempre `destino = 'catalogo'`.

Aprovar valida os campos que vão para o catálogo (os editados ou, sem edição, os de agora) com o
esquema da categoria e as regras, sem a regra do US$: o revisor pode saber o preço em reais de uma
ficha só em dólar. Inválido → 422 com `{"detail": [{campo, mensagem}]}`. Todo 422 sai nesse
formato, também o do pedido fora do formato. A categoria não muda: um produto classificado na
categoria errada é rejeitado.

Só esta máquina acessa: o servidor escuta em 127.0.0.1 e recusa outro Host (um site que se passa
por 127.0.0.1 com DNS rebinding manda o nome dele no Host). Os POST só aceitam JSON: um formulário
de outro site aberto no navegador manda um POST sem perguntar nada, mas um POST em JSON para outra
origem passa antes pela permissão do CORS, que esta API não dá.

Uso: `create_app(db_file)`. Cada pedido abre a sua conexão com o banco e fecha no fim.
"""

import json
from collections import Counter
from pathlib import Path
from types import NoneType
from typing import Literal, get_args, get_origin

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict
from starlette.middleware.trustedhost import TrustedHostMiddleware

from pipeline import db, route
from pipeline.config import DB_FILE
from pipeline.rules import validate_product
from pipeline.schemas import PRODUCT_MODELS

HOST = "127.0.0.1"
ALLOWED_HOSTS = [HOST, "localhost"]

# O tipo de cada campo no formulário. O esquema é strict: o número vai como número ("8" não vale).
FIELD_KINDS = {str: "texto", int: "inteiro", float: "numero", bool: "booleano"}


class Approval(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # O produto inteiro, como ficou na tela (todos os campos da categoria). Sem ele, aprova os
    # campos como estão.
    campos: dict | None = None


def form_fields(category: str) -> list[dict]:
    """Os campos do produto da categoria, na ordem do esquema, com o que a tela precisa para
    montar o formulário e mandar cada valor no tipo certo: o tipo, as opções (enum), se aceita
    null e a descrição (a mesma que orienta o modelo)."""
    fields = []
    for name, info in PRODUCT_MODELS[category].model_fields.items():
        args = get_args(info.annotation)
        nullable = NoneType in args
        [kind] = [arg for arg in args if arg is not NoneType] if nullable else [info.annotation]
        options = list(get_args(kind)) if get_origin(kind) is Literal else None
        fields.append(
            {
                "campo": name,
                "tipo": "opcao" if options else FIELD_KINDS[kind],
                "opcoes": options,
                "aceita_null": nullable,
                "descricao": info.description,
            }
        )
    return fields


def create_app(db_file: Path | str = DB_FILE) -> FastAPI:
    app = FastAPI(title="Revisão do catálogo")

    @app.middleware("http")
    async def json_posts_only(request: Request, call_next):
        content_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
        if request.method == "POST" and content_type != "application/json":
            return JSONResponse(
                {"detail": "Mande o pedido em JSON (Content-Type: application/json)."},
                status_code=415,
            )
        return await call_next(request)

    # Adicionado por último, roda antes de tudo: outro Host recebe 400. A porta não conta.
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=ALLOWED_HOSTS)

    @app.exception_handler(RequestValidationError)
    async def malformed_request(request: Request, error: RequestValidationError):
        # O corpo ou o id fora do formato (só com bug na tela) sai como os erros dos campos. No
        # lugar do campo vai o caminho do erro ('body.campos', 'path.ficha_id'), que nunca é um
        # campo do formulário.
        detail = []
        for item in error.errors():
            where = ".".join(str(part) for part in item["loc"])
            message = f"{where}: pedido fora do formato ({item['msg']})."
            detail.append({"campo": where, "mensagem": message})
        return JSONResponse({"detail": detail}, status_code=422)

    def connection():
        conn = db.connect(db_file)
        try:
            yield conn
        finally:
            conn.close()

    def find_product(conn, ficha_id: int):
        row = db.review_product(conn, ficha_id)
        if row is None:
            raise HTTPException(404, f"Ficha {ficha_id} não encontrada ou ainda não processada.")
        return row

    def not_in_queue(ficha_id: int) -> HTTPException:
        return HTTPException(409, f"A ficha {ficha_id} não está na fila de revisão.")

    def decide(conn, ficha_id: int, acao: str, depois: dict | None = None) -> dict:
        if not db.decide(conn, ficha_id, acao, depois):
            raise not_in_queue(ficha_id)
        return {"id": ficha_id, "destino": db.DECISIONS[acao]}

    @app.get("/api/queue")
    def queue(conn=Depends(connection)):
        return [
            {
                "id": row["id"],
                "id_origem": row["id_origem"],
                "categoria": row["categoria"],
                "nome": json.loads(row["campos"]).get("nome"),
                "motivos": json.loads(row["motivos"]),
            }
            for row in db.products_in(conn, "revisao")
        ]

    @app.get("/api/sheets/{ficha_id}")
    def sheet(ficha_id: int, conn=Depends(connection)):
        row = find_product(conn, ficha_id)
        return {
            "id": row["id"],
            "id_origem": row["id_origem"],
            "conjunto": row["conjunto"],
            "texto": row["texto"],
            "categoria": row["categoria"],
            "campos": json.loads(row["campos"]),
            "duvidas": json.loads(row["duvidas"]),
            "extracao": row["extracao"],
            "erros": json.loads(row["erros"]),
            "destino": row["destino"],
            "motivos": json.loads(row["motivos"]),
            "formulario": form_fields(row["categoria"]),
            "tentativas": [dict(attempt) for attempt in db.sheet_attempts(conn, ficha_id)],
        }

    @app.post("/api/sheets/{ficha_id}/approve")
    def approve(ficha_id: int, body: Approval | None = None, conn=Depends(connection)):
        row = find_product(conn, ficha_id)
        if row["destino"] != "revisao":
            raise not_in_queue(ficha_id)
        before = json.loads(row["campos"])
        after = before if body is None or body.campos is None else body.campos
        if issues := validate_product(row["categoria"], after, None):
            raise HTTPException(
                422, [{"campo": issue.field, "mensagem": issue.message} for issue in issues]
            )
        return decide(conn, ficha_id, "aprovar", None if after == before else after)

    @app.post("/api/sheets/{ficha_id}/reject")
    def reject(ficha_id: int, conn=Depends(connection)):
        find_product(conn, ficha_id)
        return decide(conn, ficha_id, "rejeitar")

    @app.get("/api/catalog")
    def catalog(conn=Depends(connection)):
        return [
            {
                "id": row["id"],
                "id_origem": row["id_origem"],
                "categoria": row["categoria"],
                "campos": json.loads(row["campos"]),
            }
            for row in db.products_in(conn, "catalogo")
        ]

    @app.get("/api/summary")
    def summary(conn=Depends(connection)):
        counts = sum(route.counts_by_category(conn).values(), Counter())
        rounds = db.rounds(conn)
        last_round = None
        if rounds:
            last, totals = rounds[-1], db.round_totals(conn, rounds[-1]["id"])
            last_round = {
                "id": last["id"],
                "modo": last["modo"],
                "criado_em": last["criado_em"],
                "fichas": totals["fichas"],
                "chamadas": totals["chamadas"],
                "custo": totals["custo"],
            }
        return {
            "catalogo": counts["catalogo"],
            "fila": counts["revisao"],
            "fila_por_motivo": {reason: counts[reason] for reason in route.REASONS},
            "rejeitados": counts["rejeitado"],
            "custo": {"ultima_rodada": last_round, "total": db.total_cost(conn)},
        }

    return app
