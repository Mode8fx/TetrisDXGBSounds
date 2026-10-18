"""Build Tetris DX with a Select-controlled original-Tetris music bank.

Only Python's standard library is required. Input revisions are checked by SHA-256.
See scripts/ROM_NOTES.md for the verified hook addresses and memory layout.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from gb_disasm import instruction

ROOT = Path(__file__).resolve().parents[1]
DX_NAME = 'Tetris DX (World) (SGB Enhanced) (GB Compatible).gbc'
GB_NAME = 'Tetris (World) (Rev 1).gb'
OUTPUT_NAME = 'Tetris DX - Additional Music.gbc'
HASHES = {
    DX_NAME: 'd349dc93423c6abcd775d3b6a8797df715a44a42ec837afb21bf17ae43b40a9e',
    GB_NAME: '0d6535aef23969c7e5af2b077acaddb4a445b3d0df7bf34c8acef07b51b015c3',
}

# Original engine DF70..DFFF is moved down $70, leaving DX's DC80..DEFF
# and the stack at DFFF alone. Control bytes follow it at DF90.
ENABLED, ACTIVE, PAUSED, SAVED_BANK, LAST_ID = range(0xDF90, 0xDF95)
FORCE_RESTART, RESTORE_ALL = 0xDF95, 0xDF96
NATIVE_PAN = 0xDF97
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
    for var in [ENABLED, ACTIVE, PAUSED, LAST_ID, FORCE_RESTART, RESTORE_ALL, NATIVE_PAN]: c.store_a(var)
    c.jp(0x3F00)

    # All expanded-bank execution is interrupt protected. MBC1's high bank
    # register must be restored before DX's ordinary bank-switching code runs.
    c.mark('bank_in')
    c.emit('f3 f0 fd')  # di / ldh a, [current bank]
    c.store_a(SAVED_BANK)
    c.emit('3e 01 ea 00 40 ea 00 20 3e 21 e0 fd c9')
    c.mark('bank_out')
    c.emit('af ea 00 40')
    c.ld_a(SAVED_BANK)
    c.emit('e0 fd ea 00 20 fb c9')

    c.mark('stop_original')
    c.ld_a(ACTIVE)
    c.emit('a7 c8')
    c.emit('c5 d5 e5')
    c.call('bank_in')
    c.call(MUTE)
    c.call('bank_out')
    c.emit('af')
    for var in [ACTIVE, PAUSED, CURRENT_MUSIC]: c.store_a(var)
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
    c.op(0xD2, 'native_bgm')
    c.emit('d6 3f')  # $44..46 -> original song IDs 5..7
    c.jp('original_bgm')
    c.mark('preview')
    c.emit('d6 32')  # $37..39 -> original song IDs 5..7
    c.mark('original_bgm')
    c.emit('c5 f5')
    c.emit('79')
    c.store_a(LAST_ID)
    c.emit('01 00 00')
    c.call('native_start')  # Stop DX BGM, retaining its SFX voices.
    c.call('bank_in')
    c.emit('f1')
    c.store_a(NEW_MUSIC)
    c.call(0x69A5)  # Clear locks / init original player.
    c.call(START_MUSIC)
    c.emit('3e 01')
    c.store_a(ACTIVE)
    c.emit('af')
    c.store_a(PAUSED)
    c.store_a(RESTORE_ALL)
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
    c.call(MUTE)
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
    c.mark('frame_music_done')
    # Music is stereo on free channels. Effect-owned channels retain exactly
    # the routing/mute bits produced by DX, even across later music frames.
    c.emit('0e 00')
    for channel,mask in enumerate([0x11,0x22,0x44,0x88]):
        c.ld_a(0xDF2F+channel*16)
        c.emit('e6 80')
        c.op(0xC2,f'pan_done_{channel}')
        c.emit(f'79 f6 {mask:02x} 4f')
        c.mark(f'pan_done_{channel}')
    c.ld_a(NATIVE_PAN)
    c.emit('b1 e0 25')
    c.mark('frame_mix_done')
    c.call('bank_out')
    c.emit('e1 d1 c1 c9')

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
    # instrument type (+$0B). Route only movement/rotation SFX to pulse 1.
    # The original selectors initialize the same channel state and I/O base;
    # all note streams, envelopes, priorities and durations remain native.
    c.mark('select_effect_pulse')
    c.ld_a(ACTIVE)
    c.emit('a7')
    c.op(0xCA,'native_pulse_2')
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
    c.call('toggle')
    c.jp(0x4C6F)
    c.mark('menu_music')
    c.call('toggle')
    c.jp(0x4CAB)
    c.mark('toggle')
    c.emit('f0 83 e6 04 c8')  # Newly pressed Select, not the held-key mask.
    c.ld_a(ENABLED)
    c.emit('ee 01')
    c.store_a(ENABLED)
    # Changing the set on a mode row must not start a music preview.
    c.ld_a(0xDB0E)
    c.emit('a7 c8')
    # When music has focus, restart the selected preview immediately.
    c.ld_a(0xDB0D)
    c.emit('fe 04')
    c.op(0xD2, 'toggle_off')
    c.emit('c6 36 4f 06 00')
    c.jp('bgm_start')
    c.mark('toggle_off')
    c.emit('01 00 00')
    c.jp('bgm_start')

    # Menu display routine runs in VBlank. Preserve the VRAM bank and keep
    # its original entry instructions so no LCD-time writes are introduced.
    c.mark('indicator')
    c.emit('f0 4f f5 af e0 4f')
    c.emit('3e 7f')  # font's space character, in both former indicator cells
    c.store_a(0x99A9)
    c.store_a(0x99AA)
    c.emit('f1 e0 4f')
    c.ld_a(0xC0A8)
    c.jp(0x4C64)
    return c


def build(dx: bytes, original: bytes):
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
    destination = ENGINE_BANK * 0x4000 + 0x2480
    patch(destination, bytes([0xFF])*len(engine), engine, 'Original Tetris engine and song data, WRAM relocated')
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
    hook(0x14C61, 'fa a8 c0', 'indicator', 0xC3)
    hook(0x30B0C, 'cd 78 41', 'select_effect_pulse')
    rom[0x148] = 0x05  # 1 MiB, still MBC1 + RAM + battery.
    rom[0x14D] = (-sum(rom[0x134:0x14D])-25) & 0xFF
    rom[0x14E:0x150] = b'\0\0'
    rom[0x14E:0x150] = (sum(rom) & 0xFFFF).to_bytes(2,'big')
    return rom, {'patches': edits, 'symbols': {k: f'0x{v:04X}' for k,v in code.labels.items()},
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
        if args.output.resolve() == path.resolve(): parser.error('Output must not replace either original ROM')
        inputs[name] = data
    if (ROOT/'original_roms').resolve() in args.output.resolve().parents:
        parser.error('Write output outside original_roms')
    rom, report = build(inputs[DX_NAME], inputs[GB_NAME])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(rom)
    report['inputs'] = HASHES
    args.output.with_suffix('.build.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print(f'Built {args.output} ({len(rom):,} bytes)')
    print(f'SHA-256: {report["output_sha256"]}')


if __name__ == '__main__':
    main()
