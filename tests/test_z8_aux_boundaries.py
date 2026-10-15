# SPDX-License-Identifier: GPL-2.0-or-later
"""Characterize existing opt-in AUX0 virtual boundaries; no firmware/events injected."""
import argparse,json,tempfile
from pathlib import Path
from test_z8_producer_journal import Lab,require
OPTION='event1508-aux-paired-boundary'
def run(binary,folder,paired):
    row=dict(paired=paired,passed=False,firmware_executed=False,host_event_injection=False)
    with tempfile.TemporaryDirectory(prefix='z8-aux-boundary-') as tmp:
        g=Lab(binary,Path(tmp),False)
        if paired:g.command[g.command.index('-M')+1]+=','+OPTION+'=on'
        try:
            g.start();g.write(0x1508510c,1)
            g.advance();require(g.state('event1508-aux0-state')['pending']==0,'Inactive source cannot generate boundary')
            g.periodic();g.advance();s=g.state('event1508-aux0-state')
            require(s['pending']==(3 if paired else 2),'Actual virtual timer pending')
            require(g.irq(0x102)==paired,'Only AUX0 bit0 enabled at GIC')
            g.write(0x1508510c,0);require(not g.irq(0x102),'Mask hides IRQ without clearing pending')
            require(g.state('event1508-aux0-state')['pending']==s['pending'],'Mask must preserve pending')
            g.write(0x15080014,1);g.write(0x15085108,1)
            require(g.state('event1508-aux0-state')['pending']==2,'W1C bit0 preserves bit1')
            g.write(0x15085108,2);g.advance()
            require(g.state('event1508-aux0-state')['pending']==0,'Stopped source must not republish boundary')
            g.no_pixels();row.update(passed=True,active_state=s,no_pixels=True)
        except Exception as e:row['error']=repr(e)
        finally:
            g.close();folder.mkdir(parents=True,exist_ok=False)
            (folder/'stderr.txt').write_text(g.text());(folder/'transactions.json').write_text(json.dumps(g.transactions,indent=2)+'\n')
    (folder/'result.json').write_text(json.dumps(row,indent=2)+'\n');print(json.dumps(row));return row['passed']
def main():
    p=argparse.ArgumentParser();p.add_argument('--qemu',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    rows=[run(a.qemu,a.output/('on' if on else 'default-off'),on) for on in (False,True)]
    if not all(rows):raise SystemExit(1)
if __name__=='__main__':main()
