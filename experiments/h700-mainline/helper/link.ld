ENTRY(helper_entry)
SECTIONS {
 . = 0x4a000000;
 .text : { KEEP(*(.text.entry)) *(.text*) }
 .rodata : { *(.rodata*) }
 .data : { *(.data*) }
 . = ALIGN(8);
 __bss_start = .;
 .bss (NOLOAD) : { *(.bss*) *(COMMON) . = ALIGN(8); }
 __bss_end = .;
 ASSERT(. <= 0x4a0e0000, "helper exceeds 896 KiB")
 /DISCARD/ : { *(.comment*) *(.eh_frame*) *(.note*) }
}
