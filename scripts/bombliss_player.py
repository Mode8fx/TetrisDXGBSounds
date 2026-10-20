"""Relocate the supplied Bombliss driver and isolate its APU writes from DX SFX.

No music bytes are embedded here. Expanded instructions are reassembled with
labels, including relative branches; source data/score pointers remain intact.
"""
from gb_disasm import instruction
from bombliss_dx_music_finder import locate, ROM_NAME, SHA256

BANK = 0x24
START, FRAME, MUTE, TICK = 0x4000, 0x4003, 0x4006, 0x4009
SLOTS, GLOBALS, SCRATCH = 0xDF00, 0xDF64, 0xDF70
LOCKS, READ_TEMP = 0xDF6E, 0xDF8F
APU, WAVE = 0xDFA8, 0xDFBF
NATIVE = [0xDEA0, 0xDEB8, 0xDED0, 0xDEE8]


def shadow(register):
    return APU + register - 0x10 if register < 0x30 else WAVE + register - 0x30


def compile_player(source, Code, semitones=0):
    c = Code(0x4000)
    c.jp('start')
    c.jp('frame')
    c.jp('mute')
    c.jp('src_1db5')  # extra fractional-clock step; locks already synchronized
    c.data.extend(bytes(4))
    c.mark('pitches')
    for p in range(0x22F3, 0x2323, 2):
        period = int.from_bytes(source[p:p+2], 'little')
        c.word(round(period / 2**(semitones/12)))

    def relocated(address):
        if 0x1D48 <= address < 0x25B3:
            return f'src_{address:04x}'
        if address == 0xC300:
            return SLOTS
        if 0xC396 <= address <= 0xC39E:
            return GLOBALS + address - 0xC396
        if 0xFFE6 <= address <= 0xFFFE:
            return SCRATCH + address - 0xFFE6
        if address == 0xFF30:
            return WAVE
        return address

    ranges = [(0x1D48, 0x1EC4), (0x1F04, 0x2081), (0x2091, 0x22F3)]
    data_ranges = [(0x1EC4, 0x1F04), (0x2081, 0x2091), (0x22F3, 0x25B3)]
    sections = sorted([(a,b,True) for a,b in ranges] + [(a,b,False) for a,b in data_ranges])
    for first, end, executable in sections:
        pos = first
        while pos < end:
            c.mark(f'src_{pos:04x}')
            if not executable:
                c.data.append(source[pos])
                pos += 1
                continue
            size, _ = instruction(source, pos)
            op = source[pos]
            raw = source[pos:pos+size]
            if pos == 0x1D55:
                c.emit('af')  # source bank-save push/pop remain balanced
            elif pos in (0x1D5B, 0x1DAA):
                pass  # bridge owns MBC1 banking
            elif pos == 0x1D67:
                c.op(0x11, 0x7F00)  # twelve private descriptors
            elif pos == 0x1E5B:
                assert raw == bytes.fromhex('fe 06')
                c.emit('fe 04')  # four music slots; source game's SFX omitted
            elif pos == 0x20D8:
                c.op(0x21, 'pitches')
            elif pos == 0x1EC2:
                # The source copied wave RAM through DE. Upload the private
                # copy before returning to header initialization.
                c.call('upload_wave')
                c.jp(relocated(0x1E91))
            elif op in (0x18, 0x20, 0x28, 0x30, 0x38):
                delta = raw[1] if raw[1] < 128 else raw[1]-256
                c.op({0x18:0xC3,0x20:0xC2,0x28:0xCA,0x30:0xD2,0x38:0xDA}[op],
                     relocated(pos + 2 + delta))
            elif op in (0xE0, 0xF0):
                address = 0xFF00 + raw[1]
                if address >= 0xFFE6:
                    c.op(0xEA if op == 0xE0 else 0xFA, relocated(address))
                elif op == 0xF0:
                    c.ld_a(shadow(raw[1]))
                else:
                    c.emit(f'c5 0e {raw[1]:02x}')
                    c.call('write_apu')
                    c.emit('c1')
            elif op in (0xE2, 0xF2):
                # This source site writes pulse/noise envelopes; keep the
                # private values intact and attenuate only physical output.
                c.call('write_volume' if pos == 0x21FD else
                       'write_apu' if op == 0xE2 else 'read_apu')
            elif size == 3:
                c.op(op, relocated(int.from_bytes(raw[1:], 'little')))
            else:
                c.data.extend(raw)
            pos += size
        assert pos == end

    c.mark('write_volume')
    c.emit('f5 c5 e5')
    c.mark('volume_stack')
    c.emit('47 26 df 79 c6 98 6f 70')
    # Half the initial volume nibble, retaining envelope direction/period.
    # A/B/L are private to this wrapper; the caller's registers are restored.
    c.emit('78 cb 3f e6 f0 6f 78 e6 0f b5 47')
    c.jp('write_routed')

    c.mark('write_apu')
    c.emit('f5 c5 e5 47')
    c.mark('write_stack')
    c.emit('26 df 79 c6 98 6f')  # all indirect APU accesses are $FF10..26
    c.emit('70 79 fe 1a')
    # Wave-active status belongs to the virtual music channel. Native DX
    # effects must not influence whether the next music note needs a trigger.
    c.op(0xC2,'write_wave_trigger')
    c.emit('78 e6 80')
    c.op(0xC2,'write_wave_trigger')
    c.ld_a(shadow(0x26))
    c.emit('e6 fb')
    c.store_a(shadow(0x26))
    c.mark('write_wave_trigger')
    c.emit('79 fe 1e')
    c.op(0xC2,'write_routed')
    c.emit('78 e6 80')
    c.op(0xCA,'write_routed')
    c.ld_a(shadow(0x1A))
    c.emit('e6 80')
    c.op(0xCA,'write_routed')
    c.ld_a(shadow(0x26))
    c.emit('f6 04')
    c.store_a(shadow(0x26))
    c.mark('write_routed')
    c.emit('79 fe 24')
    c.op(0xD2,'write_done')  # retain DX's master volume and stereo routing
    # The driver only writes its current hardware channel. Snapshotting its
    # ownership once per music step avoids repeated native-state lookups.
    c.ld_a(GLOBALS+3)  # current channel's $11/$22/$44/$88 mask
    c.emit('6f')
    c.ld_a(LOCKS)
    c.emit('a5')
    c.op(0xC2,'write_done')
    c.mark('write_hardware')
    c.emit('78 e2')
    c.mark('write_done')
    c.emit('e1 c1 f1 c9')

    c.mark('read_apu')
    c.emit('f5 e5')
    c.mark('read_stack')
    c.emit('26 df 79 c6 98 6f')
    c.emit('7e')
    c.store_a(READ_TEMP)
    c.emit('e1 f1')
    c.ld_a(READ_TEMP)
    c.emit('c9')

    c.mark('upload_wave')
    c.emit('f5')
    c.ld_a(NATIVE[2])
    c.emit('e6 80')
    c.op(0xC2,'wave_done')
    c.emit('c5 d5 e5')
    c.op(0x21,WAVE)
    c.op(0x11,0xFF30)
    c.emit('06 10')
    c.mark('wave_byte')
    # Halve each 4-bit sample around the DAC midpoint: 0..15 -> 4..11.
    # Retain the original waveform privately for the driver's own state.
    c.emit('2a cb 3f e6 77 c6 44 12 13 05')
    c.op(0xC2,'wave_byte')
    c.mark('wave_uploaded')
    c.emit('3e ff')
    c.store_a(0xDC9D)  # invalidate native waveform cache
    c.emit('e1 d1 c1')
    c.mark('wave_done')
    c.emit('f1 c9')

    c.mark('start')
    c.emit('f5 af')
    for address, count in [(SLOTS,0x90),(APU,39)]:
        c.op(0x21,address)
        c.emit(f'06 {count:02x}')
        c.mark(f'clear_{address:04x}')
        c.emit('22 05')
        c.op(0xC2,f'clear_{address:04x}')
    c.emit('3e 80')
    c.store_a(shadow(0x26))
    for channel in range(4):
        c.emit('3e ff')
        c.store_a(SLOTS+channel*25)
        c.store_a(SLOTS+channel*25+23)
    c.emit('f1 d6 05 87 87')
    c.jp(relocated(0x1D48))

    c.mark('frame')
    # Restore notes on channel release/unpause, before advancing the score.
    for channel,native in enumerate(NATIVE):
        c.ld_a(native)
        c.emit('e6 80')
        c.op(0xC2,f'held_{channel}')
        c.ld_a(LOCKS)
        c.emit(f'e6 {1<<channel:02x} 47')
        c.ld_a(0xDF96)
        c.emit('b0')
        c.op(0xC4,f'restore_{channel}')
        c.ld_a(LOCKS)
        c.emit(f'e6 {255-(1<<channel):02x}')
        c.jp(f'lock_saved_{channel}')
        c.mark(f'held_{channel}')
        c.ld_a(LOCKS)
        c.emit(f'f6 {1<<channel:02x}')
        c.mark(f'lock_saved_{channel}')
        c.store_a(LOCKS)
    c.jp(relocated(0x1DB5))

    for channel,registers in enumerate([
            [0x10,0x11,0x12,0x13,0x14], [0x16,0x17,0x18,0x19],
            [0x1A,0x1B,0x1C,0x1D,0x1E], [0x20,0x21,0x22,0x23]]):
        c.mark(f'restore_{channel}')
        if channel == 2:
            c.emit('af e0 1a')
            c.call('upload_wave')
        for register in registers:
            c.ld_a(shadow(register))
            if register in (0x12,0x17,0x21):
                c.emit('47 cb 3f e6 f0 6f 78 e6 0f b5')
            if register == registers[-1]:
                c.emit('f6 80')
            c.emit(f'e0 {register:02x}')
        c.emit('c9')

    c.mark('mute')
    for channel,register in enumerate([0x12,0x17,0x1C,0x21]):
        c.ld_a(NATIVE[channel])
        c.emit('e6 80')
        c.op(0xC2,f'mute_skip_{channel}')
        c.emit(f'af e0 {register:02x}')
        c.mark(f'mute_skip_{channel}')
    c.emit('c9')
    payload = c.finish()
    if c.origin + len(payload) > 0x5176:
        raise ValueError(f'Bombliss driver overlaps score data: ${c.origin+len(payload):04X}')
    bank = bytearray([0xFF])*0x4000
    bank[0x1176:0x3F00] = source[0x15176:0x17F00]
    bank[:len(payload)] = payload
    songs = locate(source)
    for index, song in enumerate(songs.values()):
        for channel, stream in enumerate(song['streams']):
            entry = bytes([channel*25, channel]) + stream['start_cpu'].to_bytes(2,'little')
            at = 0x3F00+index*16+channel*4
            bank[at:at+4] = entry
    return bank, c.labels
