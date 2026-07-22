---
name: e2e-fixture
description: Minimal fixture skill for skill-manager end-to-end happy-path tests. Exists only so skill-manager can declare, sync, and link a real publicly-clonable GitHub source; does nothing when loaded.
---

# E2E Fixture

A minimal skill fixture used by skill-manager's end-to-end happy-path tests. It gives `skill-manager sync` a real, publicly-clonable GitHub source containing a `SKILL.md` to link into `.agents/skills/`.

It is not a real skill and does nothing when loaded by pi.
