#!/usr/bin/env python3
"""
Binary Analyzer - HAOS Triage Component
Static analysis tool for ELF, PE and binary headers.

Adapted from Masriyan/Claude-Code-CyberSecurity-Skill for HAOS.
License: MIT
"""

import argparse
import json
import logging
import math
import os
import struct
import sys
import time
from collections import Counter
from typing import Any, Dict, List, Optional

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# Optional libraries
HAS_ELFTOOLS = False
HAS_PEFILE = False
ELFFile: Any = None
SymbolTableSection: Any = None
pefile: Any = None

try:
    from elftools.elf.elffile import ELFFile as _ELFFile  # type: ignore
    from elftools.elf.sections import SymbolTableSection as _SymbolTableSection  # type: ignore
    ELFFile = _ELFFile
    SymbolTableSection = _SymbolTableSection
    HAS_ELFTOOLS = True
except ImportError:
    pass

try:
    import pefile as _pefile  # type: ignore
    pefile = _pefile
    HAS_PEFILE = True
except ImportError:
    pass


class BinaryAnalyzer:
    """Static binary analysis engine for ELF and PE files."""

    def __init__(self, filepath: str):
        self.filepath = filepath
        self.filesize = os.path.getsize(filepath)
        self.file_type = self._identify_type()

    def _identify_type(self) -> str:
        """Identify binary type from magic bytes."""
        with open(self.filepath, "rb") as f:
            magic = f.read(4)
        if magic[:4] == b"\x7fELF":
            return "ELF"
        elif magic[:2] == b"MZ":
            return "PE"
        elif magic[:4] in (b"\xfe\xed\xfa\xce", b"\xce\xfa\xed\xfe"):
            return "Mach-O"
        elif magic[:4] in (b"\xfe\xed\xfa\xcf", b"\xcf\xfa\xed\xfe"):
            return "Mach-O 64"
        else:
            return "Unknown"

    def calculate_entropy(self, block_size: int = 256) -> Dict[str, Any]:
        """Calculate file entropy and per-block entropy distribution."""
        with open(self.filepath, "rb") as f:
            data = f.read()

        overall = self._shannon_entropy(data)

        blocks = []
        for i in range(0, len(data), block_size):
            block = data[i:i + block_size]
            blocks.append({
                "offset": hex(i),
                "entropy": round(self._shannon_entropy(block), 4),
            })

        high_entropy_regions = [b for b in blocks if b["entropy"] > 7.0]

        return {
            "overall_entropy": round(overall, 4),
            "max_possible": 8.0,
            "is_likely_packed": overall > 6.8,
            "is_likely_encrypted": overall > 7.5,
            "high_entropy_blocks": len(high_entropy_regions),
            "total_blocks": len(blocks),
        }

    def _shannon_entropy(self, data: bytes) -> float:
        """Calculate Shannon entropy of data."""
        if not data:
            return 0.0
        counts = Counter(data)
        length = len(data)
        entropy = 0.0
        for count in counts.values():
            probability = count / length
            if probability > 0:
                entropy -= probability * math.log2(probability)
        return entropy

    def extract_strings(self, min_length: int = 4) -> Dict[str, Any]:
        """Extract ASCII strings from binary."""
        with open(self.filepath, "rb") as f:
            data = f.read()

        ascii_strings = []
        current = ""
        for byte in data:
            if 32 <= byte <= 126:
                current += chr(byte)
            else:
                if len(current) >= min_length:
                    ascii_strings.append(current)
                current = ""
        if len(current) >= min_length:
            ascii_strings.append(current)

        urls = [s for s in ascii_strings if "http" in s.lower() or "ftp" in s.lower()]
        ips = [s for s in ascii_strings if self._is_ip_like(s)]
        paths = [s for s in ascii_strings if "/" in s and len(s) > 5]
        emails = [s for s in ascii_strings if "@" in s and "." in s]
        registry = [s for s in ascii_strings if s.startswith(("HKEY_", "HKLM\\", "HKCU\\"))]

        return {
            "total_strings": len(ascii_strings),
            "urls": urls[:50],
            "ip_addresses": ips[:30],
            "file_paths": paths[:50],
            "email_addresses": emails[:20],
            "registry_keys": registry[:30],
            "interesting": [s for s in ascii_strings if any(kw in s.lower() for kw in
                            ["password", "secret", "key", "token", "admin", "root", "cmd", "exec",
                             "shell", "debug", "backdoor", "encrypt", "decrypt"])][:50],
        }

    def _is_ip_like(self, s: str) -> bool:
        """Check if string looks like an IP address."""
        parts = s.split(".")
        if len(parts) == 4:
            try:
                return all(0 <= int(p) <= 255 for p in parts)
            except ValueError:
                pass
        return False

    def analyze_elf(self) -> Dict[str, Any]:
        """Analyze ELF binary using pyelftools or fallback parser."""
        if HAS_ELFTOOLS:
            return self._analyze_elf_with_elftools()
        return self._analyze_elf_fallback()

    def _analyze_elf_with_elftools(self) -> Dict[str, Any]:
        result: Dict[str, Any] = {}
        with open(self.filepath, "rb") as f:
            elf = ELFFile(f)

            result["header"] = {
                "class": elf.elfclass,
                "data_encoding": elf.little_endian and "Little Endian" or "Big Endian",
                "machine": elf.header.e_machine,
                "type": elf.header.e_type,
                "entry_point": hex(elf.header.e_entry),
            }

            result["sections"] = []
            for section in elf.iter_sections():
                result["sections"].append({
                    "name": section.name,
                    "type": section["sh_type"],
                    "address": hex(section["sh_addr"]),
                    "size": section["sh_size"],
                    "flags": section["sh_flags"],
                })

            result["segments"] = []
            for segment in elf.iter_segments():
                result["segments"].append({
                    "type": segment["p_type"],
                    "offset": hex(segment["p_offset"]),
                    "vaddr": hex(segment["p_vaddr"]),
                    "filesz": segment["p_filesz"],
                    "memsz": segment["p_memsz"],
                    "flags": segment["p_flags"],
                })

            result["symbols"] = {"imported": [], "exported": []}
            for section in elf.iter_sections():
                if isinstance(section, SymbolTableSection):
                    for symbol in section.iter_symbols():
                        if symbol.name:
                            sym_info = {
                                "name": symbol.name,
                                "value": hex(symbol["st_value"]),
                                "size": symbol["st_size"],
                                "type": symbol["st_info"]["type"],
                                "bind": symbol["st_info"]["bind"],
                            }
                            if symbol["st_shndx"] == "SHN_UNDEF":
                                result["symbols"]["imported"].append(sym_info)
                            else:
                                result["symbols"]["exported"].append(sym_info)

            result["security"] = self._check_elf_security(elf)
        return result

    def _analyze_elf_fallback(self) -> Dict[str, Any]:
        """Pure-Python fallback parser for ELF headers when pyelftools is not installed."""
        result: Dict[str, Any] = {}
        with open(self.filepath, "rb") as f:
            ident = f.read(16)
            if len(ident) < 16 or ident[:4] != b"\x7fELF":
                return {"error": "Invalid ELF header"}
            ei_class = ident[4]  # 1 = 32-bit, 2 = 64-bit
            ei_data = ident[5]   # 1 = little endian, 2 = big endian
            endian_fmt = "<" if ei_data == 1 else ">"

            if ei_class == 2:  # 64-bit
                f.seek(16)
                hdr_data = f.read(48)
                if len(hdr_data) >= 48:
                    e_type, e_machine, e_version, e_entry, e_phoff, e_shoff, e_flags, e_ehsize, e_phentsize, e_phnum, e_shentsize, e_shnum, e_shstrndx = struct.unpack(
                        endian_fmt + "HHIQQQIHHHHHH", hdr_data
                    )
                    machine_map = {62: "EM_X86_64", 3: "EM_386", 40: "EM_ARM", 183: "EM_AARCH64"}
                    type_map = {1: "ET_REL", 2: "ET_EXEC", 3: "ET_DYN", 4: "ET_CORE"}
                    result["header"] = {
                        "class": 64,
                        "data_encoding": "Little Endian" if ei_data == 1 else "Big Endian",
                        "machine": machine_map.get(e_machine, f"Machine_{e_machine}"),
                        "type": type_map.get(e_type, f"Type_{e_type}"),
                        "entry_point": hex(e_entry),
                    }
                    result["sections"] = [{"name": "fallback_header", "size": self.filesize, "type": "SHT_RAW"}]
                    result["segments"] = []
                    result["symbols"] = {"imported": [], "exported": []}
                    result["security"] = {"PIE": e_type == 3, "NX": True, "Stack_Canary": False, "RELRO": "Unknown"}
                    return result

        return {"error": "ELF parsing failed"}

    def _check_elf_security(self, elf) -> Dict[str, Any]:
        security: Dict[str, Any] = {
            "PIE": False,
            "NX": False,
            "Stack_Canary": False,
            "RELRO": "None",
            "RPATH": False,
            "RUNPATH": False,
        }

        if elf.header.e_type == "ET_DYN":
            security["PIE"] = True

        for segment in elf.iter_segments():
            if segment["p_type"] == "PT_GNU_STACK":
                security["NX"] = not bool(segment["p_flags"] & 0x1)
            if segment["p_type"] == "PT_GNU_RELRO":
                security["RELRO"] = "Partial"

        for section in elf.iter_sections():
            if section.name == ".got.plt":
                if section["sh_flags"] & 0x2 == 0:
                    security["RELRO"] = "Full"

        for section in elf.iter_sections():
            if isinstance(section, SymbolTableSection):
                for symbol in section.iter_symbols():
                    if "__stack_chk_fail" in symbol.name:
                        security["Stack_Canary"] = True

        return security

    def analyze_pe(self) -> Dict[str, Any]:
        """Analyze PE binary."""
        if not HAS_PEFILE:
            return {"error": "pefile not installed: pip install pefile"}

        pe = pefile.PE(self.filepath)
        result: Dict[str, Any] = {}

        result["header"] = {
            "machine": hex(pe.FILE_HEADER.Machine),
            "number_of_sections": pe.FILE_HEADER.NumberOfSections,
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(pe.FILE_HEADER.TimeDateStamp)),
            "entry_point": hex(pe.OPTIONAL_HEADER.AddressOfEntryPoint),
            "image_base": hex(pe.OPTIONAL_HEADER.ImageBase),
            "subsystem": pe.OPTIONAL_HEADER.Subsystem,
        }

        result["sections"] = []
        for section in pe.sections:
            result["sections"].append({
                "name": section.Name.decode("utf-8", errors="ignore").strip("\x00"),
                "virtual_address": hex(section.VirtualAddress),
                "virtual_size": section.Misc_VirtualSize,
                "raw_size": section.SizeOfRawData,
                "entropy": round(section.get_entropy(), 4),
                "characteristics": hex(section.Characteristics),
            })

        result["imports"] = {}
        if hasattr(pe, "DIRECTORY_ENTRY_IMPORT"):
            for entry in pe.DIRECTORY_ENTRY_IMPORT:
                dll_name = entry.dll.decode("utf-8", errors="ignore")
                result["imports"][dll_name] = [
                    imp.name.decode("utf-8", errors="ignore") if imp.name else f"Ordinal_{imp.ordinal}"
                    for imp in entry.imports
                ]

        result["exports"] = []
        if hasattr(pe, "DIRECTORY_ENTRY_EXPORT"):
            for exp in pe.DIRECTORY_ENTRY_EXPORT.symbols:
                result["exports"].append({
                    "name": exp.name.decode("utf-8", errors="ignore") if exp.name else "N/A",
                    "ordinal": exp.ordinal,
                    "address": hex(exp.address),
                })

        result["security"] = {
            "ASLR": bool(pe.OPTIONAL_HEADER.DllCharacteristics & 0x0040),
            "DEP": bool(pe.OPTIONAL_HEADER.DllCharacteristics & 0x0100),
            "SEH": not bool(pe.OPTIONAL_HEADER.DllCharacteristics & 0x0400),
            "Guard_CF": bool(pe.OPTIONAL_HEADER.DllCharacteristics & 0x4000),
            "High_Entropy_VA": bool(pe.OPTIONAL_HEADER.DllCharacteristics & 0x0020),
        }

        pe.close()
        return result

    def run(self, include_strings: bool = True, include_entropy: bool = True) -> Dict[str, Any]:
        """Execute full binary analysis."""
        logger.info("Binary Analysis: %s", self.filepath)

        results = {
            "file": self.filepath,
            "file_size": self.filesize,
            "file_type": self.file_type,
        }

        if self.file_type == "ELF":
            results["elf_analysis"] = self.analyze_elf()
        elif self.file_type == "PE":
            results["pe_analysis"] = self.analyze_pe()

        if include_entropy:
            results["entropy"] = self.calculate_entropy()

        if include_strings:
            results["strings"] = self.extract_strings()

        results["timestamp"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        return results


def main():
    parser = argparse.ArgumentParser(
        description="Binary Analyzer - Static Analysis Tool",
    )
    parser.add_argument("--file", "-f", required=True, help="Binary file to analyze")
    parser.add_argument("--output", "-o", help="Output file (JSON)")
    parser.add_argument("--strings", action="store_true", default=True, help="Extract strings")
    parser.add_argument("--entropy", action="store_true", default=True, help="Calculate entropy")
    parser.add_argument("--no-strings", action="store_true", help="Skip string extraction")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    if not os.path.exists(args.file):
        logger.error("File not found: %s", args.file)
        sys.exit(1)

    analyzer = BinaryAnalyzer(args.file)
    results = analyzer.run(
        include_strings=not args.no_strings,
        include_entropy=args.entropy,
    )

    if args.output:
        with open(args.output, "w") as f:
            json.dump(results, f, indent=2, default=str)
        logger.info("Results saved to %s", args.output)
    else:
        print(json.dumps(results, indent=2, default=str))


if __name__ == "__main__":
    main()
