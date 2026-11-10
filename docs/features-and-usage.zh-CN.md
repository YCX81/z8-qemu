# Z8 QEMU 功能与使用指南

更新日期：2026-11-11。默认分支为 `main`，11 月 Live/Wave5 进展已合并到 `experiments/20261111/`；原临时进展分支已删除。合并保留了实验源码，默认 `tools/build.py` 仍使用根目录 `qemu/` 模型，不会自动应用实验目录下的补丁。

## 当前提供什么

| 功能 | 使用入口 | 已记录状态与限制 |
|---|---|---|
| Z8 C3.11 辅助启动、原厂菜单、模拟取景 | `Start-Z8.ps1`、`Test-Z8.ps1`；底层 `tools/z8_run.py` | 有菜单往返与独立像素检查记录；依赖 MCU 对端、合成 Bayer 和显式资源，非真实 CMOS/ISP 还原 |
| C32/C64 优化校准槽位 | `tools/z8_slots_acceptance.py --slots 32/64` | 照片/视频槽位的导入、选择、NAND 保存及新进程恢复有历史验收；默认启动器不启用扩容 |
| 普通拍照与 NEF 导出 | `tools/z8_capture_probe.py` 及[公开 C12 配方](z8-capture-probe.md) | 单张 packed14 输出有逐像素核对；非 RAW 历史帧预拍 |
| F0 lossless14 与逐照片审计 | `profiles/z8-lossless14-properties.json`、`tools/z8_photo_audit.py` | 默认关闭；单张无损 NEF 有像素记录，不能视为完整 native writer/连拍生命周期通过 |
| writer / NVMe / producer 诊断 | writer 观察器、NVMe 完成跟踪、producer 代次日志 | 用于关联文件、传输与像素来源；NVMe 完成不单独证明相机介质持久化，64 条日志也不是 64 帧缓存 |
| Wave5 与 Live/audio 模型源码 | `experiments/20261111/wave5/` | 保存 prepared overlay、Wave5 及 merged patch，独立于默认模型；需匹配原实验基线和资源 |
| CFexpress 热插拔诊断 | `experiments/20261111/card-hotplug/` | 卡在位/link 属性、菜单/NP3/换卡 harness、事件/IRQ/MMIO/断电观察器源码；不等于真机热插拔保证 |
| EXMCU 与主电源/唤醒研究 | `experiments/20261111/exmcu/` | EX 原始 MCU 驱动路线、host-wake 改动与 runner；需私有 EX 输入及配套完整研究环境 |
| RAW 预拍、HE/HEIF、视频、方向、AF、E7 等专题 | README 的[公开分支列表](../README.zh-CN.md#公开分支) | 仍在各自主题分支，不能把归档进 main 理解为这些分支都已集成或完成验收 |

Live 的拍照开关、音频短片、机内 LVP 回放、图标和 C64 标签属于配套[Fenestra 功能指南](https://github.com/YCX81/fenestra-z8/blob/z8-c311/docs/features-and-usage.zh-CN.md)，不是本仓库默认模拟器新增的相机菜单。Open Gate、完整 RAW 预拍、云创在线服务、完整胶片模拟和实体设备还原没有列为已完成功能。

上表归纳的是源码与历史记录。详细证据日期、输入、计数和未覆盖项仍以 [README](../README.zh-CN.md)、[TODO](TODO.md) 和对应专题说明为准；本次文档更新未重新运行固件或实体相机。

## 准备构建环境

使用 Linux 或 WSL、Python 3.12+。安装 GCC/Clang、Ninja、patch、pkg-config，以及 GLib、Pixman、zlib、libfdt、libjpeg 开发头文件；拍摄实验还需 exfatprogs 等[构建依赖](build.md)。将构建目录和大介质放到 Linux 文件系统，避免 Windows 挂载目录的性能问题。

```sh
git clone --branch main https://github.com/YCX81/z8-qemu.git
cd z8-qemu
python3 -m venv "$HOME/.local/share/nikon-z8-qemu/venv"
. "$HOME/.local/share/nikon-z8-qemu/venv/bin/activate"
python -m pip install -r requirements.txt
python -m pip install -r requirements-capture.txt
python -m pip install -r requirements-c10.txt
python tools/build.py --work-dir "$HOME/.local/share/nikon-z8-qemu/work" --jobs 2
python tools/make_cmos.py
```

构建器固定 QEMU 10.2.4，校验源码归档身份；可用 `--archive` 提供已下载归档。它不会下载 Nikon 固件。`make_cmos.py` 生成合成输入，不是真实传感器数据。

无需固件的测试入口：

```sh
sh tests/run.sh
python tests/check_z8_machine.py \
  --qemu "$HOME/.local/share/nikon-z8-qemu/work/build/qemu-system-aarch64"
```

这些测试验证模型/工具契约，不能替代运行固件或验收像素。构建结果的 SHA-256、配置与实验源码修订应一起保留。

## 提供私有固件输入

将自行准备的 MAIN 放到 `.local/firmware/z8-c311-main.bin`。公开源码不包含升级容器、解密材料或 MAIN：

| 输入 | 精确身份 |
|---|---|
| 原厂容器 | `Z_8_0311.bin`，99,051,117 字节；SHA-256 `d67413c2ede6b9ce8887175f20700bd9fbad2a973a4820968c45503d15d80d96` |
| MAIN | 81,807,709 字节；SHA-256 `2b43a31c9de838f84b12416de4348a7004e65be07f853ec56ec2b10f3a8e3853` |

原厂容器不能直接代替 MAIN，其他版本需要独立 profile。准备方法见[固件说明](firmware.md)。槽位等派生实验要使用另行标识的输出，保留原输入。任何派生 MAIN 都不是本项目提供的相机更新文件。

## 启动菜单与模拟取景

Windows 的 PowerShell 包装脚本选择 WSL `Ubuntu`，使用上面的环境。在仓库根目录执行：

```powershell
.\Test-Z8.ps1
.\Test-Z8.ps1 -BootstrapFixture -BusWindowFixture -SerialFixture -NandFixture
.\Start-Z8.ps1
```

`Test-Z8.ps1` 是有界严格诊断；默认 fixture 关闭，遇到未知硬件访问停机。第二条显式启用假设资源，不能把其成功写成裸模型启动成功。

`Start-Z8.ps1` 的交互入口为 `http://127.0.0.1:8788/`。等待模拟取景出现后，用 MENU 进入菜单，半按返回取景；按网页“停止仿真”或该终端 Ctrl+C 结束自己的进程。需要菜单/取景诊断时使用：

```powershell
.\Start-Z8.ps1 -Headless -Seconds 360 -MenuTest -RequireLiveView
```

核对运行目录中的 `result.json`、错误原因、菜单往返及像素检查，不能只看退出码。Linux 用户从 `tools/z8_run.py --help` 开始，结合[启动与资源说明](z8-status.md)选择显式 profile；旧 `tools/run.py` / `boot.py` 是 Z9 参考入口。

## 槽位扩容、拍照和 F0

这些是独立实验，不由 `Start-Z8.ps1` 一并开启。

- **C32/C64：** 安装 `requirements-c10.txt`，按[C32/C64 四阶段配方](z8-c32.md)使用 `tools/z8_slots_acceptance.py`。依次执行 photo create、photo restore、video restore、video-cold；选择 64 时改为 `--slots 64`，使用独立新介质及输出目录。不要将 C32 介质直接作为 C64 介质；没有无损迁移工具。
- **普通 NEF：** 使用[拍摄诊断与 C12 配方](z8-capture-probe.md)，准备匹配的固件、合成 RAW、虚拟卡与已构建 QEMU。检查导出文件、LibRaw 像素与实际 producer RAM；文件出现或 NVMe 有完成记录不足以判定通过。
- **F0：** 按[逐照片审计](z8-photo-audit.md)显式选择 lossless14 与输出所有权模型。已有历史结果是 `pixels_verified`，不应改写为 `native_accepted`，也不提供 HE/HE* 微码执行。
- **RAW 预拍：** 仍要求至少两张不同的触发前保留帧及一张拍后帧，闭合时间、队列、缓冲寿命和文件集合证据。普通拍照、Live 短片或重复预览帧都不能替代该验收。

## 使用归档的 Wave5、热插拔和 EXMCU 源码

先阅读[归档来源与边界](progress-20261111.md)。实验源码已在 main，但没有自动进入默认构建。不要在唯一的工作树上将所有 patch 顺序叠加；先恢复原实验对应的 prepared QEMU 树，在独立副本检查上下文。

| 材料 | 目标与准备要求 |
|---|---|
| `wave5/source/` | 历史 prepared source overlay；保留相关 C/头文件与 prepare 脚本。它本身不包含原实验全部工具、profile、资源和运行介质 |
| `wave5/wave5.patch`、`wave5-prepared.patch` | Wave5 版本的原始/准备后变体；不是要求连续应用两次的 patch 系列 |
| `wave5/merged-prepared.patch` | 另一份 merged 基线的 prepared patch；路径以 QEMU 源码根为准，需看文件头确认目标 |
| `card-hotplug/cfexpress-hotplug.patch` | 目标是匹配模型的 `hw/arm/nikon-z9-lab.c`；缺乏上下文时停止，不能强行应用到默认 overlay |
| `exmcu/exmcu-port.patch` | 历史 `cardhp/qemu-10.2.4/` → `exmcu/qemu-10.2.4/` 的树差异，含较广的 Kconfig 变动 |
| `exmcu/host-wake.patch` | 单个 `nikon-z9-lab.c` 的增量；不能当作仓库根级补丁直接应用 |

例如，**仅当已恢复相同 cardhp 基线**时，在该 QEMU 源码根目录做不修改文件的上下文检查：

```sh
patch --dry-run -p1 < /absolute/path/to/z8-qemu/experiments/20261111/card-hotplug/cfexpress-hotplug.patch
```

此检查只说明上下文是否匹配，不证明编译或运行成功。EXMCU 树差异使用不同路径层级；先人工核对 `---` / `+++` 头，不能复用上面的 `-p1`。

`card-hotplug/run_pull.py` 提供 `imenu`、`np3`、`keyprobe`、`idle`、`cardswap` 方案，参数定义在其 `--help`。它仍依赖原研究快照、Live driver、卡片工具及部分未发布 helper；含 `/path/to/user` 的路径需配置。`--unlock` / `--garbage` 是主动 guest 状态注入，不能标为被动观察。观察器 C 源码需按匹配 QEMU plugin ABI 单独编译。

`exmcu/native_run.py` 提供 boot/standby 等实验计划，除 `--main`、`--qemu`、`--card-from` 外还依赖原 `live-regress/snap`、bundle recipe、合成输入、私有 EX 镜像和完整模型。EX 输入路径仍在脚本中定义，没有通用 EX 自动下载/提取命令。缺少这些依赖时，克隆仓库并不能直接运行。故这里提供源码入口和准备条件，不将历史 runner 包装成一键功能。

## 日常更新与问题定位

```sh
git switch main
git pull --ff-only
python -B tools/audit_publication.py
```

审计检查精确文件清单、内容及当前历史中的 blob，不能代替来源、许可或硬件验证。记录提交、MAIN 与 QEMU 哈希、模型属性、动作顺序、虚拟时间、首个拒绝以及相关数字；把合成模型假设与实际观察分开。不要上传固件、EX、转储、原始运行 JSON 或私人素材。详细边界见[发布说明](publication.md)和[贡献约定](../CONTRIBUTING.md)。
