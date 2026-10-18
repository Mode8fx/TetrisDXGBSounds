"""Emulator integration checks. Install scripts/requirements-test.txt first."""
from __future__ import annotations

import argparse
import io
import json
from pathlib import Path

import numpy as np
from pyboy import PyBoy

from build_rom import (ROOT, DX_NAME, GB_NAME, OUTPUT_NAME, HASHES, ENABLED,
                       ACTIVE, PAUSED, LAST_ID, ENGINE_BANK, CURRENT_MUSIC)
from build_rom import control_code

OUT = ROOT/'output'/'validation'
BOOT_STEPS = '600,start,120,a,120,a,120,a,8,start,120,b,120,up,8,up,8,a,120,right,8,a,120'


def emulator(path, cgb=True):
    gb = PyBoy(io.BytesIO(path.read_bytes()), ram_file=io.BytesIO(bytes(0x2000)),
               window='null', cgb=cgb, sound_sample_rate=48000)
    gb.set_emulation_speed(0)
    return gb


def press(gb, button, hold=5, settle=5):
    gb.button_press(button)
    gb.tick(hold, True, True)
    gb.button_release(button)
    gb.tick(settle, True, True)


def boot_menu(gb):
    # Create an A profile via the normal UI on a blank save, then use Guest.
    for step in BOOT_STEPS.split(','):
        if step.isdigit(): gb.tick(int(step), True, False)
        else: press(gb, step)
    assert gb.memory[0xC0A0] == 4, 'Did not reach game/music menu'
    assert gb.memory[0xDB0D] == 1


def snapshot(gb):
    state = io.BytesIO()
    gb.save_state(state)
    return state.getvalue()


def restore(gb, state):
    gb.load_state(io.BytesIO(state))


def sound_energy(gb, frames=60):
    energy = 0
    for _ in range(frames):
        gb.tick(1, False, True)
        energy += int(np.abs(gb.sound.ndarray.astype(np.int16)).sum())
    return energy


def ui_checks(path, cgb):
    gb = emulator(path, cgb)
    boot_menu(gb)
    initial = snapshot(gb)
    mode = 'cgb' if cgb else 'dmg'
    gb.screen.image.save(OUT/f'{mode}-dx-menu.png')
    assert gb.memory[ENABLED] == 0
    menu_song = gb.memory[0xDC9B]
    assert bytes(gb.memory[0,0x99A9:0x99AB]) == bytes([0x7F,0x7F])
    press(gb,'select',hold=120)
    assert gb.memory[ENABLED] == 1, 'Held Select must toggle only once'
    assert gb.memory[ACTIVE] == 0, 'Mode-row Select must not start a preview'
    assert gb.memory[0xDC9B] == menu_song, 'Mode-row Select must retain menu music'
    gb.tick(1,True,False)
    gb.screen.image.save(OUT/f'{mode}-gb-menu.png')
    press(gb,'a')
    assert gb.memory[ACTIVE] == 1 and gb.memory[CURRENT_MUSIC] == 5, 'Entering music focus must preview the selected GB set'
    press(gb,'b')
    assert gb.memory[0xDB0E] == 0 and gb.memory[ACTIVE] == 0
    press(gb,'select')
    assert gb.memory[ENABLED] == 0 and gb.memory[ACTIVE] == 0
    assert gb.memory[0xDC9B] == menu_song

    # Music focus is a separate menu input routine; verify both hooks.
    press(gb,'a')
    assert gb.memory[0xDB0E] == 1
    for enabled in [1,0]:
        press(gb,'select')
        assert gb.memory[ENABLED] == enabled
        assert bytes(gb.memory[0,0x99A9:0x99AB]) == bytes([0x7F,0x7F])
    # Native and imported A/B/C, and OFF, using only menu buttons.
    for enabled in [0,1]:
        if gb.memory[ENABLED] != enabled: press(gb,'select')
        for selection in [1,2,3,4]:
            while gb.memory[0xDB0D] > selection: press(gb,'up')
            while gb.memory[0xDB0D] < selection: press(gb,'down')
            if selection == 4:
                assert gb.memory[ACTIVE] == 0
                assert sound_energy(gb,120) == 0, 'OFF must silence the BGM'
                press(gb,'select')
                assert gb.memory[ACTIVE] == 0
                press(gb,'select')
            elif enabled:
                assert gb.memory[ACTIVE] == 1
                assert gb.memory[CURRENT_MUSIC] == selection+4
                assert gb.memory[LAST_ID] == selection+0x36
                assert sound_energy(gb) > 0
            else:
                assert gb.memory[ACTIVE] == 0
                assert gb.memory[0xDC9B] == selection+0x36
                assert sound_energy(gb) > 0

    # Start actual Marathon games for each imported song and check pause,
    # resume, and movement/rotation SFX without losing the selected song.
    for selection in [1,2,3]:
        restore(gb,initial)
        press(gb,'a')
        for _ in range(selection-1): press(gb,'down')
        press(gb,'select')
        press(gb,'a',settle=120)  # Level selection
        assert gb.memory[ACTIVE] == 0, 'Level selection retains native menu music'
        press(gb,'a',settle=120)  # Gameplay
        assert gb.memory[0xC0A0] == 5
        assert gb.memory[CURRENT_MUSIC] == selection+4
        assert gb.memory[LAST_ID] == selection+0x43
        assert sound_energy(gb) > 0
        press(gb,'select')
        assert gb.memory[ENABLED] == 1, 'Select outside the menu must not toggle music sets'
        press(gb,'start',settle=90)
        assert gb.memory[PAUSED] == 1
        paused_state = bytes(gb.memory[0xDF20:0xDF60])
        assert sound_energy(gb,60) == 0
        assert bytes(gb.memory[0xDF20:0xDF60]) == paused_state
        press(gb,'start',settle=60)
        assert gb.memory[PAUSED] == 0 and gb.memory[ACTIVE] == 1
        assert sound_energy(gb) > 0
        sfx_calls = []
        owned_samples = [0,0,0,0]
        ownership_errors = []
        before = []
        native_pan = [0]
        apu_ranges = [(0xFF10,0xFF15),(0xFF16,0xFF1A),(0xFF1A,0xFF1F),(0xFF20,0xFF24)]
        symbols = control_code().labels
        def native_done(_):
            before.clear()
            for addr,(lo,hi) in zip([0xDEA0,0xDEB8,0xDED0,0xDEE8],apu_ranges):
                before.append(bytes(gb.memory[lo:hi]) if gb.memory[addr]&0x80 else None)
            native_pan[0] = gb.memory[0xFF25]
        def music_done(_):
            for channel,((lo,hi),expected) in enumerate(zip(apu_ranges,before)):
                if expected is None: continue
                actual = bytes(gb.memory[lo:hi])
                if actual != expected:
                    ownership_errors.append(f'channel {channel+1}: {expected.hex()} -> {actual.hex()}')
                owned_samples[channel] += 1
        def mix_done(_):
            for channel,expected in enumerate(before):
                mask = [0x11,0x22,0x44,0x88][channel]
                if expected is not None and gb.memory[0xFF25]&mask != native_pan[0]&mask:
                    ownership_errors.append(f'Original music changed SFX panning on channel {channel+1}')
        def wave_load(_):
            # Active wave RAM reads return the current sample, not the whole
            # stored waveform. Check the write routine's ownership instead.
            if gb.memory[0xDED0]&0x80:
                ownership_errors.append('waveform loaded during native wave SFX')
        def note_write(_):
            channel = gb.memory[0xDF00]-1
            if gb.memory[[0xDEA0,0xDEB8,0xDED0,0xDEE8][channel]]&0x80:
                ownership_errors.append(f'note written during native SFX channel {channel+1}')
        def sfx(_): sfx_calls.append(gb.register_file.C)
        gb.hook_register(0,0x3F0C,sfx,None)
        gb.hook_register(0,symbols['frame_native_done'],native_done,None)
        gb.hook_register(0,symbols['frame_music_done'],music_done,None)
        gb.hook_register(0,symbols['frame_mix_done'],mix_done,None)
        gb.hook_register(ENGINE_BANK,0x6998,wave_load,None)
        gb.hook_register(ENGINE_BANK,0x6D21,note_write,None)
        for button in ['left','right','a','b','down']:
            press(gb,button)
        gb.hook_deregister(0,0x3F0C)
        gb.hook_deregister(0,symbols['frame_native_done'])
        gb.hook_deregister(0,symbols['frame_music_done'])
        gb.hook_deregister(0,symbols['frame_mix_done'])
        gb.hook_deregister(ENGINE_BANK,0x6998)
        gb.hook_deregister(ENGINE_BANK,0x6D21)
        assert sfx_calls, 'Movement/rotation must still invoke the native sound engine'
        assert sum(owned_samples) > 0, 'Did not observe native SFX channel ownership'
        assert not ownership_errors, f'Imported music changed active SFX: {ownership_errors[:3]}'
        assert gb.memory[ACTIVE] == 1 and gb.memory[CURRENT_MUSIC] == selection+4
        gb.screen.image.save(OUT/f'{mode}-gameplay-{selection}.png')
    gb.stop(save=False)
    return f'{mode}: mode-row Select stays silent, blank indicators, preview toggles/OFF, gameplay, pause/resume, original SFX ownership and stereo routing passed'


def note_trace(gb, bank, ram_shift):
    events = [[] for _ in range(4)]
    ticks = [0]
    def frame(_): ticks[0] += 1
    def note(_):
        channel = gb.memory[0xDF70+ram_shift]-1
        base = 0xDF90+ram_shift+channel*16
        # ROM pointer, duration, instrument/waveform, pitch, and rest flag.
        # Exclude WRAM addresses and locks, which intentionally differ.
        data = tuple(gb.memory[base+3:base+0xC])
        events[channel].append((ticks[0],data))
    gb.hook_register(bank,0x6C44,frame,None)
    gb.hook_register(bank,0x6D34,note,None)
    return events, ticks


def mix_checks(path, cgb):
    """Check actual native mixer outputs, including its write-only volumes."""
    gb = emulator(path,cgb)
    boot_menu(gb)
    menu = snapshot(gb)
    errors, counts = [], {'pulse/noise':0,'wave':0}
    pending_volume, pending_wave = [], []
    def volume_before(_):
        pending_volume[:] = [gb.register_file.B,gb.memory[0xDC94]]
    def volume_after(_):
        before,register = pending_volume
        expected = before
        if gb.register_file.B != expected:
            errors.append(f'Pulse/noise volume {before:02x} -> {gb.register_file.B:02x}, expected {expected:02x}')
        if gb.memory[ACTIVE] and register != 0x1C: counts['pulse/noise'] += 1
    def wave_before(_):
        raw = ((gb.register_file.B-0x40)&255)^0xC0
        level = ((raw>>1)|((raw&1)<<7))&0x60
        pending_wave[:] = [level]
        if gb.memory[ACTIVE]: counts['wave'] += 1
    def wave_after(_):
        if gb.register_file.A&0x60 != pending_wave[0]:
            errors.append('Wave volume differs from the native DX conversion')
    for address,callback in [(0x4FD8,volume_before),(0x4FDB,volume_after),
                             (0x4FF3,wave_before),(0x4FF9,wave_after)]:
        gb.hook_register(12,address,callback,None)
    for imported in [False,True]:
        restore(gb,menu)
        press(gb,'a')
        if imported: press(gb,'select')
        press(gb,'a',settle=120)
        press(gb,'a',settle=120)
        gameplay = snapshot(gb)
        # Feed representative native effects through the normal SFX API. This
        # covers pulse, noise, and wave effects without arranging line clears.
        desired_id = [0x21]
        def request(_): gb.register_file.C = desired_id[0]
        gb.hook_register(0,0x3F0C,request,None)
        for sound_id in [0x21,0x22,0x23,0x24,0x25,0x27,0x28,0x2B,0x2C,0x2D,0x2F,0x34,0x3C]:
            restore(gb,gameplay)
            desired_id[0] = sound_id
            press(gb,'a',settle=40)
        gb.hook_deregister(0,0x3F0C)
    assert not errors, errors[:3]
    assert all(counts.values()), f'Did not exercise all native volume paths: {counts}'
    gb.stop(save=False)
    return f'{"cgb" if cgb else "dmg"}: original pulse/noise and wave SFX volumes preserved with both music sets'


def sharing_checks(path,cgb):
    """SFX use their native channels while the GB score keeps its timing."""
    gb = emulator(path,cgb)
    boot_menu(gb)
    press(gb,'a')
    press(gb,'select')
    press(gb,'a',settle=120)
    press(gb,'a',settle=120)
    gameplay = snapshot(gb)
    def music_state():
        # The four locks intentionally change; sequence/timer/instrument state
        # must advance identically with and without sound effects.
        return tuple(gb.memory[0xDF20+i] for i in range(64) if i%16 != 15)
    expected = []
    for _ in range(60):
        gb.tick(1,False,False)
        expected.append(music_state())
    symbols = control_code().labels
    errors, routed, released = [], [], []
    current, previous, restores = set(), set(), set()
    observed = set()
    native_pan = [0]
    force_rest, rest_pattern, rest_checked = [False], [None], []
    original_music = (ROOT/'original_roms'/GB_NAME).read_bytes()
    def route_left(_):
        # Exercise a non-stereo native effect to catch the old blanket $FF
        # override. This modifies only this test run's SFX output instruction.
        index = [0x12,0x17,0x1C,0x21].index(gb.memory[0xDC94])
        gb.register_file.A &= ~(1<<index)
    def native_done(_):
        previous.clear()
        previous.update(current)
        current.clear()
        current.update(i for i,addr in enumerate([0xDEA0,0xDEB8,0xDED0,0xDEE8]) if gb.memory[addr]&0x80)
        observed.update(current)
        native_pan[0] = gb.memory[0xFF25]
        restores.clear()
        if force_rest[0] and 2 in previous-current:
            # Exercise release during a music rest, with no following note or
            # instrument command to hide a missing waveform restoration.
            gb.memory[0xDF4B] = 1
            gb.memory[0xDF42] = 20
            pointer = gb.memory[0xDF46] | gb.memory[0xDF47]<<8
            rest_pattern[0] = original_music[pointer:pointer+16]
    def restored(channel): restores.add(channel)
    def mix_done(_):
        for channel in current:
            mask = [0x11,0x22,0x44,0x88][channel]
            if gb.memory[0xFF25]&mask != native_pan[0]&mask:
                errors.append('Music changed native SFX routing')
            if native_pan[0]&mask == mask&0xF0: routed.append(channel)
        for channel in set(range(4))-current:
            mask = [0x11,0x22,0x44,0x88][channel]
            if gb.memory[0xFF25]&mask != mask:
                errors.append('Free music channel did not return to stereo')
        for channel in previous-current:
            if channel not in restores: errors.append('Released music channel was not restored immediately')
            released.append(channel)
        if rest_pattern[0] is not None:
            if gb.memory[0xFF1A]&0x80 or bytes(gb.memory[0xFF30:0xFF40]) != rest_pattern[0]:
                errors.append('Waveform was not restored while the GB music was resting')
            rest_checked.append(True)
            rest_pattern[0] = None
    desired_id = [0x21]
    def request(_): gb.register_file.C = desired_id[0]
    gb.hook_register(0,0x3F0C,request,None)
    gb.hook_register(12,0x4FC8,route_left,None)
    gb.hook_register(0,symbols['frame_native_done'],native_done,None)
    gb.hook_register(0,symbols['frame_mix_done'],mix_done,None)
    for channel in range(4): gb.hook_register(0,symbols[f'restore_{channel}'],restored,channel)
    for sound_id,channels in [(0x21,{1,2}),(0x22,{1}),(0x28,{1})]:
        restore(gb,gameplay)
        current.clear()
        previous.clear()
        observed.clear()
        desired_id[0] = sound_id
        gb.button_press('a')
        for frame in range(60):
            if frame == 5: gb.button_release('a')
            gb.tick(1,False,False)
            assert music_state() == expected[frame], f'Effect {sound_id:02x} changed GB score timing/state'
        assert observed == channels, f'Effect {sound_id:02x} changed native channel usage: {observed}'
    restore(gb,gameplay)
    current.clear()
    previous.clear()
    desired_id[0] = 0x21
    force_rest[0] = True
    press(gb,'a',settle=60)
    assert rest_checked, 'Did not exercise wave-channel release during a music rest'
    assert routed and released and not errors, errors[:3]
    gb.stop(save=False)
    return f'{"cgb" if cgb else "dmg"}: original SFX channels/routing, score timing and music/waveform restoration (including rests) verified'


def melody_checks(path, frames):
    dx = emulator(path)
    boot_menu(dx)
    press(dx,'a')
    base = snapshot(dx)
    report = []
    for selection in [1,2,3]:
        restore(dx,base)
        for _ in range(selection-1): press(dx,'down')
        # Collect before Select so the very first note is included.
        actual, dx_ticks = note_trace(dx,ENGINE_BANK,-0x70)
        press(dx,'select')
        dx.tick(frames,False,False)
        dx.hook_deregister(ENGINE_BANK,0x6C44)
        dx.hook_deregister(ENGINE_BANK,0x6D34)
        assert dx.memory[ACTIVE] == 1

        original = emulator(ROOT/'original_roms'/GB_NAME,False)
        original.tick(100,False,False)
        expected, original_ticks = note_trace(original,1,0)
        first = [True]
        def request(_):
            # Inject only the request in the unmodified source ROM's music API;
            # suppress title/demo requests for this isolated music comparison.
            original.memory[0xDFE8] = selection+4 if first[0] else 0
            first[0] = False
        original.hook_register(1,0x6A21,request,None)
        original.tick(frames+20,False,False)
        for channel in range(4):
            # Compare a shared time interval, including note timing, not just pitches.
            limit = min(dx_ticks[0],original_ticks[0])
            a = [e for e in actual[channel] if e[0] < limit]
            b = [e for e in expected[channel] if e[0] < limit]
            assert a == b, f'Track {selection}, channel {channel+1} differs: {a[:3]} vs {b[:3]}'
        count = sum(map(len,actual))
        assert count > frames//30, 'Not enough musical events captured'
        report.append(f'Tetris {"ABC"[selection-1]}: {count} note/rest events match the original ROM over {frames} frames')
        original.stop(save=False)
    dx.stop(save=False)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rom',type=Path,default=ROOT/'output'/OUTPUT_NAME)
    parser.add_argument('--frames',type=int,default=7200,help='Frames per melody comparison (default: two minutes)')
    args = parser.parse_args()
    OUT.mkdir(parents=True,exist_ok=True)
    rom = args.rom.read_bytes()
    original_dx = (ROOT/'original_roms'/DX_NAME).read_bytes()
    assert rom[0x30000:0x34000] == original_dx[0x30000:0x34000], 'Native DX sound driver and SFX must remain unmodified'
    assert len(rom) == 1024*1024 and rom[0x148] == 5
    assert rom[0x14D] == (-sum(rom[0x134:0x14D])-25)&255
    assert int.from_bytes(rom[0x14E:0x150],'big') == (sum(rom)-sum(rom[0x14E:0x150]))&65535
    report = [ui_checks(args.rom,True),ui_checks(args.rom,False)]
    report.extend([mix_checks(args.rom,True),mix_checks(args.rom,False)])
    report.extend([sharing_checks(args.rom,True),sharing_checks(args.rom,False)])
    report.extend(melody_checks(args.rom,args.frames))
    import hashlib
    for name,digest in HASHES.items():
        assert hashlib.sha256((ROOT/'original_roms'/name).read_bytes()).hexdigest() == digest
    (OUT/'results.json').write_text(json.dumps({'rom_sha256':hashlib.sha256(rom).hexdigest(),'checks':report},indent=2)+'\n')
    print('\n'.join(report))
    print('ROM checksums and unchanged originals verified.')


if __name__ == '__main__':
    main()
