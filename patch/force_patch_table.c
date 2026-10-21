/* 2026-10-21: sys_call_table 落在内核镜像 .data，这台机器把它的「直接映射」页
 * 设成了只读（Oops 里 pmd=0x...781，AP[2]=1），所以：
 *   - fixmap 写返回 0 但回读仍是旧值（不落地）
 *   - set_memory_rw 对镜像地址返回 -22（只认 vmalloc/module）
 *   - 直接写线性别名 = permission fault ⇒ Oops（3.16.6 首轮就是这么崩的）
 * 这里给这一页新建一个可写映射（vm_map_ram，新 PTE、权限我们说了算）来写；
 * 全部失败就退回 safe mode（ksu_dispatcher_nr=-1），绝不把 syscallno 改坏。
 */
static void ksu_force_patch_table(void)
{
    unsigned long va, ph = 0, ph_k = 0;
    int err = 0;
    syscall_fn_t nv = (syscall_fn_t)ksu_syscall_dispatcher;

    if (!ksu_syscall_table || ksu_dispatcher_nr < 0)
        return;
    if ((void *)READ_ONCE(ksu_syscall_table[ksu_dispatcher_nr]) == (void *)nv)
        return; /* fixmap 已经写进去了，不用兜底 */

    va = (unsigned long)&ksu_syscall_table[ksu_dispatcher_nr];
    ph = phys_from_virt(va & PAGE_MASK, &err);
    if (ksu_kimg_voff_addr) {
        unsigned long voff = *(unsigned long *)ksu_kimg_voff_addr;
        if (voff)
            ph_k = va - voff;
    }
    if (ph_k)
        ph = ph_k;
    pr_alert("ksu: fallback phys walk=0x%lx err=%d kimg=0x%lx\n", ph, err, ph_k);

    /* 入口 1：set_memory_rw（对镜像地址预计 -22） */
    if (set_memory_rw(va & PAGE_MASK, 1) == 0) {
        WRITE_ONCE(ksu_syscall_table[ksu_dispatcher_nr], nv);
        pr_alert("ksu: rw-path val=%px\n",
                 (void *)READ_ONCE(ksu_syscall_table[ksu_dispatcher_nr]));
    }
    if ((void *)READ_ONCE(ksu_syscall_table[ksu_dispatcher_nr]) == (void *)nv)
        goto outcome;

    /* 入口 2：vmap 新映射 */
    if (ph) {
        struct page *pg = pfn_to_page(__phys_to_pfn(ph));
        void *vm = vm_map_ram(&pg, 1, -1);
        if (!IS_ERR_OR_NULL(vm)) {
            syscall_fn_t *dst = (syscall_fn_t *)((char *)vm + (va & ~PAGE_MASK));
            pr_alert("ksu: vmap=%px dst=%px before=%px\n", vm, dst, (void *)*dst);
            *dst = nv;
            smp_mb();
            pr_alert("ksu: vmap after write viaVmap=%px normal=%px\n", (void *)*dst,
                     (void *)READ_ONCE(ksu_syscall_table[ksu_dispatcher_nr]));
            vm_unmap_ram(vm, 1);
        } else {
            pr_alert("ksu: vm_map_ram failed (%ld)\n", PTR_ERR(vm));
        }
    }

outcome:
    if ((void *)READ_ONCE(ksu_syscall_table[ksu_dispatcher_nr]) != (void *)nv) {
        pr_alert("ksu: slot patch NOT effective -> redirect disabled (safe mode)\n");
        ksu_dispatcher_nr = -1;
    } else {
        pr_alert("ksu: dispatcher active slot=%d\n", ksu_dispatcher_nr);
    }
}
