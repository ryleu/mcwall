# Working on mcwall

**Read `README.md` first** — it describes what the system does, how the grid
is shaped, and how to run it. `docs/findings.md` has the facts every design
decision rests on, each verified against decompiled source, a shipped jar, or
a measurement on this machine. `docs/testing.md` says what is and is not
covered.

What follows is what this project has taught us, written down because each
item cost real time to learn.

## Use the devShell

`nix develop --command <thing>` has every tool already: python with the
dependencies, cage, wlr-randr, ffmpeg, cwebp, jdk, gradle, awww, mpvpaper.
Assembling `PATH` out of `nix build --print-out-paths` works but pins older
builds than the flake does, and it is easy to forget one and get a confusing
failure three layers down.

## Verify against the source, not memory

The plan this was built from flagged two tick values as remembered. They were
right, but only checking proved it. Every version-specific claim in
`docs/findings.md` cites decompiled 1.21.4, a shipped jar, or a measurement.

`./gradlew genSources` then `python3 scripts/unpack_sources.py` gives a
readable `var/mcsrc/` tree. `grep -a` it — the files carry a length-prefix byte
that makes grep treat them as binary.

## Measure before choosing, and after changing

Several plausible fixes here were simply wrong, and only numbers showed it:

- Brightening the night to fix banding: `ATM_NIGHT_I` at 1.30, 1.60 and 2.00
  produced an *identical* luma range. The option does not touch the sky dome.
- Lossless encoding to fix banding: the source only carries 18 of 255 levels
  at night, so the format was never the limit.
- "Deband then re-encode as before": lossy encoding strips the dither that
  does the hiding, and the banding returns exactly as it was.

A ten-minute experiment ruled out a five-hour re-render that would not have
helped. Prefer that trade.

## Make the test actually test the thing

Three times in a row, "the daemon is stopped" was false while testing the
no-daemon path, because `pkill -x awww-daemon` matches nothing — the process's
`comm` is `.awww-daemon-wr`, a nixpkgs wrapper. Each run "passed" and each
conclusion was wrong.

Check the precondition held, in the same breath as the test. If a test can
pass for the wrong reason, it will.

Related: `$?` after a pipeline is the *last* command's status, not the one you
care about. `cmd | head` hides the exit code of `cmd`.

## systemd, as learned the hard way

- **A oneshot owns nothing after it exits.** `KillMode=control-group` is the
  default, so anything the unit spawned dies with it. `start_new_session`
  makes a new session, not a new cgroup, and does not save it. Long-lived
  processes belong in their own unit with `Requires=` + `After=`.
- **Fail loudly instead of papering over it.** `mcwall apply` raises when the
  daemon is absent. The alternative — starting one — looked like it worked and
  quietly churned a daemon every five minutes.
- **Refuse to build rather than install something broken.** With no daemon
  unit, the home-manager module fails evaluation with a message naming both
  fixes. A timer that errors every five minutes is a worse outcome.
- **The installed package is not the checkout.** `headless.sh` resolved
  relative to the source tree, so `PYTHONPATH=. python3 -m mcwall.cli` worked
  and the built binary could never render — and the built one is what the unit
  runs. Test `nix build` output, not just the working copy.
- **It is not even this machine's checkout.** `/etc/nixos/flake.nix` pins
  `github:ryleu/mcwall` at a revision, so the unit runs whatever was last
  *pushed and locked*. A commit here changes nothing until push, `nix flake
  update mcwall` and a rebuild. Adding a manifest field broke every timer tick
  for 40 minutes this way, while the checkout passed every test.
- **A default is a claim about every machine.** The Prism launch profile was
  a constant in `config.py` holding one developer's account name. It worked
  here and would have failed everywhere else, silently picking a profile that
  does not exist. Defaults that name a person, a path or a host belong in
  detection or in the user's config, not in the source.
- **The devShell hides every runtime dependency.** `cage`, `wlr-randr`,
  `mpvpaper` and `pkill` are on `PATH` while developing, so nothing shells
  out in vain — and none of them were in the package's closure. A render
  started from the installed binary died with `FileNotFoundError: 'cage'`
  from inside `Popen`, after printing "1626 jobs queued". The wrapper now
  carries the tools mcwall owns; `prismlauncher` stays the user's, because
  shadowing their authenticated launcher is the one thing this project
  promises not to do. `missing_render_tools` names whatever is absent before
  the game starts.
- **`sys.executable` is not your interpreter.** Installed by nix, `mcwall` is
  a bash wrapper and `sys.executable` underneath it is the bare python, which
  cannot import mcwall at all. The TUI spawned `sys.executable -m mcwall.cli`
  and every command died with ModuleNotFoundError — from inside a process
  that had imported the package to get there. A child gets `sys.path` handed
  to it explicitly now. It worked perfectly in the devShell, where the two
  interpreters happen to agree.
- **An untracked file is not in the flake either.** `nix build` on a git
  repository takes tracked files only, so a brand-new module passes every
  test in the devShell and is simply absent from the built package —
  `mcwall tui` was "invalid choice" from the installed binary while working
  perfectly from the checkout. `git add` before trusting a build.
- **The library format has two readers of different ages.** The producer
  writes it from the checkout; the display path reads it from the pinned
  build. `Manifest.read` therefore drops fields it does not recognise and
  warns, because provenance a reader does not need must never take 1,728
  images offline.
- **Check home-manager's modules before adding options.** There is one daemon
  module, `services.awww`; `services.swww.*` are `mkRenamedOptionModule`
  aliases onto it. Detecting "both" would have been detecting one thing twice.
  Evaluate real `homeManagerConfiguration`s to test a module — a hand-rolled
  `evalModules` harness gave a wrong answer about `mkIf` here.

## Minecraft and Prism

- **Commands from the server's own source stack have no `@s`.**
  `performPrefixedCommand` returns void, so the failure is invisible. Use the
  API (`ServerPlayer.teleportTo`, `setGameMode`) for anything entity-shaped.
- **Prism exits seconds after handing off to the JVM.** The launcher's
  lifetime says nothing about the batch. Wait for the mod's own `DONE` file.
- **An orphaned client is worse than a dead one.** It holds the world lock and
  keeps rendering the *previous* manifest, so the next run silently produces
  the wrong images. Check for one before starting; reap it afterwards.
- **Frame counts are not readiness.** Chunk generation takes as long as it
  takes. Gate on the actual signals.

## Gate dangerous behaviour on an explicit signal

The capture mod used to start a batch whenever it found a job manifest. A
finished render leaves one behind, so opening the instance by hand replayed
the last batch and quit the game out from under whoever was setting up a shot.

It now runs only when the orchestrator sets `MCWALL_CAPTURE=1`. The difference
is that the safe case is the default and the dangerous one has to be asked
for, rather than inferred from a file that happens to exist.

Check the signal actually arrives: the variable has to survive
cage -> headless.sh -> prismlauncher -> JVM, and that was worth proving in
both directions — a render with it set, and a launch without it that leaves
the manifest untouched.

## Nix drives the mod build; it does not sandbox it

`mcwall setup` builds the capture mod when it is missing, and `nix run
.#build-mod` does it alone. Neither is a pure derivation, and that is not an
oversight: Fabric Loom fetches the game, its mappings and the Fabric libraries
during the build and decompiles them, which a sandbox forbids. Making it pure
would mean a fixed-output derivation over the whole Gradle and Loom cache.

Loom also will not resolve on a JVM below 25, while the mod compiles to 21
because that is what the game runs. The devShell and the flake app both export
`MCWALL_LOOM_JDK`, and `options.release = 21` bridges the two.

## Don't delete the source until the output is approved

The first library's PNGs were converted and deleted, and the lossy result
turned out to be unacceptable. With the PNGs gone, the only options were a
reconstruction or a five-hour re-render.

Hence: the deband pass builds a new tree, checks the count, swaps, and keeps
the original alongside until it is deleted by hand. Do the same for anything
irreversible.

## Watch the battery before starting something long

A 12-core deband pass burned 18% of the battery in ten minutes and would not
have finished. Check `/sys/class/power_supply/AC/online` before launching
anything expensive, and make long jobs resumable so stopping costs nothing.

## Configuration changes behaviour, not output

The camera, the world and the seed are read from the world save; the config
file only changes what mcwall does at runtime. Anything that changes what the
pictures look like — shaderpack and its settings, seed, camera, resolution,
render distance — is hashed into the library's directory name instead.

Two things follow, and both were worth the change. Builds no longer overwrite
each other, so trying a second camera does not cost the first library. And
there is no configuration to edit before a render, so a config file can never
disagree with the images sitting next to it.

Keep the split honest: a setting that only affects *how* a frame is reached
(warm-up frames, stability ticks, parallelism) must not fork the library, and
`tests/test_producer.py` asserts exactly that.

**A matching settings hash is not a matching library.** The first migration
adopted the existing library on a shaderpack-hash match, because that was all
the old manifest recorded. It was a forest shot taken at a biome-anchored
camera (`located.json`: x=-192 z=192, facing west); the config now pointed at
a village 230 blocks away facing east. The hash matched and everything else
differed, so 1,728 images ended up filed under a name describing a place they
were never taken. Recover what you can prove, record it in the manifest, and
name the rest so nobody trusts it twice.

**Look at the picture.** Two libraries whose provenance is ambiguous are
distinguishable in one glance and in no other cheap way. A single `--limit 1`
render settled both "is this the right shot" and "does capture still work"
for about ten minutes of wall clock, before a 5.5-hour commitment.

**Promote, do not announce.** The root manifest is a claim that a build is
ready to display, so it is written last. Writing it when the render *starts*
pointed the wallpaper at an empty directory for five hours — the screen froze
and the timer failed every tick. A build keeps its own manifest beside its
images while it fills; only a complete one replaces the root.

**Rename the manifest with the directory.** The manifest names the build it
describes, so moving a library without rewriting it leaves the display path
unable to find a single image. The first version of the migration renamed only
the directory, and a `--dry-run` — which deliberately writes nothing — was
enough to take 1,728 images offline until the next full render.

## Ask what the number is supposed to mean

`current.weather_code` was reported as overcast while it was raining outside,
and the airport's own observation agreed. Nothing was broken: showers come in
bursts and the sample landed in a dry gap. The code was a faithful answer to
a question nobody was asking.

"Is it raining *right this instant*" and "is it raining" are different
questions, and the wallpaper wants the second. Weather is now the worst sky in
the last 30 minutes of the radar nowcast.

Worth checking a source against ground truth before concluding the code is
wrong — the station observation is what proved the model was honest.

## A dry run writes nothing — check that every time

Twice now, `--dry-run` has taken the live library offline: once by renaming a
directory without its manifest, once by stamping the manifest for a build with
no images in it. Both times the planning code did the write, and the dry-run
check sat below it.

Put the early return above the first write, and pin it with a test that fails
when the order is restored — `tests/test_producer.py` has one.

## Preflight what the game needs, before launching the game

A render with no capture mod installed boots, renders a title screen, captures
nothing, and fails only when the watchdog gives up — minutes, with the cause
buried in `latest.log` and nothing in the mod list to notice unless you look
for an absence. `run_jobs` now refuses up front if no `mcwall-capture-*.jar`
is in the instance.

## Two ways of being wrong are rarely equal

Folding weather onto three skies loses information whatever you do, so the
question is which error is cheaper. Sunshine on a grey afternoon looks broken;
rain a little early under a solid overcast just looks like weather. Overcast
and fog are therefore rain, and only WMO 0-2 stay clear.

Worth asking before every lossy mapping: not "which is more accurate" but
"which mistake will someone notice".

## A terminal UI can still be tested

Split it: `state` gathers facts, `layout` turns them into lines, `app` is the
only part that touches curses. The first two are ordinary functions, so the
screen can be asserted against without a terminal.

That leaves exactly one gap — curses itself blowing up on draw — and a pty
smoke test closes it. Prove that test works by breaking the draw on purpose
and watching it fail; a UI that crashes on the first frame otherwise passes
every layout assertion there is.

Read the command list out of argparse rather than restating it. The TUI then
gains a command the moment the CLI does, and `tests/test_tui.py` asserts the
two sets are equal.

## A command you never run is a claim you cannot check

Seven of fifteen subcommands went. The telling evidence was not opinion but
absence: `probe` had never written the `slots.toml` it exists to produce,
`scout` had been superseded by flying to the shot in-game, and `deband` was a
one-time migration already done. `plan` duplicated `render --dry-run`, and
`init` was one command for one setting, folded into `setup`.

Look for the artefact a command is supposed to leave behind. If it was never
created, nobody has used the command, and nothing has been testing it either.

## Generate the second description, never write it

A shell completion is a second description of the command surface, and a
second description drifts. `mcwall --completion {bash,zsh}` renders it from
`build_parser()`, the flake installs the output, and `tests/test_completion.py`
drives the result in a real shell rather than trusting the text.

Three traps, all found by running it rather than reading it: a case pattern
containing a space (`world list)`) is a syntax error in both shells; nixpkgs'
plain `bash` is built without `compgen`, so every reply comes back empty and
a completion test "passes" while completing nothing; and `compgen -W` splits
its word list on spaces, so a world called `New World` completes as two
suggestions that do not exist. Names are read line by line and quoted with
`printf %q`. The devShell and the check sandbox both carry `bashInteractive`.

Arguments matter as much as commands: completing only flags is the useless
half. Dynamic values come from `mcwall --complete {builds,worlds}`, which is
hidden, quiet on failure, and the same code the commands themselves use.

## Keep documentation in the present tense

Comments and docs describe how the code is *now* and why. They do not narrate
what it used to do — git has that. "We do X because Y would break" is useful;
"this used to do Z" is noise that rots.

`tests/test_config.py` enforces part of this: `config/example.toml` must
document every option and invent none, and every option must be read
somewhere. Three dead options were found that way.
