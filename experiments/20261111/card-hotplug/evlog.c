/* SPDX-License-Identifier: GPL-2.0-or-later */
/* Emulation-only: log native system dispatch events (x0+32 at entry pc=). */
#include <qemu-plugin.h>
#include <glib.h>
#include <stdio.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
QEMU_PLUGIN_EXPORT int qemu_plugin_version=QEMU_PLUGIN_VERSION;
static uint64_t pc=0x40d60ec8;static FILE *out;static GMutex mu;static struct qemu_plugin_register *x0r;
static void vinit(qemu_plugin_id_t id,unsigned cpu){(void)id;(void)cpu;GArray *r=qemu_plugin_get_registers();
 for(guint i=0;i<r->len;i++){qemu_plugin_reg_descriptor *d=&g_array_index(r,qemu_plugin_reg_descriptor,i);if(!strcmp(d->name,"x0"))x0r=d->handle;}g_array_free(r,TRUE);}
static void hit(unsigned cpu,void *u){(void)u;if(!x0r)return;GByteArray *b=g_byte_array_new();uint64_t m=0;
 if(qemu_plugin_read_register(x0r,b)>=8)memcpy(&m,b->data,8);g_byte_array_free(b,TRUE);
 uint32_t ev=0xffffffff;if(m){GByteArray *c=g_byte_array_new();if(qemu_plugin_read_memory_vaddr(m+32,c,4)&&c->len==4)memcpy(&ev,c->data,4);g_byte_array_free(c,TRUE);}
 struct timespec ts;clock_gettime(CLOCK_MONOTONIC,&ts);
 g_mutex_lock(&mu);fprintf(out,"EV %ld.%03ld cpu%u msg=%#lx event=%u\n",ts.tv_sec,ts.tv_nsec/1000000,cpu,m,ev);fflush(out);g_mutex_unlock(&mu);}
static void tb(qemu_plugin_id_t id,struct qemu_plugin_tb *t){(void)id;
 for(size_t i=0;i<qemu_plugin_tb_n_insns(t);i++){struct qemu_plugin_insn *in=qemu_plugin_tb_get_insn(t,i);
  if(qemu_plugin_insn_vaddr(in)==pc)qemu_plugin_register_vcpu_insn_exec_cb(in,hit,QEMU_PLUGIN_CB_R_REGS,0);}}
QEMU_PLUGIN_EXPORT int qemu_plugin_install(qemu_plugin_id_t id,const qemu_info_t *info,int argc,char **argv){
 (void)info;const char *path=0;
 for(int i=0;i<argc;i++){if(!strncmp(argv[i],"out=",4))path=argv[i]+4;else if(!strncmp(argv[i],"pc=",3))pc=strtoull(argv[i]+3,0,0);else return -1;}
 if(!path)return -1;out=fopen(path,"w");if(!out)return -1;
 qemu_plugin_register_vcpu_init_cb(id,vinit);qemu_plugin_register_vcpu_tb_trans_cb(id,tb);return 0;}
