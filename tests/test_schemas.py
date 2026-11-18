import pytest
from pydantic import ValidationError

from pipeline.schemas import CATEGORIES, MODELS, PRODUCT_MODELS, Classification

# Um produto certo de cada categoria, com marcas inventadas e chips reais.
EXAMPLES = {
    "processador": {
        "nome": "Processador AMD Ryzen 7 9700X",
        "marca": "AMD",
        "preco": 1899.90,
        "garantia_meses": 36,
        "nucleos": 8,
        "threads": 16,
        "clock_base_ghz": 3.8,
        "clock_boost_ghz": 5.5,
        "cache_l3_mb": 32,
        "tdp_w": 65,
        "video_integrado": True,
        "cooler_incluso": False,
        "duvidas": [],
    },
    "placa_mae": {
        "nome": "Placa-mãe Ferronix B650M Aço WiFi",
        "marca": "Ferronix",
        "preco": 1149,  # número inteiro também vale num campo float
        "garantia_meses": 12,
        "chipset": "B650",
        "formato": "micro_atx",
        "slots_memoria": 4,
        "memoria_max_gb": 192,
        "slots_m2": 2,
        "portas_sata": 4,
        "wifi": True,
        "bluetooth": True,
        "duvidas": [
            {"campo": "memoria_max_gb", "nivel": "media", "motivo": "o texto cita 128 e 192 GB"}
        ],
    },
    "memoria_ram": {
        "nome": "Memória Brisalta Fluxo 32GB (2x16GB) DDR5 6000MHz CL30",
        "marca": "Brisalta",
        "preco": 749.90,
        "garantia_meses": 60,
        "tipo": "ddr5",
        "formato": "dimm",
        "capacidade_gb": 32,
        "modulos": 2,
        "frequencia_mhz": 6000,
        "latencia_cl": 30,
        "tensao_v": 1.35,
        "rgb": True,
        "duvidas": [],
    },
    "placa_de_video": {
        "nome": "Placa de Vídeo Murak GeForce RTX 5070 12GB",
        "marca": "Murak",
        "preco": 4299.00,
        "garantia_meses": 36,
        "chip": "RTX 5070",
        "fabricante_chip": "nvidia",
        "vram_gb": 12,
        "tipo_vram": "gddr7",
        "clock_boost_mhz": 2512,
        "consumo_w": 250,
        "fonte_recomendada_w": 650,
        "comprimento_mm": 268.5,
        "duvidas": [],
    },
    "armazenamento": {
        "nome": "SSD Tecvana Raio 1TB NVMe PCIe 4.0",
        "marca": "Tecvana",
        "preco": 459.90,
        "garantia_meses": 60,
        "tipo": "ssd_nvme",
        "formato": "m2",
        "pcie_geracao": 4,
        "capacidade_gb": 1000,
        "leitura_mb_s": 7000,
        "escrita_mb_s": 6000,
        "rpm": None,
        "tbw": 600,
        "duvidas": [],
    },
    "fonte": {
        "nome": "Fonte Zentrik Forja 850W 80 Plus Gold Modular",
        "marca": "Zentrik",
        "preco": 899.00,
        "garantia_meses": 84,
        "potencia_w": 850,
        "certificacao": "80_plus_gold",
        "modularidade": "modular",
        "pfc_ativo": True,
        "bivolt": True,
        "conector_12v_2x6": True,
        "ventoinha_mm": 135,
        "duvidas": [],
    },
    "gabinete": {
        "nome": "Gabinete Mosaico Torre Mid Vidro",
        "marca": "Mosaico",
        "preco": 389.90,
        "garantia_meses": 12,
        "tamanho": "mid_tower",
        "cor": "preto",
        "lateral_vidro": True,
        "ventoinhas_inclusas": 3,
        "rgb": True,
        "usb_c_frontal": False,
        "duvidas": [],
    },
    "cooler": {
        "nome": "Water Cooler Ventisca Brisa 360mm ARGB",
        "marca": "Ventisca",
        "preco": 529.00,
        "garantia_meses": 24,
        "tipo": "water_cooler",
        "altura_mm": None,
        "radiador_mm": 360,
        "ventoinhas_inclusas": 3,
        "ventoinha_mm": 120,
        "rpm_max": 2000,
        "ruido_dba": 28.5,
        "rgb": True,
        "duvidas": [],
    },
    "outro": {
        "nome": "Hub USB-C Linkora 7 em 1",
        "marca": "Linkora",
        "preco": None,
        "garantia_meses": None,
        "categoria_detalhe": "hub USB",
        "cor": "cinza",
        "conexao": "USB-C",
        "duvidas": [{"campo": "preco", "nivel": "baixa", "motivo": "só há preço em US$"}],
    },
}


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
