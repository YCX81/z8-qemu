# NVMe 写完成取证

这批补丁为普通 NVMe WRITE 增加被动取证，记录请求实际进入队列、后端提交、最终回调、内部完成入队和成功 CQE DMA 交付。它不改写命令、数据或完成结果，不新增完成事件或中断。标准 trace 默认关闭；需要时从进程启动启用 `-trace enable=pci_nvme_audit_*`，不能把原有校验前的 `pci_nvme_write` 当作写成功。

所有事件直接读取 `QEMU_CLOCK_VIRTUAL` 的纳秒值，与传感器 DMA 和原 FULL 消费使用同一虚拟时钟。控制器实例与单调请求 ID 关联 SQID、CQID、CID、NSID、真实后端字节范围及结果；CID 和请求槽复用不会复用生命周期 ID。后端完成与 CQE 已交付分别记录，CQ 满时不能提前声称交付成功。Flush 独立记录；失败、Abort、重置和删队列保留原结果及边界。当前可验收范围限于无 metadata、无 PI、非 zoned 的普通 WRITE。

只读 QOM 属性 `audit-state` 返回 `schema`、`instance`、`last_request_id`、`last_event_seq` 和 `virtual_ns`。请求和事件计数来自控制器自身，不从日志推算；读取不会生成事件或推进 I/O。采集器应先暂停 CPU，读取末态、保存完整日志前缀、再次读取末态，并要求两次相同；后端仍可能异步工作，状态变化时不能接受该边界。

测试中的 `completed_writes(rows, end_state)` 必须取得这份独立末态，核对实例、最后序号、从1连续分配的全部请求 ID、数量和虚拟时间，再检查每个请求的完整阶段。因此，即使日志恰好在成功 CQE 后截断，省略后续整个请求，也不能通过。保持完整原日志；`trace-scope.log` 是清理前日志的原始前缀，`trace-scope.json` 明示截止条件和末态。不能删除客体运行期间的失败、Abort 或 reset；测试结束后的独占进程清理日志也保留。

这只是有界运行的日志一致性检查，不是密码学证明。普通写成功也不等于介质掉电持久性、原固件已消费 CQE、文件已关闭或 NEF 可解码。接入实际捕获驱动和多帧审核器时仍须保留末态与范围约束，不能只导出成功事件。

## 验证与复现

2026-10-12 的本地构建 SHA-256 为 `aa12e927ddccd719a4d1f68430caa83ae245dfce446eca9b7929ad7651c408a8`。使用自编零内容 kernel、始终暂停的 CPU 和新建普通文件卡完成 **14项真实 QTest 检查**，包括两条 SQ/CID及槽位复用、非连续 PRP 的6144字节写入、实际卡字节与 CQE 对照、CQ背压、后端EIO、Flush失败、Abort、重置和队列重建、完整请求边界截断反例，以及 trace 开关的行为对照。全部测试进程已关闭。

**13项单元测试**在正常及 `python -O` 模式通过；合并单元和实际 QTest 后，Python测试/解析模块的分支覆盖统计为 **97%**，不是 QEMU C 源码覆盖率。trace 开关两组的实际 CQ 结果、卡文件和独立计数相同；这只覆盖已测试的工作负载。原始运行日志、介质和私有路径不分发，远程 CI 未在本批验证。

按[构建说明](build.md)构建后，可在 Linux 中显式运行：

```sh
python -O -m unittest discover -s tests -p test_z8_nvme_trace.py -v
python -O tests/test_z8_nvme_trace.py \
  --qemu /path/to/qemu-system-aarch64 \
  --output .local/nvme-qtest-01
```

输出目录必须全新，测试创建自己的普通文件卡和进程，不需要 Nikon 固件。这些验证属于底层虚拟卡与校验基础；**没有取得 Z8 有效 NEF 或 RAW 预拍端到端验收**，也没有刷机或实体相机验证。
