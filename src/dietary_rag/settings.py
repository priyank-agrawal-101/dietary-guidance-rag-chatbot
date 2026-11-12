"""Explicit YAML/environment loading; imports never read files or credentials."""

from collections.abc import Mapping
from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

NonEmpty = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
PositiveInt = Annotated[int, Field(strict=True, gt=0)]


class SettingsModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, validate_default=True)


class FetchSettings(SettingsModel):
    timeout_seconds: Annotated[float, Field(strict=True, gt=0, allow_inf_nan=False)] = 20
    max_retries: Annotated[int, Field(strict=True, ge=0, le=5)] = 2
    max_html_bytes: PositiveInt = 5 * 1024 * 1024
    max_pdf_bytes: PositiveInt = 60 * 1024 * 1024
    max_redirects: Annotated[int, Field(strict=True, ge=0, le=10)] = 5


class EmbeddingSettings(SettingsModel):
    model_name: NonEmpty = "BAAI/bge-small-en-v1.5"
    revision: NonEmpty = "5c38ec7c405ec4b44b94cc5a9bb96e735b38267a"


class ChunkingSettings(SettingsModel):
    tokenizer_model_name: NonEmpty = "intfloat/e5-base-v2"
    tokenizer_revision: NonEmpty = "f52bf8ec8c7124536f0efb74aca902b2995e5bcd"
    target_tokens: PositiveInt = 350
    overlap_tokens: Annotated[int, Field(strict=True, ge=0)] = 50

    @model_validator(mode="after")
    def validate_overlap(self):
        if self.overlap_tokens >= self.target_tokens:
            raise ValueError("overlap_tokens must be smaller than target_tokens")
        return self


class RuntimeSettings(SettingsModel):
    question_max_characters: PositiveInt = 2000
    selected_document_limit: PositiveInt = 20
    candidate_chunks: PositiveInt = 20
    per_document_candidates: PositiveInt = 3
    request_timeout_seconds: Annotated[float, Field(strict=True, gt=0, allow_inf_nan=False)] = 60


class GenerationSettings(SettingsModel):
    provider: Literal["unconfigured", "local", "hosted", "groq"] = "unconfigured"
    model_name: NonEmpty | None = None

    @model_validator(mode="after")
    def validate_model(self):
        if self.provider != "unconfigured" and self.model_name is None:
            raise ValueError("a configured provider requires model_name")
        if self.provider == "groq" and self.model_name != "openai/gpt-oss-120b":
            raise ValueError("the reviewed Groq adapter requires openai/gpt-oss-120b")
        return self


class GroqSettings(SettingsModel):
    # Application limits, independent of the account's actual Groq limits.
    max_input_tokens: PositiveInt = 6000
    max_completion_tokens: Annotated[int, Field(strict=True, gt=0, le=65536)] = 1024
    requests_per_minute: PositiveInt = 30
    requests_per_hour: PositiveInt = 60
    requests_per_day: PositiveInt = 300
    tokens_per_minute: PositiveInt = 8000
    tokens_per_hour: PositiveInt = 30000
    tokens_per_day: PositiveInt = 100000

    @model_validator(mode="after")
    def validate_budgets(self):
        if self.max_input_tokens + self.max_completion_tokens > min(
            self.tokens_per_minute, self.tokens_per_hour, self.tokens_per_day, 131072
        ):
            raise ValueError("per-call token budget must fit every window and the model context")
        if not self.requests_per_minute <= self.requests_per_hour <= self.requests_per_day:
            raise ValueError("request windows must have increasing limits")
        if not self.tokens_per_minute <= self.tokens_per_hour <= self.tokens_per_day:
            raise ValueError("token windows must have increasing limits")
        return self


class APISettings(SettingsModel):
    max_body_bytes: PositiveInt = 16384
    max_in_flight: Annotated[int, Field(strict=True, ge=1, le=16)] = 2


class PolicySettings(SettingsModel):
    classifier: Literal["local_conservative_v1"] = "local_conservative_v1"
    minimum_confidence: Annotated[float, Field(gt=0, le=1, allow_inf_nan=False)] = 0.9
    classifier_timeout_seconds: Annotated[float, Field(gt=0, allow_inf_nan=False)] = 10


class AnsweringSettings(SettingsModel):
    generator: Literal["extractive_v1", "configured"] = "extractive_v1"
    units_per_aspect: Annotated[int, Field(strict=True, ge=1, le=3)] = 1
    max_claims_per_document: Annotated[int, Field(strict=True, ge=1, le=20)] = 8
    repair_attempts: Annotated[int, Field(strict=True, ge=0, le=1)] = 1
    semantic_minimum_confidence: Annotated[float, Field(gt=0, le=1, allow_inf_nan=False)] = 0.95
    generator_timeout_seconds: Annotated[float, Field(gt=0, allow_inf_nan=False)] = 30
    verifier_timeout_seconds: Annotated[float, Field(gt=0, allow_inf_nan=False)] = 15


class Settings(SettingsModel):
    data_dir: Path = Path("data")
    source_registry: Path = Path("config/sources.yaml")
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    fetch: FetchSettings = Field(default_factory=FetchSettings)
    embedding: EmbeddingSettings = Field(default_factory=EmbeddingSettings)
    chunking: ChunkingSettings = Field(default_factory=ChunkingSettings)
    runtime: RuntimeSettings = Field(default_factory=RuntimeSettings)
    generation: GenerationSettings = Field(default_factory=GenerationSettings)
    groq: GroqSettings = Field(default_factory=GroqSettings)
    api: APISettings = Field(default_factory=APISettings)
    policy: PolicySettings = Field(default_factory=PolicySettings)
    answering: AnsweringSettings = Field(default_factory=AnsweringSettings)

    @field_validator("data_dir", "source_registry", mode="before")
    @classmethod
    def validate_path(cls, value):
        if isinstance(value, str) and not value.strip():
            raise ValueError("configured paths must not be empty")
        return value


def load_settings(
    config_path: Path,
    *,
    project_root: Path,
    environ: Mapping[str, str] | None = None,
) -> Settings:
    """Load explicitly: defaults < YAML < allowlisted environment overrides.

    The caller supplies environment values; this function does not read .env or secrets.
    Loading resolves paths but does not create directories or open the source registry.
    """
    root = project_root.resolve()
    config = config_path if config_path.is_absolute() else root / config_path
    try:
        values = yaml.safe_load(config.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ValueError("settings file contains invalid YAML") from exc
    if not isinstance(values, dict):
        raise ValueError("settings file must contain a YAML mapping")
    env = environ if environ is not None else {}
    for variable, field in {
        "DIETARY_RAG_DATA_DIR": "data_dir",
        "DIETARY_RAG_SOURCE_REGISTRY": "source_registry",
        "DIETARY_RAG_LOG_LEVEL": "log_level",
    }.items():
        if variable in env:
            if not env[variable].strip():
                raise ValueError(f"{variable} must not be empty")
            values[field] = env[variable]
    model_overrides = {
        "DIETARY_RAG_MODEL_PROVIDER": "provider",
        "DIETARY_RAG_MODEL_NAME": "model_name",
    }
    if any(variable in env for variable in model_overrides):
        generation = values.get("generation", {})
        if not isinstance(generation, dict):
            raise ValueError("generation settings must contain a mapping")
        generation = generation.copy()
        for variable, field in model_overrides.items():
            if variable in env:
                generation[field] = env[variable]
        values["generation"] = generation
    settings = Settings.model_validate(values)
    return settings.model_copy(
        update={
            "data_dir": (root / settings.data_dir).resolve(),
            "source_registry": (root / settings.source_registry).resolve(),
        }
    )
