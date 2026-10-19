# config-to-vault

Replicate a folder of config files from git into HashiCorp Vault KV v2, one secret per file.

Config usually lives in git as Spring-style YAML or `.properties` files. Some consumers can only read Vault. One example is NiFi's HashiCorp Vault parameter provider, which reads one secret per parameter context. `config-to-vault` resolves each file for a profile, flattens it to key/value pairs and writes it to `<base>/<profile>/<name>`.

- Dry run by default. `--apply` writes.
- Unchanged secrets are not rewritten. The plan lists changed key names, never values (unless `--show-values`).
- `--prune` removes secrets whose file was deleted.

## Install

```sh
pip install .            # or: uv tool install .
docker build -t config-to-vault .
```

Python 3.11 or newer.

## Quick start

```sh
export VAULT_ADDR=http://127.0.0.1:8200
export VAULT_TOKEN=...            # see "Vault authentication"

# Plan only
config-to-vault --source-dir examples/config --shared-base application \
  --profile prod --vault-base secret/my-app

# Write
config-to-vault --source-dir examples/config --shared-base application \
  --profile prod --vault-base secret/my-app --apply
```

Example output:

```
Mode: dry run (no changes written, use --apply)
created    billing -> secret/my-app/prod/billing
    4 keys
updated    orders-service -> secret/my-app/prod/orders-service
    changed: orders.retries

Summary: 1 created, 1 updated, 2 unchanged, 0 failed
```

The exit code is 1 if any file fails, 2 for usage or configuration errors. Before reading anything, the target mount is checked to be KV v2, so a KV v1 mount is refused instead of getting `data/` folders.

## Sources

Choose where files come from.

1. Local folder (default): `--source-dir PATH`. Use this in CI after checking the repo out, with any git host.
   Reads `*.yml`, `*.yaml`, `*.properties` and `*.json` recursively. The secret name is the file name without extension, so names must be unique.
2. Config Server: `--config-server 'https://cs.example/{name}/{profile}'`. Each name is resolved by a Spring Cloud Config Server instead of locally. Set `CONFIG_SERVER_TOKEN` for a bearer token. Names come from `--source-dir` or `--bitbucket-url`.
3. Bitbucket Cloud listing: `--bitbucket-url https://bitbucket.org/<ws>/<repo>/src/<branch>/<path>`. Lists the YAML file names in that folder through the API (`BITBUCKET_EMAIL` and `BITBUCKET_API_TOKEN`) and needs `--config-server` to resolve them.

### Local resolution rules

- YAML files may hold several documents, and `.properties` files may be split with `#---` or `!---` lines. As in Spring Boot 2.4+, documents apply in file order and later ones win. A document with `spring.config.activate.on-profile` (or legacy `spring.profiles`) applies only when it matches `--profile`. The value can be a comma-separated list or a YAML list of profile expressions (`prod`, `!dev`, `prod | eu`, `(prod | eu) & !dev`).
- Nested YAML is flattened to dotted keys (`a.b.c`, lists as `a[0]`). Scalars keep their type. `null` and empty lists become `""`.
- `.properties` files follow `java.util.Properties` rules, including `\` escapes and line continuations.
- `spring.profile*` and `spring.config.activate*` keys are removed from the output.
- With `--shared-base application`, `application.yml` (or `.yaml`, `.properties`, `.json`) in the root of `--source-dir` is merged first and not written as a secret itself.
- A list set by a later document (or by the file over the shared base) replaces the earlier list as a whole, as Spring's binder does.

### Config Server merge

The Config Server lists `propertySources` highest precedence first, so earlier sources win. Lists are replaced as a whole and profile keys are removed. A response without property sources fails that file instead of emptying the secret.

## Flags

| Flag | Meaning |
| --- | --- |
| `--profile` | Required. Profile to resolve and the `{profile}` path value. |
| `--vault-base` | Required. Base path. The first segment is the KV v2 mount. |
| `--vault-path-template` | Default `{base}/{profile}/{name}`. Must contain `{name}`; only `{base}`, `{profile}` and `{name}` are allowed. |
| `--source-dir` | Folder with config files. Default: current directory. |
| `--shared-base NAME` | Shared base file merged first (local resolution only). |
| `--config-server URL_TEMPLATE` | Resolve through a Config Server. URL contains `{name}` and optionally `{profile}`. |
| `--bitbucket-url` | List names from Bitbucket Cloud. Needs `--config-server`. |
| `--apply` | Write to Vault. |
| `--prune` | Delete secrets in the target folder that have no source file. Needs a template ending in `/{name}`, with `{name}` used once. |
| `--show-values` | Print values in the plan. Off by default. |
| `--workers` | Thread pool size. Default 5. |

## Environment

| Variable | Meaning |
| --- | --- |
| `VAULT_ADDR` | Required. |
| `VAULT_TOKEN` | Token auth. |
| `VAULT_ROLE_ID`, `VAULT_SECRET_ID` | AppRole auth (mount `approle`). |
| `VAULT_K8S_ROLE`, `VAULT_K8S_MOUNT` | Kubernetes auth. Mount defaults to `kubernetes`. |
| `VAULT_NAMESPACE` | Sent as `X-Vault-Namespace`. |
| `CONFIG_SERVER_TOKEN` | Bearer token for the Config Server. |
| `BITBUCKET_EMAIL`, `BITBUCKET_API_TOKEN` | Bitbucket Cloud API credentials. |

Auth order: token, then AppRole, then Kubernetes. A dry run still needs read access to Vault to compute the plan.

## Vault policy

Least privilege for `--vault-base secret/my-app` and profile `prod`. Replace the mount and path with yours. KV v2 policies use `data/` and `metadata/` paths.

```hcl
# Dry run and --apply
path "secret/data/my-app/prod/*" {
  capabilities = ["read", "create", "update"]
}

# Only for --prune
path "secret/metadata/my-app/prod" {
  capabilities = ["list"]
}
path "secret/metadata/my-app/prod/*" {
  capabilities = ["delete"]
}
```

`--prune` deletes the secret metadata, which removes all versions.

## CI

### GitHub Actions

```yaml
name: config-to-vault
on:
  push:
    branches: [main]
    paths: ["config/**"]
jobs:
  sync:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v6
      - run: uv tool install .   # or install from your package index
      - run: config-to-vault --source-dir config --profile prod --vault-base secret/my-app --apply
        env:
          VAULT_ADDR: ${{ vars.VAULT_ADDR }}
          VAULT_ROLE_ID: ${{ secrets.VAULT_ROLE_ID }}
          VAULT_SECRET_ID: ${{ secrets.VAULT_SECRET_ID }}
```

### Bitbucket Pipelines

```yaml
pipelines:
  branches:
    main:
      - step:
          image: python:3.12-slim
          script:
            - pip install config-to-vault
            - config-to-vault --source-dir config --profile prod --vault-base secret/my-app --apply
          # Set VAULT_ADDR, VAULT_ROLE_ID and VAULT_SECRET_ID as secured repository variables.
```

Run without `--apply` on pull requests to review the plan.

## Development

```sh
uv sync
uv run ruff check && uv run ruff format --check
uv run pytest
```

Integration test against a Vault dev server:

```sh
docker run -d --name ctv-vault -p 18200:8200 -e VAULT_DEV_ROOT_TOKEN_ID=root hashicorp/vault
CONFIG_TO_VAULT_VAULT=1 uv run pytest
docker rm -f ctv-vault
```

## License

Apache-2.0
