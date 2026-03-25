"""
silent_installer.py — GUI Installer Suppression for myscoop

Handles GUI-only installers by trying multiple silent strategies in order:
    1. 7z extraction   — treat the exe as a 7z archive (never runs installer)
    2. MSI silent      — msiexec /a /qn TARGETDIR=
    3. NSIS silent     — exe /S /D=
    4. Inno Setup      — exe /VERYSILENT /SUPPRESSMSGBOXES /DIR=

Each strategy is tried in order. If one fails, the next is attempted.

Usage:
    si = SilentInstaller("C:/Users/you/myscoop/apps")
    success = si.install(filepath, app_dir)
"""

import os
import subprocess
from pathlib import Path
from typing import List, Optional

try:
    import py7zr
except ImportError:
    py7zr = None


class SilentInstallError(Exception):
    """Raised when all silent install strategies fail."""
    pass


class SilentInstaller:
    """
    Suppresses GUI installers using a multi-strategy cascade.

    Strategies are tried in order from safest (7z extraction, no code runs)
    to least safe (running the installer with silent flags).
    """

    # Common locations where 7-Zip CLI is installed
    _7Z_PATHS = [
        r"C:\Program Files\7-Zip\7z.exe",
        r"C:\Program Files (x86)\7-Zip\7z.exe",
    ]

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
        Try to silently install/extract an exe installer.

        Tries strategies in this exact order:
            1. 7z extraction
            2. MSI silent (if .msi file)
            3. NSIS silent (/S)
            4. Inno Setup silent (/VERYSILENT)

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

        # Strategy 1: 7z extraction (safest — never runs the installer)
        if installer_type != "msi":  # Skip 7z for .msi files
            try:
                print("  Trying: 7z extraction ...")
                self._strategy_7z(filepath, app_dir)
                print("  Strategy: 7z extraction succeeded")
                return True
            except Exception as e:
                errors.append(f"7z: {e}")

        # Strategy 2: MSI silent (for .msi files)
        if ext == ".msi" or installer_type == "msi":
            try:
                print("  Trying: MSI silent install ...")
                self._strategy_msi(filepath, app_dir)
                print("  Strategy: MSI silent install succeeded")
                return True
            except Exception as e:
                errors.append(f"MSI: {e}")

        # Strategy 3: NSIS silent (/S)
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

            # If detection was inconclusive, try both NSIS and Inno
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
        result = subprocess.run(
            [filepath, "/S", f"/D={app_dir}"],
            capture_output=True,
            text=True,
            timeout=300,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"NSIS installer exited with code {result.returncode}"
            )

    def _strategy_inno(self, filepath: str, app_dir: str) -> None:
        """Strategy 4: Inno Setup with /VERYSILENT /SUPPRESSMSGBOXES /DIR=."""
        result = subprocess.run(
            [
                filepath,
                "/VERYSILENT",
                "/SUPPRESSMSGBOXES",
                f"/DIR={app_dir}",
            ],
            capture_output=True,
            text=True,
            timeout=300,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"Inno Setup exited with code {result.returncode}"
            )

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
        }
        return type_map.get(installer_type.lower())
