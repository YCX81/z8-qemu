# SPDX-License-Identifier: GPL-2.0-or-later
"""Synthetic-only checks for the static-analysis ELF container."""

import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

from elftools.elf.elffile import ELFFile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from z8_elf import build_analysis_elf, main, write_analysis_elf


CODE = bytes.fromhex("20008052 c0035fd6")  # mov w0, #1; ret
SOURCE = b"synthetic-header" + CODE + b"synthetic-tail"
OFFSET = len(b"synthetic-header")
BASE = 0x40000004  # exercise a base that is not page aligned


class AnalysisElfTests(unittest.TestCase):
    def build(self, **changes):
        options = dict(base=BASE, offset=OFFSET, length=len(CODE), entry=BASE)
        options.update(changes)
        return build_analysis_elf(SOURCE, **options)

    def test_independent_decoder_confirms_executable_and_exact_slice(self):
        artifact, provenance = self.build(entry=BASE + 4)
        elf = ELFFile(io.BytesIO(artifact))
        self.assertEqual(elf.elfclass, 64)
        self.assertTrue(elf.little_endian)
        self.assertEqual(elf["e_machine"], "EM_AARCH64")
        self.assertEqual(elf["e_type"], "ET_EXEC")
        self.assertEqual(elf["e_entry"], BASE + 4)
        self.assertEqual(elf.num_segments(), 1)
        segment = elf.get_segment(0)
        self.assertEqual(segment["p_type"], "PT_LOAD")
        self.assertEqual(segment["p_flags"], 5)
        self.assertEqual(segment["p_vaddr"], BASE)
        self.assertEqual(segment["p_paddr"], BASE)
        self.assertEqual(segment["p_filesz"], len(CODE))
        self.assertEqual(segment["p_memsz"], len(CODE))
        self.assertEqual(segment["p_vaddr"] % segment["p_align"],
                         segment["p_offset"] % segment["p_align"])
        self.assertEqual(segment.data(), CODE)
        section = elf.get_section_by_name(".text")
        self.assertEqual(section.data(), CODE)
        self.assertEqual(section["sh_addr"], BASE)
        self.assertEqual(section["sh_flags"], 6)
        self.assertEqual(elf.get_section(elf["e_shstrndx"]).name, ".shstrtab")
        self.assertIn(b"NOT AN ORIGINAL FIRMWARE ELF", artifact)
        self.assertFalse(provenance["runtime_mapping_verified"])

    def test_provenance_binds_source_slice_mapping_and_output(self):
        artifact, provenance = self.build()
        self.assertEqual(provenance["source"]["sha256"], hashlib.sha256(SOURCE).hexdigest())
        self.assertEqual(provenance["source"]["size"], len(SOURCE))
        self.assertEqual(provenance["slice"], dict(
            offset=OFFSET, length=len(CODE), sha256=hashlib.sha256(CODE).hexdigest()))
        self.assertEqual(provenance["mapping"]["base"], BASE)
        self.assertEqual(provenance["mapping"]["entry"], BASE)
        self.assertEqual(provenance["mapping"]["entry_offset"], 0)
        self.assertEqual(provenance["mapping"]["authority"], "analyst-supplied")
        self.assertEqual(provenance["artifact"]["sha256"], hashlib.sha256(artifact).hexdigest())
        self.assertFalse(provenance["original_firmware_elf"])
        self.assertIn("not establish", provenance["limitations"])
        self.assertEqual((artifact, provenance), self.build())

    def test_rejects_bounds_alignment_and_integer_errors(self):
        cases = [
            dict(offset=-1), dict(offset=len(SOURCE)), dict(length=0),
            dict(length=-1), dict(length=len(SOURCE)),
            dict(entry=BASE - 4), dict(entry=BASE + len(CODE)),
            dict(entry=BASE + 1), dict(length=5, entry=BASE + 4),
            dict(base=-4), dict(base=BASE + 1),
            dict(base=2**64 - 4, entry=2**64 - 4),
            dict(base=2**64), dict(entry=2**64),
            dict(offset=True), dict(length=1.5),
        ]
        for case in cases:
            with self.subTest(case=case), self.assertRaises(ValueError):
                self.build(**case)

    def test_accepts_last_complete_instruction(self):
        artifact, _ = self.build(entry=BASE + 4)
        self.assertEqual(ELFFile(io.BytesIO(artifact))["e_entry"], BASE + 4)


class AnalysisElfFileTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.source = self.directory / "original.bin"
        self.source.write_bytes(SOURCE)
        self.output = self.directory / "slice.analysis.elf"
        self.provenance = self.directory / "slice.provenance.json"
        self.options = dict(base=BASE, offset=OFFSET, length=len(CODE), entry=BASE)

    def write(self, **changes):
        options = dict(self.options)
        options.update(changes)
        return write_analysis_elf(self.source, self.output, self.provenance, **options)

    def test_write_preserves_input_and_outputs_matching_json(self):
        record = self.write()
        self.assertEqual(self.source.read_bytes(), SOURCE)
        self.assertEqual(json.loads(self.provenance.read_text()), record)
        self.assertEqual(record["source"]["path"], str(self.source.resolve()))
        self.assertEqual(record["artifact"]["path"], str(self.output.resolve()))
        self.assertEqual(hashlib.sha256(self.output.read_bytes()).hexdigest(),
                         record["artifact"]["sha256"])

    def test_existing_either_output_blocks_all_writes(self):
        for existing, missing in ((self.output, self.provenance),
                                  (self.provenance, self.output)):
            with self.subTest(existing=existing):
                existing.write_bytes(b"keep")
                with self.assertRaises(FileExistsError):
                    self.write()
                self.assertEqual(existing.read_bytes(), b"keep")
                self.assertFalse(missing.exists())
                existing.unlink()

    def test_rejects_same_output_paths_and_source_as_output(self):
        with self.assertRaises(ValueError):
            write_analysis_elf(self.source, self.output, self.output, **self.options)
        with self.assertRaises(ValueError):
            write_analysis_elf(self.source, self.source, self.provenance, **self.options)
        with self.assertRaises(ValueError):
            write_analysis_elf(self.source, self.output, self.source, **self.options)
        self.assertEqual(self.source.read_bytes(), SOURCE)
        self.assertFalse(self.output.exists())

    def test_invalid_slice_leaves_no_outputs(self):
        with self.assertRaises(ValueError):
            self.write(length=len(SOURCE))
        self.assertFalse(self.output.exists())
        self.assertFalse(self.provenance.exists())

    def test_missing_parent_does_not_leave_one_output(self):
        with self.assertRaises(FileNotFoundError):
            write_analysis_elf(self.source, self.output,
                               self.directory / "missing" / "provenance.json", **self.options)
        self.assertFalse(self.output.exists())

    def test_cli_accepts_explicit_hex_mapping_and_writes_provenance(self):
        command = [sys.executable, str(Path(__file__).resolve().parents[1] / "tools" / "z8_elf.py"),
                   str(self.source), str(self.output), "--provenance", str(self.provenance),
                   "--base", hex(BASE), "--offset", str(OFFSET), "--length", "0x8", "--entry", hex(BASE)]
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), json.loads(self.provenance.read_text()))
        self.assertEqual(self.source.read_bytes(), SOURCE)
        second = subprocess.run(command, capture_output=True, text=True, check=False)
        self.assertEqual(second.returncode, 2)
        self.assertIn("error:", second.stderr)

    def test_main_returns_json_and_reports_invalid_mapping(self):
        arguments = [str(self.source), str(self.output), "--provenance", str(self.provenance),
                     "--base", hex(BASE), "--offset", str(OFFSET), "--length", "8", "--entry", hex(BASE)]
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(main(arguments), 0)
        self.assertEqual(json.loads(output.getvalue()), json.loads(self.provenance.read_text()))
        error = io.StringIO()
        with redirect_stderr(error), self.assertRaises(SystemExit) as raised:
            main(arguments)
        self.assertEqual(raised.exception.code, 2)
        self.assertIn("refusing to overwrite", error.getvalue())


if __name__ == "__main__":
    unittest.main()
