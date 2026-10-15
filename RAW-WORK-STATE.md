# Active RAW precapture work, 2026-10-14 (newest checkpoint at bottom)

User objective remains implement RAW precapture. User explicitly selected
"优先打通无损仿真链路" and later asked to analyze latest pulled z9-qemu and use
a separate worktree. No physical camera, flash, commits, push or publication.

Current worktree: C:/Users/ycx/Desktop/Nikon/z8-raw-precapture-shared146-20261014
Branch codex/raw-precapture-shared146, base b98e26059d50ad7d633cad946f68a24bca172139.
Old detached worktree z8-raw-precapture-20261013 remains, with exhaustive older
RAW-WORK-STATE.md. Its last on-disk checkpoint is RAW47b active; it is stale.
Main z8-qemu has unrelated dirty display C; preserve it. A concurrent video
worktree z8-video-c1-20261014 also exists; leave it alone.

## Latest upstream analysis

z9-qemu HEAD and local origin/main both c6dbfc4ef3ea2ac9d48be2ab66c7d732e241b1ff,
shared146, commit 2026-10-14 08:45:53. No fetch/pull performed by this task.
Read docs/development-snapshot.md, shared146.md/.zh-CN.md, shared144,
qemu/nikon-expeed-lossless.h, nikon-z9-video-raw.h, input-pair source.
Upstream does NOT accept RAW precapture or current public full photo pipeline.
Historical private lossless14 four-file acceptance remains its own fingerprint.
Detailed task report: docs/raw-precapture-shared146.md (opened in Codex, queued).

## Current implemented F0 port (new worktree only)

Twenty existing task code/test/doc files copied from old experiment with exact
hash verification; .local/shared146/migration-receipt.json. New copyright notice
and unchanged upstream qemu/nikon-expeed-lossless.h sha
6f6ef0da9d01571dcf3470ccf1b160fb1c50c5859b6b70ef78d63b4bc97e9100.
Default-OFF engine1540-lossless14-model takes actual acknowledged151 packed14
RAM, F0 encodes with explicit four2048 predictor profile, capacity bounded by
native output reservation, then actual target write/readback before IRQ. It
does NOT yet admit historical HSFC sources or fix the14->12 decoder conversion.
New NJ_F0_LOSSLESS14 metadata format, stride0/actual byte length. Source parent
hash is captured before encoding. Existing host acceptance remains fail-closed
for unsupported compressed format; no NEF claim. Actual native metadata must
match F0/seeds before independent file acceptance.

Build dir /home/ycx/.local/share/nikon-z8-qemu/raw-shared146-work-20261014.
Immutable binary build/qemu-system-aarch64-f0-shared146-c074973b,
SHA c074973bf51b4a15c3dc5a2ecf7d04b40d92e265119056dc2722a61822251b0d.
Build source inventory21; .local/shared146/build-identity.json binds current
sources and tests. Build session69549 finished0. All test guests stopped.
tests/test_lossless14_codec.py:4normal+4-O, compiles C -O0/-O2, independent Python
F0 reader checks all16384 values, extrema, padding, seeds and capacity errors.
tests/test_z8_lossless14.py: olde7 red actual transport fails F0 expectation;
7normal2+7optimized2 pass, actual D980->151->1540 RAM/lineage/IRQ/canaries,
two distinct frames, overflow no partial DMA/IRQ/new journal, default off.
test_z8_producer_journal.py7regressions, helper4 and contract4 also pass.

## RAW48 actual frame sync model (carried from old worktree)

Native group1 bit31 at150c0210/214 ->410b0fa0 ->04220013 ->40e8b9cc/ba60,
native mode6 copies two records and sets B49880000. Added default-off
z8-primary-frame-sync-model after ALL6 actual preview planes, separate pending,
W1C at210, IRQ mask20c, reset cancellations, pending prevents reuse.20normal/
20-O QTests plus19bit3 regressions passed. Same functional assumption as
upstream raw-pipeline-input-pair-model; physical meaning/timing unproved.
Immutable old e7e83984673e3eaf2a2b5cd7ea5e0a9e6ba95b85d9de180d0b200c37b5824ca1
binary .../raw-serial29-work-20261013/build/qemu-system-aarch64-sync48-e7e83984.
Old HERE/half48-model-sources20, pool48_memory.py. No guest/task flag writes.

## Latest original-program run is RAW48c, COMPLETE and STOPPED

Historical HERE=C:/Users/ycx/Desktop/Nikon/z8-qemu/.local/analysis/raw-precapture-20261012/half-history
WSL runs=/home/ycx/.local/share/nikon-z8-qemu/runs/raw-precapture-20261012
Driver half_history_control48c_probe.py, exec76760 completed0; no active QEMU.
Same experimental fb11 forced-RAW MAIN,185contacts,3.2s mode-neutral gaps,
HALF2s/FULL10s/release4s, same source14 fixture, plus bit31 model. 25min host.
All4images actually viewed: soundOFF,Lossless,PRE.3s,C120/RAWL/PRE healthy.
Approval helper exact3-image schema accepted. All185contacts returned,errornull.
Audit raw-history-48c-audit.json verifies218sources/currentinputs/program/binary
unchanged and owned process stopped. New F0 option was NOT in this run.

Earlier48 failed before first input due reserved PC40e98974 conflict with pool
observer.48b failed after1input because mapped observer not initialized before
release-mode-hold. Both audited/stopped, preserved.48c records early scalar
event/model only, after control init uses full mapped double-read snapshot.
New free point40e989bc reads unchanged SP+c returned flags AFTER native clear;
DO NOT interpret W0 there as wait return (it is flag-clear return).
sync48-snapshots.json native mode6 and returnedflags4088460 includes80000;
native callback40e8ba60 reached and actual W1C counts match completions.
Otherdiagnostic28 waits80000002 atLR40e95a54 still recorded, but this run passed
mode gate. That needs group0bit5/event04220004 ->40e8b918/950, NOT upstream
video group0bit4 (which sets B49810). Do not blindly reuse that bitmap.

Critical NEW backend evidence in raw-history-48c/backend47-snapshots.json:
actual tablea02acc138, RAW nodes absent, actual NR return0(bank1,15380000).
Actual direct stage bindings show postNR99a0,NLGCC9d18,MIPU9e10.
MIPU40ea6580 predicate returns0; metadata0=3,26c0,2941,2a4=0,2fc0,286c0,2570=0.
At40ea9eb0 allocation_return=0 on two visits (256180909908ns,265381958502ns).
MIPU prepare return1/retry; context c43296000, outera10d521a0, meta c4012c008,
outer3a0=a1183fcf0,680=a1184cc20,3f0/688/6a8=0,output_count0.
Independent tools/z8_export_nefs.py audit clean4dirs/1fileNIKON001.DSC512;
NO NEF. Result runs/raw-history-48c-nef-audit/ (exporter returned1/no_nef).

## Next concrete work: native class33 pool + preserve14 bits

Raw side allocator4104e6c0: w0=33 ->41047a00, at4104e6d8 availability return;
ifavailable calls41047478, return4104e6ec. New passive observer should read
these TWO sites plus native tablea03f316c0[33] (PA a03f31858), flag a03f31b4c,
config pointer a03f316b8 and its4bytes at33*4, double-read with paused CPUs.
Do not conclude pool absent vs retired vs exhausted from allocation0 alone.
Allocator41047478 calls40cdaa98; original pool metadata/ABI still needs tracing.
Pool creator41047014 uses currentconfig ptr and planslot pointers, 0x75 classes;
plan table chosen byglobal a03f31a68 through a02a24f40, elsea03f31698.
Static files .local/shared146/raw-pool-original.txt,
hsfc-format-and-pool-init.txt,native-format-and-wait.txt.

HSFC decoder allocator410478ec picks class24 (global mode0), metadata2a8=0
implies format3/cap4188000, elseformat4/5. It calls41047afc->410615e0; latter
tests metadata0==3. HSFC true ->4105cc24 sets w0=3,w1=1 and tails4105bc3c.
Need trace4105bc3c and metadata2a8, not just force decoder to write14 into6240
stride. Existing decoder uses lossful14->12, must be replaced for end-to-end
lossless; F0 cannot restore lost bits. Tablea02acc138 lacks two ordinaryRAW
nodes but directRAW exists for meta2a4=3/4 and286c0 (also meta294predicate).
No new firmware/pool/table patch has been made; only old4bytefb11policy exists.

Future source changes and new probes should be in THIS separate worktree.
Old HERE is preserved frozen historical evidence. To migrate private drivers,
copy only runtime receipt's known files relative to old z8-qemu into this
worktree's same .local/analysis/... structure, preserving existing changed
files, explicitly rebind model/binary/adapter and review/output names. ROOT is
driver.parents[4]; inputs include verified-actions.txt, original MAIN and
policy04/derived MAIN. Avoid observer reserved PC collisions and early mapped
reader access; snapshots after finalHALF initialization are easiest.

Acceptance remains at least2 distinct actual pre-FULL and1 post-FULL NEFs in
one run with time/frame/buffer lifetime/native writer/full independent pixel
closure, plus mode-scoped final policy. No acceptance yet. Do not mark complete.

Memory used only as context. Registry changed during this turn; current lines
182-184 cover ordinary NEF vs RAW precapture acceptance; Task2 rollout UUID
01a132c9-2bdb-7b93-9ec6-8b30c409122c. Z9 older-memory304-307, but latestupstream
facts verified directly. Final response needs exactly one final memory citation.
## Active checkpoint09:53 Taipei: RAW49 running in new worktree

**LIVE exec session61670**: half_history_control49_probe.py, started09:42:50
Taipei (01:42:50UTC), run root now
/home/ycx/.local/share/nikon-z8-qemu/runs/raw-precapture-20261014/raw-history-49.
NEW HERE is THIS worktree/.local/analysis/raw-precapture-20261012/half-history.
Same185contacts/fb11MAIN/3.2s neutral timing, binaryc074 immutable,21sources
frozen NEW HERE/half49-model-sources, pool49_memory.py. Adds F0 opt-in and two
passive pool49 points (4104e6d8,4104e6ec), only during finalHALF/FULL/release.
No pool/metadata/firmware-policy mutation. -O --check185 passed. Normal run
host1500s/UI600s. All49 runtime sources frozen; do not edit while active.

**At09:53 the current sound-setting.png, raw-leave-half.png and leave-menu-half.png
were actually viewed: soundOFF,Lossless,PRE.3s. history-half.png notyetexists/
notyetviewed. WATCHit promptly, confirm C120/RAWL/PRE healthy, then call NEW HERE
review_record.write_after_viewing('control49-review','raw-history-49',
actual_images_viewed=['raw-leave-half.png','leave-menu-half.png','history-half.png','sound-setting.png']).**
Never approve an S/Err or wrong-mode image. After49 use NEW audit_current_run.py
raw-history-49; its runroot was updated to20261014 (helper not runtimewatched).
Old RAW48c and build/QTests all stopped. F0 has not run in original firmware yet.

Migration copied218 historical watchedfiles (166MB incllocal original/derived
MAIN) with exacthashchecks. .local/shared146/probe-migration-receipt.json.
Found IMPORTANT old watchlist gap: actualimport mode37b_observer.py was omitted;
it incorrectly watched mode27 instead. Current37b hash30c10eee35d71e47a2f024f0ecd10a58cbc0b5a2caa43afd24b465c1bf6ae2a4
equals recorded prepare_control37b's deterministic mode27 transformation to600s.
Cannot retroactively prove itsbefore/afterhash in48c. Gap recorded in
.local/shared146/observer-source-gap.json and disclosed in commentary.
New49 explicitly watches actual37b and all currentlyloaded projectmodules in
sys.modules in addition to declared files. Source watcher before run only;
late imports remain constrained by fullFrozen/tools file inventory.

Newpool49 snapshot reads stablepaused current tablea03f31858, configptr316b8,
globalflag31b4c,mode31a68, configptr+cc4bytes,nonnull descriptorrange64opaque.
It reports availability_return orallocation_return anddoesNOTinferexhaustion.
2-Ounit tests pass aftermissingmodule-red; normalnotyetexplicitlyrun.
Newprepare49 initiallyfailed importmode18/37b becauseonly218watchedfilescopied;
fixed bycopying actual37b andwatching it. No partiallycreated49modelrootbeforefix.

Furtherstaticfacts saved .local/shared146/raw-format-details.txt:
4105cc24 HSFC wrapper (metadata0==3) calls4105bc3c withw0=3,w1=1.
4105bd08..bd48 **hardcodesformat3 andstridealign32(width*3/2)**; native4140=>6240.
So current12-bit destination isnotjustQEMUguess; formattype3 correspondence is
stronglysupported by arithmetic (still originalwirecodecunknown).
RAWsideformatter4105de1c **returns0 withoutfilling descriptor whenmetadata0==3**.
Even fixingpool33alone willnotfinishHSFCRAW; needappropriatemodel-scoped
firmwarepolicy/route+geometry, notjustpoolcapacityorfilebitdepthchanges.
Ordinaryformatter4105de50 usesmeta2a8nonzeroor286c1=>format5/14bit, elseformat3.
Do notpatchthese until currentpool49 evidenceandcompletecallergeometryareknown.
No14-bit firmwarepatchhasbeenmade.

.local/shared146/metadata-format-stores.txt hasdirectSTRcandidates:
realmetadatawriter40ec8bf8 storesmeta2a8 fromX24+28, optional40f25118transform;
40ec8c04 readsproperty417, switcha02a25210 setsmeta2a4 via40ec8c44.
DoNOTassignsemanticnameof417or2a4withoutfurtherproof. Calleroffsetx24type
andmetadata2a8actualruntimenotyetobserved. Pool49 readsmetadata pointerin
eventX20butdoesNOTcurrentlyread2a8; avoid pretending thatvalueknown.

## Active checkpoint10:21 Taipei: RAW50 pool policy running

**RAW49 completednormally andownedstopped**,185contacts/223watchedsources all
unchanged. NewHERE raw-history-49-audit.json. Completevirtualcardauditclean,
4dirs/1fileNIKON001.DSC512,NO NEF,raw49-nef-audit resultincomplete/no_nef.
Actualpool49 snapshots(two):class33 tablevalue0,configptr**a02978f90**,bytes
**[1,1,0,0]**,transitionflag0,mode1,availabilityreturn0. Allocationcallnever
reached. No evidence of exhausted live RAW pool; absent registration precedes
actual allocation. OLDfieldnamepool_descriptoractuallyreferspooltableBASE
(initcopiesplan+10physicalbase), notdescriptorstruct! Newpool50correctsthis.

Staticactualmode1 selects a03f316a0 ->getter4104572c->table**a02979510**.
Prior guessmode1selects16b0wasWRONG. selectiontables a02a24f20 =
[a03f31698,a03f316a0,a03f316a8,a03f316b0];a02a24f40 skipsfirstfor1..3.
Commontablea02979168;mode0a0297a008,mode1a02979510,mode2a029798b8,mode3a02979c60.
Mode1class33slot**a029796a8** isSTATICNULL;mode0class33pointsto**a02a243e0**.
Mode1class39slot a029796d8 ->**a02a23ba0**. Initialdescriptor <4IQ2I>:
RAW33=(512,160035840,80017920,0,0x8cf3cce00,0,7):2ordinaryfullRAWreservations.
HSFC39=(512,1947571200,4327936,450,0x877ba7200,1,7),end8ebd00600.
Only39overlapsRAW33rangeinthefullcommon+HSFCinitialpoolplans. Neednative
runtimeboundaries too; staticplanscanchange. Raw39descriptorusedonlymode1class39
amongall5tables/117classes,sochangingitdoesnotchangeanothermode'sdescriptor.
Files:.local/shared146/pool-plan-selection.txt,pool-plan-static.json,pool-config-xrefs.txt.

**Newprivate policy50.py** derivesfromoriginalMAIN2b43.. plusoldforcedformat4B:
1.HSFCslot a029796a8 becomesexistingordinaryRAWdescriptorpointera02a243e0.
2.HSFC39totalbytesbecomes**1467170304**,count**339**. End**8cf2db000**,990720B
gapbeforeRAWstart8cf3cce00. RAWend**8d8c6c200**,remainingtailunused.
Old450countwouldoverlap. No runtimehostRAMwrites/taskflags/allocatorreturns
areforced;modifiedinitializedinputtableslettheoriginalcodeallocate/register.
FullSHA/length guards,exact4allowedfilespans,allotherbytesunchanged,allinitial
common+mode1poolsnonoverlapchecked. 3normal+3-Otests;wronginput/undeclaredoutput
orold450countreject. ItdoesNOTfixRAWdescriptorcat3earlyreturnor12bitdecode.
Thisisstillprivateexperimentalpolicy,notamodescopedfinalfeatureorcameraBIN.
OriginalMAINpreserved. Generatedprogram:
NEW HERE/policy50/simulation-main-raw-pools-not-an-update.bin
SHA **d52a1c7decad40a28c598809bae2dd3b56b9a25edc137cef683187f11ce24f1c**.
PolicyprovenanceJSONlistsallowedfilespans[(14150776,4),(80234152,8),
(80931748,4),(80931756,4)]. No14-bitcodepatchyet.

**RAW50 LIVE execsession38402**,started10:19:20Taipei/02:19:20UTC.
NEW HERE/half_history_control50_probe.py,reviewcontrol50-review.
Run/home/ycx/.local/share/nikon-z8-qemu/runs/raw-precapture-20261014/raw-history-50.
Samec074QEMU/21frozenhalf49-model-sources/pool49_memory/F0optionON/all49input
contacts and3.2sneutralpacing. ONLYnewderivedd52program +passivepool50observer.
--check185under-Opassed. 1500hostseconds/600UIreview. AtcheckpointNO50images
viewedyet. WATCHfresh4imagesandpromptlyreviewexact3+soundONLYifhealthyC120RAWL
PRE.3s. Allwatched50sourcesfrozen,don'tedit. Old49/allQTests/interopstopped.

pool50_observation.py aliasesPOINTSfrom49butrecordsactualpooltablevalue,
actualselectedplanpointerviaobservedpoolmode,andliveclass33/39descriptorfields
32B (alignmentword,totalbytes,unitbytes,countword,physicalstart/end,flags).
Reportsraw_registered_at_plan_base,reservations_overlap;doesnotinventpool
capacitysuccess.4-Otests(passincludes2importedPoolEvidence);normalnewtests
notyetexplicitlyrun. Driverfilespool50-snapshots.json. Newpolicyvalidate
fullychecksbytes;driverallthreeoldpolicy26.DERIVED_SHAreferencesreplacedwith
actualpolicyhashandoldGATEinstruction33008052stillchecked. After50auditwith
NEWaudit_current_run.pyraw-history-50andlookactualplanbounds/allocationresult/
firstnewMMIO. Geometryzeroexpectedpossibleafterallocationbecauseoriginal
4105de1ccat3rejectstillthere;don'tmaskit.

## Independent F0 interoperability component evidence

Newprivate.local/shared146/f0_interop.py createdHOSTREPACKEDfixturefromunchanged
historicalcapture-public-12/export-nefs-01/file-000001.NEF sha51f11c..58.
ActualoriginalMakerNotetag150at88352startsF0+fourLE2048seeds;RAWIFD123360,
8280x5520/14bit,stripoffset772608,length80017920,stride14496.
Importedencoderproduced**34339053B**F0stream;hosttestappendeditandchangedONLY
TIFFstripoffset/lengthintheCOPY. NameHOST-REPACKED-F0-NOT-A-NATIVE-CAPTURE.NEF.
**LibRaw0.22.1/rawpy0.27.1 decodedall45,705,600sampleswith0mismatches**,u16SHA
1d5020b50e25a64b777bd59876f874e2abff84ecbfe413cc237f0bda30ca64bd.
Packed-sizefallbackexcluded. FixtureSHA13fffc1896c1069468da63b3183582db5571a1df3b728d97b81bc8634d9de59b.
Receipt runs/raw-precapture-20261014/f0-interop01/result.json explicitly
native_capturefalse/raw_precapturefalse. NOTnewnativeNEF orpretriggerproof!
OriginalNEFhashunchanged. ActiveZ8venvhasNOrawpy;usedEXISTING
/home/ycx/.local/share/nikon-z9-qemu/raw-reference-20261012/venv/bin/python.
Noextra packageinstalled. No clang/aarch64assembler/llvm/unicorncurrentlyfound.

## Active checkpoint11:34 Taipei: RAW52 U16 model running

**RAW50 stopped at real new MIPU START:** cpu1 PC40c0db14 WRITE15100008=1,
LR40ca1138.184contacts;229watchedsources/inputs/program/binaryunchanged,
ownedstopped. **Native pool policy SUCCESS:** poolbase8cf3cce00 registered,
availabilityreturn1,allocationa1184c500,imagebuffer688a1184c080,MIPUprepare
return0. Actualplan raw160035840B@8cf3cce00,history1467170304B/339slots@877ba7200,
nonoverlapfalsecheckcorrect (reservations_overlap=false). So missingpoolfixed.
MIPUconfigactualsource85065b000,4140x2756,stride6240,crop6/2to4128x2752;
Rawtarget8cf3cce00 butRAWdimensions4a0/stride4d0/format4ac all0. Newstageblocked
byunmodeled151 historicalrequest; rawsideformattercat3earlyreturnstillmatters.
Compactfields.local/shared146/raw50-mipu-request.json.

**Private policy51.py** adds3instructions to pool50:
4105bd0c MOVW10,#4 (instead3),4105bd28 LSLW8,W8,#1 (insteadwidth*3),
4105bd2c NOP (insteadofdivide2). Existing+31/align32yieldswidth*2aligned.
This is16-bit STORAGE,desiredsampleprecision14; onlyC1204140x2756investigated.
NootherHSFCsizeacceptance (larger16-bitinputmightnotfitold68MBallocation).
2-OtestsverifyCapstone instructions/full7allowedspans/undeclarededitsrejected.
Generated HERE/policy51/simulation-main-hsfc-storage16-not-an-update.bin SHA
**001029677339b364150afda97d12c7fb4f9283f97630904fd0e2d912769c7077**.
No camera update or runtimeflag/PCmanipulation.

**RAW51 completedtofirstunsupporteddecoderstartandstopped**,exec75593ended1,
all4imagesactuallyviewedhealthyC120/RAWL/PRE.3s/soundOFFandapproved.184contacts,
232watchedsources/inputs/program/binaryunchanged/ownedstopped. Nativefirstfault
40c30ec8 WRITE15300008=1. Actualfrontconfigurationdelta(oldsource/targetandstart
aside):**530:2->0,544:6240->8288**. Width4140,height2756,inputcapacity414800,
allotherprofilefieldsincluding15b0core**UNCHANGED** (core2cstill83,not84!).
Decoderrequesttarget85065b000,source8cbd32e00inthisrun. Oldpacked12modelcorrectly
refused;noU16pixelsproduced. RAWpoolpolicycarriedunchanged.

**Implemented defaultOFF z8-hsfc1530-u16-model**(requirespaireddecoder),after
tests. AdmitsONLYmeasuredmode0/stride8288 alongwithunchangedotherfields. Decode
actualvalidated14bitNZ8packet,currentfullSHA/CRC/nativephysicalspanasbefore,
writesall14-bitsamplesasLEu16with8zero-paddingbytesperrow. Norescale. Existing
mode2/stride6240rounded12pathunchanged. Otherpairingsreject. Metadataaddsstride,
storage_bits16,output_bits14,precision_preserved;legacyrecords12/12. Topprecision
trueonlynonzerojobsALLU16;separateu16_jobs. EndW1C/resetprotocolunchanged.
tests/test_z8_hsfc1530_u16.py **11normal+11-O**,15legacydecoder+18NRregressions
pass(allownedstopped). TestscheckEVERYsampleandpadding/canaries,oldpacketwhile
newexists,corruption/nohostrestore,missing/disabled/wrongprofile/overlap,
maskW1C/reuse/reset/immutable. Originalc074 redfailsactualU16DMAexpectation.
Current/immutablebinary **e90c2ea3bd1c8bd3851047f8830f510833e3495eb493c829848b18ef5d8b7563**,
.../raw-shared146-work-20261014/build/qemu-system-aarch64-u16-52-e90c2ea3.
.local/u16-52/build-identity.json;21HERE/half52-model-sources,pool52_memory.py.
OnlyC+hsfc-decode.hchangedvs49model;compiledsource/memory_regioncallclosure
checked. AllF0/oldmodelsremaininthisbinary. NRcurrentlystillpacked12-onlyand
expectedtorejectU16;doNOTclaimNR/RAW151/NEFsupportfornewstorageyet.

Newdecode52_observation.py (importaliasdecode40_observationindriver) fullactual
sourcepacket+outputU16pmemsaveat40c30ecc;independent7-byteunpackvsallU16samples,
completepadding/SHA/tag/frame/time/flatview/4CPUstablechecks. max2samples,
52MiBtotalphysicalreadbudget.4-Otestsaftermissing-module-redpass. Files
decode52-evidence.json,actual-decode52-job-N/output.u16le.bin.
NR44observeralreadycapturesBEFOREstart0x40c28974andafter978,usingactualbankX8
andconfigprogram48/4c;itwillrecordnewNRmode/stridebeforetheoldmodelrejects.
SinceNRjobs0itdoesnottryoldpacked12pixelread. Useitsdescriptor.raw_hexand
mode_low2/stride_low15tosupportU16NRlater.Donotguessmodefromformat4.

**RAW52 LIVE execsession26502**,started11:33:18Taipei/03:33:18UTC.
run/home/ycx/.local/share/nikon-z8-qemu/runs/raw-precapture-20261014/raw-history-52.
HERE/half_history_control52_probe.py,reviewcontrol52-review. Same0010program/
185contacts/3.2sneutral/HALF2FULL10release4,onlye90model+U16optin+fullU16observer.
-O--check185passed.1500host/UI600sec. **AtcheckpointNO52imagesviewedyet**.
WATCHfresh4imagespromptly;approveONLYactualC120/RAWL/PRE.3s/soundOFFviaNEW HERE
review_record.write_after_viewing('control52-review','raw-history-52',
actual_images_viewed=[raw-leave-half.png,leave-menu-half.png,history-half.png,sound-setting.png]).
Beforestartcorrecteddriver'solddecode40_scopeclaimtoaccuratedecode52_scope,
andmatchedprepare52transformation. All52runtimefilesFROZENnow.

Remainingafter52:actualU16decodeproofthenNRU16profile/support,RAWsideformatter
HSFCdescriptor(whichcurrentlyleavesRAWdimensionszero),historical151input,
RAWencodinggraph/nativeNEFwriterandfullpre/postfiles. Candidate(notapplied):
4105de34currentlyMOVW0,0onmetadata0==3couldtailbranch4105cc24whichusesnative
HSFCgeometryconstructor;normalbranchde3cunchanged. MustvalidatecallerABI and
actualtargetrequest. MissingHSFCtableRAWnodes[3,5]/[4,5]couldreusecomplete
ordinaryRAWtaskdescriptors;notjustserviceIDs. NoRAWside/graphpatchyet.
**Avoidwrongstaticinference:**41061600CSINCW0,W8,WZR,EQ chooses3whenmeta0==3,
otherwise1 (notreverse). Itsactualtail41061758 is4105dad8, NOT4105bc3c;
doNOTcallthisformatterRAW-tagdimensionswithoutmoreproof. Saveddisassembly
.local/shared146/raw-dimensions-and-stage.txt.

## Active checkpoint12:07 Taipei: RAW53 input boundary running

RAW52 FAILED BEFORE HALF,all4imagesactuallyviewed: Lossless/.3s/soundOFFbut
history-half.pngS/Err. NOapproval.182contacts/235watchedsourcesunchanged,
ownedstopped. Decoderwrites0/jobs0/u16jobs0;newU16pathneverran. Syncbit31had179
completions/179ACKs. Diagnosticallyrepeated28/mask80000002/flag0 at40e95a54;
23/B48c800000alsoappeared. CannotcallU16failureorcausalbit31fixcomplete.

NewdefaultOFF**z8-primary-input-boundary-model**,C+pixels.honlychangedvs52.
Afterall6realpreviewplanes,group0bit5(20),IRQmask200/W1C204,ownpendingholds
reuse,ownbroadresetcancellation. Z8native410b0b20=>04220004=>40e8b918/950sets
B498flag2aftercopyingfirstparameterrecord. Thisfunctionaltiminghypothesis
doesnotwritesoftwareflags/OSreturnsorclaimphysicalsource. 20normal2+20-Otests
(includingbit3/5/31coexistenceandindependentACKs),20syncregression,11U16
regressionpass. Olde90redwritessixrealplanesbutlacksbit5. Alltestguestsstopped.
Currentimmutablebinary**698a630558dae38770668aa3fcfba968fe09fc5f756172de2a2928f188d01524**
at.../raw-shared146-work-20261014/build/qemu-system-aarch64-input53-698a6305.
.local/input53/build-identity.json;21HERE/half53-model-sources,pool53_memory.py.

Newinput53_observation.py points40e8b954(afterMOVW1,2,beforetailflagcall),
40e95a3c(actualwaitreturnW0&1success). EarlycontrolNone:scalars+QMPmodelonly;
aftermappedreaderstarts:double-readSP+8flagsandstableCPU/clock.2-Otestsafter
missingmodule-redpass. ReservedPCcollisionchecked. No gueststatewrites.

**RAW53 LIVE execsession88433**,started12:05:52Taipei/04:05:52UTC.
HERE/half_history_control53_probe.py,control53-review. ProgramSTILLpolicy51
SHA001029677339b364150afda97d12c7fb4f9283f97630904fd0e2d912769c7077,
same185contacts/3.2sneutral/HALF2FULL10release4/1500host/600UI. -Ocheckpassed.
Onlyinputbit5modelandreadonlypointsadded. U16decode52observerretained. Atthis
checkpointNO53imagesviewed. WATCHfreshsound/raw-leave/leave-menu/historyPNG,
viewall4andapproveonlyC120RAWL/PRE.3shealthyviareview_record exactschema.
All53runtimefilesFROZEN. AfterrunNEWaudit_current_run.pyraw-history-53.

Futurecandidate**policy53.py is NOT the program used by RAW53**. ItaddsRAWside
tailcallandRAWnodesonpolicy51;2-Otestsnowpass. Initiallyguardfailedbecause
40e5de34actualMOVW0,WZRencoded2a1f03e0,notMOVZ0;guardcorrectedafteractualread.
Branchcandidate4105de34->4105cc24preservesX0metadata/X1descriptor/LR;normal
nonHSFCbranchearlyguardunchanged. NOTgeneratedtoBINorusedyet.
Correctgriddimensions**11rows/15columns,stride78(hex)** (not11columns!).
backend47alreadyusescorrectr*15+5. Candidatecells:
ordinarya02ac8d80 ->nodea02ac9758(kind2,taska02927d60,seqa02acbd78),
HSFCtargeta02acc2c8currently0;
ordinarya02ac8df8 ->nodea02ac97a8(kind2,taska02927dc8,seqa02acbf58),
HSFCtargeta02acc340currently0. Complete80Bnodesreused,notjustserviceIDs.
Candidateandtestsprivateonly;neededlaterafternativeU16/NRrequestsareknown.

## Active checkpoint12:27 Taipei: RAW53b bounded peer extension

RAW53 stopped at167contacts (leave-menu-half), hostchannel8 explicitcap1024
batches. All1024TXmatchedoriginal;contentsdiverse(tophit78),notoneinvented
response.235? Auditactual**238sources**unchanged/ownedstopped. Decoderstillnot
reached. Input53 observednativewaitsuccess1multiple timesandfailure0too;doNOT
claimstablefrontendfix. No53history-halfimage/approval.

NEWpeer53b_limits.py wrapsONLYfreshchannel8WritePeerbudgets1024->4096batches,
16384->65536words. Originalclock_if_readyandallrequest/TX/RXguardsinherited
unchanged;channel9unchanged;continuationmustalreadyhaveexactnewbudget.
OverrideandhelpersourceSHArecordedinrun.2-Otestsaftermissingmodule-redpass.

**RAW53b LIVE execsession65386**,started12:23:16Taipei/04:23:16UTC.
HERE/half_history_control53b_probe.py,reviewcontrol53b-review,runraw-history-53b
under20261014. Same698aQEMU/half53-model-sources/pool53_memory,0010program,
185contacts/timing/models/points;ONLYboundedhostpeerextension. -Ocheckpassed.
At12:26throughraw19,NO53bimagesviewed. Watch4freshimages/approveonlyhealthy
C120RAWL/PRE.3s/soundOFF. All53bruntimefilesfrozen. Prior53processstopped.

Also normalreadonlyobservertestsdecode52/input53/pool50all10pass now.
Latestshared146active-programfixwaschecked:running_readyisusedbynewsecondary
VIDEOpath;thisZ8ordinarypreviewdoesnotcallit. Notportedwithoutrelevantcause.
Detailedreportandtransport/boundarydocsupdatedwithscopedprogress.

## Active checkpoint12:50 Taipei: native U16 proved, RAW54 running

RAW53b passedall4actualUIreviews,C120RAWL/PRE.3s/soundOFF.184contacts,
241sources/inputs/program/binaryunchanged;ownedstoppedatNRstart15380004=1,
PC40c28974. Increasedpeerpassedold1024cap; no protocolvalidationrelaxed.
**Actualnative-selectedU16decodePROVED:** frame13,captured254533567781ns,
completed256101601978ns,read256101602138ns;hostFULLinput255284162526ns,
MCUconsumer255284670131ns. SourcepredatesFULLinput(about.7506s,nominal.3s
window/physicalC120timingSTILLnotqualified).22,841,728B,all11,409,840samples
equalactualretained14bitpacket,padding/tag/fullSHAverified. Source8cbd32e00,
target85065b000,outputSHA0b651a47dbfc1b7f61314537c924f527879a47f15d6d30826e7f4ac762fe9f94,
packetSHA67301f1e4e9a7b58886e435cc0061e6f5ed2e00c7a49ed5fbe1215f717c3ddcc.
Filesraw-history-53b/decode52-evidence.json andactual-decode52-job-0001/.
NotNEF. ActualNRbank1requested**mode_low2=0,stride8288,4140x2756**.

NR54implementation:decodedU16record(storage16/effective14)expectsNRformat0,
usesactualrecord.output_stride. ExistingfullRAMSHA/per-generationandcross-bank
consumptionguardsretained,allbytesidentitycopied;noNikonNRclaim. C-onlychange.
Newtest_z8_hsfc153_u16.py18normal+18-O,18legacyNR+11decoderregressionspass.
Redold698failspositiveNRcases. Alsofixedtest-onlyU16outputselectionfromlast
recordtoUNIQUEvalidtarget/frame (A/B/Afixturepreviouslyselectednewestwrongly).

Newbinary**ade02b697ae0001d597966201146141a817a7309fa9b3c15696c9718ad8683e1**,
.../raw-shared146-work-20261014/build/qemu-system-aarch64-nr54-ade02b69.
21HERE/half54-model-sources,pool54_memory.py,.local/nr54/build-identity.json.
nr54_observation.py andnr54_final_observation.pyadaptoldreadersforU16extent,
requireparentstorage16/effective14,fullactualRAMSHA.5-Otestsaftermissing-red
pass;normalnotyetexplicitlyrun. Afterstartfilesactual-nr54_after-bank-*;
finalfilesactual-nr54-bank-*,nr54-final-evidence.json (distinctnames).

**RAW54 LIVE execsession24445**,started12:48:55Taipei/04:48:55UTC.
HERE/half_history_control54_probe.py,control54-review;runraw-history-54under
20261014. NEWprogrampolicy53 generated:
HERE/policy53/simulation-main-hsfc-raw-graph-not-an-update.bin SHA
**356b98be8a0d9a0e1d3fab9c248c305c09bab2f35116afbc248d2357ddda4d5d**.
ThisaddsRAWsidecat3tailcall4105de34->4105cc24andcompleteRAW_A/Bnodesatcorrect
HSFCcells a02acc2c8/a02acc340 topreviouspool50+storage51. Static2normal/2-O
tests(fullboundaries/Capstone/nodeidentity)pass;NO nativegraphacceptanceyet.
Same185contacts/3.2sneutral/HALF2FULL10release4,4096/65536ch8hostbudget,
1500host/UI600. -Ocheck185passed. AtcheckpointNO54imagesviewed. WATCHfresh4
imagesandapproveonlyhealthyC120RAWL/PRE.3s/soundOFFwithreview_record.
All54runtimefilesFROZEN. Previous53b/QTestsallstopped. After54auditandinspect
nativeNRfullU16proof,actualRAWdescriptor/MIPUconfig/nextstrictMMIO.

## Active checkpoint13:41 Taipei: RAW55 historical151 running

RAW54 passedall4actualUIreviewsandexactapproval.184contacts,248runtime
sourcesunchanged/ownedstopped;nextUNKNOWNstill15100008=1at40c0db14.
**NativeU16NRbank1 full22,841,728B matcheddecoder** frame13/outputSHA0b651a47..
fe9f94. ActualHSFCtableRAWnodesnowa02ac9758/a02ac97a8. RAWdescriptornowvalid:
151 raw4a0=ac4102c,4d0=2060,4ac=2,target8cf3cce00. Inputsource85065b000,
400=ac4102c,430=2060;preview450=ac01020,crop454=20006,strides4128.
c40STILL28b,c2181,c21c3,c2202. ActualQEMUconfigsaved.local/nr54/native151-config.json.

NewdefaultOFF**z8-hsfc151-pixels-model** extends existing151finitepixelpath.
UniquematchingactualU16decodedrecordmusthavecompletedNR/notisp_consumed;
actualfullsourceSHAand14-bitvaluerangechecked. ExactRAW54profileonly. Reads
actualsourceRAM,generatesdeclaredfunctionalcroppedRGGB/sRGB/BT601NV16,RAW
copiesALLU16bytesincludingpadding. NoD980framesinvented. Actualall3writes
precedeIRQ2;perdecoderisp_consumedpreventsA/B/A;overlappingprioroutputs
invalidatebeforewrite. Protectsotherretainedpackets/decoderoutputsfromoverlap.
Z8ISPRecord512boundedmetadata:frame/capturetime/parentdecoder/source/3outputs/
expectedpayloadhashesafterMEMTXsuccess. Hashbasisexplicitlypreparedoutputs;
newobserverdoesindependentactualRAMreadback. Generic151jobs/sourceaddresses
remainusablebyexistingpreview/JPEGhelpers,source_kind1distinguishesHSFC;
consumed_dma_frame0andD980framesuntouched. RAW151sourcekindseparate.
LegacyF01540explicitlyrejectssourcekind1untiladaptedlater (nofakeD980lineage).
Legacyproducer-generation-journalMUSTOFFwiththisoptin;ownHSFCrecordsused,
notmislabelledpacked14/D980journal. Newrealizationcheckexplainsthis.

tests/test_z8_hsfc151_pixels.py:oldaderedinitialfixturemissingpairfixed,then
realold151reject. Firstgreenfixturewroteunsupported4a8=0;removedwrite(no
registeradmissionwidened),preservedfailure. Final**12normal3+12-Ooptimized2**
pass:everyRAW/NV16byte(comparedindependentNumPymath),padding/canaries,missingNR/
source,corruption,geometry/overlap/actualRAMgap,reuse/oldreceiptretires/A-B-A.
7ordinaryD980/journalregressionsand18U16NRregressionspass. Allownedstopped.
Newbinary**4323928a08d200b3f610082b29fc55ae51cdb633ecabd73b5e04a40dad8b0d58**,
.../raw-shared146-work-20261014/build/qemu-system-aarch64-isp55-4323928a.
21HERE/half55-model-sources,pool55_memory.py,.local/isp55/build-identity.json.
OnlyC+hsfc-decode.hchangedvs54;memorymaps/compiledsourceschecked.

isp55_observation.py at40c0db18(afteractual151start),max1snapshot/72MiB:
actualsourceU16+RAWout+Y+UVexports,fullsource/rawbyteequalityandhashlineage,
NV16actualhashmatchescompletedmodelreceipt. RuntimepreviewalgorithmNOT
independentlyrecomputed (unit testdoesfullmath);receiptlabelsfalse.3-Otests
passincludingfullcollectorandMMIOrejection;normalnotyetexplicitlyrun.
Outputisp55-evidence.json,actual-isp55-job-0001/{source,raw}.u16le.bin+planes.

**RAW55 LIVE execsession78219**,started13:40:10Taipei/05:40:10UTC.
HERE/half_history_control55_probe.py,control55-review,runraw-history-55under
20261014. Sameprogram356b98be8a0d9a0e1d3fab9c248c305c09bab2f35116afbc248d2357ddda4d5d,
185contacts/3.2sneutral/HALF2FULL10release4,ch8budgets4096/65536,1500host/UI600.
Onlynew151model+readonlypointadded.-Ocheck185passed. AtcheckpointNO55images
viewed. Watchall4freshPNGs/approveONLYhealthyC120RAWL/PRE.3s/soundOFF.
All55runtimefilesFROZEN. After55audit,inspectactual151outputsandnextstrict
hardwareprofile,likelyencoder/previewbutDO NOTassumeuntilactualresult.

## Active checkpoint14:18 Taipei: RAW55b unchanged retry, U16 codec component

RAW55 stopped pre-FULL at183contacts with pool14 observer unordered multi-hit:
CPU0 d8_empty40e8c1fc andCPU1 WAIT_CALL40e98970. No hardware UNKNOWN;
decoder/151jobs0. Audit raw-history-55-audit.json verifies251watchedsources/
inputs/program/binary unchanged and ownedprocess stopped. This is NOT a151
failure and no native151 success claimed. All4RAW55 UIimages actuallyviewed.

RAW55b running, launched around14:05, exactsame4323928a QEMU/356b98be MAIN,
contacts/limits/points. Newdriver half_history_control55b_probe.py and
prepare_control55b.py,provenance control55b-provenance.json. SessionIDwaslost
fromtruncatedtooloutput; process cmdline confirms owneddriver and QEMUlive.
Read-only status latest174release-mode-neutral around14:17. All3currentmenu
images incontrol55b-review actuallyviewed: soundOFF,Lossless,PRE.3s. Fourth
history-half.png NOTyetexists/viewed/approved. Watchandreviewbeforeapproval!
All55bruntimefiles frozen; doNOTedit pool14_session whileactive.

Newunlinkedcomponent qemu/nikon-z8-lossless-u16.h convertsLEu16valid14bits
to packed14 and invokes unchangedattributed F0helper. No sample rescaling,
highbitsreject,sourceunchanged,paddingignored,written0onerror. Tests new
tests/lossless_u16_wrapper.c +test_lossless_u16.py: missing-headerred,then
4normal+4-O pass; eachcompilesC-O0/O2,independentall16384values/4140width,
nondefaultseeds,padding,capacity/geometry/highbitsrejection. NOT inbuild.py/
QEMUyet; nativeencoderprofile stillneedsactualrun. Runtime55filesunchanged.
isp55_observation3NORMAL testsnowpass too (previouslyoptimizedpass).
docs/raw-precapture-shared146.md updatedwithcurrent53b/54evidence andlimits.
z9-qemu HEAD/originmain rechecked14:16 stillc6dbfc4shared146,clean.

## Active checkpoint15:13 Taipei: RAW55c live after source dispatch fix

RAW55b passed4freshUIreviews and actually executed historical151 job1/frame13
at256110590230ns. ActualRAWmodelclaims22,841,728B source85065b000 ->8cf3cce00,
RAWsha0b651a47..fe9f94,Ysha50aec01888d58171309751566e2af203391df7516945a43922ee3bbcfdc2b727,
UVsha3245ba66c869380d61e275f7d5bd7cadab4b6467993c4307e61e6861e6b424df.
BUT originalpubliccapture observer calledcapture_raw_output first and rejected
"Invalid D980 frame counter" becauseD980frames0. No independentISPreadback,
noUNKNOWN/noNEFclaim. Audit55bverifies252sourcesunchanged/ownedstopped184inputs.

Newpixels55c_scope.use_legacy_pixels validatesexplicitRAWsource_kind/jobs;
kind0ordinarypreserved,kind1history requirespositiveframe/decoder_job anduses
existingisp55U16observer,unknown/malformedreject. Nochangeguest/counters.
3normal+3-Otests aftermissingmoduleredpass. prepare_control55c verifiedall55b
watchedsources,cloneddriver/newreview,andguardsenablelegacyreaderbythishelper.
Same151pointwillnowgetprocessedafteroriginal_handle; allothermodels/points/
program/limits/contactsunmodified. -Ocheck185passed.

**RAW55c LIVE execsession86355**,startedaround15:03Taipei/07:03UTC.
Runraw-history-55c under20261014,control55c-review,driver55c. ActualsoundOFF
andrawLosslessimagesviewed15:12; leave-menu-half/history-halfNOTyetviewed.
At15:12 hadqueuednav03; nofullinput. Watchfreshremaining2images/approveonly
healthyC120RAWL/PRE.3s. All55cruntimefilesFROZEN. Immutablebinary4323928a and
MAIN356b98be8a unchanged. U16F0helperstillNOTlinkedintoQEMU;don'tclaimitactive.

## Active checkpoint15:39 Taipei: native151 proved, RAW56 running graph dependencies

RAW55c all4UIimagesreviewed/approved,actual151 fullreadbackPROVED.
isp55-evidence.json: frame13 captured254483600158ns,ISPcomplete256072372671,
read256072372673,physicalread68,403,968B. RAW22,841,728B equalsactualdecoder
source(includingpad),sha0b651a47..fe9f94;Y/UVsamehashes55b. IndependentNV16
math onlyunit-tested,notrecomputedruntime;receiptlabelsfalse. 255sources+
input/MAIN/binaryunchanged/ownedstopped.184inputs. NoUNKNOWN.

NEW actualnativeexceptionin55c: ESR96000006,FAR0,PC40d2bd70 LDRW0,[X0]
withX0=0,LR40d16040, X19=c43296020; RAW_B taska02927dc8,contextc43296000.
RAWrecordouter6a8 DID getnativealloca118c63b8at40ea9fb0beforeRAWnodes.
Themissingobjectiscontext+20 syncstorage,notRAWrecord. Noexceptionbypass.

Staticdependencyrecovery: originalordinarytablecol5row0 nodea02ac92a8 has
init40eaaad8 ->common40ea6a2c,createsctx58queuecapacity4,andctx20/ctx30via
40d15e2c. HSFCnodea02acc7f0init40eaab7c capacity2(no12e/12f),ONLYctx30.
Ordinaryrow1nodea02ac9578 seq a02acb238=[40eaba98,40eabc78,40eabca8,40eabb20].
HSFCrow1a02acca20 seqa02ace280 only[40eaba98,40eabca8]; missingbc78signalsctx20.
40eaba90 extraordinaryrow0stepisNOPreturn0(doNOTcallitasignal!).
Nodes80B sameallfieldsEXCEPTinitializer/sequence; task/type/ownershipsame.

Newpolicy56 derivespolicy53, ONLYtwoextra8bytecellreplacements:
ordinarysourcea02ac8c18 ->targeta02acc160 olda02acc7f0 ->a02ac92a8,
ordinarysourcea02ac8c90 ->targeta02acc1d8 olda02acca20 ->a02ac9578.
Completeoriginalnodesused,nohostflags/pointers/returnswritten.
test_policy56 3normal/3-O passaftermissingmodulered; completefulldiffboundaries
andCapstonenativecreate/signalvalidated.STATICnotruntime.
graph56_backend wrapsbackend47snapshot,existingRAWentrypoints48/60also
double-readctx20/30actual16Bobjectsand4Bpointeeifnonnull,doesnotdereference
null,dontclaimwaitsuccess. Tests4normal/4-O include2existingbackendtests.

**RAW56 LIVE execsession61855**,started15:36Taipei/07:36UTC.
Runraw-history-56under20261014;reviewcontrol56-review;drivercontrol56.
Program HERE/policy56/simulation-main-hsfc-raw-sync-not-an-update.bin
SHA**cb3d2098421dac784513d0408ff5bd262cd28be53185d9c66bbb9c9bcb1e3adb**.
Same4323928aQEMU/frozenhalf55sources/pool55memory/185contacts/4096ch8/limits/
tracepoints. Newprogram+readonlysyncstorageonly. -Ocheck185passed.
AtcheckpointNO56imagesviewed. Watchfresh4UIpngs/approveonlyhealthyC120RAWL
PRE.3s/soundOFF. All56runtimesourcesfrozen. OriginalMAINunchanged.
After56audit,checknativeactualnonnullsyncandnextstrictMMIO/profile.
U16F0componentheaderNOTinbuild/QEMUyet. Existing1540F0stillordinaryD980only.
Upstreamhasdualbank153c/1540raw_transport model; likelyneededafterfirstactual
nativeprofile,notportedyet. Actualnativeencoderconfigsstillnotobserved.

## CRITICAL latest steering, coordinator correction read 2026-10-14 17:02 Taipei

User explicitly requested reading
C:/Users/ycx/Desktop/Nikon/exa-results/blockers-20261014/raw-chain/MESSAGE-TO-RAW-1540-v2.md.
READ and accepted correction. Also read r1-astra.md and main docs/z8-photo-audit.md.
**D[0]=0 is TRANSFER, NOT lossless compression. Earlier policy57 naming was WRONG.**
40e84b88..90 skips compression ifD0=0; fallback40e850ac uses input layout and
40e85318..28 setsfixedstride*height,ignoring1540actualresultlength. D0=0also
suppressesMakerNote96. WritingF0forthistransferwouldcreatewronglength/noF0header.
**NextRAW58 MUSTchoose B: D0=1 nativecompression**,formatD4=5(raw14),then
measureactual1540profileandactualD+18/F0header. Noquestionneeded: userselected
Losslessbefore. RAW58notyetprepared/launched. Allnativeguestsstoppednow.
Coordinatorconstraint no loosenedguards/commit/push/flash remains.

Do NOT run new268bd59 model on RAW57's transfer parameters. It was NEVERrun
withoriginalfirmware; onlyauthoredQTest. Itsnative-semanticclaim iswithdrawn.
CurrentprovisionalCguardaccepts44=2,D4=0,strides8288andwritesF0: MUSTreplace
withactualnativeCOMPRESSIONprofileafterRAW58; don'tship/useas-is.

RAW56: originalobjects ctx20/30 created(pointera031c0520,flags10/20,idEF),
RAW_A/B proceededto15400008STARTpc40c24c88; noexception. 260sourcesaudited
unchanged/stopped. Originalparams p40=10000,44=2,4c/50=0,4088288,4280,D40.
RAW57 derivedprogramc25303a21b6c87b4837002338b9b553d2e28416ae0f67d8b77271fd40e5e07d8,
privatepolicy57replaces48B4105ef78withauthoredC120descriptor{0,5,4140,2756},
ordinary/nullfallbackequivalentuntouched4105f040. Solefounddirectcaller41049084.
Static3normal+3-O pass afterfixingrecordedMOV1originalORRbytes e0030032 and
testnormalization(onlyinternal+32branch,commontail4105efa8muststayabsolute).
**Thispolicy57isnowSUPERSEDEDbytheD0=1requirement;preservehistoricalartifact.**

RAW57nativecompleted/stoppedat15400008pc40c24c88usingOLD4323928aQEMU.
263sources/inputs/program/binaryunchanged audited. Newdims4140x2756correct,
actualQOMfields p40=10000,44=2,48=0,4c4140,502756,54=11,58=800,
D4=0,source8cf3cce00,pitch4088288,target8fae39200,pitch4288288,
descriptors[2,ff,0,0,0]*2,subscription706011f. THIS IS TRANSFERPROFILE.
NoF0originalfirmwareexecutionandnoNEF. metadata2a8runtimeNOTYETMEASURED,
mustNOTinfer0fromoldHSFCformat3. Coordinatorv2corrects tagdependence too:
RAWIFDBitDepthreadsD+4(5=>14);othermetadata/allocatorstillread2a8.

Currentnewpubliccode (NOTnativeused): qemu/nikon-z8-lossless-u16.h pureadapter
u16->packed14nofloat/rescale,highbitsreject,upstreamF0unchanged. 4normal+4-O
componenttests(passC-O0/O2each). qemu/nikon-z8-hsfc-encode.h +Cfields/hooks+
tools/build.py implementdefaultOFFz8-hsfc1540-lossless-model, actualISPRAW
hash/encoded flag, fullsource/readback/F0records,IRQ8; butwrongtransferprofile
asnotedabove. AddedZ8EncodedRecord512,source/modelrecordsnoarchivedpixels.
Modelrequireslatestglobal151target/frame/decoder_job: coordinatordetected
unnecessaryconcurrencyrestriction. Needtesttwo151jobsbeforeencodingfirst +
A/B/A/reallocation and remove latest-globaldependency whilekeeping ownership.
Capacitycurrently22,841,728 declaredtargetfootprint(notD4semantic) andNOT
worstcaseF0bound;shoulduseactualallocationcapacityafternativeproof.

BuiltcurrentcandidateSHA268bd59e7cdb738ce914230d2b6e4ffca666ed70cb682e00a689103a5037cd08,
build/qemu-system-aarch64 (notyetimmutablecopy/frozenmodeldir/buildidentity).
23sources. Earliercandidateebacf365c58727cd070d14bc09819c948658793a1b9ad5f7400725a33d1df61f
usedprovisional44=0/pitch7264/D4capacityprofile;alsoNOTnativeused.
tests/test_z8_hsfc1540_lossless.py current13normal13-O+7ordinaryregression
passin.local/f058/native-normal,native-optimized,native-ordinary-regression.
Full11,409,840samples independentlyF0decoded,hashes/guards;theseareCODEC+
RAMtests ONLY anddonotvalidatenativefileflow. Firsttestmissingwrite_ramfixed;
corruptiontestwritingFFwasnoopatwhitepixel,fixedXOR1;allfailurereceiptspreserved.
tools/z8_f0_reader.py independentfastprefixreaderextractedfromtest; supports
bounded14bit/four2048only. OriginalattributedF0headerstillunchanged.

Newprivatehelpersnotyetusedbydriver:
- graph58_backend.py originallyplannedpreflightat40eabcd0 AFTERnativeRAWwait,
  explicitLR40eabf9c/40eac00c,readsouter6a8record0x90andbufferptr(record+8)16B,
  checksrawcapacityrecord50,descriptor58,rawtarget68,bufferbase/size_raw+8,
  fullRAMcontainment. **CurrentlyexpectsD0=0;CHANGEto1forB.** size_rawmeaning
  notyetruntimequalified;ifitfails,retainactualbytesandinspect,don'tloosen.
  POINTSextendsgraph56_backend;nochangesoldfrozenhelpers.2-Otests.
- f058_observation.py point40c24c8cafterhardwareSTART, exportsactualU16source+
  F0stream,fullindependentdecode,selects2pre(inputqueued)and1post(MCUconsumer),
  excludesambiguousgap,max144MiBreads. Requiresmatchingnativepreflighttarget.
  ContemporaneousISP/encoderrecordsretained;notnativeNEFacceptance.2-Otests.
  ItimportsROOT/tools/z8_f0_reader.py viauniquespecf058_reader;newdrivermust
  addthatrealfiletowatchlistplusnewhelperfiles. Notyetvalidatednormalmode.
- chain57_offline.py replayedactual55cpacket->decoder->NR->ISPall11m,report
  raw-history-55c-partial-pixel-chain-v2.json. Usesactualmodelrecordfields,
  rejectsoutside-runfilepaths/mixedclocks/ambiguousjobs.3-Ounitguards.
  PreFULLinput767346959ns;not0.3swindow. firstv1reportpreserved,preferv2.

Next58needsD0=1leafshim. 48B4105ef78canfitguard4instructions,thenMOVW0#1;
STRW0[X1];MOVW8#5;STRW8[X1+4];MOVW8#4140;MOVKW8#2756lsl16;
STRW8[X1+8];RET (12instructions). ItomitsredundantD+18clear: solecaller
41049084followsnativeclearrecord+18lenD50at41048e38..44,soD+18already0.
Prove/recordthatandcheckD+18zeroatpreflight;ordinaryfallbackf040unchanged.
Alternativeauthoredbodymustpreserveordinarypathandcompletechangedspan.
UseOLDimmutable4323928afornextdiagnostic58,not268bd59,untilactualcompression
profilemeasured. Capture I,D,md26c/2a4/2a8andfull1540samejob;latercompletion
D+18andmetadata1700F0header46B/TAG96/NEFstripactualbytes.

Staticnativeallocation:410457a4 reads a02a244a4+28*size+14*depth+4*comp.
size0rows=[68712960,68712960,48099328,28615680,17310720,
80017920,80017920,59442176,28615680,17310720]. TheyareNOTD4.
mode1class38descriptora02a23bc0=(3432,332328960,1846272,180,38316017152,1,7).
Combinedpoolactualallocationmustbeobserved;don'tequateitwithclass33twoRAWbuffers.
Rawdriver40c24dc0readsrequest+10->44,40c24f38readsrequest+40->D4,
40c24fecreadsrequest+7c->428;thesealoneDONOTdefinetheirsemanticmeaning.

MainparallelworknowhasrealordinaryF0NEFproof (docs/z8-photo-audit.md66-68):
d39e1fd5QEMU,NEF35,111,661B,stream34,339,053B,LibRaw45,705,600zeroerrors,
NEFSHAd211e23939779a61bf5689e9ae7a5dfe09e4ac5b0a917e485a0c5f7ee3c3dfbc.
Thisisotherwork'sevidence,NOTourprecapturerun. DoNOTtouchmain'schanges.
CoordinatoralsoflagsmissingHSFCJPEGattachmentsteps,observerquotas,allocation
generationlifecycle,36-countwindow(30Hz=>~1.17s). Followupsourcefilesabove.

## LIVE checkpoint17:14 Taipei: RAW58 native compression diagnostic

**execsession39571 running**,started17:14/09:14UTC. Newdriver
half_history_control58_probe.py,reviewcontrol58-review,runraw-history-58under
20261014. OldimmutableQEMU4323928a/half55sources/pool55memory (NOT268prototype).
Newprogrampolicy58/simulation-main-c120-native-compression-not-an-update.bin
SHA**ee7d6fef558adc6ec33966fe2d207f3d2433fc88abe19e4818bc7ef04d0de5c6**.
OnlyD0=1nativecompressdescriptorvs57in48Bleaf; returnW0=1usedforD0store,
D+18clearomittedbecausesolecallerfreshnativeclearrecord+18lenD50. 3normal+
3-Ostaticpolicytestsverifyall12instructions/fulldiff/nativeclearcall.
raw58_context.py adds2readonlypoints:40eabcd0originalRAWbodyentry with
LR40eabf9c/40eac00c,40c24c88BEFOREencoderSTART. Itdouble-readsactualI48B,
D32B,md{0,120,26c,294,2a4,2a8,2fc,286c},record50/nativeallocation16B.
AtSTARTjoinsuniquecontextbyactualI+18source andD+10target againstfull1540
getters,observesfallbackD0changeifany. Allocation.size_rawisnotclaimedABI
capacityyet;diagnosticoldmodelcannotwritehistoricalF0. 3normal+3-Otests
samejob/nonassumed2a8/fallbackupdatedD/unboundtargetrejectpass.
-Ocheck185passed;samecontacts/limitsandoldmodel,onlypolicy+2points.
No58imagesviewedyet. WATCHfresh4images;approveonlyhealthyC120RAWL/PRE.3s/
soundOFF. All58runtimefilesfrozen. Donoteditpolicy58/raw58_context/tests.
Afterrun useaudit_current_run.py raw-history-58 (freshauditpath)andinspect
raw58-context.json plusactualengine1540properties. DetermineD0still1andnew
profilebeforeadaptingF0model. Preserveall57/268prototypeevidence,retraction
recordat.local/f058/profile-status.json;docsupdatedcriticalcorrection.

## LIVE checkpoint18:27 Taipei: RAW59b native F0 output/length/header run

**LIVE execsession38235**,started18:27Taipei/10:27UTC. Runraw-history-59b,
reviewcontrol59b-review,driverhalf_history_control59b_probe.py. NOUIimages
viewedyet. MUSTwatchfresh4PNGandreviewC120RAWL/PRE.3s/soundOFFbeforeapproval.
Samepolicy58ee7d6fef558adc6ec33966fe2d207f3d2433fc88abe19e4818bc7ef04d0de5c6
withD0=1 nativecompression. NEWimmutableQEMU:
**1f262d529df27c7bf2093e475142616f7214fe8f4bbcf15d0a95035840100a8e**
.../raw-shared146-work-20261014/build/qemu-system-aarch64-f059-1f262d52.
23frozenHERE/half59-model-sources,pool59_memory.py,newreadonlyobserversbelow.
All59bruntimesourcesFROZENincludingf059b/reader/poolcandidate/raw59helpers.

RAW58 completed/stopped atactual1540STARTpc40c24c88;268watchedsourcesunchanged
audited. Same-pausedraw58-context.json PROVED D0=1,D4=5,width4140,height2756,
Iformat4,stride8288,source8cf3cce00,target8fae39200. Actualmd26c0,2a40,2a8**0**
nowMEASURED(thisrunonly),nativejobmd120=1. NativeRAWreservationrecord50=
68,712,960; bufferdescriptor a11852440 rawtuple(base8fae39200,size79,389,696,
flags0). ActualallocationABI+8isstillnamedsize_raw; don'tpretendgeneralproof.
QOM1540p40=10000,44=0,D4=22,841,728(15c8980),408/4288288,54=11,58=800;
source/targetabove,fullnativeHuffmantablecaptured. D+18was0beforeSTART.
Savedroot.local/f058/native-compression58.json isactualsamejobprofile.
NO F0raninRAW58 (old4323928aused),NO NEFyet.

RAW58 packetpreFULLsamples wereIDs31,32,firstdecoder/F0sourcewas13; final
packet51. Thuspost-FULLsamplelikelyrequiresprocessing~20historyframes;old
observerlimitswillnotautomaticallysupportthat. RAW59bisFIRST-JOBclosure,
notwholeburstacceptance. Keepraw_precapture_verifiedfalse.

CurrentCmodelcorrectedAWAYfromtransfer: mode44=0,D4=inputfootprint,includes
exactnative15codetablewordsand15lengths. Explicitnegativeactualtransfer
(44=2,D4=0) nowrejected. Earlier268bd59transfer-F0prototypeNEVERnativeused,
nativeassumptionretracted.local/f058/profile-status.json;failurespreserved.
Currentcapacity=68,712,960 isactualRAW58record50bound,NOTD4. Newnativebefore-
STARTobserverrequiresactualcurrentrecordreservation==thatcapacityandfits
actualallocationtuple+realRAM. ThismodelprofileisnarrowC120FX,notuniversal.

Per-jobownershipfixinC:Z8ISPRecordvalidmeansRAWvalid;preview_validseparate.
Real151W1Cbit2ackmarksitsownrecordacknowledged. F0requiresuniquevalidowned
source/currentfullSHA/ack/!encoded; removedlatestglobal151target/frame/job
andglobal151pendingconditions. OldackedRAWmayencodewhilelater151pending.
Any151write retiresonlyoverlappingRAWorpreviewcomponent; firstrevisionused
wrongoldarrayindex(a0Yvsactuala2RAW),testcaughtandfixed. Testtarget880000000
withnew68MBcapoverlappedretainedpacket884000000; movedAUTHOREDtesttargetto
8c0000000,keptoverlapguardstrict. Originalruntimeactualtarget8fae...unchanged.
Tests final .local/f059/normal2 andoptimized2 **19eachpass**, including2ISP
completionsbeforefirstF0,olderACK/newerpending,A/B/A,targetreuse;full11,409,840
F0samplescompareactualU16. isp-regression2 12pass,ordinary-regression2 7pass.
Alltestguestsstopped;buildidentity.local/f059/build-identity.json binds1f262.
Earliernormal/optimized19failuresretained(beforeindex/testaddressrepairs).

Observercandidatecopiedbyte-identicalfromcoordinatorraw-observer toHERE/
observer59. Currentbasepool14sourceSHA7160cb...matchedsourceevidencebefore
copy. 21targeted-Otests passed; legacy38poolpassed ininitialcombinedrunwhose
other5testimportsfailedbecausemigrationomittedoldfixtures. Fixedtestharness
withlazyMetaPathFinderandONEsuiteperfreshinterpreter,readonlylegacyfixtures
inoldmainHERE. Additional5driver+27control15+12control16+2control22+2control25+
17aux21passed=103legacy-Ototal. Initialharnessimportcollisionfailuresretained
intoolhistory;sourceoldpool14_sessionunchanged. NewcandidateONLYadmitsexact
{d8_empty,pairedWAIT_CALL}2CPUwithnootherpendingpairs,retainsallguardsand
unorderedmeaning. Actualmixedguestbatchnotyetobserved/accepted. RAW59bnow
importsobserver59/pool_multihit_plan.py +candidate_pool14_session.py AFTER
freshimportgateandnewpool59memorybinding;allactualfileswatched.

Newraw59_context.py reusesfrozenraw58entry/startsnapshot, adds:
40e8508c AFTERD+18store(actualDpointerX13,storedW9,returnW0),
40eabf68 afterheadercopy/beforeepilogue(X20ctx,X19outer),
40eb16a0 TAGentry(knownctxonly). BeforeSTARTrequiresD0=1/format5/geometry/
mode44=0/D4footprint,zeroD+18,actualRAWcap68,712,960andcontainedallocation+
RAM. Laterbindssamemd120/record/ctx,requiresD+18==actualencodedrecordbytes,
headerlength46,prefix46300008000800080008,rawflag19=1. SemanticerrorsRETURN
diagnosticrow,driverpersistsTHENstops;nohostreturn/flagwrites. Readerrorsfail.
30newmetadatasamplescap=firstjobdiagnostics;olderqueue256/backend72/decoder9
etcSTILLnotmulti-framecomplete. 2normal+2-Ounitguards(pass).

f059_observation.py/f059b_observation.py readonlyactualsourceU16+F0exports,
independenttools/z8_f0_reader.py decoderallpixels. Callerbindusesoriginal
START/return SP,LR,X0andaddress/time (NOTlatestmodeljob),thenownISPparentACK.
2pre+1postselection,max272MiBreads,requiresnativepreflightsamejobtarget/source
andafterISPcompletion. NoNEFacceptance. 2normal+2-Otests;fullcollectorreal
executionpending. Sourcechecker beforeQEMUlaunchrejectedRAW59becausef059
importedROOT/tools/z8_f0_reader.py (unfrozenprojectmodule). Preservedempty
control59-review andraw-history-59-prelaunch-failure.json. NoRAW59QEMUstarted.
prepare_control59b.py frozeBYTEIDENTICALreadertoHERE/half59-validator-sources/
z8_f0_reader.py SHAe943633da72d8d69644449a8e5222f360a81163be8b0d3f86ad482a9926cf8b4,
newf059bhelperimportsprivatecopy. 59bmodel/program/contacts/pointsunchanged.
Both-Ocheck185and4hostguardtestspassed,thenlaunchedsession38235.

After59b: audit_current_run.py raw-history-59b; inspectf059-evidence.json,
raw59-context.json,firstUNKNOWN/originalexception/capfailure. Checkactual
D+18andF0headerbeforeNEFclaim. ExportactualcardwithACTUALpolicy58program,
LibRawindependentallpixels/strip/header,neverhostrepackasnative. Original
NEF/hardware/multi-frame/windowacceptanceallstillpending. DoNOTmarkcomplete.

OneWSLtestrequestqueued-generations-redautoapprovalTIMEDOUT(noexecution),
retriedonceasallowedandranredcorrectly. No safetyrejection/userapprovalneeded.

## LIVE checkpoint18:54 Taipei: RAW59c unchanged retry after frontend failure

RAW59b ended/stopped BEFOREhistoryHALF:actualhistory-half.png showsS/Error,
notC120. NOapprovalwritten. Modegatecorrectlyfailedcache4/capacity36;
182inputsreturned,queue_max0. Audit282sources/currentinputs/program/binary
unchanged/stopped. NoUNKNOWN,poolfirst_failureNone,mixed_batchesNone;
HSFCpackets0,decoder0,ISP0,F00. NoF0nativeexecution/NEFclaim.
4actualimagesviewed:menusSoundOFF/Lossless/PRE.3svalid,buthistoryS/Error.

Readonlycomparison58vs59b:bothhadearly35@40e9898c at~15.9s;healthy58input
boundarycompletionslaterrestarted135+ at176.55safterraw-leaveHALF,then196+
afterleave-menu.59b remainedcompletions/acks133at179.36s;its35at177.99s
followed70@40cc2014at179.364s. NOcode28samplesineither. Thisdoesnotprove
cause;data-modelpathsnewin59wereinactive. Oneunchangedretrychosen.
Donotblindlyportupstreamactive-programtoprimarypreview:currentlyNV16ready
usesactual151jobs/addresses,notD980frames;active-programsourceusewasVIDEO.
Hotupdatesofprimarytargetsandtheircommitsemanticsneedactualevidence.

**RAW59c LIVE execsession13589**,started18:46:44Taipei/10:46:44UTC.
Driverhalf_history_control59c_probe.py,reviewcontrol59c-review,runraw-history-59c.
Same1f262immutableQEMU,ee7d6policy58,models/points/contact185/limitsunchanged.
prepare_control59c.py verifiesall59bsources;newprovenancecontrol59c-provenance.
-Ocheckpassed. At18:54sound-settingandraw-leave-halfACTUALLYviewed(OFF/
Lossless). leave-menu-half/history-halfNOTyetviewed. Watchfreshremaining2
andapproveonlyhealthyC120RAWL/PRE.3s. Ifthisfailsagain,investigate/isolate
frontendinsteadofrepeatingunchangedindefinitely. ALL59cruntimefilesFROZEN.

StaticJPEGread: capture_nv16_prepare/try keysactual151capture_jobs andNV16
addresses/geometry; noD980frame>0gateatthatstage. Concurrency/latest-source
restrictionstillpossiblebutnotobserved. GraphreportordinaryJPEGcol6row0
init40eae96c equalsHSFCinit,HSFCrow0seqonly40eae9c8; ordinaryalsoef20,
eb07e8,eb04c8,eb079c,eb1228,eb1338. row1/2omitf63c/f660andeb12d0/eb1304.
NoJPEGgraphchangesmade;waitactualF0completion/TAGevidencebeforepatching.

## RAW59c stopped; RAW60 frontend diagnostic running

RAW59c ended at the existing pre-HALF mode gate: history-half screenshot
actually viewed, C20 / Error, NOT C120. No approval written. All four UI
images were viewed. Audit raw-history-59c-audit.json verifies 283 watched
sources plus program/input/binary unchanged and owned QEMU stopped. 182
contacts returned, queue_max 0. No UNKNOWN, no pool failure or mixed batch;
HSFC packet/decode/ISP/F0 all zero. RAW59b similarly stopped at S/Error.

Final raw preview frames: RAW58 719, RAW59b 133, RAW59c 177. Bit3/31/5 and
1514/151c completion notifications all acknowledged, pending zero. 59c
1514 and 1543 block0 active, 1543 not held, 151c config30=1; 150c active
212742, stop requested/done/pending zero. 59b final 150c inactive and
151c config30=0. These final states may include firmware cleanup, so are
not proof of the original cause. Existing receipts omitted event timing
and primary reset state at the first failed wait. MMIO log final writes
disable 1508 source; ordering versus failure requires timed observations.

Frontend component regressions on immutable 1f262 all passed: 19 frame
boundary, 20 frame sync, 20 input boundary (.local/frontend60). Native
restart remains unresolved; no encoding path executed in 59b/c.

RAW60 adds input60_observation.py, wrapping the unchanged input53 observer
at the SAME PCs. Only query-status / qom-get; clock/status consistency;
compact actual event timing, global1, reset, 150c, 1514, 151c, 1543 and
1542 pending states. No MMIO reads or guest writes. Primary hold register
is not exposed and is explicitly outside this diagnostic. Existing full
failure snapshot also gains event timing/global1/reset. Six -O tests pass
(includes inherited input53 guards); -O driver check passes 185 contacts.
prepare_control60.py verified all RAW59c watched files before creating a
new driver/provenance. All RAW60 runtime files are now FROZEN.

Active native exec session4081: half_history_control60_probe.py,
run raw-history-60, review control60-review. Same policy58 ee7d6fef...,
immutable QEMU1f262d52..., contacts, models, limits and breakpoints. Only
read-only diagnostics changed. Inspect fresh screenshots and approve only
healthy C120/RAWL/PRE.3s/soundOFF. After completion run audit_current_run.py
raw-history-60. No historical native NEF or precapture acceptance yet.

## RAW60 completed: FIRST HISTORICAL RAW NEF, partial file/burst acceptance

Session4081 stopped with strict UNKNOWN cpu0 PC40c26848 write15432004=103.
This address is **C1 (block1)**; block0=C0=15431000, block2=C2=15433000.
Do not repeat the earlier inspection mislabel C2. Audit286 watched sources,
original/derived programs, QEMU/input unchanged, owned process stopped.
184 queued/consumed/returned contacts; FULL occurred, release not reached.
Queue max36. Decoder3 jobs, historical1512 jobs, F0 one job. No native
exception or relaxed guard. Exact native F0 input/source at8cf3cce00,
target8fae39200; output **8,587,458B** SHA
f4eeef8c425dbfc412f8b6cc5b7ed33fa329638888b5ffa401a41c50b8d46521.
Actual source22,841,728B SHA0b651a47dbfc1b7f61314537c924f527879a47f15d6d30826e7f4ac762fe9f94.
All11,409,840 samples matched independent F0 reader. Source frame13 captured
254464254466ns; FULLqueued255232929155, consumed255232985825; pre queued
768674689ns. This does NOT meet PRE.3s.

Same native contextc43296000/outera10d37d10/recorda118c63b8/mdc4012c008:
D0=1,D4=5,4140x2756; actual reserved68,712,960 verified beforeSTART.
40e8508c sees D+18=8,587,458 and native success. TAG40eb16a0 sees same
length, raw_completed_flag1 and46B F0 header; all checks pass. Header point
40eabf68 was UNBOUND because X20 is1 (notctx); X19outer and X22D are actual
bindings. It correctly did not invent context. TAG supplies valid header proof.
Next observer should bind this point via X19outer/X22D, not X20.

RAW60 frontend diagnostics: both menu exits restarted and C120 gate passed;
all4 screenshots ACTUALLY viewed and review written. No frontend behavior
changed, so 59b/c failure is not repaired. Existing early15.917s wait failure
has event timing OFF, all completions ACKed; later input timing resumed.

Actual card contains DCIM/100NCZ_8/DSC_0001.NEF **8,787,650B**, SHA
f6f45650f943cc5013b6d6fba2342456f11af369abbcf4b33698bb994ecdfe37.
RAWstrip exactlyequals full same-job F0 RAM; Bits14,Compression34713;
ExifTool12.76 finds46B MakerNote96 identical to native TAG snapshot;
four2048; packed-size fallback excluded. rawpy0.27.1/LibRaw0.22.1 decodes
2756x4140 and **11,409,840 samples ZERO mismatches** against actualU16.
Receipt HERE/raw-history-60-nef-audit.json. This is original guest-written
card data, not host repack. Source/file/card/program hashes retained.

**FILE INCOMPLETE:** declared large JPEG IFD85102 hasoffset200192,length0.
160x120(6621B) and1620x1080(112288B) previewsdecode; no individualJPEGjob
byte matching yet. whole_run_accepted=false, raw_precapture_verified=false.
RAW60 log hasone NV16 generation; 15e2 tenjobs (7count-only) commit middle,
640x424 andthumbnail, NOlarge main JPEG. This confirms coordinator's omitted
40eaf63c/40eaf660 concern. NeedminimalHSFCJPEGsequence repair before rearm.

Standardexporter refuses RAW60: result33,758,471B exceeds32MiB AND runfault.
No exporter guard changed. card60_forensic.py binds exact resultSHA
3c30271682b1c078e7954ef8c68c09c1b511af2cb2a393df74b66c6f21dfdbde,
preservesfailure and audits separate sparsecopy; fsckclean, completecatalog,
unchangedactualcard/volume, no acceptance. HERE/raw-history-60-card-forensic.json.
Linux ../raw-history-60-forensic-card containsauditedcopy,volume andthree
byte-identical NEF extractions. FirstauditfailedmissingexiftoolPATH; second
failedonemptylargepreview AFTERfullRAWcomparison. Bothscripts preserved
audit60_nef_v1_exiftool_missing.py /v2_empty_preview.py. Finalaudit60_nef.py
reportsRAWsuccessANDpreviewfailureexplicitly. Usesread-onlymainprivateperl:
z8-qemu/.local/migration-20261014/exiftool/unpacked/usr/{bin/exiftool,share/perl5}.

Upstream c6dbfc4 hascapture-stream-rearm-model with strictC1held/no pending/
all3JPEGconsumed conditions. MissingmainJPEG meansdon'tblindlyenableit.
Our C1 finalactive=true,held=true,pendingA1/C1/1518all0, butcapture-nv16-state
notrecorded. Addgetter nextdriver. Model rearmnotported yet.

NEXT: minimalJPEGHSFCrow1/2 insertion40eaf63c/660 afterexistingfirststep;
doNOTcopywholeordinaryJPEGcolumn. Staticinit40eae96c (samebothgraphs)
setsctxB8,createsctx30 onlymd2fc1; actualmd2fc0. Existingfirstworkers
40eaeed8/eaeefc call40eaea0c; insertedhelpershaveidenticalctxB8/worker
selection, useouter688/690 andrecordF8/100, nativeAPI40e82090. Ordinary
extra40eb12d0/1304 onlyapplymd2fc1 andcanbeleftaloneforcurrentprofile.
Needtestboundedsequence/ordinaryunchanged+originalinitdependencybefore
newpolicy61. No newnative or tests running. No commit/push/flash.

## LIVE RAW61 plus separately frozen C1 rearm candidate62 (20:09 Taipei)

RAW61 execsession1481 ACTIVE, startedaround11:54UTC. QEMU remains1f262d52,
newprogram policy61 SHA4de3d2169ea0cae4cae990d80217ede5f146afdb0f17d9123f4284d65ce25999.
Only insertsnative40eaf63c/660 after firststepinHSFCseq a02ace460/a02acebe0;
keepsnodeowners,initializers,dispatcher,ordinarytablesandotherHSFCsteps.
Sequences6→7entrieswithinoriginalzero padding; one unique static owner each.
TDD oldpolicy58failsmissingstepasexpected;3normal+3-O policychecks pass.
2normal+2-O JPEGobserver tests pass. Newjpeg61_observation.py points554
beforeAPI and574 afterrecord1e=1;capturesnativepacket,recordF8/100,jobid,
capture-nv16-stateand15e2state, no JPEGsuccessclaim. 12snapshotscap.

raw61_context.py fixesHEADERbinding:actualX19outer+X22D→uniqueexisting
requestctx. Never changesregs/event.2-O tests replay RAW60actualheaderregs
andTAGbytes;rejectunknown/duplicateowner. 61driveraddsfullgettercapture-
nv16-pipeline-state, usespolicy61, retainsalllimits+QEMU+models+contacts.
check185passed. ALL61runtimefilesFROZEN. soundOFF,RAWLossless,PRE.3sscreens
ACTUALLYVIEWED. history-half NOTYETVIEWED, noapprovalwritten. At~12:05UTC
step170,frames238andnativewaitsuccess. Watchcontrol61-review/history-half;
approveonlyhealthyC120RAWL/PRE,thenwaitfornativeJPEGresult. Auditorafterstop.

Independentroot-sourceworkwhile61usesfrozenbinary: adaptedupstreamc6dbfc4
capture-stream-rearm-model. qemu/nikon-z9-lab.c nowincludesdefaultOFFimmutable
bool/counter/getter; requirescapture_nv16. OnlyC1active+held,exact103 command,
oldcontrol102/latched101/acceptedfiniteheader, actualNV16job/no request/fail,
all3JPEGconsumedcurrentgeneration, zeroA1/C1/1518pending ANDzeroJPEGrequest/
status/corepending. This is stricterthanupstream, noVIDEOheaderadmission.
Rearmonlyclearsactive/heldandcounts;noRAM/IRQ/pending/free/taskflagchange.
C0stopguardsunchanged. Native60missinglargeJPEGwouldstillfail thisguard!

Newtests/test_z8_capture_stream_rearm.py actualQTestsourceD980→151→3NV16
outputs→3JPEGdecodes→W1C→rearm. Preservedredfixtures: red1scaleoverflow,
red2missing1518reset, red3wrongblackpixelassumption. red4reachedproperold
UNKNOWN15432004=103 AFTERallactualconsumers.14normal2+14optimized2pass;
22C0regressionpass. Added missingeachJPEG/pendingeachsource/input/receiver,
JPEGstatus/coreACKsplit,unheld/wrongcontrol/defaultoff/immutablecases.
Docs/z8-capture-stream-rearm.md describesupstreamadaptationandlimits.

freeze_model62.py createdhalf62-model-sources(23files),pool62_memory.py,
control62-model-provenance.json,.local/rearm62/build-identity.json.
QEMUSHA **e35150ac03ab24598cf231829f7dd6a33cd5cab192708e7a7289049e2ed939bb**
immutable.../build/qemu-system-aarch64-rearm62-e35150ac.
Onlyqemu/nikon-z9-lab.c differsfromf059source;memory_regioncallsunchanged.
Actualrunning61binary1f262untouched. No62nativedriver/runyet.

READMEstatusdocdocs/raw-precapture-shared146.md updatedwithactual60NEF
RAWintegrityANDemptylargeJPEG/burstfailure. Needupdateafter61. No commits.

## LIVE RAW62 at20:24 Taipei; RAW61 complete single historical NEF validated

RAW61 session1481 ended strictUNKNOWN sameC1 write15432004=103.
292runtime sources/program/binary/input unchanged and ownedQEMUstopped.
184contactsreturned,queue205samples,max36,RAW1512jobs. All4UIimagesactually
viewed and approved. F0headerbindingX19outer/X22D nowverifiedat40eabf68;
TAGalsoverified. JPEG61 nativeAPI40eaf554/574 executedfornativejob1,
reservation7,384,576, rawmainready0→1, actualencoded129,178B,
source862179a00/862c64a00,target8fefc0c00,4128x2752,stride4160.

RAW61completeforensiccardaudit +LibRaw/ExifTool audits passsinglefileRAW:
NEF **8,917,186B** SHA
**2ee864f54c10b84beeb9fe12a460a39c1a4287315fc38d30093ff09d259033b0**.
RAWstrip8,587,458 exactlysame-job F0RAM.11,409,840samplesZERO mismatches.
All3previewsdecode:160x1206621B,1620x1080112288B,4128x2752129178B.
NoemptyJPEG;individualnativeJPEGjobbyteoraclesstillnotexported.
Frame13preFULLqueued767077294ns (not.3secwindow).
HERE/raw-history-61-card-forensic.json andraw-history-61-nef-audit.json.
Linux../raw-history-61-forensic-card/DSC_0001-raw-audit.NEF actualextraction.
result34178503B SHA64849f03a603ee2ff3c67cbdaae2a67f31cfd95422364f8fd5ce551e05c62013.
Noexporterguardchanged;stoppedfailedruntimestillwhole_run_accepted=false.

Actual61capture-nv16-pipeline-state:jobs1,requests1,requested=false,
failed=false,consumed151job1;source/receiver/inputpending0;outputs main
4128x2752stride4160,small640x424stride640,mid1620x1080stride1664;
ALLjpeg_consumed_job1. C1active/held, old102/latch101. Thisisactualnative
supportforcandidate62rearmguard (60missingmainwouldnotqualify).

RAW62 **ACTIVE execsession32031**, startedaround12:20UTC. Driver
half_history_control62_probe.py,reviewcontrol62-review,runraw-history-62.
policy61program4de3d216...UNCHANGED;newimmutableQEMUe35150ac... and
half62-model-sources/pool62_memory;onlyaddedcapture-stream-rearm-model=on
plusgetter. prepare_control62.py verifies61sources beforecreation, -Ocheck185
passes. ExistingALLdiagnosticcaps unchanged, so thisisfirstnativeC1rearm/
nextfilesexperiment; maystopbeforepost-FULL becausecaps. No62screensyet
viewed at12:24UTC (step043). Watchfour freshscreens;gateC120required.
Noothernative/QTestsrunning. ALL62runtimefilesFROZEN.

Budgetplanning factsfrom61: trace2924events, FULLqueuedseq2808, only116
eventsafterFULLatfirstC1failure.1MiBmetadatacharged179487B/3512requests.
Queue205samples/256 (save_queue silentlystopsnonforcedsamplingatcap).
Firstdecoder3/ISP2/F01 andbackend20snapshots. Bottlenecksfornextmultiframe:
NR54cap6 (2points/job),decoder39cap9 (3points),decoder41cap12(4points),
pool50cap9,backendcap72,RAWcap30(5points/job),JPEGcap12(2points/job),
HSFC34cap36,primary35cap160;traceglobal4096. Needjob-scopedobservation
design beforeclaim2pre+1post. CurrentFULL10seconds isinputhold,notmenupost
duration. Decoderfullpixelsmax2,ISPmax1,F0collector2pre+1post/272MiBread.
Don'tsimplyremoveguardsorpretendallfilelifetimesobserved.

## Observation correction at12:30UTC (RAW62 stillactive)

Important: existing CapturePeer tracepoints are ONE-SHOT PERCONTACT, not
automatically repeated per job. service_z9_mcu.WritePeer.handle disarms each
globalPC;CapturePeer.begin rearms trace_points onlyatnextcontact. Caps above
aloneDO NOT describehowmanyjobswillbeobserved. A successful62couldwrite
manyfileswhilef059hasfirstsampleonly! Do not infermulti-oraclecoverage.
Needbounded repeating entry/successor observationforRAWbody/1540START-
return/TAG (with actualCPU/SP/LR,codeguards,uniqueownershipandmixed-hit
rejection), notsimplyincreasecounts. ExistingRAWbodyeabcd0 needsnewnext-
instructionpoint to rearm; 1540START/after alreadyadjacent88/8c; TAG16a0
needsitsownsuccessor. Keepoldone-shotdiagnosticsseparateandunchanged.

Proposed(notimplemented)nextmulti-scope: up to64nativeF0jobs,actualsource
U16 exportbeforewriterforeach (22,841,728B/job;1.5GiBboundedtotal), tie
nativeallocation/context/metadata120→ISPjob/frame→sourceSHA→actualNEFstrip.
Keep2pre+1postfullF0componentreadbacks272MiB; LibRawcompareeachfileagainst
itsownprewriterRAWsource. Forfullpacketlineage, exportactualpacketatdecoder
completionbeforeitsreuse, up to64*0x414800 (~261MiB), independentlydecompress
andcomparepixeloracletoF0source,withoutduplicatingall22MBdecoderoutputs.
Recordstatedgapsifnooracle/noassignedwriter/lifetime,don'tmarkaccepted.
This isplanningonly;noneoftheseobservers/limitsimplementedyet.

At12:30UTC62step168 release-mode-hold;frames200/clockactive/nativewait1.
SoundOFFwasviewedearlier;raw-leave-half andleave-menu-halfnowACTUALLYviewed
(Lossless/PRE.3s). history-halfnotyetavailable/noapprovalwritten.

## RAW62 STOPPED, next boundary is SECOND ENCODER 153c (12:43UTC)

All4RAW62screens actuallyviewedhealthyC120RAWL/PRE.3s/soundOFF;reviewwritten.
OneautomaticapprovalTIMEOUT forreviewwrite (CreateProcess neverexecuted),
allowedoneidenticalretrySUCCEEDED. No pendingpermissionblock.
RAW62session32031 endedstrictUNKNOWN cpu0 PC40c24cbc WRITE153c01b4=0.
Audited295runtimefiles+program/binary/inputunchanged andownedprocessstopped;
184contactsreturned,maxqueue36,RAW1512jobs,decoder3,F01. `capture-stream-
rearm-state.rearms=1`: nativeC1held102→103SUCCESS,thenRAW_B reachesother
encoderregisterbank. Do notclaim2savedNEFs. No activeQEMUorunit-test session.
result34253520B SHA4e03344a72f1e68cd4472f960360204e098c17e534b33ece4238c2384e2c2694.
Card62notyetaudited;likelyfirstfilebutnotconfirmed. Canforkcard61/audit61
scriptswiththisexactresultidentityandrunname;executedprogrampolicy61same.

ActualCPU0afterfaultPC40c24cc0 X00=a02c973e8 X01=a06f50d90 X10=153c0000,
X20=a02f25b70 X21=a06f50d08 X24=a02f25b70 X25=a02f25e40
X30=40cb5c44 SP=a06f50ca0. Originalencoder-driver.txt staticconfirms
40c2459c buildsobjecta02c973e8(base153c0000,type180), registersINTID130
at40c245f4;objecta02c97418(base15400000,type181) registersINTID131 at
40c24648. BothuseSAMEcallback40c24658 and leafsetters. Thus mustmodel
153cseparatestate/IRQ/results, notalias1540completion.

Currentmodelonly1540 has10descriptors,params[0x42c/4],subscription/control,
F0DMA/IRQ131. Legacyengine_init_wordalreadyhasengine_pair_init[0]for153c
and[1]for1540,onlyoff0/4/44. New153chandlermustshareitsownlegacy44shadow,
preserveboot0/4 path, andrunBEFOREgenericengine153/legacyaccesses.
Couldreusepure1540 descriptor_address/parameter_address MASKvalidation
withnormalizedoffset; doNOTsharemutable1540params/pending/results.
Neededseparate153cparams/desc/interface/subscription/pending/jobs/bytes/
acks/result andIRQ130. DefaultOFFZ8specificmodel, requiresHSFC1540F0existing
models. StrictsameobservedD0=1U16/F0profile initially aslimitedhypothesis;
nativepreSTARTmustcaptureactual153cprofile/allocation beforeDMA, reject
anydifference. DoNOTclaim153cprofilealreadymeasuredfromRAW62partialwrites.

Suggestedimplementation(notstarted): factorqemu/nikon-z8-hsfc-encode.h
puretransactiontoacceptconstparams/desc/mode/pending+encoderbase andreturn
completedZ8EncodedRecord*. Keepactualsource/owner/capacity/Huffman/overlap/
readbackguardsidentical. Wrapper1540 updatesitsoldstats/IRQ. New153cwrapper
usesitsownstats/IRQ. SharedHSFCgenerationrecordscanaddencoder_base/FNV;
parentencodedpreventssameRAWgenerationencodinginbothbanks. QTestmustprove
bothpendingIRQsindependent,mask/W1Conebanknotackother,2actualRAWsources,
sameparentdoubleencode/transfer/wrongprofile/capacityreject/defaultoff.
CurrentrootQEMUstillfrozen62e35150ac,sourceonlyrearmchanges;no153ceditsyet.

Private repeat63_plan.py +test_repeat63_plan.py added (NOTnativeintegrated).
5-OtestsPASS. Pureplansentry→next,sameCPU/SPdelta/X0/LR;rejectmultihits,
timebackwards,missingCPU,overlimit,doesnotmutateinputstate orperformI/O.
Actualguardedwords/points:
BODY40eabcd0 ff4304d1 SUBSP110 →40eabcd4 fc6b00f9 (SP-110)
START40c24c88 280900b9 STRW8,[X9,8] →40c24c8c c0035fd6 RET (SPsame)
TAG40eb16a0 fb6bbba9 STPX27,X26,[SP,-50]! →40eb16a4 f96301a9 (SP-50).
NoI/Oadapter,producersnapshotcollector,newlimits,native63driverexistsyet.

Nextpriority: secondencoder153cstrictsupport+tests, thennativeintegration
withrepeatingRAWobserver/actualallocationpreflight foreachbank. Native62
provesC1rearmonly; first61historicalNEF remainsstrongestcompleteRAWfile.

## Dual encoder63 compiled/tested/frozen; no native63 or live tests

Implementedqemu/nikon-z8-hsfc153c.h, modelz8-hsfc153c-lossless-model:
Z8onlycreation/defaultOFF;requiresexistingHSFC1540F0dependencies. Own153c
params[42c/4],10descriptors,interface/subscription/pending/result/jobs/bytes/
acks,IRQ130. SharesONLYownlegacyengine_pair_init[0][2]mode44;0/4bootreset
unchanged. Reusespure1540offsetMASKvalidation, notmutable1540state.
Read/writeinterceptbeforegenericstrictMMIO. Full153cnativeprofileSTILL
unmeasured;currentexpectedprofile sameRAW58U16/F0 andnewnativepreflight
mustcheckactualbank/config/allocation beforeanySTART.

Refactoredcommonhistoricalencode inqemu/nikon-z8-hsfc-encode.h toaccept
params/descriptors/mode/pending/base andreturnactualZ8EncodedRecord*.
Original1540wrapperretainsseparatecounters/results/IRQ131. Sharedbounded
HSFCrecordsnowincludeencoder_base+output_fnv64; parentencodedprevents
samegenerationconsumptionbybothbanks. Existingall14-bit/source/ownership/
capacity/Huffman/overlap/fullreadbackguardsretained. Oldlogprefix1540kept,
newbanklogsZ8_HSFC153C_F0.

Tests initialcandidateSHA836d44d138d9cba7942153f2e68284e4091c6fd36ac91431eceb54c4f82ab844
passed11new+19oldhistorical, BUTnewcross-banktargetoverlaptestFAILED:
otherbankcouldoverwritependingoutput. ThisbinaryNEVERnativeused.
Fixedholdingfullpendingreservationotherbank, bothdirections, pluscannot
overwritestill-pendingencodedmetadataon512recordwrap. Lasttarget/capacity
independent perbank; normal1540pathalsogetscross-bankprotection. Onepatch
initiallyinsertedguardinto1544instead1540;buildcaughtforward-declerror;
reverted1544andinsertedatuniquep[D4]check; finaldiffcorrect.

FinalimmutableQEMU SHA **de44312c8d0b6a714d779efeeff32865dd964d6985a206be388821525746f33c**
.../build/qemu-system-aarch64-dual63-de44312c.
.local/dual63/normal2 **15pass**,optimized2 **15pass**,
1540-regression2 **19pass**,ordinary-regression2 **7pass**. Testguestsall
stopped. IncludesactualtwoRAWsources,independentpendingIRQ130/131/results,
mask/W1Cisolation,sameparentreject,transfer/corrupt/missing/badHuffman,
pendingreprogram/defaultoff/immutable, bothcross-banktargetoverlap,
unusedtailoffullreservation,ACKedcross-bankreuse.
Redbaselineold62correctlyfails153c01a4=2 (red2); firstredonlyfailedmissing
newQOMstate,kept. overlap-redretainsactualoverwritebugfailure.

freeze_model63.py createdhalf63-model-sources(24files),pool63_memory.py,
control63-model-provenance.json,.local/dual63/build-identity.json.
Memory_regioncallsunchanged;newIRQ130staticC3.11registrationverified.
tools/build.py tracknewheader;prepare_source.pyglobcopiesitautomatically.
docs/z8-hsfc-dual-encoder.md updatedwithscope/tests; nohardware/nativeBclaim.

RAW62cardforensicDONE: exactlyone8,917,186BNEF,cleancompletecatalog,
unchangedcard/program/result. HERE/raw-history-62-card-forensic.json,
Linux.../raw-history-62-forensic-card. No62LibRawrerun/extractionyet (61
alreadyindependentlyverifiedseparaterun; doNOTtreatas62pixelsproof).

Nextwork: implementactual-bankread-onlyraw63inputs/contextandbounded
repeat63I/Oadapterbefore63native. Existingone-shotobservermissesRAW_B;
cannotrunnewbankwithoutper-STARTactualallocationpreflight. Needcorrect
raw_contexthardware selectionviaactualSTARTX09base(STRW8,[X9,8]);153c
getterz8-hsfc153c-statecontainsfields/descriptors/controlscalars. Shared
encodedrecordsgetterstillz8-hsfc1540-state,eachrecordnowencoder_base.
No raw63_inputs.py/raw63_context.py/native63driverexistsyet.

## LIVE RAW63 first actual dual-bank/four-job diagnostic (13:25UTC approx)

Implementedprivateencoder63_state.py selectsactualSTARTX09base153c/1540,
rejectsunknownbase;153cusesowngetter+base/IRQ130identity;1540usesexisting
getters. raw63_inputs.py derivesfromraw58_context.pywithgenerichardware
keysencoder_parameters/descriptors/control andexplicitencoder_base.
raw63_context.py derivesraw61: preflightrequiresactualbankandOWNpending0,
sameD0=1/format5/14-bitgeometry/actualallocation checks; HEADERbinduses
X19outer/X22D; completionrecordfilterincludesencoder_base. AtverifiedTAG,
remove matchingjobfromraw58_contextsdiscoverycache toavoidreadingfuture
freedcontexts;raw59_requests/snapshotsretained. f063_observation.py retains
2pre+1postfullF0readbacks andaddsactualSTARTX09==requestbase==encodedbase.

producer63_observation.py capturesactual22,841,728B RAW RAM BEFOREeach
START,afteractualallocationpreflight. Requiresuniquevalidacknowledgedand
notyetencodedISPparent,currentmappedspan,actualfullSHA,unchangedCPU/clock/
ISPstate/flatview. Nativejobmetadata120mustbeunique;request/event/bankbound.
Fourjobs/max96MiBphysicalsource budget. Filesproducer63-job-N-frame-N.u16le.bin,
receiptproducer63-evidence.json. Nohostpixelswritten,notfile/lifetimeacceptance.

RepeatObserver privateI/Oadapterusespure repeat63_plan (LIMIT4 eachBODY,
START,TAG). Actualentry/next codeguard clone repeat63_code_reader.py uses
freshGVA/page→GPA,physicalfourbytes,flatview,CPU/clockcheck;MAX_READS48
(24activation +24hits), ownreceipt namespace. OnlyZ1/z1changes: removes
knownparentcapturepoints atpre-FULL afterexistingauxretirement;armsown
entries;singletonhitguard/SPdelta/X0/LRpair;exactremove/add;recordsreal
eventsintoparentjournal,soexistingmetadata/F0handlersreadsamepausedstate.
Parentbeginfiltersall6managedPCs topreventrearmingone-shotaliases. Close
retainsfailure/pendinggapsandcleansownedtraps. Addattemptownershiprecorded
BEFOREI/Oforuncertaininsertioncleanup. No guestresume/register/RAM/IRQinput.
Nativeglobalpendingpair/multihitambiguitystops;notclaimedcompletehistory.

19 normal +19-O hostchecks pass (includes2importedlegacycompletiontests).
Repeatpure+mockI/O11coveractualwords,caller/SPdelta,duplicates/multihits,
timebackwards,limits,foreignowner,lostowner,partialaddcleanup. Encoderbank/
sourceoracle8includeactualRAW61bytefixturecopyforunit, corrupt/clockdrift/
duplicate/nonACK/alreadyencoded/foreignbank/capreject; notnativeproof.
Potentialfuturecornernotyettested: addingnexttrapwhenanotherstoppedCPUis
alreadyatthatPC canbeconsumedbyparenthandler; currentstrictownership/caller
guardwouldSTOP,notacceptdata. DoNOTeditactive63sources. Ifobserved, add
explicitpre-addPCcollisionguard innewcopy/test; donotbypassambiguity.

prepare_control63.py verifiesall62runtimefiles unchanged;bindsde44312c
immutablebinary/24frozensources/pool63adapter. Samepolicy61program,
contacts185,host1500sec,oldglobalmetadata1MiB/otherdiagnosticcaps unchanged.
Addsnew153cmodel+getter, repeats4RAWjobs andindependentpreSTARTsource.
-Ocheck185passed. **NativeACTIVE execsession76203**:
half_history_control63_probe.py /raw-history-63 /control63-review.
Initialexecutionmessage received. No63screensviewedyet/noapprovalwritten.
All63runtimefilesFROZEN. Watch4freshscreens,onlyapprovehealthyC120RAWL/PRE.3s
andSoundOFF. Afterstop audit_current_run.py raw-history-63.

NootherQTest/nativecurrentlyrunning. FullRAWprecapturestillUNACCEPTED:
strongest61singlehistoryNEF+3previews;62C1rearm1then153cdescriptorstop;
63newbankprofileand4-jobdataoraclespending. No commits/push/flash.

## RAW63 stillactive at13:33UTC; next-copy collision guard checked

Currentstep116raw57left; onlysound-setting.png ACTUALLYVIEWED(OFF).
No other63UIimagesviewedyet/noapproval. Nativeexec76203continues, no
firstUNKNOWN/resultfile. ALL63runtimefilesunchanged.

Offline(newfilesonly) test_repeat64_collision.py proves63adapterdoesnot
pre-rejectproposedaddPCalreadyoccupiedbyanotherpausedCPU (2redfailures).
Copiedrepeat63_observer.py→repeat64_observer.py, addedbeforeanytrapI/O:
if anyCPU PC==plan.add, failwithnosteps.14-Ochecks(inheritedoldmockcases+
2newcases)pass. ThisnewcopyisNOTusedby63, sharesexisting63plan/code-reader
andreceiptprefixforcompatibility. It isstrongerfail-stop, notsolutionfor
continuousambiguousSMPcallers; no scheduling/register/PCmodification.
Current63wouldeventuallyfailownership/callerchecksifparentconsumesnewtrap;
noafter-event/pixelproofshouldbeacceptedfromthatlostownership. Inspect
actual63failurebeforechoosingnextcopy; don'tinventaCPUorder.

## FINAL integration checkpoint after latest user merge-prep instruction

LatestuserexplicitlyaskedreadMESSAGE-TO-RAW-merge-prep.md,continue153c,
reserve4outputownershippointsandrenametest. ReaditandmainM2ownership
header/actualcallsread-only. NOintegration-treechanges;doNOTmessageother
thread/coordinatorwithoutdirecthumanmessagingauthorization. Reporthere.

RAW63 session76203 STOPPED atNEWstrictUNKNOWN CPU0 PC40c0d2b8 WRITE
15e21004=1 (secondJPEGbank). ALL4screensactuallyviewed, approvedC120RAWL
PRE.3s/soundOFF;lastUIhadredclockiconand1/500,butnoErrorandmodecache4/36.
153c actualF0 SUCCESS:frame14,8,587,390B,IRQ130acks1,pending0.1540frame13
8,587,458B. Bothfull11,409,840samples independentlydecodedzeroerrors.
Bothhaveproducer63 pre-STARTfullU16snapshots; job1→frame13/A,job2→frame14/B.
BsourceSHA7c277a261d14f16d8ed034d94b6e4b820f626b6f5b3b1323254e1ddd76d7b8db.
Repeatobserverfirst_failureNone,failedfalse,pending_pair_gapfalse,cleanup[];
normalterminationstillhasoriginalhardwarefailure. ActualBprofile/reservation
captured; no needclaimfromstaticguessnow. BnativeTAG/fileNOTcomplete.

audit_current_run raw-history-63 verified311sources/currentidentities,
original/derivedprogram,input,QEMUunchangedandownedprocessstopped.
audit63_nefs.py raw-history-63:completecleanactualcardONE8,917,186BNEF,
SHA6cb614c144c01f0044f949b1e4cebaf17fb5e1c86ab74315040347b11315ca97.
Actualstrip/header +independentpre-STARTsource +LibRaw11,409,840samples
zeroerrors;3previewsdecode, noJPEGjob-byteoracle. frame13preFULLqueued
767235812ns. all_exported_raw_verifiedtrue,pre_files1,post_files0,
raw_precapture_verifiedfalse. ReceiptHERE/raw-history-63-multinef-audit.json.
NoRAW64nativecreated/run. NoQEMUorunit-test sessionsremainactive.

MergepreprootsourcechangesAFTERnative63(frozen63untouched):
- NamedHSFC_OWNER_RESERVE beforeanyDMA/oldrecordretirement afterallchecks;
  HSFC_OWNER_COMPLETE afterfullreadbackbeforevalid/IRQ; ALLtransaction
  failuresgotocommonHSFC_OWNER_CANCEL exit. Commentsareexplicitintegration
  points, NOTnbimplementation. Noownerheadercopied/connected.
- Bothwrapperseachpassowncounter+1 asencoder_job. Globalhistoricaljobis
  separatemetadataID. Recordhasencoder_job+acknowledged; scalartracksits
  currenthistoricalrecordjob. Exactownbase/localjob/globalrecordvalidated
  BEFOREW1Cmark/clear. HSFC_OWNER_ACK anchorsinBheader/Acontrol; main's
  nb_ackmustgosuccessfullythereexactlyonce. Mustmap153ctoOWNslot(nohard
  codedslotnumberthatcouldcollidewithvideointegration). Getterexplicit
  output_ownership_connected=false. OwnercallsstillNOTwired.
- RestoredmainjournalF0ABIexactheaderbyteequalitySHA
  f44b9757dc9d6b13fde226dbcf938c3da4be7b0ea94fed593da763f675dd014f:
  f0bool,producerpitch retained,pitch*heightbound,actualspanwrittenbytes.
  HSFCstillrejectsproducer_journal.enabled;docsmarkpendingbysource_kind.
- Renamedroot tests/test_z8_lossless14.py→test_z8_lossless14_lineage.py;
  oldexacttestpreservedHERE/half63-test-sources/tests/test_z8_lossless14.py.
  ThisROOTtestwasNOTwatchedbyRAW63. tests/run.shnoteupdated; public
  build/allowlisthadnoexplicitoldreference, searchnowfindsnone. Historical
  scripts/receiptskeptunchanged. 153ctestnamealreadyindependent.
- Addedrealbanklocaljob1/1 andhistoricalACK[true,false]→[true,true]checks;
  addedjournalF0validpitch/zero-stride/too-long/4byte/widthbadCcases;
  addedC1videoconfig8=302negative. No153c/JPEGguardrelaxed.

LatestfrozenmergeprepQEMU **19eec6f8ff6e9fda11e6c5cf11e160285b11046c2ffb4c6f229bcb7e48fd40e1**
.../build/qemu-system-aarch64-merge64-19eec6f8.
freeze_merge64.py snapshots24modelsourcefiles→half64-model-sources,
manifest.local/merge64/build-identity.json. dual-normal15/dual-optimized15,
1540-regression19,lineage-normal7,rearm-video-negative15 PASSandallguests
stopped. Journal4hostchecksPASS(Ccore12scenariosO0/O2DNDEBUG+adapter2),
durablereceipt.local/merge64/journal-helper-receipt.json. Alltest/source
hashesbound. CurrentbinaryhasNOTbeenrerunnative;manifestexplicitlypairs
RAW63native de44312... withpost-nativeprep19eec6... andlistsdifferences.

Human-readablehandoff docs/raw153c-integration-checkpoint.md created;
docs/raw-precapture-shared146.md andz8-hsfc-dual-encoder.md updatedbefore
freeze. Readyforuser/coordinatorreviewat153ccheckpoint. StillnofullRAW
precapture/multiNEF/windowacceptance. Nocommit/push/flash. DoNOTclaimnew
mergeprep19eeasoriginalnative63binary.

Memoryregistrylinenmoved: relevantcurrentMEMORY.md240(2pre+1post),243
(ACKnotphotocount),367-368(method/evidencelimits). IDs01a137eb-4b61-75f2-
9b49-f90e13a65fd9(Task3),01a12e9a-442f-7471-9260-a2934e67fd47(Z9).
Eventualfinalmustappendoneoai-mem-citationblockwithverifiedranges.

