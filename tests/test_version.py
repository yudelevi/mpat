import tomllib
from pathlib import Path

import mpat


def test_version_matches_pyproject():
    data = tomllib.loads((Path(__file__).parents[1] / "pyproject.toml").read_text())
    assert data["project"]["version"] == mpat.__version__ == "0.1.0"
