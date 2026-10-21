"""Integration checks for MUSIC-3, source-driver equivalence, and DX SFX sharing."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from build_rom import (Code, ROOT, OUTPUT_NAME, BOM_NAME, ENABLED, ACTIVE, PAUSED,
                       MUSIC_VARIANT, MUSIC_PHASE, RESTORE_ALL, PINCH_ENABLED,
                       PINCH_BACKDROP, DX_NAME, LABEL_SHADOW_TILES, IMPORTED_SONG,
                       control_code)
from bombliss_player import (BANK, START, FRAME, NATIVE, SLOTS, GLOBALS, SCRATCH,
                             APU, WAVE, LOCKS, compile_player, shadow)
from test_rom import emulator, boot_menu, press, snapshot, restore, sound_energy

OUT = ROOT/'output'/'validation'


def harness(path, bank, cgb):
    """Run one complete native driver call per frame, with no game/SFX calls.

    Only an unused ROM0 area in this emulator's memory is overridden with a
    CALL/idle trampoline. The original driver and source song data are intact.
    """
    gb = emulator(path,cgb)
    gb.tick(100,False,False)
    gb.memory[0xFFFF] = 0
    gb.memory[0xFF40] = 0
    gb.memory[0xFF26] = 0
    gb.memory[0xFF26] = 0x80
    gb.memory[0xFF24] = 0x77
    gb.memory[0xFF25] = 0xFF
    gb.memory[0x4000] = bank >> 5
    gb.memory[0x2000] = bank & 31
    for native in NATIVE:
        gb.memory[native] = 0
    for address in range(0xC300,0xC39F):
        gb.memory[address] = 0

    def run(entry, a=0):
        blob = bytes([0xF3,0xCD,entry&255,entry>>8,0xC3,0x84,0x3E])
        for index,value in enumerate(blob):
            gb.memory[0,0x3E80+index] = value
        gb.register_file.SP = 0xDFFF
        gb.register_file.A = a
        gb.register_file.PC = 0x3E80
        gb.tick(1,False,False)
        if gb.register_file.PC != 0x3E84:
            gb.tick(1,False,False)
        assert gb.register_file.PC == 0x3E84, hex(gb.register_file.PC)
    return gb,run


def melody_checks(path,cgb,frames):
    source = ROOT/'original_roms'/BOM_NAME
    source_bytes = source.read_bytes()
    for selection,native_id in enumerate([0x64,0x76,0x47],5):
        original,run_original = harness(source,5,cgb)
        patched,run_patched = harness(path,BANK,cgb)
        run_original(0x1D18)
        run_original(0x1D48,native_id)
        run_patched(START,selection)
        _,symbols = compile_player(source_bytes,Code)
        wave_errors,uploads = [],[]
        def uploaded(_):
            raw = bytes(patched.memory[WAVE:WAVE+16])
            scaled = bytes(((value>>1)&0x77)+0x44 for value in raw)
            if bytes(patched.memory[0xFF30:0xFF40]) != scaled:
                wave_errors.append('Incorrect attenuated waveform')
            uploads.append(True)
        patched.hook_register(BANK,symbols['wave_uploaded'],uploaded,None)
        for frame in range(frames):
            run_original(0x1DB5)
            run_patched(FRAME)
            expected = bytes(original.memory[0xC332:0xC396])
            actual = bytes(patched.memory[SLOTS:SLOTS+100])
            assert actual == expected, (selection,frame,[(i,a,b) for i,(a,b) in
                enumerate(zip(actual,expected)) if a != b][:8])
            pointer = expected[52] | expected[53]<<8  # wave stream header
            waveform = source_bytes[0x10000+pointer+3]
            wave_start = 0x1EC4+waveform*16
            assert bytes(patched.memory[WAVE:WAVE+16]) == source_bytes[wave_start:wave_start+16]
            assert patched.memory[shadow(0x25)] == original.memory[0xFF25]
            # Readable APU bits (write-only register bits differ on readback).
            for register,mask in [(0x10,0x7F),(0x11,0xC0),(0x12,0xFF),
                    (0x16,0xC0),(0x17,0xFF),(0x1A,0x80),(0x1C,0x60),
                    (0x21,0xFF),(0x22,0xFF)]:
                value = original.memory[0xFF00+register]
                if register in (0x12,0x17,0x21):
                    value = ((value>>5)<<4)|(value&15)
                assert patched.memory[0xFF00+register]&mask == value&mask, (
                    selection,frame,hex(register))
        assert uploads and not wave_errors,wave_errors
        original.stop(save=False)
        patched.stop(save=False)
    return f'{"CGB" if cgb else "DMG"}: MUSIC-3 A/B/C retain original channel state/private waveforms; pulse/noise volume and uploaded wave amplitude halved over {frames} ticks each'


def ui_checks(path,cgb):
    native = emulator(ROOT/'original_roms'/DX_NAME,cgb)
    boot_menu(native)
    title_graphics = bytes(native.memory[0,0x8CA0:0x8D00])
    native.stop(save=False)
    gb = emulator(path,cgb)
    boot_menu(gb)
    press(gb,'a')
    # Both directions wrap, holding a key changes pages only once.
    for button,page in [('left',2),('left',1),('right',2),('right',0),('left',2)]:
        press(gb,button,hold=120)
        assert gb.memory[ENABLED] == page
        assert gb.memory[0,0x99A9] == 0xA1+page
    assert gb.memory[ACTIVE] == 2
    for row in range(8):
        bits = gb.memory[0,0x8A31+row*2]
        assert bytes(gb.memory[0,0x8D00+row*2:0x8D02+row*2]) == bytes([bits^255,bits])
    assert bytes(gb.memory[0,0x8CA0:0x8D00]) == title_graphics, 'Song-title graphics must remain native'
    gb.screen.image.save(OUT/f'bombliss-{cgb}-music-menu.png')
    press(gb,'b')
    assert gb.memory[ACTIVE] == 0
    assert bytes(gb.memory[0,0x9A20:0x9A2A]) == bytes(gb.memory[PINCH_BACKDROP:PINCH_BACKDROP+10])
    sprites = [tuple(gb.memory[a:a+4]) for a in range(0xFE00,0xFEA0,4)]
    assert (0x78,0x50,LABEL_SHADOW_TILES[2],0x14 if cgb else 0) in sprites
    for button in ['select','left','right']:
        press(gb,button)
        assert gb.memory[ENABLED] == 2 and gb.memory[PINCH_ENABLED] == 1
    gb.screen.image.save(OUT/f'bombliss-{cgb}-mode-menu.png')
    press(gb,'a')
    menu = snapshot(gb)
    for song in range(1,5):
        restore(gb,menu)
        for _ in range(song-1):
            press(gb,'down')
        if song == 4:
            assert sound_energy(gb,60) == 0 and gb.memory[ACTIVE] == 0
            for _ in range(3):
                press(gb,'right')
                assert sound_energy(gb,30) == 0 and gb.memory[ACTIVE] == 0
            continue
        assert gb.memory[ACTIVE] == 2 and sound_energy(gb,30)>0
        press(gb,'a',settle=120)
        assert gb.memory[ACTIVE] == 0  # native level-selection music
        press(gb,'a',settle=120)
        assert gb.memory[ACTIVE] == 2 and sound_energy(gb,30)>0
        gb.screen.image.save(OUT/f'bombliss-{cgb}-song-{song}-gameplay.png')
        press(gb,'start',settle=60)
        assert gb.memory[PAUSED] == 1
        paused = bytes(gb.memory[SLOTS:SLOTS+0x90])
        assert sound_energy(gb,60) == 0
        assert bytes(gb.memory[SLOTS:SLOTS+0x90]) == paused
        native_bgm = bytes(gb.memory[0xDDB0:0xDEA0])
        press(gb,'start',settle=30)
        assert gb.memory[PAUSED] == 0 and sound_energy(gb,30)>0
        assert bytes(gb.memory[0xDDB0:0xDEA0]) == native_bgm, 'Unpausing Bombliss music must not wake the suspended DX theme'
    gb.stop(save=False)
    return f'{"CGB" if cgb else "DMG"}: three-page wrapping/debounce, MUSIC-3 glyphs/focus, mode-row lockout, A/B/C previews/gameplay, OFF and pause/resume verified'


def pitch_checks(path,cgb,frames):
    source = ROOT/'original_roms'/BOM_NAME
    for selection,native_id in enumerate([0x64,0x76,0x47],5):
        for variant,semitones in [(1,2),(2,4)]:
            original,run_original = harness(source,5,cgb)
            patched,run_patched = harness(path,BANK+variant,cgb)
            _,symbols = compile_player(source.read_bytes(),Code,semitones)
            normal_notes,raised_notes = [],[]
            def normal(_):
                normal_notes.append((original.memory[0xC398],original.register_file.HL))
            def raised(_):
                raised_notes.append((patched.memory[GLOBALS+2],patched.register_file.HL))
            original.hook_register(0,0x20F6,normal,None)
            patched.hook_register(BANK+variant,symbols['src_20f6'],raised,None)
            if selection == 6:
                # Independent reference: jump past each timed pickup command
                # in the original interpreter, without editing its score data.
                # These are the four original first saved-loop boundaries.
                bounds = [(0x517A,0x5180),(0x5296,0x52A6),
                          (0x5486,0x548A),(0x556C,0x55A4)]
                def skip_pickup(_):
                    pointer = original.register_file.HL
                    if (any(first <= pointer < end for first,end in bounds)
                            and original.memory[pointer] < 0xA0):
                        original.register_file.HL = pointer+2
                        original.register_file.PC = 0x1F12
                original.hook_register(0,0x1F1E,skip_pickup,None)
                loop_starts = {}
                def main_loop(_):
                    pointer = patched.register_file.HL
                    if pointer in (0x5180,0x52A6,0x548A,0x55A4):
                        loop_starts.setdefault(patched.memory[GLOBALS+2],frame)
                patched.hook_register(BANK+variant,symbols['src_1f1e'],main_loop,None)
            run_original(0x1D18)
            run_original(0x1D48,native_id)
            run_patched(START,selection)
            events = 0
            for frame in range(frames):
                normal_notes.clear()
                raised_notes.clear()
                run_original(0x1DB5)
                run_patched(FRAME)
                assert len(normal_notes) == len(raised_notes), (selection,variant,frame)
                for (channel,frequency),(raised_channel,raised_frequency) in zip(normal_notes,raised_notes):
                    assert channel == raised_channel
                    expected = round(2048-(2048-frequency)/2**(semitones/12)) if channel<3 else frequency
                    # The driver shifts octave periods after its table lookup;
                    # integer rounding can differ by one hardware frequency unit.
                    assert abs(raised_frequency-expected) <= (1 if channel<3 else 0), (
                        selection,variant,frame,channel,frequency,raised_frequency,expected)
                    events += 1
                for channel in range(4):
                    a = bytes(original.memory[0xC332+channel*25:0xC34B+channel*25])
                    b = bytes(patched.memory[SLOTS+channel*25:SLOTS+(channel+1)*25])
                    assert a[:17]+a[19:] == b[:17]+b[19:], (selection,variant,frame,channel)
            assert events > frames//30
            if selection == 6:
                assert loop_starts == dict.fromkeys(range(4),0), loop_starts
            original.stop(save=False)
            patched.stop(save=False)
    return f'{"CGB" if cgb else "DMG"}: MUSIC-3 Danger/Peril match source rhythm/instruments with B pickup skipped on all four channels; notes raised 2/4 semitones over {frames} ticks per song/variant'


def sharing_checks(path,cgb,variant,song=1):
    gb = emulator(path,cgb)
    boot_menu(gb)
    press(gb,'a')
    press(gb,'left')
    for _ in range(song-1): press(gb,'down')
    press(gb,'a',settle=120)
    press(gb,'a',settle=120)
    height = [0 if not variant else 11 if variant == 1 else 14]
    def force_height(_):
        gb.memory[0xAF98],gb.memory[0xAF99] = height[0],255
    gb.hook_register(1,0x4C40,force_height,None)
    gb.tick(20,False,False)
    assert gb.memory[MUSIC_VARIANT] == variant and gb.memory[ACTIVE] == 2
    assert gb.memory[IMPORTED_SONG] == song+4
    gameplay = snapshot(gb)
    def music_state():
        return bytes(gb.memory[SLOTS:SLOTS+0x6E]),bytes(gb.memory[SCRATCH:SCRATCH+25]),bytes(gb.memory[APU:APU+39])
    main_symbols = control_code().labels
    expected = []
    def reference_frame(_): expected.append(music_state())
    gb.hook_register(0,main_symbols['frame_mix_done'],reference_frame,None)
    gb.tick(140,False,False)
    gb.hook_deregister(0,main_symbols['frame_mix_done'])
    _,symbols = compile_player((ROOT/'original_roms'/BOM_NAME).read_bytes(),Code)
    errors,observed,releases,effect_channels = [],set(),set(),set()
    minimum_sp = [0xFFFF]
    before,current,previous = [],set(),set()
    native_pan = [0]
    completed = [0]
    ranges = [(0xFF10,0xFF15),(0xFF16,0xFF1A),(0xFF1A,0xFF1F),(0xFF20,0xFF24)]
    def native_done(_):
        previous.clear()
        previous.update(current)
        current.clear()
        current.update(i for i,a in enumerate(NATIVE) if gb.memory[a]&128)
        for channel in current:
            base = NATIVE[channel]
            voice = gb.memory[base+4] | gb.memory[base+5]<<8
            voice_id = gb.memory[voice+5] | gb.memory[voice+6]<<8
            if voice_id == desired[0]:
                effect_channels.add(channel)
        observed.update(current)
        releases.update(previous-current)
        before[:] = [bytes(gb.memory[lo:hi]) for lo,hi in ranges]
        native_pan[0] = gb.memory[0xFF25]
    def mixed(_):
        if music_state() != expected[completed[0]]:
            errors.append(('SFX changed score or virtual instruments',completed[0]))
        completed[0] += 1
        for channel,(lo,hi) in enumerate(ranges):
            mask = [0x11,0x22,0x44,0x88][channel]
            if channel in current:
                if bytes(gb.memory[lo:hi]) != before[channel]:
                    errors.append(('SFX registers overwritten',channel))
                expected_pan = native_pan[0]&mask
            else:
                expected_pan = gb.memory[shadow(0x25)]&mask
                register = [0x12,0x17,0x1C,0x21][channel]
                value = gb.memory[shadow(register)]
                if channel != 2: value = ((value>>5)<<4)|(value&15)
                volume_mask = 0x60 if channel == 2 else 0xFF
                if gb.memory[0xFF00+register]&volume_mask != value&volume_mask:
                    errors.append(('Incorrect music volume after SFX/restoration',channel))
            if gb.memory[0xFF25]&mask != expected_pan:
                errors.append(('Incorrect panning',channel))
    def written(_):
        channel = gb.memory[GLOBALS+2]
        if gb.memory[NATIVE[channel]]&128:
            errors.append(('Music wrote an owned channel',channel))
    def waveform(_):
        if gb.memory[NATIVE[2]]&128:
            errors.append('Music replaced an owned waveform')
    def wave_uploaded(_):
        raw = bytes(gb.memory[WAVE:WAVE+16])
        scaled = bytes(((value>>1)&0x77)+0x44 for value in raw)
        if bytes(gb.memory[0xFF30:0xFF40]) != scaled:
            errors.append('Incorrect music waveform gain after SFX/restoration')
    def stack(_):
        minimum_sp[0] = min(minimum_sp[0],gb.register_file.SP)
    desired = [0x21]
    def request(_): gb.register_file.C = desired[0]
    gb.hook_register(0,0x3F0C,request,None)
    gb.hook_register(0,main_symbols['frame_native_done'],native_done,None)
    gb.hook_register(0,main_symbols['frame_mix_done'],mixed,None)
    gb.hook_register(BANK+variant,symbols['write_hardware'],written,None)
    gb.hook_register(BANK+variant,symbols['wave_byte'],waveform,None)
    gb.hook_register(BANK+variant,symbols['wave_uploaded'],wave_uploaded,None)
    gb.hook_register(BANK+variant,symbols['write_stack'],stack,None)
    gb.hook_register(BANK+variant,symbols['volume_stack'],stack,None)
    gb.hook_register(BANK+variant,symbols['read_stack'],stack,None)
    for effect in [0x21,0x22,0x23,0x24,0x25,0x27,0x28,0x2B,0x2C,0x2D,0x2F,0x34,0x3C]:
        restore(gb,gameplay)
        desired[0] = effect
        effect_channels.clear()
        current.clear()
        previous.clear()
        completed[0] = 0
        gb.button_press('a')
        for frame in range(150):
            if frame == 5: gb.button_release('a')
            gb.tick(1,False,False)
            if completed[0] >= 120: break
        # Observe 120 completed audio updates, rather than sampling halfway
        # through a native SFX call at a video-frame boundary. Keep a timeout
        # so a stalled player cannot pass this comparison.
        assert completed[0] >= 120, (song,variant,effect,completed[0])
        pulse = 1 if song == 1 else 0
        expected_channels = {0x21:{pulse,2}, 0x22:{pulse}, 0x28:{1}}
        if effect in expected_channels:
            assert effect_channels == expected_channels[effect], (
                'Incorrect MUSIC-3 SFX routing',variant,effect,effect_channels)
    assert observed == {0,1,2,3} and releases == observed, (observed,releases)
    assert not errors,errors[:5]
    assert minimum_sp[0] > APU+38, hex(minimum_sp[0])
    gb.stop(save=False)
    return f'{"CGB" if cgb else "DMG"}: MUSIC-3 {"ABC"[song-1]} variant {variant}, movement/rotation use pulse {2 if song == 1 else 1}, rotation retains wave, blocked actions use pulse 2; 13 SFX preserve score/instruments, channel ownership, panning and release handling; minimum bridge SP ${minimum_sp[0]:04X}'


def height_checks(path,cgb):
    gb = emulator(path,cgb)
    boot_menu(gb)
    press(gb,'a')
    press(gb,'left')
    menu = snapshot(gb)
    height,requests,starts,ticks = [0],[],[],[0]
    def force_height(_):
        gb.memory[0xAF98],gb.memory[0xAF99] = height[0],255
    def request(_): requests.append(gb.register_file.C)
    def start(_): starts.append(gb.register_file.C)
    def tick(_): ticks[0] += 1
    gb.hook_register(1,0x4C40,force_height,None)
    gb.hook_register(0,0x3F18,request,None)
    gb.hook_register(0,control_code().labels['original_bgm'],start,None)
    _,bom_symbols = compile_player((ROOT/'original_roms'/BOM_NAME).read_bytes(),Code)
    for variant in range(3):
        gb.hook_register(BANK+variant,bom_symbols['src_1db5'],tick,None)
    for pinch in [1,0]:
        for song in range(1,5):
            restore(gb,menu)
            for _ in range(song-1): press(gb,'down')
            if not pinch: press(gb,'select')
            height[0] = 0
            press(gb,'a',settle=120)
            press(gb,'a',settle=120)
            for h,variant in [(0,0),(11,1),(14,2),(17,3),(14,2),(11,1),(0,0)]:
                old_variant = gb.memory[MUSIC_VARIANT]
                height[0] = h
                requests.clear()
                starts.clear()
                gb.tick(20,False,False)
                variant = variant if pinch else 0
                wanted = (song+0x43 if variant == 0 else 0x4C if variant == 3 else
                          0x5A+(song-1)*2+variant-1) if song<4 else 0
                assert requests and set(requests) == {wanted}, (pinch,song,h,requests)
                if song<4 and variant<3:
                    assert gb.memory[ACTIVE] == 2 and gb.memory[MUSIC_VARIANT] == variant
                    assert gb.memory[IMPORTED_SONG] == song+4
                    assert len(starts) <= 1, 'Conditional request restarted the music'
                    if not pinch: assert not starts
                    # Count music steps over a separate steady interval.
                    ticks[0] = 0
                    gb.tick(40,False,False)
                    assert abs(ticks[0]-40*(4+variant)/4) <= 3, (song,variant,ticks[0])
                else:
                    assert gb.memory[ACTIVE] == 0
                if song == 4: assert sound_energy(gb,10) == 0
            if song<4 and pinch:
                height[0] = 14
                gb.tick(10,False,False)
                press(gb,'start',settle=30)
                frozen = bytes(gb.memory[SLOTS:SLOTS+0x90]),gb.memory[MUSIC_PHASE]
                assert gb.memory[PAUSED] and sound_energy(gb,30) == 0
                assert frozen == (bytes(gb.memory[SLOTS:SLOTS+0x90]),gb.memory[MUSIC_PHASE])
                press(gb,'start',settle=30)
                assert gb.memory[ACTIVE] == 2 and not gb.memory[PAUSED]
    gb.stop(save=False)
    return f'{"CGB" if cgb else "DMG"}: MUSIC-3 A/B/C/OFF height transitions, Danger/Peril pause/resume, final native alarm and PINCH OFF suppression verified'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rom',type=Path,default=ROOT/'output'/OUTPUT_NAME)
    parser.add_argument('--frames',type=int,default=7200)
    args = parser.parse_args()
    OUT.mkdir(parents=True,exist_ok=True)
    results = []
    for cgb in [True,False]:
        results.append(ui_checks(args.rom,cgb))
        results.append(melody_checks(args.rom,cgb,args.frames))
        results.append(pitch_checks(args.rom,cgb,args.frames))
        results.append(height_checks(args.rom,cgb))
        for variant in range(3):
            for song in [1,2,3]:
                results.append(sharing_checks(args.rom,cgb,variant,song))
    (OUT/'bombliss-results.json').write_text(json.dumps({
        'rom_sha256':hashlib.sha256(args.rom.read_bytes()).hexdigest(),
        'checks':results},indent=2)+'\n',encoding='utf-8')
    print('\n'.join(results))


if __name__ == '__main__':
    main()
