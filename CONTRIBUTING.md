# Contributing to home-lab-pxe

Thank you for contributing. This project is a Docker-packaged PXE/iPXE boot server with a web console for machine discovery, Linux (cloud-init) and Windows Server (unattend + Cloudbase-Init) image deploy, and encrypted local-account storage.

## Development setup

### Requirements

- Python 3.12 is recommended
- Docker and Docker Compose for container testing
- A Linux host (or Linux VM) for real DHCP/TFTP; Docker Desktop on Windows cannot usefully serve PXE to a LAN

Install the development dependencies (once `requirements-dev.txt` exists):

```shell
python -m pip install -r requirements-dev.txt
```

Create a local configuration:

```powershell
Copy-Item .env.example .env
```

Generate unique `SECRET_KEY` and `ENCRYPTION_KEY` values. Never commit `.env`, credentials, SSH keys, certificates, cloud-init user-data, or `unattend.xml` with real passwords.

## Branch and pull request workflow

- Create feature and fix branches from `develop`.
- Submit pull requests targeting `develop`.
- Keep each pull request focused on one feature or fix.
- Explain the motivation and user-visible behavior.
- Include tests for new or changed behavior.
- Do not include unrelated formatting or refactoring.
- Do not update `VERSION` unless preparing a release.

Maintainers promote tested changes from `develop` to `release` after CI and security checks pass. Promotion from `release` to `main` is a separate manual pull request. Pushing to `main` runs [`.github/workflows/docker-publish.yml`](.github/workflows/docker-publish.yml), which publishes `hometinker12/home-lab-pxe` to Docker Hub and creates a GitHub Release when the `VERSION` tag is new.

## Project structure

Target layout (see `PLAN.md` and `ARCHITECTURE.md`):

- `src/routes/` — HTTP route modules (admin UI, inventory, iPXE, cloud-init, Windows seeds).
- `src/boot/` — boot-policy and iPXE script generation.
- `src/cloudinit/` — per-machine Linux user-data / meta-data / vendor-data.
- `src/windows/` — unattend.xml and Cloudbase-Init metadata.
- `src/inventory/` — machine registry, lifecycle, and encrypted local accounts.
- `src/templates/` and `src/static/` — administration interface.
- `src/models.py` and `src/db.py` — database models and initialization.
- `src/auth.py`, `src/csrf.py`, and `src/security.py` — security-sensitive components.
- `tests/` — pytest test suite.
- `.github/workflows/` — CI and integration smoke tests.

Avoid adding more routes directly to `src/app.py` when they can be placed in a focused module under `src/routes/`.

## Database schema changes

SQLite is the supported production engine. Do not add Alembic. After metadata `create_all`, add an idempotent `_migrate_*` helper in `src/db.py` and call it from `init_db()`.

Typical sequence:

1. Inspect the table.
2. Return early if the table is missing (`create_all` adds new tables).
3. Return early if the column or index already exists.
4. `ALTER TABLE ... ADD COLUMN` or create the index.
5. Backfill existing rows when a column default would otherwise leave legacy data incorrect.
6. Cover the helper in tests. Running the helper twice must be a no-op.

Use SQLite SQL only.

## Coding standards

Python code is checked with Ruff:

- Maximum line length: 120 characters
- Target compatibility: Python 3.12
- Enabled rule groups: `E`, `F`, `I`, and `UP`
- First-party imports use the `src` package

Format and check the code before submitting:

```shell
python -m ruff format src tests
python -m ruff check src tests
python -m ruff format --check src tests
```

Prefer clear, focused functions and established project patterns over introducing new abstractions.

## Tests

Run the full test suite:

```shell
python -m pytest
```

A complete local check is:

```shell
python -m ruff check src tests
python -m ruff format --check src tests
python -m pytest
```

Before pushing image-affecting changes to `develop`, also build and scan the Docker image with Trivy (same High/Critical gate as CI publish / release smoke). Requires Docker; uses a local `trivy` CLI when installed, otherwise runs via the `aquasec/trivy` image:

```powershell
powershell -File scripts/trivy-local-scan.ps1
```

Test files should be named `tests/test_<module>.py`. Reuse fixtures from `tests/conftest.py` and mock DHCP, TFTP, dnsmasq, and image downloads.

Changes involving routes, authentication, boot policy, cloud-init, Windows guest-init, credential encryption, Docker behavior, or public APIs should include regression or smoke-test coverage.

## Security requirements

Security regressions block acceptance. In particular:

- Never commit or log secrets, SSH private keys, cloud-init user-data, unattend passwords, or decrypted local accounts.
- Persist Linux root and Windows local Administrator username+password only as Fernet ciphertext (`ENCRYPTION_KEY`). Passwords are write-only in the UI/API.
- Preserve CSRF checks for browser form submissions.
- Do not weaken TLS verification by default.
- Do not enable OpenAPI, debug tracebacks, or insecure defaults in production.
- Unknown machines must never auto-install.
- Keep API keys hashed when they are introduced.
- Preserve a non-root process for the web UI; DHCP/TFTP may need extra Linux capabilities, not a blanket privileged container.

The following flag is intended only for isolated development or tests and must not be recommended for production:

- `PXE_ALLOW_INSECURE_DEFAULTS`

Security-sensitive changes may receive an additional adversarial review before release.

## Documentation

Update documentation alongside behavior changes:

- Update `README.md` only when install/run steps or operator-facing capabilities change. Keep CI, GitHub secrets, branch promotion, and publish internals out of the README (see `CONTRIBUTING.md` and `.github/workflows/` instead).
- Add user-visible changes to the `[Unreleased]` section of `CHANGELOG.md`.
- Update `.env.example` when environment variables are added or renamed.
- Update `PLAN.md` when architecture or milestone status changes.
- Update `ARCHITECTURE.md` when container, console, or security diagrams change.

Release version changes must remain synchronized across:

- `VERSION`
- `pyproject.toml`
- `Dockerfile`
- `docker-compose.yml`
- The README release badge
- Version-related tests

## Pull request checklist

Before opening a pull request, confirm that:

- [ ] The change is based on `develop`.
- [ ] Ruff linting passes.
- [ ] Ruff formatting passes.
- [ ] Relevant pytest tests pass.
- [ ] New behavior has test coverage.
- [ ] Secrets and generated files are excluded.
- [ ] Security implications were considered.
- [ ] README and configuration examples are updated when needed.
- [ ] `CHANGELOG.md` is updated for user-visible changes.
- [ ] The pull request explains what changed and why.

## License

By contributing, you agree that your contribution will be licensed under the repository’s MIT License.
