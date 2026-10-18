# Real HE fixture: exact v3 replay and full mosaic comparison

[中文](#中文核对) · [Receipt](verification/he-v3-decode-20261018.json) ·
[Native scheduling limits](z8-he-v3-native.md)

On 2026-10-18, both exact-hook rewraps decoded successfully with Adobe DNG
Converter 18.7. Each decoded mosaic matched the original public HE sample at
**all 45,705,600 samples (8,280 x 5,520), with zero differences**. CFA pattern,
black levels, white level and camera white balance also matched. This establishes
one real-payload container fixture; it does not establish native C15/C30 capture.

## Input and executed boundary

The input is [raw.pixls.us](https://raw.pixls.us/) record **6616**, Nikon Z8
"high efficiency low", listed under CC0, 24,384,512 bytes, SHA-256
`82df041542b8d328738443bb5ff3373b396e406ecc8cbe7dc3b35252790c53e7`.
Its 17,139,200-byte HE strip, dimensions, 14-bit tag and HE compression identifier
match the pinned patch's format. It is an ordinary camera sample, not a recorded
pre-capture burst. No sample bytes are included in Git.

`tools/z8_he_v3_fixture.py` executes the same pinned 30,301-byte release as the
[81-case lifecycle audit](z8-he-v3.md), without recompiling or changing it. It
injects this sample's HE strip and genuine main/VGA/FHD JPEG streams into explicit
E/Y allocations. An authored JPEG Exif/MPF header supplies Make/Model, white
balance, black level, geometry and the required Nikon metadata fields. It is not
a captured stock JPEG header or a complete original metadata reproduction.

Two history cases exercise RAW+JPEG basic and RAW-only. The latter uses the real
quality-hook instructions, but its JPEG output bytes are supplied by the fixture;
there is no claim that the JPEG engine produced that quality. DCF, writer and
allocator services remain models. The supplied original R-cleanup instructions
execute; all modeled resources and reservations close. Original firmware writer
instructions and RTOS scheduling are not executed in this fixture.

Both NEFs are 24,184,320 bytes and have SHA-256
`9217b17146eaf998592a25851d742e3bd1c4081fa80ee7ba1a0f4285eea1caad`.
Their identical content is intentional: both use the same image and preview
fixture. RAW+JPEG also emits one JPEG; RAW-only does not. This is two paths over
one source image, not two independent captured frames. Normal Python and `-O`
reports matched exactly; a one-byte-changed input was rejected under both.

## External decoding

The official Adobe 18.7 installer was downloaded from Adobe, its complete SHA-256
matched the package manifest, and Authenticode validation identified Adobe Inc.
The installed executable/version and hashes are recorded in the receipt. The
[official converter documentation](https://helpx.adobe.com/camera-raw/desktop/dng-and-file-formats/adobe-dng-converter.html)
describes the required profile installation and mosaic/uncompressed options.

The original NEF and both fixture NEFs were separately converted with `-u -p0`
into new output directories. No linear/demosaicing option was enabled. All three
processes returned zero and created full-size DNGs. rawpy 0.27.1 / LibRaw 0.22.1
then read each entire `raw_image`, without postprocessing, into `uint16` arrays.
Each candidate was compared to the reference with equal shape and
`numpy.array_equal`; a one-sample change was correctly rejected by the comparator.

The shared little-endian mosaic SHA-256 is
`a273a25f3b3f99be4c4ded39a8c8646464b92d29e98ceffcb47a4e0dd7b46ae1`.
All black levels are 1,008, white level is 15,892, and the CFA/color description
matches. Individual DNG file hashes differ, so whole-file equality is not the
criterion. The receipt records both file identities and mosaic identities.

Adobe is the HE decoder for **all three** files. LibRaw reads Adobe's DNG output;
this is not two independent HE decoders. Matching the already-compressed HE
source cannot prove equivalence to lossless RAW, original sensor values, dynamic
range, missing metadata, camera database behavior or repeated physical shooting.

## Reproduce locally

Supply the pinned external toolkit, your original MAIN and the exact sample:

```sh
python -B tools/z8_he_v3_fixture.py --sample /private/pixls-6616.NEF \
  --stock-main /private/z8-c311-main.bin --toolkit /private/toolkit.zip \
  --output .local/he-fixture-new
```

The output directory must not exist. The tool downloads nothing and does not
build an update container. Add `-O` to a second run with a different output path
to repeat the optimization-mode check. Keep all fixtures and outputs outside Git.

Convert each source independently on Windows, replacing the example paths and
using a new destination for each file:

```powershell
& 'C:\Program Files\Adobe\Adobe DNG Converter\Adobe DNG Converter.exe' `
  -u -p0 -d 'C:\private\dng-output' 'C:\private\input.NEF'
```

Wait for the process to exit and verify that a nonempty DNG was created before
comparing it. For each candidate, the core comparison is:

```python
import numpy as np
import rawpy

with rawpy.imread(reference_dng) as ref, rawpy.imread(candidate_dng) as test:
    if ref.raw_image.shape != test.raw_image.shape:
        raise ValueError('Different mosaic dimensions')
    if not np.array_equal(ref.raw_image, test.raw_image):
        raise ValueError('Different mosaic samples')
    if not np.array_equal(ref.raw_pattern, test.raw_pattern):
        raise ValueError('Different CFA pattern')
```

Record input/output hashes, actual converter version/arguments and the full
array hash as in the receipt. Preview decoding alone is insufficient.

## 中文核对

已安装官方 Adobe DNG Converter 18.7。一个公开真实 Z8 HE 样本，经过精确补丁的
RAW+JPEG 与 RAW-only 两条受控路径重封装，再分别由 Adobe 完整解码。两份结果各
45,705,600 个 Bayer 样本与原样本解码结果逐点一致，差异为零；CFA、黑电平、白电平、
白平衡一致。所有资源与登记项在模型中关闭，错源负控通过。

这证明该真实载荷在受控重封装中保留了解码像素。它仍是单张照片、两条路径、同一个
Adobe HE 解码器，不能声称完整 C15/C30 已通过，也不能将 HE 视为无损压缩。
原生 QEMU 的接收器和大尺寸 packed12 缺口另有记录；无损工作树未动，未刷机。
