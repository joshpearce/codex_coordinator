"""The boundary a profile id means, and the configurations refused outright.

Every refusal here covers a configuration Codex CLI 0.154.0 accepts and
under-enforces. The runtime reports no error for any of them, so a coordinator
that did not check would run workers under a ceiling its operator believes
exists and the sandbox does not apply (#0021).
"""

from pathlib import Path

import pytest

from codex_coordinator.profiles import (
    CodexHome,
    PermissionProfileError,
    builtin_profile,
    resolve_profile,
    verify_home,
)


WORKER = (
    "[permissions.worker]\n"
    'extends = ":workspace"\n'
    'filesystem = { ":tmpdir" = "read", ":slash_tmp" = "read" }\n'
)


def home(root: Path, body: str, *, default: str = 'default_permissions = ":read-only"\n') -> CodexHome:
    directory = root / "codex-home"
    directory.mkdir(exist_ok=True)
    (directory / "config.toml").write_text(default + "\n" + body)
    return CodexHome.load(directory)


def test_a_profile_resolves_to_the_boundary_and_the_chain_that_produced_it(tmp_path: Path):
    resolved = home(tmp_path, WORKER).resolve("worker")

    assert resolved.chain == ("worker", ":workspace")
    assert resolved.builtin == ":workspace"
    assert resolved.writable
    assert not resolved.grants_network
    # A profile id alone says nothing: two projects naming different ids can
    # extend the same built-in, and editing a parent changes both.
    assert resolved.provenance()["chain"] == ["worker", ":workspace"]
    assert resolved.provenance()["source"].endswith("config.toml")


def test_a_chain_layers_so_the_nearest_declaration_wins(tmp_path: Path):
    """Probed against the pinned CLI in both directions.

    A child's "read" overrides a parent's "deny" and a child's "deny" overrides
    a parent's "read"; an entry the child does not declare is inherited. This
    has to match, because what is resolved here is what decides whether the
    coordinator treats a project as writable.
    """
    resolved = home(
        tmp_path,
        "[permissions.base]\n"
        'extends = ":workspace"\n'
        'filesystem = { ":tmpdir" = "read", ":slash_tmp" = "deny" }\n'
        "\n"
        "[permissions.base.network]\n"
        "enabled = false\n"
        "\n"
        "[permissions.worker]\n"
        'extends = "base"\n'
        'filesystem = { ":slash_tmp" = "read" }\n',
    ).resolve("worker")

    assert resolved.chain == ("worker", "base", ":workspace")
    assert resolved.filesystem == {":tmpdir": "read", ":slash_tmp": "read"}
    assert resolved.network == {"enabled": False}


def test_a_built_in_needs_no_home_and_means_the_same_everywhere(tmp_path: Path):
    """A deployment that only selects :read-only needs no Codex home at all.

    That matters because the alternative is defaulting to the developer's own
    ~/.codex, which is the isolation half of this issue.
    """
    assert resolve_profile(":read-only", None).builtin == ":read-only"
    assert not resolve_profile(":read-only", None).writable
    assert resolve_profile(":read-only", home(tmp_path, WORKER)).chain == (":read-only",)

    with pytest.raises(PermissionProfileError, match="no codex_home is configured"):
        resolve_profile("worker", None)


def test_an_id_naming_nothing_is_refused_with_what_the_home_does_define(tmp_path: Path):
    with pytest.raises(PermissionProfileError, match=r"not defined in .*Profiles defined there: \['worker'\]"):
        home(tmp_path, WORKER).resolve("typo")


def test_a_writable_profile_that_leaves_the_temporary_roots_writable_is_refused(tmp_path: Path):
    """:workspace is wider than the sandbox literal this project used to send.

    The literal set excludeTmpdirEnvVar and excludeSlashTmp; :workspace leaves
    both writable. A profile that omits the demotions is not a re-spelling of
    the old boundary but a widening of it, so it fails startup.
    """
    for body in (
        '[permissions.worker]\nextends = ":workspace"\n',
        # Naming the real path instead of the token does not demote it: probed
        # against the pinned CLI, /tmp stays writable.
        '[permissions.worker]\nextends = ":workspace"\n'
        'filesystem = { ":tmpdir" = "read", "/tmp" = "read" }\n',
    ):
        with pytest.raises(PermissionProfileError, match="without demoting"):
            home(tmp_path, body).resolve("worker")

    # A read-only chain has no write grant to demote in the first place.
    assert home(
        tmp_path, '[permissions.reader]\nextends = ":read-only"\n',
    ).resolve("reader").writable is False
    # "deny" is accepted too: it is stricter, not wider.
    assert home(
        tmp_path,
        '[permissions.worker]\nextends = ":workspace"\n'
        'filesystem = { ":tmpdir" = "deny", ":slash_tmp" = "deny" }\n',
    ).resolve("worker").writable


def test_a_network_ceiling_the_runtime_would_not_enforce_is_refused(tmp_path: Path):
    """A ceiling that is written but not enforced is an error, not a default.

    With network_proxy off, a one-host `domains` map reaches every host,
    reaches unix sockets no grant names, and leaves loopback open, while
    reading as though a ceiling exists.

    This is a property of the home rather than of a selection. A home carrying
    an inert ceiling is misconfigured whichever profile a project selects
    today, and an operator reading that file believes in a boundary that is not
    there — so it is refused before any id is resolved.
    """
    ceiling = (
        WORKER
        + "\n[permissions.worker.network]\n"
        "enabled = true\n"
        'mode = "limited"\n'
        'domains = { "example.com" = "allow" }\n'
    )
    with pytest.raises(PermissionProfileError, match=r"declare network \['domains'\].*network_proxy"):
        verify_home(home(tmp_path, ceiling))

    # Named even when nothing selects it: the refusal lists the profiles.
    unselected = home(
        tmp_path,
        WORKER + '\n[permissions.other]\nextends = ":read-only"\n'
        '\n[permissions.other.network]\nenabled = true\n'
        'unix_sockets = { "/tmp/x.sock" = "allow" }\n',
    )
    with pytest.raises(PermissionProfileError, match=r"\['other'\].*unix_sockets"):
        verify_home(unselected)

    enabled = home(
        tmp_path, ceiling,
        default='default_permissions = ":read-only"\n[features]\nnetwork_proxy = true\n',
    )
    assert verify_home(enabled) is enabled
    resolved = enabled.resolve("worker")
    assert resolved.grants_network
    assert resolved.network["domains"] == {"example.com": "allow"}

    # Network declared without any proxy-dependent grant claims no ceiling, so
    # there is nothing to be inert.
    assert verify_home(home(
        tmp_path, WORKER + "\n[permissions.worker.network]\nenabled = false\n",
    )).network_proxy is False


def test_keys_the_runtime_accepts_and_ignores_are_refused(tmp_path: Path):
    """The runtime's own loader is silent about these, which is the trap.

    An unrecognized *value* fails the load in the runtime, so the valid access
    set is self-enforcing. The keys are the unguarded half: probed against the
    pinned CLI, `:tmp`, `:temp` and `:system_tmp` all parse and leave both
    temporary roots writable, where `:slash_tmp` and `:tmpdir` demote them.
    """
    for body, message in (
        (
            '[permissions.worker]\nextends = ":workspace"\nwritable_roots = ["/"]\n',
            r"unsupported keys \['writable_roots'\]",
        ),
        (
            '[permissions.worker]\nextends = ":workspace"\n'
            'filesystem = { ":tmp" = "read" }\n',
            "unknown filesystem token ':tmp'",
        ),
        (
            '[permissions.worker]\nextends = ":workspace"\n'
            'filesystem = { "relative/path" = "read" }\n',
            "neither a filesystem token nor an absolute path",
        ),
        (
            '[permissions.worker]\nextends = ":workspace"\n'
            'filesystem = { ":tmpdir" = "readonly" }\n',
            "supported: \\['deny', 'none', 'read', 'write'\\]",
        ),
        (
            WORKER + "\n[permissions.worker.network]\nenabled = true\nlocal_binding = true\n",
            r"unsupported network keys \['local_binding'\]",
        ),
        (
            WORKER + '\n[permissions.worker.network]\nmode = "partial"\n',
            "network mode 'partial'",
        ),
    ):
        with pytest.raises(PermissionProfileError, match=message):
            home(tmp_path, body).resolve("worker")


def test_a_profile_that_reaches_no_built_in_is_refused(tmp_path: Path):
    for body, message in (
        ('[permissions.worker]\nfilesystem = {}\n', "declares no `extends`"),
        ('[permissions.worker]\nextends = "absent"\n', "neither defined here nor a built-in"),
        (
            '[permissions.worker]\nextends = "other"\n\n'
            '[permissions.other]\nextends = "worker"\n',
            "extends itself",
        ),
    ):
        with pytest.raises(PermissionProfileError, match=message):
            home(tmp_path, body).resolve("worker")


def test_a_boundary_that_enforces_nothing_is_never_selected(tmp_path: Path):
    for body in (
        '[permissions.worker]\nextends = ":danger-full-access"\n',
        '[permissions.worker]\nextends = ":danger-full-access"\n'
        'filesystem = { ":tmpdir" = "deny", ":slash_tmp" = "deny" }\n',
    ):
        with pytest.raises(PermissionProfileError, match="danger-full-access"):
            home(tmp_path, body).resolve("worker")
    with pytest.raises(PermissionProfileError, match="danger-full-access"):
        resolve_profile(":danger-full-access", None)
    # It is still reported as a built-in the runtime offers; this project just
    # does not hand one to a worker.
    assert builtin_profile(":danger-full-access").writable


def test_a_home_with_profiles_and_no_pinned_default_is_refused(tmp_path: Path):
    """The implicit default follows project trust records rather than the file.

    Probed: with no `default_permissions`, an unconfigured thread resolved to
    :read-only before a trust record existed for its cwd and to :workspace
    after. The runtime also refuses to load such a home, with a message that
    names neither the file nor the reason.
    """
    directory = tmp_path / "codex-home"
    directory.mkdir()
    (directory / "config.toml").write_text(WORKER)

    with pytest.raises(PermissionProfileError, match="default_permissions must be set"):
        CodexHome.load(directory)


def test_a_home_that_is_not_there_is_reported_as_configuration(tmp_path: Path):
    with pytest.raises(PermissionProfileError, match="codex home is not a directory"):
        CodexHome.load(tmp_path / "absent")
    (tmp_path / "empty").mkdir()
    with pytest.raises(PermissionProfileError, match="has no config.toml"):
        CodexHome.load(tmp_path / "empty")
    with pytest.raises(PermissionProfileError, match="must be an absolute path"):
        CodexHome.load(Path("relative"))
