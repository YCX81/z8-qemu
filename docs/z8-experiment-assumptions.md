<!-- SPDX-License-Identifier: GPL-2.0-or-later -->

# Z8 实验输入、模型假设与证据边界

本表依据 `a97f5df` 的公开源码与已记录实验整理；本次文档核查没有重新运行固件或测试实体相机。**原固件执行过某条路径、模型接受某组配置、恢复了真实硬件协议，是三个不同结论。** 即便使用未修改的主程序，也不等于实验没有人为输入：外部资源、MCU 对端、时钟和图像供应均影响原程序行为。

完整功能参数分类见[参数来源清单](z8-parameters.md)及[Z8 模型配置](../profiles/z8-model-properties.json)。下面集中说明影响启动和 RAW 研究的输入，不把清单当成所有参数均已消融或物理验证的证明。

## 来源与已做对照

| 项目 | 来源类别与源码/文档 | 对实验的影响 | 已做的消融、负对照或检查 | 尚未验证 |
| --- | --- | --- | --- | --- |
| 缺失 ROM 资源、零参数与格式尾标记 | **人为资源占位。** [资源生成器][rom-plan] 明确创建合成版本、初始门控、索引、资源尾标记；Group12 的大部分字段为空。原 MAIN 与这些外部输入分别记录。 | 让原资源读取器取得可解析的容器或有限字段；未提供的内容并非真实机身 ROM。空白虚拟 NAND 同样不等于机身 NAND 转储。 | 对特定缺项做过下述去抖、阈值、时序和 selector 对照；严格启动还保留未知访问停止点。 | 资源正文、工厂校准、真实 NAND/bootloader 初值未恢复。**没有对所有零字段、尾标记或基础门控逐一做消融。** |
| 去抖 interval20 / count4 | **由本机原初始化状态推导的外部输入。** 原初始化含 interval20/mask15，count4 用于让原算法重建该掩码；[来源与原运行记录](z8-status.md#缺失资源导致的初始化阻塞)、[生成代码][debounce-code]。 | 避免全零采样次数在原算法中下溢并产生巨大循环，影响初始化屏障与 MCU ACK。 | 记录过全零路径的下溢、原循环后的自然到达及后续 worker 屏障返回；不是写 PC 或替换等待结果。 | 外部参数表本身与物理时间单位未恢复；从初始化状态推导不等于读出了工厂参数。 |
| 电池阈值88/77 | **取自 Z8 原程序另一模式分支的常量，再作为实验资源输入。** 当前执行模式仍为1；[原始来源、比较语义与正反例](z8-battery-resources.md)。 | 改变原有符号比较的输入，影响原程序是否阻止 RAW 启动；不直接改保护标志或比较分支。 | 已记录固定合成样本下的0/0阈值反例与88/77正例，包括原资源读取、保护结果、上层状态和 RAW 入口。 | 物理单位、真实电池合法范围、机身校准和保护恢复条件未知。不能用于实体相机。 |
| 时序系数1200 | **人为虚拟时序系数。** 参考[共享时序输入的明确说明][timing-origin]，Z8 基础资源沿用它；普通 RAW 另向 selector1/sub3 的缺项提供同值。[输入定义][capture-input]、[原读取检查][timing-guard]及[C09/C10对照][timing-runtime]。 | 原固件的时序除法、计数和等待长度随输入改变。它不是测得的 CMOS 行周期，也不证明某个真实帧率。 | C09 的目标系数为0，原计算得到巨大等待计数；C10 只增加该外部单元，实际原 LDRH 读到1200，计数下降后进入新的外设停止点。公开 C12 又核对 ROM、RAM 和原读取结果。 | 未恢复原厂系数；未系统扫描其它值、模式和合法范围，也没有物理时序标定。不能用等待缩短证明1200就是正确硬件值。 |
| processing slot15选择记录1 | **原地址/索引是固件观测，记录号1是合成假设。** 原mode14映射slot15；[公开输入与只读检查][slot15-code]、[HALF02/03记录][slot15-runtime]。 | 避免缺项0经原减1形成越界，允许程序继续读取下一层资源。没有由此选择 C15 模式或改变 RAW/JPEG 政策。 | 负对照保留原寻址异常；仅新增一个启动时字节后，原 LDRB 实际读到1，下溢消失并遇到后续严格硬件停止点。公开 helper 区分 supplied、实际读取和未命中，且有错误索引/地址/值反例。 | 记录正文与校准有效性未证明；“读到了1”不等于记录1适合真实 C15。公开 HSFC helper 的完整运行验收仍不能直接继承私有对照。 |
| processing slot8选择记录1 | **原mode7→slot8链是固件观测，记录号1仍是合成假设。** [HALF18/19记录][slot8-runtime]。 | 修复该实验里另一处相同类型的索引下溢，不改变 slot3、slot15 或记录正文。 | HALF18 保留实际0值、原运算与异常；HALF19 仅增加 slot8 字节，同一调用观察到原 LDRB 返回1、资源描述符/全局基址一致及下一次读取地址恢复正常。 | 未直接观察完整 ROM 搬运或该次首个 LDRH 执行，不证明校准正确。核查版本有公开摘要，**没有等价的公开 slot8 helper 和完整 HALF19 驱动配方**；不能宣称这一运行已完全公开复现。 |
| D980 内部端口与 wide 配置 | **配置来自原固件实写；响应语义属于模型假设。** HALF19 的完整28字配置在 shadow/submitted/latched 中一致，不能把它说成人工随意选的尺寸。假设在于把这组精确写入解释成“接受启动、等待尚未实现的内部供应者”。[原观察][wide-runtime]、[精确谓词源码][wide-code]、[模型边界](z8-d980-internal-wide.md)。 | 默认关闭的新选项仅允许这组配置及既有前置条件继续；不提供内部像素、DMA、帧完成或由 D980 引起的完成通知，也不将内部端口映为 RAM。 | 旧模型实际拒绝 wide 配置；新模型有52项普通/优化 QTest、32项旧内部回归，以及逐字段变化、活动配置切换、取消/复位等反例。journal 开启时仍无内部像素记录，取消后普通 RAM 输出有真实字节与哈希正对照。HALF20另有原程序控制准入和状态2记录。 | 完整硅片协议、内部生产者→消费者的数据合同、物理几何与时序未恢复。该元组只是在**上述合成环境下**的固件写入；接受它不代表真实硬件完成，也不代表 RAW 预拍已实现。 |
| 虚拟时钟与诊断投递 | **模型时序与实验调度。** [内部周期配置][clock-profile]与[外部 BoundaryPeer 说明](z8-parameters.md)分开记录。 | 内部周期和外部33毫秒投递可影响中断、原等待和调度；不是相机的实测 FPS。 | 有时钟推进、输入顺序和模型边界测试；原运行记录真实 QEMU 虚拟时间，不能拿宿主耗时替代。 | 未做全部周期参数的敏感性/消融实验，也没有物理 CMOS、曝光或总线带宽校准。 |
| 模拟 sensor、Bayer/ISP/JPEG 与普通 NEF | **合成或用户提供的图像输入，加功能处理模型。** [输入生成器][image-input]、[打包/重采样假设][pixel-model]及[捕获证据](z8-capture-evidence.md)。 | 提供原程序可处理的像素；简化处理、打包和采样规则影响实际输出内容。它们不是恢复的完整尼康成像算法。 | 普通 C11/C12 NEF 经独立解码，完整样本与实际 producer RAM、packed 字节比对；[公开数值收据](verification/raw-single-20261012.json)明确仅为一帧触发后证据。 | 不证明真实 sensor/ISP 画质、物理编码、完整缓冲寿命或触发前像素保留。RAW 预拍仍缺少多帧保留→原 writer→完整文件/像素的同次运行闭环。 |

“默认关闭”只适用于相应诊断选项，不能概括全部实验环境。基础运行器会[明确装载去抖/电池等辅助资源][base-launch]；普通时序与 slot15 输入、wide 内部启动则有各自显式选项。每次判断都应读取当次配方和输入收据。

## 这些对照证明什么

特定缺项的负对照与单项补充，可以支持“这个输入解释了这一次原程序阻点”的结论。原地址、原指令结果和后续停止点应分别记录。它不能自动证明输入是真实校准、同类所有模式都适用，或相机硬件具备目标吞吐能力。

QTest 的合成 MMIO 反例检验模型的准入和拒绝边界；原固件运行检验它在给定虚拟环境中的行为；文件导出、独立解码及实际 RAM 比对检验输出。三者互补，不能互相替代。内部 start/stop 的控制确认也不能冒充图像完成；机器上其它周期源或控制停止 IRQ 不等于 D980 像素 IRQ。

公开命令、固定固件身份和模型源码使相应实验可重新构建，但**文档摘要不是完整原始运行归档**。时序/slot15 的公开实现与普通 C12 配方见[拍摄资源](z8-capture-resources.md)；slot8 和若干历史诊断仍存在上述公开复现缺口。换用新二进制、输入、菜单初态或程序版本后，必须重新验收，不能自动沿用历史通过状态。本表不分发原厂程序、资源正文、截图或运行转储。

[rom-plan]: https://github.com/YCX81/z8-qemu/blob/a97f5dfbc09008d3b739131555e06036226ea568/tools/z8_resources.py#L13-L45
[debounce-code]: https://github.com/YCX81/z8-qemu/blob/a97f5dfbc09008d3b739131555e06036226ea568/tools/z8_resources.py#L27-L43
[timing-origin]: https://github.com/YCX81/z8-qemu/blob/a97f5dfbc09008d3b739131555e06036226ea568/tools/z9_timing_input.py#L17-L28
[capture-input]: https://github.com/YCX81/z8-qemu/blob/a97f5dfbc09008d3b739131555e06036226ea568/tools/z8_capture_resources.py#L18-L30
[timing-guard]: https://github.com/YCX81/z8-qemu/blob/a97f5dfbc09008d3b739131555e06036226ea568/tools/z8_capture_resources.py#L118-L138
[timing-runtime]: https://github.com/YCX81/z8-qemu/blob/a97f5dfbc09008d3b739131555e06036226ea568/docs/z8-raw-runtime.md#L5-L25
[slot15-code]: https://github.com/YCX81/z8-qemu/blob/a97f5dfbc09008d3b739131555e06036226ea568/tools/z8_capture_resources.py#L141-L176
[slot15-runtime]: https://github.com/YCX81/z8-qemu/blob/a97f5dfbc09008d3b739131555e06036226ea568/docs/z8-raw-runtime.md#L51-L57
[slot8-runtime]: https://github.com/YCX81/z8-qemu/blob/a97f5dfbc09008d3b739131555e06036226ea568/docs/z8-raw-runtime.md#L219-L240
[wide-runtime]: https://github.com/YCX81/z8-qemu/blob/a97f5dfbc09008d3b739131555e06036226ea568/docs/z8-raw-runtime.md#L232-L272
[wide-code]: https://github.com/YCX81/z8-qemu/blob/a97f5dfbc09008d3b739131555e06036226ea568/qemu/nikon-z9-d980.h#L6-L31
[clock-profile]: https://github.com/YCX81/z8-qemu/blob/a97f5dfbc09008d3b739131555e06036226ea568/profiles/z8-model-properties.json#L113-L119
[image-input]: https://github.com/YCX81/z8-qemu/blob/a97f5dfbc09008d3b739131555e06036226ea568/tools/make_cmos.py#L1-L24
[pixel-model]: https://github.com/YCX81/z8-qemu/blob/a97f5dfbc09008d3b739131555e06036226ea568/qemu/nikon-z9-d980.h#L77-L123
[base-launch]: https://github.com/YCX81/z8-qemu/blob/a97f5dfbc09008d3b739131555e06036226ea568/tools/z8_run.py#L160-L164
