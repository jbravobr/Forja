# 0035. A voz é instrução e medida, nunca bloqueio

## Contexto

O gate 12 (`voice`, `comments`) mede marcadores de texto escrito no piloto automático. A Especificação dizia que a lista de marcadores era bloqueante por padrão; `specs.py` já punha `voice` em observação. O dono decidiu em 31/10/2026 que a voz nunca bloqueia e que a Forja deve controlá-la na origem, por `AGENTS.md`, `CLAUDE.md`, a estratégia que couber a cada CLI ou uma skill, com a skill `humanizer` como referência de conteúdo. A Especificação foi emendada (linhas 292 e 591).

A Forja desliga de propósito as instruções do projeto e as skills nas CLIs (`--setting-sources user` e `--disable-slash-commands` no Claude Code; sem `AGENTS.md` no Codex), e qualquer arquivo posto na worktree da tentativa aparece no diff e cai no gate de escopo. A leitura da documentação de cada CLI (`docs\guia\documentacao-gerada.md`) achou um canal documentado para modo headless fora do diff só no Claude Code.

## Decisão

- **A voz nunca bloqueia.** O gate 12 mede e reporta. A política recusa `mode = "blocking"` para `voice` e `comments` (`NEVER_BLOCKING` em `core\policy\config.py`), com erro de configuração que aponta `docs/guia/documentacao-gerada.md`.
- **A Forja instrui o agente antes de ele escrever.** `orchestrator\voice_brief.py` guarda o texto versionado (`VERSION`), montado a partir do `humanizer` v2.9.1 no modo embutido. Regras ligadas a marcadores do gate 12 saem quando o repositório os desliga, e os padrões que o repositório acrescenta entram listados.
- **O pacote é o canal de toda CLI.** `build_package(voice=)` põe a seção `## Voice` em toda chamada que escreve texto. É texto fixo da Forja e não passa pelo redator.
- **Canal próprio só onde a documentação o confirma para headless.** No Claude Code, `InstructionChannel` (protocolo opcional do adaptador) grava `instructions.md` em `AGENT_DIR` e passa `--append-system-prompt-file`. O arquivo é protegido como o `settings` do agente.
- **Skills não entram.** O conteúdo do `humanizer` vai como texto.

## Alternativas

- **Reprovar a tentativa por voz.** É o comportamento que a emenda da Especificação tirou: a voz é difícil de medir sem falso positivo, e reprovar gasta orçamento da execução por estilo. Rejeitado pelo dono.
- **Pôr `AGENTS.md` ou `CLAUDE.md` na worktree.** Aparece no diff e cai no gate 1; as CLIs que a Forja isola não os leem de propósito. Rejeitado.
- **Carregar o `humanizer` como skill.** O Claude Code a desliga com `--disable-slash-commands` e as outras CLIs não documentam carregamento em headless. Rejeitado.
- **Substituir o prompt de sistema** (`--system-prompt`, `model_instructions_file`, `GEMINI_SYSTEM_MD`, `--system-prompt-override`). Tira a orientação de ferramentas e de segurança do padrão. Rejeitado.
- **Gravar o canal candidato de Codex, Grok e Gemini sem confirmação.** Opção desconhecida pode derrubar a chamada. Adiado até uma chamada real do dono ou a documentação confirmar.
- **Reescrever o texto depois de escrito.** A Forja não edita o que o agente produziu. Rejeitado.

## Consequências

- **Sem garantia de efeito.** Instruir não faz o modelo obedecer. Só um E2E real mostra a mudança na escrita; é do dono.
- **A voz vai duas vezes ao Claude Code**, no pacote e no prompt de sistema adicional.
- **Os outros adaptadores não mudam.** Codex, Grok, Gemini, Cursor e a CLI genérica recebem só o pacote.
- **Sem chave de idioma.** A política não a tem; o texto manda seguir o idioma ao redor.
- **O texto da voz pede zero travessões**, enquanto a medida tolera 3 (`MAX_DASHES`). Decisão reversível.
- **Mudar o texto exige novo `VERSION`.**
- **Testes.** `tests\orchestrator\test_voice_brief.py`, `tests\executors\test_package.py`, `test_agent_runner.py`, `test_claude_code.py`, `test_instruction_channel.py` e `tests\core\policy\test_problems.py`.

O uso está em `docs/guia/documentacao-gerada.md`.
