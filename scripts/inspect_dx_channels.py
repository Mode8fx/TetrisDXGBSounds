"""Trace original DX music/SFX hardware ownership without modifying its ROM.

Music IDs are requested through the native music API; effect IDs through a
normal gameplay rotation's SFX API call. Requires requirements-test.txt.
"""
import argparse
import hashlib
import json

from build_rom import ROOT, DX_NAME, HASHES
from test_rom import emulator, boot_menu, press, snapshot, restore
from tetris_dx_music_finder import SONGS

CHANNELS = ['pulse 1','pulse 2','wave','noise']
BASES = [0xDEA0,0xDEB8,0xDED0,0xDEE8]


def owner(gb,address):
    if not gb.memory[address]&0x80:
        return None
    voice = gb.memory[address+4] | gb.memory[address+5]<<8
    return gb.memory[voice+5] | gb.memory[voice+6]<<8


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--frames',type=int,default=7200)
    args = parser.parse_args()
    if args.frames<1: parser.error('--frames must be positive')
    path = ROOT/'original_roms'/DX_NAME
    assert hashlib.sha256(path.read_bytes()).hexdigest() == HASHES[DX_NAME]
    gb = emulator(path)
    boot_menu(gb)
    mode_menu = snapshot(gb)
    press(gb,'a')
    music_menu = snapshot(gb)
    wanted = [0x37]
    used = set()
    def request_music(_):
        gb.register_file.B,gb.register_file.C = 0,wanted[0]
    def sample_music(_):
        used.update(i for i,a in enumerate(BASES) if owner(gb,a) == wanted[0])
    gb.hook_register(0,0x3F18,request_music,None)
    gb.hook_register(0,0x0240,sample_music,None)
    music = []
    for name,song_id in SONGS.items():
        restore(gb,music_menu)
        wanted[0] = song_id
        used.clear()
        press(gb,'down')
        assert gb.memory[0xDC9B] == song_id
        gb.tick(args.frames,False,False)
        music.append({'song':name,'song_id':song_id,
                      'channels':[CHANNELS[i] for i in sorted(used)]})
        print(name,':',', '.join(music[-1]['channels']))
    gb.hook_deregister(0,0x3F18)
    gb.hook_deregister(0,0x0240)

    effects = []
    desired = [0x21]
    frame = [0]
    samples = [[] for _ in BASES]
    def request_effect(_): gb.register_file.C = desired[0]
    def sample_effect(_):
        for i,address in enumerate(BASES):
            if owner(gb,address) == desired[0]: samples[i].append(frame[0])
        frame[0] += 1
    gb.hook_register(0,0x3F0C,request_effect,None)
    gb.hook_register(0,0x0240,sample_effect,None)
    for song in [1,2,3]:
        restore(gb,mode_menu)
        press(gb,'a')
        for _ in range(song-1): press(gb,'down')
        press(gb,'a',settle=120)
        press(gb,'a',settle=120)
        assert gb.memory[0xDC9B] == 0x43+song
        gameplay = snapshot(gb)
        for sound_id in [0x21,0x22,0x23,0x24,0x25,0x26,0x27,0x28,
                         0x2B,0x2C,0x2D,0x2E,0x2F,0x34,0x3C]:
            restore(gb,gameplay)
            desired[0],frame[0] = sound_id,0
            for channel in samples: channel.clear()
            gb.button_press('a')
            for f in range(240):
                if f == 5: gb.button_release('a')
                gb.tick(1,False,False)
            row = {'song':'ABC'[song-1],'effect_id':sound_id,'channels':{}}
            for channel,times in zip(CHANNELS,samples):
                if times:
                    row['channels'][channel] = {'owned_updates':len(times),
                                                'first':min(times),'last':max(times)}
            effects.append(row)
            if song == 1: print(f'SFX ${sound_id:02X}:',row['channels'])
    gb.stop(save=False)
    report = {'rom_sha256':HASHES[DX_NAME],'music_observation_frames':args.frames,
              'method':'CGB, unmodified DX, native requests, hardware owner after each audio update; sets are observed use, not simultaneous continuous sound.',
              'music':music,'effects':effects}
    out = ROOT/'output/validation/dx-channel-usage.json'
    out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')


if __name__ == '__main__':
    main()
