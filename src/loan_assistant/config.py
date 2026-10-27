"""Central settings: paths, model names and retrieval parameters."""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore")

    # API keys (set in .env, never committed)
    groq_api_key: str = ""
    gemini_api_key: str = ""

    # Models
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    groq_model: str = "openai/gpt-oss-120b"  # llama-3.3-70b-versatile is not on this key
    gemini_model: str = "gemini-flash-latest"

    # Scraping
    base_url: str = "https://bankofmaharashtra.bank.in"
    user_agent: str = "BoMLoanAssistantBot/0.1 (assessment project; polite crawler)"
    request_delay_s: float = 1.0
    request_timeout_s: int = 20
    max_retries: int = 3

    # Retrieval
    retrieval_mode: str = "hybrid"  # vector | hybrid
    candidates_k: int = 20
    top_k: int = 5
    rrf_k: int = 60
    min_score: float = 0.68  # best cosine below this = refuse (in-scope >= 0.74, off-topic <= 0.66)

    # Chunking
    chunk_words: int = 300
    chunk_max_words: int = 450
    chunk_overlap_words: int = 50

    # Paths
    raw_dir: Path = ROOT / "data" / "raw"
    processed_dir: Path = ROOT / "data" / "processed"
    index_dir: Path = ROOT / "index"
    eval_dir: Path = ROOT / "eval"


settings = Settings()
