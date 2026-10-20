# Super Bombliss DX stage music

Researched against `original_roms/Super Bombliss DX (Japan) (En) (SGB Enhanced) (GB Compatible).gbc` (262,144 bytes).

SHA-256: `c282366d16bb443bc0cf234732d28f941996d23b8e6678a6a0f6771b04b02ad5`.

All numbers below are hexadecimal unless explicitly stated otherwise. File offsets are zero-based, with no extra copier header. `05:74C0` means CPU address `$74C0` with ROM bank `$05` selected. For this bank, `file_offset = $14000 + (cpu_address - $4000)`.

## Confirmed stage songs

Each song consists of four separate channel streams. These are the normal stage songs, verified by entering each mode through the original game's menus in CGB mode.

| Mode | Song request in A | Four-entry descriptor block, file offset | Descriptor block, bank:CPU |
| --- | --- | --- | --- |
| CONTEST | `$64` | `$01511E–$01512D` | `05:511E–512D` |
| PUZZLE | `$76` | `$015166–$015175` | `05:5166–5175` |
| TRAINING | `$47` | `$0150AA–$0150B9` | `05:50AA–50B9` |

The stream addresses point to the four-byte setup header. Score commands start four bytes later. Ranges include the final one-byte `$FF` terminator; lengths are decimal.

| Mode | Channel | Stream start, bank:CPU | Stream file range (inclusive) | Bytes |
| --- | --- | --- | --- | ---: |
| CONTEST | Pulse 1 | `05:74C0` | `$0174C0–$0175AA` | 235 |
| CONTEST | Pulse 2 | `05:75AB` | `$0175AB–$0176C1` | 279 |
| CONTEST | Wave | `05:76C2` | `$0176C2–$017796` | 213 |
| CONTEST | Noise | `05:7797` | `$017797–$0178F5` | 351 |
| PUZZLE | Pulse 1 | `05:5176` | `$015176–$015290` | 283 |
| PUZZLE | Pulse 2 | `05:5292` | `$015292–$015480` | 495 |
| PUZZLE | Wave | `05:5482` | `$015482–$015566` | 229 |
| PUZZLE | Noise | `05:5568` | `$015568–$01571C` | 437 |
| TRAINING | Pulse 1 | `05:5FFD` | `$015FFD–$016109` | 269 |
| TRAINING | Pulse 2 | `05:610A` | `$01610A–$016214` | 267 |
| TRAINING | Wave | `05:6215` | `$016215–$01629D` | 137 |
| TRAINING | Noise | `05:629E` | `$01629E–$01639A` | 253 |

PUZZLE has one padding byte between each of its channel streams. The scanner respects command alignment: `$FF` can appear as an operand without terminating a stream.

## How the game selects and plays them

The mode startup helper at ROM0 `$1532` reads selection index `$C6B5`, indexes the byte table at ROM0 `$159C`, and calls `$1D48` with the resulting song request in A. Observed indices are CONTEST `2`, PUZZLE `4`, and TRAINING `1`. The first six table bytes are:

```text
ROM $00159C: 43 47 64 03 76 FF
```

`$1D48` invokes the single-voice initializer `$1D51` four times, incrementing A after each voice. Thus CONTEST initializes voice IDs `$64–$67`, PUZZLE `$76–$79`, and TRAINING `$47–$4A`. The music descriptor table begins at `05:4F8E` (file `$014F8E`):

```text
entry_cpu = $4F8E + voice_id * 4
entry = [WRAM slot offset, channel flags, stream pointer low, stream pointer high]
```

The four music entries use WRAM offsets `$32`, `$4B`, `$64`, `$7D` relative to `$C300`. Their flags are `$00`, `$01`, `$02`, `$03`, respectively, identifying pulse 1, pulse 2, wave, and noise. Each slot occupies `$19` bytes. Two earlier slots are also processed by the driver; the stage music occupies slots 2 through 5.

Raw descriptor bytes, in channel order:

```text
CONTEST  $01511E: 32 00 C0 74  4B 01 AB 75  64 02 C2 76  7D 03 97 77
PUZZLE   $015166: 32 00 76 51  4B 01 92 52  64 02 82 54  7D 03 68 55
TRAINING $0150AA: 32 00 FD 5F  4B 01 0A 61  64 02 15 62  7D 03 9E 62
```

Useful driver entry points in ROM0:

| CPU/file address | Purpose |
| --- | --- |
| `$1D18` | Initialize audio and channel slots |
| `$1D48` | Start four-voice music, A = first voice ID |
| `$1D51` | Start one voice, A = descriptor index |
| `$1DB5` | Audio update, processing six channel slots |
| `$1E66` | Read the stream's four-byte setup header |
| `$1F04` | Resolve the current command index against the stream base |
| `$1F1E` | Read score opcode from HL |

The score uses two-byte commands after the header, with a single-byte `$FF` terminator. For example, `$FD` saves a loop position and `$B0` branches without a finite repeat count. The terminal branch is `B0 02` for CONTEST and TRAINING (return to the first command after the header); PUZZLE uses `B0 00` (return to its saved loop position, skipping its introduction). The encoded data and the shared ROM0 driver/tables together generate the sound; copying only these stream bytes into another game would also require translating their format or porting the driver and its dependencies.

## Repeat the research

Static address inspection uses only the Python standard library:

```powershell
python scripts/bombliss_dx_music_finder.py
```

To verify through the game's normal UI using the existing PyBoy environment:

```powershell
.venv\Scripts\python.exe scripts/bombliss_dx_music_finder.py --verify
```

Alternatively, install `scripts/requirements-test.txt` into a Python environment that supports PyBoy and run the script with `--verify` there. `--rom`, `--output`, and `--frames` can override the defaults. The supplied ROM's SHA-256 is checked before inspecting it.

Verification boots from an empty save in memory, reaches mode selection, and enters each mode independently. Hooks observe the request at `$1D48`, channel setup after the descriptor is installed at `$1DA9`, and executed score reads at `$1F1E`. It checks the request's return address `$153D`, the selection index, all four installed pointers, and executed commands from all four channel streams. The default run observes 480 extra gameplay frames per mode; it does not claim to execute every command or all later stages.

Outputs go under `output/bombliss-research/`: `addresses.json` for static inspection, `verified-addresses.json` for the gameplay trace, and menu/stage screenshots for visual confirmation. The Ares save state is unnecessary for this repeatable route. No ROM, battery save, Ares state, or existing Tetris DX build is modified.
