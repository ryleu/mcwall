"""Shell completion, which is generated rather than written.

A completion script is a second description of the command surface, so the
thing worth testing is that it still matches the first one.
"""

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
    assert set(done.stdout.split()) == {"edit", "list", "render"}


def test_an_unknown_shell_says_which_are_known():
    with pytest.raises(SystemExit, match="bash"):
        render(build_parser(), "fish")
