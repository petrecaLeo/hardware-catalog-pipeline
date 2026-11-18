import json
import re

import pytest

from pipeline.schemas import CATEGORIES, PRODUCT_MODELS
from pipeline.tools import CLASSIFY_TOOL, EXTRACT_TOOLS

# Limite do strict, somado por pedido. Cada pedido leva um molde só.
MAX_UNIONS = 16

TOOLS = [CLASSIFY_TOOL, *EXTRACT_TOOLS.values()]
TOOL_IDS = [tool["name"] for tool in TOOLS]


def nodes(schema):
    """Todos os nós do schema: ele mesmo, propriedades, itens, variantes e $defs."""
    yield schema
    for child in schema.get("properties", {}).values():
        yield from nodes(child)
    if "items" in schema:
        yield from nodes(schema["items"])
    for variant in schema.get("anyOf", []):
        yield from nodes(variant)
    for definition in schema.get("$defs", {}).values():
        yield from nodes(definition)


def objects(schema):
    return [node for node in nodes(schema) if node.get("type") == "object"]


def count_unions(schema):
    # anyOf ou lista de tipos, como ["number", "null"].
    return sum("anyOf" in node or isinstance(node.get("type"), list) for node in nodes(schema))


@pytest.mark.parametrize("tool", TOOLS, ids=TOOL_IDS)
def test_every_object_is_closed_and_has_no_optional_field(tool):
    assert tool["strict"] is True
    for node in objects(tool["input_schema"]):
        assert node["additionalProperties"] is False
        assert node["required"] == list(node["properties"])


@pytest.mark.parametrize("tool", TOOLS, ids=TOOL_IDS)
def test_unions_fit_the_limit(tool):
    assert count_unions(tool["input_schema"]) <= MAX_UNIONS


@pytest.mark.parametrize("tool", TOOLS, ids=TOOL_IDS)
def test_every_field_has_a_description(tool):
    for node in objects(tool["input_schema"]):
        for name, field in node["properties"].items():
            assert field.get("description", "").strip(), name


@pytest.mark.parametrize("tool", TOOLS, ids=TOOL_IDS)
def test_enum_values_are_lowercase_with_underscores(tool):
    for node in nodes(tool["input_schema"]):
        for value in node.get("enum", []):
            assert re.fullmatch(r"[a-z0-9_]+", value), value


@pytest.mark.parametrize("category", CATEGORIES)
def test_extraction_has_the_category_fields_and_doubts_last(category):
    # Também garante que tirar os "title" não levou nenhum campo junto.
    schema = EXTRACT_TOOLS[category]["input_schema"]
    fields = list(PRODUCT_MODELS[category].model_fields)
    assert list(schema["properties"]) == [*fields, "duvidas"]

    ref = schema["properties"]["duvidas"]["items"]["$ref"]
    doubt = schema["$defs"][ref.removeprefix("#/$defs/")]
    assert doubt["properties"]["campo"]["enum"] == fields
    assert doubt["properties"]["nivel"]["enum"] == ["media", "baixa"]


def test_classification_lists_every_category():
    properties = CLASSIFY_TOOL["input_schema"]["properties"]
    assert list(properties) == ["categoria", "categoria_detalhe"]
    assert properties["categoria"]["enum"] == list(CATEGORIES)


def test_tools_are_plain_json():
    # A ida e volta pelo JSON pega o que não é JSON puro (tupla, tipo do Python, objeto).
    assert json.loads(json.dumps(TOOLS)) == TOOLS
