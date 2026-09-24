# Local Docker image — rebuild after every change set

After **any** change set that can affect the running app (`src/**`, templates, static JS/CSS, `scripts/entrypoint.sh`, `Dockerfile`, `docker-compose.yml`, `requirements*.txt`, `pyproject.toml`, `VERSION`), rebuild and restart the local container **before reporting the work as done and before committing**:

```powershell
docker compose build
docker compose up -d
```

Then verify:

- `docker compose ps` shows the service `running` / `healthy`.
- `http://127.0.0.1:8080/login` returns 200 (console login: the operator's local test account; never print its password).
- When the change touches UI or cloud-init, exercise it in the rebuilt container (read-only checks; do not save or modify operator data).

Skip only for changes that cannot alter the image (markdown docs, `.claude/` / `.cursor/` rules, agents, skills, tests-only edits).

This runs in addition to the `commit-and-release` skill's local Trivy gate, which builds its own image for scanning and does not restart the local container. Do not touch `README.md` as part of this step (see `readme.md`).
