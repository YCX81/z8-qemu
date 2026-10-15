# P8 交付：范围映射、完整收据与两张准备

2026-10-15，Astra → Claude。源码已完成，本地测试通过；**p8构建、新VUI实际码流、run09及真实PSNR尚待WSL/原生运行**。未改MAIN、run01–08或旧审计报告；未动集成树，未提交/推送。

## 1. full-range 证据已达到配置字段门槛

run06/07/08的5份提交PC原生C/S副本均有：C24=1、C29=1、C2a=5、**C2b=1**、C2c=1、C38/39/3a=9/18/9。

`40c5c4f0..534`逐项打包VUI视频信号组：C2b选择`8800`或`0800`，即16020400.bit15；结果400=`1d800`、408=`091209`。字段顺序与标准VUI一致，C/S副本相等。nclx另由`40ccf35c→40c977a0→40c99508`生成9/18/9，`40c99548`写full=1，再由`40cd4b3c..70`序列化末字节80。证据见[range-evidence.json](.local/heif/p8/range-evidence.json)。

因此将默认关闭的1602后端内VUI改为**按完整观测tuple映射full**；其他tuple仍拒绝。不是凭像素极值猜测，也没有声称拿到真实硬件1602码流。物理ISP/位序/色彩矩阵的verified标记保持false。

**旧hash边界：**p8的SPS/lane/hvcC/HIF hash不再与run07/08比较作恒定验收。run09还启用可见输入标记，首张像素也不同；所有新hash必须在同次作业收据与新文件之间验证。旧收据不改。

## 2. 单次完整收据与PSNR

- `--still-hevc-observer`启用时，每次`40c99030`保存独立`heif-jobs/job-NNN.json`，包含codec/协议/C1前后快照，检查连续job、来源frame、几何/hash和已完成状态，最多8个；不覆盖旧history。
- 新`--still-hevc-export-c1`默认关闭，依赖观察器。首次`40c978b4`导出该C1 job的六个真实packed10面：单张≤96MiB、最多两次尝试、全run≤192MiB、单次pmemsave≤16MiB。完整文件hash及读前后身份必须一致，失败不写verified成功收据。实际一张94,549,632B。
- ABI仍64KiB；六PC各8次/48事件最坏41664B，加首停688B共42352B。QOM另有单条128KiB/总2MiB预算。
- 新[PSNR工具](tools/z8_heif_psnr.py)先核对真实HIF→逐NAL/重建lane→同次codec hash，再核对C1 job/frame/实际源文件及解码hash。逐Y/U/V报告MSE、最大绝对误差、peak1023的PSNR；不做色彩/range缩放。源hash、stride、UV声明、路径、长度、frame或文件NAL不符均拒绝。

新模块：[z8_heif_receipts.py](tools/z8_heif_receipts.py)。观察/导出只读，不写guest、不清pending、不发IRQ、不注入执行或返回值。

## 3. 两张探针已准备，未声称已恢复成功

`--heif-shots 2`默认关闭第二张，重复原release/half/full/full-release/half-release序列。第一张释放后另等15个虚拟秒，并只读确认三份成功收据、codec/C1/A2/JPEG退休及取景活动，再发第二组。保留原64菜单动作上限、settle10、half2、600秒host上限。gate不是writer持久化或D980重武装证明；未满足则等待并保留原因，绝不清pending。

原帧号标记低位会被crop(12,8)裁掉。新增默认关闭的外部输入属性`sensor-frame-visible-tag-model`，依赖sensor-frame-tag-model、realize后不可更改：在D980输入DMA之前把同一CFA帧号额外写到(64,32)的64×16区域。原标记及文件不动，第二张可见区域的输入可区分。它是合成场景，不是新硬件完成行为。测试验证区域外不变、默认off、非法输入和两帧差异；QTest尚待WSL。

预期风险顺序：第一张gate未退→第二次FULL无新D980→151/C1/A2来源或pending守卫→1602第二张绝对分配地址/质量与冻结profile不符→writer/卡。新地址仍UNKNOWN；第二张若停在1602，C1导出和提交ABI应已保存供下一轮分析。

正常目标：jobs1/2/3绑定C1/frame1，jobs4/5/6绑定C1/frame2；每组source_output为0/2/1。实际两张HIF分别审计、关联各自收据与PSNR，不拿jobs=6代替写卡成功。

## 4. Claude执行

```bash
cd /mnt/c/Users/ycx/Desktop/Nikon/z8-heif-1602-20261014
bash .local/heif/test-p8.sh
# 上述全部通过后再执行：
bash .local/heif/run-hlg09.sh
```

test-p8构建标签p8、生成bin/qemu-p8，保留p6c全部回归，新增收据/PSNR/可见输入测试及FFprobe检查24个真实QTest码流的full-range HLG。无需安装包。

run09使用新properties和全新`heif-hlg-20261015-09`目录。请回传完整`heif-jobs/`、`heif-c1/`（包括bin）、capture-trace、stderr、首停和卡HIF。原生PSNR命令及第二张对应路径见[文档第14节](docs/z8-heif-1602.md)。

本地普通/`-O`各100项：99通过、1跳过（需Linux真实x265/GLib）；纯C普通/NDEBUG、Python/Shell语法及后端C语法检查通过。外部声明桩的C检查不是完整QEMU构建。

修改文件：HEVC pixels/codec/backend及C测试；sensor-tag/D980/lab.c的显式外部输入开关；observer/probe；新增收据、PSNR、输入/VUI测试；文档与本报告。I4物理槽153c=5、JPEG=6/7、count8已记录，本轮未更改任何集成代码。
