# 153c 集成检查点（2026-10-14）

独立工作树：`z8-raw-precapture-shared146-20261014`，分支
`codex/raw-precapture-shared146`。没有修改集成树，没有提交、推送或刷机。
所有本任务原生/QTest 进程均已停止。

## 本次合并准备

- `qemu/nikon-z8-hsfc-encode.h` 已拆出共用数据事务及1540包装；
  `qemu/nikon-z8-hsfc153c.h` 使用独立153c寄存器、结果、pending、IRQ130及计数。
- 纯事务中有明确的 `HSFC_OWNER_RESERVE`、`HSFC_OWNER_COMPLETE`、
  `HSFC_OWNER_CANCEL` 接线位置；全部事务失败统一经过清理出口。
  两条原生 W1C 路径分别有 `HSFC_OWNER_ACK`，先验证对应记录，再标记确认。
- 两个包装各传入自己的 `encoder_job`。历史记录的全局 `job` 仅用于收据，
  不作为共享1540所有权 slot 的作业号。集成时153c须分配独立 slot。
- 当前没有接入 main 的 `nikon-buffer-ownership.h`；状态显式报告
  `output_ownership_connected=false`。main 的 `nb_ack` 应在标记历史记录、
  清 pending 之前成功，且1540原有调用只能执行一次。
- `tests/test_z8_lossless14.py` 改为 `tests/test_z8_lossless14_lineage.py`。
  `tests/run.sh` 说明已同步；当前 build/allowlist 没有旧文件名的显式引用。
  历史冻结测试副本、运行收据保留。153c测试使用独立文件名。
- 普通 F0 journal 恢复 main ABI：保留 producer pitch，以 `pitch×height`
  约束压缩长度，span 保存实际长度。`nikon-producer-journal.h` 与本次读取
  的 main 文件字节一致，SHA256 为
  `f44b9757dc9d6b13fde226dbcf938c3da4be7b0ea94fed593da763f675dd014f`。
- HSFC仍拒绝 `producer_journal.enabled`，标记为“待按 source_kind 支持”。
  152c/1530统一前端钩子及 NZ8BAYR1分派留给录像/集成工作。
- 已补充录像 header `0x302` 不能误用照片 C1 重用路径的反例。

## 原生证据：RAW63

实际运行二进制 SHA256：
`de44312c8d0b6a714d779efeeff32865dd964d6985a206be388821525746f33c`。
派生程序为 policy61，SHA256：
`4de3d2169ea0cae4cae990d80217ede5f146afdb0f17d9123f4284d65ce25999`。

| 通道 | 历史帧 | 实际 F0 长度 | 独立检查 |
| --- | --- | --- | --- |
| 1540 / IRQ131 | 13 | 8,587,458 B | 11,409,840样本零差异，编码前完整源快照 |
| 153c / IRQ130 | 14 | 8,587,390 B | 11,409,840样本零差异，编码前完整源快照 |

153c自身完成一次编码及一次原生 W1C，最终 pending=0。两个通道的配置、
实际输出分配与来源分别在 START 前核对。重复观察器没有失败、没有未闭合
配对，清理无错误。311项运行来源、程序、输入和二进制核对未变，进程已停止。

实际卡内只有一张 NEF，8,917,186 B，SHA256：
`6cb614c144c01f0044f949b1e4cebaf17fb5e1c86ab74315040347b11315ca97`。
其 F0 strip、原生压缩头、编码前 RAW 源和 LibRaw 全样本一致，三张附图
（4128×2752、1620×1080、160×120）均可解码。

第二路 JPEG 在 `40c0d2b8 → write32 15e21004=1` 处严格停止，第二张文件
尚未保存完成。没有放宽此守卫。第一张来自 FULL 排队前767,235,812 ns，
因此完整多张保存、原生 writer 生命周期和 PRE0.3s 窗口仍未验收。

私有收据位于 `.local/analysis/raw-precapture-20261012/half-history/`：

- `raw-history-63-audit.json`：运行身份与停止检查。
- `raw-history-63-multinef-audit.json`：原卡及独立完整源快照的逐文件检查。
- Linux原运行中的 `raw59-context.json`、`producer63-evidence.json`、
  `f059-evidence.json`、`repeat63-observations.json` 保存具体作业证据。

## 当前源码冻结与验证

合并准备版二进制 SHA256：
`19eec6f8ff6e9fda11e6c5cf11e160285b11046c2ffb4c6f229bcb7e48fd40e1`。
冻结文件名：`qemu-system-aarch64-merge64-19eec6f8`。
源码快照：`half-history/half64-model-sources/`。
构建、测试和额外文件哈希：`.local/merge64/build-identity.json`。

| 验证 | 结果 |
| --- | --- |
| 双编码器，含独立 job/历史 ACK | 正常15项、优化15项通过 |
| 原1540历史 F0回归 | 19项通过 |
| 改名后的普通 F0/lineage | 7项通过 |
| C1重用，含录像 header误用反例 | 15项通过 |
| journal C核心/适配器 | 4项通过；核心12个场景，各验证 O0 / O2+DNDEBUG |

RAW63使用的是前一冻结二进制。之后的所有权接线位置、独立作业/ACK元数据、
journal ABI和测试改名已完成上述回归，**合并准备版尚未再次跑原固件**。
这两个证据层分开保存在构建清单中。

另有 `repeat64_observer.py` 的暂停 CPU/新增断点冲突拒绝副本，离线14项
检查通过，未用于RAW63；旧观察器保持原样，不能把该副本当作新原生验证。
