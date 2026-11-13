"""The guided preparation: detect, ask only what cannot be detected, prove the CLI, confirm once.

After the confirmation there are no more questions. Everything that is asked here is also what
``execucao.toml`` holds, so ``forja --config`` can run the same thing without a person.
"""

import dataclasses
import hashlib
import json
import tempfile
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from forja.cli.args import VERBS, Arguments
from forja.cli.console import Console, choose, confirm
from forja.cli.detect import Detection, installed_clis
from forja.cli.scaffold import leftovers, template_adapters
from forja.cli.security_notes import (
    NO_LAYERS,
    NO_LICENSES,
    UNMEASURED,
    differential_notices,
    e2e_notices,
)
from forja.cli.semgrep_rules import RulesError, explicit_rules, rules_in_commit
from forja.cli.session import (
    LOW_DISK_GIB,
    OBJECTIVE_NAMES,
    e2e_commands,
    executor_ref,
    git_base_environment,
    git_executable,
    new_config,
    policy_digest,
    repo_policy,
    standard_settings,
)
from forja.cli.settings import Execucao, ModeloCli, Perfil
from forja.core.adapters import valid_project_name
from forja.core.adapters.executor import CommandTemplate, ExecutorChoice
from forja.core.artifacts.schemas import CriteriaBody
from forja.core.idempotency.git import Git
from forja.core.policy.config import EffectivePolicy, PolicyConfigError
from forja.core.redaction import RedactionSettings
from forja.executors import DEFAULT_CLI, KNOWN_CLIS, adapter_for
from forja.executors.probe import executable_version, run_probe, version_problem
from forja.orchestrator.config import CreateRequest, ExecutorRef, FixRequest, RunConfig
from forja.orchestrator.facts import Fact, FactsError, parse_facts

AVAILABLE = frozenset({"refatorar", "corrigir", "investigar", "criar"})
PENDING_BASE = "sem-commit-ainda"  # the base of a new project: the scaffold makes it, after the yes
CRITERIA_FILE = "criterios.json"
PROOF_VALID_SECONDS = 24 * 60 * 60  # a proof of life older than a day is made again


class Refused(Exception):
    """The preparation cannot go on; ``message`` says why and what to do."""

    def __init__(self, message: str, code: int = 2) -> None:
        super().__init__(message)
        self.message = message
        self.code = code


@dataclass(frozen=True)
class Prepared:
    config: RunConfig
    execucao: Execucao
    summary: tuple[str, ...]
    remember: tuple[str, str] | None  # (cli name, path) the person chose and may want kept


def objective_of(args: Arguments, saved: Execucao | None, console: Console) -> str:
    """The verb asked for, or the answer to the menu; only what exists can be run."""
    verb = args.command if args.command in VERBS else (saved.objetivo if saved else "")
    if not verb:
        if not console.interactive:
            raise Refused(
                "Diga o que fazer: `objetivo` no execucao.toml "
                f"({', '.join(sorted(AVAILABLE))}) ou o verbo na linha de comando."
            )
        names = list(VERBS)
        options = [
            f"{name}: {VERBS[name]}" + ("" if name in AVAILABLE else "  [ainda não disponível]")
            for name in names
        ]
        verb = names[choose(console, "O que você quer fazer?", options, default=1)]
    if verb not in AVAILABLE:
        raise Refused(f"`forja {verb}` ainda não está disponível: {VERBS[verb]}.")
    return verb


def _read_text(path: Path, what: str) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        reason = error.strerror if isinstance(error, OSError) and error.strerror else "não é UTF-8"
        raise Refused(f"Não consegui ler {what} ({path}): {reason}.") from None


def read_fix(args: Arguments, saved: Execucao | None) -> tuple[FixRequest, str, str]:
    """The defect and the criteria of ``forja corrigir``, with where each one came from.

    The description is the text on the command line, else the ``-f`` file. The criteria are the
    ``--criterios`` file, else ``criterios.json`` beside the ``-f`` file; with neither, the
    specifier writes them in the first stage. Both come back also as the strings
    ``execucao.toml`` keeps, so ``--config`` reproduces the run.
    """
    file = args.file or (Path(saved.arquivo) if saved and saved.arquivo else None)
    description = args.text.strip() or (saved.descricao.strip() if saved else "")
    if not description and file is not None:
        description = _read_text(file, "a descrição do erro").strip()
    if not description:
        raise Refused('Descreva o erro: `forja corrigir "texto"` ou `forja corrigir -f arquivo`.')
    path = args.criteria or (Path(saved.criterios) if saved and saved.criterios else None)
    if path is None and file is not None and (file.parent / CRITERIA_FILE).is_file():
        path = file.parent / CRITERIA_FILE
    if path is None:
        return FixRequest(description=description), description, ""
    try:
        criteria = CriteriaBody.model_validate_json(_read_text(path, "os critérios"))
    except ValidationError as error:
        first = error.errors()[0]
        where = ".".join(str(part) for part in first["loc"]) or "arquivo"
        raise Refused(f"Os critérios em {path} não servem: {where}: {first['msg']}.") from None
    return FixRequest(description=description, criteria=criteria), description, str(path.resolve())


def read_question(args: Arguments, saved: Execucao | None) -> str:
    """The question of ``forja investigar``: the text on the command line, else the ``-f`` file."""
    file = args.file or (Path(saved.arquivo) if saved and saved.arquivo else None)
    question = args.text.strip() or (saved.descricao.strip() if saved else "")
    if not question and file is not None:
        question = _read_text(file, "a pergunta").strip()
    if not question:
        raise Refused(
            'Faça a pergunta: `forja investigar "texto"` ou `forja investigar -f arquivo`.'
        )
    return question


def _project_name_of(directory: Path) -> str:
    """The name of the directory as a project name, or empty when it cannot be one."""
    spaced = "".join(c if c.isalnum() else " " for c in directory.name.lower())
    name = "-".join(word for word in spaced.split() if word.isascii())
    return name if name[:1].isalpha() and valid_project_name(name) else ""


def read_creation(
    args: Arguments, saved: Execucao | None, console: Console, root: Path
) -> CreateRequest:
    """The idea, the language and the name of ``forja criar``.

    The idea is the text on the command line, else the ``-f`` file. The language and the name
    are what ``execucao.toml`` says, else asked (the language has no default: nothing is there to
    detect it from). Without a person to ask, they have to be in the file.
    """
    file = args.file or (Path(saved.arquivo) if saved and saved.arquivo else None)
    idea = args.text.strip() or (saved.descricao.strip() if saved else "")
    if not idea and file is not None:
        idea = _read_text(file, "a ideia").strip()
    if not idea:
        raise Refused('Conte a ideia: `forja criar "texto"` ou `forja criar -f arquivo`.')
    languages = sorted(template_adapters())
    language = saved.linguagem if saved else ""
    if not language:
        if not console.interactive:
            raise Refused(
                "Diga a linguagem do projeto: `linguagem` no execucao.toml "
                f"({', '.join(languages)}). Não há como detectá-la num diretório vazio."
            )
        language = languages[choose(console, "Em que linguagem?", languages)]
    if language not in languages:
        raise Refused(f"Não sei criar projeto em `{language}`. As que sei: {', '.join(languages)}.")
    name = saved.nome if saved else ""
    if not name:
        suggestion = _project_name_of(root)
        name = console.ask(
            "Nome do projeto (palavras em minúsculas ligadas por hífen)"
            + (f" [{suggestion}]:" if suggestion else ":"),
            default=suggestion,
        )
    if not valid_project_name(name):
        raise Refused(
            f"`{name}` não serve de nome de projeto: palavras em minúsculas e dígitos ligadas por "
            "hífen (como `meu-projeto`), até 40 caracteres. Dê o nome em `nome` no execucao.toml."
        )
    return CreateRequest(description=idea, language=language, name=name)


def read_recorded_facts(args: Arguments, saved: Execucao | None) -> tuple[tuple[Fact, ...], str]:
    """The facts the person recorded, from ``--fatos`` or the saved ``fatos``, and the path."""
    path = args.facts or (Path(saved.fatos) if saved and saved.fatos else None)
    if path is None:
        return (), ""
    try:
        return parse_facts(_read_text(path, "os fatos")), str(path.resolve())
    except FactsError as error:
        raise Refused(f"Os fatos em {path} não servem: {error}.") from None


def check_empty_directory(detection: Detection) -> Path:
    """Where a new project goes: this directory, which has to be empty (or hold only a repository
    with no commit)."""
    if not detection.git:
        raise Refused("O git não está instalado ou não está no PATH. Rode `forja doctor`.")
    if not detection.greenfield:
        raise Refused(
            "`forja criar` pede um diretório vazio (ou um repositório sem commits); este já tem "
            "projeto. Para mexer no que existe use `forja refatorar`, `corrigir` ou `investigar`."
        )
    root = detection.repo or detection.cwd
    if extra := leftovers(root):
        raise Refused(
            f"{root} precisa estar vazio para criar um projeto, mas tem: {', '.join(extra[:5])}."
        )
    return root


def check_repository(detection: Detection) -> Path:
    if not detection.git:
        raise Refused("O git não está instalado ou não está no PATH. Rode `forja doctor`.")
    if detection.greenfield:
        raise Refused(
            "Este diretório está vazio ou sem commits. Para criar um projeto use "
            "`forja criar`; os outros comandos trabalham em repositório existente."
        )
    if detection.repo is None:
        raise Refused("Rode `forja` dentro de um repositório git.")
    if not (
        detection.python
        or detection.go
        or detection.typescript
        or detection.csharp
        or detection.java
    ):
        raise Refused(
            "Não encontrei nenhuma linguagem neste repositório (pyproject.toml, go.mod, "
            "package.json com tsconfig.json, .sln ou .csproj, pom.xml ou build.gradle[.kts])."
        )
    if detection.changed:
        sample = ", ".join(detection.changed[:3])
        raise Refused(
            f"Há {len(detection.changed)} arquivo(s) com alterações não salvas ({sample}). "
            "Faça commit ou stash antes: a Forja nunca mexe no que você ainda não salvou."
        )
    return detection.repo


def choose_cli(
    args: Arguments,
    saved: Execucao | None,
    profile: Perfil,
    environ: Mapping[str, str],
    console: Console,
    *,
    tests: bool = False,
    templates: Mapping[str, CommandTemplate] | None = None,
) -> tuple[str, ExecutorChoice, bool]:
    """The CLI by name and where it is; whether the choice is worth offering to keep.

    ``tests`` asks for the CLI of the test author, which has no default of its own: the caller
    only asks when ``--cli-testes`` or the saved ``cli_testes`` names one. ``templates`` are the
    generic CLIs the user described, which are known besides the Forja's own.
    """
    generic = templates or {}
    if tests:
        name = args.cli_tests or (saved.cli_testes if saved else "")
        path = saved.executavel_testes if saved else ""
    else:
        name = args.cli or (saved.cli if saved else "") or profile.cli or DEFAULT_CLI
        path = saved.executavel if saved else ""
    if name not in KNOWN_CLIS and name not in generic:
        raise Refused(
            f"Não conheço a CLI `{name}`. As que conheço: {', '.join((*KNOWN_CLIS, *generic))}."
        )
    explicit = Path(path) if path else None
    found = installed_clis(name, environ, profile.executaveis, explicit, generic.get(name))
    if not found:
        raise Refused(
            f"Não encontrei `{name}` instalado. Instale e entre na sua conta antes de usar a "
            "Forja (rode `forja doctor` para ver o que falta)."
        )
    if len(found) == 1:
        return name, found[0], False
    options = [f"{item.path} ({item.source.value})" for item in found]
    if not console.interactive:
        raise Refused(
            f"Encontrei {len(found)} instalações de {name} e não há quem escolha: "
            "dê o caminho em `executavel"
            + ("_testes" if tests else "")
            + "` no execucao.toml:\n"
            + "\n".join(f"  {option}" for option in options)
        )
    return (
        name,
        found[choose(console, f"Encontrei {len(found)} instalações de {name}:", options)],
        True,
    )


def probe_cache(directory: Path) -> Path:
    return directory / "provas.json"


def cli_templates(profile: Perfil, saved: Execucao | None) -> dict[str, CommandTemplate]:
    """The generic CLIs the user described, the execution file's over the profile's.

    A name the Forja already knows is refused: the known adapter reads that CLI's own events and
    permissions, and a template would silently replace it.
    """
    described = {**profile.clis, **(saved.clis if saved else {})}
    taken = sorted(set(described) & set(KNOWN_CLIS))
    if taken:
        raise Refused(
            f"`clis.{taken[0]}` usa o nome de uma CLI que a Forja já conhece "
            f"({', '.join(KNOWN_CLIS)}); escolha outro nome."
        )
    return {name: model.template() for name, model in described.items()}


def _template_hash(template: CommandTemplate | None) -> str:
    """What tells one way of calling the CLI from another; empty for a CLI the Forja knows."""
    if template is None:
        return ""
    described = json.dumps(dataclasses.asdict(template))
    return hashlib.sha256(described.encode("utf-8")).hexdigest()[:16]


def _still_valid(proven_at: object, now: float) -> bool:
    """A proof counts from the moment it was made until it is ``PROOF_VALID_SECONDS`` old.

    What an older Forja wrote (``true``) carries no moment and does not count.
    """
    if isinstance(proven_at, bool) or not isinstance(proven_at, int | float):
        return False
    return 0 <= now - proven_at < PROOF_VALID_SECONDS


async def prove(
    name: str,
    choice: ExecutorChoice,
    environ: Mapping[str, str],
    cache: Path,
    console: Console,
    template: CommandTemplate | None = None,
    *,
    fresh: bool = False,
    clock: Callable[[], float] = time.time,
) -> str:
    """The version of the CLI, after proving it works.

    The proof is remembered per path, version and command template, for ``PROOF_VALID_SECONDS``:
    a login expires without the executable changing. ``fresh`` proves again whatever is
    remembered, which is what ``forja doctor`` asks.
    """
    adapter = adapter_for(name, template)
    version = await executable_version(adapter, choice.path)
    if problem := version_problem(adapter, version):
        raise Refused(f"{choice.path}: {problem}.")
    assert version is not None
    key = f"{choice.path}|{version}|{_template_hash(template)}"
    try:
        proven = json.loads(cache.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        proven = {}
    if not isinstance(proven, dict):
        proven = {}
    if not fresh and _still_valid(proven.get(key), clock()):
        return version
    console.say(f"Provando que {name} {version} funciona (uma tarefa de teste)…")
    problems = await run_probe(adapter, choice.path, environ)
    if problems:
        raise Refused(
            f"A prova de vida de {name} falhou: {problems[0]}\n"
            "A configuração da CLI (login, modelo, gateway) é sua e precisa estar pronta antes. "
            "Corrija e rode `forja doctor`."
        )
    proven[key] = clock()
    try:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(proven), encoding="utf-8")
    except OSError:
        pass  # the proof is only a shortcut for next time
    return version


def policy_changes(policy: EffectivePolicy) -> tuple[str, ...]:
    """What the repository's ``forja.toml`` changes from the Forja's own defaults, in words.

    A repository can loosen a gate, run its own command in place of the Forja's or switch off a
    detector, so the person who confirms has to see it all, with the values: it is the
    repository's decision, not the Forja's.
    """
    defaults = standard_settings()
    notes = []
    for check_id, check in sorted(policy.checks.items()):
        if check.settings != defaults.get(check_id):
            on = check.settings.on_inconclusive
            how = check.settings.mode.value + (f", se não puder medir: {on.value}" if on else "")
            notes.append(f"{check_id}: modo ou política diferente do padrão ({how})")
        for name, argv in sorted(check.commands.items()):
            notes.append(f"{check_id}: roda no lugar do padrão ({name}): {' '.join(argv)}")
        for language, limits in sorted(check.thresholds.items()):
            for name, value in sorted(limits.items()):
                notes.append(f"{check_id}: limite {name} de {language} = {value}")
        for name, value in sorted(check.limits.items()):
            notes.append(f"{check_id}: limite {name} = {value}")
        if check.promotion is not None:
            notes.append(f"{check_id}: promovido de observação a bloqueio ({check.promotion.date})")
    for language, commands in sorted(policy.adapter_commands.items()):
        for name, argv in sorted(commands.items()):
            notes.append(f"{language}: roda no lugar do padrão ({name}): {' '.join(argv)}")
    notes += _repository_additions(policy)
    notes += _redaction_changes(policy)
    notes += _voice_changes(policy)
    return tuple(notes)


def _repository_additions(policy: EffectivePolicy) -> list[str]:
    """What the repository adds beyond the Forja's defaults: files and branches it guards,
    licenses it forbids, where tests go, its layers and its differential cases."""
    notes = []
    if policy.sensitive_paths:
        notes.append(f"arquivos sensíveis acrescentados: {', '.join(policy.sensitive_paths)}")
    if policy.protected_branches:
        notes.append(f"branches protegidas acrescentadas: {', '.join(policy.protected_branches)}")
    if policy.forbidden_licenses:
        notes.append(f"licenças proibidas: {', '.join(policy.forbidden_licenses)}")
    if policy.test_root is not None:
        notes.append(f"testes escritos em: {policy.test_root}")
    if policy.architecture:
        layers = " > ".join(layer.name for layer in policy.architecture)
        notes.append(f"camadas de arquitetura (da mais alta para a mais baixa): {layers}")
    if policy.differential:
        names = ", ".join(case.name for case in policy.differential)
        notes.append(f"casos do teste diferencial: {names}")
    return notes


def _voice_changes(policy: EffectivePolicy) -> list[str]:
    notes = []
    if policy.voice_off:
        notes.append(f"voz: marcadores desligados pelo repositório: {', '.join(policy.voice_off)}")
    if policy.voice_extra:
        notes.append(f"voz: padrões proibidos pelo repositório: {', '.join(policy.voice_extra)}")
    return notes


def _redaction_changes(policy: EffectivePolicy) -> list[str]:
    redaction, standard = policy.redaction, RedactionSettings()
    notes = []
    if redaction.allow or redaction.allow_patterns:
        notes.append("redação: valores liberados pelo repositório (nunca mascarados)")
    if redaction.patterns:
        names = ", ".join(name for name, _ in redaction.patterns)
        notes.append(f"redação: padrões acrescentados pelo repositório: {names}")
    off = sorted(kind.value for kind in standard.pii - redaction.pii)
    if off:
        notes.append(f"redação: detectores de dados pessoais desligados: {', '.join(off)}")
    if redaction.entropy_threshold > standard.entropy_threshold:
        notes.append(f"redação: limiar de entropia mais alto ({redaction.entropy_threshold})")
    return notes


NO_RULES = (
    "SAST NÃO EXECUTADO: sem configuração. A Forja não traz regras do Semgrep; forneça as suas "
    "com --semgrep-config ou deixe .semgrep.yml ou .semgrep/ no repositório"
)


async def semgrep_rules(
    args: Arguments, saved: Execucao | None, repo: Path, commit: str, environ: Mapping[str, str]
) -> tuple[str, str]:
    """The rules the person named (as an absolute path, else empty) and the summary line about
    the SAST. Rules a repository carries are read from the base commit."""
    named = args.semgrep_config or (saved.semgrep_config if saved else "")
    try:
        if named:
            path = explicit_rules(named)
            return str(path), f"SAST: Semgrep com as regras em {path}"
        if not commit:  # a new project has no rules of its own yet
            return "", NO_RULES
        with tempfile.TemporaryDirectory(prefix="forja-rules-") as scratch:
            git = Git(
                repo,
                executable=git_executable(),
                env=git_base_environment(environ),
                timeout=30,
                log_dir=Path(scratch),
            )
            found = await rules_in_commit(git, commit)
    except RulesError as error:
        raise Refused(str(error)) from None
    if found:
        return "", f"SAST: Semgrep com as regras do repositório ({', '.join(found)})"
    return "", NO_RULES


def suggest_author(name: str, environ: Mapping[str, str], profile: Perfil) -> str:
    """A line suggesting another installed CLI for the test author; empty when none is installed.

    Only a suggestion: it names the options that change nothing about the choice already made.
    """
    others = [
        other
        for other in KNOWN_CLIS
        if other != name and installed_clis(other, environ, profile.executaveis)
    ]
    if not others:
        return ""
    return (
        f"sugestão: {', '.join(others)} também está instalada; um agente de outra CLI nos testes "
        "dá mais diversidade de autor (`--cli-testes` ou `cli_testes` no execucao.toml)"
    )


def summary_of(
    detection: Detection,
    config: RunConfig,
    scope: tuple[str, ...],
    tokens: int,
    policy_notes: tuple[str, ...] = (),
    sast: str = "",
    security: tuple[str, ...] = (),
    suggestion: str = "",
) -> tuple[str, ...]:
    where = ", ".join(scope) if scope else "todo o código"
    limit = format(tokens, ",").replace(",", ".")
    tests = (
        "o agente pode rodar os testes do projeto na cópia dele (`agente_roda_testes = false` "
        "no execucao.toml desliga)"
        if config.agent_runs_tests
        else "o agente não roda os testes; só edita e consulta lint e complexidade"
    )
    fix = config.fix
    verb = OBJECTIVE_NAMES[config.objective]
    return (
        _opening(detection, config, verb),
        *(_creation_lines(config.create) if config.create else ()),
        *(
            (
                f"  pergunta: {config.question}",
                "  agente que investiga: o mesmo que documenta, sem comandos e sem alterar "
                "arquivos; o que ele afirma precisa citar arquivo e linhas, medição ou fato seu",
            )
            if config.question
            else ()
        ),
        *(
            (f"  erro: {fix.description}", _criteria_line(fix))
            if fix
            else ()
        ),
        *(
            (
                f"  agente que escreve os testes: {_agent(config.test_author)}",
                f"  agente que implementa: {_agent(config.implementer)}",
            )
            if config.test_author
            else (f"  agente: {_agent(config.implementer)}",)
        ),
        *((f"  {suggestion}",) if suggestion else ()),
        "  agente que documenta: o mesmo que implementa, sem comandos, só em documentação",
        *((f"  fatos registrados por você: {len(config.facts)}",) if config.facts else ()),
        f"  onde: {where}",
        f"  limite: {limit} tokens, até {config.budget.tasks} tarefas",
        "  ambiente: este terminal. A Forja não cria sandbox: leitura fora da cópia e rede "
        "são garantidas pelo seu ambiente",
        "  a Forja impõe: ambiente filtrado para a CLI, cópia sem .git e sem arquivos sensíveis, "
        "ferramentas de arquivo do agente confinadas à cópia, git conferido a cada chamada",
        f"  testes: {tests}",
        *((f"  {sast}",) if sast else ()),
        *(f"  {line}" for line in security),
        "  entrega: uma branch nova `forja-run/…`; sua branch não é alterada e nada é enviado",
        *(("  forja.toml do repositório muda o padrão:", *(f"    {note}" for note in policy_notes))
          if policy_notes else ()),
    )  # fmt: skip


def _opening(detection: Detection, config: RunConfig, verb: str) -> str:
    if config.create:
        return f"Vou {verb} o projeto {config.create.name} em {detection.cwd}."
    return (
        f"Vou {verb} {detection.repo.name if detection.repo else ''} "
        f"(branch {detection.branch}, commit {detection.head[:7]})."
    )


def _creation_lines(create: CreateRequest) -> tuple[str, ...]:
    setup = template_adapters()[create.language].template(create.name).setup
    return (
        f"  ideia: {create.description}",
        f"  linguagem: {create.language}",
        "  andaime: arquivos do modelo da linguagem, `git init` e um commit inicial, feitos pela "
        "Forja depois do seu sim, sem agente; a execução parte desse commit e só segue se o "
        "build e os testes do modelo passarem",
        *(f"  antes do build, a Forja roda no diretório: {' '.join(argv)}" for argv in setup),
        "  critérios: o agente que especifica os escreve a partir da ideia "
        "(o mesmo que implementa, sem comandos e sem alterar arquivos)",
    )


def _criteria_line(fix: FixRequest) -> str:
    if fix.criteria is None:
        return (
            "  critérios: o agente que especifica os escreve a partir da descrição "
            "(o mesmo que implementa, sem comandos e sem alterar arquivos)"
        )
    return f"  critérios: {len(fix.criteria.criteria)}"


def _agent(executor: ExecutorRef) -> str:
    model = f", modelo {executor.model}" if executor.model else ""
    return f"{executor.name} {executor.version}{model}"


async def prepare(
    args: Arguments,
    detection: Detection,
    profile: Perfil,
    profile_dir: Path,
    console: Console,
    environ: Mapping[str, str],
    *,
    saved: Execucao | None = None,
) -> Prepared:
    verb = objective_of(args, saved, console)
    fixing = read_fix(args, saved) if verb == "corrigir" else None
    question = read_question(args, saved) if verb == "investigar" else ""
    creating = verb == "criar"
    repo = check_empty_directory(detection) if creating else check_repository(detection)
    creation = read_creation(args, saved, console, repo) if creating else None
    try:
        policy = repo_policy(repo)
        notes = policy_changes(policy)
    except PolicyConfigError as error:
        raise Refused(f"O forja.toml deste repositório tem erros:\n{error}") from None
    if detection.disk_free_gib < LOW_DISK_GIB:
        raise Refused(f"Só há {detection.disk_free_gib:.1f} GiB livres; a Forja pede ao menos 2.")
    templates = cli_templates(profile, saved)
    name, choice, offer = choose_cli(args, saved, profile, environ, console, templates=templates)
    version = await prove(
        name, choice, environ, probe_cache(profile_dir), console, templates.get(name)
    )
    model = args.model or (saved.modelo if saved else "")
    model_tests = args.model_tests or (saved.modelo_testes if saved else "")
    author: tuple[str, ExecutorChoice, str] | None = None
    tests_name = args.cli_tests or (saved.cli_testes if saved else "")
    writes_tests = bool(fixing or creation)
    if writes_tests and tests_name and tests_name != name:
        author_name, author_choice, _ = choose_cli(
            args, saved, profile, environ, console, tests=True, templates=templates
        )
        author_version = await prove(
            author_name,
            author_choice,
            environ,
            probe_cache(profile_dir),
            console,
            templates.get(author_name),
        )
        author = (author_name, author_choice, author_version)
    elif writes_tests and model_tests:  # the same CLI, another model
        author = (name, choice, version)
    scope = args.scope or tuple(saved.escopo if saved else ())
    base_commit = detection.head or PENDING_BASE
    rules, sast = await semgrep_rules(args, saved, repo, detection.head, environ)
    recorded, facts_path = read_recorded_facts(args, saved)
    tokens = saved.tokens if saved else 2_000_000
    runs_tests = saved.agente_roda_testes if saved else True
    tasks = saved.tarefas if saved else 5
    parallel = saved.paralelismo if saved else 1
    config = new_config(
        repo=repo, base_commit=base_commit, executor_name=name, executor_path=choice.path,
        executor_version=version, executor_model=model, tokens=tokens, scope=scope,
        agent_runs_tests=runs_tests, tasks=tasks, parallel_tasks=parallel, semgrep_config=rules,
        policy_version=policy_digest(policy), facts=recorded,
        fix=fixing[0] if fixing else None, question=question, create=creation,
        test_author=executor_ref(
            author[0], author[1].path, author[2], model_tests, templates.get(author[0])
        )
        if author else None,
        executor_template=templates.get(name),
    )  # fmt: skip
    security = (
        *(() if policy.forbidden_licenses else (NO_LICENSES,)),
        *(() if policy.architecture else (NO_LAYERS,)),
        UNMEASURED,
        *e2e_notices(declared=bool(e2e_commands(policy))),
        *differential_notices(
            refactoring=not (creation or question or fixing), declared=bool(policy.differential)
        ),
    )
    different_author = bool(author and author[0] != name)
    suggestion = (
        suggest_author(name, environ, profile) if writes_tests and not different_author else ""
    )
    lines = summary_of(detection, config, scope, tokens, notes, sast, security, suggestion)
    execucao = Execucao(
        objetivo="criar"
        if creation
        else "investigar"
        if question
        else "corrigir"
        if fixing
        else "refatorar",
        descricao=creation.description if creation else fixing[1] if fixing else question,
        linguagem=creation.language if creation else "",
        nome=creation.name if creation else "",
        criterios=fixing[2] if fixing else "",
        cli=name,
        executavel=str(choice.path),
        cli_testes=author[0] if author else "",
        executavel_testes=str(author[1].path) if author else "",
        modelo=model,
        modelo_testes=model_tests,
        escopo=list(scope),
        tokens=tokens,
        agente_roda_testes=runs_tests,
        tarefas=tasks,
        paralelismo=parallel,
        semgrep_config=rules,
        fatos=facts_path,
        clis=_used_models(profile, saved, name, author[0] if author else ""),
        interface=saved.interface if saved else profile.interface,
    )
    return Prepared(config, execucao, lines, (name, str(choice.path)) if offer else None)


def _used_models(profile: Perfil, saved: Execucao | None, *names: str) -> dict[str, ModeloCli]:
    """The descriptions of the generic CLIs this run uses, so ``--config`` carries them along."""
    described = {**profile.clis, **(saved.clis if saved else {})}
    return {name: described[name] for name in dict.fromkeys(names) if name in described}


def confirmed(console: Console, prepared: Prepared, *, automatic: bool) -> bool:
    for line in prepared.summary:
        console.say(line)
    if automatic:
        return True
    return confirm(console, "Confirma e começa? Depois disso a Forja não faz mais perguntas.")
