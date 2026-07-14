"""Regression checks for the collector image's offline wheelhouse."""

from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _project_dependencies(pyproject: str) -> list[str]:
    """Extract the simple string array assigned to project.dependencies."""
    in_project = False
    dependency_lines: list[str] = []
    collecting = False

    for raw_line in pyproject.splitlines():
        line = raw_line.strip()
        if line.startswith("[") and line.endswith("]"):
            in_project = line == "[project]"
            collecting = False
            continue
        if not in_project:
            continue
        if not collecting:
            match = re.match(r"dependencies\s*=\s*\[(.*)", line)
            if not match:
                continue
            dependency_lines.append(match.group(1))
            collecting = "]" not in match.group(1)
        else:
            dependency_lines.append(line)
            collecting = "]" not in line
        if dependency_lines and not collecting:
            break

    if not dependency_lines:
        raise AssertionError("[project].dependencies array not found")
    array_text = "\n".join(dependency_lines).split("]", 1)[0]
    return [match.group(2) for match in re.finditer(r"(['\"])(.*?)\1", array_text)]


def _stages(dockerfile: str) -> list[tuple[str, str | None, str]]:
    headers = list(
        re.finditer(
            r"^FROM\s+(\S+)(?:\s+AS\s+(\S+))?\s*$",
            dockerfile,
            flags=re.MULTILINE | re.IGNORECASE,
        )
    )
    return [
        (
            match.group(1),
            match.group(2),
            dockerfile[match.end() : headers[index + 1].start() if index + 1 < len(headers) else None],
        )
        for index, match in enumerate(headers)
    ]


def _run_command(stage: str, needle: str) -> str:
    commands = re.findall(
        r"^RUN\s+(.+?)(?=^[A-Z][A-Z]+\s|\Z)",
        stage,
        flags=re.MULTILINE | re.DOTALL,
    )
    matches = [command for command in commands if needle in command]
    if len(matches) != 1:
        raise AssertionError(f"expected one RUN command containing {needle!r}, found {len(matches)}")
    return matches[0]


class CollectorWheelhouseTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.dockerfile = (ROOT / "Dockerfile.collector").read_text(encoding="utf-8")
        cls.pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")

    def assertCompatibleBuilder(self, dockerfile: str) -> None:
        builder = next((stage for stage in _stages(dockerfile) if stage[1] == "builder"), None)
        self.assertIsNotNone(builder, "collector builder stage not found")
        self.assertEqual("python:3.11-slim", builder[0])

    def test_builder_python_matches_final_apt_python(self) -> None:
        incompatible_fixture = "FROM python:3.12-slim AS builder\nRUN true\n"
        with self.assertRaises(AssertionError):
            self.assertCompatibleBuilder(incompatible_fixture)
        self.assertCompatibleBuilder(self.dockerfile)

    def test_offline_wheelhouse_contract(self) -> None:
        stages = _stages(self.dockerfile)
        builder = next((stage for stage in stages if stage[1] == "builder"), None)
        self.assertIsNotNone(builder, "collector builder stage not found")
        final_image, _, final_stage = stages[-1]

        self.assertEqual("node:22-bookworm-slim", final_image)
        apt_install = re.search(r"apt-get\s+install\b(.*?)\s+&&", final_stage, re.DOTALL)
        self.assertIsNotNone(apt_install, "final stage must install apt packages")
        apt_packages = re.findall(r"[A-Za-z0-9][A-Za-z0-9.+-]*", apt_install.group(1))
        self.assertIn("python3", apt_packages)

        wheel_command = _run_command(builder[2], "python -m pip wheel")
        for dependency in _project_dependencies(self.pyproject):
            self.assertIn(dependency, wheel_command)

        install_command = _run_command(final_stage, "/opt/venv/bin/pip install")
        self.assertIn("--no-index", install_command)
        self.assertIn("--find-links=/wheels", install_command)


if __name__ == "__main__":
    unittest.main()
