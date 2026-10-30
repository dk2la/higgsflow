import json
import stat
import tomllib

import httpx
import pytest

SUBMIT_URL = "https://api.higgsfield.ai/higgsfield-ai/soul/v2/standard"
SUBMITTED = {"status": "queued", "request_id": "d7e6c0f3-6699-4f6c-bb45-2ad7fd9158ff"}


@pytest.fixture
def submit(higgs, project, respx_mock):
    """Runs a `higgs gen` submit and returns the `Authorization` header it sent (or the result on failure)."""
    route = respx_mock.post(SUBMIT_URL).mock(return_value=httpx.Response(200, json=SUBMITTED))

    def run():
        result = higgs("gen", "higgsfield-ai/soul/v2/standard", "--no-validate", "--no-wait", "--json", "--yes")
        if result.exit_code != 0:
            return result
        return route.calls.last.request.headers["Authorization"]

    return run


def test_hf_key_env_var_beats_the_credentials_file(submit, estimate, api_key, monkeypatch):
    monkeypatch.setenv("HF_KEY", "env-id:env-secret")
    monkeypatch.setenv("HF_API_KEY", "other-id")
    monkeypatch.setenv("HF_API_SECRET", "other-secret")

    assert submit() == "Key env-id:env-secret"


def test_hf_api_key_and_secret_env_vars_beat_the_credentials_file(submit, estimate, api_key, monkeypatch):
    monkeypatch.setenv("HF_API_KEY", "env-id")
    monkeypatch.setenv("HF_API_SECRET", "env-secret")

    assert submit() == "Key env-id:env-secret"


def test_the_credentials_file_is_used_without_env_vars(submit, estimate, api_key):
    assert submit() == api_key


def test_hf_api_key_without_a_secret_is_an_error(submit, monkeypatch):
    monkeypatch.setenv("HF_API_KEY", "env-id")

    result = submit()

    assert result.exit_code == 1
    assert json.loads(result.output)["error"]["code"] == "incomplete_key_env"


def test_a_missing_key_points_to_auth_login(submit):
    result = submit()

    assert result.exit_code == 1
    error = json.loads(result.output)["error"]
    assert error["code"] == "missing_key"
    assert "higgs auth login" in error["hint"]


def test_auth_login_saves_the_key_to_a_private_credentials_file(higgs, config_dir):
    result = higgs("auth", "login", input="key-id:key-secret\n")

    assert result.exit_code == 0
    credentials = config_dir / "credentials.toml"
    assert stat.S_IMODE(credentials.stat().st_mode) == 0o600
    assert tomllib.loads(credentials.read_text()) == {"key_id": "key-id", "key_secret": "key-secret"}
    assert "key-secret" not in result.output


def test_auth_login_takes_the_key_as_an_option(higgs, config_dir):
    result = higgs("auth", "login", "--key", "key-id:key-secret", "--json")

    assert result.exit_code == 0
    assert json.loads(result.output) == {
        "key_id": "key-id",
        "credentials_file": str(config_dir / "credentials.toml"),
    }


def test_auth_login_rejects_a_key_without_both_parts(higgs, config_dir):
    result = higgs("auth", "login", "--key", "only-an-id", "--json")

    assert result.exit_code == 1
    assert json.loads(result.output)["error"]["code"] == "invalid_key_format"
    assert not (config_dir / "credentials.toml").exists()


def test_auth_login_with_json_never_prompts_for_the_key(higgs, config_dir):
    result = higgs("auth", "login", "--json", input="key-id:key-secret\n")

    assert result.exit_code == 1
    assert json.loads(result.output)["error"]["code"] == "missing_param"
    assert not (config_dir / "credentials.toml").exists()
