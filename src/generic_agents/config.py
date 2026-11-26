from functools import lru_cache
from pathlib import Path
import re
from typing import Literal
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field, SecretStr, field_validator


class Settings(BaseSettings):
    # Human-readable evidence explanation; remains test-database-only.
    v2_advice_evidence_explanation_enabled: bool = False
    # Isolated integration only: the production workflow is not wired yet.
    v2_advice_setup_test_enabled: bool = False
    # Recovery console is deliberately restricted to isolated test databases.
    v2_advice_recovery_console_enabled: bool = False
    # Candidate only: even if enabled it remains guarded to a disposable DB.
    v2_deepseek_dispatch_enabled: bool = False
    # Compatibility discovery is an isolated-test experiment, not a release switch.
    v2_history_compat_test_enabled: bool = False
    # Terminal-history experiment is disabled and hard-fenced to disposable PG.
    v2_terminal_history_test_enabled: bool = False
    # Separate, test-only private response spool; never a report directory.
    v2_response_spool_enabled: bool = False
    v2_response_spool_root: Path | None = None
    v2_deepseek_connect_timeout_seconds: float = Field(default=10, ge=1, le=60)
    v2_deepseek_read_timeout_seconds: float = Field(default=120, ge=1, le=600)
    v2_deepseek_total_timeout_seconds: float = Field(default=600, ge=1, le=1800)
    # Synthetic evidence may exercise real rule selection only in an explicit
    # verification service. No workspace request can change this boundary.
    threshold_policy_environment: Literal['production','verification'] = 'production'
    # Server-owned build selection lets an isolated runtime verify new UI assets
    # without replacing the files served to existing users before deployment.
    setup_build_dir: Path | None = None
    # Display-only Goal totals; can be disabled by the server operator.
    # Never used to grant execution or send.
    goal_budget_display_preview_enabled: bool = True
    # Offline delegation remains opt-in and restricted to disposable test PG.
    offline_document_delegation_enabled: bool = False
    offline_document_worker_enabled: bool = False
    model_config = SettingsConfigDict(env_prefix="GA_", env_file=".env", extra="ignore")
    database_url: str
    # Explicit process mode; no_idle closes returned connections immediately.
    # Normal deployments retain their original pooled engine by default.
    database_pool_mode: Literal['pooled', 'no_idle'] = 'pooled'
    # Per-process PostgreSQL limits. Other engines keep their original defaults.
    database_pool_size: int = Field(default=5, ge=1, le=64)
    database_max_overflow: int = Field(default=10, ge=0, le=64)
    database_pool_timeout: int = Field(default=30, ge=1, le=600)
    redis_url: str
    storage_dir: Path = Path(".runtime/evidence")
    # Server-owned component location, never accepted from workspace requests.
    local_runtime_root: Path | None = None
    cookie_secure: bool = True
    session_cookie_name: str = Field(default='ga_session', pattern=r'^[A-Za-z][A-Za-z0-9_]{2,63}$')
    allowed_origins: str = "http://127.0.0.1:8765"
    session_hours: int = 12
    allowed_connector_hosts: str = ""
    development_connector_urls: str = ""
    business_data_access_grants: str = '{}'
    # Server-owned key and exact local simulator origins. Never workspace input.
    device_gateway_key_file: Path | None = None
    device_gateway_allowed_origins: str = ''
    wms_allowed_endpoints: str = ""
    wms_access_grants: str = "{}"
    # Optional operator-approved endpoint ceiling per workspace; no secrets.
    wms_self_service_access: str = "{}"
    cloud_daily_token_limit: int = 50000
    cloud_daily_call_limit: int = 100
    # Server-owned provider policy; applies to new dispatches, not saved results.
    cloud_allowed_providers: str = 'qwen,deepseek'
    report_design_maximum_cost_usd: float = 3.0
    dashscope_api_key: SecretStr | None = Field(default=None, validation_alias="DASHSCOPE_API_KEY", repr=False)
    qwen_tokenplan_api_key: SecretStr | None = Field(default=None, validation_alias="QWEN_TOKENPLAN_API_KEY", repr=False)
    deepseek_api_key: SecretStr | None = Field(default=None, validation_alias="DEEPSEEK_API_KEY", repr=False)
    max_upload_bytes: int = 20 * 1024 * 1024
    specialist_dir: Path = Path('.runtime/specialist')
    pixel_diagnostic_python: Path | None = None
    specialist_python: Path = Path('.runtime/venvs/rtdetr/bin/python')
    geo_python: Path = Path('.runtime/venvs/geo/bin/python')
    audio_python: Path | None = None
    audio_weights: Path | None = None
    vad_python: Path | None = None
    vad_weights: Path | None = None
    media_allowed_origins: str = 'http://127.0.0.1:9997,rtsp://127.0.0.1:8554'
    media_api_secret_ref: str = ''
    media_rtsp_secret_ref: str = ''
    mqtt_allowed_brokers: str = ''
    # FTPS is disabled unless the operator explicitly supplies both allowlists.
    ftps_allowed_endpoints: str = '[]'
    ftps_access_grants: str = '{}'
    mqtt_receiver_concurrency: int = Field(default=4, ge=1, le=8)
    mqtt_receiver_poll_seconds: float = Field(default=1, ge=.2, le=10)
    mqtt_receiver_lease_seconds: float = Field(default=15, ge=5, le=120)
    mqtt_receiver_connect_timeout_seconds: float = Field(default=3, ge=1, le=15)

    @field_validator('specialist_dir', mode='after')
    @classmethod
    def absolute_specialist_directory(cls, value: Path) -> Path:
        # Native runners change cwd. Pin this setting at construction time;
        # absolute(), unlike resolve(), retains symlinks and '..' for safe_path.
        return value.absolute()

    @field_validator('database_pool_size', 'database_max_overflow', 'database_pool_timeout', mode='before')
    @classmethod
    def strict_database_pool_integer(cls, value):
        # BaseSettings supplies environment values as strings. Do not let int
        # coercion silently accept bools, floats, whitespace or signed values.
        if type(value) is str and re.fullmatch(r'[0-9]{1,6}', value) is not None:
            return int(value)
        if type(value) is not int:
            raise ValueError('Database pool settings require bounded whole decimal integers')
        return value

    @field_validator('database_pool_mode', mode='before')
    @classmethod
    def strict_database_pool_mode(cls, value):
        if type(value) is not str or value not in {'pooled', 'no_idle'}:
            raise ValueError("Database pool mode must be exactly 'pooled' or 'no_idle'")
        return value


@lru_cache
def settings() -> Settings:
    return Settings()
