# AGENTS.md

Guide for coding agents working on this repo: a Windows bot for the barista job in CDID (Roblox). It plays by reading the screen (EasyOCR + OpenCV) and sending hardware-style keyboard and mouse input. Nothing reads game memory.

## Read first
- `HANDOFF.md` (gitignored, local only): current state, the last runs, and what to do next. If it exists, it overrides anything stale here.
- `GAME_NOTES.md` (gitignored): observed game behaviour, including panel texts in English and Indonesian, recipes, station names and vision constants. Check it before re-deriving anything from frames.
- `plan.md` (gitignored): the map / navigation plan.

## Layout
| File | What it does |
|---|---|
| `main.py` | `Bot`: reads the BARISTA panel step, navigates to the station, uses the chip; screen capture (`Screen`), input (`send_key`, `click`), chip / label / prompt finders |
| `coffee.py` | the order dialogue (`take_order`), recipes / routes, the cup and flavour pickers, the EKSTRAKSI shot minigame |
| `nav.py` | the map: label observations, `Map.localize` (pose from one frame), `Navigator` (roadmap, learned standing spots, hop length, outside-the-kitchen test) |
| `head.py` | the player's head on screen; its width gives the camera distance |
| `ocr.py` | EasyOCR wrappers and fuzzy matching; recognition runs on single-line crops only (the detector is too slow on CPU) |
| `overlay.py` | the status card, docked beside Roblox and hidden from screen capture |
| `tools/mapper.py` | building the map: `tour` (live), `solve`, `show`, `selftest` |
| `keepalive.py` | anti-idle clicker (gitignored) |
| `config.py` | every key, region and tuning constant |
| `map.json` | the café map (committed). `learned.json` (gitignored) holds the roadmap and standing spots the bot learns while playing |

## Running
- The venv is `.venv` (Python 3.12). Use `.venv\Scripts\python.exe`, never the system Python.
- Normal use: `run.bat`, or `.venv\Scripts\python.exe main.py`. F6 pauses, F7 quits.
- Live debugging (from `debug/live/`): `..\..\.venv\Scripts\python.exe -W ignore -u run_bot.py SECONDS TAG ["Drink[,Syrup]"]`. It saves frames to `debug/live/TAG_###.png` at about 0.7 fps. Filter the log with `-notmatch '^\[label\] rejected|^\[chip\]|^\[coffee\] needle'`.
- `debug/live/watch.py SECONDS TAG` records a human run (frames and game inputs, sends nothing).
- Map: `.venv\Scripts\python.exe tools\mapper.py tour`, then `solve debug\map\obs.json OUT.json`, then `show OUT.json debug\map.png`. A solve takes about 10 min.
- `debug/` is gitignored scratch space for frames, logs and experiment maps.

## Live-run rules
- A live run takes over the user's keyboard and mouse on the Roblox window (second monitor). Only start one when the user says go, and check the game first with `debug/live/live.py shot NAME` (a capture only, no input). If another window covers Roblox, the capture shows that window instead: don't run, and delete the capture.
- The bot pauses whenever Roblox loses focus.
- To stop a run: kill the `python.exe` whose command line contains `run_bot`, then release every key with `.venv\Scripts\python.exe -c "import main; [main.send_key(k, False) for k in main.SCANCODES]"`.
- **Never send E or a click without a verified target.** E fires the nearest prompt, and using the wrong station ruins the drink. The bot always clicks a chip it has matched (E and click are interchangeable in-game).
- A customer can't be asked twice. An order that isn't caught becomes `config.DEFAULT_DRINK` (Black Coffee).
- The user resets the character's position by hand when the bot gets lost; ask them.

## Checking changes offline
- Replay saved frames through the function you changed before any live run (for example `coffee.dialogue_text`, `main.find_chips`, `main.highlighted_chip`, `nav.Map.localize`). Compare detections on a fixed frame set before and after a vision change, to catch regressions.
- `tools/mapper.py selftest` checks the map solver and `localize` on a synthetic café.
- There is no unit-test suite. `python -m py_compile` on the changed files is the minimum.

## Conventions
- Commits: `feat:` / `fix:` / `chore:` subject, a body that says why. **No Co-Authored-By or other attribution trailers.** Commit and push per working milestone on the current branch (`map-navigation`).
- Code comments are one tight line, not explanatory blocks. Match the surrounding style.
- Don't commit `HANDOFF.md`, `GAME_NOTES.md`, `plan.md`, `learned.json`, `keepalive.py` or `debug/`.
- Tuning numbers belong in `config.py`, with a short comment on where they came from.
