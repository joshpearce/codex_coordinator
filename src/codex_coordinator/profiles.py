"""The operator-owned Codex home, and the permission profiles defined in it.

Codex CLI 0.154.0 models permissions as named profiles. The sandbox literal
this project used to send is a derived compatibility view: sending it detaches
a thread from the profile system entirely and the runtime reports
``activePermissionProfile: null``, so the boundary has no provenance. A profile
is also the only shape that can carry a host-scoped network ceiling. Issue
#0021 records both, and the migration off the literal.

A profile is defined in the operator's ``CODEX_HOME/config.toml``, which is
operator-owned and outside every root a worker can write, the same way
``operator.toml`` and the constitutions are. The coordinator names a profile by
id per project and never writes one.

This module resolves an id to the boundary it actually means, before any thread
exists, because three things the runtime accepts silently would otherwise be
discovered only by a worker escaping:

* An unrecognized key in a profile, or an unrecognized token in its
  ``filesystem`` map, is ignored rather than refused. A ceiling can be written
  in full, accepted in full, and enforce nothing.
* ``:workspace`` leaves ``/tmp`` and ``$TMPDIR`` writable, which the sandbox
  literal excluded. A profile that does not demote them widens every worker
  boundary this project used to enforce.
* A ``domains`` or ``unix_sockets`` grant does nothing at all unless the
  ``network_proxy`` feature is on, and that feature is off by default. That one
  is a property of the home rather than of a selection, so it is checked across
  every profile the home defines.

Each is refused here with a message naming what to fix.
"""

from __future__ import annotations

import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any


#: The runtime's built-in profiles. ``:danger-full-access`` is listed because
#: the runtime offers it, not because this project will select it.
READ_ONLY = ":read-only"
WORKSPACE = ":workspace"
DANGER_FULL_ACCESS = ":danger-full-access"
BUILTIN_PROFILES = (READ_ONLY, WORKSPACE, DANGER_FULL_ACCESS)
#: The built-ins whose root grants write access to the workspace roots.
WRITABLE_BUILTINS = frozenset({WORKSPACE, DANGER_FULL_ACCESS})

#: Enabled as ``[features] network_proxy = true``. Probed: no other spelling
#: works — an ``[experimental]`` table, an ``experimental_features`` list and an
#: ``[experimental_features]`` table all load without complaint and leave the
#: feature off.
NETWORK_PROXY_FEATURE = "network_proxy"

#: ``filesystem`` map tokens confirmed to have an observable effect. ``:tmpdir``
#: and ``:slash_tmp`` are the profile spellings of the sandbox literal's
#: ``excludeTmpdirEnvVar`` and ``excludeSlashTmp``; ``:workspace_roots`` is the
#: project grant itself; ``:root`` and ``:minimal`` are the pair the judge
#: profile already uses. Anything else is refused rather than ignored: naming a
#: real path in place of a token does not work either, so ``"/tmp" = "read"``
#: silently leaves ``/tmp`` writable where ``":slash_tmp" = "read"`` does not.
FILESYSTEM_TOKENS = frozenset({
    ":root", ":minimal", ":workspace_roots", ":tmpdir", ":slash_tmp",
})
#: The complete set of access values; an unrecognized one fails the load in the
#: runtime too, which is why the keys rather than the values are the trap.
FILESYSTEM_ACCESS = frozenset({"read", "write", "deny", "none"})
#: Access values that leave no write grant behind.
NON_WRITE_ACCESS = frozenset({"read", "deny", "none"})

PROFILE_KEYS = frozenset({"extends", "description", "filesystem", "network"})
NETWORK_KEYS = frozenset({
    "enabled", "mode", "domains", "unix_sockets", "allow_local_binding",
})
NETWORK_MODES = frozenset({"limited", "full"})
#: Grants that are inert unless ``network_proxy`` is on.
PROXY_DEPENDENT_GRANTS = ("domains", "unix_sockets")

#: How deep an ``extends`` chain may go before it is treated as malformed.
MAX_EXTENDS_DEPTH = 16


class PermissionProfileError(ValueError):
    """A profile cannot be resolved to a boundary this project would enforce."""


@dataclass(frozen=True)
class PermissionProfile:
    """One resolved boundary, with the provenance the legacy view never had."""

    id: str
    extends: str | None
    chain: tuple[str, ...]
    builtin: str
    writable: bool
    filesystem: Mapping[str, str]
    network: Mapping[str, Any]
    source: str

    @property
    def grants_network(self) -> bool:
        return bool(self.network.get("enabled"))

    def provenance(self) -> dict[str, Any]:
        """What the boundary is, and where it was defined.

        Reported in place of the old ``sandboxMode``. The chain matters because
        a profile id alone says nothing: two projects naming different ids can
        extend the same built-in, and an operator editing a parent changes both.
        """
        return {
            "id": self.id,
            "extends": self.extends,
            "chain": list(self.chain),
            "builtin": self.builtin,
            "writable": self.writable,
            "network": self.grants_network,
            "source": self.source,
        }


def builtin_profile(profile_id: str) -> PermissionProfile:
    """The boundary a built-in id means without any operator definition."""
    if profile_id not in BUILTIN_PROFILES:
        raise PermissionProfileError(f"{profile_id!r} is not a built-in permission profile")
    return PermissionProfile(
        id=profile_id, extends=None, chain=(profile_id,), builtin=profile_id,
        writable=profile_id in WRITABLE_BUILTINS, filesystem={}, network={},
        source="runtime built-in",
    )


@dataclass(frozen=True)
class CodexHome:
    """The operator-owned Codex home the coordinator selects profiles from.

    The runtime writes into a live ``CODEX_HOME`` — trust records, sqlite state,
    a ``tmp`` directory — so a home under version control is a source file to be
    rendered into a run directory, never pointed at directly. ``live_e2e.py``
    does exactly that.
    """

    path: Path
    default_permissions: str | None
    network_proxy: bool
    definitions: Mapping[str, Mapping[str, Any]]

    @classmethod
    def load(cls, path: Path | str) -> "CodexHome":
        home = Path(path).expanduser()
        if not home.is_absolute():
            raise PermissionProfileError("codex home must be an absolute path")
        if not home.is_dir():
            raise PermissionProfileError(f"codex home is not a directory: {home}")
        config = home / "config.toml"
        if not config.is_file():
            raise PermissionProfileError(
                f"codex home has no config.toml: {config}. It must define "
                "default_permissions and the [permissions.<id>] profiles this "
                "coordinator selects by id."
            )
        try:
            values = tomllib.loads(config.read_text())
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise PermissionProfileError(f"cannot read {config}: {exc}") from exc
        definitions = values.get("permissions") or {}
        if not isinstance(definitions, Mapping):
            raise PermissionProfileError(f"{config}: [permissions] must be a table of profiles")
        default_permissions = values.get("default_permissions")
        if definitions and not isinstance(default_permissions, str):
            # The runtime's own message for this names neither the file nor the
            # reason, and the implicit default follows project trust records, so
            # an unpinned home resolves differently before and after a project
            # is trusted.
            raise PermissionProfileError(
                f"{config}: default_permissions must be set whenever [permissions] "
                "is present. Without it the runtime refuses to load the home, and an "
                "unpinned default follows project trust records rather than this file."
            )
        features = values.get("features") or {}
        if not isinstance(features, Mapping):
            raise PermissionProfileError(f"{config}: [features] must be a table")
        return cls(
            path=home,
            default_permissions=default_permissions,
            network_proxy=features.get(NETWORK_PROXY_FEATURE) is True,
            definitions={
                name: definition for name, definition in definitions.items()
                if isinstance(definition, Mapping)
            },
        )

    @property
    def profile_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self.definitions))

    def resolve(self, profile_id: str) -> PermissionProfile:
        """Resolve one id to its boundary, or refuse to start.

        Every refusal here is a configuration the runtime would accept and
        under-enforce, so each one names the file and the key to change.
        """
        if not isinstance(profile_id, str) or not profile_id.strip():
            raise PermissionProfileError("permission profile id must be nonempty text")
        config = self.path / "config.toml"
        if profile_id in BUILTIN_PROFILES and profile_id not in self.definitions:
            resolved = builtin_profile(profile_id)
        else:
            resolved = self._resolve_defined(profile_id, config)
        verify_profile(resolved, str(config))
        return resolved

    def _resolve_defined(self, profile_id: str, config: Path) -> PermissionProfile:
        if profile_id not in self.definitions:
            raise PermissionProfileError(
                f"permission profile {profile_id!r} is not defined in {config}. "
                f"Profiles defined there: {list(self.profile_ids)}; built-ins: "
                f"{list(BUILTIN_PROFILES)}."
            )
        chain: list[str] = []
        current = profile_id
        while current in self.definitions:
            if current in chain:
                raise PermissionProfileError(
                    f"{config}: permission profile {profile_id!r} extends itself "
                    f"({' -> '.join((*chain, current))})"
                )
            chain.append(current)
            if len(chain) > MAX_EXTENDS_DEPTH:
                raise PermissionProfileError(
                    f"{config}: permission profile {profile_id!r} extends more than "
                    f"{MAX_EXTENDS_DEPTH} profiles deep"
                )
            definition = self._checked(current, config)
            parent = definition.get("extends")
            if parent is None:
                raise PermissionProfileError(
                    f"{config}: permission profile {current!r} declares no `extends`. "
                    "A profile must reach a built-in so its boundary is knowable "
                    f"before a thread starts; built-ins: {list(BUILTIN_PROFILES)}."
                )
            if not isinstance(parent, str):
                raise PermissionProfileError(
                    f"{config}: permission profile {current!r} has a non-string `extends`"
                )
            current = parent
        if current not in BUILTIN_PROFILES:
            raise PermissionProfileError(
                f"{config}: permission profile {profile_id!r} extends {current!r}, "
                f"which is neither defined here nor a built-in {list(BUILTIN_PROFILES)}"
            )
        chain.append(current)
        # Nearest declaration wins, which is how the runtime layers a chain: walk
        # from the built-in outward so a child overrides what it inherits.
        filesystem: dict[str, str] = {}
        network: dict[str, Any] = {}
        for name in reversed(chain[:-1]):
            definition = self.definitions[name]
            filesystem.update(definition.get("filesystem") or {})
            network.update(definition.get("network") or {})
        return PermissionProfile(
            id=profile_id,
            extends=self.definitions[profile_id].get("extends"),
            chain=tuple(chain),
            builtin=current,
            writable=current in WRITABLE_BUILTINS,
            filesystem=filesystem,
            network=network,
            source=str(config),
        )

    def _checked(self, name: str, config: Path) -> Mapping[str, Any]:
        """Refuse a profile whose keys the runtime would accept and ignore."""
        definition = self.definitions[name]
        unknown = sorted(set(definition) - PROFILE_KEYS)
        if unknown:
            raise PermissionProfileError(
                f"{config}: permission profile {name!r} declares unsupported keys "
                f"{unknown}. The runtime accepts and ignores these rather than "
                f"refusing them; supported: {sorted(PROFILE_KEYS)}."
            )
        filesystem = definition.get("filesystem") or {}
        if not isinstance(filesystem, Mapping):
            raise PermissionProfileError(
                f"{config}: permission profile {name!r} has a non-table `filesystem`"
            )
        for target, access in filesystem.items():
            if access not in FILESYSTEM_ACCESS:
                raise PermissionProfileError(
                    f"{config}: permission profile {name!r} grants {target!r} "
                    f"{access!r}; supported: {sorted(FILESYSTEM_ACCESS)}"
                )
            if target.startswith(":"):
                if target not in FILESYSTEM_TOKENS:
                    raise PermissionProfileError(
                        f"{config}: permission profile {name!r} names the unknown "
                        f"filesystem token {target!r}, which the runtime ignores "
                        f"silently; supported tokens: {sorted(FILESYSTEM_TOKENS)}"
                    )
            elif not Path(target).is_absolute():
                raise PermissionProfileError(
                    f"{config}: permission profile {name!r} names {target!r}, which "
                    "is neither a filesystem token nor an absolute path"
                )
        network = definition.get("network") or {}
        if not isinstance(network, Mapping):
            raise PermissionProfileError(
                f"{config}: permission profile {name!r} has a non-table `network`"
            )
        unknown = sorted(set(network) - NETWORK_KEYS)
        if unknown:
            raise PermissionProfileError(
                f"{config}: permission profile {name!r} declares unsupported network "
                f"keys {unknown}; supported: {sorted(NETWORK_KEYS)}"
            )
        mode = network.get("mode")
        if mode is not None and mode not in NETWORK_MODES:
            raise PermissionProfileError(
                f"{config}: permission profile {name!r} has network mode {mode!r}; "
                f"supported: {sorted(NETWORK_MODES)}"
            )
        return definition


def verify_home(home: "CodexHome") -> "CodexHome":
    """Refuse a home that writes a network ceiling the runtime would not enforce.

    This is checked for every profile the home defines, not only the ones a
    project selects. With ``network_proxy`` off, a one-host ``domains`` map
    reaches every host, reaches unix sockets no grant names, and leaves loopback
    open, while reading as a ceiling — so an operator reading the file believes
    in a boundary that is not there, whichever profile is selected today. It is
    the same principle as a declared ``exec_policy`` that loaded no rules: a
    ceiling that is written but not enforced is a configuration error, not a
    default.
    """
    if home.network_proxy:
        return home
    config = home.path / "config.toml"
    inert = {
        name: [key for key in PROXY_DEPENDENT_GRANTS if (definition.get("network") or {}).get(key)]
        for name, definition in home.definitions.items()
    }
    named = sorted(name for name, grants in inert.items() if grants)
    if named:
        grants = sorted({key for name in named for key in inert[name]})
        raise PermissionProfileError(
            f"{config}: permission profiles {named} declare network {grants} while "
            f"the {NETWORK_PROXY_FEATURE!r} feature is off, so the runtime enforces "
            "none of it: every host is reachable, unix sockets no grant names are "
            "reachable, and loopback is open. Set `[features] "
            f"{NETWORK_PROXY_FEATURE} = true` in this home, or remove the grants."
        )
    return home


def verify_profile(profile: PermissionProfile, source: str) -> PermissionProfile:
    """Refuse a boundary a worker must never be given.

    These are the rules about what a selected profile may mean. The separate
    rule about ceilings the runtime would not enforce belongs to the home rather
    than to a selection, and lives in ``verify_home``.
    """
    if profile.builtin == DANGER_FULL_ACCESS:
        raise PermissionProfileError(
            f"permission profile {profile.id!r} resolves to {DANGER_FULL_ACCESS} "
            f"(chain: {' -> '.join(profile.chain)}). A worker session is never given "
            "a boundary that enforces nothing."
        )
    # A writable profile must exclude the temporary roots, as the sandbox
    # literal did. ``:workspace`` leaves both writable, so a profile that omits
    # these is not a re-spelling of the old boundary but a widening of it.
    if profile.writable:
        missing = [
            token for token in (":tmpdir", ":slash_tmp")
            if profile.filesystem.get(token) not in NON_WRITE_ACCESS
        ]
        if missing:
            raise PermissionProfileError(
                f"{source}: permission profile {profile.id!r} extends "
                f"{profile.builtin} without demoting {missing}. That leaves $TMPDIR "
                "and /tmp writable, which the boundary this project enforced before "
                "permission profiles did not. Add "
                '`filesystem = { ":tmpdir" = "read", ":slash_tmp" = "read" }` '
                "to the profile, or deny them outright."
            )
    return profile


def resolve_profile(profile_id: str, home: "CodexHome | None") -> PermissionProfile:
    """Resolve an id against the operator's Codex home, if one is configured.

    A home is required exactly when the operator names a profile of their own.
    The built-ins need no definition and mean the same thing in every home, so a
    deployment that only ever selects ``:read-only`` needs no home — and no
    default is quietly taken from the developer's own ``~/.codex``, which is the
    isolation half of #0021.
    """
    if home is not None:
        return home.resolve(profile_id)
    if profile_id not in BUILTIN_PROFILES:
        raise PermissionProfileError(
            f"permission profile {profile_id!r} is not one of the built-ins "
            f"{list(BUILTIN_PROFILES)} and no codex_home is configured to define it. "
            "Set codex_home in the operator configuration to the home that holds "
            "the [permissions.<id>] tables."
        )
    return verify_profile(builtin_profile(profile_id), "runtime built-in")
