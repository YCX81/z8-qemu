# P9：第一张完整闭环，第二张尚待精确分支证据

**第一张已在Windows完成同次输入→codec→HIF→独立解码/PSNR闭环，无不匹配。第二张按键确已进入固件与CSEQ，但第一个未满足的原生谓词尚不能从run09现存采样唯一确定；本轮没有猜测性修改模型。**

## 第一张实际结果

run09.sha256的15份原证据全部核对一致。HIF 128,065B，SHA256：
`13a23ab86e6f2ff318271732202fd4d5f4eed7870a685312f4f06e87593eb17f`。

主图完整解码8256×5504、45,441,024像素、136,323,072B；hash：
`5f517b0f6c24fcc11bb0a1651cf0d140fa94be66b3a6a2c58074530a1c7b8b7e`。

三份HEVC图像的nclx/SPS均full-range HLG，审计`color_signaling_consistent:true`、warnings为空。[实际审计报告](.local/heif/run09/audit/audit.json)。

文件重建的各lane、完整T表、24个可见图像NAL，以及20→19变换前后hash，均匹配**同次run09**的job001..003；输入匹配实际导出的C1 job1/frame1。主图四lane为14421/10708/13875/7959B，预览8894B，小图2490B。[完整闭环核对](.local/heif/p9/closure.json)。

| 图像 | Y：PSNR / 最大误差 | U：PSNR / 最大误差 | V：PSNR / 最大误差 |
|---|---|---|---|
| 主图8256×5504 | 74.4851 dB / 9 | 70.7231 dB / 4 | 70.7611 dB / 4 |
| 预览1620×1080 | 69.2638 dB / 12 | 66.3589 dB / 6 | 67.1659 dB / 6 |
| 小图320×212 | 64.7971 dB / 11 | 62.4892 dB / 6 | 63.0623 dB / 5 |

指标来源为实际C1 packed10面，peak1023；没有调色、range缩放或人为对齐。仅测当前数值/packing假设下的编码损失，不代表Nikon物理ISP或实际摄影质量。原始数据：[主图](.local/heif/p9/psnr-main.json)、[预览](.local/heif/p9/psnr-preview.json)、[小图](.local/heif/p9/psnr-small.json)。

**标记可识别为frame1。**传感器坐标(64,32)经原crop(12,8)后，主图标记左上角是(52,24)，覆盖64×16；(64,32)位于其内部。实际源面与解码图均恢复出帧号1及补码，ROI最大误差9。不是通过搜索平移找匹配。

## 第二张已证与未知

- 五个shot2 key_consumer/key_return均匹配。第二FULL在208.523604秒送出，GPIO FULL worker、资源timing、原key consumer、CSEQ event1及capture listener依次命中；不是只在host排队。
- 最终GPIO edges=acks=12、pending0、levels0；取景frames从第二次release的5522继续到6781，video_frames1463→2722。没有证据支持睡眠或取景停止解释。
- 151 pending=false；C1 jobs=retired=1且所有相关pending0；A2 pending0；15e2 jobs/两种ACK均5且pending/core0；1602 jobs3、ACK1/3、pending/errors0。这些首图完成状态均已退。
- D980仍pending04000000/acks0，但其bit26 IRQ未使能（mask1=e0240048），enabled pending=0；writes21/submits1/epoch1一直未增加。它会阻止模型再次接收，但不能据此认定它是**最早**原因。
- CCOMP模型仍只有一次request/response/ACK，第二轮物理握手未计入。不能在尚无第二请求证据时，声称模型漏发了第二个响应IRQ。

最早可证的未推进边界在“第二次FULL已进入CSEQ”与“下一轮CCOMP/D980重武装”之间。具体缺口是CSEQ的800标志轮询、ac69判定，以及后续1000/200000等待/策略helper。旧探针每contact只采第一次CSEQ入口，不能靠事件记录缺席证明某事件从未发生。**目前不够区分缺硬件事件与更深原生策略/时序分支。**静态地址与条件见[文档§15.5–15.6](docs/z8-heif-1602.md)。

另需更正等待时长：已复制trace记录host elapsed536.321690秒、deadline_exceeded=false、complete=true，最终虚拟时间238.536476秒；即第二FULL后约30.013虚拟秒正常结束按键序列，而非实际用满600秒上限。

## 最小下一步：只读补证，不修改事件

完整result中的RAM readiness数组和stderr在沙箱通过UNC访问被拒绝。先提取现有证据：

```bash
cd /mnt/c/Users/ycx/Desktop/Nikon/z8-heif-1602-20261014
bash .local/heif/extract-run09-p9.sh
```

输出新`.local/heif/p9/run09-extract/`，原result/stderr只读并前后核对hash。它不能补出当时未观察的分支返回值。

已实现默认关闭的`--capture-recovery-observer`：拍照阶段14个精确分支点、每个拍照contact的RAM/readiness及相关QOM；共享helper读取原栈LR区分调用者。64事件、64KiB RAM、单次512B、QOM总2MiB，失败保留非完整收据。输出`capture-recovery/`，trace带文件路径/hash。没有写PC、返回值、任务flags、IRQ或pending。

```bash
bash .local/heif/test-p9.sh
# host测试通过后，由Claude执行原生观察：
bash .local/heif/run-hlg10.sh
```

**无需重建，继续使用qemu-p8。**run10保留run09设备属性、按键、15秒间隔、settle10和600秒上限，只增加明确的观察选项及新输出目录；保留skip-cseq-repeat，避免高频全任务重复观察。

只有原生返回值/状态与生产者链证明缺哪个输入后，才修对应模型完成/退休事件。不能清D980 pending试运气，不能host设置800/1000等CSEQ标志，也不能放宽防重或profile守卫。

## 交付状态

本地host回归普通/`-O`各86项通过，0跳过；Python/Shell语法通过。实际Windows审计和三份PSNR已运行成功。**run10未执行，第二张尚未恢复。**

新增host观察模块、probe钩子与测试、提取/test/run脚本、文档及本报告；QEMU模型、MAIN、审计器/PSNR规则、旧证据和集成树均未修改，未提交/推送。
