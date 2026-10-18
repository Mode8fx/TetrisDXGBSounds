# ROM patch implementation

The builder accepts only the SHA-256 hashes in `build_rom.py`. All addresses below
refer to the supplied World DX ROM and World Rev 1 Tetris ROM. The original
disassembly is used as documentation; RGBDS is not needed to build the patch.
No copyrighted music bytes are stored in the scripts: the builder reads them from
the user's Tetris ROM.

## Native DX interface

DX's sound driver occupies bank `$0C`. Its six-byte song table is at ROM `$03146D`
(CPU `$546D`). Each entry contains a little-endian CPU pointer, ROM bank,
priority, starting voice, and voice count. Preview IDs are `$37..39`; gameplay
IDs are `$44..46`. These native table entries and all native song data are retained.

| Fixed-bank entry | Native function | Patch behavior |
| --- | --- | --- |
| `$3F00` | Initialize sound | Called after patch state is cleared at startup |
| `$3F06` | Advance audio | Runs first every frame, retaining native SFX |
| `$3F0C` | Play SFX | Entry unmodified; original effect data and volume retained; pulse selection adjusted below |
| `$3F12` | Start BGM | Intercepts only the six music IDs when GB is enabled |
| `$3F18` | Start BGM if different | Preserves conditional restart behavior |
| `$3F24` | Suspend BGM | Also freezes and mutes the original player |
| `$3F2A` | Resume BGM | Also resumes the original player and restores notes |

The BGM ID arrives in BC. Passing zero stops native BGM. The bridge calls the
original native entry bodies rather than recursively entering its patched API.
Other songs, including menus, game over, and results, continue using DX's player.
The builder writes hooks over checked, explicit byte sequences and refuses to
overwrite unexpected data.

## Menu hooks

The game/music menu is state `$04`. Its code is in bank `$05`:

| ROM offset | Purpose |
| --- | --- |
| `$014C48` | Mode-row input call, originally to CPU `$4C6F` |
| `$014C5B` | Music-row input call, originally to CPU `$4CAB` |
| `$014C61` | VBlank display routine, blanks the former indicator cells |

Select is bit 2 of the newly pressed input mask at `$FF83`. Both input wrappers
check this bit and flip the set. Only when `$DB0E` indicates music focus does the
wrapper restart the selected preview. Selecting the set on a mode row leaves the
existing menu music playing. Holding Select switches once. OFF remains silent
when the set changes. Original input handling continues after the wrapper. The
display wrapper writes the space character `$7F` into `$99A9` and `$99AA` during
the existing menu VBlank routine, preserving the VRAM bank.

## Original player and ROM banking

The build expands DX to 64 banks (1 MiB), keeping its MBC1/RAM/battery cartridge
type. The original Tetris region `$6480..7FFF` is copied to bank `$21` at those
same CPU addresses. Keeping addresses avoids relocating its sequence pointers,
pattern lists, instruments, and waveform references. Bank `$20` is not used
because MBC1 remaps an upper-window bank whose low five bits are zero.

Control code occupies previously zero-filled fixed ROM starting at `$3400`.
`Code` emits the instructions, resolves labels, and records symbol addresses in
the build report. Expanded-bank calls disable interrupts, save DX's bank shadow
at `$FFFD`, set MBC1's high bank register, and select bank `$21`. The high register
is cleared and the prior low bank restored before interrupts are re-enabled.
This prevents DX's VBlank routines from seeing the wrong high bank bits.

Original entry points used by the bridge:

- `$69A5`: initialize music state and clear locks
- `$69C7`: mute channels
- `$6998`: load a waveform
- `$6A21`: start the requested song
- `$6C44`: advance music by one frame

Original song IDs 5, 6, and 7 are selected for A/B/C. Both preview and gameplay
requests use the full original songs. Only those three original songs are invoked.

## RAM relocation and sound effects

DX's audio state occupies `$DC80..DEFF`; its stack starts at `$DFFF`. The original
player's `$DF70..DFFF` state is moved down `$70` to `$DF00..DF8F`. The builder
decodes actual instructions in the used original routines and relocates their
16-bit WRAM operands. It also changes the noise instrument copier's literal
low-byte end bound at original ROM `$6CC9` from `$CB` to `$5B`. Moving only its
pointer without this bound would overwrite the patch's control state.

| RAM | Meaning |
| --- | --- |
| `$DF90` | Selected set: 0 = DX, 1 = GB |
| `$DF91` | Original music active |
| `$DF92` | Original music paused |
| `$DF93` | Saved native ROM bank |
| `$DF94` | Last original preview/gameplay request ID |
| `$DF95` | Unconditional versus conditional BGM request |
| `$DF96` | Restore current notes on resume |
| `$DF97` | Native NR51 routing snapshot for the current audio update |

While imported BGM plays, native BGM voices are stopped, but DX continues advancing
its SFX. Native hardware-channel flags at `$DEA0`, `$DEB8`, `$DED0`, and `$DEE8`
are copied into the original player's four channel locks before each music frame.
An active SFX owns its channel. The original player's wave-load and wave-decay
writes are guarded as well, since these originally occurred before its lock check.
The DX wave cache is invalidated when the original player actually loads or
restores a waveform. It remains valid while an effect owns the wave channel,
avoiding unnecessary reloads between that effect's notes.

When a sound effect releases a channel, the bridge restores the current original
note immediately without advancing its sequence or changing its duration. It does
the same on unpause. Original music continues advancing while SFX occupy channels,
and freezes during pause. Free music channels use stereo routing.
The original waveform is restored even if the current music note is a rest; the
wave DAC stays off until the next note. This prevents that note from inheriting
the effect's waveform.

## Original effects and channel handoff

Native rotation (`$21`) uses both a wave voice and pulse 2; movement (`$22`) and
blocked actions (`$28`) use pulse 2. During active, unpaused GB music, movement
and rotation's pulse voice use pulse 1 instead. Rotation retains its wave voice;
blocked actions and other effects retain their native assignments. DX music
keeps all native assignments.

The call at ROM `$030B0C` (bank `$0C`, CPU `$4B0C`) normally selects pulse 2 via
`$4178`. Its replacement checks the GB player's active/pause flags, native SFX
processing flag (`$DC80` bit 5), and virtual voice ID (`+$05..06`). Only SFX IDs
`$0021` and `$0022` select pulse 1 via `$416F`. Both native selectors initialize
the hardware voice and register base before the original driver continues.
This three-byte call is the only change in bank `$0C`. The original effect
streams, note lengths, priorities, envelopes, and full volume are retained.
There are no replacement effects or attenuation hooks.

After each native audio update, the bridge snapshots NR51 and advances the GB
player with effect-owned channels locked. It then enables stereo for free music
channels, preserving the native routing and mute bits for effect-owned channels.
This replaces the earlier blanket `$FF` write, which could override effect
panning. When a channel is released, its current music note and waveform are
restored during the same audio update. The music's sequence and note timers keep
advancing during effects, so the parts remain synchronized when they return.

The Game Boy has four shared hardware channels. Keeping full multi-channel
effects necessarily replaces music on those channels temporarily; the bridge
prevents the two players from writing conflicting instruments to a channel,
but cannot provide additional independent hardware voices.

## Verification and development tools

`test_rom.py` uses in-memory ROM/save streams and never saves beside the originals.
It navigates through profile creation and Guest selection using normal buttons.
It checks Game Boy and Game Boy Color execution, selection debounce, both menu
handlers, six previews, OFF, three Marathon music selections, pause/resume, native
SFX ownership, ROM checksums, and original input hashes. Regression checks verify
that mode-row Select leaves menu music alone, both former indicator cells contain
spaces, and only the pulse-selection call changes in the DX sound-driver bank.
Mixer hooks check
that pulse/noise envelope values and the wave-volume conversion retain their
native values with both music sets. Movement/rotation channel checks verify
pulse 1 with GB music and pulse 2 with DX music, retaining rotation's wave voice
and blocked actions' pulse 2. Channel-sharing checks exercise the original
piece effects, verify their hardware channel usage, and compare music state with
a run without effects. A deliberately left-routed effect checks that music does
not override its routing. Released notes must be restored in the same audio
update, including waveform restoration during a rest, and free music channels
must return to stereo. These checks run at the
audio-update hooks because an emulator video-frame boundary can fall before the
mixing step finishes. Note/rest event comparisons
include frame numbers, sequence pointers, durations, instruments, frequencies,
and rest flags over two minutes for each imported song. Hook callback failures
are collected and checked outside callbacks because PyBoy can swallow callback
exceptions. Results and screenshots are regenerated in `output/validation`.

`gb_disasm.py ROM START END` disassembles an offset range using hex (`0x...`) or
decimal addresses. `inspect_dx.py --rom ROM --steps '600,start,120,...'` captures
screenshots, WRAM, save states, and sound API calls for reverse engineering.
Neither tool is required by the end user. The builder imports the disassembler
to locate instruction boundaries for WRAM relocation.
