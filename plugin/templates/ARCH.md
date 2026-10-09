# Architecture: templates

Files `vae.py init` copies into a project when they are missing (`VERIFY.py`, `MEMORY.md`, `CLI_GIST.md`, `EPISODES.md`, `Makefile`, `verify.yml`), and page templates the doc skill and the verifier hints point to (`README.md.tmpl`, `ARCH.md.tmpl`, `package.json.tmpl`).

## Why this design

`init` never overwrites: a project owns its files from the first copy, so templates can evolve without touching existing repos. The page templates carry `TODO(...)` markers, so a page copied but not filled fails the prose check's placeholder rule; their `.tmpl` suffix keeps the templates themselves out of that check. `package.json.tmpl` is the library case (ESM, pkgroll build, oxlint); an app drops `exports`, `files` and `build`.

## How it works

The `Makefile` declares the verbs the gate runs, and a bare `make` lists them with their usage; undefined verbs print `UNKNOWN[...]` and exit 2, so a fresh project fails closed until each is defined. `setup` installs the tools `mise.toml` pins and only the toolchains a lockfile declares, so an existing npm or poetry project is never migrated silently; the `.env` keys reach every recipe and the app `start` runs. `verify.yml` is added only for a GitHub remote where `CONFIG["ci"]` is not `False` and no workflow already runs the verification.

## Operations

- **Configuration:** `VERIFY.py` documents every `CONFIG` key inline; it is the project's policy file from then on.
- **Deployment:** templates ship inside the plugin; a template change reaches new projects only, by design.

## Security and privacy

Of the Makefile template's own recipes, only `setup` reaches the network, and only for toolchains the project declares. With `mise.toml` or `.tool-versions`, it downloads the official mise installer over HTTPS when mise is missing, and `mise install` fetches the pinned tools. With `uv.lock`, `bun.lock` or `bun.lockb`, it downloads the official uv or bun installer over HTTPS when that tool is missing, and `uv sync --locked` or `bun install --frozen-lockfile` fetches the locked dependencies. `verify.yml` runs on GitHub's runners, which pull its checkout and setup actions before running `make setup` and `make verify`. Templates contain no secrets and no personal data; `.env` stays gitignored.
