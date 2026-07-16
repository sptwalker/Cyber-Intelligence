"""Regression checks for the collector image's Trivy gate."""

from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
POLICY_EXPIRY = "2027-01-01"
FIXED_APPLICATION_CVES = {
    "CVE-2024-6345",   # setuptools 66.1.1 -> 70.0.0
    "CVE-2025-47273",  # setuptools 66.1.1 -> 78.1.1
    "CVE-2026-33671",  # npm-bundled picomatch 4.0.3 -> 4.0.4
    "CVE-2026-48815",  # npm-bundled sigstore 3.1.0 -> 4.1.1
}
PIPELINE_1528_PERL_CVES = {
    "CVE-2026-13221",
    "CVE-2026-57432",
}
IGNORE_ENTRY = re.compile(r"^CVE-\d{4}-\d+$")
IGNORE_DOCUMENTATION = re.compile(
    r"^# (?P<cve>CVE-\d{4}-\d+): "
    r"Status=(?P<status>fix_deferred|affected|will_not_fix|not_fixed); "
    r"Reason=.+; Expires=(?P<expiry>\d{4}-\d{2}-\d{2})$"
)


def _run_command(dockerfile: str, needle: str) -> str:
    commands = re.findall(
        r"^RUN\s+(.+?)(?=^[A-Z][A-Z]+\s|\Z)",
        dockerfile,
        flags=re.MULTILINE | re.DOTALL,
    )
    matches = [command for command in commands if needle in command]
    if len(matches) != 1:
        raise AssertionError(
            f"expected one RUN command containing {needle!r}, found {len(matches)}"
        )
    return matches[0]


class TrivyGateTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.dockerfile = (ROOT / "Dockerfile.collector").read_text(encoding="utf-8")
        cls.ignore_lines = (ROOT / ".trivyignore").read_text(encoding="utf-8").splitlines()
        cls.ignored_cves = {line.strip() for line in cls.ignore_lines if IGNORE_ENTRY.fullmatch(line.strip())}

    def test_every_ignore_has_status_reason_and_policy_expiry(self) -> None:
        problems: list[str] = []
        for index, raw_line in enumerate(self.ignore_lines):
            cve = raw_line.strip()
            if not IGNORE_ENTRY.fullmatch(cve):
                continue
            previous = self.ignore_lines[index - 1].strip() if index else ""
            documentation = IGNORE_DOCUMENTATION.fullmatch(previous)
            if documentation is None:
                problems.append(f"{cve}: missing adjacent status/reason/expiry comment")
                continue
            if documentation.group("cve") != cve:
                problems.append(f"{cve}: comment documents {documentation.group('cve')}")
            if documentation.group("expiry") != POLICY_EXPIRY:
                problems.append(
                    f"{cve}: expiry {documentation.group('expiry')} != {POLICY_EXPIRY}"
                )
        self.assertEqual([], problems, "\n" + "\n".join(problems))

    def test_fixed_application_findings_are_not_ignored(self) -> None:
        self.assertEqual(set(), FIXED_APPLICATION_CVES & self.ignored_cves)

    def test_pipeline_1528_perl_findings_are_narrow_base_os_exceptions(self) -> None:
        problems: list[str] = []
        for cve in sorted(PIPELINE_1528_PERL_CVES):
            matching_lines = [
                index for index, line in enumerate(self.ignore_lines) if line.strip() == cve
            ]
            if len(matching_lines) != 1:
                problems.append(f"{cve}: expected exactly one ignore entry")
                continue

            documentation = IGNORE_DOCUMENTATION.fullmatch(
                self.ignore_lines[matching_lines[0] - 1].strip()
            )
            if documentation is None:
                problems.append(f"{cve}: missing adjacent policy documentation")
                continue

            if documentation.group("status") != "affected":
                problems.append(
                    f"{cve}: status {documentation.group('status')!r} != 'affected'"
                )

            documented_line = documentation.group(0)
            for expected in (
                "perl-base 5.40.1-6",
                "python:3.12-slim",
                "Debian 13.5",
                "no Debian fixed version",
                "pipeline 1528/job 8966",
                f"Expires={POLICY_EXPIRY}",
            ):
                if expected not in documented_line:
                    problems.append(f"{cve}: documentation missing {expected!r}")

        self.assertEqual([], problems, "\n" + "\n".join(problems))

    def test_build_only_npm_bundle_is_removed_after_opencli_install(self) -> None:
        install = _run_command(self.dockerfile, "npm install --global")
        install_position = install.index("npm install --global")
        for runtime_path in (
            "/usr/local/lib/node_modules/npm",
            "/usr/local/bin/npm",
            "/usr/local/bin/npx",
        ):
            with self.subTest(runtime_path=runtime_path):
                removal_position = install.find(runtime_path)
                self.assertGreater(
                    removal_position,
                    install_position,
                    f"remove build-only {runtime_path} after installing OpenCLI; "
                    "the npm bundle owns the fixed picomatch and sigstore findings",
                )

    def test_runtime_setuptools_is_removed_after_wheel_install(self) -> None:
        install = _run_command(self.dockerfile, "/opt/venv/bin/pip install")
        install_position = install.index("/opt/venv/bin/pip install")
        removal_position = install.find("/opt/venv/bin/pip uninstall --yes setuptools")
        self.assertGreater(
            removal_position,
            install_position,
            "remove build-only setuptools after installing the project wheel",
        )


if __name__ == "__main__":
    unittest.main()
