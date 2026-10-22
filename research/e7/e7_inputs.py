# SPDX-License-Identifier: GPL-2.0-or-later
"""Explicit private inputs and non-overwriting outputs for published E7 tools."""
import hashlib
import os
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
BIRD_CORE_SHA256 = '02d876f5f0958c931b43bb81bf887de503b039012598234b8c1feef6c35f3562'


def checked_file(value, expected_sha256=None):
    path = Path(value).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(path)
    if expected_sha256 and hashlib.sha256(path.read_bytes()).hexdigest() != expected_sha256:
        raise ValueError('Input SHA-256 mismatch: '+str(path))
    return path


def bird_source():
    value = os.environ.get('E7_BIRD_SOURCE')
    if not value:
        raise ValueError('Set E7_BIRD_SOURCE to the separately obtained, pinned e7-bird-network checkout')
    return checked_file(Path(value)/'core.py', BIRD_CORE_SHA256).parent


def private_firmware_dir():
    value = os.environ.get('E7_FIRMWARE_DIR')
    if not value:
        raise ValueError('Set E7_FIRMWARE_DIR to the private pinned MAIN directory')
    path = Path(value).expanduser().resolve()
    if not path.is_dir():
        raise FileNotFoundError(path)
    return path


def new_output_dir(value):
    path = Path(value).expanduser().resolve()
    if path.is_relative_to(REPO) and not path.is_relative_to(REPO/'.local'):
        raise ValueError('Private outputs must be outside the checkout or inside its ignored .local directory')
    path.mkdir(parents=True, exist_ok=False)
    return path
