# Avaliação técnica da Forja

> Avaliação de 11/11/2026 sobre o commit `699d936`, recuperada como foi escrita. Os números de linha são desse commit. `NOTAS.md`, citado abaixo, foi apagado depois. O andamento de cada item está em [DIARIO.md](DIARIO.md).

**Veredito:** núcleo bem construído (camadas, registro de eventos, retomada, testes), mas várias garantias da Especificação existem só como código em `core` que a produção nunca chama. Os gates são sólidos em Python e bem mais fracos nas outras cinco linguagens. Nada foi alterado no repositório.

Marcação: **[V]** conferi no código ou por execução; **[A]** veio de auditoria delegada, com arquivo e linha, sem reconferência minha.

## Medições

| Medida | Resultado |
| --- | --- |
| `ruff check`, `ruff format --check`, `mypy` strict | limpos, 613 arquivos |
| `pytest -m "not slow" -n auto` | 9367 passam, 41 pulados, 1 falha, 205 s |
| Tamanho | 37,8 mil linhas em `src`, 53 mil em `tests`, 484 commits em 28 dias |
| Complexidade (lizard) | CCN médio 3,8; 23 funções acima de 15 |
| `core` importando camadas de cima | 0 |
| `unittest.mock` | 0 usos |
| CI ou hook de commit | nenhum |

## Pontos fortes

- **Camadas limpas.** `core` não importa nada de fora; executores e linguagens só conhecem `core`.
- **Registro de eventos.** A cadeia de hash e a sequência são conferidas na leitura (`core/events/state.py:128-138`); versão futura de esquema recusa a retomada (`versioning.py:26-40`).
- **Durabilidade.** Escrita atômica com `fsync`, `os.replace` e repetição (`core/atomic.py:26-47`).
- **Processos no Windows.** O processo nasce suspenso, entra no Job Object e só então roda (`core/process/_windows.py:143-155`).
- **Prova de retomada.** `tests/orchestrator/test_resume_end_to_end.py` corta em cada linha do log e mata processos reais; o git em memória é conferido contra o git real (`test_workspace_contract.py`).
- **Instalador.** Tamanho e SHA-256 antes de descompactar, proteção contra zip slip, `tar` com `filter="data"` (`toolchain/install.py:106-135`).
- **Executor do Claude Code.** `--permission-mode dontAsk`, sem bypass, pacote por stdin, hooks e MCP desligados (`executors/claude_code.py:153-165`).
- **Mutação segura.** Roda em cópias; o worktree real nunca fica mutado (`mutation/executor.py:119-120`).

## Fraquezas e plano

### P0. Segurança e correção

1. **[V] Mutex de git não ligado.** `cli/session.py:387-395` cria `GuardedGit` sem `mutex`; `GitMutex` não tem nenhum uso fora de `core`. Com `paralelismo` > 1, escritas de git correm sem serialização.
   - Plano: passar `GitMutex` e `mutex_timeout` na construção; teste com duas tarefas paralelas e git real.
2. **[V] `pip` recebe o ambiente inteiro.** `toolchain/install.py:161` passa `os.environ`, sem `--require-hashes` (limite admitido nas linhas 150-151, N23).
   - Plano: usar o ambiente filtrado que `session.py` já tem; gerar lock com hashes para o Semgrep e instalar com `--require-hashes`.
3. **[V] Redação deixa passar senha só de letras.** `core/redaction/rules.py:60-64` exige dígito ou símbolo; `DB_PASSWORD=supersecret` sai cru [A, por execução].
   - Plano: para chaves `password|secret|token`, aceitar qualquer valor acima de um tamanho mínimo; teste com o caso.
4. **[V] Motivo da rejeição não passa pela redação.** `agent_runner.py:470-471` anexa `previous_rejection` direto; ele carrega saída de teste e de ferramenta.
   - Plano: incluí-lo como `PackageItem` redigível, como já é feito com `previous_said` (linha 383).
5. **[V] Garantias de `core` sem chamador em produção.** `authorize()`, `SentPackageStore` (ADR 0009) e `RecordStore.verify()` (Especificação, "Artefato ausente ou inválido") só aparecem em testes.
   - Plano: chamar `verify` ao montar o pacote e na retomada, invalidando a etapa; gravar o pacote enviado; chamar `authorize` antes de envio, commit e integração. O que não for ligado, remover.
6. **[A] Fachada de git com brechas.** `core/gitguard/rules.py:51-67, 135-144, 200-201` aceita `ls-remote --upload-pack=...`, `config --remove-section` e `fetch . HEAD:refs/heads/main`.
   - Plano: negar `--upload-pack`, `--exec`, `--receive-pack`; uma só ação em `config`; conferir refspecs de `fetch` contra branches protegidas.
7. **[V] Exceção inesperada não grava estado.** `orchestrator/phases.py:91-101` trata só `StopRun` e `CancelledError`. Um arquivo de escopo fora de UTF-8 (`agent_runner.py:369`) já derruba assim.
   - Plano: `except Exception` que grava `PAUSED` com motivo e relança; ler o arquivo em bytes e retê-lo se não decodificar.
8. **[V] Orçamento esgotado sai com código 0.** `phases.py:264-265` faz `break`, as tarefas ficam pendentes e `cli/run.py:123-133` só olha as bloqueadas.
   - Plano: bloquear as pendentes com motivo `token_budget_exhausted`, o que cai nos códigos 4 ou 5 já existentes.
9. **[V] Log do agente perdido no Windows.** `agent_runner.py:626` usa `call_agent_cli:<id>.log`; o `:` vira fluxo alternativo do NTFS. Achei `call_agent_cli` com 0 bytes em `%LOCALAPPDATA%\forja\wt\0157a168\`.
   - Plano: `.replace(":", "-")`, como `checks/tooling.py:90` já faz.
10. **[V] Cópias de trabalho nunca removidas.** 195 pastas `*-copy` e 1,1 GB em `%LOCALAPPDATA%\forja` nesta máquina (`agent_runner.py:316-317`).
    - Plano: remover a cópia junto com o worktree; teste que conta as pastas ao fim da tarefa.

### P1. Especificação prometida e não entregue

11. **[V] Sonda de rede é um stub.** `orchestrator/ports.py:258-259` devolve `True` sempre; ninguém injeta outra. Espera fixa `(30, 60, 120)`, sem jitter.
    - Plano: sonda real injetada em `cli/session.py`; jitter na espera.
12. **[A] Planejamento sem churn e sem caracterização.** `planner.py:24-41` usa só CCN; nenhuma ocorrência de `churn` ou `hotspot`.
    - Plano: churn por `git log --name-only`, prioridade CCN × churn, etapa de autor de testes quando a nota de mutação do alvo estiver abaixo do limiar.
13. **[A] Sem orçamento de tempo nem checagem periódica de disco.** `config.py` (`Budget`) só tem tokens; disco só em `cli/prepare.py:611`.
    - Plano: campo de tempo no `Budget` e no `execucao.toml`; checar disco a cada tentativa. Ou tirar os dois da Especificação.
14. **[A] Trailers e notas faltando.** `workspace_git.py:346-357` grava cinco trailers; faltam `Evidence-Id`, `Assumption`, métricas, `git notes` e a validação de Conventional Commits.
    - Plano: decisão do dono entre implementar ou cortar do texto.
15. **[A] Tokens desconhecidos viram zero.** `agent_runner.py:527` faz `input_tokens or 0`; Cursor não informa uso.
    - Plano: representar "desconhecido" e declarar no resumo que o orçamento não foi medido.
16. **[V] Sem terminal, a confirmação é pulada.** `cli/main.py:186` torna a execução automática quando `console.interactive` é falso, mesmo sem `--config`.
    - Plano: sem TTY, exigir `--config`.
17. **[V] Falha da interface aborta a execução.** `cli/run.py:69-74` só suprime `CancelledError` ao aguardar as tarefas auxiliares.
    - Plano: suprimir e registrar `Exception` ali e seguir aguardando o orquestrador.
18. **[A] Prova de vida em cache esconde login expirado.** `cli/prepare.py:92-93`; `forja doctor` diz que a CLI respondeu sem ter rodado a prova (`doctor.py:314-321`).
    - Plano: `doctor` sempre prova; cache com validade e hash do modelo de comando.
19. **[A] Contador de falhas de infraestrutura não zera na retomada** (`task_cycle.py:746-749`).
    - Plano: contar só depois do último `session_started`.
20. **[A] `forja revisao` pareia papéis por posição** (`review.py:85`); a etapa extra de testes desloca o documentador.
    - Plano: parear pelo id da etapa.

### P2. Gates que o agente contorna

21. **[V] Supressões só de Python.** `checks/scope.py:15-20` não cobre `//nolint`, `eslint-disable`, `@ts-ignore`, `@SuppressWarnings`, `#pragma warning disable`, `t.Skip()`, `istanbul ignore`.
    - Plano: cada adaptador de linguagem declara os seus marcadores; `scope` soma as listas.
22. **[V] Arquivo que o tree-sitter não lê aprova na mutação.** `checks/mutation.py:123` faz `continue` e a linha 231 devolve `Approved()` sem mutantes.
    - Plano: linha tocada em arquivo não mutável vira `Inconclusive`.
23. **[V] Mutação paralela infla a nota.** O baseline roda sozinho e os mutantes em até 4 processos (`mutation/executor.py:120`); estouro de tempo conta como morto. O paralelismo é decisão registrada (ADR 0021); o risco é a medição do tempo.
    - Plano: medir o baseline sob a mesma carga, ou repetir sozinho o mutante que estourou antes de contá-lo.
24. **[V] `mypy` ignora a configuração do repositório.** `languages/python/adapter.py:199-204` usa `--config-file=`, por segurança contra plugins.
    - Plano: ler a configuração e recusar só a chave `plugins`, declarando a recusa como premissa.
25. **[A] Vermelho aceita qualquer falha.** `checks/red_green.py:94-107` conta `NameError` como defeito detectado.
    - Plano: exigir falha de asserção, ou que o símbolo exista na base.
26. **[A] Lint de Go passa sem rodar.** `golangci-lint` ausente vira "não aplicável" (`languages/go/adapter.py:158`).
    - Plano: ferramenta exigida e ausente vira `Inconclusive`.
27. **[A] Detectores frágeis.** `lizard` com colunas diferentes aprova (`complexity.py:57`); `## Trunk` satisfaz a seção `run` (`readme.py:25-26`); telefone sem separador e CPF com espaços passam (`pii.py:129-140`); apóstrofo curvo escapa da voz (`voice_markers.py:60-61`); testes de mesmo nome se sobrescrevem em C# e Java (`parsers.py:107`, `:219`).
    - Plano: um teste por caso, depois a correção; saída ilegível de ferramenta sempre `Inconclusive`.
28. **[A] Rede do E2E é só armadilha de proxy** (`network_trap.py`, limite admitido no ADR 0025).
    - Plano: declarar o limite no resumo da execução; sem mudança de código.
29. **[A] Dependência nova só funciona em Python.** Nas outras linguagens o manifesto é bloqueado no escopo.
    - Plano: decisão do dono; no mínimo, documentar em `docs/guia/gates/seguranca-e-dependencias.md`.

### P3. Testes e processo

30. **[V] Sem CI.** Não há `.github` nem hook; os 230 testes `integration` e 71 `slow` só rodam à mão.
    - Plano: workflow com `ruff`, `mypy`, `pytest -m "not slow"` por push e a suíte inteira por noite. O `origin` já aponta para o GitHub.
31. **[V] Teste instável.** `tests/checks/test_differential_check.py::test_a_different_exit_code_rejects` falhou com `-n auto` e passou 3 vezes sozinho (uma delas em 43 s). Causa provável: achado de tempo sob carga.
    - Plano: dar ao teste os limiares folgados que os vizinhos já usam (`max_time_ratio: 100`).
32. **[A] 60 `sleep` reais e 63 testes sem asserção** (`tests/cli/test_full_run.py:63,84`, `tests/cli/test_keys.py:142`).
    - Plano: sincronizar por `Event` ou relógio falso; exigir uma asserção de resultado.
33. **[A] Retomada sem corte em duas operações.** `rollback_integration` e `normalize_style` só têm prova unitária (`tests/orchestrator/resume_support.py:14-18`).
    - Plano: cenário com conflito de integração e com normalização, com cortes.
34. **[V] A Forja não passa no próprio limite.** `build_package` tem CCN 59 (`agent_runner.py:347`), `read_facts` 27, `judge` 185 linhas; o padrão do gate é 15. A catraca só pega função nova, então o gate não reprova.
    - Plano: quebrar `build_package` por papel (uma função por modelo de instrução) e `judge` por grupo de gates.
35. **[A] Tabela de operações não usada em produção.** `orchestrator/operations.py:122` aceita `name: str` livre.
    - Plano: tipar com o enum de `core/idempotency/operations.py`.
36. **[V] `checks` importa `orchestrator.ports`** (`checks/scope.py:13`, `checks/tooling.py:12`) e `measurer.py` importa cerca de 45 símbolos de `checks`.
    - Plano: mover `TaskInfo` e `SinkFactory` para `core`; registrar os checks por lista, sem import direto.

### P4. Documentação e higiene

37. **[A] README aponta caminhos inexistentes.** `README.md:96` cita `tests/e2e/test_resume.py`; a pasta não existe.
38. **[V] README diz "Windows e Linux"; `NOTAS.md:47` diz "Só Windows".** Quatro testes ficam fora da mutação porque falham no Linux (`pyproject.toml:80-83`).
39. **[A] 21 ADRs citam planos removidos**; 3 links quebrados; nenhum teste de links internos.
40. **[A] Restos.** Grupo `mutation` e bloco `[tool.mutmut]` sem uso; `ruff` sem pino em `dependencies`; versão `0.0.0`; `dist/` na raiz; `OUTSIDE` definido e não usado (`claude_code.py:44`).
    - Plano para 37 a 40: um commit de documentação e um de limpeza; teste que valida links e compara a tabela de gates do guia com `checks/specs.py`.

## Ordem sugerida

1. P0, itens 1 a 10: correções pequenas e localizadas, cada uma com teste.
2. Item 30 (CI) antes do resto, para as correções seguintes ficarem protegidas.
3. P2, itens 21 a 23: são os que mais enfraquecem a promessa central fora de Python.
4. P1: exige decisão sua, item por item, entre implementar e cortar da Especificação.

## Observações

- Vários itens tocam decisões suas em `NOTAS.md` (Linux fora do plano, N9 limite de 300 s, N23 pip sem checksum, paralelismo em espera). Listei como fato; não reabri.
- `NOTAS.md:62` diz "não há remoto"; o `origin` existe.
- Os itens 1 a 7 do "Frágil ou não provado" de `NOTAS.md` não foram reinvestigados aqui.
