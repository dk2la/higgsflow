import json
import tomllib


def test_init_creates_the_project_layout(higgs, projects_root):
    result = higgs("init", "demo", "--json")

    assert result.exit_code == 0
    project = projects_root / "demo"
    assert json.loads(result.output) == {"name": "demo", "path": str(project)}
    for folder in ("inputs", "references", "prompts", "generations", "final", ".hf/requests"):
        assert (project / folder).is_dir(), folder
    config = tomllib.loads((project / "project.toml").read_text())
    assert config["name"] == "demo"


def test_init_on_an_existing_name_fails_without_touching_the_project(higgs, projects_root):
    higgs("init", "demo")
    marker = projects_root / "demo" / "inputs" / "keep.jpg"
    marker.write_bytes(b"photo")
    (projects_root / "demo" / "project.toml").write_text('name = "demo"\nbudget = 5\n')

    result = higgs("init", "demo", "--json")

    assert result.exit_code == 1
    assert json.loads(result.output)["error"]["code"] == "project_exists"
    assert marker.read_bytes() == b"photo"
    assert (projects_root / "demo" / "project.toml").read_text() == 'name = "demo"\nbudget = 5\n'


def test_init_rejects_a_name_that_is_not_a_plain_folder_name(higgs, projects_root):
    for name in ("../escape", "a/b", ".hidden", ""):
        result = higgs("init", name, "--json")

        assert result.exit_code == 1, name
        assert json.loads(result.output)["error"]["code"] == "invalid_project_name"
    assert not (projects_root.parent / "escape").exists()
    assert list(projects_root.iterdir()) == []


def test_projects_lists_the_projects_under_the_root(higgs, projects_root):
    higgs("init", "beta")
    higgs("init", "alpha")
    (projects_root / "not-a-project").mkdir()

    result = higgs("projects", "--json")

    assert result.exit_code == 0
    assert json.loads(result.output) == {
        "projects_root": str(projects_root),
        "projects": [
            {"name": "alpha", "path": str(projects_root / "alpha")},
            {"name": "beta", "path": str(projects_root / "beta")},
        ],
    }


def test_a_projects_root_from_the_config_file_is_respected(higgs, monkeypatch, config_dir, tmp_path):
    monkeypatch.delenv("HIGGS_PROJECTS_ROOT")
    custom_root = tmp_path / "elsewhere"
    (config_dir / "config.toml").write_text(f'projects_root = "{custom_root}"\n')

    higgs("init", "demo")
    result = higgs("projects", "--json")

    assert (custom_root / "demo" / "project.toml").is_file()
    assert json.loads(result.output)["projects_root"] == str(custom_root)


def test_the_projects_root_env_var_beats_the_config_file(higgs, config_dir, projects_root, tmp_path):
    (config_dir / "config.toml").write_text(f'projects_root = "{tmp_path / "elsewhere"}"\n')

    higgs("init", "demo")

    assert (projects_root / "demo" / "project.toml").is_file()


def test_projects_as_text_names_each_project(higgs):
    higgs("init", "alpha")

    result = higgs("projects")

    assert result.exit_code == 0
    assert "alpha" in result.output
