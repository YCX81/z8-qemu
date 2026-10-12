# Nikon Z8 固件仿真研究

基于 [WonderLantern/z9-qemu](https://github.com/WonderLantern/z9-qemu) 的 Z8 C3.11 移植工作区，上游基线为 `27498735e3ed202052dfc6306ded6b867af70a58`。

用户功能目标、当前进度及底层依赖见 [功能 TODO](docs/TODO.md)。

**Z8 原厂英文菜单与模拟 RAW 取景已跑通，完成三次 MENU 往返与五次独立像素校验。** 修复了 NAND/存储初始化边界、MCU 绑定和缺失参数引起的初始化长循环及电池保护误触发，原始 MAIN 未修改。新机器名为 `nikon-z8-bringup`，原 `nikon-z9-lab` 保留。Z8 专用入口是 `tools/z8_run.py`；旧 `tools/run.py`、`tools/boot.py` 和原有 Z9 文档仅作参考。

## 当前证据

工具安装、REA Evidence、验证结果与剩余工作详见 [实施记录](docs/z8-status.md)。

最终有界验收：RAW 1743 帧、LCD 2547 帧，其中 731 帧包含取景；未知 MMIO 为 0。
五次独立解码的平均 RGB 误差为 0.149–0.151（8-bit 像素单位）。这验证功能性虚拟外设和模拟 Bayer 输入，不代表实机时序、真实 CMOS 或拍照写卡已实现。

2026-10-11，独立 QEMU 10.2.4 构建与原始 Z8 C3.11 的第一轮严格模式记录（当前完整进展见实施记录）：

| 情景 | 观察 | 停止点 |
| --- | --- | --- |
| 默认，可选 fixture 关闭 | 原固件进行 DMA 填充、代码搬运并释放 CPU1/2 | CPU0，PC `0x41187158`，读取 `0x0d150000` |
| 显式 `--bootstrap-fixture` | 使用继承的实验初始化值，继续配置外设 | CPU2，PC `0x41185c00`，读取 `0x14154040`，8 字节 |
| 再加 `--bus-window-fixture` | Z8 的 17 组软件寄存器布局已比对；窗口初值仍为实验假设 | CPU1，PC `0x40c14704`，写入 `0x0e70073c`，通道 7 启用 |
| 再加 `--serial-fixture` | 接受串行/PDMA 请求、继续搬运代码及 NAND reset | CPU0，PC `0x40c5e398`，读取 `0x17800118`，NAND 状态 |

四个情景均保持原载荷不变，正常关闭自己的模拟器。默认模式没有 guest RAM 补丁、指令替换或宿主函数调用。CPU/GIC、部分内存与基础外设继承 Z9 模型，属于执行假设，不是已测量的 Z8 硬件规格。

`z8_bringup.py` 的 `--bootstrap-fixture` 仅启用固定的 `bootstrap-fixtures=on`，包含未实测的 SoC revision/strap 等初始化状态。总线选项要求同时开启 bootstrap；串行选项要求前两者。该诊断入口不会注入串行回包。`z8_run.py` 另行明确启用实验外设、Z8 绑定的 MCU peer 和有来源的虚拟资源，保持未知访问停机。

## 固件

严格身份见 [profiles/z8-c311.json](profiles/z8-c311.json)。

| 对象 | 身份 |
| --- | --- |
| 原厂包 | `Z_8_0311.bin`，99,051,117 字节 |
| 包 SHA-256 | `d67413c2ede6b9ce8887175f20700bd9fbad2a973a4820968c45503d15d80d96` |
| EG 模块 | `eg2070_0311b0.bi`，body offset 581844，含 CRC 长度 81,807,711 |
| MAIN | 移除末尾两字节 CRC，81,807,709 字节 |
| MAIN SHA-256 | `2b43a31c9de838f84b12416de4348a7004e65be07f853ec56ec2b10f3a8e3853` |
| 当前 load/entry | `0x40160000`，源于 Z9 映射假设，已用于上述有界执行 |

升级包来自 [Nikon 官方下载页](https://downloadcenter.nikonimglib.com/en/download/fw/606.html)。本次已实际运行独立安装的 `nikon-fw-key-tool`，从 Z8 3.11、Z7II 1.70、Z6 3.80、Z50 2.60 四个原厂包恢复全部 XOR 表，17 个模块 CRC 与 4 个包 CRC 全部通过；重新提取的 Z8 MAIN 与首次执行载荷逐字节相同。

固件、密钥、派生 ELF、Ghidra 输出及运行日志均留在忽略的 `.local/`，不属于公开源码。升级容器不能直接代替 MAIN，其他版本也不能只修改哈希就宣称兼容。

## 本机运行

使用 WSL 的 **Ubuntu** 和独立的 Z8 Python/构建缓存。三个 PowerShell 入口现已包含在仓库根目录，自动读取当前 Ubuntu 用户的 home 路径。完成下面的环境准备并自行提供指定 MAIN 后，从仓库根目录运行：

```powershell
.\Test-Z8.ps1
.\Test-Z8.ps1 -BootstrapFixture
.\Test-Z8.ps1 -BootstrapFixture -BusWindowFixture -SerialFixture
.\Test-Z8.ps1 -BootstrapFixture -BusWindowFixture -SerialFixture -NandFixture
.\Start-Z8.ps1
```

`Test-Z8.ps1` 是有界严格模式诊断；`Start-Z8.ps1` 启动本机 `http://127.0.0.1:8788/` 控制器，显示 QEMU 原厂帧缓冲并接受 MENU/半按快门。初次取景初始化需要等待；可用 MENU 进入菜单，半按返回取景，点击“停止仿真”或在启动终端按 Ctrl+C 清理自身进程。

完整验收命令为 `Start-Z8.ps1 -Headless -Seconds 360 -MenuTest -RequireLiveView`，未完成三轮往返或独立像素校验即失败。完整运行输出使用 WSL Linux 磁盘，诊断仍输出 `.local/runs/`。Linux 严格诊断等价命令：

```bash
python tools/z8_bringup.py \
  --firmware .local/firmware/z8-c311-main.bin \
  --qemu "$HOME/.local/share/nikon-z8-qemu/work/build/qemu-system-aarch64" \
  --output .local/runs/my-new-run --seconds 5
```

运行器拒绝覆盖已有证据。退出码 0 表示诊断与清理完成；相机状态由 `result.json` 的 `stop_reason`、`menu_verified` 明确给出。

## 构建与测试

需要 Linux、Python 3.12+、C 工具链及 [构建文档](docs/build.md) 列出的 QEMU 依赖。以下命令从 WSL 中的仓库目录执行，路径与 PowerShell 入口一致：

```bash
python3 -m venv "$HOME/.local/share/nikon-z8-qemu/venv"
. "$HOME/.local/share/nikon-z8-qemu/venv/bin/activate"
python -m pip install -r requirements.txt
python tools/build.py --work-dir "$HOME/.local/share/nikon-z8-qemu/work" --jobs 8
python tools/make_cmos.py
sh tests/run.sh
python tests/check_z8_machine.py --qemu "$HOME/.local/share/nikon-z8-qemu/work/build/qemu-system-aarch64"
```

构建器固定 QEMU 10.2.4 并校验归档 SHA-256；已有归档通过 `--archive` 指定。新增测试覆盖身份锁、输出保护、QMP/进程生命周期及分析 ELF 的独立解析。合成机器测试验证注册和严格停机，不代表相机菜单验收。

## REA 分析

本次已安装并实际使用 REA 6.3.0、Ghidra 12.1.4 和 OpenJDK 21，通过 WSL CLI 成功分析了 Z8 的 revision 读取函数与总线查询函数。仓库内的 `REA.ps1` 适配已安装在 Ubuntu 用户目录下的后端；它不安装或捆绑 REA/Ghidra。命令行可用与 Codex MCP 注册是不同状态；本项目没有修改全局 Codex 配置。

REA 6.3.0 的 Linux Ghidra 后端接受 AArch64 ELF，不能直接把 Nikon 裸载荷作为 ELF 导入。[tools/z8_elf.py](tools/z8_elf.py) 因此为明确选定的代码范围生成**静态分析容器**和来源记录，原字节保持不变，不声称生成了原厂 ELF。

例如，第一次停止处的完整函数：

```bash
python tools/z8_elf.py .local/firmware/z8-c311-main.bin \
  .local/analysis/z8-revision.analysis.elf \
  --offset 0x1027154 --length 0x34 \
  --base 0x41187154 --entry 0x41187154 \
  --provenance .local/analysis/z8-revision.provenance.json
rea function /absolute/path/z8-revision.analysis.elf 0x41187154 \
  --provider ghidra --json
```

切片外引用和真实内存权限未包含在容器中。REA CLI 每条命令创建独立会话；后续可用 `rea mcp` 保持分析会话，但 CLI 成功不证明 MCP 已连接。

## 当前边界

NAND 采用空白虚拟存储；MCU、资源与启动检查点已按 Z8 C3.11 单独绑定。菜单和模拟取景已验收，但物理时序、机身校准、完整外围控制器、AF、拍照写卡和录像尚未验证。后续扩展应继续基于各自原厂路径与独立验收，不把本次图像输出扩称为完整相机仿真。

保留上游 GPL-2.0-or-later 许可与作者信息。模拟器不会连接或刷写实体相机。
