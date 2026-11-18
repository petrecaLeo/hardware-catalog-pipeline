import os

from pipeline import config


def test_uses_haiku_5_5():
    assert config.MODEL == "claude-haiku-5-5"


def test_batch_costs_half_of_direct():
    assert config.PRICES["direct"] == (0.10, 0.50)
    assert config.PRICES["batch"] == (0.05, 0.25)


def test_loading_config_keeps_the_fake_key():
    assert os.environ["ANTHROPIC_API_KEY"] == "chave-falsa-dos-testes"
