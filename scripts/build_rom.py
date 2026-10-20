"""Build Tetris DX with Tetris/Bombliss music pages and a PINCH setting.

Only Python's standard library is required. Input revisions are checked by SHA-256.
See scripts/ROM_NOTES.md for the verified hook addresses and memory layout.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from gb_disasm import instruction
from bombliss_player import (compile_player, ROM_NAME as BOM_NAME, SHA256 as BOM_HASH,
                             BANK as BOM_BANK, START as BOM_START,
                             FRAME as BOM_FRAME, MUTE as BOM_MUTE, TICK as BOM_TICK,
                             shadow as bom_shadow)

ROOT = Path(__file__).resolve().parents[1]
DX_NAME = 'Tetris DX (World) (SGB Enhanced) (GB Compatible).gbc'
GB_NAME = 'Tetris (World) (Rev 1).gb'
OUTPUT_NAME = 'Tetris DX - GB Sounds.gbc'
HASHES = {
    DX_NAME: 'd349dc93423c6abcd775d3b6a8797df715a44a42ec837afb21bf17ae43b40a9e',
    GB_NAME: '0d6535aef23969c7e5af2b077acaddb4a445b3d0df7bf34c8acef07b51b015c3',
    BOM_NAME: BOM_HASH,
}

# Original engine DF70..DFFF is moved down $70, leaving DX's DC80..DEFF
# and the stack at DFFF alone. Control bytes follow it at DF90.
ENABLED, ACTIVE, PAUSED, SAVED_BANK, LAST_ID = range(0xDF90, 0xDF95)
FORCE_RESTART, RESTORE_ALL = 0xDF95, 0xDF96
NATIVE_PAN = 0xDF97
MUSIC_VARIANT = 0xDF98  # 0 normal, 1 Danger, 2 Peril
MUSIC_PHASE = 0xDF99  # fractional clock: +variant/4 music ticks per frame
PINCH_ENABLED, GLYPH_BITS, GLYPH_ROWS = 0xDF9A, 0xDF9B, 0xDF9C
# Menu BG tiles unused by both the color and monochrome layouts. Positive
# signed-mode IDs reside at $9000+, outside sprite tile memory.
PINCH_TILES = bytes([0x24,0x26,0x27,0x28,0x2D,0x2E,0x2F,0x38,0x3A,0x3B,
                     0x3C,0x3D,0x3E,0x3F,0x44,0x45,0x46,0x47,0x48,0x4B])
PINCH_BACKDROP = 0xDF9D  # ten original bottom-row tile IDs, restored on mode rows
IMPORTED_SONG = 0xDFA7  # normalized A/B/C ID 5/6/7, shared across variants
LABEL_SHADOW_TILES = (0xC8, 0xC9, 0xD0)
VARIANTS = [('Normal', 0), ('Danger', 2), ('Peril', 4)]
NEW_MUSIC, CURRENT_MUSIC = 0xDF78, 0xDF79
START_MUSIC, PLAY_MUSIC, MUTE = 0x6A21, 0x6C44, 0x69C7
ENGINE_BANK = 0x21  # MBC1 cannot map bank $20 into the upper ROM window.


class Code:
    """Tiny label/fixup emitter: keeps the injected machine code reviewable."""
    def __init__(self, origin):
        self.origin = origin
        self.data = bytearray()
        self.labels = {}
        self.fixups = []

    def mark(self, name):
        if name in self.labels:
            raise ValueError(f'Duplicate label {name}')
        self.labels[name] = self.origin + len(self.data)

    def emit(self, hex_bytes):
        self.data.extend(bytes.fromhex(hex_bytes))

    def word(self, value):
        if isinstance(value, str):
            self.fixups.append((len(self.data), value))
            self.data.extend(b'\0\0')
        else:
            self.data.extend(value.to_bytes(2, 'little'))

    def op(self, opcode, address):
        self.data.append(opcode)
        self.word(address)

    def ld_a(self, address): self.op(0xFA, address)
    def store_a(self, address): self.op(0xEA, address)
    def call(self, address): self.op(0xCD, address)
    def jp(self, address): self.op(0xC3, address)

    def finish(self):
        for pos, name in self.fixups:
            self.data[pos:pos+2] = self.labels[name].to_bytes(2, 'little')
        return self.data


def control_code():
    c = Code(0x3400)
    c.mark('init')
    c.emit('af')
    for var in [ENABLED, ACTIVE, PAUSED, LAST_ID, FORCE_RESTART, RESTORE_ALL, NATIVE_PAN, MUSIC_VARIANT, MUSIC_PHASE, IMPORTED_SONG]: c.store_a(var)
    c.emit('3e 01')
    c.store_a(PINCH_ENABLED)
    c.jp(0x3F00)

    # All expanded-bank execution is interrupt protected. MBC1's high bank
    # register must be restored before DX's ordinary bank-switching code runs.
    c.mark('bank_in')
    c.emit('f3 f0 fd')  # di / ldh a, [current bank]
    c.store_a(SAVED_BANK)
    c.emit('3e 01 ea 00 40')
    c.ld_a(MUSIC_VARIANT)
    c.emit('c6 01 47')
    c.ld_a(ACTIVE)
    c.emit('fe 02 78')
    c.op(0xC2,'bank_selected')
    c.emit('c6 03')
    c.mark('bank_selected')
    c.emit('ea 00 20 c6 20 e0 fd c9')  # GB $21..23 / Bombliss $24..26
    c.mark('bank_out')
    c.emit('af ea 00 40')
    c.ld_a(SAVED_BANK)
    c.emit('e0 fd ea 00 20 fb c9')

    c.mark('stop_original')
    c.ld_a(ACTIVE)
    c.emit('a7 c8')
    c.emit('c5 d5 e5')
    c.call('bank_in')
    c.call('mute_imported')
    c.call('bank_out')
    c.emit('af')
    for var in [ACTIVE, PAUSED, CURRENT_MUSIC, MUSIC_VARIANT, IMPORTED_SONG]: c.store_a(var)
    c.emit('e1 d1 c1 c9')

    # BGM APIs $3F12 and $3F18 have different restart semantics.
    c.mark('bgm_conditional')
    c.emit('af')
    c.store_a(FORCE_RESTART)
    c.emit('78 a7')
    c.op(0xC2, 'dispatch_bgm')
    c.ld_a(ACTIVE)
    c.emit('a7')
    c.op(0xCA, 'dispatch_bgm')
    c.ld_a(LAST_ID)
    c.emit('b9 c8')  # cp c / ret z
    c.jp('dispatch_bgm')
    c.mark('bgm_start')
    c.emit('3e 01')
    c.store_a(FORCE_RESTART)
    c.mark('dispatch_bgm')
    c.ld_a(ENABLED)
    c.emit('a7')
    c.op(0xCA, 'native_bgm')
    c.emit('78 a7')  # b must be zero
    c.op(0xC2, 'native_bgm')
    c.emit('79 fe 37')
    c.op(0xDA, 'native_bgm')
    c.emit('fe 3a')
    c.op(0xDA, 'preview')
    c.emit('fe 44')
    c.op(0xDA, 'native_bgm')
    c.emit('fe 47')
    c.op(0xDA, 'normal_gameplay')
    c.emit('fe 5a')
    c.op(0xDA, 'native_bgm')
    c.emit('fe 60')
    c.op(0xD2, 'native_bgm')
    # $5A/$5B A, $5C/$5D B, $5E/$5F C: Danger then Peril.
    c.emit('d6 5a e6 01 3c')
    c.store_a(MUSIC_VARIANT)
    c.emit('79 d6 5a cb 3f c6 05')
    c.jp('original_bgm')
    c.mark('normal_gameplay')
    c.emit('af')
    c.store_a(MUSIC_VARIANT)
    c.emit('79')
    c.emit('d6 3f')  # $44..46 -> original song IDs 5..7
    c.jp('original_bgm')
    c.mark('preview')
    c.emit('af')
    c.store_a(MUSIC_VARIANT)
    c.emit('79')
    c.emit('d6 32')  # $37..39 -> original song IDs 5..7
    c.mark('original_bgm')
    c.store_a(IMPORTED_SONG)
    c.emit('c5 f5')
    c.emit('79')
    c.store_a(LAST_ID)
    c.emit('01 00 00')
    c.call('native_start')  # Stop DX BGM, retaining its SFX voices.
    c.ld_a(ENABLED)
    c.store_a(ACTIVE)  # player identity, independent of the selected menu page
    c.call('bank_in')
    c.emit('f1')
    c.emit('47')
    c.ld_a(ACTIVE)
    c.emit('fe 02 78')
    c.op(0xCA,'start_bombliss')
    c.store_a(NEW_MUSIC)
    c.call(0x69A5)  # Clear locks / init original player.
    c.call(START_MUSIC)
    c.jp('imported_started')
    c.mark('start_bombliss')
    c.call(BOM_START)
    c.mark('imported_started')
    c.emit('af')
    c.store_a(PAUSED)
    c.store_a(RESTORE_ALL)
    c.store_a(MUSIC_PHASE)
    c.call('bank_out')
    c.emit('c1 af c9')
    c.mark('native_bgm')
    c.call('stop_original')
    c.ld_a(FORCE_RESTART)
    c.emit('a7')
    c.op(0xCA, 'native_conditional')
    c.mark('native_start')
    c.call(0x3F60)
    c.jp(0x427F)
    c.mark('native_conditional')
    c.call(0x3F60)
    c.jp(0x42B8)

    c.mark('pause')
    c.ld_a(ACTIVE)
    c.emit('a7')
    c.op(0xCA, 'native_pause')
    c.emit('3e 01')
    c.store_a(PAUSED)
    c.emit('c5 d5 e5')
    c.call('bank_in')
    c.call('mute_imported')
    c.call('bank_out')
    c.emit('e1 d1 c1')
    c.mark('native_pause')
    c.call(0x3F60)
    c.jp(0x4414)
    c.mark('resume')
    c.emit('af')
    c.store_a(PAUSED)
    c.emit('3e 80')
    c.store_a(RESTORE_ALL)
    c.call(0x3F60)
    c.jp(0x42E4)

    c.mark('frame')
    c.call(0x3F06)
    # Snapshot DX's output before adding stereo routing for music voices.
    # Leave free channels audible while DX updates its effect channels.
    c.emit('f0 25')
    c.store_a(NATIVE_PAN)
    c.mark('frame_native_done')
    c.ld_a(ACTIVE)
    c.emit('a7 c8')
    c.ld_a(PAUSED)
    c.emit('a7 c0 c5 d5 e5')
    c.call('bank_in')
    c.ld_a(ACTIVE)
    c.emit('fe 02')
    c.op(0xCA,'bombliss_frame')
    # Each native hardware channel's bit 7 marks an active sound effect now
    # that the native BGM has been stopped. Let that SFX own the channel.
    for channel, (native, lock) in enumerate(zip([0xDEA0,0xDEB8,0xDED0,0xDEE8], [0xDF2F,0xDF3F,0xDF4F,0xDF5F])):
        c.ld_a(native)
        c.emit('e6 80 47')
        c.ld_a(lock)
        c.emit('4f 78')
        c.store_a(lock)
        c.emit('a7')
        c.op(0xC2, f'lock_done_{channel}')
        c.ld_a(RESTORE_ALL)
        c.emit('b1 e6 80')
        c.op(0xC4, f'restore_{channel}')
        if channel == 0:
            c.emit('af e0 10')  # Clear a finished SFX's frequency sweep.
        c.mark(f'lock_done_{channel}')
    c.emit('af')
    c.store_a(RESTORE_ALL)
    c.call(PLAY_MUSIC)
    c.jp('music_clock')
    c.mark('bombliss_frame')
    c.call(BOM_FRAME)
    c.emit('af')
    c.store_a(RESTORE_ALL)
    c.mark('music_clock')
    # The whole score advances at 1x / 1.25x / 1.5x. Use a fractional clock,
    # keeping note lengths intact so different rhythms cannot drift apart.
    # Native effects are still updated exactly once per video frame above.
    c.ld_a(MUSIC_VARIANT)
    c.emit('47')
    c.ld_a(MUSIC_PHASE)
    c.emit('80 fe 04')
    c.op(0xDA, 'save_music_phase')
    c.emit('d6 04')
    c.store_a(MUSIC_PHASE)
    c.call('tick_imported')
    c.jp('frame_music_done')
    c.mark('save_music_phase')
    c.store_a(MUSIC_PHASE)
    c.mark('frame_music_done')
    # GB uses stereo; Bombliss retains its score's panning/mute commands.
    # Effect-owned channels retain exactly the routing produced by DX.
    c.ld_a(NATIVE_PAN)
    c.emit('4f')
    c.ld_a(ACTIVE)
    c.emit('fe 02 06 ff')
    c.op(0xC2,'music_pan_ready')
    c.ld_a(bom_shadow(0x25))
    c.emit('47')
    c.mark('music_pan_ready')
    for channel,mask in enumerate([0x11,0x22,0x44,0x88]):
        c.ld_a([0xDEA0,0xDEB8,0xDED0,0xDEE8][channel])
        c.emit('e6 80')
        c.op(0xC2,f'pan_done_{channel}')
        c.emit(f'79 e6 {255-mask:02x} 4f 78 e6 {mask:02x} b1 4f')
        c.mark(f'pan_done_{channel}')
    c.emit('79 e0 25')
    c.mark('frame_mix_done')
    c.call('bank_out')
    c.emit('e1 d1 c1 c9')

    c.mark('tick_imported')
    c.ld_a(ACTIVE)
    c.emit('fe 02')
    c.op(0xCA,BOM_TICK)
    c.jp(PLAY_MUSIC)
    c.mark('mute_imported')
    c.ld_a(ACTIVE)
    c.emit('fe 02')
    c.op(0xCA,BOM_MUTE)
    c.jp(MUTE)

    # Restore the current note as soon as an SFX releases a channel, or the
    # player unpauses. The song's note timers and sequence pointers stay put.
    for channel, base in enumerate([0xDF20,0xDF30,0xDF40,0xDF50]):
        c.mark(f'restore_{channel}')
        c.ld_a(base+1)
        c.emit('a7 c8')
        if channel in [0,1]:
            c.ld_a(base+0xB)
            c.emit('a7 c0')
        if channel == 2:
            c.emit('af e0 1a')
            c.ld_a(base+6)
            c.emit('6f')
            c.ld_a(base+7)
            c.emit('67')
            c.call(0x6998)
            c.emit('3e ff')
            c.store_a(0xDC9D)
            # A rest must still restore the GB waveform. Otherwise the next
            # GB note can inherit the effect's waveform until a new instrument
            # command appears. Keep the DAC off until that next note starts.
            c.ld_a(base+0xB)
            c.emit('a7 c0')
            c.emit('3e 80 e0 1a af e0 1b')
            c.ld_a(base+8)
            c.emit('e0 1c')
        else:
            duty_reg, volume_reg = [(0x11,0x12),(0x16,0x17),(0,0),(0x20,0x21)][channel]
            c.ld_a(base+8)
            c.emit(f'e0 {duty_reg:02x}')
            c.ld_a(base+6)
            c.emit(f'e0 {volume_reg:02x}')
        freq_reg = [0x13,0x18,0x1D,0x22][channel]
        c.ld_a(base+9)
        c.emit(f'e0 {freq_reg:02x}')
        c.ld_a(base+0xA)
        c.emit(f'f6 80 e0 {freq_reg+1:02x} c9')

    c.mark('wave_command')
    c.ld_a(0xDF4F)
    c.emit('e6 80')
    c.op(0xC2, 0x6BB3)
    c.emit('e5 af e0 1a 6b 62')
    c.call(0x6998)
    c.emit('3e ff')
    c.store_a(0xDC9D)
    c.emit('e1')
    c.jp(0x6BB3)
    c.mark('wave_decay')
    c.ld_a(0xDF4F)
    c.emit('e6 80 c0 3e 40 e0 1c c9')

    # At DX's pulse-2 selection call, HL points to the virtual voice's
    # instrument type (+$0B). GB and Bombliss B/C use pulse 1 for
    # movement/rotation; DX and Bombliss A retain pulse 2.
    # The original selectors initialize the same channel state and I/O base;
    # all note streams, envelopes, priorities and durations remain native.
    c.mark('select_effect_pulse')
    c.ld_a(ACTIVE)
    c.emit('fe 01')
    c.op(0xCA,'pulse_1_candidate')
    c.emit('fe 02')
    c.op(0xC2,'native_pulse_2')
    c.ld_a(IMPORTED_SONG)
    c.emit('fe 06')
    c.op(0xDA,'native_pulse_2')
    c.emit('fe 08')
    c.op(0xD2,'native_pulse_2')
    c.mark('pulse_1_candidate')
    c.ld_a(PAUSED)
    c.emit('a7')
    c.op(0xC2,'native_pulse_2')
    c.ld_a(0xDC80)
    c.emit('e6 20')  # SFX processing, not a native background-music voice
    c.op(0xCA,'native_pulse_2')
    c.emit('2b 2b 2b 2b 2b 2b 2a fe 21')  # voice ID at +5, HL now +6
    c.op(0xCA,'piece_effect_pulse')
    c.emit('fe 22')
    c.op(0xC2,'native_pulse_2')
    c.mark('piece_effect_pulse')
    c.emit('7e a7')  # upper byte of the sound ID must be zero
    c.op(0xC2,'native_pulse_2')
    c.jp(0x416F)  # native pulse-1 selector, registers $FF10..14
    c.mark('native_pulse_2')
    c.jp(0x4178)  # original pulse-2 selector, registers $FF16..19

    c.mark('menu_modes')
    c.jp(0x4C6F)
    c.mark('menu_music')
    c.call('toggle_pinch')
    c.call('toggle')
    c.jp(0x4CAB)
    c.mark('toggle')
    c.emit('f0 83 e6 30 c8')  # Newly pressed Left/Right, only in music focus.
    c.ld_a(0xDB0E)
    c.emit('a7 c8')
    c.ld_a(ENABLED)
    c.emit('47 f0 83 e6 10 78')
    c.op(0xCA,'page_left')
    c.emit('3c fe 03')
    c.op(0xDA,'page_ready')
    c.emit('af')
    c.jp('page_ready')
    c.mark('page_left')
    c.emit('3d fe ff')
    c.op(0xC2,'page_ready')
    c.emit('3e 02')
    c.mark('page_ready')
    c.store_a(ENABLED)
    # When music has focus, restart the selected preview immediately.
    c.ld_a(0xDB0D)
    c.emit('fe 04')
    c.op(0xD2, 'toggle_off')
    c.emit('c6 36 4f 06 00')
    c.jp('bgm_start')
    c.mark('toggle_off')
    c.emit('01 00 00')
    c.jp('bgm_start')

    c.mark('toggle_pinch')
    c.emit('f0 83 e6 04 c8')  # Select changes PINCH only in music focus.
    c.ld_a(0xDB0E)
    c.emit('a7 c8')
    c.ld_a(PINCH_ENABLED)
    c.emit('ee 01')
    c.store_a(PINCH_ENABLED)
    c.emit('c9')

    # Apply to all pages at the shared height decision. Conditional BGM APIs
    # then retain the current normal song instead of restarting it each frame.
    c.mark('height_music')
    c.emit('f5')
    c.ld_a(PINCH_ENABLED)
    c.emit('a7')
    c.op(0xCA,'height_normal')
    c.emit('f1 fe 0b')
    c.op(0xD2,0x4C50)
    c.jp(0x4C4B)
    c.mark('height_normal')
    c.emit('f1')
    c.jp(0x4C4B)

    # Menu initialization still has the LCD off. Extend the left edge one tile
    # and use seven font cells for MUSIC-1/2/3. Reuse the original border/font.
    c.mark('menu_layout')
    c.store_a(0xC0A8)  # displaced initialization instruction
    c.emit('f0 4f f5 af e0 4f')
    c.emit('c5 d5 e5')
    c.op(0x21,0x9A20)
    c.op(0x11,PINCH_BACKDROP)
    c.emit('06 0a')
    c.mark('save_pinch_backdrop')
    c.emit('2a 12 13 05')
    c.op(0xC2,'save_pinch_backdrop')
    c.emit('e1 d1 c1')
    for address,tile in [(0x9982,0x29),(0x9983,0x2A),(0x99A2,0x39),
                         (0x99A3,0x8C),(0x99A4,0x94),(0x99A5,0x92),
                         (0x99A6,0x88),(0x99A7,0x82),(0x99A8,0x9F),
                         (0x99AA,0x7F),(0x99C2,0x49),(0x99C3,0x4A)]:
        c.emit(f'3e {tile:02x}')
        c.store_a(address)
    c.ld_a(ENABLED)
    c.emit('c6 a1')
    c.store_a(0x99A9)
    # Extend the native inactive-label highlight with -, 1, 2 and 3 glyphs.
    # These four menu tiles have no tilemap/sprite users here. Convert
    # font color 3 to the native MUSIC overlay's color 2, background color 1.
    c.emit('c5 d5 e5')
    for source,destination in [(0x89F0,0x89B0),(0x8A10,0x8C80),(0x8A20,0x8C90),(0x8A30,0x8D00)]:
        c.op(0x21,source)
        c.op(0x11,destination)
        c.call('make_label_shadow')
    c.emit('e1 d1 c1')
    # Bake both 80x8 PINCH labels onto copies of the original bottom-row
    # tiles. Black glyph pixels are ORed into both background bitplanes.
    c.emit('c5 d5 e5')
    for state,text in enumerate([' PINCH:ON ',' PINCH:OFF']):
        for column,char in enumerate(text):
            c.op(0x21,0x9A20+column)
            c.emit('7e ee 80 6f 26 00 29 29 29 29')
            c.op(0x01,0x8800)
            c.emit('09')
            c.op(0x11,0x9000+PINCH_TILES[state*10+column]*16)
            if char == ':': c.op(0x01,'pinch_colon_bits')
            else: c.op(0x01,(0x97F0 if char == ' ' else 0x8800+(ord(char)-ord('A'))*16)+1)
            c.call('draw_pinch_tile')
    c.emit('e1 d1 c1')
    c.call('draw_pinch_map')
    # CGB reads bit 0 back as zero after selecting VRAM bank 0; DMG reads
    # $FF. Only CGB has attribute RAM, so skip these writes on DMG/SGB.
    c.emit('f0 4f e6 01')
    c.op(0xC2,'menu_layout_done')
    c.emit('3e 01 e0 4f 3e 07')
    c.store_a(0x99A3)
    c.store_a(0x99A9)
    c.mark('menu_layout_done')
    c.emit('f1 e0 4f c9')

    c.mark('make_label_shadow')
    c.emit('06 08')
    c.mark('label_shadow_row')
    c.emit('23 2a 2f 12 13 2f 12 13 05')
    c.op(0xC2,'label_shadow_row')
    c.emit('c9')

    c.mark('draw_pinch_tile')
    c.emit('3e 08')
    c.store_a(GLYPH_ROWS)
    c.mark('pinch_glyph_row')
    c.emit('0a 03 03')
    c.store_a(GLYPH_BITS)
    for _ in range(2):
        c.emit('2a e5')
        c.op(0x21,GLYPH_BITS)
        c.emit('b6 e1 12 13')
    c.ld_a(GLYPH_ROWS)
    c.emit('3d')
    c.store_a(GLYPH_ROWS)
    c.op(0xC2,'pinch_glyph_row')
    c.emit('c9')
    c.mark('pinch_colon_bits')
    c.emit('00 00 00 00 18 00 00 00 00 00 18 00 00 00 00 00')

    c.mark('draw_pinch_map')
    c.emit('c5 d5 e5')
    c.ld_a(0xDB0E)
    c.emit('a7')
    c.op(0x21,PINCH_BACKDROP)
    c.op(0xCA,'pinch_map_ready')
    c.ld_a(PINCH_ENABLED)
    c.emit('a7')
    c.op(0x21,'pinch_on_map')
    c.op(0xC2,'pinch_map_ready')
    c.op(0x21,'pinch_off_map')
    c.mark('pinch_map_ready')
    c.op(0x11,0x9A20)
    c.emit('06 0a')
    c.mark('pinch_map_tile')
    c.emit('2a 12 13 05')
    c.op(0xC2,'pinch_map_tile')
    c.emit('e1 d1 c1 c9')
    c.mark('pinch_on_map')
    c.data.extend(PINCH_TILES[:10])
    c.mark('pinch_off_map')
    c.data.extend(PINCH_TILES[10:])

    # Keep all seven letters in the original label's focus/highlight behavior.
    c.mark('label_sprites_dmg')
    c.ld_a(0xDB0E)
    c.emit('a7')
    c.op(0xC2,0x439B)  # retain the native song cursor when music has focus
    c.jp('label_sprites_mono')
    c.mark('label_sprites')
    c.jp('label_sprites_color')
    for style,attribute in [('color',0x14),('mono',0x00)]:
        c.mark(f'label_sprites_{style}')
        c.ld_a(ENABLED)
        c.emit('fe 02')
        c.op(0xCA,f'label_sprites_{style}_third')
        c.emit('a7')
        c.op(0xCA,f'label_sprites_{style}_dx')
        c.op(0x11,f'label_sprites_{style}_b')
        c.jp(0x05B5)
        c.mark(f'label_sprites_{style}_dx')
        c.op(0x11,f'label_sprites_{style}_a')
        c.jp(0x05B5)
        c.mark(f'label_sprites_{style}_third')
        c.op(0x11,f'label_sprites_{style}_c')
        c.jp(0x05B5)
        for name,suffix in zip(['a','b','c'],LABEL_SHADOW_TILES):
            c.mark(f'label_sprites_{style}_{name}')
            for x,tile in [(0x18,suffix),(0x10,0x9B),(8,0xB4),(0,0xB3),
                           (0xF8,0xB2),(0xF0,0xB1),(0xE8,0xB0)]:
                c.emit(f'f8 {x:02x} {tile:02x} {attribute:02x}')
            c.emit('80')

    # Menu display routine runs in VBlank. Refresh just the 1/2/3 suffix and
    # preserve the VRAM bank and the routine's original entry instruction.
    c.mark('indicator')
    c.emit('f0 4f f5 af e0 4f')
    c.ld_a(ENABLED)
    c.emit('c6 a1')  # font 1/2/3 = $A1/$A2/$A3
    c.store_a(0x99A9)
    c.call('draw_pinch_map')
    c.emit('f1 e0 4f')
    c.ld_a(0xC0A8)
    c.jp(0x4C64)
    return c


def build(dx: bytes, original: bytes, bombliss: bytes):
    rom = bytearray(dx) + bytearray([0xFF]) * len(dx)
    code = control_code()
    payload = code.finish()
    edits = []
    def patch(offset, expected, replacement, description):
        expected = bytes.fromhex(expected) if isinstance(expected, str) else bytes(expected)
        replacement = bytes(replacement)
        if rom[offset:offset+len(expected)] != expected:
            raise ValueError(f'Unexpected bytes at ${offset:06X}: {description}')
        if len(expected) != len(replacement):
            raise ValueError('Patch size mismatch')
        rom[offset:offset+len(replacement)] = replacement
        edits.append({'offset': f'0x{offset:06X}', 'size': len(replacement), 'description': description})

    # Keep original ROM addresses for every call, music pointer, instrument
    # and waveform. Only WRAM operands in actual code need relocation.
    engine = bytearray(original[0x6480:0x8000])
    for start, end in [(0x697C,0x698B),(0x6998,0x69DD),(0x6A21,0x6ABE),(0x6B02,0x6DCB)]:
        pos = start
        while pos < end:
            size, _ = instruction(original, pos)
            if size == 3 and original[pos] in (0x01,0x11,0x21,0x31,0xEA,0xFA,0x08):
                addr = int.from_bytes(original[pos+1:pos+3], 'little')
                if 0xDF70 <= addr <= 0xDFFF:
                    rel = pos - 0x6480 + 1
                    engine[rel:rel+2] = (addr-0x70).to_bytes(2,'little')
            pos += size
        if pos != end: raise ValueError('Instruction range ends inside an instruction')
    # The noise instrument copier compares E against a literal low address.
    # Relocate this bound as well as the 16-bit pointer operands above.
    assert original[0x6CC8:0x6CCA] == bytes.fromhex('fe cb')
    engine[0x6CC9-0x6480] = 0x5B
    # Guard the original player's two wave writes while DX owns that channel.
    assert original[0x6B7D:0x6B80] == bytes.fromhex('e5 af e0')
    engine[0x6B7D-0x6480:0x6B89-0x6480] = bytes([0xC3])+code.labels['wave_command'].to_bytes(2,'little')+bytes(9)
    assert original[0x6BEB:0x6BEF] == bytes.fromhex('3e 40 e0 1c')
    engine[0x6BEB-0x6480:0x6BEF-0x6480] = bytes([0xCD])+code.labels['wave_decay'].to_bytes(2,'little')+bytes(1)
    for variant, (name, semitones) in enumerate(VARIANTS):
        music = bytearray(engine)
        if variant:
            # B-TYPE's first two sections total 192 music ticks (3.2 seconds).
            # Start all tonal parts at section 3, and loop back there too. The
            # 48-tick percussion loop already aligns with that boundary.
            for address, before, after in [
                (0x6F79,0x72C6,0x72CA), (0x6F7B,0x72B8,0x72BC),
                (0x6F7D,0x72D4,0x72D8), (0x72D2,0x72C6,0x72CA),
                (0x72C4,0x72B8,0x72BC), (0x7300,0x72D4,0x72D8),
            ]:
                assert int.from_bytes(original[address:address+2],'little') == before
                music[address-0x6480:address-0x6480+2] = after.to_bytes(2,'little')
            # Preserve note streams, durations, rests, instruments and noise
            # timbres. GB B's wave part also indexes past NotePitches into the
            # following noise table ($94 -> $6E96). Use a private full-range
            # pitch lookup rather than changing bytes shared with percussion.
            pitches = bytearray(original[0x6E02:0x6F02])
            for address in range(0x6E04, 0x6F02, 2):
                frequency = int.from_bytes(original[address:address+2], 'little')
                raised = round(2048-(2048-(frequency&0x7FF))/2**(semitones/12))
                pitches[address-0x6E02:address-0x6E02+2] = (raised|(frequency&0xF800)).to_bytes(2,'little')
            assert original[0x6CAA:0x6CAD] == bytes.fromhex('21 02 6e')
            music[0x6CAA-0x6480:0x6CAD-0x6480] = bytes.fromhex('21 00 60')
            pitch_destination = (ENGINE_BANK+variant)*0x4000+0x2000
            patch(pitch_destination, bytes([0xFF])*len(pitches), pitches,
                  f'{name} private transposed pitch table (including GB B extended notes)')
        destination = (ENGINE_BANK+variant) * 0x4000 + 0x2480
        patch(destination, bytes([0xFF])*len(music), music,
              f'{name} GB music engine/data, WRAM relocated; +{semitones} semitones, tempo {4+variant}/4'
              + ('; B-TYPE intro skipped at start and loop' if variant else ''))
    bom_symbols = {}
    for variant,(name,semitones) in enumerate(VARIANTS):
        bank, symbols = compile_player(bombliss,Code,semitones)
        patch((BOM_BANK+variant)*0x4000, bytes([0xFF])*0x4000, bank,
              f'{name} Bombliss driver and CONTEST/PUZZLE/TRAINING music; +{semitones} semitones'
              + ('; B-TYPE opening pickup skipped' if variant else ''))
        bom_symbols[name] = {k:f'0x{v:04X}' for k,v in symbols.items()}
    if code.origin + len(payload) > 0x3F00: raise ValueError('Control code exceeds free ROM0 region')
    patch(code.origin, bytes(len(payload)), payload, 'Music-bank control code')
    def hook(offset, expected, label, opcode=0xCD):
        patch(offset, expected, bytes([opcode])+code.labels[label].to_bytes(2,'little'), label)
    hook(0x023A, 'cd 00 3f', 'init')
    hook(0x023D, 'cd 06 3f', 'frame')
    hook(0x3F12, 'cd 60 3f', 'bgm_start', 0xC3)
    hook(0x3F18, 'cd 60 3f', 'bgm_conditional', 0xC3)
    hook(0x3F24, 'cd 60 3f', 'pause', 0xC3)
    hook(0x3F2A, 'cd 60 3f', 'resume', 0xC3)
    hook(0x14C48, 'cd 6f 4c', 'menu_modes')
    hook(0x14C5B, 'cd ab 4c', 'menu_music')
    hook(0x14C01, 'ea a8 c0', 'menu_layout')
    hook(0x14D6C, 'cd b5 05', 'label_sprites')
    hook(0x14DB5, 'cd 9b 43', 'label_sprites_dmg')
    hook(0x14C61, 'fa a8 c0', 'indicator', 0xC3)
    patch(0x4C47, 'fe 0b 30 05', bytes([0xC3])+code.labels['height_music'].to_bytes(2,'little')+bytes(1),
          'PINCH setting gates high-stack music for all three pages')
    hook(0x30B0C, 'cd 78 41', 'select_effect_pulse')
    rom[0x148] = 0x05  # 1 MiB, still MBC1 + RAM + battery.
    rom[0x14D] = (-sum(rom[0x134:0x14D])-25) & 0xFF
    rom[0x14E:0x150] = b'\0\0'
    rom[0x14E:0x150] = (sum(rom) & 0xFFFF).to_bytes(2,'big')
    return rom, {'patches': edits, 'symbols': {k: f'0x{v:04X}' for k,v in code.labels.items()},
                 'bombliss_symbols': bom_symbols,
                 'output_sha256': hashlib.sha256(rom).hexdigest()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--originals', type=Path, default=ROOT/'original_roms')
    parser.add_argument('--output', type=Path, default=ROOT/'output'/OUTPUT_NAME)
    args = parser.parse_args()
    inputs = {}
    for name, expected in HASHES.items():
        path = args.originals/name
        data = path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        if digest != expected: parser.error(f'Unsupported ROM revision: {path}\nExpected {expected}\nGot {digest}')
        if args.output.resolve() == path.resolve(): parser.error('Output must not replace an original ROM')
        inputs[name] = data
    if (ROOT/'original_roms').resolve() in args.output.resolve().parents:
        parser.error('Write output outside original_roms')
    rom, report = build(inputs[DX_NAME], inputs[GB_NAME], inputs[BOM_NAME])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(rom)
    report['inputs'] = HASHES
    args.output.with_suffix('.build.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print(f'Built {args.output} ({len(rom):,} bytes)')
    print(f'SHA-256: {report["output_sha256"]}')


if __name__ == '__main__':
    main()
