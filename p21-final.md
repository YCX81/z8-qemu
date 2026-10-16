P21 已完成虚拟完成屏障移植、固定配方测量和失败现场补证；**第二张 HIF 仍未产出**。

构建 `p21-vc-01` rc=0，QEMU SHA256 `f42872446a0cd0e52d90e1b3b0b491fae70ca698225840fba040af01271897f5`。新属性 `z8-still-hevc-virtual-completion-model` 默认关闭、realize 后不可变；要求 x265 和 icount/QTest，`physical_latency_verified=false`。关闭模式 codec JSON 与旧二进制逐字相同。已执行：753 项测试 OK（skip102）；虚拟完成9例、依赖守卫2例、只读原码观察8例，普通与 `-O` 均通过。首次mask夹具与新观察接入的红→绿记录均保留。

同配方、同二进制、同sh锁，先off3次、后on3次：两组均为1508失败2/3、第二D980帧1/3、首张16020eb4失败0/3。开启组9个作业全部 scheduled_ns==committed_ns；这六次样本未显示恢复失败减少。额外两次只读诊断单列，不并入失败率。

失败诊断已抓齐同次因果：source1 active→stopped，global1 mask允许40000；global0先ACK再置OS40，但global1 bit18 setter未发生。原OS AND等待40040在148569296虚拟ns后返回 **-50**，与401882f8定时回调的原始返回槽写入一致，随后固件发布 `[9,13,0x100]` 并向2620追加40。当前模型仅实现global0停止生产者；本轮没有补IRQ、扩展W1C或清pending。

153的+dc已证明是本帧分配地址低位（写者40c288ec）。但+104及44个程序地址字也变，另9字位于生成器外尾部。当前缺分配生命周期、本帧就绪与程序有效长度的完整合同；不能只忽略+dc或把所有RAM地址都视为合法。153守卫保持不变，后续需分别审阅source1停止事件与153动态缓冲合同。

下表rc为原生探针退出码：UNKNOWN对应153严格停止；其余rc1为恢复门超时。所有运行owned_qemu_stopped/input_unchanged均true、卡fsck0。八份首张HIF均128065B，独立全像素解码及同次NAL/lane哈希核对通过；不是第二张成功。

|运行名|屏障|锁|rc|UNKNOWN|1508等待|第二帧|首张HIF SHA256|
|---|---|---|---:|---:|---|---|---|
|heif-hlg-20261015-21-off-01|off|sh|1|0|失败 0x40040|无|1d31f2a94a4cd9e52a9be6f579f3c549b727e9556800823b0909427e5aae81e6|
|heif-hlg-20261015-21-off-02|off|sh|1|1|成功 0x40|有，153严格停|1951f7a01628aa178ed8f080906bb1d210f016643bcff92f18e6085a4b054dd8|
|heif-hlg-20261015-21-off-03|off|sh|1|0|失败 0x40040|无|2b8ee55a6f941a1bb0a4ad0da1a536c2978b138f2ea1c7e20cb5ee6e2a9796f6|
|heif-hlg-20261015-21-on-01|on|sh|1|1|成功 0x40|有，153严格停|216d0e779b10d5444bef04f9b5b1b2ae4f5db4864a99f854efb2d89d33ac3889|
|heif-hlg-20261015-21-on-02|on|sh|1|0|失败 0x40040|无|541c6a292f4bb07e5ee11b9c00db07226817c9f0a3503c97032dcb3ac78b04ae|
|heif-hlg-20261015-21-on-03|on|sh|1|0|失败 0x40040|无|ff5068d70ef4fef52b593d539ff71b10454e53b5ff1186117005b419cbd5d7cf|
|heif-hlg-20261015-21-on-diag-01|on|sh|1|1|成功 0x40；OS 0|有，153严格停|e7e434276b74c03a34df8659e2109d819e381aba31416c8c4152d75985020c6a|
|heif-hlg-20261015-21-on-diag-02|on|sh|1|0|失败 0x40040；OS -50|无|d0ceee970932e747fdb54d182a5811926a85123d78a4d70a8c4001f23bf46656|

交付：[文档第26节](docs/z8-heif-1602.md)；P21完整收据（本地 `.local/heif/p21/report.json`）；HM5独立delta（本地 `.local/heif/p21/hm5-virtual-completion.patch`）；只读诊断delta（本地 `.local/heif/p21/stop-raw-observer.patch`）；实际命令（本地 `.local/heif/p21/commands.log`）。

所有本人启动的QEMU和审计任务已结束。无需协调者代跑本轮命令；仍需协调者审阅上述两个后续合同。MAIN、Wave5、旧证据和集成树未改，未提交、未推送。
