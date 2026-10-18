"""Locate DX's native preview/gameplay songs; defaults to the repository ROM."""
import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ROM = ROOT/'original_roms'/'Tetris DX (World) (SGB Enhanced) (GB Compatible).gbc'
SONGS = {
    'A-TYPE Preview': 0x37,
    'B-TYPE Preview': 0x38,
    'C-TYPE Preview': 0x39,
    'A-TYPE Gameplay': 0x44,
    'B-TYPE Gameplay': 0x45,
    'C-TYPE Gameplay': 0x46,
}


def find_songs(rom):
    base = 0x30000
    data = rom[base:base+0x4000]
    formats = [(bytes.fromhex('69 60 29 09 29 01'),6),
               (bytes.fromhex('69 60 29 29 29 01'),8)]
    for signature,stride in formats:
        pos = data.find(signature)
        if pos >= 0: break
    else:
        raise ValueError('Music table loader not found in bank $0C')
    table_cpu = int.from_bytes(data[pos+6:pos+8],'little')
    if not 0x4000 <= table_cpu < 0x8000:
        raise ValueError('Music table address is outside the bank window')
    table = base+table_cpu-0x4000
    songs = []
    for name,song_id in SONGS.items():
        entry = table+song_id*stride
        cpu = int.from_bytes(rom[entry:entry+2],'little')
        bank = rom[entry+2]
        offset = bank*0x4000+cpu-0x4000
        if not 0x4000 <= cpu < 0x8000 or not 0 <= offset < len(rom):
            raise ValueError(f'Invalid song table entry for {name}')
        songs.append((name,song_id,bank,cpu,offset))
    return table,stride,songs


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('rom',type=Path,nargs='?',default=DEFAULT_ROM)
    args = parser.parse_args()
    table,stride,songs = find_songs(args.rom.read_bytes())
    print(f'Music table: ROM 0x{table:06X}, entry size {stride}')
    for name,song_id,bank,cpu,offset in songs:
        print(f'{name}: ID ${song_id:02X}, bank ${bank:02X}, CPU ${cpu:04X}, ROM 0x{offset:06X}')


if __name__ == '__main__':
    main()
