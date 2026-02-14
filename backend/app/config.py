from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    # App
    app_name: str = "ID Fine AI Chatbot"
    debug: bool = False

    # Database
    db_host: str = "postgres"
    db_port: int = 5432
    db_name: str = "idf_chatbot"
    db_user: str = "idf_user"
    db_password: str = ""

    @property
    def database_url(self) -> str:
        return (
            f"postgresql+asyncpg://{self.db_user}:{self.db_password}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}"
        )

    # Redis
    redis_url: str = "redis://redis:6379/0"

    # Qdrant
    qdrant_host: str = "qdrant"
    qdrant_port: int = 6333
    qdrant_collection: str = "idf_documents"

    # Claude API
    anthropic_api_key: str = ""
    claude_model: str = "claude-sonnet-4-5-20250929"
    claude_classifier_model: str = "claude-haiku-4-5-20251001"

    # Odoo
    odoo_url: str = ""
    odoo_db: str = ""
    odoo_username: str = ""
    odoo_password: str = ""
    odoo_api_key: str = ""  # Optional: if set, used instead of username/password
    odoo_version: int = 18
    odoo_verify_ssl: bool = True
    odoo_catalog_url: str = ""
    odoo_catalog_url_en: str = ""

    # Security
    jwt_secret: str = ""
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = 15
    jwt_refresh_token_expire_days: int = 7
    cors_origins: str = "http://localhost:3000"
    widget_allowed_domains: str = ""

    # Customer OTP Authentication
    otp_ttl_seconds: int = 300  # 5 minutes
    otp_max_attempts: int = 5  # max verification attempts per OTP
    otp_lockout_seconds: int = 900  # 15 min lockout after max attempts
    otp_max_requests_per_hour: int = 3  # max OTP requests per email per hour
    customer_session_ttl_seconds: int = 7200  # 2 hours

    # Embedding
    embedding_model: str = "intfloat/multilingual-e5-large"
    embedding_dimension: int = 1024

    # Meta (Facebook Messenger, Instagram DM, WhatsApp Business)
    meta_app_secret: str = ""
    meta_verify_token: str = ""
    meta_page_access_token: str = ""
    meta_page_id: str = ""
    meta_whatsapp_access_token: str = ""
    meta_whatsapp_phone_number_id: str = ""
    meta_graph_api_version: str = "v21.0"
    meta_source_group_id: str = ""

    # Upload
    upload_dir: str = "/app/uploads"
    max_upload_size_mb: int = 50

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


@lru_cache
def get_settings() -> Settings:
    return Settings()
