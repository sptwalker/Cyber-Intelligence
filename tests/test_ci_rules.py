"""Regression checks for CI changed-path rules."""

from __future__ import annotations

import fnmatch
import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
MAIN_BRANCH_RULE = '$CI_COMMIT_BRANCH == "main" || $CI_COMMIT_BRANCH == "master"'
FULL_PIPELINE_JOBS = (
    "build_image",
    "build_collector_image",
    "scan_trivy",
    "deploy_cce",
)


def _matches(pattern: str, path: str) -> bool:
    if pattern.endswith("/**/*"):
        return path.startswith(pattern[:-5] + "/")
    return fnmatch.fnmatchcase(path, pattern)


def _rules_for(config: dict, job_name: str) -> list[dict]:
    job = config[job_name]
    if "rules" in job:
        return job["rules"]
    parents = job.get("extends", [])
    if isinstance(parents, str):
        parents = [parents]
    for parent in parents:
        if parent in config:
            return _rules_for(config, parent)
    raise AssertionError(f"no rules found for {job_name}")


def _runs_on_main(config: dict, job_name: str, changed_paths: list[str]) -> bool:
    for rule in _rules_for(config, job_name):
        if rule.get("if") != MAIN_BRANCH_RULE:
            if rule.get("when") == "never":
                return False
            continue
        changes = rule.get("changes")
        if changes is None or any(
            _matches(pattern, path) for pattern in changes for path in changed_paths
        ):
            return True
    return False


class CIRulesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.config = yaml.safe_load(
            (ROOT / ".gitlab-ci.yml").read_text(encoding="utf-8"),
        )

    def test_test_only_change_verifies_and_mixed_change_runs_full_chain(self) -> None:
        test_only = ["tests/test_api_reports.py"]
        mixed = [*test_only, ".gitlab-ci.yml"]

        self.assertTrue(_runs_on_main(self.config, "verify_python", test_only))
        for job_name in FULL_PIPELINE_JOBS:
            with self.subTest(test_only_job=job_name):
                self.assertFalse(_runs_on_main(self.config, job_name, test_only))
        for job_name in ("verify_python", *FULL_PIPELINE_JOBS):
            with self.subTest(job_name=job_name):
                self.assertTrue(_runs_on_main(self.config, job_name, mixed))

    def test_deploy_rollout_failure_diagnostics_are_scoped_and_bounded(self) -> None:
        deploy_script = self.config["deploy_cce"]["script"]
        rollout_block = deploy_script[-1]

        self.assertIsInstance(rollout_block, str)
        self.assertEqual(
            1,
            rollout_block.count(
                "kubectl rollout status deployment/cyber-intelligence "
                "-n nexus-prod --timeout=3m"
            ),
        )
        self.assertIn(
            'kubectl rollout status deployment/cyber-intelligence '
            '-n nexus-prod --timeout=3m || rollout_rc=$?',
            rollout_block,
        )
        self.assertIn(
            'if [ "$rollout_rc" -eq 0 ]; then\n  exit 0\nfi',
            rollout_block,
        )
        self.assertTrue(rollout_block.rstrip().endswith('exit "$rollout_rc"'))

        failure_branch = rollout_block.split(
            'echo "ERROR: rollout failed; collecting bounded Kubernetes diagnostics"',
            maxsplit=1,
        )[1]
        required_fragments = (
            'kubectl get "deployment/$deployment" -n "$namespace"',
            ".status.conditions[*]",
            'kubectl get pods -n "$namespace" -l "$selector"',
            "tail -n 5",
            'kubectl get events -n "$namespace"',
            "tail -n 40",
            'kubectl describe "$pod" -n "$namespace"',
            "sed -n '1,220p'",
        )
        for fragment in required_fragments:
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, failure_branch)

        forbidden_fragments = (
            "--all-namespaces",
            "kubectl get all",
            "kubectl get secret",
            "kubectl describe secret",
            "kubectl config view",
            "kubectl logs",
            "printenv",
            "-o yaml",
        )
        for fragment in forbidden_fragments:
            with self.subTest(forbidden=fragment):
                self.assertNotIn(fragment, failure_branch)


if __name__ == "__main__":
    unittest.main()
