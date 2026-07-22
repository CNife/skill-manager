# skill-manager-e2e-fixture

Minimal public fixture repo for skill-manager's end-to-end happy-path tests.

This file and `SKILL.md` are the **canonical content**, mirrored to the live GitHub repo [`CNife/skill-manager-e2e-fixture`](https://github.com/CNife/skill-manager-e2e-fixture). Canonical lives here under `docs/e2e-fixture/` so fixture state is versioned with the product and reset is deterministic.

## Layout

- Skill at repo root (`path: "."`)
- Public, anonymously clonable over HTTPS

## Declaration (happy path)

```json
{"name": "e2e-fixture", "repo": "CNife/skill-manager-e2e-fixture", "path": "."}
```

## Create / reset

Tests only **clone** this fixture; they never push. If the live repo drifts, recreate from these canonical files (from the skill-manager repo root):

```bash
rm -rf /tmp/e2e-fix && mkdir -p /tmp/e2e-fix
cp docs/e2e-fixture/SKILL.md docs/e2e-fixture/README.md /tmp/e2e-fix/
cd /tmp/e2e-fix
git init -b main
git add -A
git commit -m "e2e fixture"
# create (first time):
gh repo create CNife/skill-manager-e2e-fixture --public --source=. --remote=origin --push
# reset (subsequent times), from the same /tmp/e2e-fix checkout:
git push --force origin main
```
