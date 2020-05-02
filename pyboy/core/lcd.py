#
# License: See LICENSE.md file
# GitHub: https://github.com/Baekalfen/PyBoy
#

from array import array
from ctypes import c_void_p

from pyboy.utils import color_code
from pyboy.core import cpu

import copy

VIDEO_RAM = 8 * 1024  # 8KB
OBJECT_ATTRIBUTE_MEMORY = 0xA0
LCDC, STAT, SCY, SCX, LY, LYC, DMA, BGP, OBP0, OBP1, WY, WX = range(
    0xFF40, 0xFF4C)
ROWS, COLS = 144, 160
TILES = 384

try:
    from cython import compiled
    cythonmode = compiled
except ImportError:
    cythonmode = False


ModeHBLANK, ModeVBLANK, ModeOAM, ModeVRAM = range(4)


class LCD:
    def __init__(self, mb):
        self.VRAM = array("B", [0] * VIDEO_RAM)
        self.OAM = array("B", [0] * OBJECT_ATTRIBUTE_MEMORY)

        self.LCDC = LCDCRegister(0)
        # self.STAT = 0x00
        self.SCY = 0x00
        self.SCX = 0x00
        self.LY = 0x00
        # self.LYC = 0x00
        # self.DMA = 0x00
        self.BGP = PaletteRegister(0xFC)
        self.OBP0 = PaletteRegister(0xFF)
        self.OBP1 = PaletteRegister(0xFF)
        self.WY = 0x00
        self.WX = 0x00

        self.mb = mb

        self.clock = 0

    def tick(self, cycles):
        # TODO: Implement Line 153 quirk

        self.clock += cycles
        if self.LCDC.lcd_enable:
            mode = self.get_STAT_mode()
            if mode == 2:
                # Mode 2 - OAM Scan
                if self.clock >= 80:
                    self.clock -= 80
                    self.set_STAT_mode(ModeVRAM)

            elif mode == 3:
                # Mode 3 - Pixel Transfer
                # TODO: Make Mode 3 variable length, can be extended
                # by sprites, the window, and scroll X
                if self.clock >= 172:
                    self.clock -= 172

                    self.mb.renderer.scanline(self.LY, self)
                    self.set_STAT_mode(ModeHBLANK)

                    # Check for STAT Hblank interrupt
                    if self.mb.getitem(STAT) & (1 << 3):
                        self.mb.cpu.set_interruptflag(cpu.LCDC)

            elif mode == 0:
                # Mode 0 - Hblank
                if self.clock >= 204:
                    self.clock -= 204

                    self.LY += 1
                    self.check_LYC()

                    self.mb.setitem(LY, self.LY)

                    if (self.LY > 143):
                        if not self.mb.disable_renderer:
                            self.mb.renderer.render_screen(self)

                        self.mb.cpu.set_interruptflag(0)
                        self.set_STAT_mode(ModeVBLANK)
                    else:
                        self.set_STAT_mode(ModeOAM)

            elif mode == 1:
                # Mode 1 - Vblank
                if self.clock >= 456:
                    self.clock -= 456

                    self.LY += 1
                    self.mb.setitem(LY, self.LY)

                    if (self.LY > 153):
                        self.LY = 0
                        self.mb.setitem(LY, self.LY)

                        self.set_STAT_mode(ModeOAM)

        else:
            # self.mb.renderer.blank_screen()
            self.clock = 0
            self.LY = 0
            self.mb.setitem(LY, self.LY)

            self.set_STAT_mode(0)
            self.mb.setitem(LY, 0)

    def check_LYC(self):
        # LY=LYC
        if self.mb.getitem(LYC) == self.LY:
            self.mb.setitem(STAT, self.mb.getitem(
                STAT) | 0b100)  # Sets the LYC flag
            if self.mb.getitem(STAT) & 0b01000000:
                self.mb.cpu.set_interruptflag(cpu.LCDC)
        else:
            self.mb.setitem(STAT, self.mb.getitem(STAT) & 0b11111011)

    def get_STAT_mode(self):
        return self.mb.getitem(STAT) & 0b11

    def set_STAT_mode(self, mode):
        self.mb.setitem(STAT, self.mb.getitem(STAT) &
                        0b11111100)  # Clearing 2 LSB
        self.mb.setitem(STAT, self.mb.getitem(
            STAT) | mode)  # Apply mode to LSB

        # Mode 3 is definitely interruptable, there are many conditions
        # where it will happen, such as when LY=LYC and the interrupt condition
        # is enabled in the STAT register, firing the interrupt.

    def save_state(self, f):
        for n in range(VIDEO_RAM):
            f.write(self.VRAM[n])

        for n in range(OBJECT_ATTRIBUTE_MEMORY):
            f.write(self.OAM[n])

        f.write(self.LCDC.value)
        f.write(self.BGP.value)
        f.write(self.OBP0.value)
        f.write(self.OBP1.value)

        f.write(self.SCY)
        f.write(self.SCX)
        f.write(self.WY)
        f.write(self.WX)

    def load_state(self, f, state_version):
        for n in range(VIDEO_RAM):
            self.VRAM[n] = f.read()

        for n in range(OBJECT_ATTRIBUTE_MEMORY):
            self.OAM[n] = f.read()

        self.LCDC.set(f.read())
        self.BGP.set(f.read())
        self.OBP0.set(f.read())
        self.OBP1.set(f.read())

        self.SCY = f.read()
        self.SCX = f.read()
        self.WY = f.read()
        self.WX = f.read()

    def getwindowpos(self):
        return (self.WX - 7, self.WY)

    def getviewport(self):
        return (self.SCX, self.SCY)


class PaletteRegister:
    def __init__(self, value):
        self.value = 0
        self.lookup = [0] * 4
        self.set(value)

    def set(self, value):
        # Pokemon Blue continuously sets this without changing the value
        if self.value == value:
            return False

        self.value = value
        for x in range(4):
            self.lookup[x] = (value >> x * 2) & 0b11
        return True

    def getcolor(self, i):
        return self.lookup[i]


class LCDCRegister:
    def __init__(self, value):
        self.set(value)

    def set(self, value):
        self.value = value

        # No need to convert to bool. Any non-zero value is true.
        # yapf: disable
        self.lcd_enable = value & (1 << 7)
        self.windowmap_select = value & (1 << 6)
        self.window_enable = value & (1 << 5)
        self.tiledata_select = value & (1 << 4)
        self.backgroundmap_select = value & (1 << 3)
        self.sprite_height = value & (1 << 2)
        self.sprite_enable = value & (1 << 1)
        self.background_enable = value & (1 << 0)
        # yapf: enable


class Renderer:
    def __init__(self, mb, color_palette):
        self.alphamask = 0xFF
        self.color_palette = [(c << 8) | self.alphamask for c in color_palette]
        self.color_format = "RGBA"

        self.buffer_dims = (160, 144)

        self.clearcache = False
        self.tiles_changed = set([])

        self.mb = mb

        # Init buffers as white
        self._screenbuffer_raw = array("B", [0xFF] * (ROWS*COLS*4))
        self._tilecache_raw = array("B", [0xFF] * (TILES*8*8*4))
        self._spritecache0_raw = array("B", [0xFF] * (TILES*8*8*4))
        self._spritecache1_raw = array("B", [0xFF] * (TILES*8*8*4))

        if cythonmode:
            self._screenbuffer = memoryview(
                self._screenbuffer_raw).cast("I", shape=(ROWS, COLS))
            self._tilecache = memoryview(self._tilecache_raw).cast(
                "I", shape=(TILES * 8, 8))
        else:
            v = memoryview(self._screenbuffer_raw).cast("I")
            self._screenbuffer = [v[i:i + COLS]
                                  for i in range(0, COLS * ROWS, COLS)]
            v = memoryview(self._tilecache_raw).cast("I")
            self._tilecache = [v[i:i + 8] for i in range(0, TILES * 8 * 8, 8)]
            self._screenbuffer_ptr = c_void_p(
                self._screenbuffer_raw.buffer_info()[0])

        self._scanlineparameters = [[0, 0, 0, 0, LCDCRegister(0xFF), PaletteRegister(
            0xFF), PaletteRegister(0xFF), PaletteRegister(0xFF)] for _ in range(ROWS)]

    def scanline(self, y, lcd):
        bx, by = lcd.getviewport()
        wx, wy = lcd.getwindowpos()
        self._scanlineparameters[y][0] = bx
        self._scanlineparameters[y][1] = by
        self._scanlineparameters[y][2] = wx
        self._scanlineparameters[y][3] = wy
        self._scanlineparameters[y][4] = copy.deepcopy(lcd.LCDC)
        self._scanlineparameters[y][5] = copy.deepcopy(lcd.BGP)
        self._scanlineparameters[y][6] = copy.deepcopy(lcd.OBP0)
        self._scanlineparameters[y][7] = copy.deepcopy(lcd.OBP1)

    def render_screen(self, lcd):
        self.update_cache(lcd)
        # All VRAM addresses are offset by 0x8000
        # Following addresses are 0x9800 and 0x9C00

        for y in range(ROWS):
            bx, by, wx, wy, rowLCDC, rowBGP, rowOBP0, rowOBP1 = self._scanlineparameters[y]

            background_offset = 0x1800 if rowLCDC.backgroundmap_select == 0 else 0x1C00
            wmap = 0x1800 if rowLCDC.windowmap_select == 0 else 0x1C00

            # Used for the half tile at the left side when scrolling
            offset = bx & 0b111

            for x in range(COLS):
                if rowLCDC.window_enable and wy <= y and wx <= x:
                    wt = lcd.VRAM[wmap + (y-wy) // 8 * 32 %
                                  0x400 + (x-wx) // 8 % 32]
                    # If using signed tile indices, modify index
                    if not rowLCDC.tiledata_select:
                        # (x ^ 0x80 - 128) to convert to signed, then
                        # add 256 for offset (reduces to + 128)
                        wt = (wt ^ 0x80) + 128

                    self._screenbuffer[y][x] = self.color_palette[rowBGP.getcolor(
                        self._tilecache[8 * wt + (y-wy) % 8][(x-wx) & 7] & 3)]
                elif rowLCDC.background_enable:
                    bt = lcd.VRAM[background_offset +
                                  (y+by) // 8 * 32 % 0x400 + (x+bx) // 8 % 32]
                    # If using signed tile indices, modify index
                    if not rowLCDC.tiledata_select:
                        # (x ^ 0x80 - 128) to convert to signed, then
                        # add 256 for offset (reduces to + 128)
                        bt = (bt ^ 0x80) + 128

                    self._screenbuffer[y][x] = self.color_palette[rowBGP.getcolor(
                        self._tilecache[8 * bt + (y+by) % 8][(x+offset) & 7] & 3)]
                else:
                    # If background is disabled, it becomes white
                    self._screenbuffer[y][x] = self.color_palette[0]

            if rowLCDC.sprite_enable:
                # Render sprites
                # - Doesn't restrict 10 sprites per scan line
                spriteheight = 16 if rowLCDC.sprite_height else 8
                bgpkey = self.color_palette[lcd.BGP.getcolor(0)]

                for n in range(0x00, 0xA0, 4):
                    # Documentation states the y coordinate needs to be subtracted by 16
                    spritey = lcd.OAM[n] - 16
                    # Documentation states the x coordinate needs to be subtracted by 8
                    spritex = lcd.OAM[n + 1] - 8

                    # Check if the sprite is on this scanline
                    if y >= spritey and y < spritey + spriteheight:
                        tileindex = lcd.OAM[n + 2]
                        attributes = lcd.OAM[n + 3]
                        xflip = attributes & 0b00100000
                        yflip = attributes & 0b01000000
                        spritepriority = (
                            attributes & 0b10000000) and not False
                        spritepalette = (rowOBP1 if attributes &
                                         0b10000 else rowOBP0)

                        # 16 height sprites: low bit ignored
                        if rowLCDC.sprite_height:
                            tileindex &= 0b11111110

                        if rowLCDC.sprite_height and yflip:
                            if y >= spritey + 8:
                                tileindex -= 1
                            else:
                                tileindex += 1

                        for dx in range(8):
                            if dx + spritex < 160 and dx + spritex >= 0 and y < 144:
                                adjX = dx
                                if xflip:
                                    adjX = adjX ^ 7

                                adjY = y - spritey
                                if yflip:
                                    adjY = adjY ^ 7

                                prepal = self._tilecache[(
                                    tileindex * 8) + adjY][adjX]

                                if not prepal == 0:
                                    col = spritepalette.getcolor(prepal)
                                    if (spritepriority and self._screenbuffer[y][dx + spritex] == bgpkey) or not spritepriority:
                                        self._screenbuffer[y][dx +
                                                              spritex] = self.color_palette[col]

    def update_cache(self, lcd):
        if self.clearcache:
            self.tiles_changed.clear()
            for x in range(0x8000, 0x9800, 16):
                self.tiles_changed.add(x)
            self.clearcache = False

        for t in self.tiles_changed:
            for k in range(0, 16, 2):  # 2 bytes for each line
                byte1 = lcd.VRAM[t + k - 0x8000]
                byte2 = lcd.VRAM[t + k + 1 - 0x8000]
                y = (t+k-0x8000) // 2

                for x in range(8):
                    colorcode = color_code(byte1, byte2, 7 - x)

                    self._tilecache[y][x] = colorcode

        self.tiles_changed.clear()

    def blank_screen(self):
        # If the screen is off, fill it with a color.
        color = self.color_palette[0]
        for y in range(ROWS):
            for x in range(COLS):
                self._screenbuffer[y][x] = color

    def save_state(self, f):
        for y in range(ROWS):
            f.write(self._scanlineparameters[y][0])
            f.write(self._scanlineparameters[y][1])
            # We store (WX - 7). We add 7 and mask 8 bits to make it easier to serialize
            f.write((self._scanlineparameters[y][2] + 7) & 0xFF)
            f.write(self._scanlineparameters[y][3])
            f.write(self._scanlineparameters[y][4])

    def load_state(self, f, state_version):
        for y in range(ROWS):
            self._scanlineparameters[y][0] = f.read()
            self._scanlineparameters[y][1] = f.read()
            # Restore (WX - 7) as described above
            self._scanlineparameters[y][2] = (f.read() - 7) & 0xFF
            self._scanlineparameters[y][3] = f.read()
            if state_version > 3:
                self._scanlineparameters[y][4] = f.read()
