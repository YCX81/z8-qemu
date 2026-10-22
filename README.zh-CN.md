# Nikon Z8 固件仿真研究

[English](README.md)

文档更新：**2026-10-19**。本工作区基于 [WonderLantern/z9-qemu](https://github.com/WonderLantern/z9-qemu) 开展 Nikon Z8 C3.11 移植，上游起点为 `27498735e3ed202052dfc6306ded6b867af70a58`。共享 Z9 参考工具保留，Z8 的固件身份、MCU 绑定、资源和验收基线独立定义。

**辅助启动下的原厂菜单、模拟取景及普通触发后 NEF（packed14 与 F0 无损各一张）已有本地 QEMU 验收记录；RAW 预拍摄仍未完成。** 本项目是实验性模拟器，不是可安装到相机的固件，不会连接或刷写实体相机。

以下状态于2026-10-19按 main `a140c42`（之后的提交只改文档）及其文档中已记录的结果统一核对。本次文档更新没有重跑固件、验证当前二进制或测试实体硬件；历史结果只适用于记录的输入、源码与二进制。详细研究文档多数为中文，完整进度见[功能 TODO](docs/TODO.md)与[实施记录](docs/z8-status.md)。

## 当前成果与边界

| 项目 | 已记录证据 | 剩余边界 |
|---|---|---|
| 菜单与模拟取景 | 三轮 MENU → 半按返回取景，五次独立解码扫描输出检查。历史运行 RAW 1,743 帧、LCD 2,547 帧，其中 731 帧含取景；未知 MMIO 为零。 | 依赖外部 MCU 对端与显式虚拟资源。菜单哈希仅作回归，未与真机图像对照；裸模型完整启动未验。 |
| C32/C64 优化校准槽位 | 照片、视频各32槽及各64槽分别通过四阶段验收：导入、原生选择、guest NAND 保存与新进程恢复。 | 显式实验使用派生 MAIN 和主动原厂函数调用。真实尼康云创文件、最终色彩输出及实体相机未验；64是已测试容量，不是硬件上限。 |
| 普通 NEF | C11/C12 导出一张 80,790,528 字节 NEF。LibRaw 解码全部 45,705,600 个 RAW 样本，与实际 producer RAM 零差异；完整 packed 字节一致，三个内嵌 JPEG 可解码。C12 使用公开 CLI 配方。 | 仅一帧触发后周期流，单帧传感器模型请求仍为零。这张 NEF 是带行填充的 packed14 拷贝，不是压缩。不代表预拍，也不证明真实传感器或 ISP 还原。 |
| 逐照片审计与 F0 lossless14（2026-10-14） | 候选 QEMU `d39e1fd5…` 上，原厂 C3.11 单张拍摄写出卡内 NEF 35,111,661 字节（`d211e239…`）。LibRaw 完整解码 45,705,600 个样本，与 151 producer 快照零差异。F0 strip 为 34,339,053 字节，排除了 80,017,920 字节的 packed fallback；预测器元数据经 exiftool 独立核对。三个内嵌 JPEG 可解码。 | 需显式开启：`engine1540-lossless14-model` 与 `output-buffer-ownership-model` 默认关闭，运行使用 `profiles/z8-lossless14-properties.json`。编码器取自上游、保留署名，是功能性 F0 格式；不执行尼康压缩微码，也不含 HE/HE*。审核结论为 `pixels_verified`，不是 `native_accepted`。JPEG 作业字节匹配、多照片界面恢复、完整原生 writer 生命周期、历史 RAW 及实体设备未验。详见[逐照片审计](docs/z8-photo-audit.md)。 |
| 原厂 writer 关联 | C16 将原文件对象、ID/路径、九次写入、最终长度、cleanup、返回及结果回写关联至导出 NEF；文件范围有 NVMe 写入完成覆盖。 | C16 使用私有观察器；公开观察器接入有合成测试，尚无等价公开运行验收。完整缓冲寿命、原生分配代次及实体介质持久化未验。 |
| RAW 预拍研究 | HALF20在显式资源与wide控制模型下进入原C120/PRE画面、getter4及state2/handler3。 | D980/RAW输出为零，没有可验收历史RAW/NEF。释放期间因诊断器跨域同时命中停止，未出现原固件异常或未知MMIO。HALF21仅准备中。 |
| producer 代次记录 | 默认关闭，记录实际 D980→151 NV16/RAW→1540 RAM 输出的完整 SHA-256、虚拟时间、模型写入代次和父帧关系。已记录七项无固件真实 QTest 与八项辅助测试通过。 | 64条元数据不是64帧像素缓存。原 latest-source 限制保留；CPU/其它 DMA 写入、原生所有权和历史保留未验。 |

数值收据：[槽位验收](docs/verification/slots-20261012.json)、[普通 NEF / C12 / C16](docs/verification/raw-single-20261012.json)。NVMe 完成不能单独证明 NEF 验收或介质持久化；文件导出、解码与 RAM 比对是独立检查。

RAW 预拍验收至少需要**两帧不同的触发前保留帧和一帧拍后帧**，触发前帧的DMA及入队须早于所有启用路线的最早FULL请求，拍后帧DMA须晚于最晚的已验证原厂FULL消费者，全部使用同一虚拟钟，并闭合队列、缓冲寿命、文件集合与完整像素证据。增加预览计数或导出重复帧不足以验收。详见[运行对照](docs/z8-raw-runtime.md)与[多帧审核器](docs/z8-precapture-audit.md)。

Open Gate 视频、AF 增强、尼康云创在线接入、完整胶片模拟、照片 Log、RED Log3G10/R3D NE 和机内全景仍未实现或未验收，详见 [TODO](docs/TODO.md)。原厂视频 N-Log 不计为项目新增功能，main 上的录像仿真仍未验收。

## 公开分支

上表只描述 `main`。主题分支保存尚未合并的实验和检查点，其结果属于各自分支，不属于 main，各分支保留自己未完成的验收边界。各分支的源码回归结果见[发布进度](docs/publication-progress-20261015.md)。

| 分支 | 用途 |
|---|---|
| [`main`](https://github.com/YCX81/z8-qemu/tree/main) | 已审核的基线：Z8 启动、菜单与取景运行、C32/C64 槽位、普通 NEF 拍摄、逐照片审计与 F0，以及保留的 Z9 参考工具 |
| [`codex/z8-integration`](https://github.com/YCX81/z8-qemu/tree/codex/z8-integration) | RAW、录像与方向的合并集成实验 |
| [`codex/raw-precapture-shared146`](https://github.com/YCX81/z8-qemu/tree/codex/raw-precapture-shared146) | 预拍用的 RAW 历史帧传输，以及 1540/153c 双编码器 |
| [`codex/z8-he-history-20261017`](https://github.com/YCX81/z8-qemu/tree/codex/z8-he-history-20261017) | 第三方 HE 预拍钩子的生命周期测试、真实 HE 样本回放，以及发现的原生模型缺口 |
| [`codex/z8-r4-lifecycle`](https://github.com/YCX81/z8-qemu/tree/codex/z8-r4-lifecycle) | 扩展槽位 NP3 预检、取景槽号提示与原生卡片输入回退实验；复制页扩展默认关闭 |
| [`codex/z8-heif-1602`](https://github.com/YCX81/z8-qemu/tree/codex/z8-heif-1602) | 经 1602 静态照片编码器的 HLG/HEIF 照片管线，以及有界恢复探针 |
| [`codex/z8-video-c1`](https://github.com/YCX81/z8-qemu/tree/codex/z8-video-c1) | C1 视频传输、编码与 MCU 实验 |
| [`codex/z8-orientation`](https://github.com/YCX81/z8-qemu/tree/codex/z8-orientation) | 录像方向与视频数据路径实验 |
| [`codex/z8-live-photo`](https://github.com/YCX81/z8-qemu/tree/codex/z8-live-photo) | 仅宿主侧的 Live 图帧保留模型 |
| [`codex/z8-live-photo-video`](https://github.com/YCX81/z8-qemu/tree/codex/z8-live-photo-video) | Live 图的视频输入与配对审核研究 |
| [`codex/z8-af-lockon`](https://github.com/YCX81/z8-qemu/tree/codex/z8-af-lockon) | AF 资源追踪与遮挡响应探针 |
| [`archive/z8-raw-precapture-20261013`](https://github.com/YCX81/z8-qemu/tree/archive/z8-raw-precapture-20261013) | 归档：早期 RAW 预拍传输实验 |
| [`archive/z8-internal-stream-20261012`](https://github.com/YCX81/z8-qemu/tree/archive/z8-internal-stream-20261012) | 归档：早期内部流记录，为溯源保留 |
| [`archive/z8-capture-evidence-20261012`](https://github.com/YCX81/z8-qemu/tree/archive/z8-capture-evidence-20261012) | 归档：早期拍摄证据记录，为溯源保留 |
| [`archive/z8-merge-rehearsal-20261014`](https://github.com/YCX81/z8-qemu/tree/archive/z8-merge-rehearsal-20261014) | 归档：未解决冲突的合并演练，有意保持不可构建 |

## 固件输入

Z8 启动入口为 `tools/z8_run.py`，机器名为 `nikon-z8-bringup`。旧 `tools/run.py`、`tools/boot.py` 与 `nikon-z9-lab` 保留作 Z9 参考。

| 输入 | 固定身份 |
|---|---|
| 原厂升级容器 | `Z_8_0311.bin`，99,051,117 字节 |
| 容器 SHA-256 | `d67413c2ede6b9ce8887175f20700bd9fbad2a973a4820968c45503d15d80d96` |
| EG 模块 | `eg2070_0311b0.bi`，body offset 581844；含 CRC 为 81,807,711 字节 |
| MAIN | 移除末尾两字节 CRC 后为 81,807,709 字节 |
| MAIN SHA-256 | `2b43a31c9de838f84b12416de4348a7004e65be07f853ec56ec2b10f3a8e3853` |
| load / entry | `0x40160000`，继承的映射假设，已用于有界执行 |

Windows 包装脚本要求用户自行将兼容 MAIN 放在 `.local/firmware/z8-c311-main.bin`。升级容器不能代替 MAIN；其他版本需要独立绑定和验证。详见[身份配置](profiles/z8-c311.json)和[固件准备](docs/firmware.md)。

原始输入只读。槽位扩容与格式政策实验使用单独标识的派生程序，不能将结果记为未修改固件的默认启动成果。固件、密钥、派生镜像、内存快照、截图和运行材料留在本地。详见[发布边界与已知历史残留](docs/publication.md)。

## 派生固件实验

槽位生成器公开了可复用的A64补丁配方，并不只是模拟器参数。它生成另行标识的本地MAIN，区别于原程序启动。派生MAIN、owned-region镜像和补丁收据可能含有从用户固件读取的原内容，属于本地实验材料，**不是相机更新文件，不得写入相机**；未提供重打包或实体设备验证。

当前树仍有源自原程序的小段指令守卫和测试literal，移除旧入口前缀并没有清除全部此类内容。详见[发布分类与待审项](docs/publication.md)、[来源及复现技术复核](docs/z8-public-review-20261013.md)、[实验输入与反例清单](docs/z8-experiment-assumptions.md)。清单审计通过不等于来源或法律审查通过。

## 构建与测试

使用 Linux / WSL、Python 3.12+ 及[构建说明](docs/build.md)所列依赖：GCC/Clang、Ninja、patch、pkg-config，以及 GLib、Pixman、zlib、libfdt、libjpeg 开发头文件。拍摄实验另需 exfatprogs 和 `requirements-capture.txt`；槽位实验使用 `requirements-c10.txt`。

在 Linux / WSL 的仓库目录执行：

```bash
python3 -m venv "$HOME/.local/share/nikon-z8-qemu/venv"
. "$HOME/.local/share/nikon-z8-qemu/venv/bin/activate"
python -m pip install -r requirements.txt
python -m pip install -r requirements-capture.txt
python tools/build.py --work-dir "$HOME/.local/share/nikon-z8-qemu/work" --jobs 2
python tools/make_cmos.py
sh tests/run.sh
python tests/check_z8_machine.py --qemu "$HOME/.local/share/nikon-z8-qemu/work/build/qemu-system-aarch64"
```

构建器固定 QEMU 10.2.4 并校验归档 SHA-256，已有归档可用 `--archive` 指定。构建和长时间运行优先使用 Linux 文件系统。合成测试和无固件 QTest 验证模型边界，不替代原厂固件或实体相机验收；显式模型测试见对应功能文档。

## 本机菜单与取景运行

PowerShell 包装脚本当前选择 WSL **Ubuntu**，自动读取对应用户的 home，并使用上述环境。从仓库根目录运行：

```powershell
.\Test-Z8.ps1
.\Test-Z8.ps1 -BootstrapFixture -BusWindowFixture -SerialFixture -NandFixture
.\Start-Z8.ps1
.\Start-Z8.ps1 -Headless -Seconds 360 -MenuTest -RequireLiveView
```

`Test-Z8.ps1` 为有界严格启动诊断，默认 fixture 关闭，未知硬件访问停机。bootstrap、bus、serial、NAND 等可选 fixture 是显式实验假设，不能证明裸模型完整启动。

`Start-Z8.ps1` 在 `http://127.0.0.1:8788/` 显示 QEMU 原厂帧缓冲，通过 MCU 路径接受 MENU / 半按输入。初次取景需等待；MENU 进入菜单，半按返回取景。网页“停止仿真”或启动终端 Ctrl+C 清理自身进程。输出使用 WSL Linux 磁盘上的新目录。headless 命令要求三轮菜单往返和独立像素检查；诊断退出码零本身不代表相机验收，请核对 `result.json`。

这个包装脚本不启用槽位扩容，也不提供完整 RAW 预拍验收。普通 NEF 使用独立[拍摄诊断驱动与公开 C12 配方](docs/z8-capture-probe.md)，须满足其输入、虚拟卡、菜单与审核前提。

## 研究文档

- [功能 TODO](docs/TODO.md)、[实施历史](docs/z8-status.md)、[工程进展](docs/research-progress.md)、[参数来源](docs/z8-parameters.md)。
- [C10](docs/z8-c10.md)、[C32/C64 槽位](docs/z8-c32.md)、[持久化介质](docs/z8-persistent-media.md)。
- [RAW 运行对照](docs/z8-raw-runtime.md)、[显式拍摄资源](docs/z8-capture-resources.md)、[拍摄证据](docs/z8-capture-evidence.md)、[writer 观察器](docs/z8-writer-observer.md)、[NVMe 取证](docs/z8-nvme-evidence.md)。
- [内部 FIFO 启动](docs/z8-d980-internal.md)、[F04F 停止](docs/z8-primary-f04f-stop.md)、[C0 停止](docs/z8-1543-c0-stop.md)、[帧代次记录](docs/z8-producer-journal.md)、[多帧审核](docs/z8-precapture-audit.md)。
- [逐照片审计、输出所有权与 F0](docs/z8-photo-audit.md)、[主题分支发布进度](docs/publication-progress-20261015.md)。

REA/Ghidra 使用从本地固件切片制作、明确标注的分析 ELF 容器，不是原厂 Nikon ELF。`REA.ps1` 调用已安装的 WSL 后端；历史 CLI 使用不代表全局 Codex MCP 注册。详见[实施记录](docs/z8-status.md)。

继承的 CPU/GIC、内存窗口、虚拟时序及模拟 Bayer/ISP/JPEG 路径属于功能研究模型。真实 CMOS、机身校准、完整外设协议、尼康成像处理和物理时序未验。`BoundaryPeer` 的33是诊断间隔毫秒数，不是测得的相机 FPS。

## 引用的仓库与来源

main 使用：

- [WonderLantern/z9-qemu](https://github.com/WonderLantern/z9-qemu)：上游分叉起点（`27498735…`），提供共享模型和 Z9 参考工具。F0 lossless14 编码器 `qemu/nikon-expeed-lossless.h` 取自该上游，保留 RdWing 版权与 GPL 声明。
- [QEMU](https://www.qemu.org/) 10.2.4：构建器固定版本并校验 SHA-256，保留上游许可。
- [LibRaw/LibRaw](https://github.com/LibRaw/LibRaw)：F0 编码器的格式参考（`nikon_load_raw`），也是审计经 rawpy 使用的独立 NEF 解码器。
- [WonderLantern/nikon-fw-key-tool](https://github.com/WonderLantern/nikon-fw-key-tool)：解密并校验本地升级容器（固定提交 `33426e8…`）。

主题分支另外引用：

- [WonderLantern/zr-qemu](https://github.com/WonderLantern/zr-qemu) `f9cc5d1`：输入配对与视频处理模型由此改编（视频 C1、方向、集成）。
- [WonderLantern/wonder-lantern-z30](https://github.com/WonderLantern/wonder-lantern-z30) 的 `video-rot`：录制开始时锁定视频方向的设计参考（方向、集成、视频 C1）。
- [XinmiaoZhao/NikonFirmwarePatch](https://github.com/XinmiaoZhao/NikonFirmwarePatch) v0.1.0 工具包与 [Ivan-mato/nikon-z8-halfpress-raw-precapture](https://github.com/Ivan-mato/nikon-z8-halfpress-raw-precapture) `205fd05`：被测试或复核其代码与样本的第三方预拍项目。[raw.pixls.us](https://raw.pixls.us/) 第 6616 号记录提供真实的 Z8 HE 样本（he-history）。
- [videolan/x265](https://github.com/videolan/x265) 用于 Main10 静态照片编码，[strukturag/libheif](https://github.com/strukturag/libheif) 作容器规则参考，[exiftool/exiftool](https://github.com/exiftool/exiftool) 作标签定义参考（HEIF）。
- [LimitPoint/LivePhoto](https://github.com/LimitPoint/LivePhoto)、[minutechreview/livephoto-maker](https://github.com/minutechreview/livephoto-maker) 与 ExifTool：Live 图配对的参考（Live 图视频）。

分支固定了提交或版本时，其文档会记录下来。引用链接不授予对应项目材料或尼康固件的任何权利。

保留上游 GPL-2.0-or-later 许可与作者信息，详见 [NOTICE](NOTICE.md) 与[贡献约定](CONTRIBUTING.md)。贡献需记录机型/固件、合成输入、命令、结构化结果和未覆盖范围。
