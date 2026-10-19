"""Command line entry point."""

import argparse
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from .plan import build_plan, prune_dir, vault_path
from .resolve import merge_flat, merge_property_sources, resolve_file
from .sources import ConfigServerClient, list_bitbucket_files, list_local_files
from .vault import VaultClient, describe_error

DEFAULT_TEMPLATE = "{base}/{profile}/{name}"


@dataclass
class Result:
    name: str
    path: str = ""
    action: str = ""
    changed: list[str] = field(default_factory=list)
    old: dict = field(default_factory=dict)
    new: dict = field(default_factory=dict)
    error: str = ""


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="config-to-vault",
        description="Replicate a folder of config files from git into Vault KV v2. Dry run unless --apply is given.",
    )
    p.add_argument("--profile", required=True, help="Spring profile to resolve and the {profile} path value")
    p.add_argument("--vault-base", required=True, help="Base path; first segment is the KV v2 mount")
    p.add_argument("--vault-path-template", default=DEFAULT_TEMPLATE, help="Default: %(default)s")
    p.add_argument("--source-dir", type=Path, help="Folder with config files (default: current directory)")
    p.add_argument("--shared-base", metavar="NAME", help="File in --source-dir (e.g. application) merged first")
    p.add_argument("--config-server", metavar="URL_TEMPLATE", help="Resolve via Config Server; URL contains {name}")
    p.add_argument("--bitbucket-url", help="List file names from a Bitbucket Cloud folder instead of --source-dir")
    p.add_argument("--apply", action="store_true", help="Write to Vault (default is a dry run)")
    p.add_argument("--prune", action="store_true", help="Delete Vault secrets whose file no longer exists")
    p.add_argument("--show-values", action="store_true", help="Print secret values in the plan")
    p.add_argument("--workers", type=int, default=5)
    return p


def collect_entries(args, parser) -> list[tuple[str, Path | None]]:
    """Return (name, local path) pairs; the path is None when names come from Bitbucket."""
    if args.bitbucket_url:
        names = list_bitbucket_files(
            args.bitbucket_url, os.getenv("BITBUCKET_EMAIL", ""), os.getenv("BITBUCKET_API_TOKEN", "")
        )
        files = [(Path(n).stem, None) for n in names]
    else:
        source_dir = args.source_dir or Path(".")
        if not source_dir.is_dir():
            parser.error(f"--source-dir {source_dir} is not a directory")
        paths = list_local_files(source_dir)
        if args.shared_base:
            paths = [p for p in paths if not (p.parent == source_dir and p.stem == args.shared_base)]
        files = [(p.stem, p) for p in paths]

    seen: set[str] = set()
    for name, _ in files:
        if name in seen:
            parser.error(f"Duplicate secret name '{name}' (same file name in several folders or extensions)")
        seen.add(name)
    return files


def find_shared_base(source_dir: Path, name: str) -> Path | None:
    matches = [p for p in list_local_files(source_dir) if p.parent == source_dir and p.stem == name]
    return matches[0] if matches else None


def process(name, path, args, vault, config_client, shared_path) -> Result:
    result = Result(name)
    try:
        result.path = vault_path(args.vault_path_template, args.vault_base, args.profile, name)
        if config_client:
            result.new = merge_property_sources(config_client.fetch(name, args.profile))
        else:
            result.new = resolve_file(path, args.profile)
            if shared_path:
                result.new = merge_flat(resolve_file(shared_path, args.profile), result.new)
        existing = vault.read_secret(result.path)
        result.old = existing or {}
        result.action, result.changed = build_plan(existing, result.new)
        if args.apply and result.action != "unchanged":
            vault.write_secret(result.path, result.new)
    except Exception as exc:
        result.error = describe_error(exc)
    return result


def format_result(r: Result, show_values: bool) -> list[str]:
    if r.error:
        return [f"failed     {r.name}: {r.error}"]
    lines = [f"{r.action:<10} {r.name} -> {r.path}"]
    if r.action == "created":
        lines.append(f"    {len(r.new)} keys")
    for key in r.changed if r.action != "unchanged" else []:
        if not show_values:
            if r.action == "updated":
                lines.append(f"    changed: {key}")
        elif r.action == "created":
            lines.append(f"    {key}={json.dumps(r.new[key])}")
        else:
            old = json.dumps(r.old[key]) if key in r.old else "<absent>"
            new = json.dumps(r.new[key]) if key in r.new else "<removed>"
            lines.append(f"    {key}: {old} -> {new}")
    return lines


def prune(args, vault, names: set[str]) -> tuple[list[str], str]:
    """Delete (or list) secrets in the target folder that have no source file. Returns (names, error)."""
    folder = prune_dir(args.vault_path_template, args.vault_base, args.profile)
    try:
        extras = sorted(set(vault.list_secrets(folder)) - names)
        for name in extras:
            if args.apply:
                vault.delete_secret(f"{folder}/{name}")
    except Exception as exc:
        return [], describe_error(exc)
    return extras, ""


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.workers < 1:
        parser.error("--workers must be at least 1")
    if args.bitbucket_url and (args.source_dir or args.shared_base):
        parser.error("--bitbucket-url cannot be combined with --source-dir or --shared-base")
    if args.bitbucket_url and not args.config_server:
        parser.error("--bitbucket-url only lists file names; add --config-server to resolve them")
    if args.shared_base and args.config_server:
        parser.error("--shared-base only applies to local resolution")
    try:
        first_path = vault_path(args.vault_path_template, args.vault_base, args.profile, "x")
        if args.prune:
            prune_dir(args.vault_path_template, args.vault_base, args.profile)
    except ValueError as exc:
        parser.error(str(exc))

    addr = os.getenv("VAULT_ADDR", "")
    if not addr:
        parser.error("VAULT_ADDR is not set")

    try:
        entries = collect_entries(args, parser)
        if not entries:
            print("error: no config files found", file=sys.stderr)
            return 2
        shared_path = None
        if args.shared_base:
            shared_path = find_shared_base(args.source_dir or Path("."), args.shared_base)
            if not shared_path:
                parser.error(f"shared base '{args.shared_base}' not found in the source directory")
        config_client = None
        if args.config_server:
            config_client = ConfigServerClient(args.config_server, os.getenv("CONFIG_SERVER_TOKEN", ""))
        vault = VaultClient(
            addr,
            token=os.getenv("VAULT_TOKEN", ""),
            role_id=os.getenv("VAULT_ROLE_ID", ""),
            secret_id=os.getenv("VAULT_SECRET_ID", ""),
            k8s_role=os.getenv("VAULT_K8S_ROLE", ""),
            k8s_mount=os.getenv("VAULT_K8S_MOUNT", ""),
            namespace=os.getenv("VAULT_NAMESPACE", ""),
        )
        vault.check_kv2(first_path)
    except Exception as exc:
        print(f"error: {describe_error(exc)}", file=sys.stderr)
        return 2

    print(f"Mode: {'apply' if args.apply else 'dry run (no changes written, use --apply)'}")
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(process, n, p, args, vault, config_client, shared_path) for n, p in entries]
        results = sorted((f.result() for f in futures), key=lambda r: r.name)

    for result in results:
        print("\n".join(format_result(result, args.show_values)))

    failures = [f"{r.name}: {r.error}" for r in results if r.error]
    deleted: list[str] = []
    if args.prune:
        deleted, error = prune(args, vault, {n for n, _ in entries})
        for name in deleted:
            print(f"{'deleted' if args.apply else 'would-delete':<10} {name}")
        if error:
            failures.append(f"prune: {error}")

    counts = {a: sum(r.action == a for r in results) for a in ("created", "updated", "unchanged")}
    summary = ", ".join(f"{n} {a}" for a, n in counts.items())
    if args.prune:
        summary += f", {len(deleted)} {'deleted' if args.apply else 'would-delete'}"
    print(f"\nSummary: {summary}, {len(failures)} failed")
    if failures:
        print("Failures:", file=sys.stderr)
        for line in failures:
            print(f"  - {line}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
