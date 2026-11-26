"""Measure the first settled framebuffer, ignoring later clock repaints."""


def settled_frame(trace: str, handoff: float) -> float | None:
    groups = []
    for line in trace.splitlines():
        fields = line.split()
        if len(fields) not in (6, 7):
            continue
        at, digest = float(fields[0]), fields[5]
        if not groups or groups[-1][2] != digest:
            groups.append([at, at, digest])
        else:
            groups[-1][1] = at
    if not groups:
        return None
    initial = groups[0][2]
    return next((start for start, end, digest in groups
                 if digest != initial and start >= handoff and end - start >= 0.2), None)
