from functools import lru_cache
from pathlib import Path
from pydantic import Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    app_name: str = "RunPod Portal"
    database_url: str = Field(default="sqlite:///./data/app.db", env="DATABASE_URL")
    secret_key: str = Field(default="change-me", env="SECRET_KEY")
    access_token_expire_minutes: int = 60 * 24
    algorithm: str = "HS256"
    # Shared secret the SSO forward_auth gateway attaches (X-KBF-Auth) so the
    # backend only honors X-KBF-User from the gateway, not from a co-located
    # process forging the header directly against 127.0.0.1. Empty disables the
    # check (local dev / tests).
    kbf_forward_auth_secret: str = Field(default="", env="KBF_FORWARD_AUTH_SECRET")
    # Fail closed by default: if no forward-auth secret is configured, the
    # X-KBF-User identity header is NOT trusted unless this dev opt-in is set.
    # Prevents a missing-secret misconfig from silently disabling anti-spoofing.
    kbf_allow_insecure_sso_header: bool = Field(default=False, env="KBF_ALLOW_INSECURE_SSO_HEADER")

    runpod_api_key: str | None = Field(default=None, env="RUNPOD_API_KEY")
    runpod_base: str = Field(default="https://api.runpod.ai/v2", env="RUNPOD_BASE")
    alphafold_endpoint_id: str | None = Field(default=None, env="ALPHAFOLD_ENDPOINT_ID")
    diffdock_endpoint_id: str | None = Field(default=None, env="DIFFDOCK_ENDPOINT_ID")
    phastest_endpoint_id: str | None = Field(default=None, env="PHASTEST_ENDPOINT_ID")
    bioemu_endpoint_id: str | None = Field(default=None, env="BIOEMU_ENDPOINT_ID")
    esmfold_endpoint_id: str | None = Field(default=None, env="ESMFOLD_ENDPOINT_ID")
    esmfold2_endpoint_id: str | None = Field(default=None, env="ESMFOLD2_ENDPOINT_ID")
    rfdiffusion_endpoint_id: str | None = Field(default=None, env="RFDIFFUSION_ENDPOINT_ID")
    proteinmpnn_endpoint_id: str | None = Field(default=None, env="PROTEINMPNN_ENDPOINT_ID")
    mmseqs_endpoint_id: str | None = Field(default=None, env="MMSEQS_ENDPOINT_ID")
    rosetta_relax_endpoint_id: str | None = Field(default=None, env="ROSETTA_RELAX_ENDPOINT_ID")
    colabfold_endpoint_id: str | None = Field(default=None, env="COLABFOLD_ENDPOINT_ID")

    openai_api_key: str | None = Field(default=None, env="OPENAI_API_KEY")
    openai_model: str = Field(default="gpt-4o-mini", env="OPENAI_MODEL")

    storage_root: Path = Field(default=Path("./data"), env="STORAGE_ROOT")
    uploads_dir: str = "uploads"
    results_dir: str = "results"
    retention_days: int = Field(default=7, env="RETENTION_DAYS")
    poll_interval_seconds: int = Field(default=30, env="POLL_INTERVAL_SECONDS")

    class Config:
        env_file = ".env"
        case_sensitive = False


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.storage_root.mkdir(parents=True, exist_ok=True)
    return settings
