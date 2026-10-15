# 逐照片审计、输出所有权与 F0 lossless14

2026-10-14。迁移参照 WonderLantern/z9-qemu `c6dbfc4`；仅迁入逐照片约束、输出所有权规则和有署名的 F0 encoder，没有整体覆盖共享C模型。现有Z8机型、journal、内部wide控制与用户修改保留。

## 逐照片审计

`z8_capture_probe.py --capture-pixels` 现在为每个实际151 RAW job保存独立的 `photo-sequence.json`。长度来自累计RAW字节差值并与当帧完整几何匹配；拒绝跳号、重复、倒退、源/目标重叠与变化中的设备状态。原快照收据保留。151 ACK同时含NV16工作，不能假设其计数必然等于RAW job。

照片输入仍是既有单张诊断入口；此迁移没有实现连续连拍或自动多照片菜单驱动。审核器可检查同一记录内1至8个实际照片输出，缺失任一快照或NEF时失败，不以目录顺序猜测native job。

停止的原MAIN运行先导出：

```sh
python tools/z8_export_nefs.py --run RUN --firmware MAIN --output RUN/nef-export
python tools/z8_photo_audit.py --manifest RUN/photo-audit-input.json --output RUN/photo-audit-result.json
```

输入JSON：

```json
{
  "schema": "z8-photo-audit-v1",
  "run": {"path": "result.json", "sha256": "actual result file SHA256"},
  "export": {"path": "nef-export/export-manifest.json", "sha256": "actual export receipt SHA256"},
  "codec": "nikon-f0-lossless14",
  "photos": [{"job": 1, "file_id": "export-000001"}]
}
```

`photos`必须给出全部独立job与文件的明确映射。审核器核对原停止记录、固定MAIN、导出收据哈希、全部卡范围、每个NEF哈希/容器、producer快照及独立LibRaw完整样本。`codec`省略时只审计原packed14兼容传输；指定F0时额外检查原RAW IFD、Compression34713、strip长度不落入LibRaw的packed fallback，以及exiftool读出的F0/four2048预测器元数据。需要已有rawpy与exiftool；不自动安装。

通过状态为 `pixels_verified`，明确保留 `native_accepted:false`。它不单独证明native writer/job归属、JPEG job匹配、菜单/取景恢复、原生allocator寿命或预拍。错配像素会失败；相同输入像素本身不足以证明native身份，须另关联writer与回收日志。注入测试decoder只返回 `synthetic_checks_passed`，CLI不能注入。

## 输出所有权

`output-buffer-ownership-model=on` 默认关闭，实例化后不可修改。模型为实际151的Y、UV、可选RAW输出和1540输出登记有界span、job及输入frame；完整DMA完成后才标为complete并交付既有IRQ。原151 bit1 W1C、1540 bit8 W1C退休相应输出；关闭mask、无关W1C不释放。原已接受的151 reset或失败DMA取消151所有权；未推断新的1540 reset协议。

D980、151、1540的已接入写入不得覆盖未退休输出，跨设备覆盖在DMA前被严格拒绝。只读 `output-buffer-ownership-state` 区分reserved/complete/ACK/cancel、span与模型代次。默认关闭维持旧路径。没有新增宿主ACK、软件任务flag、成功回调或PC写入。

这属于**仿真输出到中断退休的生命周期**，不是原厂历史队列、allocator/引用计数或整机RAM写入监控。CPU和其它DMA写入仍未覆盖；latest-source限制保留，没有选择旧帧或制造内部Bayer像素。

## F0编码

阶段1回归与实际QTest通过后才接入 `engine1540-lossless14-model=on`。默认关闭，实例化后不可修改；要求原RAW输出路径及上述所有权模型开启。共享encoder原样取自上游 `qemu/nikon-expeed-lossless.h`，SHA256 `6f6ef0da9d01571dcf3470ccf1b160fb1c50c5859b6b70ef78d63b4bc97e9100`，保留RdWing版权与GPL声明。

只消费实际、已确认的151 packed14 RAM；F0/four2048是明确的功能格式profile，最终NEF元数据须独立核对。原已验证的完整RAW目标保留区是硬容量，不能假设任意图像都能压缩进同样容量。编码失败不进行任何部分DMA、不给完成IRQ或有效长度。成功后逐字节readback，原结果寄存器只提供实际编码长度，累计bytes也累加实际输出长度。journal用独立的 `nikon-f0-lossless14` 格式与实际span，不能标成packed14拷贝。

显式入口为 `--capture-properties profiles/z8-lossless14-properties.json`，沿用[普通RAW对照](z8-capture-resources.md)的其余参数；默认配方没有改变。相机程序、取景、卡文件及解码仍须在同次构建中分别验证。没有执行尼康压缩微码、HE/HE*，也不代表实体相机功能。

## 验证入口

```sh
sh tests/run.sh
python tests/test_z8_buffer_ownership.py --qemu QEMU --output FRESH_OWNER_DIR
python tests/test_z8_lossless14.py --qemu QEMU --output FRESH_F0_DIR
python tests/test_z8_producer_journal.py --qemu QEMU --output FRESH_JOURNAL_DIR
```

单元反例覆盖错配/漏照片、源/卡变化、累计长度、缓冲重叠、ABA、部分完成、错误ACK和容量不足。C helper使用显式检查，在 `-O2 -DNDEBUG` 下仍有效。真实QTest执行零固件CPU的实际RAM、结果寄存器与IRQ/W1C；独立F0 reader逐样本检查，不把QTest说成NEF/原固件验收。

本地阶段收据存于 `.local/migration-20261014/`；阶段1与阶段2构建指纹分开。固件、像素、虚拟卡、截图和原始日志都不纳入公开清单。没有提交、推送、发布或刷机。

## 本次已执行结果

- 阶段1：621项Python回归，88项按原条件跳过；新审计模块语句/分支合并覆盖87%。所有权6组实际QTest通过，默认off/on完整像素、状态及journal等价；原journal7组通过。
- 阶段2最终：`sh tests/run.sh` 的626项Python回归（88项跳过）及C像素、sensor-tag检查通过；优化模式8项逐照片反例通过。新三个审计模块合并覆盖87%。最终候选另通过所有权6组、journal7组、lossless3组和原wide52组实际QTest，以及无固件注册smoke。
- 原Z8 C3.11单张：候选QEMU SHA256 `d39e1fd529f2f8af31784b14e6fbdf1ce03070914d730807b10b135560e79aa0`；独占运行正常结束且QEMU停止，观察到writer返回0及新RAW工作空闲。
- 卡内实际NEF为35,111,661字节，SHA256 `d211e23939779a61bf5689e9ae7a5dfe09e4ac5b0a917e485a0c5f7ee3c3dfbc`。LibRaw0.22.1/rawpy0.27.1完整解码45,705,600样本，与本次151 producer快照零差异。实际F0 strip为34,339,053字节，排除了80,017,920字节packed fallback，预测器元数据通过独立exiftool核对。
- 三个内嵌JPEG实际可解码：160×120、8256×5504、1620×1080；未保存逐JPEG encoder输出，因此没有授予JPEG job字节匹配验收。
- 原固件运行锁定当时的host/probe源码哈希；随后共享ACK空闲门控的一处计数修正单独以反例和全仓回归验证。QEMU源码/二进制与本次NEF没有变动，未将该host修正描述为另一次固件运行。

这些证据验证一轮原固件单张F0保存与全幅像素完整性。多照片UI恢复、完整native writer生命周期、历史RAW、实体设备仍未验收。依赖缺失的失败审核收据及测试修正前的失败记录保留，不覆写为通过。
