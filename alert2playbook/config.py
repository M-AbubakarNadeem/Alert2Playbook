from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_local_env() -> None:
    """Local runs read settings from `.env` in the repo (gitignored) and keys from
    the file named by A2P_ENV_FILE. That file wins over machine-wide environment
    variables, so a key set for this project is never silently replaced by another
    key saved in the operating system. On Vercel the variables are set in the
    project settings and neither file exists."""
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv(ROOT / ".env", override=False)
    if extra := os.environ.get("A2P_ENV_FILE"):
        load_dotenv(extra, override=True)


@dataclass(frozen=True)
class Settings:
    groq_api_key: str | None
    pinecone_api_key: str | None
    pinecone_index: str
    pinecone_host: str | None
    pinecone_namespace: str
    # Each agent can run on a different Groq model: the hardest job gets the
    # strongest model, and a different model family reviews the playbook so no
    # model grades its own work. On Groq's free tier, which meters tokens per
    # model, spreading the roles also multiplies daily capacity.
    triage_model: str
    mapper_model: str
    writer_model: str
    critic_model: str
    fallback_model: str
    guard_model: str | None
    max_revisions: int
    max_alert_chars: int
    candidates: int
    # Live runs spend the owner's Groq credit, so the public site only runs the
    # agents for someone who knows this code. Empty means no lock (local use).
    demo_code: str | None

    @classmethod
    def from_env(cls) -> "Settings":
        load_local_env()
        env = os.environ.get
        return cls(
            groq_api_key=env("GROQ_API_KEY"),
            pinecone_api_key=env("PINECONE_API_KEY"),
            pinecone_index=env("PINECONE_INDEX", "alert2playbook"),
            pinecone_host=env("PINECONE_HOST"),
            pinecone_namespace=env("PINECONE_NAMESPACE", "attack-techniques"),
            triage_model=env("A2P_TRIAGE_MODEL", "qwen/qwen3.8-27b"),
            mapper_model=env("A2P_MAPPER_MODEL", "openai/gpt-oss-20b"),
            writer_model=env("A2P_WRITER_MODEL", "openai/gpt-oss-120b"),
            critic_model=env("A2P_CRITIC_MODEL", "qwen/qwen3.8-27b"),
            fallback_model=env("A2P_FALLBACK_MODEL", "openai/gpt-oss-120b"),
            guard_model=env("A2P_GUARD_MODEL", "meta-llama/llama-prompt-guard-2-86m") or None,
            max_revisions=int(env("A2P_MAX_REVISIONS", "1")),
            max_alert_chars=int(env("A2P_MAX_ALERT_CHARS", "4000")),
            candidates=int(env("A2P_CANDIDATES", "25")),
            demo_code=env("A2P_DEMO_CODE") or None,
        )
