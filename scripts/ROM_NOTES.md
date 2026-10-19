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
Danger/Peril IDs are `$5A/$5B` for A, `$5C/$5D` for B, and `$5E/$5F` for C.

| Fixed-bank entry | Native function | Patch behavior |
| --- | --- | --- |
| `$3F00` | Initialize sound | Called after patch state is cleared at startup |
| `$3F06` | Advance audio | Runs first every frame, retaining native SFX |
| `$3F0C` | Play SFX | Entry unmodified; original effect data and volume retained; pulse selection adjusted below |
| `$3F12` | Start BGM | Intercepts A/B/C preview, normal, Danger and Peril IDs when GB is enabled |
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
| `$014C48` | Mode-row input call: native input, without changing music/PINCH settings |
| `$014C5B` | Music-row input call: Select toggles PINCH, Left/Right switches sets |
| `$014C01` | Initialize the extended music tab before enabling the LCD |
| `$014C61` | VBlank display routine, updates the 1/2 suffix and PINCH strip |
| `$014D6C` | Draw the seven-character inactive label on CGB |
| `$014DB5` | Draw the seven-character inactive label on DMG/SGB |

Left/Right are bits 5/4 of the newly pressed input mask at `$FF83`. Only the
music-focus wrapper checks these to flip the set and restart the current
preview, with `$DB0E` also checked defensively. The mode-row wrapper leaves the
set alone. Select (bit 2) toggles PINCH only with music focus, also checked via
`$DB0E`, without restarting music. Mode selection retains the current setting.
Holding a button changes its setting once. OFF remains silent when the set
changes. Original input handling continues after the wrapper. The
display wrapper writes font tile `$A1` (1, DX) or `$A2` (2, GB) at `$99A9`
during the existing menu VBlank routine, preserving the VRAM bank.

The tab's left edge moves one tile (8 pixels) left. With the LCD off, the
initialization wrapper reuses border tiles `$29/$39/$49` at column 2 and fills
the former edge at column 3. The seven font cells at `$99A3..99A9` read MUSIC-1/2;
the hyphen is `$9F`. CGB attributes use the original text palette 7, with border
palette 1 retained. Attribute writes are skipped on DMG/SGB by checking that
VRAM bank 0 can be selected.

The original inactive MUSIC sprites use a different font color from the focused
background label. Seven-character sprite lists preserve that treatment for the
whole new label, with separate CGB and DMG attributes. At menu initialization,
font glyphs for -, 1 and 2 are converted to that sprite color in three otherwise
unused menu tiles (`$9B/$C8/$C9`). The CGB wrapper replaces the label drawing
call; the DMG wrapper replaces only the inactive label and retains the native
song cursor when music has focus. Original ROM graphics and other menus' sprite
lists remain intact.

The 80x8 PINCH strip occupies background row 17, columns 0..9 (screen x=0..79,
y=136..143; tilemap `$9A20..9A29`). With the LCD off, the builder's injected
routine bakes two sets of ten tiles, reading the existing bottom-row patterns
and ORing black font pixels into both bitplanes. This preserves the textured
color backdrop and the monochrome background. The colon is a small embedded
bitmap; other characters reuse the native font. `PINCH_TILES` lists twenty
unused positive BG tile IDs, whose VRAM addresses `$9000+` do not overlap sprite
tiles. PINCH text is black with music focus and entirely hidden with mode focus
on both CGB and DMG. Initialization saves the ten original bottom-row tile IDs
at `$DF9D..DFA6` before replacing any tilemap entries. VBlank chooses the black
ON/OFF tilemap with music focus, or restores those original IDs with mode focus.
No tile graphics are rewritten in VBlank. All native BG/OBJ palettes and strip
attributes are retained; PINCH uses no sprites. BG palette 5 is also used by the
song-selection window, so it must not be repurposed for the indicator.
Both menu settings persist in WRAM until reset, with PINCH initialized to ON.

## Original player and ROM banking

The build expands DX to 64 banks (1 MiB), keeping its MBC1/RAM/battery cartridge
type. The original Tetris region `$6480..7FFF` is copied to banks `$21` (normal),
`$22` (Danger), and `$23` (Peril) at those same CPU addresses. Keeping addresses
avoids relocating its sequence pointers,
pattern lists, instruments, and waveform references. Bank `$20` is not used
because MBC1 remaps an upper-window bank whose low five bits are zero.

Control code occupies previously zero-filled fixed ROM starting at `$3400`.
`Code` emits the instructions, resolves labels, and records symbol addresses in
the build report. Expanded-bank calls disable interrupts, save DX's bank shadow
at `$FFFD`, set MBC1's high bank register, and select bank `$21 + variant`.
The high register
is cleared and the prior low bank restored before interrupts are re-enabled.
This prevents DX's VBlank routines from seeing the wrong high bank bits.

Original entry points used by the bridge:

- `$69A5`: initialize music state and clear locks
- `$69C7`: mute channels
- `$6998`: load a waveform
- `$6A21`: start the requested song
- `$6C44`: advance music by one frame

Original song IDs 5, 6, and 7 are selected for A/B/C. Normal preview and gameplay
requests use the full original songs. Only those three original songs are invoked.

## Danger and Peril

DX's native height decision at bank `$01`, CPU `$4C40..4C6B`, selects normal
music below 11 rows, Danger at 11..13, and Peril at 14..16. The bridge intercepts
the six variant IDs above and retains DX's conditional restart behavior: a change
of version starts the song once, while repeated requests leave it advancing.
At 17 rows DX uses a separate shared alarm (`$4C`); that alarm remains native
when PINCH is ON. With PINCH OFF, a checked hook at ROM `$004C47` sends every
height decision to the original normal-music path at CPU `$4C4B`, using `$AC0E`.
This suppresses Danger, Peril, and the final alarm for both music sets, and zero
still means music OFF. The existing conditional BGM call prevents repeated
height checks from restarting the normal song. Actual stack height and other
gameplay behavior are retained. PINCH ON follows the original thresholds.

B-TYPE's Danger/Peril versions omit the opening 192 music ticks (about 3.2
seconds at normal speed). The first two sections of each tonal part last 96
ticks each. In banks `$22/$23`, the B header's list pointers at `$6F79/$6F7B/$6F7D`
advance by four bytes to start at section 3: pulse 1 at `$738E`, pulse 2 at
`$7367`, and wave at `$73B5`. Each starts with its own instrument command. The
loop destinations at `$72D2/$72C4/$7300` also advance by four bytes so the intro
does not recur. The 48-tick percussion loop already aligns with the 192-tick
boundary and needs no edit. The normal B song and its preview retain the intro.

Danger raises tonal pitches by two semitones, and Peril by four. Each variant
bank has a private pitch table at `$6000..60FF`, selected by changing the original
tonal lookup's `ld hl, $6E02` at `$6CAA`. The table covers all even byte indexes,
including B-TYPE's wave note `$94`, which originally reads `$6E96` past the
documented pitch table. The builder transposes the 11 frequency bits using
`2048 - (2048 - frequency) / 2**(semitones/12)` and rounds to an integer,
preserving the other bits. The original noise table and percussion timbres
remain intact, as do all note streams, lengths, instruments, and waveforms.

A fractional clock advances the complete GB player once per frame, plus one
extra step every four frames for Danger or every two for Peril. This yields
exact 1.25x and 1.5x score speeds without rounding individual note lengths or
drifting between parts. The phase resets on a new track and freezes on pause.
DX's native player still updates once per frame, preserving SFX timing and
volume. Locks and restoration apply before either GB music step.

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
| `$DF98` | GB variant: 0 normal, 1 Danger, 2 Peril |
| `$DF99` | Fractional music-clock phase, 0..3 |
| `$DF9A` | PINCH enabled, initialized to 1 |
| `$DF9B..DF9C` | Temporary glyph row mask/counter during menu initialization |
| `$DF9D..DFA6` | Ten original bottom-row BG tile IDs for hiding PINCH |

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

## Channel sharing in the original GB game

The original GB game also prioritizes effects over music. Its `PlaySquareSFX`
locks pulse 1 (`$DF9F` bit 7); `PlayNoiseSFX` locks noise (`$DFCF` bit 7).
Movement and rotation use pulse 1 only, unlike DX rotation's additional wave
voice. Failed movement/rotation cancels the GB effect request in the gameplay
code, whereas DX's blocked-action effect uses pulse 2.

The song headers at `$6F6B` and `$6F76` enable all four music channels for
A-TYPE and B-TYPE, including noise percussion. C-TYPE's header at `$6F81`
enables only the two pulse channels. Thus lock/stack-fall noise effects can
replace A/B percussion, but do not replace a C-TYPE music part. Ordinary GB
movement/rotation leaves pulse 2 and wave music available.

Measured channel reservation times in the original Rev 1 ROM:

| Effect | Reserved channels | Audio frames (approximately 59.73 per second) |
| --- | --- | --- |
| Movement | Pulse 1 | 2 (~0.033 s) |
| Rotation | Pulse 1 | 18 (~0.30 s) |
| Single/double/triple line clear | Pulse 1 | 66 (~1.10 s) |
| Level up | Pulse 1 | 35 (~0.59 s) |
| Piece lock | Noise | 18 (~0.30 s) |
| Stack fall after clearing | Noise | 32 (~0.54 s) |
| Four-line Tetris | Pulse 1 for the whole effect; all four during its wave sweep | 56 total; final 32 lock all channels (~0.54 s) |

These are ownership durations, not exact audible durations or guaranteed
silence. The four-line wave sweep locks pulse 2 and noise without explicitly
silencing their existing notes, so those notes can ring while subsequent music
notes are suppressed. Overlapping effects may add reservations or replace an
earlier effect; the table measures isolated requests.

Music section pointers and note timers continue advancing during all seven
tested effects. The original does not immediately retrigger the current music
note when an effect releases its channel: normal note output resumes at the
next scheduled note/rest. Consequently a gap can extend beyond the ownership
duration. The DX bridge's immediate note restoration avoids that extra wait.

Run `.venv\Scripts\python.exe scripts/inspect_gb_audio.py` to reproduce the
measurements. It injects native audio requests into the original ROM in DMG
emulation, checks all three songs at four request timings per effect, verifies
score progression against a no-effect run and guards against music note output
on locked channels. The detailed report is
`output/validation/original_gb_audio.json`. The original ROM file and the DX
builder/output are unchanged by this inspection.

## Verification and development tools

`test_rom.py` uses in-memory ROM/save streams and never saves beside the originals.
It navigates through profile creation and Guest selection using normal buttons.
It checks Game Boy and Game Boy Color execution, selection debounce, both menu
handlers, six previews, OFF, three Marathon music selections, pause/resume, native
SFX ownership, ROM checksums, and original input hashes. Regression checks verify
that mode-row Left/Right cannot switch sets, music-focus Left/Right switches
once per press, Select changes only PINCH with music focus, mode-row Select
retains both ON and OFF, its default is ON, and its strip tracks the setting
with no text on mode rows and black text in music focus. Pixel checks compare
the entire hidden 80x8 strip against native DX in both hardware modes, including
returning from music selection with PINCH OFF. CGB checks verify all native
BG/OBJ palette sources and compare rendered song-window backdrop/border pixels
across all four modes, focus changes, all four song choices, and PINCH toggles.
They also verify palettes on level selection and gameplay. They check
that the extended border and MUSIC-1/2
label are correct with both focus states and all song selections, all seven
inactive letters have matching sprites, and only the pulse-selection call
changes in the DX sound-driver bank.
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
exceptions. Variant checks supply stack height at the real DX decision routine,
exercise both directions of each threshold for A/B/C, and verify OFF and the DX
set. They compare each variant's complete score with normal playback in music
ticks, checking exact note order/rhythm, transposition (including B's extended
wave note), unchanged percussion timbres, and fractional tick counts. For B,
the reference removes both intro sections and their durations on every loop of
normal playback; percussion retains its original phase. This checks the cut at
the start and on repeats while the normal song still matches the original ROM.
Pause and
SFX ownership/restoration checks also run with the faster variants. Results and
screenshots are regenerated in `output/validation`.

PINCH OFF checks use normal menu buttons to choose each set and A/B/C/OFF, then
exercise the real height routine at 0, 11, 14, 17, and 18 rows in both directions.
They verify only normal-music requests, no DX/GB song restarts, silence for music
OFF, and preserved pause/resume. PINCH ON checks include the native final alarm
and transitions back from it.

`gb_disasm.py ROM START END` disassembles an offset range using hex (`0x...`) or
decimal addresses. `inspect_dx.py --rom ROM --steps '600,start,120,...'` captures
screenshots, WRAM, save states, and sound API calls for reverse engineering.
Neither tool is required by the end user. The builder imports the disassembler
to locate instruction boundaries for WRAM relocation.
