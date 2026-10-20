"""Locate Super Bombliss DX stage music; optionally verify through normal gameplay.

Static inspection needs only Python. --verify needs requirements-test.txt and
boots the original ROM in PyBoy, without an Ares save state or ROM modifications.
Reports/screenshots are written under output/bombliss-research.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ROM_NAME = 'Super Bombliss DX (Japan) (En) (SGB Enhanced) (GB Compatible).gbc'
SHA256 = 'c282366d16bb443bc0cf234732d28f941996d23b8e6678a6a0f6771b04b02ad5'
BANK = 5
TABLE = 0x4F8E
CHANNELS = ('pulse 1', 'pulse 2', 'wave', 'noise')
# The mode startup routine $1532 indexes $159C with WRAM $C6B5.
MODES = {'CONTEST': (2, 0, 3), 'PUZZLE': (4, 1, 3), 'TRAINING': (1, 2, 2)}


def offset(cpu):
    if not 0x4000 <= cpu < 0x8000:
        raise ValueError(f'Address outside banked ROM: ${cpu:04X}')
    return BANK * 0x4000 + cpu - 0x4000


def locate(rom):
    songs = {}
    for mode, (selection, _, _) in MODES.items():
        song_id = rom[0x159C + selection]
        streams = []
        for channel in range(4):
            entry = TABLE + (song_id + channel) * 4
            raw = rom[offset(entry):offset(entry) + 4]
            slot, flags = raw[:2]
            assert slot == (channel + 2) * 0x19 and flags & 3 == channel
            start = int.from_bytes(raw[2:], 'little')
            # Four-byte setup header, then two-byte score commands. $FF is
            # a one-byte terminator; it can also occur as a command operand.
            end = start + 4
            while end < 0x8000 and rom[offset(end)] != 0xFF:
                end += 2
            if end >= 0x8000:
                raise ValueError(f'Unterminated {mode} {CHANNELS[channel]} stream')
            streams.append({
                'channel': CHANNELS[channel], 'voice_id': song_id + channel,
                'entry_cpu': entry, 'entry_offset': offset(entry),
                'entry_hex': raw.hex(' '), 'wram_slot': 0xC300 + slot,
                'start_cpu': start, 'start_offset': offset(start),
                'end_cpu_inclusive': end, 'end_offset_inclusive': offset(end),
                'length': end + 1 - start,
                'header_hex': rom[offset(start):offset(start) + 4].hex(' '),
            })
        songs[mode] = {'song_id': song_id, 'selection_index': selection,
                       'bank': BANK, 'streams': streams}
    return songs


def verify(rom, songs, out, frames):
    from pyboy import PyBoy

    gb = PyBoy(io.BytesIO(rom), window='null', cgb=True,
               ram_file=io.BytesIO(bytes(0x2000)))
    gb.set_emulation_speed(0)

    def press(button, settle=120):
        gb.button_press(button)
        gb.tick(2, True, False)
        gb.button_release(button)
        gb.tick(settle, True, False)

    try:
        # On an empty save the title advances to the one-player menu.
        gb.tick(1200, True, False)
        press('start')
        gb.screen.image.save(out / 'mode-selection.png')
        state = io.BytesIO()
        gb.save_state(state)
        current = {'mode': None}
        requests, setups, reads = [], [], {}

        def request(_):
            registers = gb.register_file
            sp = registers.SP
            requests.append({
                'frame': gb.frame_count, 'song_id': registers.A,
                'caller_bank': gb.memory[0x4000],
                'return_cpu': gb.memory[sp] | gb.memory[sp + 1] << 8,
                'selection_index': gb.memory[0xC6B5],
            })

        def setup(_):
            # Immediately after the table entry is installed in channel RAM.
            voice_id = gb.memory[0xC39E]
            song = songs[current['mode']]
            for stream in song['streams']:
                if voice_id == stream['voice_id']:
                    slot = stream['wram_slot']
                    pointer = gb.memory[slot + 2] | gb.memory[slot + 3] << 8
                    setups.append({'voice_id': voice_id, 'pointer_cpu': pointer,
                                   'wram_slot': slot})

        def read_command(_):
            # $1F1E reads the next score opcode at HL (before incrementing HL).
            registers = gb.register_file
            cpu = registers.HL
            if gb.memory[0x4000] != BANK:
                return
            slot = 0xC300 + gb.memory[0xC39D] * 0x19
            for stream in songs[current['mode']]['streams']:
                if slot == stream['wram_slot'] and (
                        stream['start_cpu'] + 4 <= cpu <= stream['end_cpu_inclusive']):
                    reads.setdefault(stream['channel'], set()).add(cpu)

        gb.hook_register(0, 0x1D48, request, None)
        gb.hook_register(0, 0x1DA9, setup, None)
        gb.hook_register(0, 0x1F1E, read_command, None)
        results = {}
        for mode, (_, down_count, confirmations) in MODES.items():
            gb.load_state(io.BytesIO(state.getvalue()))
            current['mode'] = mode
            requests.clear()
            setups.clear()
            reads.clear()
            for _ in range(down_count):
                press('down', 20)
            gb.screen.image.save(out / f'{mode.lower()}-selected.png')
            # CONTEST: New Game -> Stage Select -> Play.
            # PUZZLE: New Game -> Level Select -> Play.
            # TRAINING: Speed Select -> Play (default Auto speed).
            for _ in range(confirmations):
                press('a')
            gb.tick(frames, True, False)
            gb.screen.image.save(out / f'{mode.lower()}-stage.png')
            matching = [r for r in requests if r['song_id'] == songs[mode]['song_id']]
            assert matching and matching[0]['return_cpu'] == 0x153D, (mode, requests)
            assert matching[0]['selection_index'] == songs[mode]['selection_index']
            assert {s['voice_id'] for s in setups} == {
                s['voice_id'] for s in songs[mode]['streams']}, (mode, setups)
            expected_pointers = {s['voice_id']: s['start_cpu'] for s in songs[mode]['streams']}
            assert all(s['pointer_cpu'] == expected_pointers[s['voice_id']]
                       for s in setups), (mode, setups)
            assert set(reads) == set(CHANNELS), (mode, reads)
            results[mode] = {
                'requests': list(requests), 'channel_setups': list(setups),
                'observed_commands': {
                    channel: {'unique_addresses': len(addresses),
                              'first_cpu': min(addresses), 'last_cpu': max(addresses)}
                    for channel, addresses in reads.items()},
                'extra_playback_frames': frames,
            }
        return results
    finally:
        gb.stop(save=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rom', type=Path, default=ROOT / 'original_roms' / ROM_NAME)
    parser.add_argument('--verify', action='store_true', help='Trace all three modes in PyBoy')
    parser.add_argument('--frames', type=int, default=480, help='Extra gameplay frames per mode')
    parser.add_argument('--output', type=Path, default=ROOT / 'output' / 'bombliss-research')
    args = parser.parse_args()
    if args.frames < 1:
        parser.error('--frames must be positive')
    rom = args.rom.read_bytes()
    digest = hashlib.sha256(rom).hexdigest()
    if digest != SHA256:
        parser.error(f'Unexpected ROM SHA-256: {digest}')
    songs = locate(rom)
    report = {'rom': args.rom.name, 'sha256': digest,
              'addresses': 'Numeric addresses are integers; file offsets are zero-based.',
              'table_cpu': TABLE, 'table_offset': offset(TABLE), 'songs': songs}
    args.output.mkdir(parents=True, exist_ok=True)
    if args.verify:
        report['verification'] = verify(rom, songs, args.output, args.frames)
    for mode, song in songs.items():
        print(f"{mode}: request ${song['song_id']:02X}, table offset ${song['streams'][0]['entry_offset']:06X}")
        for stream in song['streams']:
            print(f"  {stream['channel']:7s} {BANK:02X}:{stream['start_cpu']:04X}"
                  f" = file ${stream['start_offset']:06X}..${stream['end_offset_inclusive']:06X}"
                  f" ({stream['length']} bytes)")
    report_path = args.output / ('verified-addresses.json' if args.verify else 'addresses.json')
    report_path.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(f"{'Verified through gameplay. ' if args.verify else ''}Report: {report_path}")


if __name__ == '__main__':
    main()
