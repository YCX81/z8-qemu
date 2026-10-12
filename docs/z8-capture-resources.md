# Z8 拍摄路径的两项外部 ROM 实验输入

这两个选项只提供固定的缺失外部资源假设，默认关闭。它们不恢复原厂校准，不改变原 MAIN、实时 RAM、RAW/JPEG 政策或任务状态，也不证明 NEF 或 RAW 预拍成功。既有运行背景见[运行对照](z8-raw-runtime.md)，诊断入口见[拍摄驱动](z8-capture-probe.md)。

| 显式选项 | 唯一新增 ROM 范围 | 合成值 | 本次运行的适用条件 |
|---|---|---|---|
| `--capture-timing-input` | `0x47f63022` 起2字节 | 小端1200 | 原 mode0、variant1、selector1、subselector3 |
| `--hsfc-processing-input` | `0x47f5af4f` 起1字节 | 1，选择record1 | 原 processing mode14 经原表选择slot15 |

可将所需选项加到 `z8_capture_probe.py` 命令中。`--hsfc-processing-input` 不选择C15、C30或预拍窗口；必须另经原 MCU/UI 进入目标模式。它也不会把高速JPEG政策变成RAW。两个新单元与既有启动资源、selector2和原程序映射均不重叠。

创建输入前再次检查原 Z8 C3.11 MAIN 的81807709字节长度与固定SHA-256，并确认 `-kernel` 指向同一已验证文件。只允许在自有QEMU启动前，以新文件和原有 `loader` 设备提供资源；不接受自定义地址、任意数值、派生MAIN替换或运行时写入。记录包括原输入身份、生成器源哈希、目标地址/长度/值、文件哈希和适用条件。资源自身与原厂数据的关系始终标为合成假设。

`result.json` 的 `capture_resource_inputs` 将 `supplied` 与 `actual_lookup_verified` 分开。文件已创建、程序启动或没有报错，都不能让后者自动通过。无关mode仅记为 `not_applicable`；未命中目标路径的输入保持 `unobserved`。观察是每个接点首次命中的有界采样，不提供完整调用历史。

目标路径的检查只读取已暂停CPU的原寄存器和有界内存：

- 时序输入核对 mode/variant/selector/subselector、原资源描述符与全局基址、原地址公式及ROM和加载后RAM中的1200；原 `LDRH` 后的W20也必须为1200，才记为本次原读取已观察。
- HSFC输入核对 mode14的原表值15、原资源基址、slot15的ROM/RAM值及原 `LDRB` 地址；读取后W14必须为1。另检查原减1运算后的record1地址，仍不把全零或缺失的record内容当作有效校准。
- 适用目标的模式选择、指针、值或原读取结果不符，保存失败观察并停止；不通过改寄存器、补中断或更改任务状态来继续。

原私有对照曾分别观察到这两个资源单元解除各自的上游阻点；不能把那次成功自动带入新运行。公开接入的16项无固件测试覆盖默认关闭、固定身份、独占文件、无重叠、原读取前后区别、错误地址/索引/值、无关模式及probe接入。该helper的Python语句/分支覆盖率为100%；公开组合运行仍须保留自己的动态收据。它们没有增加实体相机或刷机证据。

```sh
python -O -m unittest discover -s tests -p test_z8_capture_resources.py -v
```
