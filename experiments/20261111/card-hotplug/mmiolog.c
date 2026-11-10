/* SPDX-License-Identifier: GPL-2.0-or-later */
/* Emulation-only: after gate pc executed gaten times, count guest loads from MMIO
 * (va 0x0d000000..0x20000000) by (pc,va) for wins executions of gate, then dump. */
#include <qemu-plugin.h>
#include <glib.h>
#include <stdio.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
QEMU_PLUGIN_EXPORT int qemu_plugin_version=QEMU_PLUGIN_VERSION;
static uint64_t gate;static unsigned gaten=300,win=600,hits;static int on,done;static FILE *out;static GMutex mu;static GHashTable *h;
typedef struct{uint64_t pc,va;uint64_t n;uint32_t last;}E;
static void dump(void){GHashTableIter it;gpointer k,v;g_hash_table_iter_init(&it,h);
 while(g_hash_table_iter_next(&it,&k,&v)){E *e=v;fprintf(out,"%#lx %#lx %lu\n",e->pc,e->va,e->n);}fprintf(out,"DONE\n");fflush(out);}
static void mem(unsigned cpu,qemu_plugin_meminfo_t info,uint64_t va,void *ud){(void)cpu;
 if(!on||done||qemu_plugin_mem_is_store(info))return;if(va<0x0d000000||va>=0x20000000)return;
 uint64_t pc=(uint64_t)(uintptr_t)ud;uint64_t key=(pc<<20)^va;g_mutex_lock(&mu);
 E *e=g_hash_table_lookup(h,&key);if(!e){e=g_new0(E,1);e->pc=pc;e->va=va;uint64_t *kk=g_new(uint64_t,1);*kk=key;g_hash_table_insert(h,kk,e);}e->n++;g_mutex_unlock(&mu);}
static void g(unsigned cpu,void *u){(void)cpu;(void)u;g_mutex_lock(&mu);hits++;
 if(!on&&hits>=gaten){on=1;fprintf(out,"ON\n");fflush(out);}
 else if(on&&!done&&hits>=gaten+win){done=1;dump();}g_mutex_unlock(&mu);}
static void tb(qemu_plugin_id_t id,struct qemu_plugin_tb *t){(void)id;
 for(size_t i=0;i<qemu_plugin_tb_n_insns(t);i++){struct qemu_plugin_insn *in=qemu_plugin_tb_get_insn(t,i);uint64_t a=qemu_plugin_insn_vaddr(in);
  qemu_plugin_register_vcpu_mem_cb(in,mem,QEMU_PLUGIN_CB_NO_REGS,QEMU_PLUGIN_MEM_R,(void*)(uintptr_t)a);
  if(a==gate)qemu_plugin_register_vcpu_insn_exec_cb(in,g,QEMU_PLUGIN_CB_NO_REGS,0);}}
QEMU_PLUGIN_EXPORT int qemu_plugin_install(qemu_plugin_id_t id,const qemu_info_t *info,int argc,char **argv){
 (void)info;const char *path=0;h=g_hash_table_new(g_int64_hash,g_int64_equal);
 for(int i=0;i<argc;i++){char *a=argv[i];
  if(!strncmp(a,"out=",4))path=a+4;else if(!strncmp(a,"gate=",5))gate=strtoull(a+5,0,0);
  else if(!strncmp(a,"gaten=",6))gaten=strtoul(a+6,0,0);else if(!strncmp(a,"win=",4))win=strtoul(a+4,0,0);else return -1;}
 if(!path||!gate)return -1;out=fopen(path,"w");if(!out)return -1;
 qemu_plugin_register_vcpu_tb_trans_cb(id,tb);return 0;}
