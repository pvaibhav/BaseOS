"""Conservative PoC model for the SPL's temporary EL3 cache window.

Unicorn backing memory represents CPU-visible bytes. Independent poisoned PoC
copies become visible only after every modeled data-cache set/way was cleaned.
This catches an omitted/incomplete clean; it does not simulate cache latency.
"""
class CacheVisibility:
    def __init__(self, machine, image_bytes):
        self.machine = machine
        self.ranges = [(0x45000000, image_bytes), (0x48000000, 0x40000),
                       (0x4a000000, 0x100000), (0x4a100000, 0x100000),
                       (0x4a200000, 0x100000), (0x4a300000, 0x4000)]
        self.poc = {address: b"\xa5" * size for address, size in self.ranges}
        self.dirty = set()
        self.cleaned = set()
        self.enabled = False
        self.complete_cleans = 0
        # CLIDR/CCSIDR in SystemRegisters: two levels, 4 ways, 8 sets, 64 B.
        self.required = {(level << 1) | (way << 30) | (index << 6)
                         for level in range(2) for way in range(4) for index in range(8)}

    def store(self, address, size):
        if self.enabled:
            for start, length in self.ranges:
                if start <= address < start + length:
                    self.dirty.add(start)
                    self.cleaned.clear()
                    return

    def operation(self, operation, operand, value):
        if operation == "msr" and operand == "sctlr_el3":
            self.enabled = bool(value & 4)
        if operation == "dc" and operand == "cisw":
            assert value in self.required
            self.cleaned.add(value)
            if self.cleaned == self.required:
                for address, length in self.ranges:
                    self.poc[address] = bytes(self.machine.mem_read(address, length))
                self.dirty.clear()
                self.complete_cleans += 1

    def verify(self):
        assert self.complete_cleans and not self.enabled and not self.dirty
        for address, length in self.ranges:
            assert self.poc[address] == bytes(self.machine.mem_read(address, length)), hex(address)
        return {"complete_set_way_cleans": self.complete_cleans, "dirty_ranges_after_cleanup": len(self.dirty),
                "poisoned_poc_checked_bytes": sum(length for _, length in self.ranges),
                "cache_geometry_modeled": True, "latency_modeled": False}
