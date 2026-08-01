# Contributing

Thank you for your interest in `SAG-plus`, an optimization fork of
[Zleap-AI/SAG](https://github.com/Zleap-AI/SAG).

## Environment

- Backend: Python ≥ 3.11 (`apps/api`)
- Frontend: Node ≥ 20 (`apps/web`, use `npm ci`)

## Checks

```bash
cd apps/api && ruff check sag_api/ sag_agent/ tests/ && python -m pytest -q
cd apps/web && npm run typecheck && npm run lint && npm run test:unit && npm run build
```

## Workflow

1. Fork or branch from [`sishuiguozi/SAG-plus`](https://github.com/sishuiguozi/SAG-plus) `main`.
2. Keep commits focused and explain what changed and why.
3. Include tests and describe loading, empty, and error states for UI changes.
4. Open a pull request to this repository's `main` after all checks pass.

When changing a compatibility patch, vector-write behavior, retrieval behavior,
or a setting exposed in the UI, also update the matching entry in
`docs/ARCHITECTURE_PATCHES.md` or `docs/SAG_OPTIMIZATION_2026.md`.

## Issues

Please include reproduction steps, expected behavior, actual behavior, and your environment (OS, Python, and Node versions).
