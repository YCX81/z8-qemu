# P15交付：恢复分支证据与run16宿主门禁

**已确认run15在153 bank0之前就没有恢复原取景与FULL许可；尚未证明最初分歧由13ms时序偏移造成。** 本轮没有修改QEMU，不需重建，继续用`qemu-p14`（`eeacf54e…`）。

完整stderr/result证明：

- 两边最后一个MENU前MVD均为frame23；400e停止的10行控制事务完全一致。
- 后续原程序对`15080008`分别写2和4，已有配置路径差异。HALF退出都到达GPIO worker与相同UI controller；run14随后观察到task1000和task4800/set800，run15没有相应记录。
- run15在shot2-release起：`b438=1`、FULL permission=0、150c active=0、LCD video_frames固定1479；run14为`b438=2`、permission=1、active=73f67且视频帧持续增长。两个FULL都到达GPIO worker，但只有run14到CSEQ task1/poll800。
- P14新bank0 jobs=rejects=0，尚未执行；bank1参考已有效。153故障观察器因没有UNKNOWN也未执行读取。没有找到P14新代码直接改变此处guest状态、IRQ或timer的路径；宿主耗时、SMP和外部输入调度的间接影响仍不能排除。

静态证据：`b438`是CSEQ软件运行/停止请求状态，不能当作硬件idle位。task10000按`a03bba92c`分支，在`40f447a4/40f447f4`可写1；task1000经`40f43b24`的启动模式表，在多个分支写2后发原恢复请求。当前缺少当次具体写入PC，因此上述两个写1位置之间尚不能选定。

task4000在`40f44544`软件置800；已定位的上游为原callback `04190189 → 40f4b3d0 → 40f4b4c4`投递CSEQ mask4000，更上游实际调用者尚待观察。`capture_flags+10`是`40f4b40c → 40c7c778`写入的8字节计时值，不是错误bit组合。

新增默认关闭的宿主选项：

- `--heif-menu-exit-key menu`：run16用第二次MENU按下/释放退出，替代旧HALF退出；旧默认仍为half。此退出配方尚待原生验证。
- `--heif-native-rearm-gate`：shot2 release及FULL前暂停只读核对native状态、原FULL许可、key inhibit和实际取景。等待时继续普通设备服务；每阶段最多10秒虚拟时间，未满足就保留原因并停止，不发送该按键。超时是宿主错误，不冒充UNKNOWN。
- 同选项下增加5个仅intershot、每PC一次的只读点，记录停止模式、写1分支、恢复分派和callback189。读取计入原64KiB RAM／2MiB QOM预算，不扩预算。

run16合计54个菜单动作、两个FULL、settle10、seconds600；模型属性与run15完全相同。若门通过，目标是第二个D980帧和153 bank0；若门超时，应检查`capture_trace.heif_native_rearm_gate[]`和新的`capture-recovery/`，而非继续盲发FULL。若进入bank0但配置不匹配，P14仍严格UNKNOWN并导出`153-fault/`。

验证：本地普通／`-O`各104项全通过，Python AST、Shell语法及实际run16 parser通过；新gate语句行trace为48/48（不是整个probe分支覆盖）。WSL与原生run16尚未执行。

Claude命令：

```bash
cd /mnt/c/Users/ycx/Desktop/Nikon/z8-heif-1602-20261014
bash .local/heif/test-p15.sh
bash .local/heif/run-hlg16.sh
```

改动文件：`tools/z8_heif_rearm.py`、`tools/z8_capture_probe.py`、`tools/z8_capture_recovery.py`；`tests/test_z8_heif_rearm.py`及probe/recovery单测；[规格第21节](docs/z8-heif-1602.md#21-p15menu退出没有恢复原拍摄许可增加有界宿主门禁)、本报告；`.local/heif/test-p15.sh`、`run-hlg16.sh`、`p15/`分析与回执。没有修改QEMU、旧属性清单、MAIN、run01–15、p15-input或旧报告；没有提交、推送或集成树写入。
