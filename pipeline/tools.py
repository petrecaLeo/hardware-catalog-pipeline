"""Moldes das ferramentas strict, gerados dos modelos de schemas.py.

Cada pedido leva um molde só: o de classificação ou o de extração de uma categoria. Os limites
do strict são somados por pedido (16 campos com união, 24 opcionais), então valem para cada
molde sozinho.

Uso: `CLASSIFY_TOOL` e `EXTRACT_TOOLS["fonte"]`.
"""

from anthropic import transform_schema

from pipeline.schemas import MODELS, Classification


def _drop_titles(node):
    # O Pydantic põe um "title" em cada campo ("Tdp W", "Clock Boost Ghz"). O modelo não precisa
    # dele, e ele custa uns 12% do molde em todo pedido. Nenhum campo se chama "title".
    if isinstance(node, dict):
        return {key: _drop_titles(value) for key, value in node.items() if key != "title"}
    if isinstance(node, list):
        return [_drop_titles(value) for value in node]
    return node


def _input_schema(model) -> dict:
    return _drop_titles(transform_schema(model))


CLASSIFY_TOOL = {
    "name": "classificar_produto",
    "description": "Registra a categoria do produto descrito numa ficha técnica de fornecedor.",
    "strict": True,
    "input_schema": _input_schema(Classification),
}

EXTRACT_TOOLS = {
    category: {
        "name": f"extrair_{category}",
        "description": (
            f"Registra os dados de um produto da categoria '{category}' tirados de uma ficha "
            "técnica de fornecedor. Preencha só com o que está escrito no texto. Quando uma "
            "informação não aparece, use null: não estime nem complete com o que você sabe sobre "
            "o produto. Nos campos true/false, use false só quando o texto diz que não tem."
        ),
        "strict": True,
        "input_schema": _input_schema(model),
    }
    for category, model in MODELS.items()
}
