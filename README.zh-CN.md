# Z9 上游参考文档

**当前工作区的 Z8 状态、入口、固件身份与限制见 [README.md](README.md)。下面保留的是 Z9 上游说明，不能作为 Z8 已完成这些功能的证据。**

[English](README.md) · [构建](docs/build.md) · [固件输入](docs/firmware.md) · [原理](docs/architecture.md) · [测试边界](docs/testing.md)

本项目提供 QEMU 10.2.4 的实验性 Z9 C 5.32 板级模型、极简本地网页控制器，
以及 Bayer → 功能性 ISP → 固件提交的缓冲区 → LCD 的图像路径。

**固件由用户自行提供。** 仓库不包含尼康固件、解密后的私有程序、密钥、机身转储、
字体、图标位图、校准数据、私人照片或原厂界面截图，也不会自动下载尼康固件。
这是 WonderLantern 独立开展的研究与学习项目，与尼康没有隶属或背书关系。

独立启动器已通过三次原生菜单往返、RAW/LCD 持续产帧及四次独立像素校验；此前
相同功能配置的三次冷启动也通过。具体证据见 [测试说明](docs/testing.md)。本地模型
能够运行原厂英文菜单、拍摄界面和 RAW 图像路径；ISO、快门、黑色描边及红色无卡
图标来自固件。网页仅展示 QEMU LCD，不自行绘制相机界面。

## 使用

在 Linux 或明确指定的 WSL 环境中运行。此项目只使用 `ubuntu-noble-nikon`，
不会调用或修改作业专用的 `ubuntu-noble`。构建依赖及安装命令见英文首页。

```sh
python tools/build.py --work-dir "$HOME/wonderlantern-z9-build" --jobs 2
python tools/make_cmos.py
python tools/run.py --firmware /绝对路径/z9-main-payload.bin
```

打开 `http://127.0.0.1:8789/`。等待原厂画面与 RAW/LCD 计数出现后操作。
MENU 进入菜单，Half shutter hold 半按返回拍摄，Release 释放半按。Ctrl+C 退出。
需要自己的图片时，先运行 `python tools/make_cmos.py --image /路径/图片.jpg`。

唯一支持的 MAIN 文件为 81,823,629 字节，SHA-256：

```text
6380ded18395ce11cb327c766721274c85127d2cf1a1e887ee60e3d156810bdd
```

请勿把升级包直接当 MAIN。仓库不提供提取服务或密钥。固件始终以只读输入打开。
运行生成的数据和截图留在本地，不能连同代码上传。

## 必须理解的边界

- 从 MAIN 入口启动，采用推断的内存布局、空存储模型和显式外设输入，并非真实上电。
- 部分常驻资源缺失。模型提供少量明确标注的虚拟 ROM 输入，参数默认值由用户固件
  在本机生成；这些不是恢复出的工厂校准数据。
- GPIO/MCU 服务使用硬件断点协调和外部事件输入。没有通过修改 PC、任务结果、
  OS 标志或看门狗让界面跳过流程。
- 输入示例为 640×480、12-bit RGGB/u16；这只是实验格式，不是真实 Z9 CMOS 参数。
- ISP 使用通用插值和颜色转换。看到 ISO 数字不等于真实自动曝光已实现。
- 服务核使用有限的 WFE/SEV 等待模型，配合指令时钟和虚拟时间外部输入；这不是
  完整 ARM 事件实现或实测硬件时序。恢复运行请冷启动，整机热复位尚未支持。
- 尚不承诺 AF/PDAF、机械时序、写卡拍照、录像、完整 GPU 或真实 CMOS 协议。
- 本项目不访问 USB 相机、不刷机，也不是可安装进相机的软件。Z7 和 ZR 独立推进。

[研究笔记](docs/research-notes.md)记录可跨机型复用的方法；寄存器地址、事件位和
固件对象布局必须按机型、版本重新建立证据，不能只因共用 EXPEED 便直接套用。
