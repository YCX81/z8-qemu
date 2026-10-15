/* SPDX-License-Identifier: GPL-2.0-or-later */
#include <stdio.h>
#include <stdlib.h>
#include "../qemu/nikon-buffer-ownership.h"
#define CHECK(x) do { if (!(x)) { fprintf(stderr,"failed line %d\n",__LINE__); exit(1); } } while (0)
int main(void) {
    NBOwner o = {.enabled=true};
    NBSpan a[] = {{0x1000,32},{0x2000,64}};
    CHECK(nb_reserve(&o,0,1,7,a,2));
    CHECK(!nb_writable(&o,(NBSpan){0x101f,2}));
    CHECK(nb_writable(&o,(NBSpan){0x1020,32}));
    CHECK(!nb_reserve(&o,1,1,7,a,1));
    CHECK(!nb_ack(&o,0,1)); /* Before actual completion. */
    CHECK(nb_complete(&o,0,1));
    CHECK(!nb_ack(&o,0,2));
    CHECK(nb_ack(&o,0,1));
    CHECK(nb_writable(&o,a[0]));
    CHECK(!nb_reserve(&o,0,1,7,a,1)); /* ABA job reuse. */
    CHECK(nb_reserve(&o,0,2,8,a,2));
    nb_cancel(&o,0);
    CHECK(nb_writable(&o,a[0]));
    CHECK(!nb_reserve(&o,0,2,8,a,1));
    NBSpan bad[]={{UINT64_MAX-1,4}}, overlap[]={{0x1000,32},{0x1010,32}};
    CHECK(!nb_reserve(&o,0,3,9,bad,1));
    CHECK(!nb_reserve(&o,0,3,9,overlap,2));
    CHECK(!nb_reserve(&o,2,3,9,a,1));
    CHECK(!nb_reserve(&o,0,3,0,a,1));
    CHECK(!nb_complete(&o,0,3));
    NBOwner off={0};
    CHECK(nb_reserve(&off,0,1,1,a,2));
    CHECK(nb_writable(&off,a[0]));
    CHECK(!off.slots[0].held);
    puts("buffer ownership checks passed");
}
