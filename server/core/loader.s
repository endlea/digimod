| digimod's reset loader (the scheme of digikit's core/expansion, ElekLabs/digikit, AGPL-3.0-or-later).
| The reset code's two calls (copy the SRAM data, clear the bss: jsr 0x4000045c ; jsr 0x400004b2 at 0x4000053e, the
| same in OS 1.16 and 1.17) become `jsr loader ; nop`. The loader walks the segment chain digimod appended to the
| MAIN OS image (at its stock end, 0x4031ff60), copies each payload to its place in the free top of RAM and zeroes
| its bss, then runs the two original calls. It runs before the bss clear that would wipe the chain.
|     [u32 len][u32 dest][u32 bss] [payload: len bytes] ...  [u32 0xffffffff]   (big endian, multiples of 4)
    .text
    .globl loader
loader:
    lea     0x4031ff60,%a0
1:  move.l  (%a0)+,%d0
    addq.l  #1,%d0
    beq.s   4f
    subq.l  #1,%d0
    movea.l (%a0)+,%a1
    move.l  (%a0)+,%d1
    lsr.l   #2,%d0
    beq.s   3f
2:  move.l  (%a0)+,(%a1)+
    subq.l  #1,%d0
    bne.s   2b
3:  lsr.l   #2,%d1
    beq.s   1b
5:  clr.l   (%a1)+
    subq.l  #1,%d1
    bne.s   5b
    bra.s   1b
4:  jsr     0x4000045c
    jsr     0x400004b2
    rts
