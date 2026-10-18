# Ivan-mato halfpress reference review

Reference: [nikon-z8-halfpress-raw-precapture at 205fd05](https://github.com/Ivan-mato/nikon-z8-halfpress-raw-precapture/tree/205fd05f9ebae87c6171d756f358f81ad949b6c2).
The repository was cloned separately; no model, binary or sample was merged here.

Its new held-half experiment reports 27 distinct 8280x5520 F0/lossless NEFs after
about 14 virtual seconds of HALF, with stock HALF producing none. It publishes
an 80-file frozen QEMU 11.1.1 model, source locks, portable build/run scripts and
27 highly compressible synthetic NEFs. Seven in-place instructions redirect the
button path; this does not retain a history to save on a subsequent FULL press.

The published sample auditor was executed locally against all 27 files. All
1,234,051,200 CFA samples matched their corresponding pinned synthetic sources,
all decoded hashes were distinct, and the deliberately wrong source was rejected.
This independently rechecks sample content using the supplied algorithm, not its
execution provenance. No fresh firmware run or QEMU build of that repository was
performed. The result still uses LibRaw through rawpy, not a second RAW decoder.

Useful references for the independent lossless work are alternate RAW/JPEG bank
ownership, acknowledged consumption, receiver rearm and ordered queue draining.
They are not drop-in patches for this checkout's different model and firmware
policy. The input is 640x480 synthetic Bayer scaled with CFA phase preserved;
file dimensions are not real sensor-detail evidence.

Limits retained by the author: no physical-camera or 10-fps validation, no
accepted historical-frame retention, no per-frame producer snapshot comparison,
no complete original-three plus sustained-three gate, and no external runtime
reproduction. One capture followed release by about 0.08 virtual seconds, and
the last encode completed about 63 seconds later. The model does not establish
immediate stop or unlimited throughput.

For HE v3 this repository supplies no real HE compressed-frame fixture. Its
value is comparison of lifecycle verification and the alternative lossless
capture route; it cannot substitute for native C15/C30 HE end-to-end evidence.

## 中文结论

已另行拉取该仓库并重验 27 张公开样本，全部样本与对应合成输入一致，错源负控也被拒。
这是半按直接启动普通无损拍摄的功能模型实验，与“全按时保存之前的历史帧”不同。
其多帧 bank、所有权、ACK 和队列排空可用于对照现有无损研究；不能把另一份 QEMU
11.1.1 冻结模型的成功直接移植成本项目已通过，更不能当成 HE 实机证据。
