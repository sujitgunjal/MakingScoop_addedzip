"""
silent_installer.py — GUI Installer Suppression for myscoop

Handles local installers by trying multiple strategies in order:
    1. MSI silent      — msiexec /a /qn TARGETDIR=
    2. NSIS silent     — exe /S /D=
    3. Inno Setup      — exe /VERYSILENT /SUPPRESSMSGBOXES /DIR=
    4. GUI automation  — existing UI automation fallback

Archive extraction is reserved for actual archive inputs like .7z/.zip
or when a manifest explicitly marks the payload as type "7z".

Each strategy is tried in order. If one fails, the next is attempted.

Usage:
    si = SilentInstaller("C:/Users/you/myscoop/apps")
    success = si.install(filepath, app_dir)
"""

import os
import subprocess
import time
import ctypes
import ctypes.wintypes
from pathlib import Path
from typing import List, Optional

try:
    import py7zr
except ImportError:
    py7zr = None

try:
    from myscoop.gui_installer import GUIInstaller, GUIInstallError
    _HAS_GUI_INSTALLER = True
except ImportError:
    _HAS_GUI_INSTALLER = False


class SilentInstallError(Exception):
    """Raised when all silent install strategies fail."""
    pass


class SilentInstaller:
    """
    Runs installers using a silent-first cascade.

    Real installer execution is preferred for .exe/.msi inputs so that
    "success" means the application was actually installed, not just unpacked.
    """

    # Common locations where 7-Zip CLI is installed
    _7Z_PATHS = [
        r"C:\Program Files\7-Zip\7z.exe",
        r"C:\Program Files (x86)\7-Zip\7z.exe",
    ]
    _INTERACTIVE_WINDOW_KEYWORDS = (
        "setup",
        "install",
        "installer",
        "wizard",
        "extract",
        "self-extract",
        "extract to",
    )

    def __init__(self, apps_dir: str) -> None:
        """
        Args:
            apps_dir: Root apps directory.
        """
        self.apps_dir: str = apps_dir
        self._7z_exe: Optional[str] = self._find_7z_exe()

    def install(
        self,
        filepath: str,
        app_dir: str,
        installer_type: Optional[str] = None,
    ) -> bool:
        """
        Try to silently install an installer file.

        Tries strategies in this exact order:
            1. Explicit manifest hint
            2. MSI silent (for .msi)
            3. NSIS silent (/S)
            4. Inno Setup silent (/VERYSILENT)
            5. Archive extraction for archive payloads only
            6. GUI automation

        Args:
            filepath:       Path to the installer file.
            app_dir:        Target directory to install/extract into.
            installer_type: Optional hint from manifest ("7z", "msi", "nsis", "inno").

        Returns:
            True if installation succeeded with any strategy.

        Raises:
            SilentInstallError: If ALL strategies fail.
        """
        os.makedirs(app_dir, exist_ok=True)
        errors = []
        ext = Path(filepath).suffix.lower()

        # If installer_type is explicitly specified, try that first
        if installer_type:
            strategy = self._get_strategy_for_type(installer_type)
            if strategy:
                try:
                    strategy(filepath, app_dir)
                    return True
                except Exception as e:
                    errors.append(f"{installer_type}: {e}")
                    if installer_type.lower() == "gui":
                        error_details = "\n    ".join(errors)
                        raise SilentInstallError(
                            f"Explicit GUI automation failed for: {filepath}\n"
                            f"  Errors:\n    {error_details}"
                        )

        archive_like_type = installer_type.lower() if installer_type else None
        is_archive_payload = ext in {".7z", ".zip"} or archive_like_type in {"7z", "zip"}

        if is_archive_payload:
            try:
                print("  Trying: archive extraction ...")
                self._strategy_7z(filepath, app_dir)
                print("  Strategy: archive extraction succeeded")
                return True
            except Exception as e:
                errors.append(f"archive: {e}")

        # Strategy 1: MSI silent (for .msi files)
        if ext == ".msi" or installer_type == "msi":
            try:
                print("  Trying: MSI silent install ...")
                self._strategy_msi(filepath, app_dir)
                print("  Strategy: MSI silent install succeeded")
                return True
            except Exception as e:
                errors.append(f"MSI: {e}")

        # Strategy 2/3: executable installers
        if ext == ".exe":
            detected = self.detect_installer_type(filepath)

            if detected == "nsis" or installer_type == "nsis":
                try:
                    print("  Trying: NSIS silent install ...")
                    self._strategy_nsis(filepath, app_dir)
                    print("  Strategy: NSIS silent install succeeded")
                    return True
                except Exception as e:
                    errors.append(f"NSIS: {e}")

            # Strategy 4: Inno Setup silent (/VERYSILENT)
            if detected == "inno" or installer_type == "inno":
                try:
                    print("  Trying: Inno Setup silent install ...")
                    self._strategy_inno(filepath, app_dir)
                    print("  Strategy: Inno Setup silent install succeeded")
                    return True
                except Exception as e:
                    errors.append(f"Inno: {e}")

            # If detection was inconclusive, try both common silent modes
            if detected is None and installer_type not in ("nsis", "inno"):
                try:
                    print("  Trying: NSIS silent install ...")
                    self._strategy_nsis(filepath, app_dir)
                    print("  Strategy: NSIS silent install succeeded")
                    return True
                except Exception as e:
                    errors.append(f"NSIS: {e}")

                try:
                    print("  Trying: Inno Setup silent install ...")
                    self._strategy_inno(filepath, app_dir)
                    print("  Strategy: Inno Setup silent install succeeded")
                    return True
                except Exception as e:
                    errors.append(f"Inno: {e}")

            # Some setup.exe files are really extractable archives containing
            # MSI/MSIZip payloads. Prefer extraction over blind GUI driving.
            if self._looks_like_extractable_setup(filepath):
                try:
                    print("  Trying: archive extraction ...")
                    self._strategy_7z(filepath, app_dir)
                    print("  Strategy: archive extraction succeeded")
                    return True
                except Exception as e:
                    errors.append(f"archive: {e}")

        # For non-exe archive-like payloads that were not explicitly marked,
        # try extraction before GUI fallback.
        if ext in {".7z", ".zip"} and not is_archive_payload:
            try:
                print("  Trying: archive extraction ...")
                self._strategy_7z(filepath, app_dir)
                print("  Strategy: archive extraction succeeded")
                return True
            except Exception as e:
                errors.append(f"archive: {e}")

        # Strategy 4/5: GUI automation (last resort for installers)
        if _HAS_GUI_INSTALLER:
            try:
                print("  Trying: GUI automation ...")
                self._strategy_gui(filepath, app_dir)
                print("  Strategy: GUI automation succeeded")
                return True
            except Exception as e:
                errors.append(f"GUI: {e}")

        # All strategies failed
        error_details = "\n    ".join(errors)
        raise SilentInstallError(
            f"All silent install strategies failed for: {filepath}\n"
            f"  Errors:\n    {error_details}\n"
            f"  Suggested fix: Try 'myscoop install vcredist2022' if missing dependencies."
        )

    # ──────────────────────────────────────────────
    # Individual strategies
    # ──────────────────────────────────────────────

    def _strategy_7z(self, filepath: str, app_dir: str) -> None:
        """Strategy 1: Treat the file as a 7z archive. Try py7zr first, then 7z.exe."""
        errors: List[str] = []

        # Try py7zr first (works for pure .7z archives)
        if py7zr is not None:
            try:
                with py7zr.SevenZipFile(filepath, mode="r") as archive:
                    archive.extractall(path=app_dir)
                return
            except Exception as e:
                errors.append(f"py7zr: {e}")

        # Try 7z.exe CLI (handles NSIS, Inno, SFX, etc.)
        if self._7z_exe:
            try:
                result = subprocess.run(
                    [self._7z_exe, "x", filepath, f"-o{app_dir}", "-y"],
                    capture_output=True, text=True, timeout=600,
                )
                if result.returncode == 0:
                    return
                errors.append(f"7z.exe exit code {result.returncode}")
            except Exception as e:
                errors.append(f"7z.exe: {e}")

        raise RuntimeError(f"7z extraction failed: {'; '.join(errors)}")

    def _strategy_msi(self, filepath: str, app_dir: str) -> None:
        """Strategy 2: MSI administrative install (no registry, no UAC)."""
        filepath_abs = os.path.abspath(filepath)
        app_dir_abs = os.path.abspath(app_dir)
        if not app_dir_abs.endswith("\\"):
            app_dir_abs += "\\"
        result = subprocess.run(
            ["msiexec", "/a", filepath_abs, "/qn", f"TARGETDIR={app_dir_abs}"],
            capture_output=True,
            text=True,
            timeout=300,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"msiexec exited with code {result.returncode}"
            )

    def _strategy_nsis(self, filepath: str, app_dir: str) -> None:
        """Strategy 3: NSIS installer with /S (silent) and /D= (target dir)."""
        self._run_executable_strategy(
            filepath,
            ["/S", f"/D={app_dir}"],
            timeout=300,
            strategy_name="NSIS",
        )

    def _strategy_inno(self, filepath: str, app_dir: str) -> None:
        """Strategy 4: Inno Setup with /VERYSILENT /SUPPRESSMSGBOXES /DIR=."""
        self._run_executable_strategy(
            filepath,
            [
                "/VERYSILENT",
                "/SUPPRESSMSGBOXES",
                f"/DIR={app_dir}",
            ],
            timeout=300,
            strategy_name="Inno Setup",
        )

    def _strategy_gui(self, filepath: str, app_dir: str) -> None:
        """Strategy 5: GUI automation — drive the installer wizard via UI tree."""
        if not _HAS_GUI_INSTALLER:
            raise RuntimeError(
                "GUI automation requires pywinauto and psutil. "
                "Run: pip install pywinauto pyautogui psutil"
            )
        gui = GUIInstaller()
        success = gui.install(filepath, app_dir)
        if not success:
            raise RuntimeError("GUI automation reported failure")

    # ──────────────────────────────────────────────
    # Installer type detection
    # ──────────────────────────────────────────────

    def detect_installer_type(self, filepath: str) -> Optional[str]:
        """
        Peek at the binary contents of an exe to detect if it's
        an NSIS or Inno Setup installer.

        Args:
            filepath: Path to the exe file.

        Returns:
            "nsis", "inno", or None if detection is inconclusive.
        """
        try:
            with open(filepath, "rb") as f:
                # Read first 64KB for signature detection
                header = f.read(65536)

                # NSIS markers: "Nullsoft" or "NSIS" appear in the header
                if b"NullsoftInst" in header or b"NSIS" in header:
                    return "nsis"

                # Inno Setup markers
                if b"Inno Setup" in header or b"InnoSetup" in header:
                    return "inno"

                # Also check deeper in the file for some installers
                f.seek(0)
                # Read a larger chunk for installers that embed signatures later
                content = f.read(1024 * 512)  # 512KB
                if b"NullsoftInst" in content:
                    return "nsis"
                if b"Inno Setup" in content:
                    return "inno"

        except (IOError, OSError):
            pass

        return None

    # ──────────────────────────────────────────────
    # Helpers
    # ──────────────────────────────────────────────

    def _find_7z_exe(self) -> Optional[str]:
        """Find the 7z.exe command-line tool on the system."""
        for path in self._7Z_PATHS:
            if os.path.exists(path):
                return path
        try:
            result = subprocess.run(
                ["where", "7z"], capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip().splitlines()[0]
        except Exception:
            pass
        return None

    def _get_strategy_for_type(self, installer_type: str):
        """Map installer type string to strategy method."""
        type_map = {
            "7z": self._strategy_7z,
            "msi": self._strategy_msi,
            "nsis": self._strategy_nsis,
            "inno": self._strategy_inno,
            "gui": self._strategy_gui,
        }
        return type_map.get(installer_type.lower())

    def _looks_like_extractable_setup(self, filepath: str) -> bool:
        """
        Detect EXEs that are better treated as archives.

        This catches installers like MAPSetup.exe where 7-Zip can list MSI/MSIZip
        contents directly, so extraction is a safer path than GUI automation.
        """
        if not self._7z_exe:
            return False

        try:
            result = subprocess.run(
                [self._7z_exe, "l", filepath],
                capture_output=True,
                text=True,
                timeout=30,
            )
        except Exception:
            return False

        if result.returncode != 0:
            return False

        listing = f"{result.stdout}\n{result.stderr}".lower()
        archive_markers = (
            "method = msizip",
            " msizip",
            ".msi",
            ".cab",
        )
        return any(marker in listing for marker in archive_markers)

    def _run_executable_strategy(
        self,
        filepath: str,
        args: List[str],
        timeout: int,
        strategy_name: str,
    ) -> None:
        """
        Run an EXE-based silent strategy, but fail fast if it shows UI.
        """
        # Inject RunAsInvoker to bypass embedded requireAdministrator manifests 
        # so Windows doesn't immediately throw WinError 740 and we can stay silent.
        env = os.environ.copy()
        env["__COMPAT_LAYER"] = "RunAsInvoker"

        try:
            process = subprocess.Popen(
                [filepath, *args],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=env,
            )
        except OSError as e:
            if getattr(e, "winerror", None) == 740:
                print(f"  Elevation required for {strategy_name} despite RunAsInvoker. Requesting UAC...")
                # Escape arguments for PowerShell
                ps_args = " ".join(args)
                ps_cmd = f"Start-Process -FilePath '{filepath}' -ArgumentList '{ps_args}' -Wait -WindowStyle Hidden"
                result = subprocess.run(["powershell", "-Command", ps_cmd], check=False, timeout=timeout)
                if result.returncode != 0:
                    raise RuntimeError(f"{strategy_name} elevated installer exited with code {result.returncode}")
                return
            raise

        deadline = time.time() + timeout
        first_detection_deadline = time.time() + 15

        while time.time() < deadline:
            returncode = process.poll()
            if returncode is not None:
                if returncode != 0:
                    raise RuntimeError(
                        f"{strategy_name} installer exited with code {returncode}"
                    )
                return

            if (
                time.time() <= first_detection_deadline
                and self._has_interactive_window(process.pid)
            ):
                self._terminate_process(process)
                raise RuntimeError(
                    f"{strategy_name} installer ignored silent flags and opened interactive UI"
                )

            time.sleep(0.5)

        self._terminate_process(process)
        raise RuntimeError(
            f"{strategy_name} installer timed out after {timeout}s"
        )

    def _terminate_process(self, process: subprocess.Popen) -> None:
        """Best-effort cleanup for timed out or interactive processes."""
        try:
            process.terminate()
            process.wait(timeout=5)
        except Exception:
            try:
                process.kill()
                process.wait(timeout=5)
            except Exception:
                pass

    def _has_interactive_window(self, pid: int) -> bool:
        """Detect whether a visible top-level installer window appeared."""
        try:
            user32 = ctypes.windll.user32
        except AttributeError:
            return False

        found = False

        @ctypes.WINFUNCTYPE(
            ctypes.wintypes.BOOL,
            ctypes.wintypes.HWND,
            ctypes.wintypes.LPARAM,
        )
        def enum_callback(hwnd, _lparam):
            nonlocal found

            if found or not user32.IsWindowVisible(hwnd):
                return True

            window_pid = ctypes.wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(window_pid))
            if window_pid.value != pid:
                return True

            length = user32.GetWindowTextLengthW(hwnd)
            if length <= 0:
                return True

            title_buffer = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, title_buffer, length + 1)
            title = title_buffer.value.lower()

            if any(keyword in title for keyword in self._INTERACTIVE_WINDOW_KEYWORDS):
                found = True
                return False

            return True

        try:
            user32.EnumWindows(enum_callback, 0)
        except Exception:
            return False

        return found
