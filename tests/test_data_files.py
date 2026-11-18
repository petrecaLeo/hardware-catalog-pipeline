import re
from collections import Counter

import pytest

from pipeline.config import DATA_DIR
from pipeline.rules import validate_classification, validate_product
from pipeline.schemas import CATEGORIES
from pipeline.sheets import load_sheets, split_answer_key

SETS = ["dev"]

STYLES = (
    "anuncio_informal",
    "tabela_colada",
    "ingles",
    "email_fornecedor",
    "ficha_incompleta",
    "unidades_misturadas",
    "caixa_alta_emoji",
    "pdf_colado",
    "kit",
    "preco_dolar",
    "de_por",
    "parcelado",
)
MIN_PER_CATEGORY = 4
MIN_PER_STYLE = 3

# Marcas que as fichas não usam: as dos exemplos dos testes e as de dados anteriores.
FORBIDDEN_BRANDS = (
    "Ferronix", "Brisalta", "Murak", "Tecvana", "Zentrik", "Mosaico", "Ventisca", "Linkora",
    "Gigantec", "Voltrix", "Conecta+", "Lumina", "Hyperion", "Nebula", "Atlas", "Frost",
    "Vortex", "Orion", "Nimbus", "Glacier", "Titan", "Vega Storm", "Kestrel", "Hubtron",
    "Circuito", "Aurora", "Pulse", "Vetor",
)
# Palavra inteira, como a marca é escrita ou em caixa alta ("Circuito", "CIRCUITO"), e não
# depois de hífen: assim "Titanium" e "curto-circuito" (ou "CURTO-CIRCUITO") não contam.
FORBIDDEN = re.compile(
    r"(?<![\w-])("
    + "|".join(re.escape(form) for brand in FORBIDDEN_BRANDS for form in (brand, brand.upper()))
    + r")(?!\w)"
)
FIELDS = {"id", "texto", "estilo", "pegadinha", "gabarito"}


@pytest.fixture(params=SETS)
def sheets(request):
    return load_sheets(DATA_DIR / request.param / "fichas.jsonl")


def test_sheets_have_the_expected_fields(sheets):
    assert [sheet["id"] for sheet in sheets if set(sheet) != FIELDS] == []


def test_ids_are_unique(sheets):
    ids = Counter(sheet["id"] for sheet in sheets)
    assert [sheet_id for sheet_id, count in ids.items() if count > 1] == []


def test_answer_keys_pass_schema_and_rules(sheets):
    # Com o texto: ficha só em dólar tem preço null no gabarito.
    failures = {}
    for sheet in sheets:
        classification, product = split_answer_key(sheet["gabarito"])
        issues = validate_classification(classification) + validate_product(
            classification["categoria"], product, sheet["texto"]
        )
        if issues:
            failures[sheet["id"]] = [issue.message for issue in issues]
    assert failures == {}


def test_every_category_has_enough_sheets(sheets):
    counts = Counter(sheet["gabarito"]["categoria"] for sheet in sheets)
    assert {cat: counts[cat] for cat in CATEGORIES if counts[cat] < MIN_PER_CATEGORY} == {}


def test_every_style_has_enough_sheets(sheets):
    counts = Counter(sheet["estilo"] for sheet in sheets)
    assert set(counts) <= set(STYLES)
    assert {style: counts[style] for style in STYLES if counts[style] < MIN_PER_STYLE} == {}


def test_no_forbidden_brand(sheets):
    found = {}
    for sheet in sheets:
        text = " ".join([sheet["texto"], sheet["pegadinha"] or "", str(sheet["gabarito"])])
        if match := FORBIDDEN.search(text):
            found[sheet["id"]] = match.group()
    assert found == {}
