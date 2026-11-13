# Diário de bordo

Registro das execuções de trabalho sobre a Forja: o que foi feito, o que foi verificado, o que se concluiu e onde houve dificuldade. Entradas em ordem cronológica. Os números dos itens vêm da avaliação técnica de 11/11/2026 (P0 a P4, itens 1 a 40), guardada em [AVALIACAO.md](AVALIACAO.md).

## 11/11/2026 — avaliação técnica e primeira leva de correções (P0)

### Ponto de partida

- HEAD `699d936`, branch `main`.
- Por ordem do dono, as notas foram desconsideradas e apagadas: `NOTAS.md`, `NOTAS-2026-11-10.md`, `NOTAS-anterior.md` e `NOTAS-referencia.md`. Nenhum dos quatro estava no git, então não há como recuperá-los pelo histórico.
- Medição antes de qualquer mudança: `ruff` e `mypy` strict limpos em 613 arquivos; `pytest -m "not slow" -n auto` com 9367 aprovados, 41 pulados e 1 falha intermitente (`tests/checks/test_differential_check.py::test_a_different_exit_code_rejects`, que passou 3 vezes sozinho).

### O que foi feito

| Item | Mudança | Arquivos | Teste novo |
| --- | --- | --- | --- |
| 1 | O `GuardedGit` da sessão recebe o `GitMutex` do repositório e espera até `GIT_MUTEX_TIMEOUT` (300 s). Antes, nenhuma escrita de git era serializada. | `cli/session.py` | `test_a_git_write_of_the_run_waits_for_the_mutex_of_the_repository` |
| 2 (parte) | O `pip` do instalador roda com o ambiente filtrado, sem chaves nem tokens. O filtro saiu de `cli/session.py` para `core/isolation/environment.py`, porque `toolchain` não pode importar `cli`. | `toolchain/install.py`, `core/isolation/environment.py`, `cli/session.py` | `test_pip_runs_without_the_keys_and_tokens_of_the_person` |
| 3 | Regra nova `environment-credential`: valor só de letras numa linha de arquivo de ambiente (`NOME=valor`, nome em maiúsculas). Versão das regras `2026.11.1`; a ADR 0009 passou de 19 para 21 regras (já eram 20 antes desta mudança). | `core/redaction/rules.py`, ADR 0009 | `test_a_credential_of_letters_only_is_caught_in_an_environment_file` e o par negativo |
| 4 | O motivo da rejeição anterior passa pela redação antes de entrar no pacote do agente. | `orchestrator/agent_runner.py` | `test_a_secret_in_the_reason_of_the_rejection_never_reaches_the_package` |
| 6 | A fachada de git recusa `--upload-pack`, `--exec`, `--receive-pack` e `--ext-diff`; `config --remove-section` e `--rename-section`; refspec de `fetch` com `:` ou `+`; `fetch --refmap`; `notes --ref`. | `core/gitguard/rules.py` | 11 casos novos em `tests/core/gitguard/test_rules.py` |
| 7 | Erro inesperado no orquestrador grava a execução como `failed` com o motivo (até 500 caracteres) e o rastro em `falha.log`, e depois sai. A retomada volta ao estado anterior. Arquivo de escopo que não é UTF-8 deixa de derrubar a montagem do pacote: é retido, com aviso ao agente. | `orchestrator/phases.py`, `orchestrator/agent_runner.py` | dois testes em `test_phases.py`, um em `test_package.py` |
| 8 | Entrega com tarefa que não começou por falta de tokens sai com código 4 ou 5, não 0, e a mensagem final diz quantas não começaram. | `cli/run.py` | `test_a_delivery_with_tasks_the_tokens_did_not_reach_does_not_exit_zero` |
| 9 | O log bruto do agente troca `:` por `-` no nome do arquivo. | `orchestrator/agent_runner.py` | `test_the_raw_output_of_the_cli_is_kept_in_a_file_with_a_name_every_system_accepts` |
| 10 | Remover o worktree de uma tentativa remove também a cópia em que o agente trabalhou. O nome da cópia ficou num só lugar (`workcopy_path`). | `orchestrator/workspace_git.py`, `testing/workspace.py`, `orchestrator/agent_runner.py` | `test_removing_a_worktree_removes_the_copy_the_agent_worked_in`, nos dois workspaces |

### Verificação ao final

- `ruff format --check`, `ruff check .` e `mypy` strict: limpos, 613 arquivos.
- `pytest -m "not slow" -n auto`: 9400 aprovados, 41 pulados, nenhuma falha, 198 s.
- Não rodaram: os testes marcados `slow`, o gate de mutação (`scripts/mutate.py`) e nenhum E2E com agente real.
- Commitado na branch `correcoes-p0`, a partir de `main` (`699d936`): um commit por item, mais um para a opção abreviada do git e um para este diário. Só a ponta da branch foi testada; os commits intermediários não foram rodados um a um. Nada foi enviado a remoto.

### Conclusões

- **Item 3, regra entre aspas descartada.** A primeira versão também pegava valor só de letras entre aspas (`password = "supersecret"`). Rodada contra o próprio código da Forja, acusou 4 falsos positivos (`EXPOSE_SECRETS_TO_AGENT = "expose_secrets_to_agent"`, `NO_SECRETS = "no_secrets"`, `"secrets": "segredos"`, `tokens="bright_magenta"`). Ficou só a regra de arquivo de ambiente. Senha só de letras entre aspas em código continua sem máscara; é o limite conhecido.
- **Item 5, avaliação corrigida.** A avaliação dizia que nada confere o hash de um artefato. Não é exato: `ArtifactStore.get` lê o corpo por `_read_blob`, que confere o hash e levanta `ArtifactMissing` ou `ArtifactCorrupt`. O que falta é comparar com o hash guardado no evento e refazer a etapa sozinho, como a Especificação pede. Com o item 7, um artefato corrompido agora deixa a execução `failed` com o motivo gravado, em vez de sair sem registro.
- **Item 8, desenho preservado.** Existe teste que afirma que tarefa não iniciada por falta de tokens fica `pending` e a execução é entregue (`test_when_the_tokens_are_up_the_tasks_not_started_stay_pending_and_the_run_is_delivered`). O plano original era marcá-las como bloqueadas; mantive o estado e corrigi só o código de saída e a mensagem.
- **Item 7, compatível com os testes de queda.** Os testes de retomada simulam a morte do processo levantando exceção e esperam que ela saia do orquestrador. Por isso o registro é feito e a exceção é relançada, e o registro é de melhor esforço: quando o próprio log é o que quebrou, nada é escrito.
- **Item 10, medido.** Havia 195 pastas `*-copy` e 1,1 GB em `%LOCALAPPDATA%\forja` nesta máquina. A correção vale para execuções novas; as pastas antigas continuam lá.
- **Item 9, confirmado no disco.** Havia arquivos `call_agent_cli` de 0 byte ao lado dos worktrees, que é o que o NTFS deixa quando o nome tem `:`.

- **Item 6, brecha na própria correção.** A revisão automática de segurança do commit apontou que o git aceita opção longa abreviada (`--upload-pa` vale `--upload-pack`), e a recusa era por nome exato. Corrigido em `_refuse_flags`: prefixo de um nome proibido é recusado, o que vale também para as listas que já existiam (`--prune`, `--delete`, `--unset`). `ls-remote -u` também é recusado. Dez casos novos em `test_rules.py`. A fachada continua sendo lista de proibição por subcomando; trocar por lista de permissão de opções, e fixar `protocol.ext.allow=never` no ambiente do git, ficou de fora.

### Dificuldades

- Separar um commit por item em arquivos tocados por vários itens: a primeira tentativa, aplicando pedaços do diff sem contexto, pôs linhas no lugar errado em quatro commits de `agent_runner.py` (a ponta estava certa, os intermediários não). Os quatro foram refeitos com o conteúdo exato de cada etapa, conferido por análise sintática.

- Editar arquivos por script embutido no shell corrompeu sequências de barra invertida duas vezes (`\b` virou o caractere de retrocesso numa expressão regular; `\n` virou quebra de linha dentro de uma string de teste). Os dois casos foram pegos pelos testes e corrigidos. Daí em diante as edições com barra invertida foram feitas pela ferramenta de edição ou por arquivo de script separado.
- Mover `workcopy_path` para `orchestrator/ports.py` criou importação circular com `workspace_git.py`. Ficou em `workspace_git.py`.
- `mypy` recusou o teste que importava `install_environment` de `forja.cli.session`; o teste passou a importar de `forja.core.isolation.environment`.

### Pendências desta leva

- **Item 2, resto:** `pip` ainda resolve as dependências do wheel do Semgrep sem `--require-hashes`. Precisa de um arquivo de travas com hashes por plataforma, gerado com rede.
- **Item 6, resto:** lista de permissão de opções por subcomando na fachada de git e `protocol.ext.allow=never`.
- **Item 5:** três decisões do dono.
  1. `authorize()` repete o que o `GuardedGit` (branch protegida) e o redator (arquivo sensível) já impõem. Ligar como segunda linha ou remover o módulo.
  2. `SentPackageStore` (ADR 0009): gravar o pacote enviado exige que `build_package` devolva o resultado da redação, o que se junta à quebra dessa função (item 34).
  3. Artefato inválido: refazer a etapa sozinho, como diz a Especificação, ou manter a parada com `failed`.
- **Referências às notas apagadas:** a regra 6 de `CLAUDE.md`, a seção "Construção da Forja" de `docs/Especificação.md` e a linha `NOTAS.md` de `.gitignore` ainda citam `NOTAS.md`. Não foram alteradas.
- **Itens 11 a 40:** não iniciados. A lista só existia na conversa da sessão; foi recuperada da transcrição e gravada em `AVALIACAO.md`.

## 11 e 12/11/2026 — segunda sessão: avaliação recuperada, itens 21 a 23 e a suíte completa

### Ponto de partida

- HEAD `7a7610c`, branch `correcoes-p0`.
- A lista dos 40 itens só existia na conversa da sessão anterior. Foi recuperada da transcrição e gravada, como foi escrita, em `AVALIACAO.md`.
- Decisão do dono: sem GitHub por ora, então o item 30 (CI) fica adiado; todos os outros itens da avaliação serão resolvidos.

### O que foi feito

| Item | Mudança | Arquivos | Teste novo |
| --- | --- | --- | --- |
| 21 | Os adaptadores de Go, TypeScript, Java e C# declaram `suppressions`; o gate de escopo (`silencers`) soma os marcadores da linguagem dona do arquivo aos que já valiam em qualquer arquivo. | `checks/scope.py`, `orchestrator/measurer.py`, os quatro `languages/*/adapter.py` | `tests/checks/test_scope_silencers.py`, 28 casos |
| 22 | Arquivo alterado que as regras de mutação não conseguem ler deixa a medição inconclusiva (`unsupported_construct`). Antes era pulado, e sem mutantes o gate aprovava. | `checks/mutation.py` | `test_a_file_the_rules_cannot_read_leaves_the_measurement_undecided` |
| 23 | Com mais de um worker, o mutante que estourou o tempo é julgado de novo, sozinho, antes de contar como morto. Com um worker nada muda. | `mutation/executor.py` | três testes em `tests/mutation/test_executor.py` |
| novo | Nove testes `slow` esperavam o comportamento anterior ao commit `63172ea` (N15, 09/11), que passou a corrigir lint e formatar os arquivos da tentativa antes dos gates. Foram atualizados para o comportamento atual. | `tests/checks/test_real_measurer.py`, `test_real_run.py`, `test_real_languages.py` | os próprios |

### Verificação

- `ruff check .` e `mypy` strict: limpos, 614 arquivos.
- Suíte completa, com `slow`, `pytest -n auto`: 9648 aprovados, 41 pulados, nenhuma falha, 29 min. Rodou com `JAVA_HOME` no JDK 21 e antes de os testes dos itens 22 e 23 existirem; esses rodaram à parte (29 aprovados).
- Não rodou: E2E com agente real (`FORJA_LIVE=1`).

### Conclusões

- **A primeira rodada completa deu 22 falhas, nenhuma do item 21.** Rodadas no HEAD sem as mudanças deram as mesmas falhas. Eram três causas:
  1. Nove testes `slow` desatualizados desde `63172ea` (linha "novo" da tabela). O `ruff check --fix` da normalização remove `noqa` que não esconde nada e o formatador desfaz o que o teste escrevia fora do formato; no teste de Go, a linha em branco acrescentada ao teste congelado era retirada pelo `gofmt`, e a tentativa deixava de alterar o arquivo. Como `slow` só roda à mão, ninguém viu por três dias. É o item 30 acontecendo.
  2. Doze testes de Java falham nesta máquina porque o `java` do `PATH` é um JRE 1.8 e `JAVA_HOME` está vazio; o Maven não acha compilador. Com `JAVA_HOME` apontando para `C:\Program Files\Eclipse Adoptium\jdk-21.0.13.9-hotspot` os 17 testes de Java passam. É do ambiente; nada foi mudado na máquina.
  3. Uma falha de `test_a_different_exit_code_rejects`, o teste instável do item 31.
- **Item 21, marcadores por arquivo.** `.Skip(` pula teste em Go e é operação de sequência em C#, e a busca que já existia ignora maiúsculas. Por isso os marcadores de uma linguagem só são procurados nos arquivos dela, como são escritos. Os marcadores antigos continuam valendo em qualquer arquivo.
- **Item 23, o mutator continua sendo a regra.** Tempo esgotado ainda mata o mutante; o que mudou é que ele precisa esgotar o tempo sozinho, que é a condição em que o baseline foi medido. `scripts/mutate.py` usa a mesma função e ganha o mesmo comportamento.
- **A medição do diário anterior estava incompleta.** Ele registra `ruff format --check` limpo; `ruff format --check .` acusa `scripts/capturas/terminal.py` e um bloco de código de `docs/guia/documentacao-gerada.md`, os dois anteriores a esta sessão.

### Dificuldades

- A primeira rodada completa começou enquanto o item 21 era editado, e por isso não servia para separar falha antiga de falha nova. Foi preciso guardar as mudanças (`git stash`), rodar no HEAD e restaurar.
- Um teste temporário de depuração imprimiu arquivos `.pyc` inteiros, por filtrar caminhos com "format" no nome.

### Pendências

- **Fora da avaliação, visto no item 21:** pular teste em TypeScript (`it.skip`), Java (`@Disabled`), C# (`Skip =`) e Python (`pytest.mark.skip`) não é procurado; a avaliação só listou `t.Skip()` de Go.
- **Item 40:** formatar os dois arquivos que `ruff format --check .` acusa.

## 12/11/2026 — itens 24 a 29 e 11

### O que foi feito

| Item | Mudança | Arquivos | Teste novo | Commit |
| --- | --- | --- | --- | --- |
| 24 | O `mypy` lê uma cópia da configuração do repositório, gravada fora da worktree, sem `plugins` e sem as chaves de relatório. Configuração com `plugins` deixa o `types` inconclusivo. ADR 0041 (proposto) substitui uma linha do ADR 0039. | `languages/python/mypy_config.py` (novo), `languages/python/adapter.py`, `checks/layout.py`, guia `gates/tipos.md` | `tests/languages/python/test_mypy_config.py` | `c45360e`, `d80761c` |
| 25 | O leitor do pytest diz o que o teste que falhou levantou (`test.failure`). Teste declarado que falha com `NameError` não vale como vermelho: é erro do próprio teste. | `languages/python/parsers.py`, `checks/red_green.py`, `checks/build_and_tests.py` | cinco em `test_red_green.py`, um em `test_adapter.py` | `1b5473c` |
| 26 | Projeto Go sem `golangci-lint` deixa o `lint` inconclusivo (`tool_failed`), não "não aplicável". O adaptador responde por `unmeasurable`, o mesmo gancho do item 24. | `languages/go/adapter.py`, `checks/layout.py`, guias `gates/tipos.md` e `instalacao.md` | um em `test_layout_adapters.py`, um em `tests/languages/go/test_adapter.py` | `f446df4` |
| 27 | Cinco detectores: relatório do `lizard` em formato desconhecido é inconclusivo; título do README conta pelo começo da palavra (`Trunk` não é `run`); CPF com espaços e celular sem separador são mascarados; marcador de voz é achado com apóstrofo curvo; de dois casos de teste com o mesmo nome, em C# e Java, fica o pior resultado. | `checks/complexity.py`, `checks/readme.py`, `core/redaction/pii.py`, `checks/voice_markers.py`, `checks/voice.py`, `core/adapters/common.py`, os `parsers.py` de C# e Java | `tests/checks/test_detectors_that_looked_away.py`, 37 casos | `78820d7` |
| 28 | Nada a mudar: o limite da armadilha de proxy já vai para o resumo em toda execução com E2E declarado (`PROXY_LIMIT` em `cli/security_notes.py`, com teste em `tests/cli/test_e2e_notes.py`). | nenhum | nenhum | nenhum |
| 29 | Nada a mudar no mínimo pedido: o guia `gates/seguranca-e-dependencias.md` já diz que manifestos de outras linguagens são recusados porque só o Python tem passo de instalação. Estender a outras linguagens continua sendo decisão do dono. | nenhum | nenhum | nenhum |
| 11 | A sonda de rede deixou de ser um stub que respondia sempre "sim": a execução pergunta à CLI do implementador, pela prova de vida. A espera depois de falha transitória vai de metade a uma vez e meia do valor da tabela. | `cli/session.py`, `orchestrator/ports.py`, `orchestrator/task_cycle.py` | um em `test_session_open.py`, cinco casos em `test_phases.py` | `48766ef` |
| novo | Quatro mutantes de `checks/mutation.py` sobreviviam (funções antigas: `min_score`, `worker_count`, `changed_lines`). Testes novos os matam. | `tests/checks/test_mutation.py` | os próprios | `545d4d6` |

### Verificação

- `ruff check .` e `mypy` strict: limpos, 617 arquivos.
- `pytest -m "not slow" -n auto` na ponta (`48766ef`): 9524 aprovados, 41 pulados, nenhuma falha.
- Suíte completa com `slow`: a última rodada inteira foi antes do item 24 (9648 aprovados). Falta repetir na ponta.
- Mutação: `scripts/mutate.py` nos arquivos dos itens 21 e 23 passou, menos os quatro mutantes de `checks/mutation.py`, corrigidos depois. A rodada sobre os arquivos dos itens 22 a 26 foi iniciada num checkout separado e o resultado ainda não foi lido. Os itens 27 e 11 não passaram pela mutação.
- Não rodou: E2E com agente real.

### Conclusões

- **Item 24 contraria uma decisão registrada.** O ADR 0039 rejeitou ler a configuração do repositório, por dois motivos: `plugins` executa código, e a base e a tentativa ficariam com opções diferentes. A Especificação diz que a configuração do repositório prevalece, e em divergência vale a Especificação. Os dois motivos foram conferidos: só `plugins` executa código, e a base roda numa cópia da mesma worktree, com a mesma configuração. O ADR 0041 ficou como "proposto" para o dono confirmar; a mudança está em dois commits e pode ser revertida.
- **Item 24, brecha na primeira versão.** A revisão automática de segurança do commit `c45360e` apontou dois furos reais na cópia: `Plugins` com maiúscula num TOML passava pelo filtro, e o leitor de INI do `mypy` o lê como `plugins`; e um retorno de carro, um nome de opção com quebra de linha ou um nome de seção com colchete acrescentavam uma linha à cópia. Corrigido em `d80761c`: nome comparado sem maiúsculas, nome ou valor fora do formato deixa a configuração sem cópia (e o `types` inconclusivo), e a cópia é relida com o leitor que o `mypy` usa antes de ser usada.
- **Item 25, só Python.** Em Python, `NameError` num teste nunca é consertado por código de produção. Importar um símbolo que ainda não existe já dava erro de coleta, que já era recusado; `AttributeError` continua valendo como vermelho, porque é o que acontece quando o código ainda não foi escrito. Limite: teste que usa `from módulo import *` e chama a função nova falha com `NameError` e é recusado. O leitor de TypeScript não guarda a mensagem da falha; `ReferenceError` em JavaScript puro continua valendo como vermelho.
- **Item 26, o `doctor` não mudou.** `forja doctor` ainda lista o `golangci-lint` como opcional, e agora a falta dele para a tarefa de Go na política padrão. O guia de instalação diz isso; a regra do `doctor` ficou como estava.
- **Item 27, celular sem separador.** O padrão novo pega 11 dígitos seguidos com DDD válido e 9 na terceira posição. Um identificador numérico com essa forma passa a ser mascarado no pacote e contado pelo check de dados de teste.
- **Item 11, a sonda custa uma chamada.** Cada pergunta "a rede voltou?" é uma prova de vida da CLI. Sem rede ela falha sem gastar tokens; quando a rede volta, gasta os de uma tarefa trivial, que não entram no medidor de tokens da execução.
- **Achado novo: a Forja não passa nos próprios testes em Python 3.13.** `tests/ui/test_panels_exact.py::test_the_panels_say_what_they_must_at_every_size` falha em 3.13: a quebra de linha dos painéis deixa de ter um espaço no fim de algumas linhas. O `pyproject.toml` e o README dizem "3.12+". Não corrigido.

### Dificuldades

- Quatro vezes um script embutido no shell errou barras invertidas, não achou o trecho a trocar ou nem foi lido pelo shell. Texto com barra invertida ou com muitas aspas passou a ser gravado pela ferramenta de escrita.
- O `uv` criou o ambiente do checkout separado com Python 3.13, e a mutação parou no primeiro passo (os testes precisam passar antes). Recriado com `--python 3.12`.
- Os testes do item 27 não foram rodados contra o código anterior: o arquivo de teste importa uma função nova e nem chega a ser coletado sem ela.

### Pendências

- **Decisões do dono:** confirmar o ADR 0041 (item 24); item 29, estender dependência nova às outras linguagens ou manter; item 5, as três decisões da primeira leva.
- **Verificação que falta:** suíte completa com `slow` na ponta; mutação dos itens 27 e 11; ler a rodada de mutação dos itens 22 a 26.
- **Python 3.13:** o teste de painéis.
- **Itens 12 a 20 e 31 a 40** (o 30 adiado), e os restos dos itens 2 e 6.

### Para retomar numa sessão nova

- **Estado:** branch `correcoes-p0`, árvore limpa, nada enviado a remoto. A lista dos itens está em `AVALIACAO.md`; o que cada um virou, neste diário.
- **Seguinte:** item 12, depois 13 a 20, 31 a 40 (o 30 adiado) e os restos dos itens 2 e 6. Um commit por item, com teste, e a entrada no diário antes do commit do diário.
- **Java nesta máquina:** antes de rodar testes, definir `JAVA_HOME` como `C:\Program Files\Eclipse Adoptium\jdk-21.0.13.9-hotspot` e pôr o `bin` dele na frente do `PATH`, só no comando. Sem isso doze testes de Java falham.
- **Comandos de verificação:** `uv run ruff check .`, `uv run mypy`, `uv run pytest -q -m "not slow" -n auto -p no:cacheprovider` (cerca de 4 min), a mesma linha sem `-m "not slow"` (cerca de 30 min) e `uv run python scripts/mutate.py <arquivos>`.
- **Checkout de verificação:** `%LOCALAPPDATA%\forja-verif` é um worktree do git, destacado, com ambiente próprio em Python 3.12 (`uv sync --python 3.12`). Serve para rodar a mutação sem travar a árvore principal: `git -C <caminho> checkout --detach <commit>` e rodar lá. Para removê-lo: `git worktree remove`.
- **Mutação em andamento ao fim desta sessão:** `scripts/mutate.py` no checkout de verificação, no commit `f446df4`, sobre `checks/mutation.py`, `checks/layout.py`, `checks/build_and_tests.py`, `checks/red_green.py`, `languages/python/parsers.py`, `languages/python/mypy_config.py`, `languages/python/adapter.py` e `languages/go/adapter.py`. O resultado não foi lido. Uma sessão nova não é avisada do fim: é preciso rodar de novo e ler. Saída 3 quer dizer mutante sobrevivente, e a correção é um teste que o mate.
- **Cuidados que custaram tempo:** não editar `src` nem `tests` enquanto a suíte roda na mesma árvore; gravar pela ferramenta de escrita todo texto com barra invertida ou muitas aspas, não por script embutido no shell; cortar a saída dos comandos (`tail`, `grep`) antes de ler.

## 13/11/2026 — item 41: o gate de mutação mais rápido, com os mesmos vereditos

Item novo, pedido pelo dono em 13/11, fora dos 40 da avaliação. Só `scripts/mutate.py` mudou; o motor (`forja.mutation`) e as regras do `unclebob/mutator` ficaram como estavam.

### O que foi feito

| Mudança | Como | Commit |
| --- | --- | --- |
| Cobertura em paralelo | Os arquivos de teste são divididos em até `--workers` rodadas simultâneas sob `coverage`, e os dados são unidos com `coverage combine`. Cada teste roda uma vez. | `9782e97` |
| Base em paralelo | A rodada na cópia sem mutação, em que todos os testes do arquivo precisam passar, usa `-n<workers>`. Cada mutante continua num processo só. | `9782e97` |
| Testes pela linha mutada | Cada mutante é julgado primeiro pelos arquivos de teste que executam a linha dele. O que sobrevive é julgado de novo por todos os testes do arquivo antes de contar como sobrevivente. | `9782e97` |
| Leitura do relatório | O arquivo de teste de cada contexto de cobertura é resolvido uma vez (`functools.cache`). | `9782e97` |
| Andamento | Saída linha a linha mesmo em arquivo ou pipe; uma linha ao começar cada arquivo, com o número de mutantes, e outra ao terminar. | `9782e97` |

Teste novo: `tests/mutation/test_mutate_script.py`, 19 casos. Um deles roda mutantes reais num projeto de dois arquivos e compara o veredito da rodada estreita com o da rodada inteira.

### Medições

Mesma máquina, 8 workers. As rodadas novas disputaram CPU com uma rodada antiga que ainda estava em andamento, então os números novos são pessimistas.

| Parte | Antes | Depois |
| --- | --- | --- |
| Cobertura por teste | 1188 s a 1422 s | 374 s |
| Leitura da cobertura reaproveitada | 106 s | 6 s |
| `checks/scope.py` (40 mutantes) | 552 s | 116 s |
| `languages/go/adapter.py` (16) | 90 s | 38 s |
| `orchestrator/measurer.py` (86) | 2732 s | 1351 s |
| `checks/mutation.py` (37) | 848 s | 396 s |
| `languages/python/mypy_config.py` (24) | 690 s | 88 s |
| `languages/python/adapter.py` (24) | 1041 s | 492 s |

Vereditos iguais em todos os arquivos medidos nas duas versões.

### Conclusões

- **Minha primeira explicação estava errada.** Eu disse que o tempo ia em rodar, para cada mutante, todos os testes do arquivo. Medido: dos 563 s de `checks/scope.py`, 489 s eram a rodada de base (29 arquivos de teste em série, uma vez) e cerca de 70 s os 40 mutantes. O script já usava `-x`, que para no primeiro teste que falha, então mutante morto nunca rodava os 29 arquivos. A escolha por linha sozinha deixou `scope.py` em 563 s; foi a base em paralelo que o levou a 116 s.
- **A escolha por linha precisa da reconferência.** O `coverage` troca de contexto quando a função de teste começa, e o que roda antes disso, na preparação dos fixtures, fica na conta do teste anterior. Uma linha pode então estar atribuída ao teste errado. Por isso o mutante que sobrevive à rodada estreita é julgado de novo pelo conjunto inteiro: morto pelo subconjunto é morto pelo conjunto, que o contém, e sobrevivente só conta depois do conjunto inteiro.
- **O limite de tempo do mutante não encolheu.** O executor dá dez vezes a duração da base. Com a base em paralelo esse número cairia, e um mutante lento seria contado como morto por tempo. O script dá no mínimo dez vezes o número de workers vezes a duração medida, que é pelo menos dez vezes o que um processo sozinho levaria.
- **A suíte depende da ordem de coleta.** A primeira versão distribuía os arquivos de teste por tamanho, e um grupo falhou com `fixture 'repo' not found` em `tests/core/locking/test_repository.py`. O `conftest.py` de `tests/core/locking` importa o fixture `repo` do `conftest.py` de `tests/core/idempotency`, e isso só funciona na ordem dos nomes. Reproduzido com o mesmo grupo de 35 arquivos nas duas ordens. O script passou a rodar cada grupo na ordem dos nomes; a dependência continua na suíte.
- **A rodada antiga dos itens 22 a 26 terminou em 2h08 com dois sobreviventes,** os dois mortos por teste novo (`74a6824`): `MypyConfig.write` (nenhum teste gravava duas configurações na mesma pasta) e `PythonAdapter._plan` (nenhum teste conferia que só os testes recebem `PYTHONPATH`; código antigo). Conferido com o script novo: os dois arquivos passam.
- **`measurer.py` ainda leva 22 min.** São 86 mutantes e os testes que os julgam rodam pipelines inteiros, de 15 a 30 s por arquivo de teste. Não investigado além disso.

### Dificuldades

- Uma hora sem ver o andamento da rodada antiga, porque a saída passava por um filtro que só gravava no fim. Foi isso que motivou a saída linha a linha.
- `ruff format scripts` reformatou `scripts/capturas/terminal.py`, que não era da mudança. Desfeito; o arquivo continua para o item 40.

### Verificação

- `ruff check .` e `mypy` strict limpos, 618 arquivos. `pytest -m "not slow" -n auto`: 9545 aprovados, 41 pulados.
- Mutação, com o script novo, na ponta: `checks/scope.py`, `languages/go/adapter.py`, `orchestrator/measurer.py`, `checks/mutation.py`, `languages/python/mypy_config.py` e `languages/python/adapter.py`, todos sem sobrevivente. Ficam 12 pontos de `checks/mutation.py` e 2 de `mypy_config.py` em linhas que nenhum teste executa.
- Ainda sem mutação: `checks/layout.py`, `checks/build_and_tests.py`, `checks/red_green.py` e `languages/python/parsers.py` passaram na rodada antiga, no commit `f446df4`; os arquivos dos itens 27 e 11 não passaram por nenhuma.
- Suíte completa com `slow` na ponta: não rodou.

### Para retomar

Vale o que está em "Para retomar numa sessão nova", com duas diferenças: não há mais mutação em andamento, e `scripts/mutate.py` agora mostra o andamento. `--reuse-coverage` continua valendo só enquanto nem os testes nem as linhas do arquivo-fonte mudaram desde a cobertura: com cobertura velha, um mutante pode ser dado como "linha sem teste" e não rodar. Seguinte: mutação dos itens 27 e 11, suíte completa com `slow`, e o item 12.

## 13/11/2026 — item 12: a refatoração começa pelos hotspots e escreve testes antes onde a rede é fraca

### O que foi feito

| Mudança | Arquivos | Teste novo | Commit |
| --- | --- | --- | --- |
| Prioridade por hotspot. As funções acima do limite são ordenadas por complexidade vezes o número de commits que mudaram o arquivo (`git log --format= --name-only <commit base>`). Empate: a mais complexa, depois arquivo e nome. | `orchestrator/planner.py`, `cli/session.py` | seis em `tests/orchestrator/test_planner.py`, dois em `tests/cli/test_session_open.py` | `52a703f` |
| Rede fraca. Para as funções que o orçamento de tarefas alcança, `PipelineMeasurer.weak_nets` diz quais os testes não protegem, pelo que o gate 7 pede de linha nova: linha sem teste, ou nota de mutação abaixo do `min_score` da política. A conta é de `MutationCheck.protects`, sobre as linhas da função (as que o `lizard` dá). | `orchestrator/measurer.py`, `checks/mutation.py` | `tests/checks/test_characterization.py`, 7 casos, dois `slow` com mutação real | `52a703f` |
| Etapa de caracterização. Tarefa de função com rede fraca ganha o autor de testes antes do implementador. Os testes dele dizem o que o código faz hoje: precisam passar (`TestsCheck` no lugar do `RedGreenCheck`), não respondem a critério (sem manifesto) e ficam congelados. O implementador continua com as instruções de refatoração. `TaskInfo.characterizes` leva isso a todas as etapas da tarefa. | `orchestrator/task_cycle.py`, `orchestrator/ports.py`, `orchestrator/agent_runner.py`, `orchestrator/measurer.py` | `tests/orchestrator/test_characterization.py` (6), três em `tests/executors/test_package.py`, um em `test_agent_runner_gaps.py` | `52a703f` |
| Guia. `comandos.md` diz a ordem e a etapa a mais; `clis.md` diz quem escreve esses testes. | `docs/guia/comandos.md`, `docs/guia/clis.md` | nenhum | `52a703f` |
| Dois mutantes do item 27 sobreviviam: o tamanho a partir do qual uma linha do `lizard` é linha do relatório (`complexity.py:98`) e o trecho de README sem título valendo por uma seção (`readme.py:162`). | `tests/checks/test_detectors_that_looked_away.py` | os dois | `8c05462` |

### Verificação

- `ruff check .`, `ruff format --check src tests` e `mypy` strict: limpos, 620 arquivos.
- `pytest -m "not slow" -n auto` com as mudanças do item 12: 9569 aprovados, 41 pulados, nenhuma falha, 306 s. Os dois testes `slow` novos passaram à parte.
- **Duas rodadas foram interrompidas pelo Claude Code por falta de memória na máquina, e não foram refeitas:**
  - Suíte completa com `slow` em `52a703f`: chegou a cerca de 98% sem nenhuma falha impressa e parou com um worker derrubado. Não vale como rodada completa.
  - Mutação dos itens 27 e 11, no checkout de verificação, em `9e7df06`: julgou `checks/complexity.py` (1 sobrevivente), `checks/readme.py` (1 sobrevivente) e `core/redaction/pii.py` (nenhum), e parou em `checks/voice_markers.py`. Não rodaram: `checks/voice_markers.py`, `checks/voice.py`, `core/adapters/common.py`, os `parsers.py` de C# e Java, `cli/session.py`, `orchestrator/ports.py` e `orchestrator/task_cycle.py`.
- Os dois sobreviventes foram mortos por teste novo, conferido à mão: com cada mutação aplicada o teste novo falha, e sem ela passa. O script de mutação não foi rodado de novo sobre esses dois arquivos.
- Mutação dos arquivos do item 12: não rodou.
- Não rodou: E2E com agente real.

### Conclusões

- **Rede fraca é o que o gate 7 pede de linha nova.** Pela conta do mutator, mutante em linha sem cobertura fica fora da nota; uma função sem teste nenhum teria nota vazia e passaria por protegida. Por isso linha sem teste também conta como rede fraca.
- **O que não pode ser medido não é chamado de fraco.** Linguagem sem cobertura, arquivo que as regras de mutação não leem, testes que não rodam: a tarefa fica sem a etapa. Limite: repositório sem nenhum teste cai aqui (a cobertura sai com erro), e nenhuma função dele ganha caracterização.
- **Custo.** A medição roda antes da primeira tarefa: uma cobertura por linguagem e, para cada função escolhida (até `tarefas`), um mutante de cada vez contra a suíte inteira, como no gate. Não foi medido num repositório grande.
- **O churn conta até o commit base,** para o commit `style:` que a própria Forja faz não entrar. Renomeação não é seguida.
- **Quem escreve os testes numa refatoração é a CLI do implementador.** A preparação só lê `cli_testes` em `corrigir` e `criar`; isso não mudou.
- **O assunto do commit dos testes** é `test: before the change: <intenção>`. Um prefixo mais longo cortava o nome do arquivo no limite de 72 caracteres.
- **Lido no código, não reproduzido:** antes, numa refatoração, a etapa extra de testes (linha nova sem teste) usava o runner do implementador, porque a sessão não criava o do autor de testes. Agora a refatoração tem um.

### Dificuldades

- Um script embutido no shell trocou `\n` por quebra de linha dentro de uma string de teste, o mesmo erro das sessões anteriores. Corrigido pela ferramenta de edição.
- As duas rodadas longas correram ao mesmo tempo que a suíte rápida, e a máquina ficou sem memória.

### Pendências

- **Refazer, uma de cada vez:** suíte completa com `slow` na ponta; mutação dos arquivos dos itens 27 e 11 que não rodaram; mutação dos arquivos do item 12 (`orchestrator/planner.py`, `checks/mutation.py`, `orchestrator/task_cycle.py`, `orchestrator/agent_runner.py`, `orchestrator/measurer.py`, `orchestrator/ports.py`, `cli/session.py`).
- **Observação para o dono:** a etapa de caracterização é aprovada quando os testes passam. Ninguém confere se a nota de mutação da função subiu. Se os testes forem fracos, o implementador é reprovado no gate 7 com os testes congelados, como já acontecia sem a etapa.
- **Item 13 em diante:** não iniciado. O item 14 pede decisão do dono entre implementar e cortar da Especificação.

## 13/11/2026 — as três rodadas longas, uma de cada vez

Pedido do dono, depois da interrupção por falta de memória. Rodaram em sequência, nunca duas ao mesmo tempo.

### Resultados

| Rodada | Commit | Resultado |
| --- | --- | --- |
| Suíte completa com `slow`, `pytest -n auto`, `JAVA_HOME` no JDK 21 | `9797991` | 9780 aprovados, 41 pulados, nenhuma falha, 29 min 44 s |
| Mutação dos itens 27 e 11 (10 arquivos) | `9797991` | saída 3, 24 sobreviventes |
| Mutação do que faltava do item 12 (4 arquivos) | `9797991` | saída 3, 5 sobreviventes |

Sem sobrevivente: `checks/complexity.py`, `checks/readme.py` (os dois testes de `8c05462` mataram os da rodada interrompida), `checks/voice_markers.py`, `checks/voice.py`, `cli/session.py`, `orchestrator/ports.py`, `checks/mutation.py` e `orchestrator/agent_runner.py`.

### Sobreviventes do item 12: resolvidos

| Mutante | O que foi feito |
| --- | --- |
| `orchestrator/planner.py:53`, `1 -> 0` | Equivalente: sem churn, peso 1 ou 0 para todos dá a mesma ordem, a da complexidade. O peso sem churn passou a ser a própria complexidade, e o literal saiu. |
| `orchestrator/measurer.py:669`, `:697` (dois) e `:698`, em `weak_nets` | Só os testes `slow` chegavam à medição, e o gate de mutação usa os rápidos. Dois testes rápidos novos em `tests/checks/test_characterization.py` põem um substituto no lugar de `MutationCheck.protects` e conferem as linhas pedidas e a resposta. Conferido à mão: com cada uma das quatro mutações aplicada, o arquivo de teste falha. |

O script de mutação não foi rodado de novo sobre `planner.py` e `measurer.py` depois da correção (`measurer.py` leva 35 min).

### Sobreviventes dos itens 27 e 11: abertos

| Arquivo | Mutantes |
| --- | --- |
| `core/adapters/common.py` | `:143`, três, em `keep_worst` (código do item 27) |
| `languages/csharp/parsers.py` | `:30` `_lines`, `:40` `_path`, `:60` `parse_format`, `:74` `parse_lint` |
| `languages/java/parsers.py` | `:39` `_lines`, `:48` `_path`, `:73`, `:74` (dois) e `:75` `_reports`, `:87` (dois) `parse_format`; mais 3 pontos em linhas sem teste |
| `orchestrator/task_cycle.py` | `:411` (dois) `_call_agent`, `:470` `_judge`, `:873`, `:875` (dois), `:876` (dois) e `:878` `_within_event_limit` |

Só os três de `keep_worst` são de código escrito nesta leva. Os outros 21 estão em funções antigas dos mesmos arquivos, que nunca tinham passado pela mutação.

### Conclusões

- **`checks/mutation.py` tem 18 pontos em linhas que nenhum teste rápido executa** (eram 12). Os seis novos são de `protects`, que só os dois testes `slow` exercitam.
- **Rodar uma de cada vez resolveu a falta de memória.** Nenhuma das três foi interrompida.

### Pendências

- **24 mutantes abertos** da tabela acima: um teste que mate cada um, ou a prova de que é equivalente.
- Rodar a mutação de novo em `planner.py` e `measurer.py`.
- **Observação para o dono**, da entrada anterior: a etapa de caracterização não é julgada pela nota de mutação.
- **Item 13 em diante:** não iniciado. O item 14 pede decisão do dono.

## 13/11/2026 — os 24 mutantes abertos, o item 13 e duas decisões do dono

### Decisões do dono

- **Item 13, tempo:** cortar da Especificação. Vale o ADR 0016: o orçamento é de tokens e o tempo é só exibido. As três frases da Especificação que falavam em orçamento de tempo passaram a dizer tokens.
- **Item 14:** implementar tudo (trailers `Evidence-Id`, `Assumption` e métricas, `git notes` e validação de Conventional Commits). Não iniciado.

### O que foi feito

| Item | Mudança | Arquivos | Teste novo | Commit |
| --- | --- | --- | --- | --- |
| mutantes | Os 24 sobreviventes da entrada anterior. 22 ganharam teste; 2 não mudavam nada e saíram por reescrita: o nome de um caminho fora do projeto, em C# e Java, é tirado com `rpartition` (o `[-1]` valia o mesmo que `[1]`), e o agrupamento das linhas de rastreio começa de um grupo cheio (o `size = 0` inicial nunca era lido). | `languages/csharp/parsers.py`, `languages/java/parsers.py`, `orchestrator/task_cycle.py` | três casos em `test_detectors_that_looked_away.py`, três testes em `tests/languages/csharp/test_adapter.py`, seis em `tests/languages/java/test_adapter.py`, dois em `test_test_manifest.py`, um em `test_together.py` | `e90e190` |
| 13 | Antes de cada tentativa nova a execução pergunta se há espaço em disco, no disco do repositório e no da pasta da Forja. Com menos de 2 GiB livres a etapa fica `infra_paused` e a execução pausa com o motivo `the disk is nearly full`; `forja` retoma. O limite ficou num só lugar (`cli/session.py`); `detect.py` e `prepare.py` tinham cada um o seu. | `orchestrator/ports.py`, `orchestrator/task_cycle.py`, `cli/session.py`, `cli/detect.py`, `cli/prepare.py`, `cli/doctor.py`, guias `execucao-toml.md` e `paralelismo.md`, `docs/Especificação.md` | um em `test_phases.py`, um em `test_session_open.py` | `f30bb6e` |

### Conclusões

- **Os 22 mutantes foram conferidos à mão,** como na entrada anterior: com cada mutação aplicada o arquivo de teste falha, e sem ela passa. O script de mutação não rodou de novo sobre `common.py`, os dois `parsers.py` e `task_cycle.py`.
- **O teste do `_path` de Java cobre uma linha que antes nenhum teste rodava** (o nome do arquivo fora do projeto). Era um dos "3 pontos em linhas sem teste" do arquivo.
- **`_reports` de Java:** uma pasta com nome de relatório (`TEST-x.xml`) é o caso que separa as quatro condições sem precisar de link simbólico, que no Windows pede privilégio.
- **Item 13, a limpeza não é barrada.** A pergunta do disco é feita só antes de criar uma tentativa nova; remover o worktree de uma tentativa anterior, que libera espaço, continua acontecendo.
- **Item 13, sem espera.** A Especificação aceita "aguarda e retoma sozinha, ou para com estado gravado". Ficou a segunda: ninguém libera disco sozinho.

### Verificação

- `ruff check .`, `ruff format --check src tests` e `mypy` strict: limpos, 620 arquivos.
- `pytest -m "not slow" -n 6`: 9588 aprovados e 41 pulados em `e90e190`; 9590 aprovados e 41 pulados em `f30bb6e`. Nenhuma falha.
- Mutação de `orchestrator/planner.py` e `orchestrator/measurer.py`, em `d61fcc7`, no checkout de verificação: saída 0, nenhum sobrevivente (8 e 93 mutantes; `measurer.py` levou 28 min). Fecha a pendência da entrada anterior.
- Não rodou: suíte completa com `slow` na ponta; mutação dos arquivos tocados nesta entrada (`common.py`, os `parsers.py` de C# e Java, `task_cycle.py`, `ports.py`, `cli/session.py`, `cli/detect.py`, `cli/prepare.py`, `cli/doctor.py`); E2E com agente real.

### Dificuldades

- `cli/session.py` não pode importar o limite de disco de `cli/detect.py`: `detect` já importa de `session`. O limite foi para `session`.
- A suíte rápida rodou junto com a mutação, com 6 workers em vez de `-n auto`, e a memória bastou (13,9 GiB livres de 31,2 com a mutação em andamento).

### Pendências

- **Item 14:** implementar tudo, por decisão do dono. Não iniciado.
- **Itens 15 a 20 e 31 a 40** (o 30 adiado), e os restos dos itens 2 e 6.
- **Verificação que falta:** a da lista acima.
- **Decisões do dono ainda abertas:** ADR 0041 (item 24); item 29; as três do item 5; a observação sobre a etapa de caracterização (item 12).
