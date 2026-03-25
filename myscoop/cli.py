"""
cli.py — Click CLI Entry Point for myscoop

The main command-line interface for the myscoop package manager.
All commands are implemented here, orchestrating the individual modules.

Usage:
    python myscoop.py install postman
    python myscoop.py list
    python myscoop.py search mysql
"""

import json
import os
import sys
import shutil
from pathlib import Path
from typing import Optional

import click
from colorama import Fore, Style, init as colorama_init

from myscoop.manifest import Manifest, ManifestNotFoundError
from myscoop.downloader import Downloader, DownloadError
from myscoop.extractor import Extractor, ExtractionError
from myscoop.silent_installer import SilentInstaller, SilentInstallError
from myscoop.shim import ShimManager
from myscoop.path_manager import PathManager, PathManagerError
from myscoop.dependency import DependencyResolver, CircularDependencyError
from myscoop.bucket import BucketManager, BucketError
from myscoop.metadata import AppMetadata


# ──────────────────────────────────────────────
# Directory setup
# ──────────────────────────────────────────────

# All paths relative to the user's home directory
HOME = os.path.expanduser("~")
MYSCOOP_ROOT = os.path.join(HOME, "myscoop")
APPS_DIR = os.path.join(MYSCOOP_ROOT, "apps")
CACHE_DIR = os.path.join(MYSCOOP_ROOT, "cache")
SHIMS_DIR = os.path.join(MYSCOOP_ROOT, "shims")
BUCKETS_DIR = os.path.join(MYSCOOP_ROOT, "buckets")

# Also look for local buckets in the project directory
# (for development / testing with bundled manifests)
PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOCAL_BUCKETS_DIR = os.path.join(PROJECT_DIR, "buckets")


def get_buckets_dir() -> str:
    """
    Return the buckets directory to use.
    Prefers the myscoop home directory, falls back to local project buckets.
    """
    if os.path.exists(BUCKETS_DIR) and os.listdir(BUCKETS_DIR):
        return BUCKETS_DIR
    if os.path.exists(LOCAL_BUCKETS_DIR) and os.listdir(LOCAL_BUCKETS_DIR):
        return LOCAL_BUCKETS_DIR
    return BUCKETS_DIR


def ensure_dirs() -> None:
    """Create all required directories if they don't exist."""
    for d in [APPS_DIR, CACHE_DIR, SHIMS_DIR, BUCKETS_DIR]:
        os.makedirs(d, exist_ok=True)


# ──────────────────────────────────────────────
# Error handling decorator
# ──────────────────────────────────────────────

def handle_errors(func):
    """Decorator to catch all known errors and print user-friendly messages."""
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except ManifestNotFoundError as e:
            click.echo(f"\n{Fore.RED}Error: {e}{Style.RESET_ALL}")
            sys.exit(1)
        except DownloadError as e:
            click.echo(f"\n{Fore.RED}Download Error: {e}{Style.RESET_ALL}")
            sys.exit(1)
        except ExtractionError as e:
            click.echo(f"\n{Fore.RED}Extraction Error: {e}{Style.RESET_ALL}")
            sys.exit(1)
        except SilentInstallError as e:
            click.echo(f"\n{Fore.RED}Install Error: {e}{Style.RESET_ALL}")
            sys.exit(1)
        except CircularDependencyError as e:
            click.echo(f"\n{Fore.RED}Dependency Error: {e}{Style.RESET_ALL}")
            sys.exit(1)
        except BucketError as e:
            click.echo(f"\n{Fore.RED}Bucket Error: {e}{Style.RESET_ALL}")
            sys.exit(1)
        except PathManagerError as e:
            click.echo(f"\n{Fore.RED}PATH Error: {e}{Style.RESET_ALL}")
            sys.exit(1)
        except KeyboardInterrupt:
            click.echo(f"\n{Fore.YELLOW}Cancelled by user.{Style.RESET_ALL}")
            sys.exit(1)
        except Exception as e:
            click.echo(f"\n{Fore.RED}Unexpected error: {e}{Style.RESET_ALL}")
            sys.exit(1)
    wrapper.__name__ = func.__name__
    wrapper.__doc__ = func.__doc__
    return wrapper


# ──────────────────────────────────────────────
# Core install logic (used by install and update)
# ──────────────────────────────────────────────

def install_single_app(
    app_name: str,
    buckets_dir: str,
    is_dependency: bool = False,
) -> bool:
    """
    Install a single app (no dependency resolution — just download+extract+shim).

    Args:
        app_name:       Name of the app.
        buckets_dir:    Buckets directory.
        is_dependency:  If True, print as dependency install.

    Returns:
        True if installed successfully.
    """
    colorama_init(autoreset=True)

    # Load manifest
    manifest = Manifest(app_name, buckets_dir)

    # Check if already installed
    resolver = DependencyResolver(APPS_DIR, buckets_dir)
    if resolver.is_installed(app_name) and not is_dependency:
        click.echo(
            f"{Fore.YELLOW}'{app_name}' is already installed. "
            f"Run: myscoop update {app_name}{Style.RESET_ALL}"
        )
        return False

    if is_dependency:
        click.echo(f"\n{Fore.BLUE}Installing dependency: {app_name}{Style.RESET_ALL}")
    else:
        click.echo(
            f"\n{Fore.BLUE}Installing '{manifest.name}' "
            f"({manifest.version}) [64bit]{Style.RESET_ALL}"
        )

    # Download
    if manifest.url:
        downloader = Downloader(CACHE_DIR)
        filepath = downloader.download(manifest.url, app_name, manifest.version)
    else:
        click.echo(f"{Fore.YELLOW}  No download URL in manifest. Skipping download.{Style.RESET_ALL}")
        filepath = None

    # Extract / Install
    if filepath:
        app_dir = os.path.join(APPS_DIR, app_name, manifest.version)
        os.makedirs(app_dir, exist_ok=True)

        extractor = Extractor(APPS_DIR)
        extractor.extract(
            filepath=filepath,
            app_name=app_name,
            version=manifest.version,
            extract_dir=manifest.extract_dir,
            url_hint=manifest.url_hint,
            installer_type=manifest.installer_type,
        )
    else:
        app_dir = os.path.join(APPS_DIR, app_name, manifest.version)
        os.makedirs(app_dir, exist_ok=True)

    # Create shims for all bin entries
    if manifest.bin:
        shim_manager = ShimManager(SHIMS_DIR)
        for exe_name in manifest.bin:
            exe_path = os.path.join(app_dir, exe_name)
            # Check if it's a GUI app (heuristic: has "shortcuts" defined)
            is_gui = bool(manifest.shortcuts)
            shim_manager.create_shim(exe_name, exe_path, gui=is_gui)
            click.echo(f"{Fore.GREEN}  Creating shim: {Path(exe_name).stem}{Style.RESET_ALL}")

    # Create Start Menu shortcuts
    if manifest.shortcuts:
        for shortcut_entry in manifest.shortcuts:
            if len(shortcut_entry) >= 2:
                exe_path = os.path.join(app_dir, shortcut_entry[0])
                shortcut_name = shortcut_entry[1]
                _create_shortcut(exe_path, shortcut_name)
                click.echo(f"{Fore.GREEN}  Creating shortcut: {shortcut_name}{Style.RESET_ALL}")

    # Ensure shims dir is in PATH
    try:
        pm = PathManager()
        if pm.add_to_path(SHIMS_DIR):
            click.echo(f"{Fore.BLUE}  Added shims folder to PATH{Style.RESET_ALL}")
    except PathManagerError:
        pass  # Non-critical

    # Run post_install commands
    if manifest.post_install:
        click.echo(f"{Fore.BLUE}  Running post-install commands ...{Style.RESET_ALL}")
        for cmd in manifest.post_install:
            try:
                os.system(cmd)
            except Exception as e:
                click.echo(f"{Fore.YELLOW}  Warning: post_install failed: {e}{Style.RESET_ALL}")

    # Extract and save metadata
    if manifest.bin:
        try:
            first_exe = manifest.bin[0]
            exe_full_path = os.path.join(app_dir, first_exe)
            if os.path.exists(exe_full_path):
                meta = AppMetadata(exe_full_path)
                meta.extract()
                meta.save(app_dir)
                meta.display()
        except Exception:
            pass  # Metadata is non-critical

    # Save install info
    install_info = {
        "name": manifest.name,
        "version": manifest.version,
        "bin": manifest.bin,
        "shortcuts": manifest.shortcuts,
        "url": manifest.url,
    }
    info_path = os.path.join(app_dir, "install.json")
    with open(info_path, "w", encoding="utf-8") as f:
        json.dump(install_info, f, indent=4)

    click.echo(
        f"\n{Fore.GREEN}'{manifest.name}' ({manifest.version}) "
        f"installed successfully ✓{Style.RESET_ALL}"
    )
    return True


def _create_shortcut(exe_path: str, shortcut_name: str) -> None:
    """
    Create a Start Menu shortcut for an application.
    Uses PowerShell to create .lnk files.
    """
    start_menu = os.path.join(
        os.environ.get("APPDATA", ""),
        "Microsoft", "Windows", "Start Menu", "Programs", "MyScoop"
    )
    os.makedirs(start_menu, exist_ok=True)

    lnk_path = os.path.join(start_menu, f"{shortcut_name}.lnk")

    # Use PowerShell to create the shortcut
    ps_cmd = (
        f'$WshShell = New-Object -ComObject WScript.Shell; '
        f'$Shortcut = $WshShell.CreateShortcut("{lnk_path}"); '
        f'$Shortcut.TargetPath = "{exe_path}"; '
        f'$Shortcut.Save()'
    )
    os.system(f'powershell -Command "{ps_cmd}" >nul 2>&1')


def _remove_shortcut(shortcut_name: str) -> None:
    """Remove a Start Menu shortcut."""
    start_menu = os.path.join(
        os.environ.get("APPDATA", ""),
        "Microsoft", "Windows", "Start Menu", "Programs", "MyScoop"
    )
    lnk_path = os.path.join(start_menu, f"{shortcut_name}.lnk")
    if os.path.exists(lnk_path):
        os.remove(lnk_path)


# ──────────────────────────────────────────────
# CLI Group
# ──────────────────────────────────────────────

@click.group()
def cli():
    """myscoop — A Python-based package manager for Windows"""
    colorama_init(autoreset=True)
    ensure_dirs()


# ──────────────────────────────────────────────
# install
# ──────────────────────────────────────────────

@cli.command()
@click.argument("app")
@handle_errors
def install(app: str):
    """Install an application."""
    buckets_dir = get_buckets_dir()

    # Resolve dependencies
    resolver = DependencyResolver(APPS_DIR, buckets_dir)
    install_order = resolver.resolve(app)

    # Install dependencies first, then the app
    for i, dep_name in enumerate(install_order):
        is_dep = (dep_name.lower() != app.lower())

        # Skip already-installed dependencies
        if is_dep and resolver.is_installed(dep_name):
            click.echo(f"{Fore.BLUE}  Dependency '{dep_name}' already installed ✓{Style.RESET_ALL}")
            continue

        install_single_app(dep_name, buckets_dir, is_dependency=is_dep)


# ──────────────────────────────────────────────
# uninstall
# ──────────────────────────────────────────────

@cli.command()
@click.argument("app")
@handle_errors
def uninstall(app: str):
    """Uninstall an application."""
    app_lower = app.lower()
    app_path = os.path.join(APPS_DIR, app_lower)

    if not os.path.exists(app_path):
        click.echo(f"{Fore.RED}'{app}' is not installed.{Style.RESET_ALL}")
        sys.exit(1)

    buckets_dir = get_buckets_dir()

    # Try to load manifest to find bin entries and shortcuts to clean up
    try:
        manifest = Manifest(app_lower, buckets_dir)
        bin_entries = manifest.bin
        shortcuts = manifest.shortcuts
    except ManifestNotFoundError:
        bin_entries = []
        shortcuts = []

    # Remove shims
    if bin_entries:
        shim_manager = ShimManager(SHIMS_DIR)
        removed = shim_manager.remove_shims_for_app(bin_entries)
        click.echo(f"  Removed {removed} shim(s)")

    # Remove shortcuts
    for shortcut_entry in shortcuts:
        if len(shortcut_entry) >= 2:
            _remove_shortcut(shortcut_entry[1])

    # Delete app folder
    shutil.rmtree(app_path, ignore_errors=True)
    click.echo(f"\n{Fore.GREEN}'{app}' uninstalled successfully ✓{Style.RESET_ALL}")


# ──────────────────────────────────────────────
# update
# ──────────────────────────────────────────────

@cli.command()
@click.argument("app")
@handle_errors
def update(app: str):
    """Update an application to the latest version."""
    app_lower = app.lower()
    buckets_dir = get_buckets_dir()

    resolver = DependencyResolver(APPS_DIR, buckets_dir)
    if not resolver.is_installed(app_lower):
        click.echo(f"{Fore.RED}'{app}' is not installed. Run: myscoop install {app}{Style.RESET_ALL}")
        sys.exit(1)

    # Get current installed version
    current_version = resolver.get_installed_version(app_lower)

    # Get latest version from manifest
    manifest = Manifest(app_lower, buckets_dir)
    latest_version = manifest.version

    if current_version == latest_version:
        click.echo(
            f"{Fore.GREEN}'{app}' is already up to date ({current_version}) ✓{Style.RESET_ALL}"
        )
        return

    click.echo(
        f"{Fore.BLUE}Updating '{app}': {current_version} → {latest_version}{Style.RESET_ALL}"
    )

    # Install the new version (keeps old version until confirmed)
    install_single_app(app_lower, buckets_dir)

    # Remove old version folder if different
    if current_version and current_version != latest_version:
        old_path = os.path.join(APPS_DIR, app_lower, current_version)
        if os.path.exists(old_path):
            shutil.rmtree(old_path, ignore_errors=True)
            click.echo(f"  Removed old version: {current_version}")


# ──────────────────────────────────────────────
# list
# ──────────────────────────────────────────────

@cli.command(name="list")
@handle_errors
def list_apps():
    """List all installed applications."""
    if not os.path.exists(APPS_DIR):
        click.echo("No apps installed.")
        return

    apps = []
    for app_name in sorted(os.listdir(APPS_DIR)):
        app_path = os.path.join(APPS_DIR, app_name)
        if not os.path.isdir(app_path):
            continue
        versions = [
            d for d in os.listdir(app_path)
            if os.path.isdir(os.path.join(app_path, d))
        ]
        if versions:
            version = sorted(versions)[-1]
            apps.append((app_name, version))

    if not apps:
        click.echo("No apps installed.")
        return

    click.echo(f"\n{Fore.BLUE}Installed apps:{Style.RESET_ALL}")
    click.echo(f"{'─' * 40}")
    click.echo(f"  {'Name':<20} {'Version':<15}")
    click.echo(f"{'─' * 40}")
    for name, version in apps:
        click.echo(f"  {name:<20} {version:<15}")
    click.echo(f"{'─' * 40}")
    click.echo(f"  Total: {len(apps)} app(s)\n")


# ──────────────────────────────────────────────
# search
# ──────────────────────────────────────────────

@cli.command()
@click.argument("query")
@handle_errors
def search(query: str):
    """Search for apps across all buckets."""
    buckets_dir = get_buckets_dir()
    bm = BucketManager(buckets_dir)
    results = bm.search(query)

    if not results:
        click.echo(f"{Fore.YELLOW}No apps found matching '{query}'.{Style.RESET_ALL}")
        return

    click.echo(f"\n{Fore.BLUE}Search results for '{query}':{Style.RESET_ALL}")
    click.echo(f"{'─' * 60}")
    click.echo(f"  {'Name':<20} {'Version':<12} {'Description'}")
    click.echo(f"{'─' * 60}")
    for r in results:
        click.echo(f"  {r['name']:<20} {r['version']:<12} {r['description']}")
    click.echo(f"{'─' * 60}")
    click.echo(f"  {len(results)} result(s)\n")


# ──────────────────────────────────────────────
# info
# ──────────────────────────────────────────────

@cli.command()
@click.argument("app")
@handle_errors
def info(app: str):
    """Show detailed info about an app from its manifest."""
    buckets_dir = get_buckets_dir()
    manifest = Manifest(app, buckets_dir)

    click.echo(f"\n{Fore.BLUE}{'─' * 50}{Style.RESET_ALL}")
    click.echo(f"  {Fore.WHITE}{Style.BRIGHT}{manifest.name}{Style.RESET_ALL}")
    click.echo(f"{Fore.BLUE}{'─' * 50}{Style.RESET_ALL}")
    click.echo(f"  Description:  {manifest.description}")
    click.echo(f"  Version:      {manifest.version}")
    click.echo(f"  Homepage:     {manifest.homepage}")
    click.echo(f"  License:      {manifest.license}")
    click.echo(f"  Executables:  {', '.join(manifest.bin) if manifest.bin else 'none'}")
    click.echo(f"  Dependencies: {', '.join(manifest.depends) if manifest.depends else 'none'}")
    click.echo(f"  Installer:    {manifest.installer_type or 'auto'}")
    click.echo(f"  URL:          {manifest.url}")
    if manifest.url_hint:
        click.echo(f"  URL hint:     {manifest.url_hint}")
    if manifest.shortcuts:
        names = [s[1] for s in manifest.shortcuts if len(s) >= 2]
        click.echo(f"  Shortcuts:    {', '.join(names)}")

    # Show installed status
    resolver = DependencyResolver(APPS_DIR, buckets_dir)
    if resolver.is_installed(app):
        version = resolver.get_installed_version(app)
        click.echo(f"  Status:       {Fore.GREEN}Installed ({version}){Style.RESET_ALL}")
    else:
        click.echo(f"  Status:       {Fore.YELLOW}Not installed{Style.RESET_ALL}")

    click.echo(f"{Fore.BLUE}{'─' * 50}{Style.RESET_ALL}\n")


# ──────────────────────────────────────────────
# bucket
# ──────────────────────────────────────────────

@cli.group()
def bucket():
    """Manage app buckets (repositories)."""
    pass


@bucket.command(name="add")
@click.argument("name")
@click.argument("url")
@handle_errors
def bucket_add(name: str, url: str):
    """Add a new bucket from a Git repository URL."""
    bm = BucketManager(BUCKETS_DIR)
    bm.add_bucket(name, url)


@bucket.command(name="list")
@handle_errors
def bucket_list():
    """List all added buckets."""
    bm = BucketManager(get_buckets_dir())
    buckets = bm.list_buckets()

    if not buckets:
        click.echo("No buckets added.")
        return

    click.echo(f"\n{Fore.BLUE}Buckets:{Style.RESET_ALL}")
    click.echo(f"{'─' * 50}")
    click.echo(f"  {'Name':<15} {'Manifests':<10} {'Path'}")
    click.echo(f"{'─' * 50}")
    for b in buckets:
        click.echo(f"  {b['name']:<15} {b['manifests']:<10} {b['path']}")
    click.echo(f"{'─' * 50}\n")


@bucket.command(name="update")
@handle_errors
def bucket_update():
    """Update all buckets (git pull)."""
    bm = BucketManager(BUCKETS_DIR)
    count = bm.update_buckets()
    click.echo(f"\n{Fore.GREEN}Updated {count} bucket(s) ✓{Style.RESET_ALL}")


@bucket.command(name="rm")
@click.argument("name")
@handle_errors
def bucket_rm(name: str):
    """Remove a bucket."""
    bm = BucketManager(BUCKETS_DIR)
    bm.remove_bucket(name)


# ──────────────────────────────────────────────
# cache
# ──────────────────────────────────────────────

@cli.group()
def cache():
    """Manage the download cache."""
    pass


@cache.command(name="rm")
@click.argument("app")
@handle_errors
def cache_rm(app: str):
    """Remove cached files for an app."""
    dl = Downloader(CACHE_DIR)
    if dl.remove_cached(app):
        click.echo(f"{Fore.GREEN}Cache cleared for '{app}' ✓{Style.RESET_ALL}")
    else:
        click.echo(f"{Fore.YELLOW}No cached files found for '{app}'.{Style.RESET_ALL}")


# ──────────────────────────────────────────────
# depends
# ──────────────────────────────────────────────

@cli.command()
@click.argument("app")
@handle_errors
def depends(app: str):
    """Show the dependency tree for an app."""
    buckets_dir = get_buckets_dir()
    resolver = DependencyResolver(APPS_DIR, buckets_dir)

    click.echo(f"\n{Fore.BLUE}Dependency tree for '{app}':{Style.RESET_ALL}")
    tree = resolver.get_dependency_tree(app)
    click.echo(tree)
    click.echo()


# ──────────────────────────────────────────────
# metadata
# ──────────────────────────────────────────────

@cli.command()
@click.argument("app")
@handle_errors
def metadata(app: str):
    """Show metadata for an installed app."""
    app_lower = app.lower()
    app_path = os.path.join(APPS_DIR, app_lower)

    if not os.path.exists(app_path):
        click.echo(f"{Fore.RED}'{app}' is not installed.{Style.RESET_ALL}")
        sys.exit(1)

    # Find metadata.json — search version subdirs
    meta_path = None
    for version_dir in os.listdir(app_path):
        candidate = os.path.join(app_path, version_dir, "metadata.json")
        if os.path.exists(candidate):
            meta_path = candidate
            break

    if meta_path is None:
        click.echo(
            f"{Fore.YELLOW}No metadata found for '{app}'. "
            f"Try reinstalling.{Style.RESET_ALL}"
        )
        sys.exit(1)

    meta = AppMetadata.load_from_json(meta_path)
    meta.display()


# ──────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────

def main():
    """Main entry point for the CLI."""
    cli()


if __name__ == "__main__":
    main()
