# E7 神经网络研究检查点

[English](README.md)

本分支整理 Z8 C3.11 / Z9 C5.32 研究中自写的格式解析器、候选 CPU 前向路径、
量化算术和有界后处理测试。它是独立研究源码检查点，不是相机功能或刷机包，
不能据此认定 E7 加速器数值等价、相机输入链或 AF 性能已经验收。

## E7-bird 的对应关系

引用 [WonderLantern/e7-bird-network](https://github.com/WonderLantern/e7-bird-network/tree/8ced43b19edb845baef39a80c45d40be0b49efba)，
固定提交及 `core.py` 哈希保存在 `external-reference.json`。本地通用 PCNN 回放
使用单独提供的该项目算术模块；没有把其源码、权重、夹具或二进制重新打包。
其 ZR 有限输入实测属于上游自己的证据，不能转述为本项目的 Z8 硬件验证。
将 744 算术移用于其他资源、以及实验性资源的解读仍保留候选假设。

源码入口、依赖和命令见 [英文说明](README.md)。主要内容为：

- `parse_models.py`：固定 MAIN 身份下的动物/车辆 CDNN 静态描述恢复。
- `pcnn_inventory.py`、`pcnn_packets.py`、`pcnn_replay.py`：资源身份、严格包解析及候选回放。
- `neupro_forward.py`：动物整数前向候选，显式保留舍入、激活和缩放等未验证策略。
- `animal_channels.py`：动物通道/筛选重建，不含最终整数裁切和输出记录打包。

公开合同测试不需要固件或厂商 SDK；可选 Torch 和私有输入测试会明确跳过。
私有测试只从显式环境变量读取原始 MAIN、符号清单、资源清单和外部 E7-bird
目录，不自动下载资产。新输出必须位于检出目录外或忽略的 `.local` 中，拒绝覆盖。

[当前状态](docs/status.md)、[模型基准](docs/model-benchmark-20261022.zh-CN.txt)和
[同图模型比较](docs/peer-benchmark-20261022.zh-CN.txt)分别说明证据层、测试规模、
语义/ROI/阈值边界及未在本次发布中重跑的历史结果。两种模型序列化表示不能
直接累计成独立识别模型数量；CPU 候选解释器耗时也不能当作 E7 芯片帧率。

厂商后端驱动、参数导出链及专用环境仍属于本地实验组件；本公开子集没有冒称
完整、无私有输入即可复现的 SDK 管线。原始快照和发布适配哈希见
`source-provenance.json`。固件、模型权重、照片/激活、SDK、许可证及原始设备
会话缓冲均未分发。
