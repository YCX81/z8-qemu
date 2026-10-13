# Z8 公开源码、派生程序与复现边界核查

审查基准：`a97f5dfbc09008d3b739131555e06036226ea568`。本次只读检查该提交的 Git blob、相关源码与文档，并对指定指令 guard 和本地固定 MAIN 作静态比对；没有构建、运行固件、生成派生镜像或重做拍摄验收。工作区未提交的 C 修改不用于代表发布内容。

这是技术来源与可复现性审查，**未作法律结论**。来源分类、文件清单、许可证声明及自动审计是不同层次，不能互相替代。

## 1. “source-only” 的实际含义

该提交的220个跟踪文件中，没有以`.bin/.elf/.nef/.img/.raw/.ppm/.png`为扩展名的原始镜像或运行产物。这支持“发布源码、配置、测试与文档”的文件层描述，**不支持“当前树完全没有原固件指令内容”**。

`tools/audit_publication.py`核对允许路径、清单哈希、文件大小、NUL、部分凭据模式、连续大段hex及源码SPDX。它没有证明每个短常量、整数数组或汇编片段的来源；分散的4字节指令 guard 不会触发连续大段hex阈值。审计自身文档也明确它不是来源或许可判定器。

定点核查发现如下内容，报告不复制对应指令字节：

| 位置 | 当前可证内容 |
|---|---|
| `tools/z8_writer_observer.py` 的 `SITES.return_word`、`TARGET_WORDS`、`EXPECTED`及两处store guard | 共30个原地址的4字节guard，合计120字节。20个指令字以整数literal记录：10个后继、8个callee入口、2个回写位置；另外10个BL由call/target地址公式生成。30项均与固定MAIN对应位置一致。它们是原指令守卫，不是仅有不可逆的身份哈希。 |
| `tools/z8_picture_control_list.py:45` | 包含一个4字节原prologue的显式hex比较，绑定原列表函数；`tests/test_z8_picture_control_list.py:101`重复该literal。 |
| `tests/test_z8_capture_probe.py:19/110` | 两处4字节测试指令常量与其指定原CSEQ入口一致。测试夹具也需纳入内容分类，不能因文件位于tests而排除。 |
| 历史提交`950dff15817d14849bdcb921965fb46af0228c5d`中的`profiles/z8-c311.json` | 已披露的`entry_prefix_hex`保存过32字节程序前缀。a97当前profile已删除该字段，但950d仍是其祖先；当前树清理不等于历史已移除。见[发布边界](publication.md)。 |

这是指定范围内的正面发现，不是全库“没有其他类似内容”的结论。本次没有发现所审生成器内嵌完整MAIN或完整原厂设置表；这也不等于已完成全部历史和内容来源审查。

后续若目标是移除公开的原指令literal，应另批将guard改为固定整MAIN长度/哈希准入后，从用户本地已验证MAIN取得原字节，并继续在每个真实暂停CPU上验证实际指令。必须保留错误opcode、映射变化、越界及错误固件的失败反例；不能直接删除运行时guard。完成回归前不得宣称这些literal已移除。本次不改writer、测试或Git历史。

## 2. 固件派生生成器是补丁配方，生成物另算

`tools/z8_c10_image.py`和`tools/z8_slots_image.py`是针对固定C3.11 MAIN的仿真补丁配方。其下层`_check_source`核完整长度和SHA；patch检查原字节、长度、映射、重叠和预留区域。调用者提供已经提取的本地MAIN，生成器不提供升级容器解密服务、密钥或原程序下载。

需要分别描述以下对象：

| 对象 | 来源与边界 |
|---|---|
| 跟踪的Python生成器 | 地址、布局、检查逻辑和补丁/适配器构造源码；其中上述少量固定原指令guard另行列明。 |
| `simulation-main-not-an-update.bin` | 从完整用户MAIN复制后修改指定位置，必然保留大量原程序内容。不能描述成“纯自编译代码”，不纳入本项目源码发布清单；它也不是Nikon升级容器。 |
| `owned-region-*.bin` | 包含新增代码/数据，也可能包含从用户MAIN读取的原设置、菜单表和被移位指令。`owned`指实验地址区域归属，不证明其全部字节独立创作。 |
| `experiment.json`及运行材料 | patch记录明确输出expected/replacement hex；快照、反汇编、截图和虚拟介质还可能含原内容。即使是JSON也不自动属于源码发布清单。 |

例如`z8_picture_control.py:174–180`从本地MAIN读取原group/item表；`z8_slots_core.py`同样读取表和被移位指令。它们不是将完整原表直接硬编码进公开Python，但生成的loader数据仍包含复制结果。`z8_c10_image.py:102–131`、`z8_slots_image.py:66–82`明确写出上述镜像和patch记录。

原输入只读、新输出目录拒绝复用等检查解决的是实验隔离和误覆盖问题，不改变生成物的内容来源。源码、生成物、原MAIN及运行证据应分别列身份，继续遵守[固件输入说明](firmware.md)与发布排除范围。

## 3. 公开writer的0ff准入与当前构建缺口

`tools/z8_writer_integration.py`在configure与enable两处要求固定原MAIN及精确QEMU产物SHA`0ff25311649a43d8691d60e5da02160cf71ea68d0d2725df0ad7f06f49ddce27`。关闭该选项时不会给普通capture入口附加这个pin。

这项限制对应已审查的旧运行时和内存区域契约。`z8_writer_code_policy.py`绑定该来源的区域白名单；reader仍逐次执行actual-CPU VA→PA、完整安全4字节范围、region/type及读前后状态检查。这些动态检查不是自动接纳任意新构建的依据。

旧来源的C可在公开历史提交`2974e140985004fd63c6031b7315c8130b040b08`定位，C源SHA与[writer说明](z8-writer-observer.md)记录相符；但仓库未分发0ff二进制，也没有已验证的跨工具链逐字节重建保证。二进制SHA不是源码身份。

`tools/build.py`固定上游QEMU归档，应用当前overlay，记录构建产物及来源哈希；它没有让当前manifest替代writer准入。**任何新构建的产物SHA只要不同于0ff，就会被该writer选项拒绝。** 本次没有实际新构建或执行拒绝用例；这是代码直接给出的准入条件。

因此公开复现层次应分开：

* 普通C12 RAW路径有公开CLI配方和历史成功记录，使用当时记录的不可变运行时；它不是当前任意build的既成验收。
* C16的逐文件writer闭环来自私有观察器的历史运行。当前公开writer接入有合成测试，文档明确其独立原程序运行验收待做；其命令前提是用户已经持有精确0ff运行时。
* 即使当前源码构建并通过无固件QTest，也不代表fresh clone立即满足writer的0ff条件，不自动复现C16或RAW历史保留。

补齐方式应是提供可审查的匹配运行时来源/构建链，或先独立审查并验证新运行时契约，再登记精确受控准入；随后用当前公开CLI完成同次来源、writer、停卡文件与解码收据。不能靠删pin、仅改哈希或接受任意自报manifest解决。

## 4. 独立decoder验证了什么

普通NEF记录中的LibRaw解码是独立于此模拟器的文件格式实现；全样本及完整packed字节对照，可以支持“本次模拟输入经处理/写入后，被文件正确保存并解码”的结论。D980、151与1540实际RAM跨度和原writer的关联还必须使用各自收据，不能把不同地址当作同一缓冲。

对照真值仍来自模拟器产生的guest RAM及作者生成的输入，并非独立真实传感器测量。因此零像素差异不证明物理Z8的CMOS、ISP、颜色、降噪、压缩、校准、曝光或时序准确，也不证明触发前历史帧被原生持有。普通触发后单帧成功、独立文件解码、真实传感器真值、历史多帧所有权是四个不同证据层。

本次审查没有新增任何一层运行验收。后续修复、CI检查和结构分析应另记实际执行结果，不能反写成此基准提交已经通过。

## 5. 构建、文档与体量复核

GitHub上的[a97f5df检查](https://github.com/YCX81/z8-qemu/actions/runs/38354020642)
在依赖安装成功后，于`Reviewed source and history`失败，后续测试/构建全部跳过。
本地核对该提交Git blob发现README双语与z8-status三份文档未同步manifest；
不能把更早提交的绿色CI算作a97的构建成功。`3e75b58`已修复清单及小范围工具问题，
[该提交的新CI](https://github.com/YCX81/z8-qemu/actions/runs/38356818323)已通过，包含依赖安装、QEMU构建及无固件QTest；不包含Nikon原固件或历史NEF验收。

用户报告的596项、102跳过和1项导入失败属于其审阅环境，本次没有复跑同一全套环境。
本轮实际运行现有导出测试22项、journal C helper编译/执行4项、构建编排离线测试11项，
全部通过；这些37项不含新QEMU构建、原固件运行或NEF生成。
两份journal C测试经`tests/run.sh`的unittest discovery执行，并非没有接入；
`tests/test_build.py`中的旧发行版名字是正向回归反例，虚拟卡audit里的同名禁用才是待修问题。

PR恢复ccache的条件已修正，save仍限定main push；实际PR命中尚未验证。
SPDM_SOCKET服务于固定上游NVMe的链接依赖，已补注释，不是Nikon认证支持。
硬编码WSL、旧HMP导出、prepare_source重复逻辑和字符串改写仍列为待办。
现有源码行数/字节数和23处机型判定见[拆分分类](z8-model-split-plan.md)，
本轮没有提高体积上限或把未完成重构算作已修复。

TODO原RAW项有23个已勾选的阶段子项（不是40多个），其中包括失败定位；
数量差异不影响“容易夸大功能进度”的批评。它们已移到[阶段日志](verification/raw-research-log-20261013.md)，
TODO只保留功能状态和未完成工作。README与status统一到HALF20，但保留C11/C12/C16旧收据身份。
