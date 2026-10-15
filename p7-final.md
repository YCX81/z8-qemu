# P7 交付：第一份原生 HIF 的独立审计

2026-10-15，Astra → Claude。本轮只写HEIF树，未改MAIN、QEMU模型或集成树，未提交/推送。

**结论：原程序已封装并写出可完整解码的HIF；发现full/limited色彩信令差异，尚未验收连续第二张或真实ISP。**

## 已在 Windows 实际完成

输入：[DSC_0001.HIF](.local/heif/run08/DSC_0001.HIF)，127,514B，SHA256：
`92a6c1fd327f4a9e078d54bac6ce2a389b4016f81e55d87e186989f071b99ee9`。

- 原run08.sha256四份证据全部核对相等；原文件、原失败回执未改。
- 审计器修复`infe`隐藏bit0；其他flag仍拒绝，pitm不得隐藏，核对dimg/cdsc/thmb角色。
- 真实文件随后暴露隐藏图块无pixi。先依据[MIAF规则核对](https://bugzilla.mozilla.org/show_bug.cgi?id=1725022#c2)补测试，只允许隐藏hvc1省略pixi；实际SPS仍须10位4:2:0，已存在的pixi不得不符。
- 所有iloc范围、27个item、11×2 grid、24个hvc1样本的type19、hvcC/SPS PTL、Exif边界通过现有有界检查。
- FFmpeg/FFprobe 8.1.1完整解码主图、两份HEVC附图，另独立解码JPEG。

| 输出 | 可见尺寸 | 原始解码字节 | SHA256 |
|---|---|---:|---|
| 主图 | 8256×5504 | 136323072 | `103139e998d47db78e6806ee5ac3cff6eed4f2279acfbefd944a5d727c9e9bf8` |
| item8193 | 1620×1080 | 5248800 | `0a758315c9948ec07516473f4dfa593ce72c1384f2c47a2c9e947aad7ac7f15e` |
| item8192 | 320×212 | 203520 | `67ce91f941710e78c52627b7b0dcb4e6922af49c5cc787ce2ea5906a5f9c81e3` |
| item8194 JPEG | 160×120 | 38400 | `1674fbdb9fcb02c180bb7d5bc432315ae57f98c4e412b0714fd4f575feb27bb3` |

前三项为yuv420p10le，JPEG为8位yuvj422p；未旋转或tone-map。主图共45,441,024像素。最终审计：[audit.json](.local/heif/run08/audit/p7-release/audit.json)。`decoded:true`不是完整ISO/MIAF符合性或色彩一致性结论。

## 关键问题：容器与裸 HEVC 的 range 不同

文件的24个hvc1项关联nclx：primaries9、transfer18、matrix9、**full_range=1**，即BT.2020 / HLG / BT.2020nc。裸码流FFprobe同样报告这三个色彩值，但range是 **tv/limited**。报告已明确`color_signaling_consistent:false`。

代码定位：[`nikon-z8-still-hevc-codec.h`](qemu/nikon-z8-still-hevc-codec.h)的x265参数是`range=limited`；151模型声明full-range数值假设。下一阶段先核对原C/VUI位义与实际C1输入，再决定后端映射。本轮没有修改文件或编码器来隐藏差异，也不将该差异直接等同“文件不可解码”。

## 文件、lane 与模型证据

由文件恢复参数集和Annex-B前缀后，主图四lane恰为 **13870 / 10708 / 13875 / 7959B**，两附图分别 **8739 / 2502B**，与同次run08日志和原CPU D报告完全一致。各lane前四条T逐字节相等，附图因此已覆盖全部T记录。

主图：`item=1+11*r+c`，`slot=12*r+c`，`lane=slot%4`，`T_index=3+floor(slot/4)`。slot11/23复用item11/22，不作为额外HIF项。参数集在hvcC；hvc1样本只有大端长度和一个实际type19 NAL。

更强的跨次核对：从run08文件重建的四lane hash、四张完整T表hash和22块NAL hash，全部匹配run07留存的主图模型收据。**run08自身没有完整codec/hash收据，附图没有同次码流hash可比；不能把跨次证据冒充同次完整闭环。**详见[correlation.json](.local/heif/p7/correlation.json)。

## 元数据要点（来自文件）

- irot=0；Exif Orientation=1。
- pixi：主grid和两份HEVC缩略图10/10/10；隐藏块无pixi但SPS为10位；JPEG实际8位。
- 三份hvcC PTL与SPS逐字段一致：Main10/profile2、compatibility `20000000`、constraint `900000000000`；主图/1620 level120，320 level60。
- Make=`NIKON CORPORATION`，Model=`NIKON Z 8`，Software=`Ver.03.11`；Exif尺寸8256×5504，ColorSpace=65535（Uncalibrated），ExifVersion=`0232`。
- XMP只有CreatorTool、CreateDate、Rating，没有独立HLG/HDR字段。
- 8192、8193、8194都通过thmb指向主项4096；Exif12288和XMP12289通过cdsc指向4096。
- UUID=`f7505511-17a6-48a4-b813-23fffbeea951`，其后12B为`0000000c 4e484946 00000001`（NHIF子盒/原值1）。私有版本或标志含义尚未证明。

完整原值：[file-details.json](.local/heif/p7/file-details.json)，解码/XMP：[decoded-details.json](.local/heif/p7/decoded-details.json)。

## 本次不能计算 PSNR；下次最小导出

run08没有C1输出面的实际字节，不能由hash或模型重建当作真实输入计算PSNR/最大误差。

已实现一个默认关闭的补充：`--still-hevc-observer`启用时，正常结束也会将5项HLG/1602 QOM状态保存到`capture-trace.json.final_device_states`，其中包含codec/history/ACK。不需要重建QEMU。

更完整方案见文档§13.5：每次`40c99030`前保存codec收据；需要字节证据时按实际used长度导出lane/T。首次`40c978b4`前导出同一C1 job/frame的三个packed10输出，共94,549,632B，独立96MiB预算，逐面与QOM hash核对。解包后逐平面计算MSE、最大误差和peak1023的PSNR；错job/frame、hash、stride、UV顺序或截断均不计算。此RAM导出尚未实现，不混入原64KiB ABI观察预算。

## 下一阶段方案，尚未执行

1. 第二张前验证拍后释放、稳定取景、D980/151/C1/A2/15e2/1602退休与writer idle。同一开机、同一卡，第二张使用可区分来源。run08 D980末态仍pending=`4000000`、acks=0，不能仅据HIF存在宣称恢复完成。若上游正常，1602冻结的绝对分配地址最可能阻止第二张；保持UNKNOWN，按真实第二份元组再收敛。
2. H按独立header和拍照钩子分批合入，保持五个新属性默认off，不覆盖集成视频C1/ring。14个新设备header及相对迁移main的完整文件清单见[integration-inventory.json](.local/heif/p7/integration-inventory.json)。
3. **H来源owner4与I4来源owner5/6兼容，但数组/分支必须随之扩展。**`engine15e2_count_consumed`在H为5项；I4需至少7项，5/6不能落进SDR三元素消费数组。H只允许bank0来源。
4. **物理NBOwner槽另算：当前集成NB_SLOT_153C=5。**若I4的bank0=5指物理槽则冲突，建议保留153c=5、JPEG物理槽用6/7或由Claude统一编号。H没有物理`nb_reserve(...,4)`；不能混同两个命名空间。

## 测试与交付命令

本地相关 **67项普通及`-O`均通过，0跳过**；Python/Shell语法通过。QEMU模型源码与P6C交付hash一致，本轮没有WSL构建需求。

```bash
# 可选WSL host测试，不启动QEMU：
bash .local/heif/test-p7.sh
```

Windows复审命令与完整方案见[文档第13节](docs/z8-heif-1602.md)。输出必须使用新目录。P7修改文件：审计器、capture_probe、两份对应单元测试、文档及本报告；分析和hash清单在`.local/heif/p7/`。

请Claude下一阶段优先确认：range修正的证据路线、I4 owner所指命名空间；然后执行带完整收据的恢复/第二张实验。没有将本次文件验收扩大为真实相机或iPhone HDR显示验收。
