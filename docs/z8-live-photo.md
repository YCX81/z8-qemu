# Z8 Live 图：可行性与独立推进记录

2026-10-14，Z8 C3.11。结论：**适合开展分阶段研发；当前不能宣称已经能够机内拍摄 Live 图，也没有足够证据承诺真机实现。**

当前工作树 `z8-live-photo-video-20261014`，分支 `codex/z8-live-photo-video`，起点 `ed1c496`。按用户“换个工作树”要求，已改为 C1 相同基线，避免与 RAW 主线模型的交叉冲突。
本树导入经哈希冻结的 C1 工作副本源码和测试，以及原 Live 图工作树的缓存原型。导入记录在 `.local/live-photo/import.json`，候选源码与原构建清单逐项一致；来源工作树未被修改。旧树 `z8-live-photo-20261014` 的原型、未提交修改与运行证据继续保留。

新工作树的新运行、录像事件追踪与回归结果见[录像前置链推进记录](z8-live-video-progress.md)。下文“本次核查”及13项测试是此前可行性阶段的原范围，不能覆盖这份新的运行记录。

## 功能定义与路线选择

用户已确认：**相机直接生成 iPhone／苹果「照片」可识别的实况图资源，不能依赖导出后的转换或元数据补写。** 此要求取代此前“导出后封装手机格式”的临时方案。相机必须在卡上完成照片、配套 MOV 及关联元数据的写入。手机侧允许正常传输和导入，但不把后期生成 Live Photo 资源当作机内功能完成。

按一次快门，保存照片及曝光时刻前后各约 1.5 秒片段；窗口长度仍是研究默认值，声音与画质尚未由用户指定。优先试验 JPEG＋MOV 的资源组合，具体视频编码与元数据布局须验证后固定；不要求单一物理文件。

候选起步参数是 1080p、30 fps、SDR；不是已验证的硬件模式。第一阶段可验证无声视频，声音另设门槛；无声测试不作为含音频 Live 图验收。

- Apple 的官方捕获/保存流程将静态图与 paired video 作为一个 Photos 资产保存；这通常涉及多个资源文件，不是仅凭一个文件扩展名识别。[Apple 文档](https://developer.apple.com/documentation/avfoundation/capturing-and-saving-live-photos)
- Apple 明确说明 Live Photo MOV 包含 content identifier，与对应静态图片的 Exif MakerNote 中的标识相关联。机内需完成两者一致性；本轮选定 Apple 配对 JPEG 与原 Nikon JPEG 分文件保留的候选方案，详细格式和兼容性门槛见下文，尚未经 iPhone 实测。[Apple 元数据说明](https://developer.apple.com/documentation/avfoundation/avcapturephotosettings/livephotomoviemetadata)
- Android Motion Photo 不再作为本次交付目标。独立帧缓存仍可复用，但最终封装和验收以 Apple 为准。不把 GIF、宿主录屏或静态画面重复编码算作拍摄成功。

## 本次核查的证据

| 项目 | 当前证据 | 缺口 |
|---|---|---|
| 普通照片 | 主树普通 NEF 收据已有完整 45,705,600 样本比对记录；本次只读核查，没有重跑 | 尚未与视频在同一拍摄会话同时验收；NEF 内嵌预览不自动等于可交付全分辨率 JPEG |
| C1 视频输入/输出 | `c1-refill-native01` 的实际收据记载 33 个包、33 次解码、32 帧输出，`recording_verified=false` | 这是功能模型 RAM 输出，未证明编码、容器、录像文件闭环 |
| 较新 C1 运行 | `c1-fourth-native02` 收据为 5 个包、5 次解码、4 帧输出，仍 `recording_verified=false` | 不同实验不能把计数累加、挑最大值或宣布最新路径已稳定 |
| 录像写卡 | 已核查视频文档保留卡识别/目录建立但没有 MOV/MP4 的结果 | 本次没有挂载/重检最新 C1 虚拟卡；当前没有找到可用于本功能验收的解码证据 |
| 触发前缓存 | RAW 预拍文档仍未验收；producer journal 是记录，不是帧缓存 | Live 图视频历史须独立保留数据和所有权；不以 RAW 预拍完成为必需前提，也不借其计数证明视频预录 |
| 照片和视频并行 | 分别存在照片和 C1 线索 | 全按快门是否停源、改读出模式、暂停编码、覆盖缓冲，均待同会话验证 |
| 真机 | 无本功能实拍证据 | 时序、功耗、吞吐、内存、音频与机内文件兼容全部待验证 |

本次源文件/收据哈希及摘录保存在 `.local/live-photo/evidence.json`，其中值来自历史运行收据，不是本次新固件执行。

## 分阶段实现与验收

1. **独立帧保留原型（本次完成）**：`tools/z8_live_photo_buffer.py` 复制并持有帧数据，按单一纳秒时钟保留窗口；单次触发冻结历史、继续接收拍后帧，提供静态时刻相对首帧的偏移。容量不足、时间空洞、重叠帧和身份倒退失败；取消后可重新开始。
2. **正常录像前置门槛（未通过）**：接入已冻结 C1 实现，原固件自然启停，取得虚拟卡实际 MOV/MP4；独立解码全部帧，检查动态标记、帧序、时间戳及关闭/Flush。至少重复三轮；视频设备计数与宿主配方 `error=null` 不能替代此门槛。
3. **快门同会话试验（未实现）**：在可解码视频持续输出中发送原 MCU FULL，并记录原厂消费时刻及照片实际曝光/生成时刻。检查前后帧与来源代次。不得假定 FULL 输入等于曝光时刻，也不得用 PC 跳转、改任务返回或虚构完成位消除间断。
4. **机内预录缓存（未实现）**：优先评估压缩码流环形缓存，保留解码所需的随机访问帧和参数集；处理 PTS/DTS、关键帧、音频同步及编码配置切换。本次独立帧原型不接纳帧间压缩包，也不处理 B 帧重排。
5. **机内 Apple Live Photo 封装（未实现）**：相机端直接生成可解码静态图片、配套 MOV，以及 Apple 所需的关联和时序元数据；两个文件关闭成功后再标为完成。检查匹配标识、照片在视频中的曝光对应时间、方向、窗口长度和文件所有权。独立 JSON 关联记录可作为诊断材料，但不能代替文件内元数据。原 NEF 如另存，应保持其格式，不将 NEF 本身假定为已验证的 Apple 静态资源。
6. **iPhone 导入验收（未执行）**：以卡上原始资源为起点，记录 iPhone 型号、iOS 版本和传输/导入途径；导入前不转换、不补写元数据。苹果「照片」应只出现一个关联资产，显示 LIVE，并能长按播放前后动态内容；核对静态照片时刻、方向和音频（启用时）。重新打开相册后仍应成立。普通文件复制或第三方传输是否保留配对需逐途径验证，不能保证任何导入方式都自动识别。

Apple 官方提供使用 `.photo` 与 `.pairedVideo` 资源创建单个 Photos 资产的流程，可作为兼容性诊断参考；若只有专用导入辅助程序能成功，必须单列为受限导入路径，不能宣称 iPhone 原生相机导入已通过。验收导入器不得生成或修复缺失的机内媒体元数据。

下一项相机侧的决定性实验是第 3 步，依赖第 2 步录像通过。若全分辨率照片导致视频断流，必须先找持续低分辨率分支；从视频抽帧只能作为明确降低照片画质的另一路线，不能冒充全分辨率照片。

## 缓存量与实际接入位置

作为容量估算，1920×1080 的 NV12 每帧 3,110,400 字节，30 fps、3 秒为 279,936,000 字节，约 267 MiB，不含边界帧、stride、对象与其他工作缓冲。假设视频码率 12 Mbit/s，3 秒载荷约 4.5 MB，另需随机访问余量、音频和封装。后者只是设计估算，尚未测量 Z8 可分配内存或实际码率。

- `tools/z8_capture_probe.py` 的 `shutter_body()`、原 MCU 消费证据和 `tools/z8_precapture_audit.py` 的共同时间域/文件证据思路可复用；不能直接照搬 RAW 验收格式。
- 视频工作树 `qemu/nikon-z8-video-c1.h` 提供输入/输出与代次观察边界。它目前仍是 QEMU 功能模型，不能移植为实体 EXPEED 驱动。
- 视频文档已定位 `0x40cf2880` 对象创建、`0x40cf3a24` 配置和 `0x40cef3d0` 视频轨构造。地址仅限相应 MAIN 身份；它们不是现成的 Live 图挂钩，须先证明实际录像调用链与对象寿命。

## 本次验证和限制

```powershell
python -m unittest discover -s tests -p test_z8_live_photo_buffer.py
python -O -m unittest discover -s tests -p test_z8_live_photo_buffer.py
```

13 项合成测试覆盖 30 fps / 3 秒完整窗口、数据副本所有权、滚动淘汰、非整帧触发、历史不足、拍后不足、丢帧、重叠、重复身份、容量超限、取消与重入。标准库 trace 行覆盖率记录见本地验证文件。

此模块是单线程宿主参考实现；调用方必须提供真实连续帧和统一时钟。字节预算只限制保留的 payload，不包括 Python 对象和复制峰值。没有连接 QEMU 帧源、编码器、音频、原厂保存器、菜单或手机导入；没有新的固件运行或实体拍摄。这批测试通过仅证明缓存契约，不证明 Live 图功能通过。

## 2026-10-14：Apple 配对设计阻塞的结论

本节由 Astra 完成宿主格式研究和检查工具，不执行固件、QEMU 或刷机。目标仍是**相机在卡上一次写齐资源**；以下宿主参考文件只用于隔离格式兼容问题。录像初始化由另一会话推进，本树不新增设备模型。

### 官方确认、开源实践与未验证项

| 层级 | 可据此确认的内容 | 不能据此确认的内容 |
|---|---|---|
| Apple 官方 | MOV 的 content identifier 与静态图 Exif MakerNote 内标识关联；PhotoKit 将 `.photo` 和 `.pairedVideo` 加到同一创建请求，构成 Photos 资产 | 没有公开承诺任意相机的两个同名文件通过任何传输方式都自动合并，也不是 Nikon 双 MakerNote 兼容保证 |
| ExifTool 源码/定义 | Apple `0x0011`（十进制17）为 ContentIdentifier；MakerNote 头 `Apple iOS\0` 与 `Nikon\0\x02` 分别选择不同解析器；MOV 全局 keys 与 mebx 定时 keys 是不同结构 | 能读出标签不等于 Photos 接纳，更不等于标签所关联的照片、曝光和视频来自同一次捕获 |
| LimitPoint/LivePhoto 开源实现 | ImageIO 的 `kCGImagePropertyMakerAppleDictionary["17"]`；MOV 全局 UTF-8 标识；`AVAssetWriterInputMetadataAdaptor` 写 int8 still-image-time 定时样本，并使用样本的时间范围定位照片 | 开源代码能生成资源不等于 Z8 已具备编码/写卡路径，也不保证当前 iOS 或锁屏动态壁纸通过 |
| 本轮实证 | Python 合成布局测试；FFmpeg 宿主参考样本 JPEG/视频可解码，ffprobe 可见 mebx 轨 | 未在 Apple ImageIO/AVFoundation 或 iPhone 上读取本轮文件；没有机内直出证明 |

来源（2026-10-14 查阅；在线源码链接为移动分支，本轮未取得固定 commit，不当作已冻结上游依赖）：

- [Apple livePhotoMovieMetadata](https://developer.apple.com/documentation/avfoundation/avcapturephotosettings/livephotomoviemetadata)；[捕获与保存流程](https://developer.apple.com/documentation/avfoundation/capturing-and-saving-live-photos)。
- [Apple QuickTime 定时元数据](https://developer.apple.com/documentation/quicktime-file-format/timed_metadata_media)是容器规范入口；本轮网页只返回 JavaScript 页面，未据其正文作未经核实的断言。
- [ExifTool Apple.pm](https://github.com/exiftool/exiftool/blob/master/lib/Image/ExifTool/Apple.pm)、[MakerNotes.pm](https://github.com/exiftool/exiftool/blob/master/lib/Image/ExifTool/MakerNotes.pm)、[QuickTime.pm](https://github.com/exiftool/exiftool/blob/master/lib/Image/ExifTool/QuickTime.pm)、[QuickTimeStream.pl](https://github.com/exiftool/exiftool/blob/master/lib/Image/ExifTool/QuickTimeStream.pl)。本工具独立实现有界解析，没有复制这些项目的实现代码。
- [LimitPoint/LivePhoto.swift](https://github.com/LimitPoint/LivePhoto/blob/master/LivePhoto.swift)提供 JPEG/MOV 写入和定时读取实践；[HEIC 开源生成工具](https://github.com/minutechreview/livephoto-maker)同样使用 MakerApple 17，并未展示 Nikon 共存。
- [Apache Tika 的单样本定时元数据说明](https://tika.apache.org/4.0.0/api/org/apache/tika/metadata/QuickTime.html)也区分样本值与样本呈现时刻。

### 选定的第一阶段文件契约

1. 为一次捕获分配一个新的不复用标识，建议 UUID。静态 JPEG 的 Exif APP1 / ExifIFD / `MakerNote(0x927c)` 中写 Apple MakerNote，内部 `0x0011` ASCII 字符串保存标识；不要把十进制17误写成十六进制0x17（后者是另一个标签）。保留标准曝光、时间、方向、镜头和相机 Make/Model 字段；不得为了让文件“像 iPhone”而虚报相机来源。
2. MOV 的 `moov/meta` 必须采用无 version/flags 的 QuickTime 结构（拒绝 ISO FullBox），其下采用 `hdlr=mdta`、`keys` 中的 `com.apple.quicktime.content.identifier`，`ilst` 以局部键索引引用 UTF-8 字符串，内容与 JPEG 精确一致。把字符串塞进注释或只写 XMP 不满足此契约。
3. 独立 `meta` 媒体轨用 `mebx` 样本描述；该描述的 `keys` 是局部 ID 容器，包含 `keyd = mdta + com.apple.quicktime.still-image-time` 及 int8 `dtyp`，**不是全局 keys 的 count/namespace 布局**。实际 mdat 样本用该局部 ID 保存标记。本项目现在强制写 int8 -1（0xFF），dtyp=0x41，并要求该轨的 tref/cdsc 指向视频轨；标记不是秒数。照片时刻由样本时间及 edit list 映射到电影时间线，本轮默认1.5秒。样本表必须能定位到真实 mdat 字节，声明一个空轨不算通过。
4. 第一阶段选择 JPEG＋H.264/avc1、SDR、CFR、无 B 帧、无声，目标1080p/30，前后各1.5秒。3秒/30fps是本项目默认窗口，**不是 Apple 对所有 Live Photos 的普遍硬性限制**。JPEG不要求转 HEIC；HEIC仍需可被 ImageIO 识别的 Apple 标识及对应 MOV，本轮不验收 HEIC 写入。
5. 文件例：`DCIM/100NZ8LP/LVP_0001.JPG`、`LVP_0001.MOV`；另存同次原厂 JPEG `DSC_0001.JPG`，完整保留其 Nikon MakerNote。同名及 DCIM 是本项目写卡契约，Photos 的身份关联仍靠内嵌标识。机内需要事务性完成三个文件；原件多占一份照片空间，手机若全量导入可能出现额外普通照片，应只选择配对资源进行兼容试验。

JPEG 的关联元数据位于 APP1 中的 TIFF/Exif；HEIC 则不能直接套用 JPEG APP1 字节。HEIC候选需要合法的 HEIF图像/编码属性和 Exif metadata item，通过 item location/description 关联到主图，Exif内部再携带 Apple MakerNote标识；不能把标识写到缩略图或辅助图的元数据就当主图已关联。上述容器关联是 HEIC实现需要检查的结构条件；Photos对自制HEIC、多个Exif item及原Nikon数据的具体选择行为仍需 ImageIO读取和真机试验，不由本轮JPEG审计器推断。HEIC扩展名本身既不保证配对，也不要求对应MOV必然采用HEVC。

### Nikon 与 Apple MakerNote 能否共存

**不能将同一个标准 ExifIFD 的单个 MakerNote 值同时解释为两个厂商布局。** 这是 Exif 容器和现有解析器行为支持的工程判断，不是“任何容器都绝无办法存两份字节”的绝对断言。重复0x927c、重复 Exif APP1、拼接两套头部均不构成已验证的共存协议；读取器可能只取其中一份。本检查器拒绝重复 Exif APP1/同 IFD 重复标签。

| 候选 | Nikon 保留情况与代价 | Photos 证据/决策 |
|---|---|---|
| Apple JPEG＋MOV，另存原 Nikon JPEG | 原件字节不变；多占照片空间、增加写卡量，原件与配对 JPEG 的像素/曝光关联需机内收据证明 | **选定的研发方案**；Apple JPEG 的格式有开源实践，本轮具体文件仍需导入实测 |
| 直接把原 JPEG MakerNote 换成 Apple | 标准 Exif可保留，但丢失 Nikon 私有拍摄/处理信息，可能影响 Nikon 软件工作流 | 可以作为有损元数据方案研究；当前严格审计拒绝缺少 Nikon 原件的输出，不默认授权舍弃 |
| 将 Nikon 原块存到私有 APP、XMP 或额外文件 | 可存档字节；常规 Nikon 软件不会自动把存档当原 MakerNote，偏移引用还可能失效 | 存档≠语义保留；不能用 XMP identifier 替代 Apple 17。本轮未找到 Apple 官方或所查实现确认 XMP 单独可配对 |
| HEIC＋MOV | HEIF 可容纳多种元数据 item，但还需要新增 HEIC 编码、item 关联、方向/色彩和兼容验证 | 开源 HEIC 路线仍用 MakerApple 17；多个 Exif item是否同时保留 Nikon 语义并被 Photos正确选中无证据，**不作为冲突的现成解法** |
| 保留 Nikon JPEG，只给 MOV 写标识/按文件名配对 | Nikon完整，但静态图没有已验证的 Apple关联字段 | 不满足本项目契约；PhotoKit显式提交两个资源也不应被当成可以省略文件内元数据的依据 |

以上“另存原件”只是选定的实现设计，没有修改原厂 writer。后续需在相机端生成 Apple APP1、MOV 全局元数据和定时轨，再结合已保留的视频窗口关闭文件；不能让导入器补写它们。

### 配对审计工具及边界

[`tools/z8_live_photo_pair_audit.py`](../tools/z8_live_photo_pair_audit.py) 只读输入，标准输出 JSON，退出0表示**本工具支持的元数据结构契约通过**，非0表示失败。缺失或不支持的字段不放行。检查包括标识一致、定时轨的声明/实际样本/时间范围、MOV与视频时长、CFR帧数与窗口、同名/DCIM，以及另存 Nikon 原件的 MakerNote 结构与整文件 SHA256。

```powershell
python tools/z8_live_photo_pair_audit.py CARD/DCIM/100NZ8LP/LVP_0001.JPG CARD/DCIM/100NZ8LP/LVP_0001.MOV --nikon-original CARD/DCIM/100NZ8LP/DSC_0001.JPG --nikon-sha256 <封装前原件SHA256> --pre 1.5 --post 1.5 --fps 30 > pair-receipt.json
# 单组卡也可把上述两个输入替换为 CARD。
python tools/z8_live_photo_pair_audit.py CARD --preservation-manifest preservation.json > card-receipt.json
```

多组卡的 `preservation.json` 格式如下，路径相对 CARD，用 `/` 分隔；映射必须覆盖卡上每个 MOV，任何一组失败或跨捕获复用 content identifier 都导致整卡失败：

```json
{
  "DCIM/100NZ8LP/LVP_0001.MOV": {
    "nikon_original": "DCIM/100NZ8LP/DSC_0001.JPG",
    "nikon_sha256": "由封装前可信记录提供的64位十六进制SHA256"
  }
}
```

哈希必须来自相机 writer/封装前可信基线；事后对任意文件自行算一遍再传入只能证明自洽，不能证明历史未变。当前原件检查限定 Nikon type-3 MakerNote JPEG，不接受 NEF作为替代证明。它证明指定原件字节保留，不证明它与配对 JPEG 来自同次曝光；这一关联仍需捕获收据和像素比较。

解析器支持大小端 TIFF、32/64位 box/chunk offset、v0/v1 时间头与单个 rate-1媒体 edit（可带前导空 edit）。普通模式暂不支持 HEIC、碎片MOV、ctts/B帧重排、复杂剪辑和多样本 still轨，均失败而非猜测。时长及静态时刻容差1ms，fps和预期帧数容差0.001；缓存原型允许非整帧触发，调用方须把实际选帧窗口传入，不能默认用理想3秒覆盖边界差异。

该工具**不解码像素/音频，不验证方向/色彩/编码配置，不证明曝光对应、历史帧来源或写卡原子性**。JSON始终保留 `camera_verified=false`、`photos_import_verified=false`、`media_decode_verified=false`。严格成功不是 Live Photo 功能完成。合成夹具没有可解码图像/视频载荷；这刻意将元数据单元测试与实际媒体验收分开。

本轮先失败测试后实现；新增18个测试方法覆盖参数化负例及 CLI，正常与 `python -O` 均通过。相邻缓存13项、容器方向5项、视频资源4项，两种模式也均通过，各模式40项；未设置固件环境、没有运行原指令或 QEMU。标准库 trace 工具行覆盖约93%（不是分支覆盖），命令、逐轮结果、源码哈希见 `.local/live-photo/pair-audit-verification/results.json`。

### 旧宿主参考样本（已停用）与导入验证边界

**旧 `.local/live-photo/reference-pair/DCIM/` 有已知缺陷，不应再用于 iPhone 实测。以下旧验证记录仅是历史证据；下一节的 v2 样本和实测顺序取代旧样本步骤。**

本机 FFmpeg/ffprobe 8.1.1 可用；PATH 未找到 ExifTool，WSL返回 `E_ACCESSDENIED`，未安装工具或改变权限。`.local/live-photo/reference-pair/DCIM/100HOST_/HOST0001.JPG` 和 `HOST0001.MOV` 是**宿主生成的320×180无声动态测试图**，3秒/30fps/90帧，静态时刻1.5秒；不是1080p相机目标模式，也不是 Z8 输出。本轮通过 FFmpeg 全视频/静态图解码，ffprobe显示 avc1、mebx和全局标识；JSON、生成脚本与说明保存在同目录上层。没有伪造 Nikon 原件，严格工具因此报 Nikon 保留条件缺失，属于预期失败。不得把这份失败收据改标为机内验收通过。

1. 先用一组已知正常的 iPhone Live Photo 做传输途径对照，记录 iPhone型号、iOS完整版本、电脑/macOS版本、导入途径及是否启用 iCloud Photos。不要把锁屏动态壁纸资格混入“照片”播放验收。
2. 冻结输入文件哈希。先试宿主参考对；之后机内产物必须以卡上未改写文件再独立重复。导入前后不得补写 MakerNote、转 HEIC、重新封装 MOV，或用第三方“转实况”软件修复。
3. 优先测试目标用户实际要用的途径：读卡器→iPhone「照片」导入，选择 JPG/MOV 两个资源；记录是一项 LIVE还是两项普通媒体、是否能长按播放。若该途径失败，不能仅凭文件中有标识就判兼容。
4. 做隔离诊断：macOS「照片」同时导入两个原文件，再通过 iCloud Photos 同步或从「照片」AirDrop该资产；另可用测试 App 在一个 `PHAssetCreationRequest` 内添加 `.photo` 与 `.pairedVideo`，只提交资源、不修复元数据。记录 API 错误。此途径成功只说明该导入路径兼容，不能回填读卡器原生导入为通过。直接从 Finder/Files发送两个文件也须单独记录结果，不作保证。
5. 检查只生成一个 LIVE资产、长按时前后图案都变化、静态图对应中间时刻；检查方向/色彩，退出并重新打开「照片」复查。声音后续启用时另做同步验收，本参考对无声。
6. 保存屏幕录制、导入日志、资源哈希和结果表；条件允许时导出未修改原件，核对是否发生转换。至少重复三组不同标识，避免 Photos缓存/重复导入污染结论。参考样本固定标识只能用于单组初测，重测应删除先前测试资产或重新生成独立标识。

最终机内验收仍需：同次捕获的照片/历史与后续视频帧证据、卡上三个文件及关闭收据、独立解码、严格元数据审计、目标iPhone导入实证，逐层收敛；当前只完成宿主结构工具和待导入参考对。

### 2026-10-14 真机对照修正与 v2 实测

对照依据：[真机结构记录](../../exa-results/blockers-20261014/live-photo-agent/iphone-heic-reference.md) 的“真机配对样本 IMG_4581”段；前半部分是更早的单 HEIC 样本，不能混为一组。私人原件只读打开，未复制或嵌入本树；工具不读取 GPS IFD，也不输出 MOV 位置键值。诊断收据屏蔽完整 content identifier。

新增硬性检查：`moov/meta` 必须是 QuickTime 子盒布局；still-image-time 载荷必须是 `0xFF`（int8 -1）；局部键的 `dtyp` 必须是 `0x41`；still 轨必须有 `tref/cdsc` 且目标是视频轨 ID。缺失、指向自身或悬空引用均失败。

`--apple-structure-only` 用于 JPEG/MOV 或原生 HEIC/MOV 结构对照：跳过 Nikon 原件保留、项目 DCF 命名、固定窗口/CFR 和视频 composition/edit 时间检查；仍检查同名同目录、标识一致、上述四项及 still 样本表与时间范围。HEIC 只支持文件偏移 extent 的单 Exif item，并检查其 `cdsc` 关联主图。不会跟随外部引用。普通模式保持 JPEG、CFR/窗口和 Nikon 保留要求。

收据含 `diagnostic_mode="apple_structure_only"`、`z8_acceptance_eligible=false`；`ok=true` 仅表示该诊断范围通过，**不能作为 Z8 验收**。真机诊断通过，见 [脱敏收据](../../exa-results/blockers-20261014/live-photo-fix/iphone-4581-diagnostic.json)。最初失败来自旧审计器过严：真机采用 flags=1 的自包含 `alis` 引用；其他 mebx 辅助轨不应套用 still 轨的 edit 约束。现接受内部 `alis`，不检查无关辅助轨；仍拒绝外部引用。诊断中的 fps 是媒体样本数/媒体时长，不代表完整呈现时间线验证。

生成脚本 `.local/live-photo/reference-pair/build_reference.py` 只读取宿主 `host-base.jpg/mov`，输出 `.local/live-photo/reference-pair-v2/`；目标目录存在时拒绝重跑覆盖。旧 JPG/MOV 未修改。每组使用不同随机 UUID，无私人照片数据：

| 变体 | 卡目录与资源 | 用途 |
|---|---|---|
| v2-min | `reference-pair-v2/v2-min/DCIM/100HOST_/HMIN0001.{JPG,MOV}` | 仅修正三项结构差异；Apple MakerNote 仅有 0x0011 |
| v2-full | `reference-pair-v2/v2-full/DCIM/100HOST_/HFUL0001.{JPG,MOV}` | 加 transform / reference-dimensions、live-photo.auto=1、make/model/creationdate、MakerNote 0x0001=17 与 IFD0 Make/Model |

两组为 320×180、H.264、3秒/30fps/90帧、无声宿主测试图。可选 transform 以真机的 dtyp 与载荷长度为结构参考（72字节矩阵、8字节尺寸），单位矩阵和320×180尺寸独立合成，语义/Photos 兼容性待实测。Make/Model 为 `Host Test Generator` / `Live Photo Pattern v2`，未冒充相机厂商。MakerNote 0x0017 含义未知，**两组均不写入**。

两组均通过修正后的诊断、ffprobe 与 `mov_probe.py`；FFmpeg 全视频/静态解码通过。普通审计因没有 Nikon 原件而按预期失败。各组 `verification.json` 及 [汇总验证](../../exa-results/blockers-20261014/live-photo-fix/v2-verification.json) 保存结果。

实测顺序：

1. 先验证一组已知正常的 iPhone Live Photo 可走通同一导入路径。
2. 删除前一次导入的测试项，再只导入 **v2-min** 的 JPG/MOV；不要混入 v2-full 或旧样本。读卡器根目录采用该组完整 DCIM 结构。记录是否合成一个 LIVE、长按播放、前后动态和静态时刻。
3. v2-min 失败才试 **v2-full**；导入前删除前一次导入的测试项。保持同一设备、iOS、读卡器和导入方式，记录完整错误/结果。
4. v2-full 成功只提示可选字段组合可能有帮助，不能判断具体哪个字段必要；两组都失败则继续区分导入路径与容器兼容性。成功仅是宿主样本兼容性证据，机内输出需另验。

先补失败测试，确认 ISO meta、零标记及缺失/错误 cdsc 被旧代码放行，再修正。新增 HEIC、诊断脱敏、内部/外部引用测试。普通及 `python -O` 各通过46项（配对24、缓存13、方向5、资源4）；stdlib trace 行覆盖约86.4%，非分支覆盖。[测试收据](../../exa-results/blockers-20261014/live-photo-fix/tests.json) 包含初次导入路径错误及设置 `PYTHONPATH=tools` 后的通过记录。未运行固件/QEMU、未刷机、未提交或推送。
