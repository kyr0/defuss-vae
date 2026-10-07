# Stack defaults

Defaults for the `Makefile` verbs per toolchain. Session start injects the sections for the stacks a repository uses, and the gate cites the `lint`, `test`, `coverage` and `e2e` lines when a verb is missing. Pin every tool in `mise.toml` (`make setup` runs `mise install`) and in the ecosystem's own pin file, so IDEs, CI and agents agree. `make coverage` prints a total the gate parses: `TOTAL <n>%`, a `total:` or `| Total |` row, or an `All files |` table.

## go
- lint: `golangci-lint run` (v2, includes vet) + `go mod tidy -diff`
- test: `go test -race ./...`
- coverage: `go test -coverprofile=tmp/cover.out ./... && go tool cover -func=tmp/cover.out | tail -n1`
- e2e: `go build -o output/app ./cmd/app`, then run it on `input/`
- pin: `go` and `toolchain` in `go.mod`; commit `go.sum`; `-race` needs cgo and a C compiler.

## rust
- lint: `cargo clippy --all-targets --locked -- -D warnings` + `cargo fmt --check`
- test: `cargo test --locked`
- coverage: `cargo llvm-cov` (its `TOTAL` row starts with region coverage, stricter than lines)
- e2e: `cargo build --release --locked`, then run `target/release/<bin>` on `input/`
- pin: `rust-toolchain.toml` with the clippy, rustfmt and llvm-tools-preview components; commit `Cargo.lock`.

## jvm
- lint: `./gradlew spotlessCheck classes testClasses` with javac `-Xlint:all -Werror`
- test: `./gradlew test` on the JUnit Platform
- coverage: JaCoCo CSV report, then `awk -F, 'NR>1{m+=$8;c+=$9}END{printf "TOTAL %.1f%%\n",100*c/(m+c)}' build/reports/jacoco/test/jacocoTestReport.csv`
- e2e: `./gradlew installDist`, then run `build/install/<app>/bin/<app>` on `input/`
- pin: Gradle toolchain for the JDK, versions in `gradle/libs.versions.toml`; commit the wrapper (`gradlew`, `gradle/wrapper/`); Maven projects use the `./mvnw` equivalents.

## dotnet
- lint: `dotnet format --verify-no-changes` + `dotnet build -warnaserror`
- test: `dotnet test` with xUnit
- coverage: `dotnet test /p:CollectCoverage=true` (coverlet.msbuild in each test project prints a `| Total |` row)
- e2e: `dotnet publish -c Release -o output/app`, then run it on `input/`
- pin: SDK in `global.json`; `Directory.Build.props` sets `Nullable` and `TreatWarningsAsErrors`; versions in `Directory.Packages.props`; `RestorePackagesWithLockFile`, and `dotnet restore --locked-mode` in CI.

## js
- lint: `bunx oxlint --deny-warnings` (plain oxlint exits 0 on findings)
- test: `bun test`; a Vite app: `vitest run`, which shares the Vite config and transforms
- coverage: `bun test --coverage` (vitest: `vitest run --coverage` with `@vitest/coverage-v8`)
- e2e: `bun pm pack`, then `bun add <tgz>` in a clean consumer that runs on `input/`
- pin: `packageManager` in `package.json`; new packages are ESM, libraries build with pkgroll; never npm, yarn or pnpm.

## python
- lint: `uv run ruff check .`
- test: `uv run pytest`
- coverage: `uv run pytest --cov`
- e2e: `uv build`, then `uv run --isolated --no-project --with dist/<wheel>` on `input/`
- pin: `uv.lock`; `uv run --env-file .env` loads config; never pip or poetry; no venv activation, which agent shells do not keep.

## web
- e2e: the built app, served via `make start`, driven in a real Playwright browser (`bunx playwright install --with-deps chromium` or `uv run playwright install --with-deps chromium`): WebGL2 on GPU-less CI via the launch args `--use-angle=swiftshader --enable-unsafe-swiftshader`, real network, permissions via `context.grantPermissions([...])` (Python `grant_permissions`); fail on console errors and failed requests; the report (`outputDir`) goes to `output/`.
- https: Caddy's internal CA covers `localhost` (trusted on first run): `caddy reverse-proxy --from localhost:8443 --to :3000`, static files `caddy file-server --domain localhost --root <dir>`.
