"""Small LR35902 disassembler used to inspect the DX routines (no dependencies)."""
import argparse
from pathlib import Path

REG = ['b', 'c', 'd', 'e', 'h', 'l', '[hl]', 'a']
PAIR = ['bc', 'de', 'hl', 'sp']

def instruction(data, offset, cpu=None):
    cpu = offset if cpu is None else cpu
    op = data[offset]
    x, y, z = op >> 6, (op >> 3) & 7, op & 7
    p, q = y >> 1, y & 1
    n = data[offset+1] if offset+1 < len(data) else 0
    nn = n | ((data[offset+2] if offset+2 < len(data) else 0) << 8)
    if x == 1:
        return (1, 'halt' if op == 0x76 else f'ld {REG[y]}, {REG[z]}')
    if x == 2:
        return 1, f'{["add a,", "adc a,", "sub", "sbc a,", "and", "xor", "or", "cp"][y]} {REG[z]}'
    if x == 0:
        if z == 0:
            if y == 0: return 1, 'nop'
            if y == 1: return 3, f'ld [${nn:04x}], sp'
            if y == 2: return 2, 'stop'
            cond = '' if y == 3 else ['nz, ', 'z, ', 'nc, ', 'c, '][y-4]
            return 2, f'jr {cond}${(cpu+2+(n if n < 128 else n-256)) & 65535:04x}'
        if z == 1:
            return (1, f'add hl, {PAIR[p]}') if q else (3, f'ld {PAIR[p]}, ${nn:04x}')
        if z == 2:
            target = ['[bc]', '[de]', '[hl+]', '[hl-]'][p]
            return 1, f'ld a, {target}' if q else f'ld {target}, a'
        if z == 3: return 1, f'{"dec" if q else "inc"} {PAIR[p]}'
        if z == 4: return 1, f'inc {REG[y]}'
        if z == 5: return 1, f'dec {REG[y]}'
        if z == 6: return 2, f'ld {REG[y]}, ${n:02x}'
        return 1, ['rlca','rrca','rla','rra','daa','cpl','scf','ccf'][y]
    if z == 0:
        if y < 4: return 1, f'ret {["nz","z","nc","c"][y]}'
        return 2, [f'ldh [${0xff00+n:04x}], a',f'add sp, ${n:02x}',f'ldh a, [${0xff00+n:04x}]',f'ld hl, sp+${n:02x}'][y-4]
    if z == 1:
        if not q: return 1, f'pop {["bc","de","hl","af"][p]}'
        return 1, ['ret','reti','jp hl','ld sp, hl'][p]
    if z == 2:
        if y < 4: return 3, f'jp {["nz","z","nc","c"][y]}, ${nn:04x}'
        return [(1, 'ld [$ff00+c], a'),(3, f'ld [${nn:04x}], a'),(1, 'ld a, [$ff00+c]'),(3, f'ld a, [${nn:04x}]')][y-4]
    if z == 3:
        if y == 0: return 3, f'jp ${nn:04x}'
        if y == 1:
            return 2, (f'{["rlc","rrc","rl","rr","sla","sra","swap","srl"][(n>>3)&7]} {REG[n&7]}' if n < 64 else f'{["bit","res","set"][(n>>6)-1]} {(n>>3)&7}, {REG[n&7]}')
        return 1, {6:'di',7:'ei'}.get(y,'illegal')
    if z == 4: return (3, f'call {["nz","z","nc","c"][y]}, ${nn:04x}') if y < 4 else (1,'illegal')
    if z == 5:
        if not q: return 1, f'push {["bc","de","hl","af"][p]}'
        return (3, f'call ${nn:04x}') if p == 0 else (1,'illegal')
    if z == 6: return 2, f'{["add a,","adc a,","sub","sbc a,","and","xor","or","cp"][y]} ${n:02x}'
    return 1, f'rst ${y*8:02x}'

def disassemble(data, start, end):
    while start < end:
        cpu = start if start < 0x4000 else 0x4000 + start % 0x4000
        size, name = instruction(data, start, cpu)
        print(f'{start:06x} ({cpu:04x}) {data[start:start+size].hex(" "):8s} {name}')
        start += size

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('rom', type=Path)
    parser.add_argument('start', type=lambda s: int(s, 0))
    parser.add_argument('end', type=lambda s: int(s, 0))
    args = parser.parse_args()
    disassemble(args.rom.read_bytes(), args.start, args.end)
