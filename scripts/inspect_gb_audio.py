"""Measure channel interruptions in the unmodified GB ROM using its audio API.

Requires requirements-test.txt. No ROM files are modified. Requests are injected
at the original audio update; reports go under output/validation.
"""
from __future__ import annotations

import hashlib
import io
import json

from pyboy import PyBoy

from build_rom import ROOT, GB_NAME, HASHES


CHANNELS = ['pulse 1', 'pulse 2', 'wave', 'noise']
EFFECTS = {
    'movement': (4, 0, 0), 'rotation': (3, 0, 0),
    'line clear': (6, 0, 0), 'Tetris': (7, 0, 0),
    'level up': (8, 0, 0), 'piece lock': (0, 0, 2),
    'stack fall': (0, 0, 1),
}


def main():
    rom = (ROOT/'original_roms'/GB_NAME).read_bytes()
    assert hashlib.sha256(rom).hexdigest() == HASHES[GB_NAME]
    gb = PyBoy(io.BytesIO(rom), window='null', cgb=False)
    gb.set_emulation_speed(0)
    gb.tick(100, False, False)
    control = {'song': 5, 'first': True, 'frame': -1, 'effect': None,
               'offset': 24, 'record': False}
    rows, triggers = [], []

    def requests(_):
        control['frame'] += 1
        gb.memory[0xDFE8] = control['song'] if control['first'] else 0
        control['first'] = False
        effect = (EFFECTS[control['effect']]
                  if control['effect'] and control['frame'] == control['offset']
                  else (0, 0, 0))
        for address, value in zip([0xDFE0, 0xDFF0, 0xDFF8], effect):
            gb.memory[address] = value

    def before_music(_):
        if control['record']:
            rows.append({
                'frame': control['frame'],
                'locks': [bool(gb.memory[0xDF9F+16*i]&128) for i in range(4)],
            })

    def music_trigger(_):
        if control['record']:
            triggers.append((control['frame'], gb.memory[0xDF70]-1))

    def after_music(_):
        if control['record']:
            # Sequence/section pointers, timer, instrument, pitch and rest.
            # Vibrato/envelope counters and ownership locks are excluded.
            rows[-1]['score'] = [
                list(gb.memory[0xDF90+16*i:0xDF9C+16*i]) for i in range(4)]

    gb.hook_register(1, 0x64FA, requests, None)
    gb.hook_register(1, 0x6509, before_music, None)
    gb.hook_register(1, 0x650C, after_music, None)
    # Reached only after the lock check, when a music note/rest is written.
    gb.hook_register(1, 0x6D21, music_trigger, None)
    results = []
    for selection, song in enumerate('ABC', 5):
        control.update(song=selection, first=True, record=False, effect=None)
        gb.tick(600, False, False)
        assert gb.memory[0xDFE9] == selection
        active = [CHANNELS[i] for i in range(4) if gb.memory[0xDF91+16*i]]
        state = io.BytesIO()
        gb.save_state(state)

        def run(effect, offset):
            gb.load_state(io.BytesIO(state.getvalue()))
            control.update(first=False, frame=-1, effect=effect,
                           offset=offset, record=True)
            rows.clear()
            triggers.clear()
            gb.tick(300, False, False)
            return list(rows), list(triggers)

        baseline, _ = run(None, 24)
        for effect in EFFECTS:
            samples = []
            for offset in [24, 60, 120, 180]:
                observed, writes = run(effect, offset)
                score_matches = all(a['score'] == b['score'] for a, b in zip(observed, baseline))
                assert score_matches, (song, effect, 'changed music score progression')
                channels = {}
                for i, name in enumerate(CHANNELS):
                    held = [r['frame'] for r in observed if r['locks'][i]]
                    if not held:
                        continue
                    assert not any(f in held and channel == i for f, channel in writes)
                    # Wave/Tetris effects may lock channels without replacing
                    # their already sounding notes: these are ownership spans,
                    # not necessarily complete acoustic silence.
                    spans = []
                    for frame in held:
                        if spans and frame == spans[-1][1]+1:
                            spans[-1][1] = frame
                        else:
                            spans.append([frame, frame])
                    channels[name] = []
                    for first, last in spans:
                        following = [f for f, channel in writes if channel == i and f > last]
                        channels[name].append({
                            'start': first-offset, 'locked_frames': last-first+1,
                            'frames_from_release_to_next_music_note_or_rest': (
                                min(following)-(last+1) if following else None),
                        })
                samples.append({'request_frame': offset, 'channels': channels,
                                'score_progression_matches_without_sfx': score_matches})
            results.append({'song': song, 'active_music_channels': active,
                            'effect': effect, 'samples': samples})
    gb.stop(save=False)
    report = {'rom_sha256': HASHES[GB_NAME], 'mode': 'DMG',
              'method': 'Unmodified ROM, injected native audio requests; four timings per song/effect.',
              'results': results}
    output = ROOT/'output'/'validation'/'original_gb_audio.json'
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2)+'\n')
    print(output)
    for row in results:
        spans = {}
        for sample in row['samples']:
            for channel, values in sample['channels'].items():
                spans.setdefault(channel, set()).update(v['locked_frames'] for v in values)
        print(f"{row['song']} {row['effect']}: {spans}")


if __name__ == '__main__':
    main()
