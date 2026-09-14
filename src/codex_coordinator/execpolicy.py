"""Operator-owned execpolicy rules evaluated by the coordinator, not by Codex.

A rules file uses the ``prefix_rule`` syntax of Codex CLI 0.154.0's execpolicy
so that an operator learns one syntax and can lint the file with
``codex execpolicy check --rules``. The runtime never loads these files: they
sit beside ``operator.toml``, outside every root a worker can write, and the
coordinator evaluates them itself before a request would reach a judge.

The coordinator evaluates them rather than handing them to Codex for reasons
established by live probing of the pinned CLI and recorded in ``docs/security.md``:

- the runtime accepts rules only from ``$CODEX_HOME/rules`` and from the worker
  project's own ``.codex/rules``; there is no per-thread configuration key, so
  rules cannot be scoped to one project through the app-server;
- a runtime ``prefix_rule`` constrains the program and its leading flags but not
  the paths it touches, so a ``sed -n`` rule would silently admit a read of any
  file on the machine, which the sandbox does not prevent;
- the runtime does not decide the ``if … then exit 1; fi`` guard shape that the
  workers actually issue.

The evaluation here is deliberately conservative. It matches the same prefix
semantics, evaluates every simple command in a compound script, additionally
requires every argument to resolve inside the registered project, and refuses
anything it cannot fully parse so that the request falls through to a judge.
"""

from __future__ import annotations

import ast
import hashlib
import shlex
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

#: Shells whose ``-c``/``-lc`` wrapper the evaluator unwraps, by basename.
WRAPPER_SHELLS = frozenset({"bash", "zsh", "sh"})
WRAPPER_FLAGS = frozenset({"-c", "-lc"})

#: Operator tokens that separate simple commands. Anything else built from
#: punctuation (``&``, ``|&``, ``>``, ``<``, ``(``, ``)``, ``;;`` …) is refused.
SEPARATORS = frozenset({"&&", "||", ";", "|", "\n"})
#: Reserved words accepted at command position. Every branch of an ``if`` is
#: still evaluated, so whatever the guard decides, only allowed commands run.
CONTROL_WORDS = frozenset({"if", "then", "elif", "else", "fi"})
#: Shell builtins accepted as a whole simple command without a rule.
BUILTINS = frozenset({"exit", "true", "false"})
#: Punctuation the lexer splits into standalone tokens. Any such token that is
#: not one of ``SEPARATORS`` — ``&``, ``|&``, ``>``, ``<``, ``(``, ``)``, ``;;``
#: and so on — is a redirection, background job, grouping, or substitution,
#: and the whole script is refused. The same characters inside a word were
#: quoted or escaped, which the shell also takes literally.
PUNCTUATION = "|&;()<>\n"
#: Characters that give a word meaning beyond its text even when they survive
#: lexing inside it: parameter and command expansion, and brace or extended
#: glob expansion, which could produce arguments the evaluator never sees.
#: Plain globs (``*``, ``?``, ``[``) are permitted because they expand within
#: the directory being matched and cannot add a parent-directory component.
REFUSED_CHARACTERS = frozenset("$`{}^")
#: Programs that execute arbitrary code from their arguments; a rule for one
#: of them must name at least one leading argument, so ``["python"]`` alone,
#: which would admit ``python -c …``, is rejected at load time.
INTERPRETERS = frozenset({
    "python", "python3", "python2", "node", "nodejs", "deno", "bun", "ruby",
    "perl", "php", "lua", "julia", "Rscript", "osascript",
    "bash", "zsh", "sh", "dash", "fish", "ksh", "pwsh", "powershell", "cmd",
    "eval", "exec", "env", "xargs", "nohup", "time", "sudo", "doas", "su",
})


class ExecPolicyError(ValueError):
    """A rules file could not be accepted; startup must fail closed."""


@dataclass(frozen=True)
class PrefixRule:
    """One ``prefix_rule``: an argv prefix, each element a set of alternatives."""

    pattern: tuple[tuple[str, ...], ...]
    justification: str

    def matches(self, argv: Sequence[str]) -> bool:
        if len(argv) < len(self.pattern):
            return False
        return all(word in choices for word, choices in zip(argv, self.pattern))

    def json(self) -> dict[str, Any]:
        return {
            "pattern": [list(choices) for choices in self.pattern],
            "justification": self.justification,
        }


@dataclass(frozen=True)
class ExecPolicyMatch:
    """Why a command was allowed: every simple command and the rule it met."""

    commands: tuple[tuple[str, ...], ...]
    justifications: tuple[str, ...]

    def json(self) -> dict[str, Any]:
        return {
            "commands": [list(command) for command in self.commands],
            "justifications": list(self.justifications),
        }


class ExecPolicy:
    """An immutable set of allow rules with the trusted path they came from."""

    __slots__ = ("rules", "source", "text", "_sealed")

    def __setattr__(self, name: str, value: Any) -> None:
        if getattr(self, "_sealed", False):
            raise AttributeError("exec policy is immutable")
        object.__setattr__(self, name, value)

    def __init__(self, rules: Sequence[PrefixRule], *, source: str, text: str) -> None:
        if not isinstance(source, str) or not source:
            raise ExecPolicyError("exec policy source must be nonempty")
        self.rules = tuple(rules)
        self.source = source
        self.text = text
        self._sealed = True

    @property
    def digest(self) -> str:
        return hashlib.sha256(self.text.encode()).hexdigest()[:16]

    def provenance(self) -> dict[str, Any]:
        return {"source": self.source, "digest": self.digest, "rules": len(self.rules)}

    # -- loading ---------------------------------------------------------

    @classmethod
    def from_text(cls, text: str, *, source: str) -> "ExecPolicy":
        """Parse a rules file, accepting only ``prefix_rule(...)`` allow rules.

        The grammar is a strict subset of Starlark: a module consisting of
        calls to ``prefix_rule`` with keyword arguments ``pattern``,
        ``decision``, ``justification`` and optional ``match``/``not_match``
        example lists. Anything else — assignments, other functions,
        positional arguments, computed values — is refused, because an
        operator file this evaluator cannot fully account for must not load.
        """
        if not isinstance(text, str):
            raise ExecPolicyError(f"{source}: rules must be text")
        try:
            module = ast.parse(text, filename=source, mode="exec")
        except SyntaxError as exc:
            raise ExecPolicyError(f"{source}: cannot parse rules: {exc.msg} (line {exc.lineno})") from None
        rules: list[PrefixRule] = []
        examples: list[tuple[PrefixRule, list[list[str]], list[list[str]]]] = []
        for statement in module.body:
            call = statement.value if isinstance(statement, ast.Expr) else None
            if (
                not isinstance(call, ast.Call)
                or not isinstance(call.func, ast.Name)
                or call.func.id != "prefix_rule"
                or call.args
            ):
                raise ExecPolicyError(
                    f"{source}: line {statement.lineno}: only prefix_rule(...) calls with "
                    "keyword arguments are accepted"
                )
            keywords = {keyword.arg: keyword.value for keyword in call.keywords}
            if None in keywords or set(keywords) - {"pattern", "decision", "justification", "match", "not_match"}:
                raise ExecPolicyError(
                    f"{source}: line {statement.lineno}: prefix_rule accepts pattern, decision, "
                    "justification, match, and not_match"
                )
            for required in ("pattern", "decision", "justification"):
                if required not in keywords:
                    raise ExecPolicyError(f"{source}: line {statement.lineno}: prefix_rule requires {required}")
            decision = _literal(keywords["decision"], source, statement.lineno, "decision")
            if decision != "allow":
                raise ExecPolicyError(
                    f"{source}: line {statement.lineno}: decision must be \"allow\"; every command "
                    "no rule allows already reaches a judge"
                )
            justification = _literal(keywords["justification"], source, statement.lineno, "justification")
            if not isinstance(justification, str) or not justification.strip():
                raise ExecPolicyError(f"{source}: line {statement.lineno}: justification must be nonempty text")
            raw_pattern = _literal(keywords["pattern"], source, statement.lineno, "pattern")
            pattern = _pattern(raw_pattern, source, statement.lineno)
            rule = PrefixRule(pattern, justification.strip())
            rules.append(rule)
            examples.append((
                rule,
                _examples(keywords.get("match"), source, statement.lineno, "match"),
                _examples(keywords.get("not_match"), source, statement.lineno, "not_match"),
            ))
        if not rules:
            raise ExecPolicyError(f"{source}: rules file declares no prefix_rule")
        policy = cls(rules, source=source, text=text)
        policy._self_check(examples)
        return policy

    def _self_check(self, examples: list[tuple[PrefixRule, list[list[str]], list[list[str]]]]) -> None:
        """Prove at load time that the rules decide what their author expects.

        Each rule must admit the command spelled by its own first alternatives
        and must not admit that command with an out-of-project argument, and
        any ``match``/``not_match`` examples must evaluate as declared. A
        failure here fails startup, so a rules file that loaded but does not
        do what it says is never inferred from approval volume.
        """
        project = Path("/codex-coordinator-execpolicy-self-check")
        for rule, matches, non_matches in examples:
            canonical = [choices[0] for choices in rule.pattern]
            if self.evaluate(shlex.join(canonical), cwd=str(project), project=project) is None:
                raise ExecPolicyError(f"{self.source}: rule {canonical} does not admit its own pattern")
            escaped = self.evaluate(
                shlex.join([*canonical, "/etc/hosts"]), cwd=str(project), project=project,
            )
            if escaped is not None:
                raise ExecPolicyError(f"{self.source}: rule {canonical} admits an out-of-project path")
            for example in matches:
                if self.evaluate(shlex.join(example), cwd=str(project), project=project) is None:
                    raise ExecPolicyError(f"{self.source}: match example is not allowed: {example}")
            for example in non_matches:
                if self.evaluate(shlex.join(example), cwd=str(project), project=project) is not None:
                    raise ExecPolicyError(f"{self.source}: not_match example is allowed: {example}")

    # -- evaluation ------------------------------------------------------

    def evaluate(self, command: str, *, cwd: str, project: Path) -> ExecPolicyMatch | None:
        """Return why ``command`` is allowed, or ``None`` so a judge decides.

        ``command`` is the string the app-server reports, normally
        ``<shell> -lc "<script>"``. ``cwd`` is the request's already-normalized
        working directory and ``project`` the registered canonical project.
        """
        try:
            script = _unwrap(command)
            simple_commands = _split(script)
        except ValueError:
            return None
        if not simple_commands:
            return None
        base = Path(cwd)
        justifications: list[str] = []
        allowed: list[tuple[str, ...]] = []
        for argv in simple_commands:
            if argv[0] in BUILTINS:
                if argv[0] == "exit" and not (len(argv) == 1 or (len(argv) == 2 and argv[1].isdigit())):
                    return None
                if argv[0] != "exit" and len(argv) != 1:
                    return None
                continue
            if not _contained(argv, base, project):
                return None
            rule = next((rule for rule in self.rules if rule.matches(argv)), None)
            if rule is None:
                return None
            allowed.append(tuple(argv))
            justifications.append(rule.justification)
        if not allowed:
            return None
        return ExecPolicyMatch(tuple(allowed), tuple(justifications))


def _literal(node: ast.AST, source: str, line: int, name: str) -> Any:
    try:
        return ast.literal_eval(node)
    except ValueError:
        raise ExecPolicyError(f"{source}: line {line}: {name} must be a literal") from None


def _pattern(raw: Any, source: str, line: int) -> tuple[tuple[str, ...], ...]:
    if not isinstance(raw, list) or not raw:
        raise ExecPolicyError(f"{source}: line {line}: pattern must be a nonempty list")
    pattern: list[tuple[str, ...]] = []
    for element in raw:
        choices = element if isinstance(element, list) else [element]
        if not choices or any(not isinstance(choice, str) or not choice for choice in choices):
            raise ExecPolicyError(
                f"{source}: line {line}: pattern element must be a string or list of strings"
            )
        pattern.append(tuple(choices))
    programs = pattern[0]
    if any("/" in program or program in CONTROL_WORDS or program in BUILTINS for program in programs):
        raise ExecPolicyError(
            f"{source}: line {line}: a rule names a bare program, not a path or shell word"
        )
    if any(program in INTERPRETERS for program in programs) and len(pattern) < 2:
        raise ExecPolicyError(
            f"{source}: line {line}: a rule for an interpreter must fix at least one leading argument"
        )
    return tuple(pattern)


def _examples(node: ast.AST | None, source: str, line: int, name: str) -> list[list[str]]:
    if node is None:
        return []
    raw = _literal(node, source, line, name)
    if not isinstance(raw, list) or any(
        not isinstance(example, list) or not example
        or any(not isinstance(word, str) for word in example)
        for example in raw
    ):
        raise ExecPolicyError(f"{source}: line {line}: {name} must be a list of argv lists")
    return raw


def _unwrap(command: str) -> str:
    """Return the script inside ``<shell> -lc <script>``, or the bare command."""
    if not isinstance(command, str) or "\x00" in command:
        raise ValueError("malformed command")
    outer = shlex.split(command, posix=True)
    if len(outer) == 3 and PurePosixPath(outer[0]).name in WRAPPER_SHELLS and outer[1] in WRAPPER_FLAGS:
        return outer[2]
    if outer and PurePosixPath(outer[0]).name in WRAPPER_SHELLS:
        raise ValueError("unrecognized shell invocation")
    return command


def _split(script: str) -> list[list[str]]:
    """Split a script into simple commands, refusing anything not word-only."""
    lexer = shlex.shlex(script, posix=True, punctuation_chars=PUNCTUATION)
    lexer.whitespace = lexer.whitespace.replace("\n", "")
    lexer.whitespace_split = True
    lexer.commenters = ""
    commands: list[list[str]] = []
    current: list[str] = []
    for token in lexer:
        if token in SEPARATORS or token in CONTROL_WORDS:
            if current:
                commands.append(current)
                current = []
            continue
        if token == "!" or all(char in PUNCTUATION for char in token) or any(
            char in REFUSED_CHARACTERS for char in token
        ):
            raise ValueError("shell construct outside the evaluated grammar")
        if not current and ("=" in token or "/" in token):
            # A leading assignment changes the command's environment, and a
            # path in command position is not a bare program a rule can name.
            raise ValueError("unsupported command position")
        if token.startswith("~") or token.startswith("="):
            raise ValueError("shell expansion")
        current.append(token)
    if current:
        commands.append(current)
    return commands


def _contained(argv: Sequence[str], cwd: Path, project: Path) -> bool:
    """Every argument, read as a path relative to ``cwd``, stays in the project.

    Arguments that are not paths — sed addresses, search patterns, counts —
    resolve to nonexistent names inside the project and pass trivially. What
    cannot pass is an absolute path, a ``..`` component, a symlink that leaves
    the project, or an option carrying such a path after ``=``.
    """
    for word in argv[1:]:
        candidates = [word]
        if word.startswith("-"):
            if "=" not in word:
                continue
            candidates = [word.split("=", 1)[1]]
        for candidate in candidates:
            if not candidate:
                continue
            try:
                resolved = (cwd / Path(candidate)).resolve(strict=False)
                resolved.relative_to(project)
            except (OSError, RuntimeError, ValueError):
                return False
    return True
