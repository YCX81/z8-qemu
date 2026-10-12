# Z8 RAW 预拍：下一项最小实验

> 历史快照：下文的“本轮未运行”和工具缺口描述的是此前静态研究阶段。此后C11普通NEF已独立解码及全像素比对通过，RAW历史帧完整链仍未完成。当前证据见[运行对照](z8-raw-runtime.md)、[拍摄诊断驱动](z8-capture-probe.md)和[任务进度](TODO.md)，下文保留原阶段的分析与设计。

日期：2026-10-12。本轮为只读复查和验证设计；没有运行新仿真、修改原MAIN或RAW门控。本轮没有重置或覆盖既有Z8工作区，也没有将Z9新增捕获实现迁入Z8。

## 当前可执行边界

- Z8 C3.11：原菜单/模拟取景可运行；`tools/z8_run.py` 没有完整快门/NEF/虚拟卡验收入口。C10实验NAND不是相机NEF写卡后端。
- Z9 `deb3a75df1795924e9be8f814d9a24ce6b439a67`：已有 `tools/capture.py --audit` 普通单张RAW链，使用原固件封装与NVMe/exFAT写入，独立解码全部RAW样本和三个内嵌JPEG。作者报告整机通过；不能把源码同步或合成测试当成本机复现，更不能当RAW预拍通过。
- 没有可直接把Z9命令中的MAIN替换成Z8而运行的完整捕获配方。固件指纹、事件/IRQ、工作对象、GPIO、物理RAM范围和审计几何均须绑定Z8。

上游源码固定参考：[capture.py](https://github.com/WonderLantern/z9-qemu/blob/deb3a75df1795924e9be8f814d9a24ce6b439a67/tools/capture.py)、[capture-profile.json](https://github.com/WonderLantern/z9-qemu/blob/deb3a75df1795924e9be8f814d9a24ce6b439a67/tools/capture-profile.json)、[audit_native_nef.py](https://github.com/WonderLantern/z9-qemu/blob/deb3a75df1795924e9be8f814d9a24ce6b439a67/tools/audit_native_nef.py)。

## 门控的精确含义

原函数 `0x40ede814` 在 `0x40edec74` 执行 `cmp w12,#0`，随后 `0x40edec78` 是 **`csinc w19,w21,wzr,eq`，不是条件分支**：

```text
result211a = (w12 == 0) ? candidate_w21 : 1
```

它消费的是由缓存、其他状态及标志共同算出的有效状态。属性 `0x06006200` 与Pixel Shift有关，不是一个已确认的通用RAW预拍开关；不能为了取得非零w12直接打开它。既有两条指令16种输入测试是历史指令证据，本轮没有重新执行，也没有提升为完整拍摄证据。

已证静态链：

```text
政策表 record+20，经 40f27220 间接调用 40ede814
  →40edec78 选择数值
  →40ed3e24 将 (211a,value) 放入待提交队列
  →[尚缺该次队列到SettingData实际commit证据]
211a →keytable a02a4bf28+138 →snapshot a03bb7070+138
  →40f28914/28 →info+3c8
  →40f6c0d8 →同一HSFC job+9e0
  →40f6f968 / 41059214 →metadata+294
  →41059014 RAW谓词允许0/1
```

`40f6c060 →40f79348` 的HSFC构造使用缓存标记1，不能假定每次都重新读取属性。值1获得RAW资格，也不自动提供后端所需的Bayer descriptor。详细证据见 `.local/analysis/raw-gate-20261012/findings.md` 及 [完整条件链](z8-raw-precapture-chain.md)。

## 下一项最小新增运行：同一job的门控与历史链观察

先增加一个**仅使用原MCU输入、只读观察**的Z8专用runner，沿用 `z8_boot.py` 和 `profiles/z8-c311-bindings.json`；不改格式值、RAM状态、PC或完成返回值。当前没有该runner，下面是实现契约，不是声称已有CLI。

在同一版本、同一输入下做普通RAW与原厂HSFC最短窗口两组对照。先通过实际菜单确认模式；不照搬Z9的QUAL拨轮次数。完整FULL触点及捕获GPIO的Z8消费位置需要补绑，不能只证明frame已送进MCU就算全按被应用。

| 观察点 | 最少记录 | 判定目的 |
|---|---|---|
| `40edec74/78`，`40ed3e24` | CPU、虚拟时刻、w12/w21/w19、key/value、队列指针 | 确认实际政策结果，而非只看属性6200 |
| `40f6c060/40f6c0d8`，`40f28914/28` | snapshot版本/内容、job地址、info地址、info+3c8 | 证明commit和缓存时序，区分旧快照 |
| `40ea048c`，`40f6f968`前后 | 实际job、metadata+294/+2fc、输入像素descriptor | 关联真实生产帧与历史对象 |
| `40f6fff0`，`40f6fe90`，`40f93098` | 入队/淘汰/提交序号、对象与buffer身份 | 记录原历史队列保留和提交顺序 |
| `40e9cb58`，`40eabd10` | 实际阶段矩阵、RAW条件、+3a0/+3f0及descriptor+30 | 确认RAW步骤真的收到有效输入 |
| `40eb17b0`，`40ea3638`，`406ce108` | 同帧输出项、段地址/长度、实际写入返回 | 把像素关联到NEF容器与文件写入 |
| `402df280`或`402dfca0`，`40f7bb18` | 块地址/长度/完成与buffer归还时刻 | 区分“writer被调用”与持久化、资源释放 |

若在未建模MMIO、CCOMP或像素生产处停止，交付该具体阻断点、最后完成事件及相关descriptor；不得用强制完成、直接调用writer或宿主造NEF跨过。即使只得到这一结果，也比修改CSINC后看菜单更有判别力。

## 写卡前提与Z9迁移差异

Z9新增可借的组件是全帧D980传输、151/153/1540处理完成模型、1518/1542 NV16支路、15e2 libjpeg后端、DesignWare/NVMe/exFAT及NEF审计。Z8必须先以自己的普通单张建立正对照；该阶段单独标记为“普通NEF”，不记作预拍。

- `z9-qemu/tools/capture_readiness.py` 的工作对象和GPIO表指向Z9 C5.32，并检查Z9指纹；必须重新解析Z8原表。
- `capture_sequence.py` 的consumer、捕获/ISR观察点及实际内存范围是Z9绑定；不能按固定差值批量替换。`capture_engine151_pixels.py` 当前仅有Z9/ZR字典。
- `audit_native_nef.py` 当前固定Z9模型和 `DSC_0001.NEF`，几何8280×5520；不能套在Z8或多张历史输出上。应从Z8实际descriptor确认尺寸、位深、pitch，并关联每张NEF与相应producer快照。
- 新Z9 `nikon-raw-pixels.h` 已扩至单维16384和64Mi样本；本地Z8仍为旧8192/16Mi限制。新上限只是宿主存储边界，不是相机读出模式证据。
- 新JPEG来自libjpeg模型；RAW为packed14功能传输。有效NEF不等于尼康专有压缩、ISP色彩或真实硬件时序已恢复。

## 预拍证明必须增加时变源

当前Z9 `statistics_load()` 只在初始化读取一次NZ9RAW1，`set_statistics_raw()` 在realize后拒绝改源；`qemu/nikon-z9-d980.h` 的打包缓存只随几何、pitch或格式改变。**反复产生相同源帧、延长半按或增加文件数量，均不足以证明触发前历史帧。** 在运行中改源文件也不会自动更新已加载像素。

最小最终用例目标为至少**两个触发前不同帧ID，加一个触发后ID**：

1. 新的外部传感器fixture按原DMA帧边界选择有限序列，每帧在CFA对齐的大块区域编码唯一ID；记录完整输入hash、实际DMA输出hash和虚拟时刻。它只能改变外部像素输入，不得事后改guest队列或NEF。
2. 每次源代际变化必须使D980打包缓存失效。统计输入、取景与捕获分辨率若共用源，也要明确代际关联，防止只有画面变了而RAW仍旧。
3. 以**原Z8 FULL触点被消费**的虚拟时刻T为分界，不能用宿主发送时刻或EXIF秒级时间。原DMA完成、入队、提交和写入均记录同一时钟；T之前DMA完成且进入历史队列的帧才算预拍候选。
4. 数量由实际保留/淘汰日志推导，不硬改原容量表为3。若原最短窗口产生更多帧，必须审计全部NEF，不能挑三张通过的文件。
5. 每个NEF须独立解码，并与其提交前保存的完整producer样本逐点对应；帧ID只是辅助识别。检查文件数量、无重复/遗漏、队列顺序及无buffer提前复用。

通过标准是 `N_pre >= 2`、`N_post >= 1`，且实际NEF集合与队列预测集合及顺序一致。拍后取景/菜单恢复、取消半按不落历史文件、重复半按清旧历史、慢卡/写失败资源回收应分别验证。虚拟时钟用于因果排序，不用于声称真机fps、缓存秒数或热余量。

## 现有命令：只用于上游普通NEF正对照

以下命令尚未在本轮执行。在待验证的 **z9-qemu checkout根目录** 运行，先按自己的环境设置变量，并提供Z9 C5.32 MAIN的**绝对路径**。需先具备 `libjpeg-dev`、`exfatprogs` 及 `requirements-capture.txt` 依赖；输入和输出路径必须全新，路径示例须自行替换。独立build目录避免覆盖Z8已验证的QEMU。`capture_support.py`会要求本checkout对应的构建manifest，不能直接拿旧Z8二进制替代。

```sh
Z9_PYTHON=/absolute/path/to/venv/bin/python
Z9_MAIN=/absolute/path/to/z9-c532-main.bin
Z9_BUILD_DIR=/absolute/path/to/new/build-deb3-raw-reference
Z9_RAW_INPUT=/absolute/path/to/new/raw-reference-deb3-001.raw
Z9_CAPTURE_OUTPUT=/absolute/path/to/new/capture-run-deb3-001
"$Z9_PYTHON" tools/build.py --work-dir "$Z9_BUILD_DIR" --jobs 2
"$Z9_PYTHON" tools/make_cmos.py --width 8256 --height 5504 --bits 14 --output "$Z9_RAW_INPUT"
"$Z9_PYTHON" tools/capture.py --firmware "$Z9_MAIN" \
  --raw "$Z9_RAW_INPUT" --output "$Z9_CAPTURE_OUTPUT" --audit
```

Z8门控trace、时变输入和多帧NEF审计尚无对应实现，不提供一个看似可执行但实际不存在的预拍命令。下一轮最小新增范围应限定为上述被动trace与第一个明确硬件阻断点，再按证据迁移捕获链。
