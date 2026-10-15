# P8b：可见输入夹具与Windows VUI验证入口修复

**只改测试与脚本，无需重建；继续使用bin/qemu-p8。**run-hlg09.sh未改，待Claude确认两项visible_input通过后再运行。

## 具体原因与修复

- 128×64 packed14的精确stride应为`align32(128×14/8)=224`。旧夹具写256，虽然已submit/latch，仍被`primary_d980_ready`中的精确stride守卫拒绝；不是需要host补写epoch。epoch由成功START自然递增。
- 修正版使用已通过的D980 RAM公共顺序：全配置→submit→latch→读回核对→IRQ mask→START/epoch核对→周期输入。整帧读取改为14336B，ROI步距同步改为224。
- off/on各产生两帧，核对实际物理地址、完整RAM hash、DMA字节及frame/ACK；独立解包ROI帧号与补码，验证on不同、off相同。QTest中的W1C只用于接口测试，未改变原生run09行为。
- 未修改任何qemu源码或守卫，未伪造输入完成。

## VUI验证状态

Claude已在Windows通过24条实际QTest码流的full-range HLG检查，原回执为WSL `p8-tests.H5IC5D/vui-streams.json`。本轮不重写该证据。

test-p8.sh现在明确将Windows FFprobe列为`SKIP`并提示Windows侧执行，不再假定WSL互操作可用。检查脚本支持新`--output`，拒绝覆盖已有报告。完整Windows命令见[§14.8](docs/z8-heif-1602.md)。

## Claude只需执行

```bash
cd /mnt/c/Users/ycx/Desktop/Nikon/z8-heif-1602-20261014
bash .local/heif/test-p8b.sh
```

仅跑visible_input普通与`-O`，两项内部均覆盖off/on。直接读HEIF工作树的新测试，使用现有qemu-p8；不会构建、同步覆盖其他树或重跑整套测试。结果写入全新`results/p8b-tests.XXXXXX`。

本地新增6项单元回归普通/`-O`通过，Python/Shell语法检查通过。**修正版真实QTest尚未执行，本地通过不代替WSL绿。**P8其余676项及24流VUI通过采用Claude提供的执行结果。

改动：visible_input夹具、VUI检查脚本、两份新单元回归、test-p8.sh/test-p8b.sh、文档及本报告。MAIN、模型、旧证据和run09脚本均保持不变；未提交、未推送。
