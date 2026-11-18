"""Leitura das fichas com gabarito (data/<conjunto>/fichas.jsonl).

Cada linha é uma ficha: `id`, `texto`, `estilo`, `pegadinha` (o que a ficha testa, ou null) e
`gabarito`. O gabarito é o que o pipeline deve extrair: `categoria`, `categoria_detalhe` e os
campos de `PRODUCT_MODELS[categoria]`.
"""

import json
from pathlib import Path


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
