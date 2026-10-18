# Exact v3 HE patch: lifecycle verification

This checkpoint preserves the [v1 audit](z8-he-history.md) and adds a separate
auditor for NikonFirmwarePatch commit `39665d1347aba2d6181c6b60b630a2156f4407cd`.
It executes the external release's exact 30,301 bytes, SHA-256
`479f4e37db3968a9e796fc97d865246793ebbac83b28f10a95711535c45cb715`.
There is no recompilation or compiler-fingerprint bypass. No upstream machine
code, Nikon firmware or captured image is included in this repository.

Follow-up checkpoints separate [native C15/C30 scheduling stops](z8-he-v3-native.md)
from [real HE payload replay and external decode](z8-he-v3-decode.md). The original
81-case receipt below remains a synthetic lifecycle result.

## Scope

The v3 implementation controls historical and current C15/C30 frames from the
ordinary Image quality menu. It retains E, uses a database member and the stock
writer entry, optionally cancels the JPEG reservation for RAW-only, and allocates
a separate preview region within the original Y resource. Eight entry functions
occupy nine hook call sites; v1's post-write/header-free hook is no longer the
writer interception point. V1 re-entry findings are not automatically v3 findings.

The [receipt](verification/he-v3-20261018.json) covers 81 controlled scenarios:

- 52 combinations of C15/C30, history/current, RAW and six paired/JPEG grades.
- Registration failure, unavailable database, lock failure, NEF writer failure,
  cancellation failure, heap failure, slow writer and false-success/missing file.
- Missing/oversized preview output, insufficient or overlapping Y capacity,
  descriptor initialization failure, Y allocation failure and cancellation.
- Repeated cleanup; per-frame menu snapshot; invalid-metadata JPEG fallback;
  a second trigger with six retained frames and interleaved writer completion;
  a failure followed by a current frame under the persistent stop marker.
- A non-returning writer as an explicitly reproduced limitation.

Every successful synthetic NEF is independently parsed for dimensions, bit depth,
compression identifier, SubIFDs, raw length and frame-to-DMA binding. The HE bytes
and JPEG streams are authored placeholders, not valid codec fixtures. This is
not an independent HE decode or image-quality test.

## Resource and failure observations

With the caller-supplied stock MAIN, the original R-cleanup instructions execute
the E, Y, source and header release calls. The allocator release implementations
and pool notifications remain explicit service models. The preview region is
part of Y; the patch does not separately free that region. Y remains live after
the patch writer returns and is released by R cleanup, including canceled jobs.

Database registration failure preserves the JPEG and uses the legacy unregistered
NEF fallback. That file is not falsely counted as registered playback success.
RAW-only writes the NEF before canceling the JPEG member, checks the cancellation
state, and falls back to JPEG if the NEF write or cancellation fails. The modeled
writer cancels its own failed NEF reservation, as an explicit contract boundary;
the actual filesystem/database implementation has not been reproduced here.

A blocking original-writer call keeps E, Y and scratch in flight. The cooperative
budget cannot unwind it. Missing-file success and over-budget completion create
a stop marker. In the specific RAW-only false-success injection, `stock_nef`
detects the missing file but returns the writer's zero status. `flow` then cancels
the JPEG and returns zero: neither photograph exists, while the modeled NEF
database entry remains. This is recorded as a known limitation, not successful
recovery. It does not establish that the real filesystem has produced this fault.
A future repair would need to propagate the missing-file failure before JPEG
cancellation; the exact reviewed machine code has not been modified here.

## Reproduce

Use Python 3.12+ and `requirements-he-history.txt`. Supply the exact v0.1.0
[release toolkit](https://github.com/XinmiaoZhao/NikonFirmwarePatch/releases/tag/v0.1.0),
as its ZIP or extracted directory. The ZIP hash is pinned in
`profiles/z8-he-v3-source.json`; archive loading extracts no files.

```sh
python -B tools/z8_he_v3_audit.py --toolkit /path/to/toolkit.zip \
  --report .local/he-v3-new.json
```

Add `--stock-main /path/to/z8-c311-main.bin` to execute the original cleanup.
Without it, that boundary is an explicitly labeled contract stand-in. Optional
asset tests use `Z8_HE_V3_TOOLKIT`. The independent v1 audit and its pins remain
unchanged. Reports require a new path.

`tools/z8_he_v3_image.py` derives a local MAIN for emulator experiments only. It
requires the exact stock hash, checks every dependency and all 13 edit fingerprints,
and preserves all other bytes. Its result is not an update container:

```sh
python -B tools/z8_he_v3_image.py --stock-main /path/to/stock-main.bin \
  --toolkit /path/to/toolkit.zip --output .local/he-main-new
```

## 中文核对

本检查点使用作者公布的精确 v3 预编译机器码，旧 v1 的 43 场景及其源码固定关系保留。
本轮 81 场景覆盖数据库登记失败、RAW-only 回退、Y 预览资源失败/取消/释放和忙碌交错。
提供本地原始 MAIN 时，实际执行 R 清理指令；数据库、writer、分配器和 JPEG 引擎仍是
明确服务模型。源数据是合成占位字节，不能据此声称 HE 解码或完整 C15/C30 QEMU 通过。

登记失败时允许“JPEG＋未登记 NEF”回退；RAW-only 取消失败时保住 JPEG；Y 的预览区域
归 Y 分配整体所有，由原 R 清理归还。阻塞调用仍不能被协作预算中断。

一个具体限制已复现：writer 返回零但 NEF 文件不存在时，`stock_nef` 写停止标记后仍返回零，
RAW-only 随后取消 JPEG，外层也返回零，最终没有照片文件。此处只能说故障注入分支有缺口，
不能说实机已经发生丢片。未来修复需要在取消 JPEG 前传回缺文件错误；本轮没有修改精确
审阅机器码。完整系统运行仍须核对菜单、代码部署、原生队列和虚拟卡文件。
