# Z8 `0xf04f` hold/reset 控制停止

`z8-primary-f04f-stop-model=on` 是默认关闭、仅 Z8 创建时可选的独立模型。它依赖[内部端口启动模型](z8-d980-internal.md)及既有主参数模型，解决原程序在内部端口启动后发出的一个精确停止事务；不实现内部图像供应者或 RAW 预拍。

06实际遇到 `0x150c000c=0xf04f`。后续原指令和 ISR 的有界静态核查表明，这个掩码整组走 hold/reset，不能将其中 `0xc000` 当成普通 direct stop。原流程依次写 request、将 mask OR 入 hold、写 kick；中间没有数据等待或函数调用。随后才可能另行停止不相交的 `0x10` 单元。

## 事务边界

1. 仅在内部供应者等待状态、所有目标单元 active/latched、无重叠停止所有权和 reset 阶段时，接受精确32位 `+0x0c=0xf04f`。登记 owned/requested，不清 active、不发完成。
2. owned 中的逻辑掩码 `0x2`、`0x4` 暂时不参与旧的 empty-end timer，避免请求在 kick 之前被旧33毫秒路径抢先退休；其它单元保持原行为。
3. hold 包含整个掩码且收到精确 `+0x34=0x1f87d` 后，取消内部等待并转入控制 stop-done。Group2 cause 为 `0x07e8dc00`；没有 D980 帧完成、DMA、帧标记或 D980 source/channel 通知。
4. 原程序通过 group2 W1C、assert/release reset、清 hold、ACK 退休对应 owned bits。分片退休受到同样检查。全局 bounded reset 也能取消该事务。

只读 `z8-primary-f04f-stop-state` 暴露 owned mask 及 request、kick、ACK、reset-cancel 计数。它们是控制停止计数，不能当成图像完成。

| Logical engine | `+0x34` kick | Group2 cause |
|---:|---:|---:|
| `1` | `18` | `c00` |
| `2` | `1` | `1000` |
| `4` | `4` | `4000` |
| `8` | `40` | `8000` |
| `40` | `20` | `80000` |
| `1000` | `1800` | `600000` |
| `2000` | `6000` | `1800000` |
| `4000` | `8000` | `2000000` |
| `8000` | `10000` | `4000000` |

表中数字为十六进制。原有独立 `stop10` 的 `group0=0x04000000` 属于不同状态组；它可以在新事务前、中、后请求或确认，不能被 group2 ACK 清除。新模型沿用已建模的控制停止通知时机，这仍是功能性假设，不是测量得到的硬件延迟。

## 测试与总线范围

29项真实 QTest 在普通和优化模式下通过，覆盖完整事务、默认关闭、机型/依赖、错误顺序与掩码拒绝的状态原子性、分片 ACK、reset 取消，以及独立 stop10 的请求和 ACK 交错。原32项内部启动/普通 RAM 回归也通过。测试仅用自写空 kernel、合成 Bayer 和不执行 CPU 指令的 QTest accelerator；不等于原固件完整运行或实体相机验证。

本模型校验设备实际收到的事务宽度。byte、halfword及实际8字节 reset 事务会拒绝。`+0x0c` 不满足8字节对齐：现有 QEMU 总线会把该地址的 host `writeq` 拆成两笔32位操作，第一笔合法 request 可以登记，第二笔零高字会写入 `+0x10`。测试明确保留该行为并确认未产生 kick、帧或完成；不能宣称对原始 host64位命令原子拒绝。没有修改全局 MMIO ops 或增加特殊总线窗口。

```sh
python tests/test_z8_f04f_stop.py --qemu /path/to/qemu-system-aarch64 --output /path/to/new-results
```

这一批目前完成源码、构建与合成事务测试。新的原固件对照仍须独立记录；06中的停止现场不能自动当作新停止模型的运行验收。
