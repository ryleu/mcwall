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

## Keep documentation in the present tense

Comments and docs describe how the code is *now* and why. They do not narrate
what it used to do — git has that. "We do X because Y would break" is useful;
"this used to do Z" is noise that rots.

`tests/test_config.py` enforces part of this: `config/example.toml` must
document every option and invent none, and every option must be read
somewhere. Three dead options were found that way.
