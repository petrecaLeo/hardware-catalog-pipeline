from pathlib import Path

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parent.parent
PROMPTS_DIR = ROOT_DIR / "pipeline" / "prompts"
DATA_DIR = ROOT_DIR / "data"
DB_FILE = DATA_DIR / "catalog.db"

load_dotenv(ROOT_DIR / ".env")

MODEL = "claude-haiku-5-5"

# US$ por milhão de tokens: (entrada, saída). O lote custa a metade. O Haiku 5.5 cobra 5× acima de
# 100K tokens de prompt; as fichas ficam bem abaixo disso.
PRICES = {
    "direct": (0.10, 0.50),
    "batch": (0.05, 0.25),
}
CACHE_WRITE_FACTOR = 1.25
CACHE_READ_FACTOR = 0.1

CLASSIFY_MAX_TOKENS = 150
EXTRACT_MAX_TOKENS = 600
# Resposta cortada (stop_reason "max_tokens") volta igual, com este limite um pouco maior.
CLASSIFY_RETRY_MAX_TOKENS = 300
EXTRACT_RETRY_MAX_TOKENS = 900
# Depois da primeira resposta, quantas vezes a ficha volta ao modelo com o erro de validação.
EXTRA_ATTEMPTS = 2
# Modo lote: de quantos em quantos segundos o lote é consultado até terminar (0 com a API falsa).
BATCH_POLL_SECONDS = 30
