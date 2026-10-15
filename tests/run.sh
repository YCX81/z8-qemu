#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-or-later
set -eu
cd "$(dirname "$0")/.."
# Discovery includes test_producer_journal_helpers.py: both C helpers at -O0 and -O2 -DNDEBUG.
# Standalone D980 ->151 ->1540 F0/lineage QTest is test_z8_lossless14_lineage.py
# (requires explicit --qemu and fresh --output; separate from main's codec test).
python3 -B -m unittest discover -s tests -v
test_binary=$(mktemp)
trap 'rm -f "$test_binary"' EXIT HUP INT TERM
cc -std=c11 -O2 -Wall -Wextra -Werror tests/test_pixels.c $(pkg-config --cflags --libs glib-2.0) -lm -o "$test_binary"
"$test_binary"
cc -std=c11 -O2 -Wall -Wextra -Werror tests/test_sensor_tag.c -o "$test_binary"
"$test_binary"
