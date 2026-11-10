/* SPDX-License-Identifier: GPL-2.0-or-later */
/* Emulation-only power-cut plugin. At the Nth execution of pc= (counted only after gate= ran,
 * if given) waits delay_us= host microseconds then _exit()s QEMU: a battery pull at a chosen
 * instruction. Optional cmd=V,cmdpc=P,cmdn=N writes V into Fenestra's startup.command
 * (State+0xb828+0xb8) at the Nth execution of P, standing in for a menu request. */
#include <qemu-plugin.h>
#include <glib.h>
#include <stdio.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <time.h>
QEMU_PLUGIN_EXPORT int qemu_plugin_version=QEMU_PLUGIN_VERSION;
static uint64_t pc,gate,cmdpc,dumppc;static unsigned dumpn=200,dumphits;static unsigned n=1,cmdn=1,delay_us,cmdv;static int gated,armed,cmddone;
static unsigned hits,cmdhits;static FILE *out;static GMutex mu;
static uint64_t rd64(uint64_t a){GByteArray *b=g_byte_array_new();uint64_t v=0;
 if(qemu_plugin_read_memory_vaddr(a,b,8)&&b->len==8)memcpy(&v,b->data,8);g_byte_array_free(b,TRUE);return v;}
static uint32_t rd32(uint64_t a){GByteArray *b=g_byte_array_new();uint32_t v=0xdeadbeef;
 if(qemu_plugin_read_memory_vaddr(a,b,4)&&b->len==4)memcpy(&v,b->data,4);g_byte_array_free(b,TRUE);return v;}
static uint64_t state(void){uint64_t root=rd64(0xa032b50c8ull+16);if(!root)return 0;return rd64(root+663*96+80);}
static void dump(const char *why){uint64_t s=state();uint64_t ss=s+0xb828;
 fprintf(out,"%s state=%#lx core_phase=%u activation=%u st_phase=%u desired=%u err=%u flag_known=%u journal_phase=%u\n",why,s,
  s?rd32(s+76):0,s?rd32(s+72):0,s?rd32(ss+204):0,s?rd32(ss+208):0,s?rd32(ss+212):0,s?rd32(ss+216):0,s?rd32(ss):0);fflush(out);}
static void *killer(void *p){(void)p;if(delay_us)usleep(delay_us);g_mutex_lock(&mu);dump("CUT");g_mutex_unlock(&mu);_exit(0);return 0;}
static void hit(unsigned cpu,void *ud){(void)cpu;uint64_t a=(uint64_t)(uintptr_t)ud;
 g_mutex_lock(&mu);
 if(dumppc&&a==dumppc&&++dumphits%dumpn==0)dump("TICK");
 if(gate&&a==gate&&!gated){gated=1;fprintf(out,"GATE %#lx\n",a);fflush(out);}
 if(cmdpc&&a==cmdpc&&!cmddone&&++cmdhits>=cmdn){uint64_t s=state();
   if(s){uint32_t v=cmdv;GByteArray b={.data=(guint8*)&v,.len=4};qemu_plugin_write_memory_vaddr(s+0xb828+0xb8,&b);
     cmddone=1;dump("CMD");}}
 if(pc&&a==pc&&(!gate||gated)&&!armed&&++hits>=n){armed=1;dump("HIT");
   if(!delay_us){dump("CUT");_exit(0);}
   g_thread_new("cut",killer,0);}
 g_mutex_unlock(&mu);}
static void tb(qemu_plugin_id_t id,struct qemu_plugin_tb *t){(void)id;
 for(size_t i=0;i<qemu_plugin_tb_n_insns(t);i++){struct qemu_plugin_insn *in=qemu_plugin_tb_get_insn(t,i);
  uint64_t a=qemu_plugin_insn_vaddr(in);
  if((dumppc&&a==dumppc)||(pc&&a==pc)||(gate&&a==gate)||(cmdpc&&a==cmdpc))
   qemu_plugin_register_vcpu_insn_exec_cb(in,hit,QEMU_PLUGIN_CB_NO_REGS,(void*)(uintptr_t)a);}}
static void atexit_cb(qemu_plugin_id_t id,void *p){(void)id;(void)p;dump("EXIT");}
QEMU_PLUGIN_EXPORT int qemu_plugin_install(qemu_plugin_id_t id,const qemu_info_t *info,int argc,char **argv){
 (void)info;const char *path=0;
 for(int i=0;i<argc;i++){char *a=argv[i];
  if(!strncmp(a,"out=",4))path=a+4;
  else if(!strncmp(a,"pc=",3))pc=strtoull(a+3,0,0);
  else if(!strncmp(a,"gate=",5))gate=strtoull(a+5,0,0);
  else if(!strncmp(a,"n=",2))n=strtoul(a+2,0,0);
  else if(!strncmp(a,"delay_us=",9))delay_us=strtoul(a+9,0,0);
  else if(!strncmp(a,"cmd=",4))cmdv=strtoul(a+4,0,0);
  else if(!strncmp(a,"cmdpc=",6))cmdpc=strtoull(a+6,0,0);
  else if(!strncmp(a,"dumppc=",7))dumppc=strtoull(a+7,0,0);
  else if(!strncmp(a,"dumpn=",6))dumpn=strtoul(a+6,0,0);
  else if(!strncmp(a,"cmdn=",5))cmdn=strtoul(a+5,0,0);
  else return -1;}
 if(!path)return -1;out=fopen(path,"w");if(!out)return -1;
 qemu_plugin_register_vcpu_tb_trans_cb(id,tb);qemu_plugin_register_atexit_cb(id,atexit_cb,0);return 0;}
