"""Emulator integration checks. Install scripts/requirements-test.txt first."""
from __future__ import annotations

import argparse
import io
import json
from pathlib import Path

import numpy as np
from pyboy import PyBoy

from build_rom import (ROOT, DX_NAME, GB_NAME, OUTPUT_NAME, HASHES, ENABLED,
                       ACTIVE, PAUSED, LAST_ID, ENGINE_BANK, CURRENT_MUSIC,
                       MUSIC_VARIANT, MUSIC_PHASE, START_MUSIC, PINCH_ENABLED,
                       PINCH_TILES, PINCH_BACKDROP, LABEL_SHADOW_TILES)
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
    from PIL import ImageChops
    native = emulator(ROOT/'original_roms'/DX_NAME,cgb)
    boot_menu(native)
    backdrop_map = bytes(native.memory[0,0x9A20:0x9A2A])
    backdrop_image = native.screen.image.crop((0,136,80,144)).convert('RGB')
    native.stop(save=False)
    gb = emulator(path, cgb)
    boot_menu(gb)
    initial = snapshot(gb)
    mode = 'cgb' if cgb else 'dmg'
    def check_music_tab():
        suffix = 0xA1+gb.memory[ENABLED]
        assert bytes(gb.memory[0,0x9982:0x9984]) == bytes.fromhex('29 2a')
        assert bytes(gb.memory[0,0x99C2:0x99C4]) == bytes.fromhex('49 4a')
        assert bytes(gb.memory[0,0x99A2:0x99AB]) == bytes.fromhex('39 8c 94 92 88 82 9f')+bytes([suffix,0x7F]), 'Extended tab must read MUSIC-1/2/3'
        for source, shadow in [(0x8A10,0x8C80),(0x8A20,0x8C90),(0x8A30,0x8D00)]:
            for row in range(8):
                bits = gb.memory[0,source+row*2+1]
                assert bytes(gb.memory[0,shadow+row*2:shadow+row*2+2]) == bytes([bits^255,bits]), 'Inactive label must use digit glyphs'
        if cgb:
            assert bytes(gb.memory[1,0x99A3:0x99AA]) == bytes([7])*7
            assert gb.memory[1,0x99A2] == 1, 'Border palette changed'
        music_focus = bool(gb.memory[0xDB0E])
        expected_map = (PINCH_TILES[:10] if gb.memory[PINCH_ENABLED] else PINCH_TILES[10:]) if music_focus else backdrop_map
        assert bytes(gb.memory[PINCH_BACKDROP:PINCH_BACKDROP+10]) == backdrop_map
        assert bytes(gb.memory[0,0x9A20:0x9A2A]) == expected_map, 'PINCH strip does not reflect setting/focus'
        if cgb:
            assert bytes(gb.memory[1,0x9A20:0x9A2A]) == bytes([2])*10
        # Check a visible pixel of P, using its actual native font mask.
        x,y = next((x,y) for y in range(8) for x in range(8)
                   if gb.memory[0,0x88F1+y*2]&(128>>x))
        rgb = gb.screen.image.getpixel((8+x,136+y))[:3]
        if music_focus:
            assert rgb == (0,0,0), 'PINCH must be black in music focus'
        else:
            current = gb.screen.image.crop((0,136,80,144)).convert('RGB')
            assert ImageChops.difference(current,backdrop_image).getbbox() is None, 'PINCH must be entirely hidden on mode rows'
        if not gb.memory[0xDB0E]:
            sprites = [tuple(gb.memory[a:a+4]) for a in range(0xFE00,0xFEA0,4)]
            for x,tile in zip(range(0x20,0x51,8),[0xB0,0xB1,0xB2,0xB3,0xB4,0x9B,LABEL_SHADOW_TILES[gb.memory[ENABLED]]]):
                assert (0x78,x,tile,0x14 if cgb else 0) in sprites, 'Highlight must cover all seven characters at the new position'
    gb.screen.image.save(OUT/f'{mode}-dx-menu.png')
    assert gb.memory[ENABLED] == 0 and gb.memory[PINCH_ENABLED] == 1
    menu_song = gb.memory[0xDC9B]
    check_music_tab()
    for button in ['left','right']:
        press(gb,button,hold=20)
        assert gb.memory[ENABLED] == 0 and gb.memory[ACTIVE] == 0, 'Mode rows must not switch music sets'
    press(gb,'select',hold=120)
    assert gb.memory[PINCH_ENABLED] == 1, 'Mode rows must not toggle PINCH'
    assert gb.memory[ENABLED] == 0 and gb.memory[ACTIVE] == 0
    assert gb.memory[0xDC9B] == menu_song
    check_music_tab()
    press(gb,'a')
    assert gb.memory[ACTIVE] == 0 and gb.memory[0xDB0E] == 1
    check_music_tab()
    preview_song = gb.memory[0xDC9B]
    press(gb,'select',hold=120)
    assert gb.memory[PINCH_ENABLED] == 0, 'Held Select must toggle PINCH only once'
    assert gb.memory[0xDC9B] == preview_song, 'PINCH change must retain preview music'
    check_music_tab()
    gb.screen.image.save(OUT/f'{mode}-pinch-off.png')
    press(gb,'b')
    check_music_tab()
    gb.screen.image.save(OUT/f'{mode}-pinch-off-inactive.png')
    press(gb,'select',hold=120)
    assert gb.memory[PINCH_ENABLED] == 0, 'Mode rows must retain PINCH OFF'
    check_music_tab()
    press(gb,'a')
    check_music_tab()
    press(gb,'select')
    assert gb.memory[PINCH_ENABLED] == 1
    press(gb,'right',hold=120)
    assert gb.memory[ENABLED] == 1, 'Held Right must switch only once'
    assert gb.memory[ACTIVE] == 1 and gb.memory[CURRENT_MUSIC] == 5
    gb.tick(1,True,False)
    gb.screen.image.save(OUT/f'{mode}-gb-menu.png')
    check_music_tab()
    gb.screen.image.save(OUT/f'{mode}-gb-music-focus.png')
    press(gb,'b')
    assert gb.memory[0xDB0E] == 0 and gb.memory[ACTIVE] == 0
    check_music_tab()
    for button in ['left','right']:
        press(gb,button)
        assert gb.memory[ENABLED] == 1 and gb.memory[ACTIVE] == 0
    assert gb.memory[0xDC9B] == menu_song

    # Music focus is a separate menu input routine; verify both hooks.
    press(gb,'a')
    assert gb.memory[0xDB0E] == 1
    for enabled,button in [(0,'left'),(1,'right'),(2,'right'),(0,'right')]:
        press(gb,button)
        assert gb.memory[ENABLED] == enabled
        check_music_tab()
        gb.screen.image.save(OUT/f'{mode}-{("dx","gb","bombliss")[enabled]}-music-focus.png')
    native_song = gb.memory[0xDC9B]
    press(gb,'select',hold=120)
    assert gb.memory[PINCH_ENABLED] == 0 and gb.memory[ENABLED] == 0
    assert gb.memory[0xDC9B] == native_song
    check_music_tab()
    press(gb,'select')
    assert gb.memory[PINCH_ENABLED] == 1
    # Native and imported A/B/C, and OFF, using only menu buttons.
    for enabled in [0,1]:
        if gb.memory[ENABLED] != enabled: press(gb,'right')
        for selection in [1,2,3,4]:
            while gb.memory[0xDB0D] > selection: press(gb,'up')
            while gb.memory[0xDB0D] < selection: press(gb,'down')
            check_music_tab()
            if selection == 4:
                assert gb.memory[ACTIVE] == 0
                assert sound_energy(gb,120) == 0, 'OFF must silence the BGM'
                for _ in range(3):
                    press(gb,'right')
                    assert gb.memory[ACTIVE] == 0
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
        press(gb,'right')
        press(gb,'a',settle=120)  # Level selection
        assert gb.memory[ACTIVE] == 0, 'Level selection retains native menu music'
        press(gb,'a',settle=120)  # Gameplay
        assert gb.memory[0xC0A0] == 5
        assert gb.memory[CURRENT_MUSIC] == selection+4
        assert gb.memory[LAST_ID] == selection+0x43
        assert sound_energy(gb) > 0
        press(gb,'right')
        press(gb,'select')
        assert gb.memory[ENABLED] == 1 and gb.memory[PINCH_ENABLED] == 1, 'Gameplay controls must not change menu settings'
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
    return f'{mode}: music-focus Left/Right and Select PINCH controls, mode-row lockout, PINCH hidden on mode rows/black in music focus, default/palettes/highlights, preview/OFF, gameplay, pause/resume and SFX passed'


def menu_palette_checks(path):
    """Compare song-window backdrop pixels and palette data against native DX."""
    from PIL import ImageChops
    native, patched = emulator(ROOT/'original_roms'/DX_NAME), emulator(path)
    for gb in [native,patched]: boot_menu(gb)
    def check_palettes():
        assert bytes(native.memory[0xC200:0xC280]) == bytes(patched.memory[0xC200:0xC280]), 'Native BG/OBJ palette sources changed'
    for focus in [0,1,0,1,0]:
        for gb in [native,patched]:
            if bool(gb.memory[0xDB0E]) != bool(focus): press(gb,'a' if focus else 'b')
        for selection in [1,2,3,4]:
            for gb in [native,patched]:
                address,target = (0xDB0D,selection) if focus else (0xDB07,selection-1)
                while gb.memory[address] > target: press(gb,'up')
                while gb.memory[address] < target: press(gb,'down')
            check_palettes()
            # Sample the backdrop and borders around all four titles,
            # avoiding their independently animated foreground highlights.
            for box in [(144,100,152,140),(104,100,144,104),(104,136,144,140)]:
                a,b = (gb.screen.image.crop(box).convert('RGB') for gb in [native,patched])
                assert ImageChops.difference(a,b).getbbox() is None, ('Song-title backdrop differs from native DX',focus,selection,box)
            if focus:
                press(patched,'select')
                check_palettes()
        if not focus:
            for gb in [native,patched]:
                while gb.memory[0xDB07]: press(gb,'up')
    for stage in ['music','level selection','gameplay']:
        for gb in [native,patched]: press(gb,'a',settle=120)
        if stage != 'music':
            assert bytes(native.memory[0xC200:0xC280]) == bytes(patched.memory[0xC200:0xC280]), 'PINCH color leaked outside the music/mode menu'
    for gb in [native,patched]: gb.stop(save=False)
    return 'cgb: native BG/OBJ palettes and song-window backdrop retained across all modes/songs, focus changes and PINCH toggles'


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
    desired_id, effect_channels = [0x21], set()
    symbols = control_code().labels
    def native_done(_):
        for channel,base in enumerate([0xDEA0,0xDEB8,0xDED0,0xDEE8]):
            if not gb.memory[base]&0x80: continue
            voice = gb.memory[base+4] | gb.memory[base+5]<<8
            if (gb.memory[voice+5] | gb.memory[voice+6]<<8) == desired_id[0]:
                effect_channels.add(channel)
    gb.hook_register(0,symbols['frame_native_done'],native_done,None)
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
    for imported,selection in [(0,1),(1,1),(2,1),(2,2),(2,3)]:
        restore(gb,menu)
        press(gb,'a')
        for _ in range(imported): press(gb,'right')
        for _ in range(selection-1): press(gb,'down')
        press(gb,'a',settle=120)
        press(gb,'a',settle=120)
        gameplay = snapshot(gb)
        # Feed representative native effects through the normal SFX API. This
        # covers pulse, noise, and wave effects without arranging line clears.
        def request(_): gb.register_file.C = desired_id[0]
        gb.hook_register(0,0x3F0C,request,None)
        for sound_id in [0x21,0x22,0x23,0x24,0x25,0x27,0x28,0x2B,0x2C,0x2D,0x2F,0x34,0x3C]:
            restore(gb,gameplay)
            desired_id[0] = sound_id
            effect_channels.clear()
            press(gb,'a',settle=40)
            pulse = 0 if imported == 1 or (imported == 2 and selection > 1) else 1
            expected = {0x21: {pulse,2}, 0x22: {pulse}, 0x28: {1}}
            if sound_id in expected:
                assert effect_channels == expected[sound_id], (
                    f'Effect {sound_id:02x}, page={imported+1}, song={selection}: channels {effect_channels}')
        gb.hook_deregister(0,0x3F0C)
    assert not errors, errors[:3]
    assert all(counts.values()), f'Did not exercise all native volume paths: {counts}'
    gb.stop(save=False)
    return f'{"cgb" if cgb else "dmg"}: native SFX volumes preserved; movement/rotation use pulse 1 with GB/Bombliss B/C, pulse 2 with DX/Bombliss A'


def sharing_checks(path,cgb,variant=0):
    """Piece SFX use the requested channels while the score keeps its timing."""
    gb = emulator(path,cgb)
    boot_menu(gb)
    press(gb,'a')
    press(gb,'right')
    press(gb,'a',settle=120)
    press(gb,'a',settle=120)
    if variant:
        def force_height(_):
            gb.memory[0xAF98] = 11 if variant == 1 else 14
            gb.memory[0xAF99] = 255
        gb.hook_register(1,0x4C40,force_height,None)
        gb.tick(20,False,False)
        assert gb.memory[MUSIC_VARIANT] == variant
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
    for sound_id,channels in [(0x21,{0,2}),(0x22,{0}),(0x28,{1})]:
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
        assert observed == channels, f'Effect {sound_id:02x} used incorrect channels: {observed}'
    restore(gb,gameplay)
    current.clear()
    previous.clear()
    desired_id[0] = 0x21
    force_rest[0] = True
    press(gb,'a',settle=60)
    assert rest_checked, 'Did not exercise wave-channel release during a music rest'
    assert routed and released and not errors, errors[:3]
    gb.stop(save=False)
    return f'{"cgb" if cgb else "dmg"}: GB {("Normal","Danger","Peril")[variant]} movement/rotation pulse-1 routing, score timing and music/waveform restoration (including rests) verified'


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
        press(dx,'right')
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


def variant_checks(path, cgb, frames):
    """Exercise real height decisions and compare each variant's score/clock."""
    gb = emulator(path,cgb)
    boot_menu(gb)
    mode_menu = snapshot(gb)
    symbols = control_code().labels
    height, requests, starts = [0], [], []
    def force_height(_):
        # Keep gameplay's real threshold selection and BGM call intact. Supply
        # only its computed stack height, without building a dangerous board.
        gb.memory[0xAF98] = height[0]
        gb.memory[0xAF99] = 255
    def requested(_): requests.append(gb.register_file.C)
    def started(_): starts.append(gb.register_file.C)
    gb.hook_register(1,0x4C40,force_height,None)
    gb.hook_register(0,0x3F18,requested,None)
    gb.hook_register(0,symbols['original_bgm'],started,None)
    for imported in [False,True]:
        for selection in [1,2,3,4]:
            restore(gb,mode_menu)
            press(gb,'a')
            for _ in range(selection-1): press(gb,'down')
            if imported: press(gb,'right')
            height[0] = 0
            press(gb,'a',settle=120)
            press(gb,'a',settle=120)
            for h,variant in [(11,1),(14,2),(17,3),(14,2),(11,1),(0,0)]:
                stopped_native_id = gb.memory[0xDC9B]
                height[0] = h
                requests.clear()
                starts.clear()
                gb.tick(20,False,False)
                song = (selection+0x43 if variant == 0 else 0x4C if variant == 3 else
                        0x5A+2*(selection-1)+variant-1) if selection<4 else 0
                assert requests and set(requests) == {song}, (h,requests)
                if imported and selection<4 and variant<3:
                    assert gb.memory[ACTIVE] and gb.memory[LAST_ID] == song
                    assert gb.memory[CURRENT_MUSIC] == selection+4
                    assert gb.memory[MUSIC_VARIANT] == variant
                    assert starts == [song], 'Repeated conditional requests restarted the score'
                    assert gb.memory[0xDC9B] == stopped_native_id, 'A native DX song took over the GB variant'
                else:
                    assert gb.memory[ACTIVE] == 0
                    if song: assert gb.memory[0xDC9B] == song
                    else: assert sound_energy(gb,30) == 0, 'OFF started music at a height threshold'
            if imported and selection<4:
                height[0] = 14
                gb.tick(10,False,False)
                press(gb,'start',settle=30)
                assert gb.memory[PAUSED] == 1 and gb.memory[MUSIC_VARIANT] == 2
                paused = bytes(gb.memory[0xDF20:0xDF60]),gb.memory[MUSIC_PHASE]
                assert sound_energy(gb,30) == 0
                assert paused == (bytes(gb.memory[0xDF20:0xDF60]),gb.memory[MUSIC_PHASE])
                press(gb,'start',settle=15)
                assert gb.memory[PAUSED] == 0 and gb.memory[MUSIC_VARIANT] == 2
                assert sound_energy(gb,15)>0
    for bank,address in [(1,0x4C40),(0,0x3F18),(0,symbols['original_bgm'])]:
        gb.hook_deregister(bank,address)

    restore(gb,mode_menu)
    press(gb,'a')
    press(gb,'right')
    music_menu = snapshot(gb)
    def record(song,variant,length):
        restore(gb,music_menu)
        events,ticks = note_trace(gb,ENGINE_BANK+variant,-0x70)
        def clear_trace(_):
            for channel in events: channel.clear()
            ticks[0] = 0
        def request(_):
            gb.register_file.B,gb.register_file.C = 0,song
        gb.hook_register(ENGINE_BANK+variant,START_MUSIC,clear_trace,None)
        gb.hook_register(0,0x3F18,request,None)
        press(gb,'down')
        gb.hook_deregister(0,0x3F18)
        assert gb.memory[LAST_ID] == song and gb.memory[MUSIC_VARIANT] == variant
        ticks_before = ticks[0]
        gb.tick(length,False,False)
        # Music tick count measures score speed, independently of note spacing.
        assert abs(ticks[0]-ticks_before-length*(4+variant)/4)<=2
        for address in [START_MUSIC,0x6C44,0x6D34]:
            gb.hook_deregister(ENGINE_BANK+variant,address)
        return events
    for selection in [1,2,3]:
        normal = record(selection+0x43,0,frames*2)
        reference = normal
        if selection == 2:
            # Derive the expected shortened score from normal playback, rather
            # than from the patched variant's header/loop pointers. Remove the
            # two opening sections whenever they recur, including their time.
            reference = [[],[],[],normal[3]]
            intro_ranges = [((0x7308,0x730B),(0x733C,0x733F)),
                            ((0x730B,0x731F),(0x733F,0x7353)),
                            ((0x731F,0x7333),(0x7353,0x7367))]
            for channel,ranges in enumerate(intro_ranges):
                skipped = 0
                for tick,data in normal[channel]:
                    pointer = data[1] | data[2]<<8
                    if any(lo<=pointer<hi for lo,hi in ranges):
                        skipped += data[0]
                    else:
                        reference[channel].append((tick-skipped,data))
                assert reference[channel][0][0] == 1
                assert next(tick for tick,data in normal[channel]
                            if data == reference[channel][0][1]) == 193, 'B intro is not 192 ticks'
        for variant,semitones in [(1,2),(2,4)]:
            raised = record(0x5A+2*(selection-1)+variant-1,variant,frames)
            for channel in range(4):
                assert len(reference[channel])>=len(raised[channel])
                for (tick,a),(normal_tick,b) in zip(raised[channel],reference[channel]):
                    assert tick == normal_tick, 'Variant changed the score rhythm'
                    assert a[:6] == b[:6] and a[8] == b[8], 'Variant changed notes, instruments or rests'
                    if channel<3 and not a[8]:
                        f = b[6] | b[7]<<8
                        expected = round(2048-(2048-(f&0x7FF))/2**(semitones/12)) | (f&0xF800)
                        assert a[6] | a[7]<<8 == expected, (
                            'Incorrect transposition',selection,variant,channel,tick,a,b,expected)
                    elif channel == 3:
                        assert a[6:8] == b[6:8], 'Noise percussion changed timbre'
            assert sum(map(len,raised))>frames//30, 'Variant was not audible/advancing'
    gb.stop(save=False)
    return f'{"cgb" if cgb else "dmg"}: all GB Danger/Peril tracks, pitch, synchronized 1.25x/1.5x tempo, B intro skipped at start/loop with percussion aligned, native height transitions, return to normal, pause/resume, DX set and OFF verified'


def pinch_checks(path,cgb):
    """PINCH OFF keeps every song normal at every high-stack threshold."""
    gb = emulator(path,cgb)
    boot_menu(gb)
    menu = snapshot(gb)
    symbols = control_code().labels
    height,requests,starts = [0],[],[]
    def force_height(_):
        gb.memory[0xAF98] = height[0]
        gb.memory[0xAF99] = 255
    def requested(_): requests.append(gb.register_file.C)
    def started(kind): starts.append(kind)
    gb.hook_register(1,0x4C40,force_height,None)
    gb.hook_register(0,0x3F18,requested,None)
    gb.hook_register(0,symbols['original_bgm'],started,'GB')
    gb.hook_register(12,0x4284,started,'DX')
    for imported in [False,True]:
        for selection in [1,2,3,4]:
            restore(gb,menu)
            press(gb,'a')
            for _ in range(selection-1): press(gb,'down')
            if imported: press(gb,'right')
            press(gb,'select')
            assert gb.memory[PINCH_ENABLED] == 0
            height[0] = 0
            press(gb,'a',settle=120)
            press(gb,'a',settle=120)
            assert gb.memory[PINCH_ENABLED] == 0, 'PINCH must persist into gameplay'
            starts.clear()
            for h in [0,11,14,17,18,14,11,0]:
                height[0] = h
                requests.clear()
                gb.tick(20,False,False)
                song = selection+0x43 if selection<4 else 0
                assert requests and set(requests) == {song}, ('PINCH OFF requested a high-stack song',imported,selection,h,requests)
                if imported and selection<4:
                    assert gb.memory[ACTIVE] and gb.memory[LAST_ID] == song
                    assert gb.memory[MUSIC_VARIANT] == 0 and gb.memory[CURRENT_MUSIC] == selection+4
                else:
                    assert gb.memory[ACTIVE] == 0
                    if song: assert gb.memory[0xDC9B] == song
                assert not starts, 'PINCH OFF restarted the normal song at a height change'
            if selection == 4:
                assert sound_energy(gb,30) == 0, 'Music OFF must remain silent with PINCH OFF'
            else:
                assert sound_energy(gb,30)>0
                press(gb,'start',settle=30)
                assert gb.memory[PINCH_ENABLED] == 0 and sound_energy(gb,30) == 0
                press(gb,'start',settle=30)
                assert gb.memory[PINCH_ENABLED] == 0 and sound_energy(gb,30)>0
    gb.stop(save=False)
    return f'{"cgb" if cgb else "dmg"}: PINCH OFF suppresses Danger/Peril/final alarm for DX and GB A/B/C; normal songs do not restart, music OFF and pause/resume preserved'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rom',type=Path,default=ROOT/'output'/OUTPUT_NAME)
    parser.add_argument('--frames',type=int,default=7200,help='Frames per melody comparison (default: two minutes)')
    args = parser.parse_args()
    OUT.mkdir(parents=True,exist_ok=True)
    rom = args.rom.read_bytes()
    original_dx = (ROOT/'original_roms'/DX_NAME).read_bytes()
    assert (rom[0x30000:0x30B0C] == original_dx[0x30000:0x30B0C]
            and rom[0x30B0F:0x34000] == original_dx[0x30B0F:0x34000]), 'Only the pulse-selection call may change in DX audio bank; effect data and mixer must remain original'
    assert len(rom) == 1024*1024 and rom[0x148] == 5
    assert rom[0x14D] == (-sum(rom[0x134:0x14D])-25)&255
    assert int.from_bytes(rom[0x14E:0x150],'big') == (sum(rom)-sum(rom[0x14E:0x150]))&65535
    report = [ui_checks(args.rom,True),ui_checks(args.rom,False)]
    report.append(menu_palette_checks(args.rom))
    report.extend([mix_checks(args.rom,True),mix_checks(args.rom,False)])
    report.extend([sharing_checks(args.rom,True),sharing_checks(args.rom,False)])
    for variant in [1,2]:
        report.extend([sharing_checks(args.rom,True,variant),
                       sharing_checks(args.rom,False,variant)])
    report.extend(melody_checks(args.rom,args.frames))
    report.extend([variant_checks(args.rom,True,args.frames),
                   variant_checks(args.rom,False,args.frames)])
    report.extend([pinch_checks(args.rom,True),pinch_checks(args.rom,False)])
    import hashlib
    for name,digest in HASHES.items():
        assert hashlib.sha256((ROOT/'original_roms'/name).read_bytes()).hexdigest() == digest
    (OUT/'results.json').write_text(json.dumps({'rom_sha256':hashlib.sha256(rom).hexdigest(),'checks':report},indent=2)+'\n')
    print('\n'.join(report))
    print('ROM checksums and unchanged originals verified.')


if __name__ == '__main__':
    main()
