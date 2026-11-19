import re
from collections import Counter

import pytest

from pipeline.schemas import CATEGORIES
from pipeline.sheets import SETS, UNKNOWN_STYLE, answer_key_issues, load_sheets, sheets_path

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
# A prova é menor (cerca de 30 fichas), então pede menos fichas por categoria e por estilo.
MIN_PER_CATEGORY = {"dev": 4, "holdout": 3}
MIN_PER_STYLE = {"dev": 3, "holdout": 2}

# Marcas que as fichas não usam: as dos exemplos dos testes e as de dados anteriores.
FORBIDDEN_BRANDS = (
    "Ferronix", "Brisalta", "Murak", "Tecvana", "Zentrik", "Mosaico", "Ventisca", "Linkora",
    "Gigantec", "Voltrix", "Conecta+", "Lumina", "Hyperion", "Nebula", "Atlas", "Frost",
    "Vortex", "Orion", "Nimbus", "Glacier", "Titan", "Vega Storm", "Kestrel", "Hubtron",
    "Circuito", "Aurora", "Pulse", "Vetor",
)
# Os fabricantes reais (a marca dos processadores) podem aparecer nos dois conjuntos.
REAL_BRANDS = {"AMD", "Intel"}
FIELDS = {"id", "texto", "estilo", "pegadinha", "gabarito"}


def brand_pattern(brands):
    """Acha a marca como palavra inteira, como é escrita ou em caixa alta ("Circuito",
    "CIRCUITO"), e não depois de hífen: assim "Titanium" e "curto-circuito" não contam."""
    forms = (re.escape(form) for brand in brands for form in (brand, brand.upper()))
    return re.compile(r"(?<![\w-])(" + "|".join(forms) + r")(?!\w)")


FORBIDDEN = brand_pattern(FORBIDDEN_BRANDS)


def load(set_name):
    return load_sheets(sheets_path(set_name))


def full_text(sheet):
    return " ".join([sheet["texto"], sheet["pegadinha"] or "", str(sheet["gabarito"])])


def same_text(text):
    """O texto sem diferença de espaços e de caixa: duas fichas com ele são a mesma."""
    return " ".join(text.split()).casefold()


@pytest.fixture(params=SETS)
def set_name(request):
    return request.param


@pytest.fixture
def sheets(set_name):
    return load(set_name)


def test_sheets_have_the_expected_fields(sheets):
    assert [sheet["id"] for sheet in sheets if set(sheet) != FIELDS] == []


def test_ids_are_unique(sheets):
    ids = Counter(sheet["id"] for sheet in sheets)
    assert [sheet_id for sheet_id, count in ids.items() if count > 1] == []


def test_texts_are_unique(sheets):
    # O banco guarda cada texto uma vez (a ficha repetida nunca seria avaliada), e a API falsa
    # responde pelo texto. A correção de uma ficha que o dev já tem não é caso novo.
    texts = Counter(same_text(sheet["texto"]) for sheet in sheets)
    assert [sheet["id"] for sheet in sheets if texts[same_text(sheet["texto"])] > 1] == []


def test_answer_keys_pass_schema_and_rules(sheets):
    # Com o texto: ficha só em dólar tem preço null no gabarito.
    failures = {}
    for sheet in sheets:
        if issues := answer_key_issues(sheet):
            failures[sheet["id"]] = [issue.message for issue in issues]
    assert failures == {}


def test_every_category_has_enough_sheets(sheets, set_name):
    counts = Counter(sheet["gabarito"]["categoria"] for sheet in sheets)
    minimum = MIN_PER_CATEGORY[set_name]
    assert {cat: counts[cat] for cat in CATEGORIES if counts[cat] < minimum} == {}


def test_every_style_has_enough_sheets(sheets, set_name):
    counts = Counter(sheet["estilo"] for sheet in sheets)
    minimum = MIN_PER_STYLE[set_name]
    # Uma correção humana de ficha da inbox, colada no dev, entra com o estilo desconhecido. Na
    # prova, não: ela não recebe correções.
    assert set(counts) <= ({*STYLES, UNKNOWN_STYLE} if set_name == "dev" else set(STYLES))
    assert {style: counts[style] for style in STYLES if counts[style] < minimum} == {}


def test_no_forbidden_brand(sheets):
    found = {}
    for sheet in sheets:
        if match := FORBIDDEN.search(full_text(sheet)):
            found[sheet["id"]] = match.group()
    assert found == {}


# A prova só mede de verdade se não repete nada do desenvolvimento, onde o prompt é ajustado.


def test_no_product_name_in_both_sets():
    dev_names = {sheet["gabarito"]["nome"].casefold() for sheet in load("dev")}
    repeated = [s["id"] for s in load("holdout") if s["gabarito"]["nome"].casefold() in dev_names]
    assert repeated == []


def test_no_text_in_both_sets():
    dev_texts = {same_text(sheet["texto"]) for sheet in load("dev")}
    assert [s["id"] for s in load("holdout") if same_text(s["texto"]) in dev_texts] == []


def test_no_invented_brand_in_both_sets():
    # A marca inventada de um conjunto não aparece em nenhuma ficha do outro.
    sets = {name: load(name) for name in ("dev", "holdout")}
    found = {}
    for name, other in (("dev", "holdout"), ("holdout", "dev")):
        brands = {sheet["gabarito"]["marca"] for sheet in sets[name]} - REAL_BRANDS - {None}
        pattern = brand_pattern(brands)
        for sheet in sets[other]:
            if match := pattern.search(full_text(sheet)):
                found[sheet["id"]] = match.group()
    assert found == {}
