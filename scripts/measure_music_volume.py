"""Measure preview RMS for all three pages without gameplay sound effects.

Requires requirements-test.txt. Uses the normal menu on a temporary in-memory
save. Reports contain audio measurements only, never original ROM data.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path

import numpy as np

from test_rom import ROOT, OUTPUT_NAME, emulator, boot_menu, press, snapshot, restore


def measure(path,frames=1200):
    gb = emulator(path)
    boot_menu(gb)
    press(gb,'a')
    menu = snapshot(gb)
    rows = []
    try:
        for page in range(3):
            for song in range(1,4):
                restore(gb,menu)
                for _ in range(song-1): press(gb,'down')
                for _ in range(page): press(gb,'right')
                gb.tick(30,False,False)
                total,squares,count = 0.0,0.0,0
                peak = 0
                for _ in range(frames):
                    gb.tick(1,False,True)
                    samples = gb.sound.ndarray.astype(np.float64)
                    total += float(samples.sum())
                    squares += float(np.square(samples).sum())
                    count += samples.size
                    peak = max(peak,float(np.abs(samples).max()))
                rms = math.sqrt(max(0,squares/count-(total/count)**2))
                rows.append({'page':page+1,'song':'ABC'[song-1],
                             'rms':rms,'peak':peak,'frames':frames})
    finally:
        gb.stop(save=False)
    return {'rom_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
            'method':'CGB, 48 kHz, stereo preview samples, DC-removed RMS; 30-frame lead-in.',
            'measurements':rows}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rom',type=Path,default=ROOT/'output'/OUTPUT_NAME)
    parser.add_argument('--frames',type=int,default=1200)
    parser.add_argument('--output',type=Path,default=ROOT/'output/validation/music-volume.json')
    args = parser.parse_args()
    if args.frames < 1: parser.error('--frames must be positive')
    report = measure(args.rom,args.frames)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    for row in report['measurements']:
        print(f"MUSIC-{row['page']} {row['song']}: RMS {row['rms']:.3f}, peak {row['peak']:.0f}")


if __name__ == '__main__':
    main()
