# Z8 参数来源与验收边界

更新日期：2026-10-13。`profiles/z8-model-properties.json` 是 Z8 运行器的独立功能参数配置，按完整 MAIN SHA-256、长度、机型和版本绑定 C3.11。`tools/model-properties.json` 保留给共享 Z9 运行器。独立配置没有替换共享外设实现，也没有将继承的参数自动升级为 Z8 硬件事实。

本次迁移保持原有 65 项功能参数的值和顺序，以及 Z8 运行器附加的空 SD 卡配置。`tools/z8_properties.py` 校验配置身份、参数类别覆盖和诊断输入边界；`z8_run.py`、`z8_guest.py`、`z8_c10_acceptance.py` 均通过它读取 Z8 配置。MCU 地址仍由 Z8 bindings 单独核验，不借用 Z9 的固件身份。

| 类别 | 来源与当前边界 |
| --- | --- |
| 启动与外围 fixture | 继承共享模型的合成初始化、NAND、串行、I²C、PCIe、ADC 和方向输入；不是工厂校准或真实设备内容 |
| 总线窗口 | Z8 软件描述符的 17 组布局已比对；bootloader 初值和物理映射仍是假设 |
| 服务核事件 | 共享 SEV 与事件处理；CPU3 阻塞策略和独占监视器事件对 Z8 的适用性待查 |
| 虚拟事件时钟 | 内部 `33333333 ns` 是合成周期，与外部诊断输入的 `33 ms` 分开记录 |
| 显式数值输入 | ADC、服务状态、零结果和元数据等值为实验输入，不是实测值 |
| 图形与扫描输出 | 使用共享实现；独立解码实现检查扫描输出一致性，未与真机图像对照；菜单哈希仅为回归基线 |
| Z8 空 SD 卡 | 表达无卡状态；不证明真实卡导入、NEF 写卡或真实云创文件兼容性 |
| 其他共享寄存器与处理模型 | 支持功能级执行的假设；不证明完整硅片行为、AF、拍照或编码链路 |

JSON 中每一个功能参数都归入且仅归入一个类别，各类分别记录 `source`、`assumptions` 和 `physical_hardware_verified:false`。这些类别是当前实现的来源清单，不是参数逐项物理验收清单。资源附件中的电池采样与阈值另见 [Z8 实施记录](z8-status.md)及[电池资源来源](z8-battery-resources.md)。

外部 `BoundaryPeer` 的参数是 `period_ms=33`，表示 QEMU 虚拟时间下的诊断投递间隔，不是 33 fps。它保留共享类的丢弃错过周期、禁止补发突发等行为，不是测得的 CMOS 时钟，也不证明 RAW 预拍速度或 Z8 物理时序。运行结果单独记录 `z8_diagnostic_boundary`。

拍摄阶段追加的时序1200、slot15/slot8、内部FIFO和模拟像素输入，统一见[实验假设与反例](z8-experiment-assumptions.md)。清单区分程序实际请求、外部数值选择及模型行为，尚未做的移除对照明确留空。

## 身份与分层证据

公开 `profiles/z8-c311.json` 不再包含 `entry_prefix_hex` 原始入口字节。加载仍必须同时满足 MAIN 完整长度 `81807709` 和完整 SHA-256；缺少或无效身份字段、长度不符、同长度内容不同都拒绝。没有新增身份绕过参数，也没有修改输入 MAIN。

| 验收层级 | 当前记录方式 |
| --- | --- |
| 裸模型完整启动 | `bare_model_full_boot_verified:false`；旧 `full_boot_verified` 保留 false |
| 外部 MCU 辅助的原厂菜单 | 静态 profile 记载已有本地验收，依据为 [Z8 实施记录](z8-status.md)；每次新运行仍从 false 开始，仅按当次菜单结果更新 |
| 完整相机功能 | `complete_camera_verified:false`；菜单、扫描输出或单项导入通过都不能代替此验收 |
| 实体硬件 | `physical_hardware_verified:false`；没有刷机或连接实体相机 |

`verification_scope` 将上述四层分开。旧 profile 的 `menu_verified:false` 只保留为裸启动范围的历史兼容字段；当前运行的 `menu_verified` 表示当次辅助菜单证据。单元测试和模拟传输测试不产生新的完整 QEMU 或物理硬件验收。

删除字段只改变新提交的文件内容，**不会清除旧 Git 提交中的入口字节**；历史残留需要单独记录和决定如何处理，本次不重写历史或强制推送。发布脚本通过仅说明它所实现的检查通过，不等于人工来源审查或法律结论。
