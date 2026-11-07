"""Shell completion, which is generated rather than written.

A completion script is a second description of the command surface, so the
thing worth testing is that it still matches the first one.
"""

import re
import shlex
import shutil
import subprocess

import pytest

from mcwall.cli import build_parser
from mcwall.completion import render
from mcwall.tui.state import command_specs


@pytest.mark.parametrize("shell", ["bash", "zsh"])
def test_every_command_is_offered(shell):
    script = render(build_parser(), shell)
    for spec in command_specs(build_parser()):
        last = spec.name.split()[-1]
        assert last in script, f"{spec.name} is missing from the {shell} completion"


@pytest.mark.parametrize("shell", ["bash", "zsh"])
def test_every_flag_is_offered(shell):
    script = render(build_parser(), shell)
    apply_spec = next(
        s for s in command_specs(build_parser()) if s.name == "library apply"
    )
    for option in apply_spec.options:
        if option.positional:
            continue
        assert option.flags[-1] in script, option.flags


@pytest.mark.parametrize("shell", ["bash", "zsh"])
def test_the_generated_script_parses(tmp_path, shell):
    """A completion with a syntax error is silently dead in the shell."""
    binary = shutil.which(shell)
    if binary is None:
        pytest.skip(f"{shell} is not installed")

    path = tmp_path / ("mcwall" if shell == "bash" else "_mcwall")
    path.write_text(render(build_parser(), shell))
    done = subprocess.run([binary, "-n", str(path)], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr


def test_a_command_path_with_a_space_is_quoted():
    """`world list` as a bare case pattern is a syntax error in both shells."""
    for shell in ("bash", "zsh"):
        script = render(build_parser(), shell)
        assert '"world list")' in script, shell
        assert "\n        world list)" not in script, shell


def capable_bash():
    """A bash with `compgen`.

    nixpkgs' plain `bash` is built without programmable completion, so the
    builtins a completion script needs are simply absent and every reply
    comes back empty. `bashInteractive` has them.
    """
    for name in ("bash", "bashInteractive"):
        binary = shutil.which(name)
        if binary is None:
            continue
        probe = subprocess.run(
            [binary, "-c", 'compgen -W "a b" -- ""'], capture_output=True, text=True
        )
        if probe.returncode == 0 and probe.stdout.split():
            return binary
    return None


def test_bash_completion_actually_completes(tmp_path):
    """Drive it the way the shell does, rather than trusting the text."""
    binary = capable_bash()
    if binary is None:
        pytest.skip("no bash with compgen available")

    script = tmp_path / "mcwall.bash"
    script.write_text(render(build_parser(), "bash"))
    program = f"""
    source {script}
    COMP_WORDS=(mcwall world "")
    COMP_CWORD=2
    _mcwall
    echo "${{COMPREPLY[*]}}"
    """
    done = subprocess.run([binary, "-c", program], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    offered = set(done.stdout.split())
    assert {"edit", "list", "render"} <= offered
    # A group carries its own options too. `mcwall --completion` lives on the
    # root, which is nothing but a group, so a group that offers only its
    # subcommands can never offer it.
    assert {"-h", "--help"} <= offered, offered


def test_an_unknown_shell_says_which_are_known():
    with pytest.raises(SystemExit, match="bash"):
        render(build_parser(), "fish")


def stub_mcwall(directory, names):
    """A fake `mcwall --complete` on PATH, so the test owns the data."""
    binary = directory / "mcwall"
    listing = "\n".join(names)
    binary.write_text(f'#!/bin/sh\nprintf \'%s\\n\' "$@" >/dev/null\ncat <<\'EOF\'\n{listing}\nEOF\n')
    binary.chmod(0o755)
    return binary


@pytest.mark.parametrize(
    "command, expected",
    [("world edit", {"Alpha\\ One", "Beta"}), ("library use", {"Alpha\\ One", "Beta"})],
)
def test_positionals_complete_to_names(tmp_path, command, expected):
    """`library use <TAB>` offered only flags, which is the useless half.

    A name containing a space is the case that breaks naive completion:
    `compgen -W` splits it into two suggestions, neither of which exists.
    """
    binary = capable_bash()
    if binary is None:
        pytest.skip("no bash with compgen available")

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub_mcwall(bin_dir, ["Alpha One", "Beta"])

    script = tmp_path / "mcwall.bash"
    script.write_text(render(build_parser(), "bash"))
    words = " ".join(f'"{w}"' for w in ["mcwall"] + command.split() + [""])
    program = f"""
    export PATH="{bin_dir}:$PATH"
    source {script}
    COMP_WORDS=({words})
    COMP_CWORD={len(command.split()) + 1}
    _mcwall
    printf '%s\\n' "${{COMPREPLY[@]}}"
    """
    done = subprocess.run([binary, "-c", program], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    offered = set(done.stdout.split("\n"))
    assert expected <= offered, f"{command}: got {sorted(offered)}"


def test_a_path_positional_also_offers_directories(tmp_path):
    """A library can live anywhere, so the shell has to help you walk there."""
    binary = capable_bash()
    if binary is None:
        pytest.skip("no bash with compgen available")

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub_mcwall(bin_dir, [])
    (tmp_path / "somewhere").mkdir()

    script = tmp_path / "mcwall.bash"
    script.write_text(render(build_parser(), "bash"))
    program = f"""
    export PATH="{bin_dir}:$PATH"
    cd {tmp_path}
    source {script}
    COMP_WORDS=("mcwall" "library" "use" "some")
    COMP_CWORD=3
    _mcwall
    printf '%s\\n' "${{COMPREPLY[@]}}"
    """
    done = subprocess.run([binary, "-c", program], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    assert "somewhere" in done.stdout


@pytest.mark.parametrize("shell", ["bash", "zsh"])
def test_positional_commands_ask_mcwall_for_names(shell):
    script = render(build_parser(), shell)
    assert "--complete worlds" in script
    assert "--complete builds" in script
    # Quietly: a prompt is no place for a traceback.
    assert script.count("2>/dev/null") >= 2


# zsh ties a handful of array parameters to scalars: `path` to PATH, `fpath`
# to FPATH, and so on. A local of that name inside a completion function
# empties the real variable for the rest of the call.
ZSH_TIED_ARRAYS = (
    "path", "fpath", "cdpath", "manpath", "module_path", "mailpath", "psvar",
)


def test_zsh_never_shadows_a_tied_array():
    """Naming a local `path` in zsh empties PATH for the whole function.

    Commands and flags still complete, because those are literals in the
    script. Only the branches that shell out to `mcwall --complete ...` go
    quiet, so the failure looks like "names don't complete" and nothing else.
    """
    script = render(build_parser(), "zsh")
    for name in ZSH_TIED_ARRAYS:
        # Word-bounded: `_cmdpath+=(` is the fix, not another instance of it.
        for pattern in (rf"local (-a )?{name}\b", rf"(?<![\w]){name}\+?="):
            found = re.search(pattern, script)
            assert found is None, f"{name}: {found.group(0)!r}"


def zsh_binary():
    for name in ("zsh",):
        found = shutil.which(name)
        if found:
            return found
    return None


def zsh_candidates(tmp_path, line, names):
    """Everything a real zsh offers for `line`, via a pty.

    zsh cannot be asked "what would you complete here" without a terminal, so
    the completion is driven at one and `compadd` is wrapped to record what it
    is handed. Reading the screen instead would measure TERM and menu styles.
    """
    binary = zsh_binary()
    if binary is None:
        pytest.skip("no zsh available")

    comp = tmp_path / "comp"
    comp.mkdir(exist_ok=True)
    (comp / "_mcwall").write_text(render(build_parser(), "zsh"))

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    stub_mcwall(bin_dir, names)

    log = tmp_path / "offered.log"
    hook = tmp_path / "hook.zsh"
    hook.write_text(
        "compadd() {\n"
        '  local -a av=("$@")\n'
        "  integer i\n"
        "  for ((i = 1; i <= $#av; i++)); do\n"
        "    if [[ $av[i] == -a ]]; then\n"
        "      local nm=$av[i+1]\n"
        '      print -rl -- ${(P)nm} >> $MCW_LOG\n'
        "      (( i++ ))\n"
        "    fi\n"
        "  done\n"
        '  builtin compadd "$@"\n'
        "}\n"
    )

    driver = tmp_path / "drive.zsh"
    driver.write_text(
        "zmodload zsh/zpty\n"
        "export TERM=xterm\n"
        f'export MCW_LOG={log}\n'
        f'export PATH={bin_dir}:$PATH\n'
        'zpty ZS "PS1=R%% zsh -f -i"\n'
        f'zpty -w ZS "fpath=({comp} \\$fpath)"\n'
        f'zpty -w ZS "autoload -Uz compinit && compinit -u -d {tmp_path}/zcompdump"\n'
        f'zpty -w ZS "source {hook}"\n'
        'zpty -w ZS "setopt NO_BEEP"\n'
        "sleep 1\n"
        "local junk; while zpty -r -t ZS junk 2>/dev/null; do :; done\n"
        f'zpty -w -n ZS {shlex.quote(line)}\n'
        "sleep 0.3\n"
        "zpty -w -n ZS $'\\t'\n"
        "sleep 2.5\n"
        "zpty -d ZS 2>/dev/null\n"
    )

    done = subprocess.run([binary, str(driver)], capture_output=True, text=True, timeout=90)
    assert done.returncode == 0, done.stderr
    if not log.exists():
        return set()
    return {entry for entry in log.read_text().split("\n") if entry}


def test_zsh_completes_names_not_just_flags(tmp_path):
    """The bug this exists for: `mcwall world edit <TAB>` offered no worlds.

    Every bash test passed throughout, because `path` is an ordinary name in
    bash. Checking the zsh script parses and contains the right words was not
    enough either — the script was correct to read and silent to run.
    """
    offered = zsh_candidates(tmp_path, "mcwall world edit ", ["Alpha One", "Beta"])
    assert {"Alpha One", "Beta"} <= offered, sorted(offered)


def test_zsh_completes_an_option_value_from_its_choices(tmp_path):
    offered = zsh_candidates(tmp_path, "mcwall --completion ", ["ignored"])
    assert {"bash", "zsh"} <= offered, sorted(offered)


def test_zsh_survives_an_option_that_takes_a_value(tmp_path):
    """`--limit 5` used to leave the command path as "world render 5".

    Only flags are dropped when the path is read, so the *value* stayed and
    matched no branch, which killed completion for the rest of the line.
    """
    offered = zsh_candidates(
        tmp_path, "mcwall world render --limit 5 ", ["Alpha One", "Beta"]
    )
    assert {"Alpha One", "Beta"} <= offered, sorted(offered)


@pytest.mark.parametrize("shell", ["bash", "zsh"])
def test_a_group_offers_its_own_flags(shell):
    """`mcwall --<TAB>` used to offer nothing in either shell.

    The root is a group and nothing else, so a generator that gives groups
    only their subcommands can never reach `--completion` — the flag that
    produces this very script.
    """
    from mcwall.completion import _group_flags

    script = render(build_parser(), shell)
    for prefix, flags in _group_flags(build_parser()).items():
        for flag in flags:
            assert flag in script, f"{' '.join(prefix) or '(root)'}: {flag}"


@pytest.mark.parametrize("shell", ["bash", "zsh"])
def test_a_flag_with_choices_offers_them(shell):
    """argparse already knows these, so neither shell should restate them."""
    from mcwall.completion import _choice_flags

    script = render(build_parser(), shell)
    for flag, values in _choice_flags(build_parser()).items():
        assert flag in script, flag
        for value in values:
            assert value in script, f"{flag}={value}"


def bash_candidates(tmp_path, words, names):
    """What bash offers for a command line, given a stubbed `mcwall`."""
    binary = capable_bash()
    if binary is None:
        pytest.skip("no bash with compgen available")

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    stub_mcwall(bin_dir, names)
    script = tmp_path / "mcwall.bash"
    script.write_text(render(build_parser(), "bash"))

    quoted = " ".join(f'"{w}"' for w in words)
    program = f"""
    export PATH="{bin_dir}:$PATH"
    source {script}
    COMP_WORDS=({quoted})
    COMP_CWORD={len(words) - 1}
    _mcwall
    printf '%s\\n' "${{COMPREPLY[@]}}"
    """
    done = subprocess.run([binary, "-c", program], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    return {entry for entry in done.stdout.split("\n") if entry}


def test_bash_completes_an_option_value_from_its_choices(tmp_path):
    offered = bash_candidates(tmp_path, ["mcwall", "--completion", ""], ["ignored"])
    assert {"bash", "zsh"} <= offered, sorted(offered)


def test_bash_survives_an_option_that_takes_a_value(tmp_path):
    """The same path-reading bug as zsh, from the same cause."""
    offered = bash_candidates(
        tmp_path, ["mcwall", "world", "render", "--limit", "5", ""], ["Alpha One"]
    )
    assert "Alpha\\ One" in offered, sorted(offered)
