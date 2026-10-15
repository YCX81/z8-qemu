# P19（WSL 续 P18）交付

**第二张恢复0/3，尚未打通。已证明恢复请求在应用层被拒，不能据此补硬件IRQ。** 本轮由我执行了测试、三次连续独占原生运行及一次独立只读诊断，四次自己的QEMU均已退出，输入不变；无构建、无QEMU源码改动，复用qemu-p14。

## 原因与证据

run18成功样本的约10秒来自MENU释放到半按的宿主间隔；半按后0.804275ms即dispatch。恢复链为外部HALF→应用request4→resume API `40f4d464`→CSEQ task1000→`resume_mode_dispatch`→callback189/post4000，dispatch本身不是硬件ISR或已证明的超时回调。

本轮把MENU固定在原生纯task800的分支出口，三次仍全部失败。三次HALF getter均为4，在`40d5abc0/c8`直接返回，未提交request4。额外诊断明确了更早条件：MENU后197002870748ns，`40f4b5fc→40f532bc→40ece93c`向属性2620列表追加0x40，原计数0；半按时207072645051ns仍为count=1、codes=[0x40]，getter=4。排除了“列表已空、getter缓存陈旧”。原始证据位于`.local/heif/p19/run19-diag-01/capture-recovery/023-*`、`026-*`、`027-*`。

静态表将事件04190181的索引9映射到0x40。候选生产分支`40f5b2a0`在停止返回值含0x80/0x100、且bit10/11未置时选择9；但该原始回调入参/最外层LR本轮没有采到，不能冒称某个具体设备超时。最小下一步是捕获`40f4b0a8`的event、LR和X1的12字节；确认生产者后追具体返回位。没有依据清pending、写guest策略或补发完成，因此未修改模型。完整区分见文档§24.8。

## 自行执行结果

- `test-p19.sh`：最终20组rc=0，普通与`-O`各134项。先红后绿、默认关闭、预算/身份变化及原始调用者提取均覆盖。冻结三轮版本为每模式129项，后续诊断版增加5项。
- `run-hlg19.sh`：三次均单独经`z8-lock.sh ex`；rc=1、UNKNOWN=0、首张三个HEVC作业、D980一帧。首个停止原因均为`Native rearm gate timeout before shot2-release: native-b438-not-2,full-worker-permission,liveview-video`。恢复0/3，第二张FULL没有发送。
- `run-hlg19-diag.sh`：另一次ex锁，rc=1、UNKNOWN=0，同一门禁停止；只读诊断未混入3/3判定。
- 四张首图均128065B、fsck clean，现有独立审计器以Linux ffmpeg6.1.1完整解码通过，主图8256×5504、45441024像素、136323072B。libheif只读结构，缺解码器，不计作解码验收。
- 四张解码主图SHA256均为`5f517b0f6c24fcc11bb0a1651cf0d140fa94be66b3a6a2c58074530a1c7b8b7e`，与run09一致。前三张图像item载荷全部一致，文件差异位于Exif，未解释内部字段，不归因于日期。

| 运行 | HIF SHA256 |
|---|---|
| 19-01 | `e09978d76dbd9cecf91624f6c48fc6fc189a868fe1083234c971b9b2c9a3d19a` |
| 19-02 | `a773b96ebe277db2664aceea5888e54fd0128c3012a16a9745c4e71f17e3e867` |
| 19-03 | `3a282ba9300913364c1a6cdddea1d4826e62786db5b45d90bfa0986bbcfa5b30` |
| 19-diag-01 | `23bed8c8076dd616b77690f73f6333053f6ca19cd30b2503d4231dc63ee6ab77` |

QEMU SHA256：`eeacf54e28658c285c4b8ab5dbaecf7ad2140f8f7a27334e7398e5025f869d1f`。不需要重建或代跑本轮；稳定恢复仍需下一步追查上述状态生产者，不能报为完成。

## 修改与中断处理

修改宿主`tools/z8_capture_probe.py`、`tools/z8_capture_recovery.py`及相应两份测试；新增`tools/z8_heif_menu_boundary.py`、`tests/test_z8_heif_menu_boundary.py`，新行为默认关闭。脚本为`.local/heif/{test-p19,run-hlg19,run-hlg19-diag}.sh`，辅助测试、静态证据、回执及执行日志均在`.local/heif/p19/`。文档更新§24。

中断时HALF_POINTS/can_arm编辑已完整落盘；“Custom tool call output is missing”对应改动经7项边界测试核实生效。未完成的probe接线、运行包装器及文档已补完。原三轮源码与后续诊断源码分开存档。诊断首次提取因尚未生成卡分区镜像报错，未写HIF；随后只读fsck提取、独立审计正常完成，没有用部分文件充当产物。

旧证据未改。旧p18-input/SHA256SUMS自身条目错误保留并记录，其余载荷核对通过；Git仍为Windows路径指针，本轮未修元数据。不改MAIN、不提交、不推送、不触碰集成线文件，不终止其他线进程。
