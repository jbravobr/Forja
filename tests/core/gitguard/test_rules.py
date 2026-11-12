"""What the allow-list lets through and what it refuses, judged from the words alone."""

import pytest

from forja.core.gitguard import GitCommandRefused, check, parse

REFUSED = [
    # remote history
    # branches and tags
    ["branch", "-D", "x"],
    ["branch", "-d", "x"],
    ["branch", "--delete", "x"],
    ["branch", "-f", "x"],
    ["branch", "-m", "a", "b"],
    ["branch", "-M", "a", "b"],
    ["branch", "--set-upstream-to=origin/x"],
    ["tag", "-d", "v1"],
    ["tag", "--delete", "v1"],
    ["tag", "-f", "v1"],
    ["checkout", "-B", "x"],
    ["switch", "-C", "x"],
    ["switch", "--force-create", "x"],
    ["worktree", "add", "-B", "x", "p"],
    ["worktree", "add", "--force", "p"],
    ["worktree", "move", "a", "b"],
    ["worktree", "lock", "a"],
    ["worktree"],
    # subcommands that are not on the list
    ["update-ref", "-d", "refs/heads/x"],
    ["update-ref", "refs/heads/x", "abc"],
    ["filter-branch"],
    ["rebase", "main"],
    ["clean", "-fd"],
    ["stash"],
    ["gc"],
    ["cherry-pick", "abc"],
    ["revert", "abc"],
    ["restore", "."],
    ["clone", "url"],
    ["submodule", "update"],
    ["daemon"],
    # configuration and plumbing that writes
    ["config", "user.name", "x"],
    ["config", "--unset", "user.name"],
    ["config", "--get", "--add", "x"],
    ["config"],
    ["symbolic-ref", "HEAD", "refs/heads/x"],
    ["symbolic-ref", "-d", "HEAD"],
    ["remote", "add", "o", "url"],
    ["remote", "remove", "o"],
    ["remote", "set-url", "o", "u"],
    ["notes", "remove"],
    ["notes", "prune"],
    ["fetch", ".", "HEAD:refs/heads/main"],
    ["fetch", "origin", "+refs/heads/x"],
    ["fetch", "--upload-pack=echo x", "origin"],
    ["fetch", "--refmap=x:y", "origin"],
    ["ls-remote", "--upload-pack=echo x", "origin"],
    ["ls-remote", "--exec=echo", "origin"],
    ["diff", "--ext-diff"],
    ["config", "--get", "--remove-section", "x"],
    ["config", "--list", "--rename-section", "a", "b"],
    ["notes", "--ref=refs/heads/main", "add", "-m", "x"],
    ["notes", "--ref", "refs/heads/main", "add", "-m", "x"],
    ["fetch", "--prune"],
    ["fetch", "-p"],
    ["log", "--output=file"],
    ["diff", "--output", "file"],
    # global options that change what git does
    ["-c", "core.hooksPath=/evil", "status"],
    ["--git-dir=/x", "status"],
    ["-C", "/other", "status"],
    [],
]

ALLOWED = [
    ["status"],
    ["status", "--porcelain"],
    ["log", "main", "--format=%H"],
    ["rev-parse", "HEAD"],
    ["show-ref", "--verify", "--quiet", "refs/heads/x"],
    ["for-each-ref", "--format=%(refname)"],
    ["ls-remote", "origin", "refs/heads/x"],
    ["merge-base", "--is-ancestor", "a", "b"],
    ["worktree", "add", "--detach", "p"],
    ["worktree", "add", "-b", "new", "p"],
    ["worktree", "remove", "--force", "p"],
    ["worktree", "prune"],
    ["worktree", "list", "--porcelain"],
    ["commit", "-m", "-f looks like a flag"],
    ["commit", "-am", "message"],
    ["commit", "--allow-empty", "-m", "m"],
    ["add", "-A"],
    ["branch", "x"],
    ["branch", "x", "main"],
    ["branch", "--list"],
    ["tag", "v1"],
    ["tag", "-a", "v1", "-m", "message"],
    ["merge", "--no-ff", "-m", "m", "x"],
    ["fetch", "origin"],
    ["fetch", "origin", "main"],
    ["config", "--get", "user.name"],
    ["config", "--list"],
    ["symbolic-ref", "--short", "refs/remotes/origin/HEAD"],
    ["symbolic-ref", "-q", "HEAD"],
    ["remote", "-v"],
    ["remote", "get-url", "origin"],
    ["notes", "add", "-m", "metrics"],
    ["notes", "show", "HEAD"],
    ["init", "-b", "main"],
    ["checkout", "-b", "x"],
    ["checkout", "x"],
    ["switch", "-c", "x"],
    ["reset", "--hard", "HEAD"],
]


@pytest.mark.parametrize("args", REFUSED, ids=lambda a: " ".join(a) or "(nothing)")
def test_what_forja_must_never_do_is_refused(args: list[str]) -> None:
    with pytest.raises(GitCommandRefused):
        check(args)


@pytest.mark.parametrize("args", ALLOWED, ids=lambda a: " ".join(a))
def test_what_forja_needs_is_allowed(args: list[str]) -> None:
    assert check(args).sub == args[0]


def test_a_message_that_looks_like_a_flag_is_not_a_flag() -> None:
    command = parse(["commit", "-m", "-f --force --amend"])

    assert command.flags == {"-m"}
    assert command.positionals == ()


def test_clusters_long_options_with_values_and_the_end_of_options_marker() -> None:
    assert parse(["commit", "-am", "msg"]).flags == {"-a", "-m"}
    assert parse(["merge", "--strategy=ours", "x"]).flags == {"--strategy"}
    assert parse(["log", "--", "-f"]).positionals == ("-f",)
    assert parse(["worktree", "add", "-b", "new", "p"]).positionals == ("add", "p")
    assert parse(["merge", "-s", "ours", "x"]).positionals == ("x",)


def test_the_reason_is_in_the_message() -> None:
    with pytest.raises(GitCommandRefused, match="subcommand 'rebase' is not allowed"):
        check(["rebase"])


@pytest.mark.parametrize(
    "args",
    [
        ["push"],
        ["push", "origin", "feature"],
        ["push", "-u", "origin", "forja-run/r1"],
        ["push", "--force", "origin", "x"],
        ["push", "origin", "main"],
    ],
)
def test_forja_never_pushes_whatever_the_branch_or_the_flags(args: list[str]) -> None:
    with pytest.raises(GitCommandRefused) as info:
        check(args)

    assert info.value.reason == "Forja never pushes; publishing is for the user"


def test_a_long_option_with_its_value_attached_does_not_take_the_next_word() -> None:
    command = parse(["merge", "--strategy=ours", "x"])

    assert command.positionals == ("x",)


def test_a_long_option_with_its_value_apart_takes_the_next_word() -> None:
    command = parse(["merge", "--strategy", "ours", "x"])

    assert command.positionals == ("x",)
    assert command.flags == {"--strategy"}
