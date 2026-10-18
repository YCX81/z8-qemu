# Exact HE v3: native C15/C30 discovery

[中文](#中文核对) · [Lifecycle audit](z8-he-v3.md) ·
[Measured receipt](verification/he-v3-native-20261018.json)

The 2026-10-18 native runs did **not** produce photographs. They establish current
model gaps after booting the exact derived MAIN, using menu contacts and original
HALF/FULL MCU consumers. No PC redirection or debugger RAM modification was used.
The independent lossless worktrees and their retained inputs were not changed.

| Run | Menu readback | First unsupported request | Card NEF/JPEG |
| --- | --- | --- | --- |
| HE C30 | mode 3, history capacity 30, RAW type 1 | CPU1 PC `0x40c30ec8`, write `0x15300008=1` after FULL | 0 / 0 |
| Stock C30 control | same | same PC, address and value | 0 / 0 |
| HE C15 | mode 6, history capacity 15, RAW type 1 | CPU1 PC `0x40c3167c`, write `0x152c0008=1` during HALF, before FULL | 0 / 0 |

The HE runs read back all 30,301 release-code bytes from the declared boot-copy
destination, with SHA-256 matching the pinned release. That verifies deployment
at this address; it does not itself prove instruction fetch or hook completion.
The three owned QEMU processes were stopped before read-only card extraction and
`fsck.exfat -n`. All three filesystems passed; independent catalogs found only
configuration/body metadata and directories, with no photograph files.

## C30: original large packed12 request

Both HE and stock request format word `15300530=2`, capacity `15300534=01058600`,
stride `15300544=30a0` (12,448 bytes), width `15300548=2058` (8,280), height
`1530054c=1590` (5,520), and `15b00084=20581590`. The frozen I6i NR02 model's
`nikon-z8-hsfc-decode.h` admits large C30 only with its U16 option, format 0 and
stride 16,576. Its smaller packed12 contract does not cover this request.
This explains the shared first stop; it is not evidence that the HE patch failed
on a physical camera or that the rest of its native execution would pass.

The receiver emitted 26 model packets in the HE run and 27 in the stock control.
These are NZ8BAYR1 synthetic packets, **not valid Nikon HE**, and not counts of
saved NEFs. Timing and packet counts are not deterministic equivalence claims.
The HE trace reached original history submission and backend submission for the
same R object before the unsupported decoder request.

## C15: different receiver contract

At the first receiver START, bank-0 word `0x114` is zero, with only the initial
target populated. The frozen C30 receiver requires `0x114=3` and its four-target
layout. This is an earlier, separate gap. A C15 stock control and a complete
receiver ownership/cadence model remain outstanding; no C15 historical packet
or complete history drain was observed.

## Reproduction limits and next acceptance gates

These discovery runs used a retained QEMU 10.2.4 binary, pinned by hash in the
receipt; 47 build inputs matched its retained I6i source. They used fresh 8 GiB
virtual cards, a CRC-checked v3 configuration, explicit synthetic sensor/timing
inputs, and the existing bounded MCU/writer peers. Menu navigation needed an
explicit private channel-8 peer budget of 4,096 batches / 65,536 words. This does
not remove descriptor, address, transmission or first-unknown guards.

The public MAIN builder supplies the exact 13-edit recipe. The discovery wrapper
was private and was not snapshotted at each launch. Actual `/proc` argv was saved
for C15 HE and C30 stock, but not C30 HE. The retained result/log hashes therefore
identify observations, not a complete independently reproducible native release.
No runtime logs, firmware bytes, cards or photographs are published here.

Next, isolate and test C15 receiver ownership and the observed large packed12
decoder contract without weakening strict unknown handling. Native scheduling
must then reach original writer, final card close, per-frame identities and
repeat-trigger cleanup. A valid HE replay is a separate codec/container fixture;
the current NZ8BAYR1 encoder cannot be treated as a Nikon HE encoder. Full C15/C30
acceptance needs both a complete scheduling run and independently decodable,
correctly associated photo files. None of these stops validates flashing.

## 中文核对

本轮完整 C15/C30 尚未通过，虚拟卡上 NEF/JPEG 均为零。C30 原厂与精确 HE 补丁
同停于大尺寸 packed12 解码请求；现有模型只接纳大尺寸 U16。C15 更早停于半按时
不同的接收器缓冲配置，还需要原厂对照和所有权/节拍验证。

两次 HE 运行均读回并核对了完整预编译代码；这证明指定地址的部署，不等于已证明
每个钩子执行成功。26/27 是合成模型包数，不能写成有效 HE 帧数或已保存照片数。
三张停止后的虚拟卡文件系统均通过检查，但这同样不等于拍摄成功。无损分支保留，未刷机。
