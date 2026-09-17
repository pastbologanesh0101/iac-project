# Infrastructure as Code Project

A genuinely runnable **Ansible** configuration-management project: idempotent
local provisioning with roles, Jinja2 templates, and a real test suite that
executes `ansible-playbook` and asserts on its output.

This project's angle is **configuration management / server provisioning**
(as opposed to cloud resource orchestration, which is the Terraform-flavored
sibling project). Everything here targets `localhost` via Ansible's `local`
connection plugin, so it runs with **no cloud account, no SSH, and no
credentials** — just Python and `pip install ansible`.

## What it provisions

Running `site.yml` against the `local` inventory group applies three roles:

| Role | What it does |
|---|---|
| `webapp_deploy` | Creates a directory tree and renders an **nginx-style vhost config** (`nginx.conf.j2`) and a **systemd-unit-style service file** (`webapp.service.j2`) from Jinja2 templates, plus a release marker file. |
| `user_provision` | Simulates provisioning system users by managing "user records" as JSON entries in a local file (`users.json`), instead of touching real system accounts. |
| `idempotent_baseline` | Demonstrates classic idempotency patterns: a `creates:`-guarded one-time initialization command, and a `state: present`-managed file that never changes across runs. |

## Why sandboxed to a local temp dir, not real system config

This project intentionally **never writes to real system paths** like
`/etc/nginx`, `/etc/systemd/system`, or `/etc/passwd`, and never creates real
OS user accounts. Every file a role writes is rooted under a project-local
`./sandbox/` directory (`sandbox_root`, defined in `group_vars/local.yml`),
and `user_provision` manages a plain JSON file instead of calling Ansible's
`user:` module.

This is deliberate, for three reasons:

1. **Safety** — it can be run and re-run on a laptop, in CI, or by anyone
   cloning this repo, without `sudo`, without risking a real service
   config, and without any chance of locking yourself out of a real user
   account.
2. **Reviewability** — the whole point of this project is to demonstrate
   patterns (templates, idempotency, roles, variable precedence) that
   transfer directly to real infrastructure once you point `sandbox_root`
   at `/` and add privilege escalation — the *mechanics* are identical,
   only the destination path changes.
3. **Testability** — the test suite in `tests/` needs deterministic,
   disposable output it can assert against and wipe between runs. A
   sandbox directory is trivial to inspect and `rm -rf`; real system state
   is not.

## Layout

```
inventory.ini              # [local] group -> localhost, ansible_connection=local
ansible.cfg                # project-local Ansible settings
site.yml                   # main playbook, applies all roles to [local]
group_vars/local.yml       # variables shared by the local group
host_vars/localhost.yml    # per-host override (bumps worker count)
roles/
  webapp_deploy/
    defaults/main.yml
    templates/nginx.conf.j2
    templates/webapp.service.j2
    tasks/main.yml
  user_provision/
    defaults/main.yml
    tasks/main.yml
  idempotent_baseline/
    defaults/main.yml
    tasks/main.yml
tests/test_playbook.py     # real subprocess-driven integration tests
.github/workflows/tests.yml
sandbox/                   # generated output (gitignored)
```

## Requirements

- Python 3.11+
- `pip install -r requirements.txt` (installs `ansible` and `pytest`)

No collections, no cloud SDKs, no Docker.

## How to run

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

ansible-playbook -i inventory.ini site.yml
```

Run it a second time to see true idempotency — every task reports `ok`
(no `changed`) because the templates render byte-identical content and the
`creates:`-guarded task is skipped:

```
PLAY RECAP *********************************************************************
localhost                  : ok=11   changed=0    unreachable=0    failed=0    skipped=1    rescued=0    ignored=0
```

Inspect what was generated:

```bash
cat sandbox/etc/nginx/sites-available/demoapp.conf
cat sandbox/etc/systemd/system/demoapp.service
cat sandbox/var/lib/iac/users.json
```

### Example output (first run)

```
TASK [webapp_deploy : Ensure sandbox directory structure exists] ***************
changed: [localhost] => (item=/path/to/iac-project/sandbox)
...
TASK [webapp_deploy : Render nginx-style site config from template] ************
changed: [localhost]

TASK [user_provision : Report user provisioning summary] ***********************
ok: [localhost] => {
    "msg": "Managed 3 user record(s) in /path/to/iac-project/sandbox/var/lib/iac/users.json (changed=True)"
}

PLAY RECAP *********************************************************************
localhost                  : ok=12   changed=10   unreachable=0    failed=0    skipped=0    rescued=0    ignored=0
```

## Running the tests

The test suite (`tests/test_playbook.py`) is a real integration harness, not
mocks: it shells out to `ansible-playbook` via `subprocess`, running the
playbook **twice**, and then asserts against the actual files it produced:

```bash
python3 -m pytest -v tests/test_playbook.py
```

It checks, among other things:

- The playbook exits `0` and reports `changed > 0` on the first run.
- The nginx-style config, systemd-unit-style file, release marker, and
  `creates:` guard marker all exist on disk after the first run.
- The Jinja2-rendered files contain the *correct* variable values (port,
  domain, version, and the `host_vars`-overridden worker count).
- The JSON user-records file contains exactly the expected user entries
  with the right `uid`/`role`/`shell` fields.
- Real `/etc/passwd` was never touched.
- **The second run reports `changed == 0`** — true idempotency, not just
  "it ran twice without erroring."
- The `creates:`-guarded task is skipped (not re-run) on the second pass.

CI (`.github/workflows/tests.yml`) runs this same suite on every push and
pull request, on Python 3.11 and 3.12, on `ubuntu-latest`.

## License

MIT — see [LICENSE](LICENSE).
