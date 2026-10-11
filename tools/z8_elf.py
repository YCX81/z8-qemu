#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Wrap an explicitly mapped firmware slice for static AArch64 analysis only.

This creates a derived ELF64 container, NOT an original firmware ELF or a
flashable image. The user supplies every mapping value: ``offset`` is a source
file byte offset, ``length`` is the slice byte count, ``base`` is the analysis
address of the slice's first byte, and ``entry`` is an ABSOLUTE analysis address
inside that slice. No parameter establishes the actual device runtime mapping.

Example (addresses and range must be independently investigated first)::

    python tools/z8_elf.py payload.bin slice.analysis.elf \
        --offset 0x100 --length 0x1000 --base 0x40000000 \
        --entry 0x40000000 --provenance slice.provenance.json

Only the chosen bytes are loaded as a read/execute .text section. Bytes outside
the slice, cross-slice references, actual memory permissions, and hardware state
are absent. The entry must point to a complete, four-byte-aligned instruction;
this utility does not determine whether those bytes are valid code.
"""

import argparse
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import struct


ANALYSIS_MARKER = (
    b"STATIC ANALYSIS CONTAINER - NOT AN ORIGINAL FIRMWARE ELF; "
    b"analyst-supplied addresses do not establish device runtime mapping.\0"
)
LIMITATIONS = (
    "Derived static-analysis container, not an original firmware ELF or flashable "
    "image. Analyst-supplied addresses and the synthetic RX segment do not "
    "establish runtime mapping or device permissions. Only the selected source "
    "bytes are present; references outside the slice remain unresolved."
)


def build_analysis_elf(source: bytes, *, base: int, offset: int,
                       length: int, entry: int) -> tuple[bytes, dict]:
    """Return deterministic ELF bytes and provenance without filesystem access.

    ``entry`` is an absolute analysis address, not an offset. ``base`` maps
    ``source[offset]``. Source bytes are copied unchanged into the ELF .text.
    """
    for name, value in (("base", base), ("offset", offset),
                        ("length", length), ("entry", entry)):
        if type(value) is not int or value < 0 or value >= 2**64:
            raise ValueError(f"{name} must be an unsigned 64-bit integer")
    if length == 0 or offset + length > len(source):
        raise ValueError("slice must be nonempty and entirely inside the source")
    if base + length > 2**64:
        raise ValueError("analysis mapping exceeds the ELF64 address space")
    if base % 4 or entry % 4:
        raise ValueError("base and absolute entry must be four-byte aligned")
    if entry < base or entry + 4 > base + length:
        raise ValueError("entry must select a complete instruction inside the slice")

    selected = source[offset:offset + length]
    # ELF requires p_offset and p_vaddr to be congruent modulo p_align.
    text_offset = 0x1000 + base % 0x1000
    comment_offset = text_offset + length
    names = b"\0.text\0.comment\0.shstrtab\0"
    names_offset = comment_offset + len(ANALYSIS_MARKER)
    section_offset = (names_offset + len(names) + 7) & ~7
    result = bytearray(section_offset + 4 * 64)

    ident = b"\x7fELF\x02\x01\x01" + bytes(9)
    struct.pack_into("<16sHHIQQQIHHHHHH", result, 0,
                     ident, 2, 183, 1, entry, 64, section_offset, 0,
                     64, 56, 1, 64, 4, 3)
    struct.pack_into("<IIQQQQQQ", result, 64,
                     1, 5, text_offset, base, base, length, length, 0x1000)
    result[text_offset:text_offset + length] = selected
    result[comment_offset:comment_offset + len(ANALYSIS_MARKER)] = ANALYSIS_MARKER
    result[names_offset:names_offset + len(names)] = names

    # The first section header remains the required all-zero SHT_NULL entry.
    for index, name, kind, flags, address, file_offset, size, alignment in (
        (1, b".text", 1, 6, base, text_offset, length, 4),
        (2, b".comment", 1, 0, 0, comment_offset, len(ANALYSIS_MARKER), 1),
        (3, b".shstrtab", 3, 0, 0, names_offset, len(names), 1),
    ):
        struct.pack_into("<IIQQQQIIQQ", result, section_offset + index * 64,
                         names.index(name), kind, flags, address, file_offset,
                         size, 0, 0, alignment, 0)

    artifact = bytes(result)
    provenance = {
        "schema": "z8-static-analysis-elf-v1",
        "original_firmware_elf": False,
        "runtime_mapping_verified": False,
        "source": {"sha256": hashlib.sha256(source).hexdigest(), "size": len(source)},
        "slice": {"offset": offset, "length": length,
                  "sha256": hashlib.sha256(selected).hexdigest()},
        "mapping": {"authority": "analyst-supplied", "base": base,
                    "entry": entry, "entry_offset": entry - base,
                    "segment_permissions": "synthetic-read-execute"},
        "artifact": {"format": "ELF64-LE-AArch64-analysis-container",
                     "sha256": hashlib.sha256(artifact).hexdigest(), "size": len(artifact)},
        "limitations": LIMITATIONS,
    }
    return artifact, provenance


def write_analysis_elf(source_path: Path, output_path: Path, provenance_path: Path,
                       *, base: int, offset: int, length: int, entry: int) -> dict:
    """Create two new outputs exclusively; never rewrite the input or outputs.

    Both parent directories must exist. A failed output write removes only the
    new files created by this call. Existing files (including symlinks) are
    refused; invalid slices produce neither output.
    """
    source_path, output_path, provenance_path = map(
        Path, (source_path, output_path, provenance_path))
    resolved = [path.resolve() for path in (source_path, output_path, provenance_path)]
    if len(set(resolved)) != 3:
        raise ValueError("source, ELF output, and provenance must have distinct paths")
    for path in (output_path, provenance_path):
        if os.path.lexists(path):
            raise FileExistsError(f"refusing to overwrite existing output: {path}")
    artifact, provenance = build_analysis_elf(
        source_path.read_bytes(), base=base, offset=offset, length=length, entry=entry)
    provenance["source"]["path"] = str(resolved[0])
    provenance["artifact"]["path"] = str(resolved[1])
    metadata = (json.dumps(provenance, indent=2, sort_keys=True) + "\n").encode("utf-8")
    created = []
    try:
        with ExitStack() as stack:
            for path, content in ((output_path, artifact), (provenance_path, metadata)):
                stream = stack.enter_context(path.open("xb"))
                created.append(path)
                stream.write(content)
    except BaseException:
        for path in created:
            path.unlink(missing_ok=True)
        raise
    return provenance


def main(argv: list[str] | None = None) -> int:
    """CLI requiring explicit slice, mapping, entry, and provenance output."""
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("source", type=Path, help="local source payload; read only")
    parser.add_argument("output", type=Path, help="new derived .analysis.elf file")
    parser.add_argument("--provenance", required=True, type=Path, help="new provenance JSON file")
    for name, description in (
        ("base", "analysis address of the first slice byte"),
        ("offset", "source file byte offset"),
        ("length", "slice byte count"),
        ("entry", "absolute analysis entry address, NOT a source or slice offset"),
    ):
        parser.add_argument(f"--{name}", required=True, type=lambda value: int(value, 0),
                            help=description + " (decimal or 0x hex)")
    args = parser.parse_args(argv)
    try:
        provenance = write_analysis_elf(args.source, args.output, args.provenance,
                                        base=args.base, offset=args.offset,
                                        length=args.length, entry=args.entry)
    except (OSError, ValueError) as error:
        parser.error(str(error))
    print(json.dumps(provenance, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
