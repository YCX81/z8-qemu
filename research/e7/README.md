# E7 neural research checkpoint

[中文](README.zh-CN.md)

Authored model-format parsers, candidate CPU replay/quantization paths and bounded
postprocessing contracts recovered during the Z8 C3.11 / Z9 C5.32 research.
This is an independent topic-branch checkpoint, not a camera feature or firmware
package. It does not establish original E7 accelerator equivalence or autofocus
performance. See [status and limits](docs/status.md).

## E7-bird reference

The separately obtained [WonderLantern/e7-bird-network](https://github.com/WonderLantern/e7-bird-network/tree/8ced43b19edb845baef39a80c45d40be0b49efba)
supplies the resource744 arithmetic helper used by the generic PCNN replay.
`external-reference.json` pins the public commit and the checked `core.py` SHA-256.
That project's finite-input physical validation is its own ZR evidence; it is not
a local Z8 hardware validation. Other resources retain additional hypotheses.
No external source, weights, fixtures or binaries are copied into this checkpoint.

## Files and public checks

| Tool | Published scope |
| --- | --- |
| `parse_models.py` | Identity-pinned static animal/vehicle CDNN descriptor recovery |
| `pcnn_inventory.py` | Recognized PCNNP resource identities; explicit private extraction output |
| `pcnn_packets.py` | Bounded packet parser; unknown commands fail closed |
| `pcnn_replay.py` | Candidate generic CPU replay; external pinned arithmetic dependency |
| `neupro_forward.py` | Candidate animal integer forward path with explicit, unresolved rounding policy |
| `animal_channels.py` | Animal-channel reconstruction; no final integer clipping/record packing |

From the repository root:

```sh
python -m pip install -r research/e7/requirements.txt
python -B -m unittest discover -s research/e7/tests -v
```

The public checks require no firmware or SDK. Torch and private-input cases skip
explicitly when their dependencies are absent. For optional CPU Torch arithmetic
and replay, install `requirements-replay.txt` in a separate environment appropriate
to your platform. The locally tested baseline used CPU Torch 2.9.1.

Private checks accept explicit paths; no inputs are downloaded automatically:

```sh
export E7_FIRMWARE_DIR=/private/pinned-main-images
export E7_ANIMAL_INVENTORY=/private/animal-symbol-inventory.json
export E7_BIRD_SOURCE=/external/pinned-e7-bird-network
export E7_REPLAY_INVENTORY=/private/pcnn-inventory.json
python -B -m unittest discover -s research/e7/tests -v
```

PowerShell users can set the same environment variables with `$env:NAME='path'`.
Private MAIN files must be named `z8-c311-main.bin` and `z9-c532-main.bin` and match
the hashes in the parsers. Private resource and quantization manifests keep their
own input-file hashes. Original weights and calibration tables are not supplied.

Metadata extraction and replay use new output directories outside the checkout,
or under its ignored `.local` directory, and refuse existing output directories:

```sh
python research/e7/parse_models.py --firmware-dir /private/main --output /private/new-static-report
python research/e7/pcnn_inventory.py --firmware-dir /private/main --output /private/new-resource-inventory
python research/e7/pcnn_replay.py --inventory /private/new-resource-inventory/inventory.json \
  --bird-source /external/pinned-e7-bird-network --ids 744 --cases stripe \
  --output /private/new-replay
python research/e7/neupro_forward.py --parameters /private/original-quantization.json \
  --case zero --output /private/new-integer-forward
```

The quantization parameter exporter and native vendor-backend drivers remain
private lab components. This public subset is not a complete standalone recreation
of those SDK-dependent pipelines. `source-provenance.json` identifies the authored
source snapshots and publication adaptations. See the dated [model benchmark](docs/model-benchmark-20261022.zh-CN.txt)
and [peer comparison](docs/peer-benchmark-20261022.zh-CN.txt) for historical results,
small-sample limits, unresolved semantics and evidence that was not rerun here.

Firmware, model assets, photographic inputs/activations, private vendor software,
licenses and raw device-session buffers are excluded. Tests and numerical
summaries must not be interpreted as a flashable firmware or a physical camera
acceptance result.
