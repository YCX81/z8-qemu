# SPDX-License-Identifier: GPL-2.0-or-later
"""Hash locally supplied v36 artifacts against the recorded laboratory identities."""
import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def digest(path):
    result = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(block)
    return result.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--artifact', action='append', required=True, metavar='NAME=PATH',
                        help='name from identities.json and a local file; repeatable')
    args = parser.parse_args()
    pins = json.loads((ROOT / 'identities.json').read_text())['artifacts']
    results = []
    for item in args.artifact:
        name, separator, filename = item.partition('=')
        if not separator or name not in pins:
            parser.error('unknown artifact name: ' + name)
        path = Path(filename)
        actual = digest(path)
        expected = pins[name]
        passed = actual == expected['sha256'] and path.stat().st_size == expected['bytes']
        results.append(dict(name=name, path=str(path), sha256=actual, passed=passed))
    print(json.dumps(dict(passed=all(row['passed'] for row in results), artifacts=results), indent=2))
    raise SystemExit(0 if all(row['passed'] for row in results) else 1)


if __name__ == '__main__':
    main()
