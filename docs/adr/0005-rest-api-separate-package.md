# REST API ships as a separate package depending on the core

## Status

accepted

## Context

The core `lucon` client is deliberately **zero-dependency** (`pyproject` `dependencies = []`), a property worth protecting: `import lucon` must never pull in a web framework. The REST API needs FastAPI, uvicorn, and pydantic-settings.

## Decision

Ship the API as a separate distribution, **`lucon-api`**, with its own `pyproject.toml` and version, depending on `lucon` plus the web stack. The repository becomes a monorepo: `packages/lucon/` (the existing zero-dep core, moved from the repo root) and `packages/lucon-api/`. Core and API release independently.

## Considered options

- **Subpackage behind an optional extra** (`src/lucon/api/`, installed via `lucon[api]`): keeps one distribution and one version, but muddies the "lucon is zero-dep" story (the extra's deps sit in the same project metadata), couples the API's release cadence to the core's, and risks an accidental top-level import of the `api` subpackage dragging FastAPI into a core-only environment.
- **Single package with the web deps as hard dependencies**: simplest layout, but destroys the zero-dependency core outright — unacceptable, since that is an explicit design value.

## Consequences

- Two `pyproject.toml` files, two release streams, and CI that builds/tests both — meaningful overhead for one product.
- The existing core moves from the repo root into `packages/lucon/`; the import path for consumers is unchanged (the package is still `lucon`).
- The dependency boundary is enforced structurally: `lucon-api` depends on `lucon`, never the reverse.
