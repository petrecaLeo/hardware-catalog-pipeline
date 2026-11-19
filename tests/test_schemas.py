import pytest
from pydantic import ValidationError

from pipeline.schemas import CATEGORIES, MODELS, PRODUCT_MODELS, Classification
from tests.examples import EXAMPLES


def format_error(category, data):
    """O primeiro erro de formato: (tipo, campo)."""
    with pytest.raises(ValidationError) as caught:
        MODELS[category].model_validate(data)
    error = caught.value.errors()[0]
    return error["type"], error["loc"]


@pytest.mark.parametrize("category", CATEGORIES)
def test_valid_example_passes(category):
    MODELS[category].model_validate(EXAMPLES[category])
    # Sem as dúvidas, é o formato do gabarito e da edição na tela.
    answer_key = {key: value for key, value in EXAMPLES[category].items() if key != "duvidas"}
    PRODUCT_MODELS[category].model_validate(answer_key)


def test_every_field_has_a_label():
    # O rótulo da tela ("Garantia (meses)"): a etapa que acrescentar um campo também dá o rótulo.
    for model in PRODUCT_MODELS.values():
        for name, info in model.model_fields.items():
            assert info.title and info.title != name, name


def test_text_instead_of_number_is_a_format_error():
    data = {**EXAMPLES["processador"], "nucleos": "8"}
    assert format_error("processador", data) == ("int_type", ("nucleos",))


def test_text_instead_of_price_is_a_format_error():
    data = {**EXAMPLES["fonte"], "preco": "R$ 899,00"}
    assert format_error("fonte", data) == ("float_type", ("preco",))


def test_extra_field_is_a_format_error():
    # socket fica para a v2: por enquanto é um campo a mais.
    data = {**EXAMPLES["processador"], "socket": "AM5"}
    assert format_error("processador", data) == ("extra_forbidden", ("socket",))


def test_missing_field_is_a_format_error():
    # Todo campo é obrigatório: o que falta no texto vem como null, não ausente.
    data = {key: value for key, value in EXAMPLES["gabinete"].items() if key != "cor"}
    assert format_error("gabinete", data) == ("missing", ("cor",))


def test_name_cannot_be_null():
    data = {**EXAMPLES["cooler"], "nome": None}
    assert format_error("cooler", data) == ("string_type", ("nome",))


def test_enum_value_outside_the_list_is_a_format_error():
    data = {**EXAMPLES["memoria_ram"], "tipo": "DDR5"}
    assert format_error("memoria_ram", data) == ("literal_error", ("tipo",))


def test_doubt_about_a_field_of_another_category_is_a_format_error():
    data = {
        **EXAMPLES["processador"],
        "duvidas": [{"campo": "vram_gb", "nivel": "baixa", "motivo": "?"}],
    }
    assert format_error("processador", data) == ("literal_error", ("duvidas", 0, "campo"))


def test_doubt_level_is_media_or_baixa():
    data = {
        **EXAMPLES["processador"],
        "duvidas": [{"campo": "tdp_w", "nivel": "alta", "motivo": "?"}],
    }
    assert format_error("processador", data) == ("literal_error", ("duvidas", 0, "nivel"))


@pytest.mark.parametrize(
    "data",
    [
        {"categoria": "placa_de_video", "categoria_detalhe": None},
        {"categoria": "outro", "categoria_detalhe": "hub USB"},
    ],
)
def test_valid_classification_passes(data):
    Classification.model_validate(data)


def test_category_outside_the_list_is_a_format_error():
    with pytest.raises(ValidationError) as caught:
        Classification.model_validate({"categoria": "monitor", "categoria_detalhe": None})
    error = caught.value.errors()[0]
    assert (error["type"], error["loc"]) == ("literal_error", ("categoria",))
