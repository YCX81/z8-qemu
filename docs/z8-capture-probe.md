# Z8 RAW 拍摄诊断驱动

2026年10月12日。当前提供的是有界诊断驱动，**尚未实现 Z8 历史帧到可解码 NEF 的完整链路**。后续C11已通过普通触发后NEF独立解码，见[运行对照](z8-raw-runtime.md)；程序正常退出、接点全部消费、格式门控或传感器帧本身仍不能代替文件验收。

本页保留C08及早期HALF-only的驱动基线。后续单表项时序修正、C15资源修正和独立NEF政策副本的正负对照见[运行进展](z8-raw-runtime.md)；旧卡点已消除与新卡点仍未完成分开记录。

## 已观察到的链路与卡点

| 阶段 | 实际 Z8 仿真证据 | 尚缺的证据 |
|---|---|---|
| 设置与触发 | 原 MCU/UI 选择 RAW + Lossless；53个接点均在原 consumer 和 return 匹配；格式门控为候选1、有效门控0、结果1 | 普通 RAW 的结果不能证明高速预拍已改为 RAW |
| CCOMP 握手 | 原 F3 命令应答、GPIO回调、任务转发、捕获处理及专用 flag8 参数路径已运行 | 不能将此握手直接当成传感器帧完成 |
| 拍摄调度 | 原命令函数真实返回至 `0x40f4a9d4`，走 B=1 路径；原次级任务确实按帧序号递减等待计数 | 等待计数异常大，尚未进入有效 RAW 后处理 |
| 传感器数据 | C05取得8280×5520、14位全幅数据；ID1及反码正确，仅预定1024个样本与未标记对照不同 | 此帧晚于全按，是周期源数据；单张输入请求仍为0，不能算历史帧 |
| 写卡 | 原固件已在虚拟卡建立目录，停止后完整目录和exFAT检查通过 | Z8 NEF数量为0；RAW151、JPEG后处理job仍为0 |
| 高速预拍设置 | 独立原UI实验选中C15、d3预拍窗口0.3秒，实际模式6、容量5 | HALF-only阶段进入原异常处理，历史队列55个暂停样本均为空；还未产生历史帧 |

普通拍摄 C08 的实测下界从 `91,027,582` 增至 `546,041,279`；原函数选出的最大延迟为 `455,013,599`。随后周期 `P=8366`、基准 `T=91,002,044`、偏移0生成计数 `N=54393`，初始剩余为54392。原字段 `acb0=65536` 参与这项计算。这里的调度数值与虚拟纳秒时间分别记录，不混用单位。

静态追踪显示，`acb0` 的上游字段来自原 `u16(input+0x128)+1`；尚需实际追到产生 `0xffff` 的算术或表读取分支。不能据此认定为硬件带宽不足、RAW编码器失败或需要强制中断。

C15 HALF-only 的后续直接异常采样得到：故障PC `0x41170d84`，FAR `0x2a156b2806`，ESR `0x96000005`。原寄存器的基址与索引精确相加得到FAR；此前模式索引的减1运算需要继续追溯。未映射该错误地址、改写故障指令或伪造完成。原始寄存器、截图、固件和卡文件只保存在本地。

## 运行普通 RAW 诊断

先按[构建说明](build.md)构建当前源码并安装[捕获依赖](../requirements-capture.txt)。使用自己的原始 Z8 C3.11 MAIN、合成 Bayer 输入，以及全新的输出目录。只有固定长度和SHA-256匹配的 MAIN 会被接受。

```sh
python tools/z8_capture_probe.py \
  --firmware /private/z8-c311-main.bin \
  --qemu /path/to/qemu-system-aarch64 \
  --raw /private/input14.raw \
  --output .local/raw-probe-01 \
  --seconds 600 --settle 10 \
  --capture-properties profiles/z8-capture-properties.json \
  --card --gpio --rtc-status --capture-selector \
  --tagged-input --capture-pixels --skip-cseq-repeat \
  --menu-actions menu,down,down,down,down,down,down,down,down,right,right,up,up,up,up,j,left,down,down,right,up,j
```

这条菜单路径依赖全新模拟器的当前默认菜单位置。驱动检查原解码后的44字节接点，但接点匹配不能代替实际屏幕复核。`j` 对应已验证的确认键；不接受未经验证的 `ok` 别名。

`--card` 仅新建普通文件虚拟卡。`--capture-properties` 显式启用共享外设模型假设。`--capture-selector` 向程序之外的缺失外部ROM输入 `0x47f5af42` 提供记录号1，这是合成资源假设，未恢复原厂系数。基础运行还使用明确标注的合成资源和MCU回复，不能称为完整实体硬件复现。

后续诊断可显式选择[两项资源输入](z8-capture-resources.md)：`--capture-timing-input` 只补索引1/sub3的时序系数，`--hsfc-processing-input` 只补处理模式14所用的索引槽15。它们默认关闭，不选择HSFC模式或改变格式政策；当前运行须经原读取守卫验证，不能仅凭选项存在声称修复成功。

`--gpio` 和 MCU 接点是两条输入路线。输出分别记录排队、GPIO电平、原MCU消费和原GPIO处理，不能把较晚的MCU时间自动当作唯一触发时间。所有运行事件使用同一虚拟时钟；静态固件内部时序字段另有类型和单位。

## 观察范围

驱动通过原函数边界读取寄存器和有界内存；不写PC、固件指令、任务标志、队列或计数。一般观察点每个接点只记录首次命中，日志明确标为不完整历史，不能用“没有记录”证明该事件没有发生。

默认CSEQ重复观察在原任务入口和下一条指令之间交替设断点，让原指令自行执行。它检查注册回调、CPU、SP及原调用者，遇到任务迁移、重入或超过4096次观察即失败。`--skip-cseq-repeat` 保留原绑定检查，改为每接点首次采样，用于避免无关周期事件耗尽观察容量；它不会修改固件任务或等待次数。

`--wait-capture-work` 要求新RAW/JPEG计数、观察到成功writer返回并持续两虚拟秒空闲；这只是进一步检查用的静止候选，**仍不代表 NEF、NVMe持久性或历史帧验收通过**。当前诊断命令不启用此开关，以保存未完成阶段的有界现场。

运行写出 `result.json`、`capture-trace.json`、局部像素收据和私有截图。超时或首个未知访问会保留失败原因与最终外设状态并停止本次进程；传输和清理可使主机时间预算略有超出。中途停止可在该运行目录创建 `stop.request`。

`z8_hsfc_controls.py` 只提供释放模式键与单步转轮接点，`mode_dial_sequence` 的末尾释放按键，并禁止不按模式键的转轮输入。独立仿真已从S验证+3为C15、+4为C30；它不是通用曝光拨轮接口，也不能证明已选中预拍窗口。完整C15 HALF-only驱动仍属本地诊断，尚未作为端到端预拍工具提供。

## 可选 NVMe 日志收集

在上述命令增加 `--nvme-trace` 可从控制器创建时启用 `pci_nvme_audit_*`；该开关默认关闭，且必须同时指定 `--card`。虚拟卡控制器使用固定设备名 `z8-recording-card`。需要包含[完成事件与只读末态补丁](z8-nvme-evidence.md)的 QEMU；缺少 trace 或 getter 会留下失败收据，不会自动退化为写卡成功。

驱动停止 CPU 后、清理 MCU peer 和 QEMU 之前，先读独立 `audit-state`，保存当时完整的 `stderr.log` 字节前缀并核对 SHA-256，再读一次独立末态。`nvme-evidence.json` 保留两份原始/解析末态、停止状态、前缀身份、原运行错误和收集错误；`nvme-stderr-prefix.log` 是该范围的原始日志。原 `stderr.log` 继续保留范围之后的清理事件，不能拿前缀替代整个进程日志。已有证据文件不会被覆盖。

两次末态必须相同；事件序号从1连续、请求生命周期编号与独立末态的最大值及总数均须吻合，虚拟时间不能倒退。即使日志正好在一条成功请求之后被截断，也会因为独立计数不符而失败。暂停 CPU 不保证异步块请求已经结束：收集器不排空队列、不重试到成功、不补完成。未完成请求（包括正常可能挂起的管理 AER）、失败状态、Abort/Reset/Delete 边界都保留。

`collection_complete` **仅表示这个暂停范围的日志完整性检查通过**。它不要求所有命令已完成；`all_commands_completed` 也仅指已有 CQ 交付，包括失败。两者都不能证明照片、NEF、历史帧、Flush 持久性或实体卡写入，`nef_accepted` 始终为 `false`。

本次接入的19项无固件契约测试覆盖启动开关、缺失/变化末态、完整请求边界截断、失败/挂起请求及清理顺序；收集模块的 Python 语句/分支覆盖率为100%。底层补丁此前的14项实际队列测试另见上文链接；还离线复核了10份真实QTest日志，保留失败及队列边界。

原Z8固件的独立菜单运行随后通过实际采集：4个接点及返回均匹配、无FULL，正常退出；72请求/259事件与两次独立QOM末态一致，其中10个管理命令、39次Read、19次Write、4次Flush全部交付CQ，无失败。原始前缀871113字节，完整stderr也保留。卡目录及exFAT检查正常，只有NIKON001.DSC和目录，**没有NEF**。本次原固件未发AER，真实挂起AER仍未覆盖，相关拒绝/保留契约仅有合成测试证据。MAIN、RAW、二进制及冻结驱动前后hash一致，拥有的QEMU已停止。

## 验证层级与下一步

驱动原39项契约测试继续通过；原C3.11按键解码器在Unicorn中覆盖4种受支持的模式接点，另有输入范围和按键释放检查。历史C08实际运行53个接点全部匹配，MAIN未变、拥有的QEMU已关闭；当时同源单测加该实际运行的Python覆盖率92%，不代表后续变更、固件或QEMU C代码的覆盖率。

```sh
python -O -m unittest discover -s tests -p test_z8_capture_probe.py -v
python -O -m unittest discover -s tests -p test_z8_nvme_evidence.py -v
python -O -m unittest discover -s tests -p test_z8_hsfc_controls.py -v
# 可选私有原解码器检查，需要Unicorn；不提供MAIN时会明确跳过。
Z8_MAIN=/private/z8-c311-main.bin python -O -m unittest discover -s tests -p test_z8_hsfc_controls.py -v
```

下一步须先解决普通拍摄的异常时序输入及C15的资源索引故障，然后取得真正历史队列和原NEF写卡。最终验收仍要求至少两帧触发前历史加一帧触发后数据，逐帧核对虚拟时间、FIFO顺序、RAW任务、writer、NVMe范围及LibRaw解码后的全部像素，见[多帧审核器](z8-precapture-audit.md)。目前没有刷机或实体相机验收。
