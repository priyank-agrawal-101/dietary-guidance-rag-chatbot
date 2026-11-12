from pathlib import Path

import pytest
from pydantic import ValidationError

from dietary_rag.cli import main
from dietary_rag.settings import Settings, load_settings


def test_yaml_environment_precedence_and_no_directory_creation(tmp_path):
    config = tmp_path / "settings.yaml"
    config.write_text("data_dir: snapshots\nlog_level: WARNING\n")
    settings = load_settings(
        config,
        project_root=tmp_path,
        environ={"DIETARY_RAG_DATA_DIR": "override", "DIETARY_RAG_MODEL_API_KEY": "secret"},
    )
    assert settings.data_dir == tmp_path / "override"
    assert settings.source_registry == tmp_path / "config/sources.yaml"
    assert settings.log_level == "WARNING"
    assert settings.generation.provider == "unconfigured"
    assert not settings.data_dir.exists()
    assert "secret" not in settings.model_dump_json()


@pytest.mark.parametrize(
    "payload",
    [
        {"data_dir": ""},
        {"source_registry": " "},
        {"api_key": "secret"},
        {"fetch": {"timeout_seconds": -1}},
        {"fetch": {"timeout_seconds": True}},
        {"fetch": {"max_retries": "2"}},
        {"fetch": {"max_html_bytes": 0}},
        {"chunking": {"target_tokens": 50, "overlap_tokens": 50}},
        {"runtime": {"selected_document_limit": 0}},
        {"generation": {"provider": "hosted"}},
    ],
)
def test_invalid_settings_fail(payload):
    with pytest.raises(ValidationError):
        Settings.model_validate(payload)


def test_load_rejects_nonmapping_yaml(tmp_path):
    config = tmp_path / "settings.yaml"
    config.write_text("- data\n")
    with pytest.raises(ValueError, match="mapping"):
        load_settings(config, project_root=tmp_path)


def test_cli_reports_invalid_config_without_printing_inputs(tmp_path, capsys):
    config = tmp_path / "settings.yaml"
    config.write_text("api_key: do-not-print-this\n")
    assert main(["check-settings", "--config", str(config)]) == 2
    assert "do-not-print-this" not in capsys.readouterr().out


def test_repository_settings_load():
    root = Path(__file__).resolve().parents[2]
    settings = load_settings(Path("config/settings.yaml"), project_root=root)
    assert settings.source_registry.is_file()
    assert settings.embedding.model_name == "BAAI/bge-small-en-v1.5"
    assert settings.embedding.revision == "5c38ec7c405ec4b44b94cc5a9bb96e735b38267a"
    assert settings.chunking.tokenizer_revision == "f52bf8ec8c7124536f0efb74aca902b2995e5bcd"
    assert settings.chunking.target_tokens == 350


@pytest.mark.parametrize(
    "payload",
    [
        {"groq": {"max_completion_tokens": 0}},
        {"groq": {"max_input_tokens": 8000}},
        {"groq": {"requests_per_hour": 1}},
        {"groq": {"tokens_per_day": 100}},
        {"groq": {"requests_per_day": True}},
        {"api": {"max_body_bytes": 0}},
        {"api": {"max_in_flight": 17}},
        {"generation": {"provider": "groq", "model_name": "unreviewed-model"}},
    ],
)
def test_invalid_api_and_groq_budgets_fail(payload):
    with pytest.raises(ValidationError):
        Settings.model_validate(payload)


def test_env_file_is_explicit_and_exports_win_without_interpolation(tmp_path, monkeypatch):
    pytest.importorskip("dotenv")
    from dietary_rag.api.runtime import load_environment

    monkeypatch.setenv("GROQ_API_KEY", "exported-test-key")
    (tmp_path / ".env").write_text("GROQ_API_KEY=file-test-key\nLITERAL=${GROQ_API_KEY}\n")
    environ = load_environment(tmp_path)
    assert environ["GROQ_API_KEY"] == "exported-test-key"
    assert environ["LITERAL"] == "${GROQ_API_KEY}"
    settings = Settings()
    assert "test-key" not in settings.model_dump_json()
