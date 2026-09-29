from pathlib import Path
from urllib.parse import urlparse

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AGENT_", env_file=".env", extra="ignore")
    account: str = ""
    restlet_url: str = ""
    client_id: str = ""
    certificate_id: str = ""
    private_key_path: Path = Path("secrets/private.pem")
    jwt_algorithm: str = "PS256"
    ollama_url: str = "http://127.0.0.1:11434"
    model: str = "qwen3.5:4b"
    context_tokens: int = Field(8192, ge=4096, le=32768)
    document_path: Path = Path("knowledge/business.md")
    data_dir: Path = Path("data")
    management_token: str = ""
    poll_seconds: float = Field(2, ge=2)
    schema_refresh_seconds: int = Field(3600, ge=60)
    # Full metadata rescan interval; routine refreshes only re-probe core/new/failed tables.
    schema_full_refresh_seconds: int = Field(86400, ge=300)
    schema_probe_batch_size: int = Field(12, ge=1, le=12)
    schema_probe_concurrency: int = Field(2, ge=1, le=4)
    # Comma-separated SuiteQL table names preferred on incremental refresh.
    schema_core_tables: str = (
        "customer,vendor,transaction,transactionline,item,employee,account,"
        "subsidiary,contact,department,location,classification,currency,entity"
    )
    inference_timeout: int = Field(90, ge=10, le=900)
    creation_inference_timeout: int = Field(90, ge=10, le=1800)
    retention_days: int = Field(7, ge=1, le=90)
    worker_id: str = "local-primary"
    max_attempts: int = Field(3, ge=1, le=3)
    integration_role_id: str = ""
    creation_enabled: bool = False
    sdf_enabled: bool = False
    sdf_auth_id: str = ""
    suitecloud_bin: str = "suitecloud"
    sdf_timeout: int = Field(120, ge=30, le=1800)

    @model_validator(mode="after")
    def endpoints(self):
        if self.restlet_url:
            u = urlparse(self.restlet_url)
            host = self.account.lower().replace("_", "-") + ".restlets.api.netsuite.com"
            if u.scheme != "https" or u.hostname != host or u.username or u.password:
                raise ValueError("RESTlet URL must be HTTPS on this account's NetSuite RESTlet host")
            if u.path != "/app/site/hosting/restlet.nl":
                raise ValueError("Unexpected RESTlet path")
        u = urlparse(self.ollama_url)
        if u.scheme not in {"http", "https"} or u.hostname not in {"localhost", "127.0.0.1", "::1", "ollama"}:
            raise ValueError("Inference is restricted to loopback or the private Docker service 'ollama'")
        if self.jwt_algorithm not in {"PS256", "ES256"}:
            raise ValueError("Use PS256 or ES256")
        if "cloud" in self.model.lower() or "/" in self.model or "://" in self.model:
            raise ValueError("Use an installed local model tag, not a cloud model or remote registry URL")
        return self

    def check_credentials(self):
        if not all([self.account, self.restlet_url, self.client_id, self.certificate_id]):
            raise ValueError("Configure account, RESTlet URL, client ID, and certificate ID in .env")
        if not self.private_key_path.is_file():
            raise ValueError("OAuth private key file is missing")

    @property
    def token_url(self):
        account = self.account.lower().replace("_", "-")
        return f"https://{account}.suitetalk.api.netsuite.com/services/rest/auth/oauth2/v1/token"
