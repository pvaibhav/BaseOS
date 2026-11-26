"""Explicit EL2/EL3 register model for Unicorn's EL1-only execution context."""
from unicorn import arm64_const as a


def xreg(index):
    # Unicorn assigns FP/LR separate IDs; X0..X30 are not one contiguous enum.
    return (a.UC_ARM64_REG_X0 + index) if index < 29 else (a.UC_ARM64_REG_X29, a.UC_ARM64_REG_X30)[index - 29]


class SystemRegisters:
    def __init__(self, level):
        self.level = level
        self.registers = {"sctlr_el3": 0x30c50830,
                          "clidr_el1": 0x02000023,
                          "ccsidr_el1": 2 | (3 << 3) | (7 << 13),
                          "id_aa64mmfr2_el1": 0}
        self.accesses = []

    def execute(self, machine, instruction):
        if instruction.mnemonic in ("dc", "ic") and self.level == 3:
            operands = instruction.op_str.split(", ")
            value = machine.reg_read(xreg(int(operands[1][1:]))) if len(operands) > 1 else 0
            self.accesses.append((instruction.mnemonic, operands[0], value))
            machine.reg_write(a.UC_ARM64_REG_PC, instruction.address + 4)
            return True
        if instruction.mnemonic == "tlbi" and self.level == 3:
            self.accesses.append(("tlbi", instruction.op_str, 0))
            machine.reg_write(a.UC_ARM64_REG_PC, instruction.address + 4)
            return True
        if instruction.mnemonic not in ("mrs", "msr"): return False
        operands = instruction.op_str.split(", ")
        system = operands[1] if instruction.mnemonic == "mrs" else operands[0]
        if system == "currentel":
            value = self.level * 4
        elif (system.endswith(("_el3", "_el2")) or system in {
                "s3_1_c15_c2_1", "spsr_abt", "spsr_und", "spsr_irq", "spsr_fiq",
                "spsr_el1", "elr_el1", "sctlr_el1", "actlr_el1", "cpacr_el1", "csselr_el1",
                "sp_el1", "esr_el1", "afsr0_el1", "afsr1_el1", "ttbr0_el1", "ttbr1_el1",
                "mair_el1", "amair_el1", "vbar_el1", "contextidr_el1", "tpidr_el1", "cntkctl_el1",
                "tcr_el1", "par_el1", "far_el1", "clidr_el1", "ccsidr_el1", "id_aa64mmfr2_el1"}) and self.level == 3:
            value = self.registers.get(system, 0)
        else:
            return False
        if instruction.mnemonic == "mrs":
            target = int(operands[0][1:])
            machine.reg_write(xreg(target), value)
        else:
            source = operands[1]
            value = 0 if source == "xzr" else machine.reg_read(xreg(int(source[1:])))
            self.registers[system] = value
        self.accesses.append((instruction.mnemonic, system, value))
        machine.reg_write(a.UC_ARM64_REG_PC, instruction.address + 4)
        return True
