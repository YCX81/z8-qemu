# P11 交接

**已证明800由原固件CSEQ task200000显式清除。**run11第一张poll前后均为`0x0b08d800`，poll不消耗800；拍后`40f46440`原调用将`0x0b2cf82f`改为`0x0b2cf02f`。第二张poll前同一事件对象为`0x0b08d00c`，确实缺800。

拍后的0419018b回调已经到达，调用者是原软件任务的`40f55600`。它走set800路径后约11微秒，CSEQ又将800清除。不是已证明的1602完成IRQ漏发；也不能据此排除软件任务上游的其他硬件缺口。

两处仍未闭合：第一张初始置位早于旧观察窗口；清除前后的请求13是否被原软件准入状态拒绝，缺直接返回值。**本轮没有模型修复，也没有无依据地改变按键/间隔。**已补默认关闭的早期set点及恢复请求/生产者状态观察，保持64份/64KiB预算。

先提取原截图判断界面状态：

```bash
bash .local/heif/extract-run11-p11.sh
```

输出`.local/heif/p11/run11-screens/`，8份原PPM及hash清单；这些均为对应按键输入前截图，需Claude回传。本轮未据此声称相机处于回放或忙状态。

继续补证的命令：

```bash
bash .local/heif/test-p11.sh
bash .local/heif/run-hlg12.sh
```

继续用qemu-p8，**无需重建**。run12保留原拍摄配方，预期仍可能只有首图；重点是原请求13返回、队列状态、bb95重新置位门及set/clear顺序。

本地普通/`-O`各98项测试通过，Python/Shell语法通过；WSL测试、截图检查、run12待执行。详见[第17节](docs/z8-heif-1602.md)与[run11逐点解析](.local/heif/p11/comparison.json)。

改动文件：两份host工具`z8_capture_recovery.py`/`z8_capture_probe.py`、对应两份测试、文档第17节、本报告，以及`.local/heif/`下三份交接脚本和新`p11/`分析/验证文件。没有修改QEMU、MAIN或旧证据；没有清pending、写CSEQ或伪造完成，未提交/推送。
