"""Leitura das fichas com gabarito (data/<conjunto>/fichas.jsonl).

Cada linha é uma ficha: `id`, `texto`, `estilo`, `pegadinha` (o que a ficha testa, ou null) e
`gabarito`. O gabarito é o que o pipeline deve extrair: `categoria`, `categoria_detalhe` e os
campos de `PRODUCT_MODELS[categoria]`.
"""

import json
from pathlib import Path

from pipeline.config import DATA_DIR
from pipeline.rules import Issue, validate_classification, validate_product

# Os conjuntos com gabarito: o de desenvolvimento e o de prova.
SETS = ("dev", "holdout")
# O estilo de uma ficha da inbox que virou caso pela correção humana (cli.py export-corrections):
# ninguém marcou como ela foi escrita.
UNKNOWN_STYLE = "desconhecido"


def sheets_path(conjunto: str) -> Path:
    """O arquivo das fichas com gabarito de um conjunto."""
    return DATA_DIR / conjunto / "fichas.jsonl"


def load_sheets(path: Path) -> list[dict]:
    """As fichas do arquivo, na ordem em que estão."""
    with open(path, encoding="utf-8") as file:
        return [json.loads(line) for line in file if line.strip()]


def split_answer_key(answer_key: dict) -> tuple[dict, dict]:
    """O gabarito separado em (classificação, campos do produto).

    `categoria_detalhe` é da classificação e, no 'outro', também é campo do produto.
    """
    classification = {key: answer_key[key] for key in ("categoria", "categoria_detalhe")}
    product = {key: value for key, value in answer_key.items() if key not in classification}
    if answer_key["categoria"] == "outro":
        product["categoria_detalhe"] = answer_key["categoria_detalhe"]
    return classification, product


def join_answer_key(categoria: str, campos: dict) -> dict:
    """O gabarito de um produto: o contrário de `split_answer_key`."""
    detail = campos["categoria_detalhe"] if categoria == "outro" else None
    product = {key: value for key, value in campos.items() if key != "categoria_detalhe"}
    return {"categoria": categoria, "categoria_detalhe": detail, **product}


def answer_key_issues(sheet: dict) -> list[Issue]:
    """Os erros do gabarito de uma ficha: a classificação, o esquema e as regras da categoria, com
    o texto (ficha só em dólar tem preço null). Lista vazia = gabarito válido."""
    classification, product = split_answer_key(sheet["gabarito"])
    return validate_classification(classification) + validate_product(
        classification["categoria"], product, sheet["texto"]
    )
