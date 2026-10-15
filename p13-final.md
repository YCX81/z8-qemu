# P13 交接

**400e走held/reset，不能扩进401e direct drain。**`40c1fce4`之后原程序会写hold400e、kick8045；原ISR应处理group2 `0200d000`，再reset/ACK。D980的group1数据pending `04000000`是另一回事。

已实现默认关闭 **`z8-heif-menu-stop-model`**。只接纳目标active/latched、真实完成的D980世代、同帧HLG151已消费并ACK、无新的输入/重叠控制或C1持有者的精确400e事务。request不完成；原kick后才发停止IRQ。新模型不清D980数据pending、不写像素或guest任务状态。旧401e守卫未放宽。

实现集中在两个独立头，main只加request、owner/timer排除、kick/ACK及reset取消钩子；与I4的录像F04F所有权分开，合并点列于文档。

```bash
cd /mnt/c/Users/ycx/Desktop/Nikon/z8-heif-1602-20261014
bash .local/heif/extract-run13-p13.sh
bash .local/heif/test-p13.sh
bash .local/heif/run-hlg14.sh
```

`test-p13.sh`构建**p13**，并增加16组真实RAM QTest、F04F回归。run14保持MENU实验，只多开新模型，**seconds=600**。P12的900秒是我的脚本错误，已补真实parser预检查和600/900回归；协调者修正的run13未改动。

本地普通/`-O`各113项OK（各skip3），C助手`-O0`及`-O2 -DNDEBUG`通过，Python/Shell与run14参数验证通过。**完整QEMU构建、QTest和原生run14待Claude执行。**

材料没有复制原`first-fault-state.json`，因此本次未冒称知道fault时X30及完整MENU teardown调用栈；提取脚本只读既有run13文件补此缺口，不重跑仿真。低层request/hold/kick/ISR序列已由固定MAIN证明。

详见[第19节](docs/z8-heif-1602.md)。改动包含模型头/main/build清单、测试、probe条件收据、本节/本报告及新交接脚本。MAIN、run01–13证据、集成树未改，未提交/推送。
