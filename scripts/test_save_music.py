"""Cold-boot checks for music settings in DX's battery-backed suspend save."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
from pathlib import Path

from pyboy import PyBoy

from build_rom import (ROOT, OUTPUT_NAME, ENABLED, ACTIVE, PAUSED, PINCH_ENABLED,
                       SAVED_MUSIC, IMPORTED_SONG)
from test_rom import (BOOT_STEPS, emulator, press, snapshot, restore, sound_energy)

OUT = ROOT/'output'/'validation'


def registered_menu(gb):
    # Same UI-created A profile as the other checks, selected instead of Guest.
    for step in BOOT_STEPS.replace('right,8,a,120','a,120').split(','):
        if step.isdigit():
            gb.tick(int(step),True,False)
        else:
            press(gb,step)
    assert gb.memory[0xC0A0] == 4 and gb.memory[0xAC09] == 0x80


def battery(gb):
    data = bytes(gb.memory[0xA000:0xC000])
    assert data[0xC05] == 1
    checksum = sum(data[0xB00:0x1000])&255
    assert all(data[p] == checksum for p in (0x8FF,0x9FF,0xAFF))
    return data


def cold_boot(path,cgb,data):
    # A new emulator instance has fresh WRAM. Only the cartridge battery RAM
    # survives; no emulator save states or injected continuation calls are used.
    gb = PyBoy(io.BytesIO(path.read_bytes()),ram_file=io.BytesIO(data),
               window='null',cgb=cgb,sound_sample_rate=48000)
    gb.set_emulation_speed(0)
    gb.tick(600,True,False)
    press(gb,'start',settle=120)
    assert gb.memory[ENABLED] == 0 and gb.memory[PINCH_ENABLED] == 1
    return gb


def resume(path,cgb,data,page,song,pinch,mode=0):
    gb = cold_boot(path,cgb,data)
    assert gb.memory[0xAC05] == 1, 'Native save validation rejected suspend data'
    press(gb,'a',settle=120)  # CONTINUE? YES
    assert gb.memory[0xC0A0] == 5 and gb.memory[0xAC05] == 1
    assert gb.memory[ENABLED] == page and gb.memory[PINCH_ENABLED] == pinch
    assert gb.memory[0xAC0A] == mode
    assert gb.memory[0xAC0E] == (0x43+song if song<4 else 0)
    assert gb.memory[ACTIVE] == (page if song<4 else 0)
    if page and song<4:
        assert gb.memory[IMPORTED_SONG] == song+4 and gb.memory[PAUSED] == 1
    assert sound_energy(gb,30) == 0, 'Continued game should remain paused'
    native_bgm = bytes(gb.memory[0xDDB0:0xDEA0])
    press(gb,'start',settle=30)
    assert gb.memory[0xAC05] == 0
    if page and song<4:
        assert not gb.memory[PAUSED] and gb.memory[ACTIVE] == page
        assert all(not gb.memory[0xDDB0+i*0x3C]&0x80 for i in range(4)), (
            'Unpausing imported music reactivated native background voices',page,song)
    assert (sound_energy(gb,180)>0) == (song<4)
    if page and song<4:
        assert bytes(gb.memory[0xDDB0:0xDEA0]) == native_bgm, (
            'Native background music advanced alongside imported music',page,song)
    press(gb,'start',settle=30)
    saved = battery(gb)
    assert saved[SAVED_MUSIC-0xA000] == (0x80|pinch<<5|page<<3|song)
    gb.stop(save=False)
    return saved


def checks(path,cgb):
    gb = emulator(path,cgb)
    registered_menu(gb)
    menu = snapshot(gb)
    saved_b = None
    count = 0
    # Every page, song (including OFF), and PINCH value in Marathon; also
    # exercise the other modes with each imported page and PINCH disabled.
    cases = [(page,song,pinch,0) for page in range(3)
             for song in range(1,5) for pinch in range(2)]
    cases += [(page,2,0,mode) for mode in range(1,4) for page in (1,2)]
    for page,song,pinch,mode in cases:
        restore(gb,menu)
        for _ in range(mode): press(gb,'down')
        press(gb,'a')
        for _ in range(page): press(gb,'right')
        for _ in range(song-1): press(gb,'down')
        if not pinch: press(gb,'select')
        press(gb,'a',settle=120)
        press(gb,'a',settle=120)
        if mode == 3: press(gb,'start',settle=120)  # VS COM matchup screen
        assert gb.memory[0xC0A0] == 5 and gb.memory[0xAC0A] == mode, (
            page,song,pinch,mode,gb.memory[0xC0A0],gb.memory[0xAC0A])
        press(gb,'start',settle=30)
        data = battery(gb)
        assert data[SAVED_MUSIC-0xA000] == (0x80|pinch<<5|page<<3|song)
        repeated = resume(path,cgb,data,page,song,pinch,mode)
        if (page,song,pinch,mode) == (2,2,0,0):
            # Repeat a complete save/power cycle; page 3 B with PINCH OFF is
            # also the fixture for legacy, corrupted and declined continues.
            resume(path,cgb,repeated,page,song,pinch,mode)
            saved_b = data
        count += 1
    gb.stop(save=False)
    assert saved_b is not None

    # An untagged native-format save has no way to identify an imported page.
    legacy = bytearray(saved_b)
    legacy[SAVED_MUSIC-0xA000] = 2
    for p in (0x8FF,0x9FF,0xAFF): legacy[p] = sum(legacy[0xB00:0x1000])&255
    resume(path,cgb,bytes(legacy),0,2,1)

    # A changed settings byte must invalidate the same native checksum as a
    # change to the board, rather than silently selecting the wrong page.
    damaged = bytearray(saved_b)
    damaged[SAVED_MUSIC-0xA000] ^= 8
    gb = cold_boot(path,cgb,bytes(damaged))
    assert gb.memory[0xAC05] == 0
    gb.stop(save=False)

    # Declining CONTINUE retains the ordinary new-game flow (the native
    # profile-selection code resets its song index to the default A-TYPE).
    gb = cold_boot(path,cgb,saved_b)
    press(gb,'right')
    press(gb,'a',settle=120)  # NO -> ordinary title/mode flow
    press(gb,'a',settle=120)  # 1PLAYER -> registered profile selection
    press(gb,'a',settle=120)  # A profile -> mode/music menu
    assert gb.memory[0xC0A0] == 4 and gb.memory[0xDB0D] == 1
    # Exercise a native menu reload with tagged data: its table/index logic
    # must still receive the original song number, without the settings bits.
    gb.memory[SAVED_MUSIC] = saved_b[SAVED_MUSIC-0xA000]
    gb.memory[0xC0A8] = 0
    gb.tick(120,True,False)
    assert gb.memory[0xDB0D] == 2
    gb.stop(save=False)
    return (f'{"CGB" if cgb else "DMG"}: {count} real suspend/cold-boot cases retain '
            'page, song and PINCH across all modes; repeated saves, legacy saves, '
            'checksum rejection, OFF, pause/unpause without native BGM overlap '
            'and declining CONTINUE verified')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rom',type=Path,default=ROOT/'output'/OUTPUT_NAME)
    args = parser.parse_args()
    OUT.mkdir(parents=True,exist_ok=True)
    results = [checks(args.rom,cgb) for cgb in (True,False)]
    (OUT/'save-music-results.json').write_text(json.dumps({
        'rom_sha256':hashlib.sha256(args.rom.read_bytes()).hexdigest(),
        'checks':results},indent=2)+'\n',encoding='utf-8')
    print('\n'.join(results))


if __name__ == '__main__':
    main()
