"""Development aid: capture DX screens, RAM and audio API calls with PyBoy."""
import argparse
import io
from pathlib import Path
from pyboy import PyBoy

ROOT = Path(__file__).resolve().parents[1]

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rom', type=Path, default=next((ROOT / 'original_roms').glob('*.gbc')))
    parser.add_argument('--steps', default='600,start,120,a,120,a,120')
    args = parser.parse_args()
    out = ROOT / 'output' / 'inspection'
    out.mkdir(parents=True, exist_ok=True)
    gb = PyBoy(io.BytesIO(args.rom.read_bytes()), ram_file=io.BytesIO(bytes(0x2000)), window='null')
    gb.set_emulation_speed(0)
    def log(addr):
        reg = gb.register_file
        sp = reg.SP
        stack = bytes(gb.memory[sp:sp+12]).hex(' ')
        print(f'API {addr:04x} BC={reg.B:02x}{reg.C:02x} state={gb.memory[0xc0a0]:02x}/{gb.memory[0xc0a1]:02x} stack={stack}')
    for addr in [0x3f00,0x3f0c,0x3f12,0x3f18,0x3f24,0x3f2a]:
        gb.hook_register(0,addr,log,addr)
    for i, step in enumerate(args.steps.split(',')):
        if step.isdigit():
            gb.tick(int(step), True, False)
        else:
            gb.button_press(step)
            gb.tick(5, True, False)
            gb.button_release(step)
            gb.tick(5, True, False)
        print(i, step, 'state',bytes(gb.memory[0xc0a0:0xc0b0]).hex(' '),'input',bytes(gb.memory[0xff80:0xff88]).hex(' '))
        gb.screen.image.save(out / f'{i:02d}.png')
        (out / f'{i:02d}.ram').write_bytes(bytes(gb.memory[0xc000:0xe000]))
        with (out / f'{i:02d}.state').open('wb') as f:
            gb.save_state(f)
    gb.stop(save=False)

if __name__ == '__main__':
    main()
