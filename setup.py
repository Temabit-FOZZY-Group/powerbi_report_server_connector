from typing import Dict, Set

from setuptools import find_packages, setup

plugins: Dict[str, Set[str]] = {
    "powerbireportserver": {
        "acryl-datahub",
        "orderedset",
        "pydantic",
        "requests",
        "requests_ntlm",
    },
}

setup_output = setup(
    name="powerbi_report_server",
    version="0.0.3",
    description="Ingest PowerBi Report Server metadata to DataHub",
    package_dir={"": "src"},
    packages=find_packages("src"),
    install_requires=list(plugins["powerbireportserver"]),
    extras_require={
        "dev": ["pytest", "pre-commit", "isort", "black", "flake8", "mypy", "tox"],
    },
)
