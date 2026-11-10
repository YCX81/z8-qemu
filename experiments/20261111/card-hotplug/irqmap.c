/* SPDX-License-Identifier: GPL-2.0-or-later */
/* Emulation-only: capture the IRQ dispatch context (x21 at 0x4016c374) and, at the Nth
 * execution of dumppc=, write every installed INTID handler (ctx+0x20+id*8 -> {.,.,fn,arg}). */
#include <qemu-plugin.h>
#include <glib.h>
#include <stdio.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
QEMU_PLUGIN_EXPORT int qemu_plugin_version=QEMU_PLUGIN_VERSION;
static uint64_t ctx,dumppc;static unsigned dumpn=1,hits,done;static FILE *out;static GMutex mu;
static struct qemu_plugin_register *x21;
static uint64_t rd64(uint64_t a){GByteArray *b=g_byte_array_new();uint64_t v=0;
 if(qemu_plugin_read_memory_vaddr(a,b,8)&&b->len==8)memcpy(&v,b->data,8);g_byte_array_free(b,TRUE);return v;}
static void vinit(qemu_plugin_id_t pid,unsigned cpu){(void)pid;(void)cpu;GArray *r=qemu_plugin_get_registers();
 for(guint i=0;i<r->len;i++){qemu_plugin_reg_descriptor *d=&g_array_index(r,qemu_plugin_reg_descriptor,i);if(!strcmp(d->name,"x21"))x21=d->handle;}
 g_array_free(r,TRUE);}
static void at_disp(unsigned cpu,void *u){(void)cpu;(void)u;if(ctx||!x21)return;GByteArray *b=g_byte_array_new();
 if(qemu_plugin_read_register(x21,b)>=8){memcpy(&ctx,b->data,8);g_mutex_lock(&mu);fprintf(out,"CTX %#lx\n",ctx);fflush(out);g_mutex_unlock(&mu);}g_byte_array_free(b,TRUE);}
static void at_dump(unsigned cpu,void *u){(void)cpu;(void)u;if(done||!ctx||++hits<dumpn)return;done=1;g_mutex_lock(&mu);
 for(unsigned id=32;id<1020;id++){uint64_t p=rd64(ctx+0x20+id*8ull);if(!p)continue;
  fprintf(out,"IRQ %u spi %u desc %#lx fn %#lx arg %#lx\n",id,id-32,p,rd64(p+0x10),rd64(p+0x18));}
 fprintf(out,"DUMPED\n");fflush(out);g_mutex_unlock(&mu);}
static void tb(qemu_plugin_id_t id,struct qemu_plugin_tb *t){(void)id;
 for(size_t i=0;i<qemu_plugin_tb_n_insns(t);i++){struct qemu_plugin_insn *in=qemu_plugin_tb_get_insn(t,i);uint64_t a=qemu_plugin_insn_vaddr(in);
  if(a==0x4016c374)qemu_plugin_register_vcpu_insn_exec_cb(in,at_disp,QEMU_PLUGIN_CB_R_REGS,0);
  if(a==dumppc)qemu_plugin_register_vcpu_insn_exec_cb(in,at_dump,QEMU_PLUGIN_CB_NO_REGS,0);}}
QEMU_PLUGIN_EXPORT int qemu_plugin_install(qemu_plugin_id_t id,const qemu_info_t *info,int argc,char **argv){
 (void)info;const char *path=0;
 for(int i=0;i<argc;i++){char *a=argv[i];
  if(!strncmp(a,"out=",4))path=a+4;else if(!strncmp(a,"dumppc=",7))dumppc=strtoull(a+7,0,0);
  else if(!strncmp(a,"dumpn=",6))dumpn=strtoul(a+6,0,0);else return -1;}
 if(!path)return -1;out=fopen(path,"w");if(!out)return -1;
 qemu_plugin_register_vcpu_init_cb(id,vinit);qemu_plugin_register_vcpu_tb_trans_cb(id,tb);return 0;}
