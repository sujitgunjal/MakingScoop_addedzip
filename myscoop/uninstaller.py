"""
uninstaller.py - Shared uninstall orchestration for myscoop.

Combines manifest-driven uninstallers, native uninstallers, registry-based
fallbacks, and managed-folder cleanup. The goal is to only report success
when we either removed a real managed payload or successfully ran an external
uninstaller and then cleaned up myscoop's metadata.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple
from urllib.parse import unquote, urlparse

from myscoop.manifest import Manifest, ManifestNotFoundError
from myscoop.shim import ShimManager

try:
    from myscoop.gui_installer import GUIInstaller, GUIInstallError
    _HAS_GUI_INSTALLER = True
except ImportError:
    GUIInstallError = RuntimeError
    _HAS_GUI_INSTALLER = False

try:
    import winreg
except ImportError:  # pragma: no cover - Windows only
    winreg = None


class UninstallError(Exception):
    """Raised when myscoop cannot confidently uninstall an application."""


_METADATA_FILES = {"install.json", "gui_install_info.json", "metadata.json"}
_SUCCESS_CODES = {0, 1641, 3010}


def uninstall_app(
    app_name: str,
    buckets_dir: str,
    apps_dir: str,
    shims_dir: str,
    remove_shortcut: Callable[[str], None],
    log: Optional[Callable[[str], None]] = None,
) -> Dict[str, Any]:
    """Uninstall an app and return structured details on success."""
    app_lower = app_name.lower()
    app_root = os.path.join(apps_dir, app_lower)
    if not os.path.isdir(app_root):
        raise UninstallError(f"'{app_name}' is not installed.")

    manifest = _load_manifest(app_lower, buckets_dir)
    bin_entries = manifest.bin if manifest else []
    shortcuts = manifest.shortcuts if manifest else []
    latest_app_dir = _get_latest_version_dir(app_root)
    has_payload = _app_dir_has_payload(app_root)
    actions: List[str] = []
    external_success = False
    external_attempted = False

    if manifest and manifest.uninstaller:
        external_attempted = True
        if _run_manifest_uninstaller(manifest, latest_app_dir, log):
            external_success = True
            actions.append("manifest uninstaller")

    if not external_success:
        native_success = _run_native_uninstallers(app_root, log)
        if native_success:
            external_success = True
            external_attempted = True
            actions.append("native uninstaller")

    if not external_success and manifest:
        registry_success = _run_registry_uninstaller(app_lower, manifest, log)
        if registry_success:
            external_success = True
            external_attempted = True
            actions.append("registry uninstaller")

    if not has_payload and not external_success:
        raise UninstallError(
            f"Could not find a working uninstaller for '{app_name}'. "
            f"No managed payload was found inside {app_root}."
        )

    if has_payload or external_success:
        _remove_app_root(app_root)
        if os.path.exists(app_root):
            raise UninstallError(
                f"Uninstall cleanup for '{app_name}' did not finish; "
                f"'{app_root}' still exists."
            )

    _cleanup_shell_entries(bin_entries, shortcuts, shims_dir, remove_shortcut)

    action_text = ", ".join(actions) if actions else "managed folder removal"
    if log:
        log(f"Uninstall complete for '{app_name}' via {action_text}.")

    return {
        "app": app_lower,
        "message": f"'{app_name}' uninstalled successfully.",
        "actions": actions,
        "external_uninstaller_attempted": external_attempted,
    }


def _load_manifest(app_name: str, buckets_dir: str) -> Optional[Manifest]:
    try:
        return Manifest(app_name, buckets_dir)
    except ManifestNotFoundError:
        return None


def _get_latest_version_dir(app_root: str) -> str:
    versions = [
        os.path.join(app_root, name)
        for name in os.listdir(app_root)
        if os.path.isdir(os.path.join(app_root, name))
    ]
    if not versions:
        return app_root
    return sorted(versions)[-1]


def _app_dir_has_payload(app_root: str) -> bool:
    for root, dirs, files in os.walk(app_root):
        dirs[:] = [name for name in dirs if name != "__pycache__"]
        rel_root = os.path.relpath(root, app_root)
        if rel_root != ".":
            return True
        for filename in files:
            if filename.lower() not in _METADATA_FILES:
                return True
    return False


def _remove_app_root(app_root: str) -> None:
    for _ in range(3):
        shutil.rmtree(app_root, ignore_errors=True)
        if not os.path.exists(app_root):
            return
        time.sleep(1.0)


def _cleanup_shell_entries(
    bin_entries: Sequence[str],
    shortcuts: Sequence[Sequence[str]],
    shims_dir: str,
    remove_shortcut: Callable[[str], None],
) -> None:
    if bin_entries:
        shim_manager = ShimManager(shims_dir)
        shim_manager.remove_shims_for_app(list(bin_entries))

    for shortcut_entry in shortcuts:
        if len(shortcut_entry) >= 2:
            remove_shortcut(shortcut_entry[1])


def _run_manifest_uninstaller(
    manifest: Manifest,
    app_dir: str,
    log: Optional[Callable[[str], None]],
) -> bool:
    config = manifest.uninstaller
    if not isinstance(config, dict):
        return False

    uninstall_type = str(config.get("type", "")).lower()
    if uninstall_type == "gui":
        target = _resolve_uninstaller_source_path(config, manifest)
        if not target:
            return False
        return _run_gui_uninstaller(target, app_dir, log)

    if uninstall_type == "command":
        target = _resolve_uninstaller_source_path(config, manifest)
        if not target:
            return False
        args = [str(arg) for arg in config.get("args", [])]
        return _run_executable(target, args, log, timeout=600)

    if uninstall_type == "registry":
        return _run_registry_uninstaller(manifest.app_name, manifest, log)

    return False


def _resolve_uninstaller_source_path(
    config: Dict[str, Any],
    manifest: Manifest,
) -> Optional[str]:
    executable = config.get("path")
    if isinstance(executable, str) and executable:
        return os.path.abspath(executable)

    source = str(config.get("source", "installer")).lower()
    if source != "installer":
        return None

    installer_url = manifest.url
    if not installer_url:
        return None

    parsed = urlparse(installer_url)
    if parsed.scheme != "file":
        return None

    raw_path = unquote(parsed.path or "")
    if re.match(r"^/[A-Za-z]:", raw_path):
        raw_path = raw_path[1:]
    if parsed.netloc:
        raw_path = f"//{parsed.netloc}{raw_path}"
    resolved = os.path.abspath(raw_path)
    return resolved if os.path.isfile(resolved) else None


def _run_gui_uninstaller(
    executable: str,
    app_dir: str,
    log: Optional[Callable[[str], None]],
) -> bool:
    if not _HAS_GUI_INSTALLER:
        raise UninstallError(
            "GUI uninstallation requires pywinauto and related automation dependencies."
        )
    if log:
        log(f"Running GUI uninstaller: {executable}")
    gui = GUIInstaller(mode="uninstall")
    try:
        return bool(gui.install(executable, app_dir))
    except GUIInstallError:
        return False


def _run_native_uninstallers(
    app_root: str,
    log: Optional[Callable[[str], None]],
) -> bool:
    uninstallers: List[str] = []
    for root, _dirs, files in os.walk(app_root):
        for file in files:
            lowered = file.lower()
            if (lowered.startswith("unins") or "uninstall" in lowered) and lowered.endswith(".exe"):
                uninstallers.append(os.path.join(root, file))

    for executable in uninstallers:
        if log:
            log(f"Running native uninstaller: {executable}")
        if "unins000" in executable.lower():
            args = ["/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"]
        else:
            args = ["/S", f"_?={os.path.dirname(executable)}"]
        if _run_executable(executable, args, log, timeout=300):
            return True
    return False


def _run_executable(
    executable: str,
    args: Sequence[str],
    log: Optional[Callable[[str], None]],
    timeout: int,
) -> bool:
    env = os.environ.copy()
    env["__COMPAT_LAYER"] = "RunAsInvoker"
    try:
        completed = subprocess.run(
            [executable, *args],
            check=False,
            timeout=timeout,
            env=env,
        )
        if completed.returncode in _SUCCESS_CODES:
            return True
        if log:
            log(
                f"Uninstaller exited with code {completed.returncode}: "
                f"{Path(executable).name}"
            )
    except OSError as exc:
        if getattr(exc, "winerror", None) == 740:
            arg_text = subprocess.list2cmdline(list(args))
            ps_cmd = f"Start-Process -FilePath '{executable}' -ArgumentList '{arg_text}' -Wait"
            completed = subprocess.run(
                ["powershell", "-Command", ps_cmd],
                check=False,
                timeout=timeout,
            )
            return completed.returncode in _SUCCESS_CODES
        if log:
            log(f"Failed to launch uninstaller {executable}: {exc}")
    except Exception as exc:
        if log:
            log(f"Uninstaller failed for {executable}: {exc}")
    return False


def _run_registry_uninstaller(
    app_name: str,
    manifest: Manifest,
    log: Optional[Callable[[str], None]],
) -> bool:
    if winreg is None:
        return False

    entry = _find_best_registry_entry(app_name, manifest)
    if not entry:
        return False

    uninstall_string = entry.get("QuietUninstallString") or entry.get("UninstallString")
    if not uninstall_string:
        return False

    command = _normalize_registry_uninstall_string(uninstall_string)
    if log:
        display_name = entry.get("DisplayName", app_name)
        log(f"Running registry uninstaller for: {display_name}")

    try:
        completed = subprocess.run(command, shell=True, check=False, timeout=600)
        return completed.returncode in _SUCCESS_CODES
    except Exception as exc:
        if log:
            log(f"Registry uninstaller failed: {exc}")
        return False


def _find_best_registry_entry(app_name: str, manifest: Manifest) -> Optional[Dict[str, str]]:
    candidates = _registry_candidates(app_name, manifest)
    best_match: Optional[Tuple[int, Dict[str, str]]] = None

    for entry in _iter_registry_uninstall_entries():
        display_name = _normalize_registry_text(entry.get("DisplayName", ""))
        if not display_name:
            continue
        score = max(_score_registry_match(display_name, candidate) for candidate in candidates)
        if score <= 0:
            continue
        if best_match is None or score > best_match[0]:
            best_match = (score, entry)

    return best_match[1] if best_match else None


def _registry_candidates(app_name: str, manifest: Manifest) -> List[str]:
    values = {
        _normalize_registry_text(app_name),
        _normalize_registry_text(manifest.name),
        _normalize_registry_text(manifest.description),
    }
    return [value for value in values if value]


def _normalize_registry_text(text: str) -> str:
    text = text.lower().replace("asp.net", "aspnet").replace(".net", "dotnet")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def _score_registry_match(display_name: str, candidate: str) -> int:
    if not display_name or not candidate:
        return 0
    if candidate == display_name:
        return 100
    if candidate in display_name:
        return 80

    candidate_tokens = candidate.split()
    if not candidate_tokens:
        return 0

    overlap = sum(1 for token in candidate_tokens if token in display_name.split())
    required = max(2, min(len(candidate_tokens), 4))
    return overlap * 10 if overlap >= required else 0


def _iter_registry_uninstall_entries() -> Iterable[Dict[str, str]]:
    key_specs = [
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall", winreg.KEY_WOW64_64KEY),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall", winreg.KEY_WOW64_32KEY),
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall", 0),
    ]
    wanted = ("DisplayName", "UninstallString", "QuietUninstallString")

    for hive, path, wow_flag in key_specs:
        access = winreg.KEY_READ | wow_flag
        try:
            with winreg.OpenKey(hive, path, 0, access) as root_key:
                subkey_count = winreg.QueryInfoKey(root_key)[0]
                for index in range(subkey_count):
                    try:
                        subkey_name = winreg.EnumKey(root_key, index)
                        with winreg.OpenKey(root_key, subkey_name) as subkey:
                            data: Dict[str, str] = {}
                            for value_name in wanted:
                                try:
                                    value, _ = winreg.QueryValueEx(subkey, value_name)
                                except OSError:
                                    continue
                                if isinstance(value, str):
                                    data[value_name] = value
                            if data.get("DisplayName"):
                                yield data
                    except OSError:
                        continue
        except OSError:
            continue


def _normalize_registry_uninstall_string(command: str) -> str:
    normalized = command.strip()
    lowered = normalized.lower()
    if "msiexec" in lowered and "/i" in lowered and "/x" not in lowered:
        normalized = re.sub(r"(?i)\s/i(?=\s|{)", " /x", normalized, count=1)
    if "msiexec" in normalized.lower():
        if "/qn" not in normalized.lower():
            normalized += " /qn"
        if "/norestart" not in normalized.lower():
            normalized += " /norestart"
    return normalized
