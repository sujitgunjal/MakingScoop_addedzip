"""
metadata.py — Application Metadata Extractor for myscoop

Extracts Windows file properties from installed executables using pywin32.
Saves metadata as JSON and displays it in a formatted terminal table.

Usage:
    meta = AppMetadata("C:/Users/you/myscoop/apps/postman/12.1.4/Postman.exe")
    info = meta.extract()
    meta.save("C:/Users/you/myscoop/apps/postman/12.1.4/")
    meta.display()
"""

import json
import os
import zlib
from pathlib import Path
from typing import Any, Dict, Optional

try:
    import win32api
    HAS_WIN32 = True
except ImportError:
    HAS_WIN32 = False

from colorama import Fore, Style, init as colorama_init


class AppMetadata:
    """
    Extracts and manages metadata for an installed application executable.

    Uses the pywin32 library to read Windows file version info
    (the same data you see in Properties → Details).
    """

    def __init__(self, filepath: str) -> None:
        """
        Args:
            filepath: Absolute path to the exe file.

        Raises:
            FileNotFoundError: If the file does not exist.
        """
        self.filepath: str = os.path.normpath(filepath)
        if not os.path.exists(self.filepath):
            raise FileNotFoundError(
                f"File not found: {self.filepath}\n"
                f"  Cannot extract metadata for a file that doesn't exist."
            )
        self._metadata: Optional[Dict[str, Any]] = None

    def extract(self) -> Dict[str, Any]:
        """
        Extract all metadata fields from the executable.

        Returns a dict with these keys:
            filename, filepath, filesize, filecrc32,
            fileversion, filedescription, filemanufacturer

        Returns:
            Dict containing all 7 metadata fields.
        """
        file_size = os.path.getsize(self.filepath)

        self._metadata = {
            "filename": os.path.basename(self.filepath),
            "filepath": self.filepath,
            "filesize": {
                "bytes": file_size,
                "human": self._human_size(file_size),
            },
            "filecrc32": self._compute_crc32(),
            "fileversion": self._get_version_info("FileVersion"),
            "filedescription": self._get_version_info("FileDescription"),
            "filemanufacturer": self._get_version_info("CompanyName"),
        }

        return self._metadata

    def save(self, output_dir: str) -> str:
        """
        Save metadata as a JSON file to the specified directory.

        Args:
            output_dir: Directory to save metadata.json into.

        Returns:
            Path to the saved metadata.json file.
        """
        if self._metadata is None:
            self.extract()

        os.makedirs(output_dir, exist_ok=True)
        output_path = os.path.join(output_dir, "metadata.json")

        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(self._metadata, f, indent=4, ensure_ascii=False)

        return output_path

    def display(self) -> None:
        """Print metadata in a clean formatted table using colorama."""
        colorama_init(autoreset=True)

        if self._metadata is None:
            self.extract()

        data = self._metadata

        # Table dimensions
        label_width = 18
        value_width = 40
        total_width = label_width + value_width + 3  # borders + separator

        # Format file size display
        size_info = data["filesize"]
        size_str = f"{size_info['bytes']:,} bytes"

        rows = [
            ("Filename", data["filename"]),
            ("Filepath", self._truncate_path(data["filepath"], value_width)),
            ("File Size", size_str),
            ("CRC32", data["filecrc32"] or "N/A"),
            ("File Version", data["fileversion"] or "N/A"),
            ("Description", data["filedescription"] or "N/A"),
            ("Manufacturer", data["filemanufacturer"] or "N/A"),
        ]

        # Print table
        print(f"\n{Fore.CYAN}┌{'─' * total_width}┐")
        print(f"│  {Fore.WHITE}{Style.BRIGHT}App Metadata{Style.RESET_ALL}"
              f"{' ' * (total_width - 14)}{Fore.CYAN}│")
        print(f"├{'─' * label_width}┬{'─' * (value_width + 2)}┤")

        for label, value in rows:
            print(
                f"│  {Fore.WHITE}{label:<{label_width - 2}}"
                f"{Fore.CYAN}│  {Fore.GREEN}{value:<{value_width}}{Fore.CYAN}│"
            )   

        print(f"└{'─' * label_width}┴{'─' * (value_width + 2)}┘{Style.RESET_ALL}\n")

    @classmethod
    def load_from_json(cls, json_path: str) -> "AppMetadata":
        """
        Load metadata from a previously saved metadata.json file.

        Args:
            json_path: Path to metadata.json.

        Returns:
            AppMetadata instance with loaded data.
        """
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        # Create instance without file existence check
        instance = object.__new__(cls)
        instance.filepath = data.get("filepath", "")
        instance._metadata = data
        return instance

    # ──────────────────────────────────────────────
    # Internal helpers
    # ──────────────────────────────────────────────

    def _compute_crc32(self) -> str:
        """Compute CRC32 checksum of the file."""
        try:
            crc = 0
            with open(self.filepath, "rb") as f:
                while True:
                    chunk = f.read(65536)
                    if not chunk:
                        break
                    crc = zlib.crc32(chunk, crc)
            # Format as uppercase hex string
            return f"{crc & 0xFFFFFFFF:08X}"
        except (IOError, OSError):
            return None

    def _get_version_info(self, field: str) -> Optional[str]:
        """
        Read a string from the exe's Windows version info resource.

        Uses win32api.GetFileVersionInfo to read fields like
        FileVersion, FileDescription, CompanyName.

        Args:
            field: Version info field name.

        Returns:
            Field value string, or None if not available.
        """
        if not HAS_WIN32:
            return None

        try:
            info = win32api.GetFileVersionInfo(self.filepath, "\\")
            # Get the translation table to find the right language/codepage
            translations = win32api.GetFileVersionInfo(
                self.filepath,
                "\\VarFileInfo\\Translation"
            )
            if translations:
                lang, codepage = translations[0]
                str_path = f"\\StringFileInfo\\{lang:04x}{codepage:04x}\\{field}"
                value = win32api.GetFileVersionInfo(self.filepath, str_path)
                if value:
                    return value.strip()
        except Exception:
            pass

        return None

    @staticmethod
    def _human_size(size_bytes: int) -> str:
        """Convert bytes to human-readable string (matches Windows Explorer)."""
        import math
        for unit in ["B", "KB", "MB", "GB"]:
            if size_bytes < 1024.0:
                # Match Windows Explorer:
                #   < 100 → show 1 decimal (e.g. 63.4 MB)
                #   >= 100 → whole number  (e.g. 185 MB)
                if size_bytes >= 100:
                    return f"{int(size_bytes)} {unit}"
                truncated = math.floor(size_bytes * 10) / 10
                return f"{truncated:.1f} {unit}"
            size_bytes /= 1024.0
        return f"{int(size_bytes)} TB"

    @staticmethod
    def _truncate_path(path: str, max_len: int) -> str:
        """Truncate a file path for display if too long."""
        if len(path) <= max_len:
            return path
        # Show ...\last_parts
        parts = path.split(os.sep)
        result = path
        while len(result) > max_len and len(parts) > 2:
            parts.pop(1)
            result = parts[0] + os.sep + "..." + os.sep + os.sep.join(parts[1:])
        return result
