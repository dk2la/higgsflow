import json

from hfcore import __version__


def test_version_prints_the_package_version(higgs):
    result = higgs("--version")

    assert result.exit_code == 0
    assert result.output.strip() == f"higgs {__version__}"


def test_version_as_json(higgs):
    result = higgs("--version", "--json")

    assert result.exit_code == 0
    assert json.loads(result.output) == {"version": __version__}
