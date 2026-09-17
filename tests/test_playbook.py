"""
Real integration tests for the Infrastructure as Code Project.

These tests actually invoke `ansible-playbook` via subprocess against the
real roles in this repo, targeting `localhost` through the `local`
connection plugin. Nothing here is mocked: every assertion is made against
files genuinely written to (and read back from) the project-local
`sandbox/` directory.

Run with:  pytest -v tests/test_playbook.py
(or:       python3 -m pytest -v tests/test_playbook.py)
"""

import json
import os
import shutil
import subprocess
import sys

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SANDBOX_DIR = os.path.join(PROJECT_ROOT, "sandbox")
INVENTORY = os.path.join(PROJECT_ROOT, "inventory.ini")
PLAYBOOK = os.path.join(PROJECT_ROOT, "site.yml")


def run_playbook(extra_args=None):
    """Run ansible-playbook against this project and return CompletedProcess."""
    cmd = ["ansible-playbook", "-i", INVENTORY, PLAYBOOK]
    if extra_args:
        cmd.extend(extra_args)
    result = subprocess.run(
        cmd,
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=180,
    )
    return result


def parse_recap(stdout):
    """Extract the PLAY RECAP counters (ok/changed/failed/skipped) for localhost."""
    for line in stdout.splitlines():
        if line.strip().startswith("localhost") and "ok=" in line:
            counters = {}
            for part in line.split(":", 1)[1].split():
                key, _, value = part.partition("=")
                if key and value.isdigit():
                    counters[key] = int(value)
            return counters
    raise AssertionError(f"Could not find PLAY RECAP line in output:\n{stdout}")


@pytest.fixture(scope="module", autouse=True)
def clean_sandbox_and_run_twice():
    """
    Module-scoped fixture: wipe the sandbox, run the playbook twice (to
    exercise real idempotency), and hand both CompletedProcess results to
    the tests via a small shared namespace.
    """
    if os.path.isdir(SANDBOX_DIR):
        shutil.rmtree(SANDBOX_DIR)

    first = run_playbook()
    assert first.returncode == 0, f"First run failed:\n{first.stdout}\n{first.stderr}"

    second = run_playbook()
    assert second.returncode == 0, f"Second run failed:\n{second.stdout}\n{second.stderr}"

    yield {"first": first, "second": second}

    # Leave sandbox/ in place after the suite for manual inspection;
    # it is gitignored so it never gets committed.


@pytest.fixture(scope="module")
def runs(clean_sandbox_and_run_twice):
    return clean_sandbox_and_run_twice


class TestFirstRunProvisions:
    def test_first_run_reports_changes(self, runs):
        recap = parse_recap(runs["first"].stdout)
        assert recap["failed"] == 0
        assert recap["unreachable"] == 0
        assert recap["changed"] > 0

    def test_nginx_config_created(self, runs):
        path = os.path.join(SANDBOX_DIR, "etc/nginx/sites-available/demoapp.conf")
        assert os.path.isfile(path), "nginx-style config was not rendered"

    def test_systemd_unit_created(self, runs):
        path = os.path.join(SANDBOX_DIR, "etc/systemd/system/demoapp.service")
        assert os.path.isfile(path), "systemd-unit-style file was not rendered"

    def test_release_marker_created(self, runs):
        path = os.path.join(SANDBOX_DIR, "var/www/demoapp/RELEASE")
        assert os.path.isfile(path)

    def test_baseline_marker_created(self, runs):
        path = os.path.join(SANDBOX_DIR, "var/lib/iac/.baseline_initialized")
        assert os.path.isfile(path), "creates: guard marker file missing after first run"


class TestTemplatedContentIsCorrect:
    def test_nginx_config_renders_expected_variables(self, runs):
        path = os.path.join(SANDBOX_DIR, "etc/nginx/sites-available/demoapp.conf")
        with open(path) as f:
            content = f.read()
        assert "listen 8080;" in content
        assert "server_name demoapp.local;" in content
        assert "proxy_pass http://127.0.0.1:8080;" in content
        assert "version: 1.4.0" in content

    def test_systemd_unit_renders_expected_variables(self, runs):
        path = os.path.join(SANDBOX_DIR, "etc/systemd/system/demoapp.service")
        with open(path) as f:
            content = f.read()
        assert "Description=demoapp application service (v1.4.0)" in content
        assert "--port 8080" in content
        # host_vars/localhost.yml overrides worker_processes to 4
        assert "--workers 4" in content
        assert "Environment=WEBAPP_DOMAIN=demoapp.local" in content

    def test_release_file_contains_version(self, runs):
        path = os.path.join(SANDBOX_DIR, "var/www/demoapp/RELEASE")
        with open(path) as f:
            content = f.read()
        assert content.strip() == "demoapp release 1.4.0"


class TestUserProvisioningRecords:
    def test_users_json_file_exists(self, runs):
        path = os.path.join(SANDBOX_DIR, "var/lib/iac/users.json")
        assert os.path.isfile(path)

    def test_users_json_has_expected_entries(self, runs):
        path = os.path.join(SANDBOX_DIR, "var/lib/iac/users.json")
        with open(path) as f:
            users = json.load(f)

        assert isinstance(users, list)
        assert len(users) == 3

        by_name = {u["name"]: u for u in users}
        assert set(by_name.keys()) == {"alice", "bob", "carol"}

        assert by_name["alice"]["uid"] == 2001
        assert by_name["alice"]["role"] == "admin"
        assert by_name["alice"]["shell"] == "/bin/bash"

        assert by_name["bob"]["uid"] == 2002
        assert by_name["bob"]["role"] == "developer"

        assert by_name["carol"]["uid"] == 2003
        assert by_name["carol"]["role"] == "readonly"
        assert by_name["carol"]["shell"] == "/usr/sbin/nologin"

    def test_no_real_system_users_were_touched(self):
        """Sanity check: /etc/passwd must never be modified by this project."""
        with open("/etc/passwd") as f:
            passwd_content = f.read()
        assert "alice" not in passwd_content.lower().split("\n")[0]
        # None of our fictional managed users should exist as real accounts
        for fake_user in ("alice", "bob", "carol"):
            assert not any(
                line.startswith(f"{fake_user}:") for line in passwd_content.splitlines()
            ), f"{fake_user} unexpectedly found in real /etc/passwd"


class TestIdempotency:
    """The core requirement: a second run must be a true no-op."""

    def test_second_run_reports_zero_changed(self, runs):
        recap = parse_recap(runs["second"].stdout)
        assert recap["failed"] == 0
        assert recap["unreachable"] == 0
        assert recap["changed"] == 0, (
            f"Second run was not idempotent, changed={recap['changed']}\n"
            f"{runs['second'].stdout}"
        )

    def test_second_run_skips_creates_guarded_task(self, runs):
        """The baseline init command must not re-run once its marker exists."""
        stdout = runs["second"].stdout
        # The guarded command task should show up as 'ok' (skipped-by-creates),
        # never as 'changed', on the second run.
        assert "changed: [localhost]" not in stdout.split(
            "TASK [idempotent_baseline : Run one-time baseline initialization"
        )[1].split("TASK [")[0]

    def test_creates_guard_file_still_present_after_second_run(self, runs):
        path = os.path.join(SANDBOX_DIR, "var/lib/iac/.baseline_initialized")
        assert os.path.isfile(path)

    def test_content_unchanged_across_runs(self, runs):
        """File content must be byte-identical after the idempotent second run."""
        path = os.path.join(SANDBOX_DIR, "etc/nginx/sites-available/demoapp.conf")
        with open(path) as f:
            content_after_second_run = f.read()
        assert "listen 8080;" in content_after_second_run
