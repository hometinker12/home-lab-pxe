# Git workflow trigger

- When the user says **"commit"** (or "commit all pending changes"), load the `commit-and-release` skill and run its full sequence without asking for confirmation: local gates → commit → push `develop` → SHA-matched CI green → `security-reviewer` PASS → PR `develop` → `release` → merge → release smoke green.
- **Stop at `release`.** Never push to `main`, merge to `main`, or open a `release` → `main` PR unless the user explicitly asks in a separate request. Push to `main` triggers Docker Hub publish.
- Work on `develop` unless the user names another branch.
