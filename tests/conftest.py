import os

# Antes de qualquer import do código: o load_dotenv não sobrescreve variável que já existe,
# então a chave real do .env nunca chega aos testes.
os.environ["ANTHROPIC_API_KEY"] = "chave-falsa-dos-testes"
