# P10 交接

第二张的**首个已观测失败条件已定位**：`40f44b3c` 的 CSEQEV01 `0x800` 轮询，第一张 W0=1，第二张 W0=0。mode=2、native_state=1一致；第二张因此绕过参数准备和原生task100投递。

第一张的ac69/post2 gate也为0，不能把它与第二张的差异归因于ac69。第一张随后进入event2的来源需核对原生task100分支。按键已进入原CPU，取景仍活动，首图151/C1/A2/15e2/1602均已退休。

**没有足够依据修硬件模型。**布尔0没有保留内核原错误码，事件pattern/attribute和恢复set/clear过程也未采；不能直接认定缺IRQ或手动清D980 pending。静态已定位800的四类原生置位路径、task bit21清除路径，以及0419018b回调恢复分支。

已补默认关闭的只读观察：原14点保留，新增14点；从真实handle解析事件对象，记录pattern/attribute并复核身份。总64份/64KiB预算不增加。模型、MAIN和拍摄时序未改，**无需重建**。

```bash
cd /mnt/c/Users/ycx/Desktop/Nikon/z8-heif-1602-20261014
bash .local/heif/test-p10.sh
bash .local/heif/run-hlg11.sh
```

run11继续用qemu-p8；预期可能仍只生成首图。重点回读poll前800、原clear前后、恢复set路径、event100与0419018b分支。未命中仅代表当前首次命中观察范围，不能代替完整事件历史。

本地普通与`-O`各90项测试通过，Python/Shell语法通过；WSL测试与run11待Claude执行。run10原34份文件hash已核对。trace显示537.21秒完成、deadline未触发；600秒是预算上限。run09/run10 HIF同大小但hash不同。

改动文件：

- `tools/z8_capture_recovery.py`
- `tests/test_z8_capture_recovery.py`
- `docs/z8-heif-1602.md` 第16节
- `.local/heif/test-p10.sh`、`.local/heif/run-hlg11.sh`
- `.local/heif/p10/` 新分析、反汇编、比较与验证回执
- `p10-final.md`

详见[第16节](docs/z8-heif-1602.md)及[逐点比较](.local/heif/p10/comparison.json)。本轮没有修改QEMU模型，没有伪造完成、清pending或写CSEQ标志；未提交/推送。
