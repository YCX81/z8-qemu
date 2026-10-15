# Z8 C3.11 原生界面方向实验

2026-10-13 起，当前目标为[视频取景／录制信息层旋转，主菜单保持原厂行为](z8-video-orientation.md)。该流程尚未完成整体验收。下文是此前的照片与菜单实验记录，不能代替视频拍摄模式验证。

本实验已复现原厂照片界面的横拍、左竖拍、右竖拍。私有派生镜像可分别解除主菜单和已定位的视频 i 菜单策略位；新增 `menu_layout=True` 修复视频菜单的画布裁切和更新缺行。首个完整验收页面为 **Video recording menu → Video file type**：左右竖拍均遍历六项、保持标题/侧栏/OK 控件、返回并重新进入，最后回到照片界面。输入通过模拟 I2C 方向传感器送入；截图来自未加工的 QEMU 帧缓冲。原始 MAIN 文件保持不变，派生镜像明确记录程序修改，不生成相机升级包。

## 输入与修复

| 虚拟姿态 | XYZ 原始值 | 六字节 BE12 输入 | 原厂分类 |
|---|---|---|---|
| landscape | -1024, 0, 0 | `c00000000000` | 0 |
| left | 0, -1024, 0 | `0000c0000000` | 1 |
| right | 0, 1024, 0 | `000040000000` | 2 |

左右按从相机背面观察机身旋转定义，固定的 LCD 像素反向补偿。这是通过本次显示实验建立的虚拟轴约定，物理传感器安装方向与标定仍未验证。

`orientation-input` 是运行时 QOM 字符串属性，接受六个十六进制字节，三轴各为大端、有符号、左对齐 12 bit。非法长度、非十六进制、非零低四位及未启用 fixture 的写入会被拒绝。已排入 RX FIFO 的样本保持完整，下一笔 I2C 事务才读取新输入。默认仍保留原来的 `(0,0,1024)`。

仅改变输入最初不能改变方向：原厂 `40c8bc70` 已读取新 XYZ，但现有虚拟资源把滤波窗口长度设为零，`40c8be90` 在 `40c8c00c` 返回零向量，随后角度保持 180°。显式选项 `orientation_sampling=True` 只修改 Group12 / Item16 的四个字段：

- `462ea420`：仿真采样间隔 20，未验证物理时间单位。
- `462ea42c`：窗口 5 个样本。
- `462ea430`、`462ea434`：剔除最低、最高样本各 1 个。

5/1/1 来自原程序 `40c8ae38..40c8ae60` 的另一条输入分支。全部角度计算、方向分类与事件通知仍由原程序执行；没有写入最终方向值、跳过分类或替换返回值。角度校准资源仍是明确未验证的零输入。

竖屏还触发了原来只对 ZR 开放的二级 Q11 正交旋转/缩放命令。新增显式 `z8-secondary-resample-model=on` 入口，保留几何、源目标范围、重叠和完整命令检查；支持原生照片信息层的 YYUV+A，以及返回路径的 Y+全高交错UV格式。关闭读回平面 `2b=ffffffff` 时，只写实际输出平面。所有像素来自 guest 源缓冲，没有内置菜单图或宿主旋转。

## 已定位的原厂限制

原生菜单切换 Auto rotate info display 的通知确认设置键为 **`0x02001dcf`**，关闭写 0、开启写 1。最初观察到的短键 `2512/2513` 是先到达的其他状态写入，不能据此认定旋转设置。

- `4038d59c` 返回有效界面方向：开关关闭或抑制掩码非零时返回 0；否则读取 `a03228244`。掩码位于 `a03228308`。
- `4038e3e4(mask, enabled)` 按位设置/清除抑制原因，保留其他位。
- **主菜单**：`40507144..4050714c` 设置 `0x20`，`40507e44..40507e4c` 清除该位。当前全机实测照片状态掩码为 0，进入主菜单变为 `0x20`，真实方向仍为 2；原生菜单保持横向。
- **视频相关 i 菜单路径**：`4046dcf0..4046dd00` 读取模式设置 `218c`、比较是否为 1，再调用 `404a5354` 设置位 `0x1`。同一设置在 `4044e750..4044e764` 选择照片/视频菜单父页 `4f/a5`。`404a5354` 还根据原生布局状态设置位 `0x2`。

`tests/test_z8_orientation_policy.py` 从身份校验后的本地 MAIN 执行这些原始 A64 指令，覆盖开关/掩码/方向组合及视频菜单门控。设置读取和布局读取是显式测试边界；它们不是全机视频模式验收。

## 派生镜像解除限制与页面验收

`tools/z8_orientation_unlock.py` 校验原始 MAIN 的完整 SHA256、长度及原叶函数 SHA256。默认的策略实验仅替换 `4038e3e4` 起始的 64 字节：保持设置/清除掩码语义，然后去除显式选择的菜单 `0x20`、视频 i 菜单 `0x1` 位。自动旋转设置及 `0x2` 等其他位保持有效。输出目录必须不存在，已修改的 MAIN 不能再次作为原始输入。

`UnlockedGuest` 仅把独占 QEMU 的 `-kernel` 改为该私有派生镜像，并核对全部补丁点的已加载字节。结果同时记录原文件身份、实际执行镜像身份、`program_modified_in_simulation=true`、`loaded_leaf_verified=true`、`loaded_patch_sites_verified=true`。原生 MCU 导航验收不重定向 CPU 寄存器，不写最终方向或菜单状态。

首次试验发现：竖屏初次显示正常，移动焦点后标题消失。原因是二级旋转模型没有执行原命令的 `SRC_OVER`，透明像素覆盖了已有内容。模型现在根据实际混合系数区分 `SRC` 与 `SRC_OVER`，复用颜色转换与透明度合成；缺少逆颜色矩阵或禁用读回时拒绝 OVER。修复后管理页标题保留、焦点回到 Rename 时整帧 RGB 哈希恢复。

## 视频菜单的画布修复

单纯清除菜单抑制位会把画布切成 `480×720`，但原厂菜单资源仍按 `720×480` 布置。原生列表纹理仍完整，合成阶段却以错误父画布换算位置：动态捕获 `40332b44` 时，背景的旋转后父尺寸为 `480×720`，列表/滚动条却为 `720×480`，滚动条甚至落在屏外。只修正图像通道仍不够，透明清除区域还会走另一条尺寸查询路径。

显式启用 `menu_layout=True` 时，保留原厂 `0x20` 作为菜单生命周期/布局标记，但有效显示方向、变化通知和禁止状态忽略该位。`4038d658` 仍按原厂逻辑返回横向布局。共享逻辑尺寸 getter `40340b9c` 只在菜单标记有效且结果恰为 `480×720` 时返回 `720×480`；其他尺寸、源纹理、物理输出尺寸及非菜单调用保持原行为。图层和透明清除因此使用同一套坐标。

六处受身份约束的修改范围总计 156 字节：

| 范围 | 用途 |
|---|---|
| `4038e3e4`，64 字节 | 掩码设置/清除和辅助函数尾部 |
| `4038d59c`，76 字节 | 等价压缩有效方向 getter，保留原 16 字节栈帧，容纳指针常量与辅助入口 |
| `4038d554`、`4038d57c`，各 4 字节 | 方向变化前后忽略菜单布局标记 |
| `4038d62c`，4 字节 | 禁止状态与有效方向一致，允许原生姿态事件完成刷新 |
| `40340be0`，4 字节 | 共享尺寸 getter 的受限转换入口 |

辅助逻辑位于上述两个原生函数原有范围内，重放被替换的尺寸读取指令；没有新增可执行映射、堆分配或额外栈空间。这里只是在固定版本仿真中重用已审查函数范围，不是相机安装方案。

呈现方式是**原厂菜单等比缩放、旋转并居中显示**，保留黑边及原厂列表滚动；尚未重新排版菜单控件。文件类型页的说明文字和 Tone mode 提示会随选项正常变化，不能把这些区域当成必须不变的截图内容。

显示模型还补充了已观察到的 direct 控制字 `0x200`：GPU format71 生成的 Y 加全高度 UV 菜单缓存（NV16）。原模型只读取 format4 的 NV12 取景源，会忽略这类缓存。新增读取遵循源使能、尺寸、双 stride、地址范围及平面不重叠检查，保留独立的 NV12 路径；无需依赖 RAW 生产者是否启用。菜单缓存统计为 `menu_cache_frames`、`background_format=NV16`，不会被计为视频取景帧。九项独立 QTest 像素测试验证逐行 UV、透明合成、禁用/未知格式、越界 UV 和两种格式的高度约束，扫描输出不改写源缓冲。

正式验收等待原生传感器分类、MCU 完成回执和虚拟时间内的 UI 刷新，避免将姿态刚变化时的过渡帧当成稳定页面。仅传感器分类改变或等待固定墙钟时间不足以完成验收。

```sh
python3 -B tools/z8_orientation_video_acceptance.py \
  --firmware /path/to/z8-c311-main.bin \
  --qemu /path/to/qemu-system-aarch64 \
  --raw /path/to/input.raw \
  --output /path/to/new-linux-output-directory
```

脚本先用**未修改的原始 MAIN** 建立同一按键轨迹的横屏基线，存于输出目录的 `-landscape-reference` 同级目录；可用 `--landscape-reference` 复用已完成、身份及截图哈希校验通过的基线。随后从已知横屏 MENU 基线进入派生镜像的视频文件类型页，左右竖拍分别遍历 N-RAW、ProRes RAW HQ、ProRes 422 HQ、H.265 10-bit、H.265 8-bit、H.264 六项。

验收包括焦点变化、标题/侧栏/OK 控件、屏内边界、首项整帧哈希恢复、返回及重新进入，并把列表/说明区的归一化内容覆盖率与原始横屏**同一导航轨迹**比较。允许 2 个百分点的栅格化差异；本轮十二项最大差为 0.63 个百分点。这项检查识别大块缺行/缺失区域，不等于 OCR 或全部文字正确性证明，仍需逐图复核。没有采用姿态往返后的像素位置作为逐项参考，因为原厂完整重绘会重新居中焦点、改变滚动位置。

不会发送 OK 确认格式修改。派生运行生成 27 张未经像素编辑的 PNG、`video-orientation.json` 和 `result.json`；原始横屏参考另有 11 张 PNG。

当前证据边界：

- **通过的页面范围**：管理菜单库的标题、Rename/Copy 两项及焦点，左右竖拍均可显示；原生 DOWN/UP、LEFT 返回、RIGHT 重新进入、半按回到照片界面均执行。没有确认 Rename/Copy 写入，也未验证触控。
- **视频文件类型页**：启用新布局选项后完成六项焦点遍历、静态控件保留、返回/重新进入以及照片界面恢复；列表按原厂方式滚动，滚动边缘的部分行与横屏基线一致。
- **视频主列表**：标题、侧栏、滚动条和焦点已在原生截图中验证；不据此代验所有视频设置页。
- **视频 i 菜单策略**：补丁已在原始 A64 视频调用点测试，`0x1` 被抑制，布局位 `0x2` 及自动旋转 OFF 仍生效。真正视频取景/录制 OSD、视频 i 菜单全机流程、录像切换及实体相机尚未验证。

先前的策略限制实验仍可单独复现（需要 `requirements-c10.txt` 的 Keystone 和 Unicorn）：

```sh
python3 -B tools/z8_orientation_unlock_acceptance.py \
  --firmware /path/to/z8-c311-main.bin \
  --qemu /path/to/qemu-system-aarch64 \
  --raw /path/to/input.raw \
  --output /path/to/new-linux-output-directory \
  --unlock-video
```

这个旧脚本不启用新布局修复，保留原型对照用途；省略 `--unlock-video` 则保留视频位。它匹配原厂横屏 MENU 像素基线，再验证管理页焦点改变/恢复及标题，产生 12 个原始截图。完整视频页验证使用上面的新脚本。

## 复现

先按项目构建说明构建当前工作树的 QEMU。原始输入是固定身份的 Z8 C3.11 MAIN；使用 Linux 文件系统输出目录，避免 Windows 挂载路径上的日志并发读取问题。

```sh
python3 -B tools/z8_orientation_acceptance.py \
  --firmware /path/to/z8-c311-main.bin \
  --qemu /path/to/qemu-system-aarch64 \
  --raw /path/to/input.raw \
  --output /path/to/new-linux-output-directory
```

脚本检查原生方向分类、MCU 完成回执和既有 MENU 像素基线，生成十个原始截图及 `orientation.json`、`result.json`。传输完成不等于 UI 已处理，所以进入 MENU 前释放半按并等待；未进入时仅作一次有界重试。`completed` 表示脚本完成；可读性、方向和焦点仍需逐图视觉检查。

使用现有 owner 手动输入时：

```python
from z8_orientation import set_pose, read_orientation

# g 是已暂停、由当前调用者独占的 Z8 guest；需显式启用上述资源和图形选项。
set_pose(g, 'left')
# 让原程序正常运行和处理输入后，再暂停观察。
observed = read_orientation(g)
```

定向测试：

```sh
python3 -B tests/test_z8_orientation_device.py --qemu /path/to/qemu --output /new/device-tests
python3 -B tests/test_z8_orientation_graphics.py --qemu /path/to/qemu --output /new/graphics-tests
python3 -B tests/test_z8_orientation_resample.py --qemu /path/to/qemu --output /new/pixel-tests
python3 -B tests/test_z8_menu_scanout.py --qemu /path/to/qemu --output /new/cache-tests
Z8_MAIN=/path/to/z8-c311-main.bin python3 -B -m unittest discover -s tests -p 'test_z8_orientation*.py' -v
```

原生策略测试使用 `requirements-c10.txt` 固定的 Unicorn。无需固件的 QTest 分别验证 38 项传感器输入检查、7 种图形配置场景及 12 种旋转像素/合成/拒绝场景。独立预期检查像素顺序、UV/alpha、透明/半透明覆盖、源缓冲、保护区、越界、重叠和不完整命令；配置写入不制造完成信号。补丁测试还包括 288 个掩码/范围/ABI 组合，以及 16 个原始菜单、视频调用点与有效方向组合。

未修改固件的初始实验归档在 `.local/analysis/orientation-20261012/`；解除限制原型在 `.local/analysis/orientation-unlock-20261012/`；本轮视频修复在 `.local/analysis/orientation-video-20261012/`，其中 `video-acceptance05` 及其原始横屏参考是最终验收。早期 `menu-layout*`、`video-acceptance01..04` 不作为最终通过依据。本轮 344 项 Python 测试通过，补丁构建/加载器行覆盖率 96%，实际 UI 脚本执行覆盖率 98%，均不代表所有相机页面覆盖率。新增 A64 测试覆盖 96 个方向/开关/其他限制组合、96 个共享尺寸 getter 边界以及图层/滚动条/局部区域的原生定点换算。通过记录限定于基线 `ed1c496` 加本工作树旋转改动，不代验同时进行的 RAW/AF 开发、实体相机、真实帧率、完整启动或安装发布。
