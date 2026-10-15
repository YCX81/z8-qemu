# P14交付：1534 bank0的严格参考合同

目前已确定拒绝点，完成候选模型、测试与run15脚本；**未在此环境构建完整QEMU或验证原生第二张**。

run14实际已有两个D980输入帧，第二张停在HLG151之前的153 bank0。`15340000`为设备185、IRQ251，程序`a02c97500`；另一bank1为`15380000`、设备186、IRQ252。当前run14配方其实已经开启`engine153-inplace-identity-model`，拒绝来自旧模型的`bank!=1`硬条件，不能用再开frame-pass解决。

原驱动以任务记录首字`&1`选bank，两个bank共享提交/ISR；同步链为`40e97aac/40ea99c8 → 40e87c54 → 40cba3dc → 40c2896c`。第一张bank1、第二张bank0的轮换是当前假设，**不是已获得的原生首张调用证据**。复制来的run14缺完整153日志、控制和程序RAM；不能断言MENU改变了降噪设置或第一张完全绕过153。详见[规格第20节](docs/z8-heif-1602.md#20-p14第二帧的1534-start与bank0参考合同)。

新增默认关闭的`z8-heif-1534-inplace-model`，只在同次开机已有旧模型成功且ACK的bank1参考、当前D980为下一世代、全部84控制字和1984B程序相同的条件下接纳bank0。唯一允许差异为静态表中的bank程序地址。实际当前RAW RAM读取→原位写回→全量回读与hash之后才完成；不取第一张像素，不清D980 pending、不写任务标志。真实NR及bank数值等价性仍明确未验证。无参考或任何差异继续UNKNOWN。

新`--still-153-observer`只在原有严格停点只读保存两bank程序、控制、CPU栈及同步请求体，不加断点；RAM总预算8192B。新state区分当前参考与上次已接纳作业，保留hash及差异偏移，避免后续bank1覆盖证据。

改动文件：

- 模型：`qemu/nikon-z8-1534-reference.h`、`qemu/nikon-z8-1534-reference-qemu.h`；`qemu/nikon-z9-lab.c`窄钩子和`tools/build.py`输入清单。
- 探针：`tools/z8_153_fault.py`、`tools/z8_capture_probe.py`。
- 测试：`tests/test_z8_1534_reference.c`、`test_z8_1534_reference_helpers.py`、`test_z8_1534_reference.py`、`test_z8_1534_fixture.py`、`test_z8_153_fault.py`；补充`test_z8_capture_probe.py`。
- 文档及配方：规格第20节、本报告；`.local/heif/test-p14.sh`、`run-hlg15.sh`、`extract-run14-p14.sh`、`still-hevc-p14-properties.json`及`p14/`中的分析和收据。

本地普通/`-O`各120项结束OK（各3项平台相关skip）；独立C在`-O0`、`-O2 -DNDEBUG`通过。Shell/Python语法、run15实际参数解析通过。12组新增QTest及完整QEMU构建仍待WSL，测试明确不是native MMIO重放。旧bank1的on/off hash、真实两帧DMA、IRQ/W1C、缺参考/未ACK/同帧/错误字段/程序/指针/数据已ACK/重复START等都在待运行用例中。

Claude执行：

```bash
cd /mnt/c/Users/ycx/Desktop/Nikon/z8-heif-1602-20261014
bash .local/heif/extract-run14-p14.sh
bash .local/heif/test-p14.sh
# 测试通过后：
bash .local/heif/run-hlg15.sh
```

第一个命令可直接核对旧stderr中第一张是否有`Z9_153_FRAME_PASS bank=1`以及控制差异，无需重跑run14。测试脚本构建`qemu-p14`，run15仍seconds600、settle10、原MENU进出时序，只增加新合同与只读首停采样。

若参考缺失或差异存在，预期仍停在1534，输出明确reason和`153-fault/`，**不要为使其通过而删守卫**。若精确匹配，预期原IRQ251/W1C后继续第二张151/C1/HEVC；下游可能仍有新的配置或世代拒绝，尚不宣称第二份HIF完成。MAIN、旧run01–14证据和P13模型不改；无提交、推送或集成树修改。
