# SPDX-License-Identifier: GPL-2.0-or-later
import sys,subprocess,time,random
name=sys.argv[1];extra=sys.argv[2:]
import os
sys.argv=['x','none'];exec(open('chains.py').read().split('keys=sys.argv')[0])
Q='/path/to/user/.local/share/nikon-z8-qemu/cardhp/build/qemu-system-aarch64'
for a in range(30):
    launch_slot()
    subprocess.run(['rm','-rf',str(RUNS/name),str(RUNS/(name+'-properties.json'))])
    cmd=[PY,'-u','-B','run_pull.py','--name',name,'--plan','cardswap','--main',os.environ.get('SWAPMAIN',MAIN),'--fenestra',os.environ.get('SWAPCARD',CARD),'--seconds','420','--qemu',Q,
         '--powercut','dumppc=0x401d88c4,dumpn=100']+extra
    with open(RUNS/(name+'.log'),'w') as f:subprocess.run(cmd,stdout=f,stderr=subprocess.STDOUT)
    if 'started <' in (RUNS/(name+'.log')).read_text(errors='replace'):time.sleep(random.randint(20,50));continue
    break
R=RUNS/name
print(subprocess.run(f"grep -h 'DBISTATE' {RUNS}/{name}.log | cut -c1-260; grep -h 'CARDSWAP\\|Traceback\\|Error' {RUNS}/{name}.log | head; grep -h 'Z8_CFEXPRESS\\|Z9_PCIE_LINK_INPUT\\|pci_nvme' {R}/stderr.log | cut -c1-200 | uniq -c | head -80",shell=True,capture_output=True,text=True).stdout)
print(open(R/'powercut.log').read()[-6000:])
