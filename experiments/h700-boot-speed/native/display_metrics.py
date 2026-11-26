"""Validate DE writeback against the framebuffer page currently displayed."""


def compare_pixels(scanout, framebuffer, width, height, stride, yoffset=0):
    if (not 0 < width <= 1920 or not 0 < height <= 1920 or
            stride < width * 4 or yoffset < 0 or
            len(scanout) != width * height * 4 or
            len(framebuffer) < stride * (yoffset + height)):
        raise ValueError("invalid display buffer extent")
    matches = 0
    nonuniform = False
    first = scanout[:3]
    for y in range(height):
        output = scanout[y * width * 4:(y + 1) * width * 4]
        active = framebuffer[(yoffset + y) * stride:(yoffset + y) * stride + width * 4]
        for x in range(0, width * 4, 4):
            pixel = output[x:x + 3]
            matches += pixel == active[x:x + 3]
            nonuniform |= pixel != first
    agreement = matches / (width * height)
    # Captures are consecutive: a clock or battery icon can repaint between them.
    threshold = 0.98
    return {"matching_rgb_fraction": agreement, "minimum_rgb_fraction": threshold,
            "scanout_nonuniform": nonuniform, "passed": nonuniform and agreement >= threshold}
