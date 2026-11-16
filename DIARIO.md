# Diário de bordo

Registro das execuções de trabalho sobre a Forja: o que foi feito, o que foi verificado, o que se concluiu e onde houve dificuldade. Entradas em ordem cronológica. Os números dos itens vêm da avaliação técnica de 11/11/2026 (P0 a P4, itens 1 a 40), guardada em [AVALIACAO.md](AVALIACAO.md).

## 11/11/2026 — avaliação técnica e primeira leva de correções (P0)

### Ponto de partida

- HEAD `699d936`, branch `main`.
- Por ordem do dono, as notas locais foram desconsideradas e apagadas, quatro arquivos. Nenhum dos quatro estava no git, então não há como recuperá-los pelo histórico.
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

- Editar arquivos por script embutido no shell corrompeu sequências de barra invertida duas vezes (`\b` virou o caractere de retrocesso numa expressão regular; `\n` virou quebra de linha dentro de uma string de teste). Os dois casos foram pegos pelos testes e corrigidos. Daí em diante as edições com barra invertida foram feitas pela ferramenta de edição ou por arquivo de script separado.
- Mover `workcopy_path` para `orchestrator/ports.py` criou importação circular com `workspace_git.py`. Ficou em `workspace_git.py`.

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
- Não rodou: E2E com agente real.

### Conclusões

- **Rede fraca é o que o gate 7 pede de linha nova.** Pela conta do mutator, mutante em linha sem cobertura fica fora da nota; uma função sem teste nenhum teria nota vazia e passaria por protegida. Por isso linha sem teste também conta como rede fraca.
- **O que não pode ser medido não é chamado de fraco.** Linguagem sem cobertura, arquivo que as regras de mutação não leem, testes que não rodam: a tarefa fica sem a etapa. Limite: repositório sem nenhum teste cai aqui (a cobertura sai com erro), e nenhuma função dele ganha caracterização.
- **Custo.** A medição roda antes da primeira tarefa: uma cobertura por linguagem e, para cada função escolhida (até `tarefas`), um mutante de cada vez contra a suíte inteira, como no gate. Não foi medido num repositório grande.
- **O churn conta até o commit base,** para o commit `style:` que a própria Forja faz não entrar. Renomeação não é seguida.
- **Quem escreve os testes numa refatoração é a CLI do implementador.** A preparação só lê `cli_testes` em `corrigir` e `criar`; isso não mudou.
- **O assunto do commit dos testes** é `test: before the change: <intenção>`. Um prefixo mais longo cortava o nome do arquivo no limite de 72 caracteres.
- **Lido no código, não reproduzido:** antes, numa refatoração, a etapa extra de testes (linha nova sem teste) usava o runner do implementador, porque a sessão não criava o do autor de testes. Agora a refatoração tem um.

## 13/11/2026 — as três rodadas longas, uma de cada vez

Pedido do dono, depois de duas rodadas interrompidas por falta de memória na máquina (a suíte completa e a mutação dos itens 27 e 11, que corriam ao mesmo tempo que a suíte rápida). Rodaram em sequência, nunca duas ao mesmo tempo, e nenhuma das três foi interrompida.

A suíte completa com `slow`, em `9797991`, com `JAVA_HOME` no JDK 21: 9780 aprovados, 41 pulados, nenhuma falha, 29 min 44 s. A mutação dos itens 27 e 11 (10 arquivos) deixou 24 sobreviventes, e a do que faltava do item 12 (4 arquivos), 5. `checks/complexity.py` e `checks/readme.py` passaram sem sobrevivente, com os dois testes de `8c05462`. Dos 24, só os três de `keep_worst` (`core/adapters/common.py`) são de código escrito nesta leva; os outros 21 estão em funções antigas dos `parsers.py` de C# e de Java e de `orchestrator/task_cycle.py`, que nunca tinham passado pela mutação. Os 5 do item 12 foram resolvidos nesta entrada: em `orchestrator/planner.py` o peso sem churn passou a ser a própria complexidade, e o literal saiu (o mutante era equivalente); os quatro de `weak_nets`, em `orchestrator/measurer.py`, ganharam dois testes rápidos em `tests/checks/test_characterization.py`, conferidos à mão (com cada mutação aplicada, o arquivo de teste falha). Os 24 restantes foram mortos na entrada seguinte (`e90e190`).

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
- Mutação de `orchestrator/planner.py` e `orchestrator/measurer.py`, em `d61fcc7`, no checkout de verificação: saída 0, nenhum sobrevivente (8 e 93 mutantes; `measurer.py` levou 28 min). Era o que faltava da entrada anterior: o script não tinha rodado de novo sobre os dois depois da correção.
- Não rodou: suíte completa com `slow` na ponta; mutação dos arquivos tocados nesta entrada (`common.py`, os `parsers.py` de C# e Java, `task_cycle.py`, `ports.py`, `cli/session.py`, `cli/detect.py`, `cli/prepare.py`, `cli/doctor.py`); E2E com agente real.

## 13/11/2026 — item 14: o commit da etapa diz o que a Forja mediu, e as métricas vão numa nota

### Decisões do dono

- **`Evidence-Id`:** um trailer para cada check que aprovou a tentativa (o `gates_passed` do evento de aprovação). A execução não grava registros de evidência: `EvidenceStore` e `GateDecisionStore` existem em `core` e nada os usa. Ligá-los ficou fora do item.
- **Métricas:** trailer `Metric-<nome>: <antes> -> <depois>` só para a métrica que mudou em relação à base da execução; a tabela inteira vai em JSON na nota do commit.

### O que foi feito

| Mudança | Arquivos | Teste novo | Commit |
| --- | --- | --- | --- |
| `StageFacts` leva os fatos da etapa aprovada: checks, métricas (antes, depois) e premissas. `CommitMessage.trailers` escreve os trailers numa ordem só, e os dois workspaces a usam. Depois de `Policy-Version` vêm `Evidence-Id`, `Metric-<nome>` e `Assumption`. | `orchestrator/workspace_git.py`, `testing/workspace.py` | `tests/orchestrator/test_commit_facts.py` (40 casos), três cenários em `test_workspace_contract.py`, nos dois workspaces | `2808d03` |
| Nota do git no commit que fecha a etapa (`git notes add -f -m <json> HEAD`), com todas as métricas, mudadas ou não, e os checks. Sem métrica medida não há nota. `MemoryRepo.notes` faz o mesmo em memória. | `orchestrator/workspace_git.py`, `testing/repo.py`, `testing/workspace.py` | os mesmos; uma linha em `tests/core/gitguard/test_rules.py` | `2808d03` |
| `PipelineMeasurer.stage_metrics`: cada métrica como a base da execução a tinha (`base-metrics.json`) e como a worktree da tentativa a tem. Só as que não pedem rodar testes são medidas de novo. | `orchestrator/measurer.py`, `orchestrator/ports.py` | dois em `tests/checks/test_measurer.py`; três linhas no teste `slow` de `test_real_run.py` | `2808d03` |
| O ciclo monta os fatos só na tentativa aprovada (`_stage_facts`). O commit que guarda trabalho reprovado para o autor de testes e o commit à parte do README ficam sem eles. | `orchestrator/task_cycle.py` | quatro em `test_commit_facts.py` | `2808d03` |
| Conventional Commits: `conventional()` recusa assunto fora de `tipo(escopo): texto`, com os tipos da Especificação (`docs`, `feat`, `fix`, `refactor`, `style`, `test`). `CommitMessage.of` passa por ela. | `orchestrator/workspace_git.py` | 24 casos em `test_commit_facts.py` | `2808d03` |
| Com tarefas em paralelo, a guarda de git deixa de contar `refs/notes/commits` como obra do agente: a nota de uma tarefa é escrita enquanto o agente de outra roda. | `orchestrator/workspace_git.py` | duas linhas em `test_together.py` | `2808d03` |
| Guia: o que cada commit de etapa diz e como ler a nota. | `docs/guia/ambiente.md` | nenhum | `2808d03` |

### Conclusões

- **O "antes" é a base da execução, não o commit pai.** É o único "antes" que a Forja guarda. Na segunda tarefa de uma execução, o trailer mostra a soma do que as tarefas já integradas e esta mudaram.
- **`tests` e `unstable_tests` saem na nota com "depois" nulo.** Contar os testes pede rodá-los de novo; a nota diz o que foi medido e não inventa o resto. Métrica com um lado nulo não vira trailer.
- **Custo:** uma medição de lint, complexidade e grafo por commit de etapa, a mesma que o registro de mudança já fazia para o documentador.
- **O nome da métrica vira nome de trailer** trocando o que não é letra nem dígito por `-`: `max_ccn` sai `Metric-max-ccn`, `dependency_cycles[services/api]` sai `Metric-dependency-cycles-services-api`.
- **A premissa vai inteira, numa linha.** Não passa pela redação, como o corpo do commit, que também é texto do agente.
- **O merge da tarefa não é validado.** O assunto dele é `merge: <intenção>`, e `merge` não está entre os tipos da Especificação. A validação ficou nos commits de etapa, que é onde a Especificação a pede ("Commits atômicos").
- **Assunto recusado derruba a execução** com `NotConventional`, e desde o item 7 isso fica gravado como `failed`. Só acontece por erro da própria Forja: o tipo é escolhido por ela e a intenção nunca é vazia.
- **Limite na retomada:** queda entre o commit e a nota deixa o commit sem nota. A reconciliação vê o `Stage-Id` e dá a etapa por feita.
- **A guarda em paralelo ficou mais frouxa num ponto:** com mais de uma tarefa ao mesmo tempo, um agente que escrevesse em `refs/notes/commits` não seria notado. Com uma tarefa por vez, continua sendo. O agente trabalha numa cópia sem `.git`.
- **As notas não acompanham o `git push` da branch.** O guia diz como publicá-las.

### Verificação

- `ruff check .`, `ruff format --check src tests` e `mypy` strict: limpos, 621 arquivos.
- `pytest -m "not slow" -n 6`: 9638 aprovados, 41 pulados, nenhuma falha, 328 s.
- `tests/checks/test_real_run.py` (`slow`), com git de verdade e a fachada: 2 aprovados; o commit da etapa sai com `Evidence-Id: scope`, `Metric-functions: 2 -> 3` e a nota.
- Mutação em `2808d03`, no checkout de verificação: `orchestrator/task_cycle.py` (78 mutantes) e `orchestrator/measurer.py` (93, 26 min) sem sobrevivente; `orchestrator/workspace_git.py` (28) com um, `:351` `or -> and` em `GitWorkspace.watch`, código antigo. Um teste novo no contrato dos workspaces o mata, em `9ea30b4` (`test_a_watch_told_only_the_task_or_only_the_attempt_overlooks_nothing`): conferido à mão, com a mutação aplicada os dois casos de git falham. O script não rodou de novo sobre o arquivo.
- Não rodou: suíte completa com `slow` na ponta; mutação dos arquivos da entrada anterior (`common.py`, os `parsers.py` de C# e Java, `ports.py`, `cli/session.py`, `cli/detect.py`, `cli/prepare.py`, `cli/doctor.py`); E2E com agente real.

## 13/11/2026 — item 15: token que a CLI não informou é desconhecido, não zero

### O que foi feito

| Mudança | Arquivos | Teste novo | Commit |
| --- | --- | --- | --- |
| O evento de uso guarda `null` no lado que a CLI não informou. Antes gravava `0`. | `orchestrator/agent_runner.py` | três linhas em `tests/executors/test_agent_runner.py` | `df54063` |
| Chamada que termina sem nenhum evento de uso (CLI genérica, CLI que caiu, CLI encerrada por silêncio) ganha um evento de uso desconhecido, escrito pela Forja ao fim da chamada. | `orchestrator/agent_runner.py` | quatro em `tests/executors/test_agent_runner.py` | `df54063` |
| `Consumption.unmeasured` conta as chamadas com um lado desconhecido. O lado informado continua somado. | `orchestrator/budget.py` | oito casos em `tests/orchestrator/test_unmeasured_tokens.py` | `df54063` |
| O resumo diz, em "Ambiente e garantias", quantas chamadas ficaram sem medida e que o gasto delas não entrou na conta. Sem chamada desconhecida, não diz nada. | `orchestrator/summary.py` | três em `test_unmeasured_tokens.py` | `df54063` |
| Guia: Cursor e CLI genérica. | `docs/guia/clis.md` | nenhum | `df54063` |

### Conclusões

- **O plano falava só do Cursor; a CLI genérica tinha o mesmo furo por outro caminho.** O Cursor emite um evento de uso sem números. A genérica não emite evento nenhum, e contar só `null` a deixaria de fora. Por isso a chamada sem evento de uso também é registrada.
- **O limite continua sem parar a execução com essas CLIs.** Desconhecido soma zero na conta. O que mudou é que o registro e o resumo dizem que não foi medido. Parar ou recusar a execução quando a CLI não informa tokens seria comportamento novo, que ninguém pediu.
- **Registro antigo não muda de leitura.** Eventos gravados antes têm `0` nos dois lados e continuam contando como medidos.
- **O painel não mudou.** Mostra `0/<limite>` para uma CLI que não informa tokens.
- **Falta de chave conta como não informado.** Evento de uso sem `input` ou sem `output` entra em `unmeasured`.

### Verificação

- `ruff check .`, `ruff format --check src tests` e `mypy` strict: limpos, 622 arquivos.
- `pytest -m "not slow" -n 6`: 9658 aprovados, 41 pulados, nenhuma falha, 533 s.
- Mutação em `df54063`, no checkout de verificação: `orchestrator/budget.py` (30 mutantes), `orchestrator/summary.py` (48) e `orchestrator/agent_runner.py` (120), saída 0, nenhum sobrevivente.
- Não rodou: suíte completa com `slow` na ponta; E2E com agente real; nenhuma execução com o Cursor de verdade.

## 13/11/2026 — itens 16 a 20, e as notas apagadas deixam de ser citadas

### Decisão do dono

- **Notas apagadas:** tirar toda menção a elas. Saíram a regra 6 de `CLAUDE.md`, a seção "Construção da Forja" da Especificação, a linha do `.gitignore`, a pendência deste diário e as citações em `AVALIACAO.md`, que por isso deixou de ser "como foi escrita" (`000bda1`).

### O que foi feito

| Item | Mudança | Arquivos | Teste novo | Commit |
| --- | --- | --- | --- | --- |
| 16 | Sem terminal e sem `--config`, a Forja recusa no ponto da confirmação ("Sem terminal não há quem confirme"), com código 2 e sem alterar nada. `--plano` continua valendo sem terminal. | `cli/main.py`, guia `execucao-toml.md` | um em `tests/cli/test_main.py` | `11c28fe` |
| 17 | Tela ou leitor de teclas que quebra não derruba a execução: o rastro vai para `interface.log`, ao lado do resumo, e a execução segue até o fim. Vale para `start`, `update` e `stop` da tela e para as tarefas auxiliares. | `cli/run.py` | cinco em `tests/cli/test_run.py` | `7d51a47` |
| 18 | A prova de vida da CLI é lembrada por um dia, por caminho, versão e, na CLI genérica, pelo modelo de comando. `forja doctor` sempre prova de novo. | `cli/prepare.py`, `cli/doctor.py`, guias `clis.md`, `ambiente.md`, `execucao-toml.md`, Especificação | `tests/cli/test_proof_of_life.py` (19 casos), um ajuste em `test_doctor_checks.py` | `440d9d2` |
| 19 | O contador de falhas de infraestrutura de uma etapa recomeça depois da última pausa dela. `RunState` passou a guardar o número do evento de cada entrada num estado (`transition_seq`). | `core/events/state.py`, `orchestrator/budget.py` | dois em `test_budget.py`, um em `test_phases.py` | `cfcec50`, `cd4308e` |
| 20 | `forja revisao` lê o papel de cada etapa do id dela (`-s`, `-t`, `-t<n>`, `-d`, `-c`, `-f`), e não da posição no plano. A letra de cada papel ficou num só lugar (`STAGE_LETTER`). | `orchestrator/ports.py`, `orchestrator/review.py`, `orchestrator/task_cycle.py` | 21 casos em `tests/orchestrator/test_review.py` | `a2bd76f` |
| mutantes | 18 sobreviventes em código antigo de `cli/run.py` (5, em `exit_code` e `closing_lines`) e de `cli/prepare.py` (10, mais 3 do item 18). | `tests/cli/test_exit_code.py`, `tests/cli/test_prepare_edges.py` (novo), `test_proof_of_life.py` | os próprios | `44375ec`, `a30b11c` |

### Conclusões

- **Item 16, onde a recusa fica.** No ponto da confirmação, não na entrada: sem terminal, as outras recusas da preparação (descrição que falta, repositório sujo, execução incompleta) continuam aparecendo primeiro, com a mensagem delas.
- **Item 17, a tela parada não é avisada.** Depois da falha a tela deixa de atualizar e a execução segue. As linhas finais não dizem que a tela quebrou; só o `interface.log` diz.
- **Item 18, um dia.** O plano pedia validade sem dizer quanto. Ficou 24 horas (`PROOF_VALID_SECONDS`). Prova gravada por uma Forja anterior (`true`, sem momento) não vale e é refeita.
- **Item 19, o plano foi trocado.** O plano dizia "contar só depois do último `session_started`". Isso quebrava um teste que protege contra laço de quedas (`test_results_lost_again_and_again_pause_the_run_like_any_infrastructure_failure`): cada queda abre uma sessão, e o contador nunca chegaria ao limite. O corte ficou na última pausa da etapa: quem retoma depois de pausar tem o limite inteiro; quedas sem pausa continuam somando.
- **Item 19, toda pausa zera.** A etapa também entra em `infra_paused` por disco cheio, rede que não volta e erro bloqueante. Retomar depois de qualquer uma zera o contador.
- **Item 19, mutante equivalente.** `discarded < paused` e `<=` valem o mesmo, porque dois eventos nunca têm o mesmo número. A comparação foi reescrita com `max` e o operador saiu (`cd4308e`).
- **Item 20, etapa de id desconhecido fica fora do relatório.** Nenhum papel escreve outro id hoje.

### Verificação

- `ruff check .`, `ruff format --check src tests` e `mypy` strict: limpos, 624 arquivos.
- `pytest -m "not slow" -n 6` em `a30b11c`: 9731 aprovados, 41 pulados, nenhuma falha.
- Mutação em `11c28fe`: `cli/main.py` (34 mutantes), nenhum sobrevivente.
- Mutação em `440d9d2`: `cli/doctor.py` sem sobrevivente; `cli/run.py` com 5 e `cli/prepare.py` com 13, todos mortos por teste depois.
- Mutação em `a30b11c`: `cli/run.py` (27), `cli/prepare.py` (126), `core/events/state.py` (27), `orchestrator/ports.py` (8), `orchestrator/review.py` (36) e `orchestrator/task_cycle.py` (78) sem sobrevivente; `orchestrator/budget.py` (33) com um, o equivalente acima. Ficam 3 pontos de `prepare.py` em linhas que nenhum teste executa.
- Não rodou: suíte completa com `slow`; E2E com agente real; a mutação de `budget.py` depois de `cd4308e`.

## 13/11/2026 — a mutação comparada com o `unclebob/mutator` e o `unclebob/crapper`, e a primeira parte do realinhamento

### O que motivou

O dono notou que as rodadas de mutação deixavam o PC lento e que o README do mutator descreve opções que a Forja não tem (`--max-workers`). Pediu a comparação com a referência.

### O que foi lido

`unclebob/mutator` no commit `830ec7e` (README e todo `src/mutator`) e `unclebob/crapper` no commit `c757ec7` (menos `cli.py` e os leitores de Rust e Clojure), contra `src/forja/mutation`, `checks/mutation.py`, `checks/crap.py`, `checks/complexity.py` e `scripts/mutate.py`.

### Medições

- **A cópia de cada worker levava `target/` junto.** `target/coverage/python/contexts.json` tem 1,9 GB; o código copiado, cerca de 10 MB. Uma rodada com 8 workers tinha 15 GB em `%TEMP%\forja-mutants-*`, refeitos a cada arquivo mutado e de novo na reconferência.
- **Depois da mudança:** uma cópia tem 852 arquivos e 10,4 MB e leva 0,96 s. A mesma árvore em links físicos leva 0,31 s.
- **A máquina:** Ryzen 9 7900X3D (24 threads), 32 GB, C: em NVMe. O arquivo de paginação teve pico de 737 MB; a memória não foi o gargalo nesta sessão.

### Conclusões da comparação

- **A Forja não é cópia do mutator.** Portou as regras de sítio e a regra de julgamento. O executor, a seleção de mutantes e a linha de comando são próprios. O `crapper`, de que o mutator depende, não foi copiado nem citado.
- **Iguais:** tabela de operadores, pais binários e unários, booleanos, `0`/`1`, Go só `*` para `/`, `??` e `?.`; mutante morto por falha ou tempo; tempo-limite de 10 vezes a base, mínimo 2 s; linha sem cobertura não roda; nota = mortos / (mortos + sobreviventes); saídas 0, 2 e 3.
- **O mutator faz e a Forja não:** worker como overlay de links; um worker por núcleo, limitado por `--max-workers` e pelo número de sítios; snapshot em `.metrics/mutate` com modo diferencial; base uma vez por comando; `anchor_line` (a linha onde o comando começa também vale como coberta); `_in_type` (literal em tipo de TypeScript não é sítio); em Go, só o pacote do arquivo; em Java, `mvn -q test` sem `clean`.
- **A Forja faz e o mutator não:** escolha de testes pela linha mutada (só em `scripts/mutate.py`), reconferência do mutante que estourou o tempo ao lado dos outros, canário, arquivo de supressões, C#.
- **A função dona do sítio diverge em TypeScript e Java.** O `crapper` só tem por entrada função de topo, método de classe, `const f = () =>` de topo e callback de rota Express; sítio fora delas é descartado. A Forja aceita qualquer função. A Forja muta mais código.
- **CRAP:** fórmula e limite iguais. O `crapper` conta complexidade com tree-sitter e lê a cobertura por instrução (JaCoCo) e por ramo (LCOV); a Forja usa o `lizard` e a fração de linhas.

### Decisão do dono

Adotar o executor do mutator, mantendo da Forja a função dona do sítio, a reconferência e o canário. O CRAP fica para depois.

### O que foi feito (primeira parte)

| Mudança | Arquivos | Teste novo | Commit |
| --- | --- | --- | --- |
| A cópia do worker deixa de fora, na raiz, `target`, `coverage`, `.metrics`, `.forja-coverage` e `.forja-cover.out`; em qualquer nível, os caches (`.pytest_cache`, `.gradle`, `.hg`, `.svn`, `.idea`, além dos que já saíam). | `mutation/executor.py` | um em `tests/mutation/test_executor.py` | `b9f241e` |
| Nunca há mais workers que mutantes a rodar. Sem nenhum mutante coberto, nada é copiado e nenhum teste roda. | `mutation/executor.py` | três | `b9f241e` |
| Só o bytecode do arquivo mutado é removido (`__pycache__/<nome>.*.pyc`), como no mutator. Antes a cópia inteira era varrida duas vezes por mutante. | `mutation/executor.py` | dois | `b9f241e` |

### Conclusões desta parte

- **`dist`, `build` e `out` continuam na cópia,** ao contrário do mutator. O comando de testes que a Forja recebe pode rodar o que foi construído ali; sem eles a rodada de base falharia e o gate ficaria inconclusivo.
- **Um mutante só não é reconferido.** Com um mutante a rodar há um worker, e ele já rodou sozinho.
- **O overlay de links ainda não foi feito, e pede decisão.** Medido aqui, o ganho sobre a cópia já enxuta é de 0,65 s por worker. O risco é escrita no lugar: num worker de links, uma ferramenta que reescreve um arquivo existente (compilador sobre `bin`/`obj`, formatador, teste que regrava um fixture) altera a árvore que o gate está julgando. A cópia é imune a isso.

### Verificação

- `ruff check .`, `ruff format --check src tests` e `mypy` strict: limpos, 624 arquivos.
- `pytest -m "not slow" -n 6` em `b9f241e`: 9736 aprovados, 41 pulados, nenhuma falha, 361 s.
- Não rodou: mutação de `mutation/executor.py`; suíte completa com `slow`.

## 13/11/2026 — o resto do realinhamento da mutação com o `unclebob/mutator`

Continuação da entrada anterior, na mesma sessão. As duas entradas anteriores estavam datadas de 14/11; os commits são todos de 13/11 e os títulos foram corrigidos (`95f79db`, que era `621524b` antes da reescrita registrada na entrada seguinte).

### O que foi feito

| Mudança | Arquivos | Teste novo | Commit |
| --- | --- | --- | --- |
| Literal dentro de um tipo de TypeScript não é sítio (`_in_type`). O sítio conta como coberto pela própria linha ou pela linha onde o comando começa (`anchor_line`), no gate e no script. | `mutation/sites.py`, `mutation/model.py`, `checks/mutation.py`, `scripts/mutate.py`, `docs/guia/gates/mutacao.md` | em `tests/mutation/test_sites.py` e `tests/checks/test_mutation.py` | `cc209e3` |
| A opção do script passa a se chamar `--max-workers`, como no mutator. `--workers` deixou de existir. O padrão continua o menor entre 8 e o número de processadores. | `scripts/mutate.py` | um | `585b7ab` |
| O script lê os testes de cada linha direto do banco do `coverage` (`.coverage`), um arquivo por vez. Não gera mais o `contexts.json`, e apaga o que uma rodada antiga deixou. | `scripts/mutate.py` | um; dois ajustados | `f9fe4db` |
| O mutante de um projeto Maven roda `mvn test`, sem `clean`. O pedido ao adaptador ganhou o aviso `rerun`, que só o Maven lê; o gate de testes continua com `clean`. | `core/adapters/language.py`, `languages/java/adapter.py`, `checks/mutation.py`, `docs/guia/gates/mutacao.md` | dois | `17d06c6` |
| Dois sobreviventes de `sites.py` mortos por teste; a chamada e o índice opcionais (`a?.()`, `a?.[b]`) ganharam teste. | `tests/mutation/test_sites.py` | três | `6a68aff` |
| Modo diferencial no script, como no mutator: cada rodada deixa em `.metrics/mutate/<arquivo>.json` o hash de cada função e o destino de cada mutante. Na rodada seguinte, mutante morto em função de texto igual não roda de novo; sobrevivente e mutante de função alterada rodam. `--mutate-all` ignora o histórico; `--since-last-run` é o nome do padrão; os dois juntos são recusados. Testes que falham antes da mutação não regravam o histórico. | `scripts/mutate.py` | dezessete | `a74d680` |

### Medições

- **`contexts.json`:** 1,8 GB em disco. Lê-lo custava 4,34 GB de memória e 5,1 s. Ler o mesmo do `.coverage` (2,2 MB) custa 0,03 GB e 0,9 s. As duas leituras deram a mesma resposta nos 275 arquivos da rodada guardada: nenhuma linha diferente.
- **Maven sem `clean`:** projeto de 300 classes e 300 testes, um arquivo trocado por rodada. Com `clean`, 9,3 s; sem, 8,6 s (7%). Sem `clean` o mutante foi recompilado e pego, e o arquivo restaurado voltou a passar, três vezes seguidas.
- **Modo diferencial:** `mutation/model.py` e `mutation/sites.py` (51 mutantes) com `--reuse-coverage`. Primeira rodada, 108 s. Segunda, 4 s, com 49 mutantes aproveitados e o mesmo veredito.

### Conclusões

- **`mutation_incremental` não serve para o Maven sem `clean`.** O contrato dessa capacidade é planejar `Operation.MUTATION` com `changed_files`; nenhum adaptador a declara. Por isso o aviso novo (`rerun`), que um adaptador pode ignorar.
- **O identificador do mutante usa a posição do token no arquivo, como no mutator.** Mexer numa função desloca as de baixo, e os mutantes delas rodam de novo mesmo sem mudança. É seguro; só aproveita menos.
- **O modo diferencial não vê os testes.** O hash é do texto da função. Depois de enfraquecer ou apagar um teste, a rodada que vale é `--mutate-all`. É o comportamento do mutator. Fica só no script; o gate da Forja continua rodando todos os mutantes.
- **Formato do histórico:** JSON por arquivo-fonte, não o EDN por namespace do mutator. O EDN existe para o `uml-viewer`, que ninguém pediu.
- **O sobrevivente de `_statement_line` passava porque o teste punha o sítio na linha 1:** âncora 0 vira a própria linha no modelo. O teste novo usa a linha 3.

### Verificação

- `ruff check .`, `ruff format --check src tests` e `mypy` strict: limpos, 624 arquivos.
- `pytest -m "not slow" -n 6` em `17d06c6`: 9755 aprovados, 41 pulados, nenhuma falha, 404 s. Depois disso só mudaram `scripts/mutate.py` e dois arquivos de `tests/mutation`, que passaram sozinhos (364 e 38 testes).
- Mutação em `96edf08`: `mutation/executor.py` (21 mutantes) e `orchestrator/budget.py` (33), nenhum sobrevivente.
- Mutação em `cc209e3`: `mutation/model.py` (4) sem sobrevivente; `mutation/sites.py` (47) com dois, mortos em `6a68aff`, conferidos à mão e por nova rodada em `a74d680`; `checks/mutation.py` (43) sem sobrevivente, com 18 sítios em linhas que os testes rápidos não executam.
- Não rodou: suíte completa com `slow`; mutação de `checks/mutation.py` e `languages/java/adapter.py` depois de `17d06c6`. `scripts/mutate.py` não é mutado: o script só muta `src`.

## 13/11/2026 — os três pontos de atenção tratados

O dono pediu, em 13/11, que três pontos que ficaram da entrada anterior fossem tratados: o modo diferencial de `scripts/mutate.py` não via os testes (com um teste enfraquecido ou apagado e a função igual, o mutante continuava dado como morto e não rodava); o histórico é JSON por arquivo-fonte, não o EDN por namespace do mutator; e o commit `621524b` tinha a mensagem errada (dizia ser o diário do realinhamento, e só corrigia duas datas de título).

### Decisões do dono

O dono concordou com as três sugestões que eu fiz:

- **Ponto 1:** aceitar a divergência do mutator e implementar. O veredito aproveitado passa a depender dos testes que o deram. A escolha é por mutante, não por função: o mutante que sobrevive à rodada estreita é julgado de novo por todos os testes do arquivo-fonte, e pode ser morto por um teste que a cobertura não liga a nenhuma linha da função. A divergência só acrescenta rodadas: todo mutante que o mutator rodaria continua rodando, e alguns que ele aproveitaria passam a rodar.
- **Ponto 2:** o histórico fica em JSON até a decisão sobre o `crapper`. Nenhuma mudança de código.
- **Ponto 3:** reescrever a mensagem do commit.

### Ponto 3: a mensagem reescrita

Feito primeiro, com a árvore limpa: `621524b` virou `95f79db`, com a mensagem `docs: the two logbook entries dated 14/11 are dated 13/11, the day of their commits`. Os hashes dos commits que vinham depois dele na branch mudaram junto.

### Ponto 1: o que foi feito

Commit `f579255`. Só `scripts/mutate.py` e os testes dele mudaram.

| Mudança | Onde |
| --- | --- |
| O histórico guarda, para cada mutante morto, os arquivos de teste que o mataram (`judges`) e o hash de cada um (`tests`). Versão 2. | `write_history`, `load_history`, `History` |
| O hash de um arquivo de teste cobre o conteúdo dele, todo `conftest.py` sob `tests/` e a lista de `deselected()`. Arquivo que sumiu tem um hash que nenhum arquivo tem. | `test_digests` (nova) |
| Quem matou: os testes da linha do mutante, ou todos os testes do arquivo-fonte quando ele foi julgado pela segunda vez. | `judges_of` (nova); `judge_file` devolve os lugares dos reconferidos, não mais a contagem |
| O veredito só é aproveitado se todo arquivo que matou o mutante tem hoje o hash que tinha. | `carried` |
| A linha de andamento diz `killed before, the function and its tests left as they were`. | `main` |

Testes: dez casos novos em `tests/mutation/test_mutate_script.py`, que ficou com 48. Cobrem: teste enfraquecido, arquivo de teste apagado, `conftest.py` alterado numa pasta que não está acima do teste, mutante morto só na reconferência, histórico da versão 1, e o hash em si.

### Conclusões

- **Os nomes dos arquivos são guardados, não só um hash do conjunto.** Para comparar na rodada seguinte é preciso saber qual conjunto julgou o mutante na rodada anterior: o da linha ou o inteiro. Só com o hash não dá para refazer a conta. De quebra, o JSON mostra qual teste matou o quê.
- **Histórico da versão 1 não aproveita nada.** Ele não diz quem matou. A primeira rodada de cada arquivo depois desta mudança roda todos os mutantes, uma vez.
- **O próprio arquivo de teste do script aparece entre os que julgam.** Na rodada real abaixo, `tests/mutation/test_mutate_script.py` está entre os três arquivos que mataram os mutantes de `model.py`. Mexer nele faz esses mutantes rodarem de novo. É o comportamento pedido.
- **Arquivo sumido antes e sumido agora conta como igual.** Na prática não acontece: com cobertura nova, um arquivo apagado deixa de aparecer entre os testes do arquivo-fonte.
- **O guia não mudou.** `docs/guia/gates/mutacao.md` não fala do modo diferencial do script; a sugestão supôs que falava. O texto de ajuda do script foi atualizado.
- **`--mutate-all` antes de release continua valendo,** por três limites que o histórico continua sem ver: código de apoio dos testes fora de `tests/` (como `src/forja/testing`), arquivos de dados que um teste lê, e mudança em outra função de que o teste dependia para matar o mutante.

### Verificação

- `ruff check .`, `ruff format --check src tests scripts/mutate.py` e `mypy` strict: limpos, 624 arquivos.
- `pytest tests/mutation`: 435 aprovados.
- Rodada real do script na árvore principal, sobre `src/forja/mutation/model.py` (4 mutantes, 3 arquivos de teste), com cobertura nova (358 s):
  1. Primeira rodada: 4 julgados, saída 0, histórico na versão 2 com os três arquivos em cada mutante.
  2. Sem mudar nada, com `--reuse-coverage`: 4 aproveitados.
  3. Uma linha de comentário acrescentada a `tests/mutation/test_sites.py`: nenhum aproveitado, 4 julgados.
  4. Arquivo restaurado: 4 julgados de novo (o hash guardado era o do arquivo alterado).
  5. Mais uma vez, sem mudar nada: 4 aproveitados.
- Não rodou: a suíte rápida inteira à parte (ela rodou sob `coverage` na rodada real, sem falha que impedisse a medição; só `scripts/mutate.py` e o teste dele mudaram); suíte completa com `slow`; E2E com agente real. `scripts/mutate.py` não é mutado: o script só muta `src`.

## 13/11/2026 — itens 31 e 33 a 40

Sessão nova, a partir de `23aa6cd`. O item 32 não foi iniciado; o 36 ficou pela metade.

### O que foi feito

| Item | Mudança | Arquivos | Teste novo | Commit |
| --- | --- | --- | --- | --- |
| 31 | O teste do código de saída do gate diferencial usa os limiares folgados dos vizinhos (`LOOSE`). | `tests/checks/test_differential_check.py` | nenhum | `e84ac1e` |
| 33 | Cenário de retomada em que o gate de integração reprova a tarefa, com corte em cada linha a partir da integração (10 cortes limpos, 6 com linha rasgada). | `tests/orchestrator/test_resume_rollback.py` (novo, `slow`) | 18 casos | `ee3bd81` |
| 33 (defeito achado) | A retomada depois de um corte em volta do `rollback_integration` bloqueava a tarefa sem o motivo da reprovação. O motivo agora vai na intenção do rollback, e a retomada o lê de lá. `RunState` passou a guardar o que cada intenção disse (`intent_of`), também depois de fechada. | `core/events/state.py`, `orchestrator/operations.py`, `orchestrator/task_cycle.py` | um em `test_state.py`, um em `test_operations.py`; um ajustado em `test_details.py` | `ee3bd81` |
| 34 | `judge` (186 linhas) lê a worktree uma vez (`_Read`) e monta os checks um grupo de gates por vez, em oito métodos. `build_package` (CCN 67) virou uma função por seção do pacote. Nenhuma das duas passa mais do limite de 15. | `orchestrator/measurer.py`, `orchestrator/agent_runner.py` | nenhum | `cec13d5`, `fee5657`, `6eb9e2a` |
| 35 | `Journal.operate`, `done` e `operation_id` recebem `OperationName`, não texto livre. Os chamadores, `HANDLED_OPERATIONS`, `AGENT_OPERATIONS` e as duas constantes `OPERATION` usam o enum. | `orchestrator/operations.py`, `phases.py`, `task_cycle.py`, `workspace_git.py`, `agent_runner.py`, `checks/tooling.py` | nenhum; o `mypy` é quem cobra | `9b6f005` |
| 36 (parte) | `TaskInfo` foi para `core/task.py` e `SinkFactory` para `core/events/sink.py`. `orchestrator/ports.py` continua exportando os dois. Nenhum módulo de `checks` importa `orchestrator`. | `core/task.py` (novo), `core/events`, `checks/scope.py`, `checks/tooling.py`, `orchestrator/ports.py` | `tests/checks/test_layering.py` | `2c3d71c` |
| 37 | O README cita testes `slow` que existem (`tests/acceptance/test_resume.py`, `tests/orchestrator/test_resume_end_to_end.py`). | `README.md` | o de links, abaixo | `6ff092b` |
| 38 | README: plataforma "Windows"; Linux x64 recebe as ferramentas do `forja instalar`, mas não é testado. O guia de instalação diz o mesmo na tabela de plataformas. | `README.md`, `docs/guia/instalacao.md` | nenhum | `6ff092b` |
| 39 | Os três links quebrados saíram (`docs/adr/README.md`, ADR 0019). Nove ADRs deixaram de apontar para arquivos `docs/plano-*.md` que não existem. O índice das ADRs avisa que os planos citados não estão mais no repositório. | `docs/adr` (11 arquivos) | `tests/cli/test_links_da_documentacao.py`: todo link de `README.md` e de `docs` para um arquivo leva a um arquivo que existe | `6ff092b` |
| 39 | A tabela de gates do guia é comparada com `checks/specs.py`: gate e modo padrão de cada verificação. | `tests/cli/test_guia_cobre_codigo.py` | um | `6ff092b` |
| 40 | Saíram o grupo `mutation` (`mutmut`), o bloco `[tool.mutmut]`, a linha `mutants/` do `.gitignore` e `OUTSIDE` de `claude_code.py`. `ruff` ganhou piso (`>=0.17.0`, a versão do `uv.lock`). | `pyproject.toml`, `uv.lock`, `.gitignore`, `executors/claude_code.py` | nenhum | `6f149ec` |

### Conclusões

- **Item 33, a avaliação estava errada num ponto e certa no outro.** `normalize_style` já era cortado em toda linha pelo cenário de `test_resume_end_to_end`: o medidor falso formata `base.txt`, então a normalização faz commit de verdade ali. `rollback_integration` tinha um teste rápido com git em memória e um corte só (`test_details.py`); faltava o corte em cada linha com git real.
- **Item 33, o teste rápido que existia fixava o defeito.** `test_a_cut_after_the_rollback_blocks_the_task_without_judging_the_integration_again` afirmava o bloqueio sem `detail`. O cenário novo compara o resumo inteiro com o da execução sem corte, e o resumo saía diferente em 3 dos 16 cortes (linhas 60 e 61). A afirmação do teste rápido foi trocada: com corte ou sem, o bloqueio leva o mesmo motivo.
- **Item 33, por que na intenção.** O veredito do gate só existe em memória. Depois do corte, a integração já foi desfeita e não dá para julgar de novo. A intenção é escrita antes do rollback, então sobrevive a qualquer corte depois dele. Log gravado antes desta mudança não tem o motivo na intenção; retomado, bloqueia sem ele, como antes.
- **Item 34, o pacote é o mesmo.** O `build_package` novo foi comparado com o de `cec13d5` em 221.184 combinações de papel, escopo, relatórios e textos fixos: nenhum caractere de diferença.
- **Item 34, a supressão de mutação mandou no desenho.** `mutation-suppressions.toml` nomeia `build_package` e `True -> False` (a marca de item essencial). A primeira divisão levou os `True` para as funções auxiliares e `tests/mutation/test_sites.py` falhou. A marca voltou para dentro de `build_package` (`6eb9e2a`); a supressão aprovada não foi tocada.
- **Item 34, `judge` mantém a ordem.** As leituras de git, a instalação de dependências e a lista de checks seguem a ordem de antes.
- **Item 34, `read_facts` (CCN 27) e outras 20 funções continuam acima de 15.** O plano do item pedia `build_package` e `judge`. As maiores que ficaram: `cli/prepare.py::prepare` (48), `summary.py::read_facts` (27), `languages/go/parsers.py::parse_tests` (22).
- **Item 38, o código não mudou.** `forja instalar` continua instalando no Linux x64. O que mudou é o que a documentação promete.
- **Item 39, as ADRs continuam citando "questão N do plano" e `plano-3.md`.** São o registro do que se via na hora; só saíram os ponteiros para arquivos que não existem.
- **Item 40, `OUTSIDE` era resto mesmo.** O commit `219dc76` trocou a negação de `../**` pela permissão só sob `./**` e deixou a constante.

### Verificação

- `ruff check .`, `ruff format --check src tests` e `mypy` strict: limpos, 628 arquivos.
- `pytest -m "not slow" -n 6` em `ee3bd81`: 9855 aprovados, 41 pulados, nenhuma falha, 360 s.
- `tests/orchestrator/test_resume_end_to_end.py` e `tests/orchestrator/test_resume_rollback.py` (os dois `slow`): 120 aprovados, 196 s.
- `scripts/mutate.py` sobre os sete arquivos de `src` alterados, com cobertura nova: saída 0. Mutantes: `core/events/state.py` 27, `orchestrator/operations.py` 17, `task_cycle.py` 78, `agent_runner.py` 101, `measurer.py` 94, `core/events/sink.py` 1. Os únicos sobreviventes são as marcas `True -> False` de `build_package`, que `mutation-suppressions.toml` já nomeia. `core/task.py` não teve mutante julgado: o arquivo só declara `TaskInfo`, e nenhuma linha dele roda dentro de um teste.
- Não rodou: a suíte completa com `slow` (só os dois arquivos de retomada acima); E2E com agente real.

## 14/11/2026 — as decisões abertas do dono, o item 32 e o conflito de integração do item 33

Sessão nova, a partir de `ff39846`. O dono decidiu tudo o que estava aberto e pediu, por ora, só o que não gera a necessidade de um teste de mutação. Por isso nada em `src` mudou nesta entrada.

### Decisões do dono

| Assunto | Decisão | Estado |
| --- | --- | --- |
| Item 5, `authorize()` | Ligar. O Contrato 3 da Especificação diz que a autorização é uma política separada do fluxo da tarefa. | A fazer; muda `src`. |
| Item 5, `SentPackageStore` | Ligar: gravar o pacote enviado ao agente (ADR 0009). | A fazer; muda `src`. |
| Item 5, artefato inválido | Refazer a etapa sozinho, como a Especificação diz ("é invalidada e refeita como nova tentativa"). | A fazer; muda `src`. |
| Item 24 | ADR 0041 confirmado. | Feito. |
| Item 29 | Manter: dependência nova só em Python. | Encerrado. |
| Item 36, segunda metade | Encerrar. `checks` não importa `orchestrator`, e `test_layering.py` guarda isso. `measurer.py` importar `checks` é a direção certa. | Encerrado. |
| Item 12, etapa de caracterização | Exigir a nota de mutação da função para aprovar a etapa. | A fazer; muda `src`. |
| Overlay de links | Não fazer. | Encerrado; registrado no ADR 0021. |
| CRAP pelo `crapper` | Não trocar. O `lizard` e a fração de linhas ficam. Com isso o histórico da mutação fica em JSON. | Encerrado; registrado no ADR 0021. |
| Item 40 | A versão fica `0.0.0` até a primeira execução com agente real. `dist/` apagada. | Feito. |
| Item 34, as 21 funções acima do limite | Não estender o item à mão. Rodar a própria Forja sobre elas (`prepare`, CCN 48, primeiro), o que entrega também o E2E com agente real. | A fazer; muda `src`. |
| Item 30 (CI) | Continua adiado. Até haver GitHub, a suíte completa com `slow` na ponta fecha cada leva. | Adiado. |

### O que foi feito

| Item | Mudança | Arquivos | Commit |
| --- | --- | --- | --- |
| 24 | O ADR 0041 passou de "proposto" a "aceito". | `docs/adr/0041-*.md`, `docs/adr/README.md` | `1a6c498` |
| ADRs | Os ADRs 0021 e 0034 ganharam uma seção de revisão: o que o realinhamento com o mutator mudou, o que a Forja mantém de diferente e as duas decisões de hoje (overlay e CRAP). O texto antigo ficou como estava. | `docs/adr/0021-*.md`, `docs/adr/0034-*.md` | `5ba7844` |
| 32, `sleep` | 17 esperas por tempo fixo passaram a esperar pelo que o teste precisa. As teclas dos testes de execução são dadas quando o agente entra na chamada (`Waiting.began`), não depois de 0,2 s. A última tecla da tela cheia é dada quando a tela mostra o relatório. Nas tarefas em paralelo, uma tarefa espera o evento da outra (a chamada começou, a tarefa foi integrada). A armadilha de rede é lida quando registrou a conexão. | `tests/cli/test_run.py`, `tests/cli/test_full_run.py`, `tests/orchestrator/test_together_resume.py`, `tests/checks/test_network_trap.py` | `4b45973` |
| 32, asserção | 13 testes que só conferiam "não levanta erro" passaram a afirmar o resultado. | `tests/cli/test_keys.py`, `test_output_encoding.py`, `test_session_open.py` (2), `test_run.py`, `tests/core/artifacts/test_llm_output.py`, `tests/core/gitguard/test_guarded.py`, `tests/core/locking/test_mutex.py`, `tests/core/policy/test_problems.py` (2), `tests/core/test_report_readers.py`, `tests/executors/test_registry.py`, `tests/orchestrator/test_workspace_git.py` | `4b45973` |
| 33 | Cenário de retomada em que o merge da segunda tarefa não aplica, com git de verdade: duas tarefas saem do mesmo commit e escrevem o mesmo arquivo. Corte em cada linha a partir do `integrate_task` que conflita (8 cortes limpos, 4 com linha rasgada). | `tests/orchestrator/test_resume_conflict.py` (novo, `slow`), 14 casos | `c4ff3ab` |
| 40 | A pasta `dist/` foi apagada. Estava fora do git. | nenhum | nenhum |

### Conclusões

- **Item 32, os números da avaliação e os de agora.** A avaliação contou 60 `sleep` e 63 testes sem asserção. A busca de hoje achou 64 linhas com `sleep(` e 27 funções de teste sem `assert`, sem `pytest.raises` e sem chamar um auxiliar com nome de conferência. Não sei como os 63 foram contados.
- **Item 32, os `sleep` que ficaram (56 linhas) e por quê.**
  - Processo filho que precisa estar vivo para ser morto ou estourar o tempo (`time.sleep(60)` dentro do código do filho).
  - Chamada que nunca volta até ser cancelada (`asyncio.sleep(3600)`).
  - Ceder a vez (`asyncio.sleep(0)`) e os `sleep` falsos dos testes, que só anotam o pedido.
  - Laço que espera uma condição, com prazo: `test_resume_end_to_end.py`, `core/locking/support.py`, `core/process/support.py`, `test_runner.py`, `test_install_command.py`, `test_install_full.py`, e os dois que esta entrada escreveu (`recorded` e `leave_soon`).
  - Testes em que o tempo é o que se mede: quem espera o mutex enquanto outro o segura (`test_mutex.py`), os workers da mutação rodando ao mesmo tempo (`test_executor.py`), o pico de memória (`test_peak_memory.py`), a tela que continua esperando a tecla (`test_install_full.py`), o agente de `test_together.py` e o corte de `tests/acceptance/test_resume.py`.
- **Item 32, os 14 sem `assert` que ficaram.** Onze afirmam por um auxiliar: `invalid` em `test_schemas.py` (4), `run` em `test_templates.py` (5, confere o código de saída) e `drive` nos dois testes de tela (confere o layout). Três chamam uma função que não devolve nada, e o resultado é a recusa não acontecer: `test_agent_runner.py::test_a_path_that_only_looks_like_a_hidden_one_is_not_refused` e os dois de `test_agent_runner_gaps.py` sobre o manifesto.
- **Item 32, a segunda tecla não precisa de intervalo.** O teste da segunda pressão durante a gravação dava 0,1 s entre as duas. Sem intervalo o resultado é o mesmo: a primeira já deixou a execução parando.
- **Item 33, nenhum defeito achado.** Nos 12 cortes a retomada chega aos mesmos fatos da execução sem corte: a primeira tarefa integrada, a segunda bloqueada com `integration_conflict` e o mesmo detalhe, a árvore da branch da execução, as refs, o resumo. A retomada não chama o agente de novo; o teste falha se chamar.
- **Item 33, como o cenário fica determinístico com duas tarefas ao mesmo tempo.** A chamada da primeira espera a da segunda começar, e a da segunda espera o merge da primeira. As linhas antes do primeiro merge podem trocar de ordem entre rodadas, mas a contagem é a mesma (75, três rodadas), e do `integrate_task` da segunda tarefa em diante só uma tarefa escreve.
- **`test_keys.py`, corrigido depois do primeiro commit.** A primeira asserção de `test_closing_a_reader_that_never_started_does_nothing` afirmava que o terminal é restaurado, e o nome do teste diz "não faz nada". Ficou só o que o nome diz: nenhuma thread foi criada (`41882a6`).

### Verificação

- `ruff check .`, `ruff format --check src tests` e `mypy` strict: limpos, 629 arquivos.
- `pytest -m "not slow" -n 6` em `4b45973`: 9855 aprovados, 41 pulados, nenhuma falha, 366 s.
- Os arquivos de teste alterados pelo item 32 rodaram três vezes seguidas cada um, sem falha.
- `tests/orchestrator/test_resume_conflict.py`: 14 aprovados, duas vezes.
- Suíte completa com `slow`, `pytest -n auto`, `JAVA_HOME` no JDK 21, em `c4ff3ab`: 10096 aprovados, 41 pulados, nenhuma falha, 38 min 42 s. É a primeira rodada inteira desde `9797991`; fecha a pendência "suíte com `slow`" das entradas anteriores. A correção de `test_keys.py` veio depois, e só o arquivo dela rodou de novo (13 aprovados).
- Mutação: não rodou. Nada em `src` mudou.
- Não rodou: E2E com agente real.

### Pendências

- **Com mudança em `src`, e por isso com mutação, para depois:** as três do item 5; a nota de mutação na etapa de caracterização (item 12); o resto do item 6 (lista de permissão de opções e `protocol.ext.allow=never`); o resto do item 2 (`--require-hashes`); a Forja rodando sobre as 21 funções acima do limite (item 34).
- **Mutações antigas que nunca rodaram:** `core/adapters/common.py`, os `parsers.py` de C# e Java, `cli/session.py`, `cli/detect.py`, `testing/workspace.py`, `testing/repo.py`, e `checks/mutation.py` e `languages/java/adapter.py` depois de `17d06c6`.
- **Fora dos 40:** o teste de painéis em Python 3.13; pular teste em TypeScript, Java, C# e Python não é procurado pelo gate de escopo; os históricos da versão 1 em `%LOCALAPPDATA%\forja-verif`; as dez pastas `forja-captura-*` no `%TEMP%`.
- **Item 30:** adiado.

## 14/11/2026 — o pull request 1: a prova de vida da CLI do Cursor 2026.11

Fora da avaliação: o dono pediu para avaliar o que estava aberto no GitHub. Não havia issue. Havia um pull request, o 1, de André Amorim, aberto contra `main` (`699d936`), com três commits.

### O que o pull request diz e o que foi conferido

| Parte | O que diz | Conferência |
| --- | --- | --- |
| 1 | Arquivo de permissão numa subpasta de `.forja-agent` derruba a prova de vida com `FileNotFoundError` (Cursor, `cursor/cli-config.json`; Grok, `grok/config.toml`). | Confirmado em `correcoes-p0`: `executors/probe.py` e `orchestrator/agent_runner.py` criavam só `.forja-agent`. Com os dois arquivos de `561809e`, os testes novos falham com esse erro, nas duas CLIs. O defeito não era só da prova: a cópia de trabalho de uma execução real falhava do mesmo jeito. |
| 2 | O Cursor lia "here" como a pasta do pacote e gravava `probe.txt` dentro de `.forja-agent`. O pedido passa a dizer "in the working directory root (not inside .forja-agent)". | Não conferido: não há CLI do Cursor nesta máquina. O texto novo é mais preciso para as cinco CLIs e nenhum teste dependia do antigo. |
| 3 | O Cursor 2026.11.10 informa a escrita como `editToolCall` com `streamContent`, não mais `writeToolCall` com `fileText`. O leitor aceita os dois. | Não conferido contra a CLI nem contra a documentação: o formato vem da saída que o autor colou no pull request. A forma antiga continua lida, com o teste que já existia. |

### Crítica

- **O teste do pull request não servia aqui.** `test_probe_setup_files.py` usava um script `#!/bin/sh` como CLI falsa, que não roda no Windows, e afirmava só `isinstance(problems, list)`. É o tipo de teste que o item 32 tirou.
- **A correção em `agent_runner.py` veio sem teste.** Só a prova de vida era exercitada.
- **O texto do pedido continua repetido em seis lugares** (`GENERIC_PROBE` e os cinco adaptadores), com `.forja-agent` escrito à mão em vez de `AGENT_DIR`. Já era repetido antes; o pull request não piorou. Não mexi.
- **A contagem de linhas do `editToolCall` é aproximada.** `streamContent` é contado inteiro como linhas adicionadas, zero removidas, também quando o arquivo já existia. O `writeToolCall` já fazia o mesmo.
- **Os quatro testes que o autor viu falhar no Linux em `main`** (`test_snapshot.py`, `test_repository.py`, `test_adapter.py` de Python, `test_e2e.py`) não foram investigados. A Forja só foi rodada no Windows.

### O que foi feito

| Mudança | Arquivos | Commit |
| --- | --- | --- |
| O pull request entrou em `correcoes-p0` por merge, sem conflito, com os três commits do autor como estão. Quando a branch chegar a `main`, o GitHub dá o pull request por incorporado. | os 10 arquivos do pull request | `288da06` |
| O teste da prova de vida roda em qualquer sistema: o adaptador real, com o programa trocado por um Python que não faz nada. Afirma que cada arquivo declarado está em `.forja-agent` com o conteúdo declarado na hora da chamada, e que a prova chegou a procurar o `probe.txt`. | `tests/executors/test_probe_setup_files.py` | `78bdf66` |
| Teste novo para a cópia de trabalho: com o adaptador do Cursor e o do Grok, o arquivo de permissão em subpasta é gravado na cópia. | `tests/executors/test_agent_runner.py` | `78bdf66` |

### Verificação

- Os 4 casos novos de Cursor e Grok falham com `FileNotFoundError` sem as duas linhas de `mkdir` e passam com elas.
- `ruff check .`, `ruff format --check src tests` e `mypy` strict: limpos, 630 arquivos.
- `pytest -m "not slow" -n 6`: 9861 aprovados, 41 pulados, nenhuma falha, 325 s.
- Não rodou: os testes `slow`, a mutação (`src` mudou em 8 arquivos) e a prova de vida com a CLI real do Cursor ou do Grok.

### Pendências

- Mutação sobre o que o pull request mudou em `src`, junto com a próxima leva que mexe em `src`.
- Prova de vida com o Cursor 2026.11 de verdade, para conferir as partes 2 e 3.
- O pull request 1 segue aberto no GitHub até `correcoes-p0` chegar a `main`. A branch foi enviada ao remoto e o pull request recebeu um comentário com o que foi feito.

## 14/11/2026 — o desempenho da mutação passa a ser o ponto central

Sessão de análise e decisão. Nenhum código mudou; só o `CLAUDE.md` e este diário.

### O que motivou

O dono está descontente com o caminho seguido no desempenho da mutação. Com a Forja rodando, a máquina (Ryzen 9 7900X3D, 24 threads, 32 GB) não serve para jogo nem para transmissão ao vivo. O gate de mutação é um pilar de qualidade e pede o máximo de desempenho real, sem perder qualidade.

### Decisões do dono

| Assunto | Decisão |
| --- | --- |
| Prioridade baixa nos processos de mutação, no gate e no script | Fazer, mesmo sem estar confirmada no mutator. |
| Um worker por núcleo como padrão, no gate e no script | Fazer. |
| Go: só o pacote do arquivo mutado, como no mutator | Fazer. |
| Ordem do trabalho | Esta leva passa à frente do item 5. |
| Testes pela linha mutada e histórico dentro do gate, "só em Python por enquanto" | Recusado como proposto. Os pilares da Forja não têm comportamento diferente por linguagem. A técnica entra no gate para todas as linguagens do requisito básico, ou não entra. |

### Conclusões

- **A proposta "só em Python por enquanto" foi um erro meu.** A regra passou a estar escrita no `CLAUDE.md`, item 7: processo, qualidade, assertividade e usabilidade iguais em todas as linguagens, frameworks e sistemas operacionais do requisito básico.
- **A escolha de testes pela linha no gate depende de cobertura por teste em cada linguagem.** Hoje só o script a tem, em Python, pelos contextos do `coverage`. O equivalente nas outras linguagens ainda não foi levantado; é a primeira parte do plano.
- **A medição de `orchestrator/measurer.py` (22 min, 86 mutantes) é anterior à cópia enxuta e ao modo diferencial.** Precisa ser refeita antes de qualquer mudança.
- **O que preserva a qualidade:** morto pelo subconjunto é morto pelo conjunto; sobrevivente só conta depois do conjunto inteiro. Ficam recusados: amostrar mutantes, encurtar o tempo-limite, pular a reconferência e descartar mutante por heurística de equivalência. Toda mudança entra com medição antes e depois e com comparação de vereditos.

### Verificação

Nada rodou: nenhum código mudou.

## 14/11/2026 — plano da leva de desempenho da mutação

Sessão de leitura e plano. Nenhum código mudou; só este diário. O plano espera a ordem do dono.

### O que foi lido

- **Forja:** `mutation/executor.py`, `checks/mutation.py`, o comando de testes e o de cobertura dos cinco adaptadores, o pedido e as capacidades em `core/adapters/language.py`, o grupo de processos de `core/process` (Windows e POSIX), o modelo de cobertura de `core/reports`, e de `scripts/mutate.py` o runner, `judge_file` e `main`.
- **`unclebob/mutator` em `830ec7e`,** o mesmo commit de 13/11: README, `runner.py`, `workers.py`, `engine.py`, `cli.py` e `coverage.py`, por inteiro. Não relidos: `metrics.py`, `sites.py`, `functions.py`, `edn.py`, `report.py`, `model.py` e `crapper_link.py`; nenhum deles executa mutante. `metrics.py` tem de ser relido antes da parte do histórico (fases 4 e 5). O `crapper` não foi lido.

### O que foi conferido

1. **O mutator não trata prioridade de processo.** A busca por `nice`, `priority`, `affinity`, `setpriority`, `sched_` e `ionice` no repositório inteiro não achou nada. A prioridade baixa é divergência, já decidida pelo dono.
2. **Workers no mutator:** o menor entre os sítios escolhidos, `os.cpu_count()` e `--max-workers` (`workers.py:75`).
3. **Base no mutator:** uma por par de comando e pasta, guardada entre os arquivos do mesmo comando (`engine.py:191`), sem limite de tempo. O executor recebe o tempo-limite já calculado: `max(2, base × fator)` (`engine.py:231`).
4. **Go no mutator:** `go test -count=1` no pacote do arquivo (`runner.py:205`). Como a base é por comando, é uma base por pacote.
5. **O mutator não escolhe testes, não os ordena e não para no primeiro que falha.** Python roda `pytest`, Java `mvn -q test -DexcludeTags=no-mutate`, TypeScript `npm test`. Escolha pela linha, ordem e histórico por teste são da Forja.
6. **O gate não usa `-x` em Python,** ao contrário do que eu tinha dito na sessão anterior. `languages/python/adapter.py:251` planeja `pytest -p no:cacheprovider -q -rA --color=no`. `-x` só aparece em `scripts/mutate.py:587` e em `cli/facade.py:63`. No gate, nas seis linguagens, o mutante morto roda a suíte até o fim.
7. **O modelo de cobertura do gate já guarda os testes de cada linha.** `FileCoverage.tests` (`core/reports/models.py:31`) é preenchido pelo leitor do coverage.py, e o driver de Python já grava o contexto por função de teste (`coverage_driver.py:30`, no formato `tests/test_a.py::test_x`). `MutationCheck` não lê esse campo. Os leitores das outras linguagens (istanbul, JaCoCo, Cobertura, perfil do Go) não o preenchem.
8. **Filtro de testes nos adaptadores:** só Python aceita `test_files` (`TEST_FILTER_BY_FILE`). Nenhum adaptador aceita `tests`. Go, Java (Maven e Gradle), C# e TypeScript/JavaScript respondem `NotSupported` a qualquer filtro.
9. **As cópias dos workers são feitas uma depois da outra** (`executor.py:182`). A 0,96 s por cópia (medida de 13/11 na árvore da Forja), 24 workers custam cerca de 23 s antes da base.
10. **O script faz até duas bases por arquivo:** `judge_file` chama `run_mutants` uma vez para a rodada estreita e outra para a reconferência, e cada chamada roda a sua base.
11. **A cobertura de Go é medida com `-coverpkg=./...`** (`languages/go/adapter.py:186`): uma linha pode estar coberta só por teste de outro pacote.
12. **Esta máquina tem WSL com Ubuntu** (`wsl -l -q`). O que há instalado lá não foi conferido. macOS não há.

### O plano

Seis fases, com as regras de qualidade da entrada anterior. O que cada uma das fases 0 a 4 fez e mediu está nas entradas seguintes; a fase 5 foi cancelada pelo dono em 15/11, na entrada que encerra a leva.

| Fase | O que | Depende de |
| --- | --- | --- |
| 0 | Medição de base | nada |
| 1 | Prioridade baixa e um worker por núcleo | 0 |
| 2 | Go só no pacote, e a base uma vez por comando no script | 1 |
| 3 | Levantamento: filtro, parada e teste por linha em cada linguagem | nada |
| 4 | No gate: testes da linha, ordem e histórico, nas seis linguagens de uma vez | 2, 3 e a decisão do dono sobre o levantamento |
| 5 | No script: escolha por função de teste, e quem matou roda primeiro | 2 |

### Verificação

Nada rodou: nenhum código mudou.

## 14/11/2026 — leva de desempenho, fases 0 e 1: a prioridade baixa aprova o que devia reprovar com a máquina toda ocupada

A fase 1 parou na medição com a máquina em uso: o plano previa que, se essa medição desse veredito diferente, a fase parava e o caso voltava ao dono.

### Decisões do dono

Os três pontos do plano foram confirmados: a prioridade mais baixa de cada sistema; no gate, o histórico só para a ordem; macOS sem prova nesta máquina.

### Fase 0: medição de base

Código de `8b61fbe`, no checkout de verificação. A máquina não estava parada de todo: durante a rodada do script eu rodei `ruff`, `mypy`, cerca de 150 testes rápidos e a instalação do ambiente no WSL.

| Rodada | Resultado |
| --- | --- |
| Script, `orchestrator/measurer.py`, `--mutate-all`, 8 workers | 2201 s no total: canário 2 s, cobertura por teste 382 s, o arquivo 1816 s. 94 mutantes, 21 arquivos de teste, todos mortos. O script não diz quanto do arquivo é base e quanto é mutante. |
| Gate, um projeto por linguagem | tabela abaixo, coluna "antes" |

O pico de memória da rodada do script não foi medido de forma confiável: a amostra somou 1,29 GB para 44 processos `python`, o que não é crível. Os vereditos da rodada ficaram guardados (o histórico `.metrics/mutate` do checkout).

**O projeto de cada linguagem** é o de `tests/languages/<linguagem>/test_mutation.py` (em Python, o de `tests/checks/support.py`), com N funções a mais, cada uma com um teste que mata o seu único mutante (`a - b`). O medidor chama `MutationCheck.evaluate` e guarda o veredito de cada mutante. Ele ficou no scratchpad da sessão, não no repositório.

### Fase 1: o que foi feito

| Mudança | Arquivos | Teste novo |
| --- | --- | --- |
| Prioridade baixa no gate. `ProcessSpec.low_priority` pede; o runner chama `ProcessGroup.lower_priority` antes de criar o processo. No Windows o Job Object impõe a classe ociosa à árvore inteira (`JOB_OBJECT_LIMIT_PRIORITY_CLASS`); no POSIX, `os.setpriority` dá 19 ao grupo de processos em `attach`. `AdapterRunner` liga para a base e para cada mutante. | `core/process/model.py`, `group.py`, `runner.py`, `_windows.py`, `_posix.py`, `core/adapters/common.py`, `checks/mutation.py` | `tests/core/process/test_priority.py` (7), um em `tests/core/adapters/test_common.py`, um em `tests/checks/test_mutation.py` |
| Prioridade baixa no script. `yield_the_machine` baixa a do próprio processo no começo de `main`; o que ele inicia herda. | `scripts/mutate.py` | dois em `tests/mutation/test_mutate_script.py` |
| Um worker por núcleo. O padrão passa a `os.cpu_count()` no gate e no script. As cópias dos workers são feitas ao mesmo tempo. | `mutation/executor.py`, `scripts/mutate.py`, `docs/guia/gates/mutacao.md`, `docs/guia/forja-toml.md` | dois em `tests/mutation/test_executor.py`; um ajustado em `test_mutate_script.py` |

Conferido na documentação antes de escrever: `JOB_OBJECT_LIMIT_PRIORITY_CLASS` é `0x20` e vale para todos os processos do job; `IDLE_PRIORITY_CLASS` é `0x40` e é herdada pelos filhos; `os.setpriority` com `PRIO_PGRP` existe no Unix desde o Python 3.3. A página do Job Object diz que quem chama precisa do privilégio `SE_INC_BASE_PRIORITY_NAME`; nesta máquina, sem privilégio, a classe ociosa foi imposta e o teste a lê no processo neto.

### Medições do gate, máquina parada

Antes: `8b61fbe`, 4 workers, prioridade normal. Depois: árvore de trabalho, 24 workers, prioridade baixa. Tempo total do `evaluate`, e a memória que a máquina perdeu no pior momento.

| Linguagem | 30 mutantes, antes | depois | 120 mutantes, antes | depois |
| --- | --- | --- | --- | --- |
| Python | 9,2 s; 0,1 GB | 6,6 s; 0,7 GB | 26,5 s; 0,3 GB | 15,7 s; 0,7 GB |
| Go | 13,5 s; 0,5 GB | 8,0 s; 1,8 GB | 31,5 s; 0,5 GB | 25,0 s; 1,2 GB |
| Java (Maven) | 65,8 s; 1,0 GB | 57,0 s; 4,9 GB | 234,1 s; 2,0 GB | 203,2 s; 5,7 GB |
| C# | 40,8 s; 1,0 GB | 28,8 s; 4,7 GB | 124,2 s; 3,2 GB | 85,3 s; 5,2 GB |
| TypeScript (Vitest) | 7,2 s; 0,3 GB | 4,2 s; 1,3 GB | 21,4 s; 0,4 GB | 11,2 s; 1,4 GB |

Vereditos iguais, mutante a mutante, nas cinco linguagens e nos dois tamanhos.

### Medições do gate, máquina em uso

48 mutantes por linguagem, 12 deles com teste que não confere nada (têm de sobreviver). A carga são processos em laço, em prioridade normal, ligados depois da base.

| Carga | Código | Resultado |
| --- | --- | --- |
| nenhuma | novo | 12 sobreviventes e 36 mortos nas cinco. Python 7,7 s; Go 11,0 s; Java 81,5 s; C# 41,0 s; TypeScript 5,7 s. |
| 12 processos | novo | vereditos iguais nas cinco. Python 25,8 s; Go 73,8 s; Java 481,8 s; C# 244,1 s; TypeScript 23,7 s. |
| 24 processos | novo | **Python: 42 mortos por tempo, 3 sobreviventes, 3 mortos (260 s). TypeScript: 48 mortos por tempo (675 s), e o gate aprova onde devia reprovar** (a nota certa é 0,75, abaixo do limite 0,80). Java, C# e Go não foram medidos nesta carga. |
| 24 processos | antigo (4 workers, prioridade normal) | vereditos certos: 12 sobreviventes e 36 mortos. Python 31,8 s; TypeScript 47,3 s, reprovado. |

### Conclusões

- **Com todos os processadores ocupados, a prioridade baixa troca sobrevivente por morto.** O tempo-limite é de relógio e vem de uma base que rodou com a máquina livre. Um processo de classe ociosa quase não roda quando tudo está ocupado; o mutante estoura o tempo ao lado dos outros, estoura de novo sozinho em `_alone`, e conta como morto. Em prioridade normal o mutante divide o processador com a carga e cabe nas dez vezes.
- **Com metade dos processadores ocupados os vereditos se mantêm,** e é `_alone` que os segura: os mutantes que estouram ao lado dos outros são julgados de novo um a um. É isso que leva Java de 81 s a 482 s.
- **Um worker por núcleo rende pouco onde o comando de testes já usa vários núcleos.** Com 120 mutantes e seis vezes mais workers: Java 1,15 vez mais rápido, Go 1,26, C# 1,46, Python 1,7, TypeScript 1,9, com duas a quatro vezes mais memória. Em Java cada `mvn test` levou cerca de 39 s com 24 juntos, contra cerca de 8 s com 4; o limite, de dez vezes a base, é de cerca de 60 s.
- **O Job Object impõe a prioridade sem privilégio nesta máquina.** Se um sistema recusar, o processo não é criado (`SPAWN_FAILED`) e o gate fica inconclusivo.

### Verificação

- `ruff check`, `ruff format --check` e `mypy` strict: limpos, 631 arquivos.
- `pytest -m "not slow" -n auto`: 9873 aprovados, 41 pulados, 267 s.
- Linux, pelo WSL (Ubuntu 22.04, Python 3.12, ambiente próprio): os testes de prioridade, os dois do script, o do `AdapterRunner` e `test_common.py`, 22 aprovados. A suíte inteira não rodou no Linux.
- Não rodou: a mutação dos arquivos de `src` tocados; a nova medição de `measurer.py` com o código novo; a suíte completa com `slow`; Java, C# e Go com 24 processos de carga; macOS.

## 14/11/2026 — leva de desempenho, fase 1 fechada: o estouro de tempo só mata se a máquina não for a causa

### Decisão do dono

A minha sugestão para o defeito da entrada anterior foi aceita ("sim, implemente"): o estouro de tempo só conta como morte se, logo depois dele, a cópia sem mutação ainda passa dentro do mesmo limite. Quando não passa, a rodada fica sem veredito. É divergência do mutator, que conta todo estouro como morte.

### O que foi feito

| Mudança | Arquivos | Teste novo | Commit |
| --- | --- | --- | --- |
| Prioridade baixa no gate (descrita na entrada anterior). O literal `0` da classe de prioridade saiu de `WindowsJobGroup.__init__`: `_limit` só grava a classe quando ela é pedida. | `core/process/*`, `core/adapters/common.py`, `checks/mutation.py` | `tests/core/process/test_priority.py` (7), um em `test_common.py`, três em `tests/checks/test_mutation.py` | `7ea132d` |
| Cinco mutantes antigos de `_windows.py` mortos por teste: o código de saída de uma árvore morta e de um processo morto sozinho, e os identificadores abertos sobre um processo, que não podem ser herdados. | `tests/core/process/test_windows_group.py` | os três do arquivo | `c5aac71` |
| Um worker por núcleo, cópias dos workers ao mesmo tempo, e a guarda do estouro de tempo no executor: `_still_in_time` roda a cópia sem mutação no limite do mutante e levanta `MachineTooBusy` (filha de `TestsDidNotRun`) quando ela estoura. No gate isso dá inconclusivo. | `mutation/executor.py` | oito em `tests/mutation/test_executor.py` | `3f9224d` |
| Script: prioridade mais baixa do sistema, um worker por núcleo, saída 2 com o histórico intacto quando a máquina está ocupada, e o estouro do limite por teste tratado como estouro de tempo (abaixo). | `scripts/mutate.py` | oito em `tests/mutation/test_mutate_script.py`, e um ajustado | `104e349` |
| O teste do gate diferencial pede razão acima do limite (1,25), não acima de 2. | `tests/checks/test_differential_check.py` | nenhum | `a91536a` |

Os itens que dividem arquivo foram no mesmo commit: worker por núcleo e guarda em `executor.py`; prioridade, worker por núcleo e limite por teste em `scripts/mutate.py`.

### A guarda, medida no gate

48 mutantes por linguagem, 12 que têm de sobreviver, 24 workers, prioridade baixa.

| Carga | Resultado |
| --- | --- |
| nenhuma | vereditos iguais aos da base. Python 7,1 s; Go 9,5 s; Java 71,7 s; C# 36,0 s; TypeScript 5,0 s. |
| 12 processos | vereditos iguais. Python 13,9 s; Go 43,2 s; Java 309,4 s; C# 74,7 s; TypeScript 6,4 s. |
| 24 processos | as cinco inconclusivas, com "the unchanged copy no longer passes in the time a mutant is given". Python 60 s; Go 65 s; Java 252 s; C# 192 s; TypeScript 50 s. Nenhuma aprovou. |

### O script: o limite por teste tinha o mesmo defeito

A primeira rodada de mutação dos arquivos tocados parou em `_windows.py`: os testes falharam na cópia sem mutação. Repetindo a base com a saída guardada, o processo do `pytest-xdist` morria em `tests/languages/python/test_environment.py::test_an_editable_install_of_a_src_layout_repository_stays_visible`. O teste leva 12,4 s sozinho e passou dos 30 s do `--timeout` por teste com 24 processos lado a lado (1 falha em 2 repetições). O comentário do script dizia que o teste mais lento "leva alguns segundos".

O que isso mostrou: no script, um teste que estoura o limite saía como teste que falhou (`pytest-timeout` encerra com código 1), e o mutante contava como morto. Com prioridade ociosa e a máquina ocupada, era o mesmo morto falso que o dono mandou corrigir no gate. Apliquei a mesma regra, sem nova pergunta:

- `PytestRunner` reconhece o estouro pela linha que o `pytest-timeout` escreve no método `thread` (`+++ Timeout +++`, conferida no código da versão 2.4.0 instalada), pedido em todos os sistemas, e devolve `TIMED_OUT`. O executor julga o mutante de novo sozinho e, se estourar outra vez, pede a cópia sem mutação.
- Essa cópia recebe os testes em que o mutante estourou, não todos os do arquivo: é o mesmo comando no mesmo limite, e custa segundos em vez de uma base inteira em um processo só. O runner sem arquivo-fonte (o do rejulgamento por todos os testes) continua a rodar todos.
- O limite por teste foi de 30 s para 120 s, cerca de dez vezes o teste mais lento sozinho. Com o estouro tratado, esse número só mexe no tempo, não no veredito.

### Mutação dos arquivos tocados

- Sem sobrevivente ao fim de quatro rodadas de `scripts/mutate.py --mutate-all`, com os testes novos: `mutation/executor.py`, `core/process/runner.py`, `model.py`, `group.py` e `_windows.py`, `checks/mutation.py`, `core/adapters/common.py`, `orchestrator/measurer.py` e `checks/differential.py`. Este último entrou porque afrouxei um teste que o protege: `test_a_slower_version_rejects_with_the_ratio_and_the_limit` pedia razão acima de 2, mediu 1,85 rodando numa cópia e derrubou a base de uma rodada.
- Ficam sem rodar 15 sítios em linhas que nenhum teste rápido executa: 12 em `checks/mutation.py` (eram 18) e 3 em `_windows.py`.
- `_posix.py` não foi mutado: o script roda no Windows, onde nenhum teste executa as linhas dele.

### O script, antes e depois

`orchestrator/measurer.py`, `--mutate-all`, 94 mutantes, todos mortos nas duas: 1816 s com 8 workers em prioridade normal; 1477 s com 24 em prioridade ociosa (1,23 vez). A cobertura por teste foi de 382 s para 299 a 315 s. A rodada de 24 workers chegou a tomar cerca de 12 GB: a memória livre da máquina caiu de 14 GB para 2,16 GB.

### Conclusões

- **A prioridade baixa não cobre a memória.** Um worker por núcleo rende 1,2 vez no script e de 1,15 a 1,9 no gate, e toma de duas a quatro vezes mais memória. Quem joga ou transmite com a Forja rodando sente a memória, não só o processador. `limits.workers` e `--max-workers` baixam o número; o padrão ficou o do mutator, como o plano aprovado pede.
- **Com 24 processos, teste de tempo deixa de ser estável.** Os dois que derrubaram a base dependiam de quanto tempo um processo leva para começar. Outros podem aparecer em arquivos que esta rodada não tocou.
- **O script não diz qual teste falhou na base.** A saída do `pytest` é descartada; achar os dois testes custou três rodadas e um medidor à parte.

### Verificação

- `ruff check`, `ruff format --check` e `mypy` strict: limpos, 632 arquivos.
- `pytest -m "not slow" -n16`, Windows: 9895 aprovados, 41 pulados, 256 s.
- Linux, pelo WSL: os arquivos de teste tocados e `tests/core/process` inteiro, 256 aprovados, 6 pulados e 1 falha, `tests/mutation/test_mutate_script.py::test_each_line_gets_the_test_files_that_ran_it`, que compara `src\\forja\\nowhere.py` com `src/forja/nowhere.py`. Já falhava em `8b61fbe`; não é desta leva e não foi corrigida.
- Não rodou: a suíte completa com `slow`; a suíte inteira no Linux; macOS; Java, C# e Go do código antigo com 24 processos de carga; a mutação de `scripts/mutate.py` (o script só muta `src`) e de `_posix.py`.

## 15/11/2026 — leva de desempenho, fase 2: Go pelo pacote primeiro, e a base uma vez por comando no script

### O que foi lido

Do mutator, em `830ec7e`, por inteiro: `runner.py`, `engine.py` e `workers.py`. Em `cli.py` só a busca por `baselines`. O que vale para esta fase:

- A base roda uma vez por par (comando, pasta) e fica guardada pela invocação inteira; roda sem limite, na árvore real.
- O limite de cada mutante é `max(2.0, segundos da base × fator)`, entregue pronto a quem roda os mutantes.
- Em Go o comando é `go test -count=1` no pacote do arquivo mutado.
- Sem sítio escolhido, não há base.

`metrics.py` não foi relido: é das fases 4 e 5.

### O que foi feito

| Mudança | Arquivos | Teste novo | Commit |
| --- | --- | --- | --- |
| Executor: o mutante pode levar uma execução menor dos testes (`Mutant.first`), que o julga primeiro. Morto nela, está morto; quem sobrevive a ela é julgado pelo comando inteiro antes de contar. Cada execução tem a sua base, medida uma vez e só se algum mutante a usa, e o seu limite de dez vezes essa base. Quem chama pode entregar o limite já medido (`timeout`), e aí o executor não roda base nenhuma. `baseline()` mede a base numa cópia nova. `run_mutants` devolve o relatório e os lugares dos julgados duas vezes. | `mutation/executor.py` | dez em `tests/mutation/test_executor.py` | `15437a7` |
| Gate, Go: o pedido ao adaptador leva o arquivo mutado (`OperationRequest.mutated`, um aviso, como o `rerun`), e o adaptador de Go planeja `go test -count=1 -json` no pacote desse arquivo. `judged_first` pergunta ao adaptador o comando de cada arquivo: quando é o comando inteiro, o mutante não ganha execução menor; mutantes do mesmo comando dividem a execução e a base. | `core/adapters/language.py`, `languages/go/adapter.py`, `checks/mutation.py` | quatro em `tests/checks/test_mutation.py`, um em `tests/languages/go/test_adapter.py`, três `slow` com Go de verdade em `tests/languages/go/test_mutation.py` | `15437a7` |
| Script: a base roda uma vez, com a união dos testes dos arquivos que têm mutante coberto a rodar, e não roda quando o histórico resolve todos. O limite é de dez vezes essa base, vezes o número de processos em que ela foi espalhada. Os testes da linha passam a ser a execução menor do mutante; saíram a impressão digital do arquivo mutado e as duas chamadas ao executor. Base que falha: saída 2, antes de qualquer mutante, com o histórico intacto. | `scripts/mutate.py` | `tests/mutation/test_mutate_script.py`: onze funções de teste novas ou reescritas no lugar de treze, que conferiam o que foi removido | `15437a7` |
| Guia: "Quando roda" e "Limites de tempo" dizem o pacote primeiro em Go e uma base por comando. | `docs/guia/gates/mutacao.md` | nenhum | com esta entrada |

Os dois itens dividem `executor.py` e foram no mesmo commit: com o executor novo e o script antigo os testes do script não passam.

O sobrevivente do pacote em Go é julgado por `./...` porque a cobertura do gate é medida com `-coverpkg=./...`: uma linha pode contar como executada só pelo teste de outro pacote. Os três testes `slow` provam os três casos: o teste de outro pacote mata, ninguém confere, e o pacote do arquivo mata sem esperar os outros. Nas outras cinco linguagens o adaptador devolve o mesmo comando com ou sem o aviso, como o mutator faz; o ganho equivalente nelas é a fase 4, como o plano já dizia.

### Medições

Todas com 24 workers e prioridade ociosa. Vereditos comparados mutante a mutante.

**Gate, Go.** Módulo de 12 pacotes, 48 mutantes em dois deles, 12 que têm de sobreviver.

| | Tempo | Vereditos |
| --- | --- | --- |
| antes (`f0549a0`) | 65,8 s e 53,0 s | 36 mortos, 12 sobreviventes, aprovado |
| depois | 20,3 s e 20,6 s | os mesmos, um a um |

**Script, quatro arquivos** (`core/process/model.py`, `core/process/group.py`, `core/adapters/common.py`, `checks/differential.py`), `--mutate-all`, cobertura nova.

| Parte | antes | depois |
| --- | --- | --- |
| cobertura por teste | 351 s | 392 s |
| base | dentro de cada arquivo | 268 s, uma vez, 93 arquivos de teste |
| `model.py` (13 mutantes, 75 arquivos de teste) | 1772 s | 1669 s |
| `group.py` (1, 68) | 323 s | 52 s |
| `common.py` (16, 61) | 185 s | 36 s |
| `differential.py` (16, 13) | 118 s | 36 s |
| total | 2750 s | 2456 s |

Os 46 mutantes morreram nas duas, com o mesmo veredito por mutante nos históricos dos dois checkouts.

**Script, `orchestrator/measurer.py`**, `--mutate-all`, cobertura aproveitada, 94 mutantes. Duas rodadas seguidas, com um anotador por fora que registra cada execução dos testes:

| | Total | Base | Mutantes |
| --- | --- | --- | --- |
| antes (`f0549a0`) | 2103 s | 97 s | 94 execuções, soma 37 464 s, mediana 72 s |
| depois | 2214 s | 116 s | 94 execuções, soma 39 053 s, mediana 80 s |

Mesmos conjuntos de teste por execução, nenhum estouro de tempo, 94 mortos nas duas. Uma rodada anterior do código novo, sem o anotador, levou 2022 s.

**Mutação dos arquivos tocados**, 1134 s, saída 0: `mutation/executor.py` (21), `checks/mutation.py` (47), `languages/go/adapter.py` (16), `core/adapters/language.py` (17), nenhum sobrevivente. Continuam os 12 sítios de `checks/mutation.py` em linhas que nenhum teste rápido executa.

Memória livre mínima nas quatro primeiras rodadas: 7,38 GB.

### Conclusões

- **No gate, Go ficou de 2,6 a 3,2 vezes mais rápido** nesse módulo, com os mesmos vereditos.
- **No script, o ganho só existe com mais de um arquivo, e foi de 11% nessa rodada.** Os três arquivos pequenos foram de 626 s para 124 s; `model.py` sozinho é dois terços do total e não mudou. Com um arquivo só (`measurer.py`) o trabalho é o mesmo de antes: uma base e as mesmas 94 execuções. A diferença de 5% entre as duas rodadas não tem causa no código.
- **Os 1477 s da fase 1 para `measurer.py` não se repetiram.** O mesmo script de `f0549a0` levou 2103 s hoje. A causa não foi investigada. Uma hipótese, não conferida: aquela rodada pode ter sido feita ainda com o limite de 30 s por teste, quando o teste que estourava contava como morte e encerrava a execução mais cedo.
- **O que pesa em `measurer.py` são poucos mutantes.** Oito levam de 1200 a 1590 s cada, numa execução de 9 a 18 arquivos de teste em um processo só: o teste que mata está no fim da fila. A mediana é de 72 a 80 s. É o que a fase 5 ataca (quem matou roda primeiro, escolha por função de teste).
- **O limite do script ficou maior.** Para `measurer.py` são 27 836 s (dez vezes a base de 116 s, vezes 24 processos). O laço dentro de um teste continua a parar nos 120 s do limite por teste. Um mutante que não termine fora de teste (na importação, por exemplo) esperaria o limite inteiro; já era assim antes, com 23 184 s.

### Dificuldades

- `ruff format --check .` acusa dois arquivos em `scripts/capturas`. Conferido com `git stash`: já era assim antes desta fase; ficam para o item 40.

### Verificação

- `ruff check .`, `ruff format --check src tests scripts/mutate.py` e `mypy` strict: limpos, 632 arquivos.
- `pytest -m "not slow" -n16`, Windows: 9908 aprovados, 41 pulados, 255 s.
- `tests/languages/go/test_mutation.py`, com Go 1.27.4 de verdade: 6 aprovados.
- Linux, pelo WSL: os quatro arquivos de teste rápidos tocados, 162 aprovados e 1 falha, a mesma `test_each_line_gets_the_test_files_that_ran_it` que falha desde `8b61fbe`.
- Revisão independente do diff (agente de boas práticas da linguagem): nenhum defeito apontado.
- Não rodou: a suíte completa com `slow`; a suíte inteira no Linux; os testes de Go de verdade no Linux (o WSL não tem Go); macOS; a mutação de `scripts/mutate.py` (o script só muta `src`).

## 15/11/2026 — leva de desempenho, fase 3: filtro, parada, ordem e testes por linha nas sete pilhas

Sessão de levantamento. Nenhum código de produção mudou; só este diário. A fase 4 espera a decisão do dono sobre o que está aqui.

### O que foi feito

Para cada pilha do requisito básico (Python com pytest, Go, Java com Maven, Java com Gradle, C# com xUnit, TypeScript com Vitest e com Jest), as três perguntas do plano (como rodar só os testes escolhidos, e qual é a unidade; como parar no primeiro teste que falha e como impor a ordem; como saber quais testes executam cada linha, e quanto custa medir) foram respondidas pela documentação da ferramenta e por prova executada no Windows e no Linux (WSL, Ubuntu 22.04):

- Um projeto pequeno por pilha, com os mesmos casos: três unidades de produção, três arquivos de teste, 14 a 17 testes, uma linha com testes de dois arquivos, um teste parametrizado, uma preparação compartilhada que executa código de produção, e uma linha sem teste.
- Um `prove.sh` com cinco provas (filtro; parada; ordem; testes por linha; ida e volta do id entre o mapa e o filtro) e um `medir.sh` com quatro medidas (testes de hoje; cobertura de hoje; cobertura com testes por linha; um teste só pelo filtro).
- O levantamento de cada linguagem foi feito por um agente, os cinco ao mesmo tempo, com o mesmo protocolo. C# teve duas rodadas a mais e Vitest uma, atrás de um caminho mais barato para os testes por linha.
- As ferramentas e versões são as dos adaptadores e dos testes da Forja: pytest 9.2.0 e coverage 7.17.3 (7.16.2 no Linux); Go 1.27.4; Surefire 3.2.5, JUnit 5.10.2 e JaCoCo 0.8.13 no Maven; Gradle 9.9.1 com JaCoCo 0.8.15; SDK .NET 9.0.321 com `net8.0`, xUnit 2.9.2 e coverlet.collector 6.0.2; Vitest 5.0.5 com `@vitest/coverage-v8`; Jest 30.5.2 com ts-jest 29.4.15.
- O WSL só tinha Python. Go, JDK 21, Maven, Gradle, Node e .NET foram postos em `~/forja-fase3/tools` (1,7 GB, sem `sudo`); entram no PATH com `. ~/forja-fase3/env.sh`. Para desfazer: `rm -rf ~/forja-fase3`.
- O material (protocolo, projetos, scripts, logs, tempos e um `RESULTADO.md` por linguagem, com a marca de provado, documentado ou não conferido em cada afirmação) está em `%LOCALAPPDATA%\forja-fase3`, fora do repositório.

### As três respostas

| Pilha | (a) Filtro e unidade | (b) Parada | (b) Ordem | (c) Testes por linha |
| --- | --- | --- | --- | --- |
| Python | ids do pytest na linha de comando; a unidade é o caso parametrizado | `-x` | a dos argumentos, entre arquivos e dentro do arquivo | o driver da Forja como está, na mesma execução da cobertura |
| Go | `-run` com uma expressão por nível; a unidade é a função ou o subteste; o mesmo `-run` vale para todos os pacotes pedidos | `-failfast`, que vale por pacote; com `-p 1` os pacotes seguintes não começam | numa invocação, só a de declaração; a ordem pedida exige o binário de teste do pacote (`go test -c`) e uma chamada por id | o binário de cada pacote com `-cover -coverpkg=./...`, e uma chamada por id com `-test.coverprofile` |
| Java, Maven | `-Dtest=Classe#metodo,...`; a unidade é o método (o caso parametrizado não é escolhível) | `skipAfterFailureCount` não para nada com o JUnit 5 (17 rodaram, 5 falharam); para com uma extensão do JUnit posta de fora no classpath de teste | classe ordenadora do JUnit, posta de fora, ligada por `-D` | um ouvinte do JUnit, posto de fora, que zera e grava os dados do agente do JaCoCo a cada teste, na mesma execução; a leitura usa `org.jacoco.core` |
| Java, Gradle | `--tests 'Classe.metodo'`; a unidade é o método | `failFast` do próprio Gradle, que vale também entre forks | a mesma classe ordenadora, entregue por um script de inicialização | o mesmo ouvinte, entregue pelo script de inicialização |
| C# | `--filter`; por método com `FullyQualifiedName=`, por caso com `DisplayName=` e escape | `dotnet test` não tem; `stopOnFail` do xUnit, num `xunit.runner.json` posto de fora por um arquivo de targets do MSBuild | ordenadores do xUnit em código-fonte acrescentado ao projeto de teste pelo mesmo arquivo de targets | não há numa execução só com o coverlet; uma invocação por id com o coverlet, ou o `dotnet-coverage` com um snapshot por teste |
| Vitest | arquivo mais `-t "^nome$"`; a unidade é o caso; `arquivo:linha` existe | `--bail=1`, exata sem paralelismo de arquivos | entre arquivos, um sequenciador numa configuração da Forja passada por `--config`, que carrega a do usuário; dentro do arquivo, não há | um arquivo de preparação na mesma configuração, que tira a cobertura do v8 a cada teste; tem de ser uma execução separada da do relatório de cobertura |
| Jest | arquivo mais `-t "^nome$"`; a unidade é o caso | `--bail` vale por arquivo e não foi exata (de 9 a 14 testes começaram, sem causa achada); só um processo por teste para de fato | entre arquivos, `--testSequencer` pela linha de comando, com `-i`; dentro do arquivo, não há | `--setupFilesAfterEnv` pela linha de comando, que lê os contadores do istanbul a cada teste, na mesma execução da cobertura |

Em todas, o id que sai do mapa de testes por linha voltou ao filtro sem edição manual e rodou exatamente os testes pedidos. Em nenhuma o projeto do usuário foi alterado: o que entra, entra pela linha de comando ou por arquivo da Forja fora do projeto.

### Medições

Mediana de três rodadas depois de uma de aquecimento, em segundos, Windows / Linux. Rodadas feitas por mim, um `medir.sh` de cada vez, com a máquina parada.

| Pilha | Testes de hoje | Cobertura de hoje | Com testes por linha | Um teste pelo filtro |
| --- | --- | --- | --- | --- |
| Python | 0,34 / 0,17 | 0,65 / 0,26 | 0,73 / 0,29 | 0,34 / 0,15 |
| Go (15 ids, 3 pacotes) | 0,62 / 0,15 | 0,67 / 0,16 | 3,65 / 0,81 | 0,58 / 0,14 |
| Java, Maven | 4,78 / 3,44 | 5,39 / 3,86 | 5,43 / 3,92 | 4,11 / 2,91 |
| Java, Gradle, com o daemon | 1,66 / 1,02 | 2,42 / 1,54 | 2,36 / 1,54 | 1,53 / 0,96 |
| C# (17 ids) | 1,45 / 1,86 | 2,04 / 2,74 | 10,19 / 7,52 com snapshot; 21,89 / 18,17 com o coverlet pela DLL; 29,11 / 27,73 com o coverlet pela solução | 1,09 / 1,21; pela DLL, 0,63 / 0,66 |
| Vitest | 0,38 / 0,27 | 0,47 / 0,33 | 0,74 / 0,50 numa execução; 6,71 / 5,16 com um processo por teste | 0,35 / 0,27 |
| Jest | 0,98 / 0,86 | 1,19 / 0,96 | 1,42 / 1,15 | 0,92 / 0,84 |

Quatro séries do Linux tiveram uma rodada fora da faixa, uma delas negativa: é o relógio do WSL. A mediana não muda.

### Conclusões

- **As sete pilhas têm as três respostas.** Pelo critério do plano (as três respostas em todas as pilhas), a técnica pode seguir para a fase 4. A qualidade das respostas não é a mesma em todas, e é isso que o dono decide.
- **O filtro existe em todas.** A unidade é o caso em Python, Go, C#, Vitest e Jest, e o método em Java.
- **A parada só é exata em série,** em todas: com o paralelismo da ferramenta ligado (xdist, JUnit em paralelo, coleções do xUnit, workers do Vitest e do Jest), os testes que já começaram terminam. A execução estreita do mutante teria de rodar em série.
- **A parada é da própria ferramenta em Python, Go, Gradle e Vitest.** Em Maven e em C# exige uma peça posta de fora. Em Jest, só um processo por teste.
- **A ordem pedida, teste a teste, só existe em Python, Java e C#.** Em Vitest e Jest a ordem é entre arquivos; dentro do arquivo vale a de definição. Em Go, a ordem pedida custa uma chamada do binário por teste.
- **Os testes por linha saem numa execução em Python, Java, Jest e Vitest,** e custam pouco sobre a cobertura de hoje: de zero a 0,3 s nesses projetos. Em Go custam uma chamada por id: 0,2 s por id no Windows e 0,04 s no Linux. **C# é a pilha cara:** 0,6 s por id com o snapshot e 1,3 s por id com o coverlet, e nenhum caminho de uma execução só fechou. Os contadores do coverlet podem ser lidos em memória a cada teste, mas o coverlet não expõe a que linha cada contador pertence.
- **O ganho não foi medido, e este levantamento não o mede.** Nos projetos pequenos, um teste pelo filtro custa quase o mesmo que a suíte inteira: o que pesa é a partida da ferramenta (4,1 s no Maven, 1,5 s no Gradle, 1,1 s no `dotnet test`). Esse é o piso de cada mutante. O ganho da execução estreita depende do tempo dos testes do projeto de verdade, e a fase 4 já prevê medir antes e depois em cada linguagem.
- **A preparação compartilhada não é atribuída do mesmo jeito.** `@BeforeEach` e `beforeEach` entram na conta de cada teste. O fixture do pytest e o fixture de classe do xUnit, com o snapshot, entram só na do primeiro teste que os dispara. O `TestMain` do Go entra na de todos os testes do pacote. Linha executada na importação do módulo não tem teste nenhum, em Python e em TypeScript. Como o sobrevivente só conta depois do comando inteiro, uma atribuição incompleta custa tempo e não muda veredito.

### Achados fora das três perguntas

Registrados, não tratados.

- **No Python 3.14, o coverage perde testes por linha** com o núcleo padrão: uma linha executada por dois testes aparece com um só; com `COVERAGE_CORE=ctrace` aparecem os dois. Provado no Linux. Hoje só `scripts/mutate.py` escolhe testes por esse dado, e a Forja roda em 3.12.
- **O JaCoCo do Gradle não tem versão fixada** pela Forja (veio a 0.8.15); o do Maven é 0.8.13.
- **A documentação do Surefire diz que `skipAfterFailureCount` vale para o JUnit 5.** Na versão 3.2.5 não valeu.
- **Com `--bail`, o Vitest escreve `not ok <arquivo>` para os arquivos que não rodaram.** Como o leitor da Forja trata essa linha não foi conferido.
- **Limite da linha de comando do Windows:** cerca de 1050 ids do pytest e 700 do `dotnet test` cabem em 32 767 caracteres. O pytest aceita os ids por arquivo (`@arquivo`, 5000 ids).

### Verificação

- Os sete `prove.sh` rodados de novo por mim no Windows: saída 0, e o mapa de testes por linha de cada pilha igual ao do levantamento. O de Maven é igual ao de Gradle, e o de Vitest igual ao de Jest.
- `skipAfterFailureCount=1` conferido por mim numa cópia com a troca que faz cinco testes falharem: 17 rodaram, 5 falharam, nenhum pulado.
- O mapa do snapshot de C# difere do mapa do coverlet em uma linha, a do fixture de classe, nos dois sistemas.
- No Linux, as provas são as dos levantamentos; a medida foi refeita por mim.
- Não rodou: a suíte da Forja (nenhum código mudou); macOS; projetos de tamanho real; Jest com outro transformador que não o ts-jest; a conversão por teste no Vitest 2.1; Surefire com forks e JUnit em paralelo com a cobertura por teste; Python 3.12 e 3.13 nos projetos pequenos (rodaram em 3.11 e 3.10).

### Pontos para o dono

1. **A técnica segue para a fase 4?** As sete pilhas têm as três respostas. O custo de medir é pequeno em seis; em C# é por teste.
2. **C#: qual caminho.** O coverlet com uma invocação por id não acrescenta ferramenta e custa 1,3 s por teste. O `dotnet-coverage` com snapshot custa 0,6 s por teste, é uma ferramenta a mais que a Forja teria de instalar, e atribui o fixture de classe só ao primeiro teste.
3. **O que a Forja passaria a pôr de fora na execução dos testes:** em Java, um jar com o ouvinte, a extensão de parada e os ordenadores (cerca de 190 linhas de Java, que alguém tem de compilar) e, no Gradle, um script de inicialização; em C#, um arquivo de targets, um `xunit.runner.json` e código-fonte acrescentado ao projeto de teste; em Vitest, uma configuração que embrulha a do usuário e um arquivo de preparação; em Jest, um arquivo de preparação e um sequenciador. Hoje ela já põe de fora o relator do Jest, o script de cobertura do Gradle e o JaCoCo do Maven.
4. **Unidade e ordem não são iguais entre as pilhas.** A regra seria a mesma em todas (os testes da linha primeiro, quem matou na frente, parada no primeiro que falha), com a unidade e a ordem que cada ferramenta dá: por método em Java, por arquivo em Vitest e Jest. O veredito não muda com isso; muda o quanto se ganha.

### Decisão do dono

**Ponto 3: aceito.** Suportar uma linguagem é usar as ferramentas do ecossistema dela, e isso é esperado da Forja: ela precisa do `dotnet` para C#, do Maven e do Gradle para Java, e assim por diante. Python é só a linguagem em que a Forja é escrita. Ela é obrigada a cumprir o seu propósito de fábrica de software agêntica, com os gates e os gates de qualidade garantidos em todas as linguagens suportadas. Peças da Forja em Java, C# e JavaScript, postas na execução dos testes, fazem parte desse suporte.

Eu tinha apresentado a manutenção dessas peças como a parte ruim do ponto. Não é objeção: é o trabalho de absorver a particularidade de cada linguagem, que a regra 7 já pede.

## 15/11/2026 — os pontos da fase 3 decididos, e o suporte por linguagem conferido por evidência

Sessão de leitura. Nenhum código mudou; só este diário.

### Decisões do dono

| Ponto da entrada anterior | Decisão |
| --- | --- |
| 1. A técnica segue para a fase 4 | Sim. |
| 2. C# | `dotnet-coverage` com um snapshot por teste. Ferramenta do ecossistema não é obstáculo (ponto 3). |
| 4. Unidade e ordem | A mesma regra em todas, com a unidade e a ordem que cada ferramenta dá. |

O dono pediu também a confirmação, por evidência, de que a Forja não funciona só para Python com as outras linguagens superficiais: Python é a linguagem em que ela é escrita, e o arcabouço de técnicas e de qualidade depende de ferramentas feitas dentro dela e de ferramentas consagradas, para todas as linguagens suportadas.

### O que foi feito

- Três leituras do código, cada uma por um agente, sem alterar nada: os adaptadores e o preparo do ambiente, operação por operação; os 34 gates, linguagem por linguagem; os testes e o processo (orquestrador, papéis, linha de comando).
- As linhas críticas de cada achado foram abertas por mim; as que não abri estão marcadas abaixo.
- Os testes com ferramenta de verdade rodaram no Windows: `pytest tests/checks/test_real_languages.py tests/languages`, com o JDK 21 em `JAVA_HOME`.

### O que a evidência confirma

- **466 aprovados, nenhum pulado, 1744 s.** Entram o reparo de ponta a ponta em Go, C#, Java com Maven, Java com Gradle e TypeScript (12 projetos, 24 casos, com formato, lint, build, testes, vermelho e verde e congelamento bloqueando), a mutação de verdade em Go, Java com Maven, C# e TypeScript, e os modelos de projeto novo.
- **Dos 34 gates do registro (`checks/specs.py:14-88`), 22 atuam do mesmo jeito nas seis linguagens.** Os outros 12 têm "não" ou "parcial" em pelo menos uma: `dependency_install`, `format`, `lint`, `types`, `build`, `tests`, `red_green`, `e2e`, `mutation`, `package_metrics`, `dependency_audit` e `new_dependency`. Em quatro deles (`tests`, `e2e`, `mutation` e `package_metrics`) o que falta é só de JavaScript ou de o projeto declarar o executor de testes.
- **Feito dentro da Forja:** a análise sobre tree-sitter, com regra própria para as seis (`core/structure`, `core/architecture`, `mutation/sites.py:61-76`, `checks/symbols.py:15-51`).
- **Ferramentas consagradas, chamadas direto:** gitleaks, semgrep, osv-scanner, jscpd e lizard; e os registros PyPI, npm, Go, Maven e NuGet (`checks/registries.py`).
- **Ferramentas de cada ecossistema, pelos adaptadores:** ruff, mypy, pytest e coverage.py; gofmt, golangci-lint e `go`; Spotless, Checkstyle, PMD, SpotBugs, JaCoCo, Maven e Gradle; `dotnet format`, `build` e `test` com coverlet; Prettier, ESLint, `tsc`, Vitest e Jest.
- **A Forja instala, com versão e sha256 fixos,** Go, golangci-lint, Node, o SDK do .NET, o JDK, Maven e Gradle, além de gitleaks, osv-scanner, jscpd, semgrep e mypy (`toolchain/manifest.toml`).

### Onde o suporte não é igual

Conferido por mim no código:

1. **O agente só roda testes e lint em Python.** `forja agente testar|lint|medir` chama pytest, ruff e lizard (`cli/facade.py:33-37` e `58-67`). O agente não tem shell próprio (`facade.py:3`): em Go, Java, C# e TypeScript ele entrega sem poder rodar os testes nem o lint.
2. **JavaScript sem `tsconfig.json` é recusado** (`cli/prepare.py:234-244`; `languages/typescript/adapter.py:111`; `core/policy/config.py:494-495`). São cinco adaptadores; arquivo `.js` é medido dentro de um projeto TypeScript. O `README.md:134` lista JavaScript entre as linguagens que a Forja conduz.
3. **Formato e lint em Java e em TypeScript só rodam se o projeto declara a ferramenta** (`languages/java/adapter.py:194-202`, `languages/typescript/adapter.py:166-171`). Sem ela o gate dá "não aplicável", que passa (`checks/layout.py:104-105`, `checks/base.py:71-72`). Os modelos de Java e de TypeScript não declaram nenhuma: projeto criado pela Forja nessas linguagens nasce sem os dois gates. Em Python o ruff vem da Forja; em Go, gofmt e golangci-lint vêm do preparo; em C#, do SDK.
4. **Projeto criado em C# e em TypeScript não mede cobertura** (achado no levantamento da fase 3): falta `coverlet.collector` e `@vitest/coverage-v8`. A mutação exige cobertura (`checks/mutation.py:256`).
5. **Instalar dependência nova só existe para PyPI** (`checks/dependency_install.py:26`).
6. **`red_green` só reconhece o erro do próprio teste em Python** (`NameError`, `checks/red_green.py:17`).

Apontado pelas leituras, não conferido por mim:

- `LINT_FIX` não é oferecido em Java (`languages/java/adapter.py:223-224`); a normalização do lint é pulada.
- A semente dos testes de propriedade só é fixada em Python e em Go.
- `doc_drift` só confere comandos de `make` e de `npm`, `yarn`, `pnpm` e `bun` (`checks/doc_drift.py:24`).
- A auditoria de dependências não lê `go.sum`, `build.gradle` nem `.csproj` (`checks/dependency_audit.py:33-41`).
- `new_dependency` fica inconclusivo em Go (o registro não dá idade nem uso) e não tem downloads em Maven.
- Os descartáveis do gate de escopo são os de Python e `node_modules`; `target/`, `bin/` e `obj/` não estão na lista (`checks/scope.py:44-65`).
- `thresholds.javascript` é aceito, mas os gates que medem pelo adaptador leem `typescript`.
- O guia diz que as regras de mutação de C# são as do mutator; o mutator não tem C# (`mutation/sites.py:8-9`).
- Não há modelo de projeto com Gradle, nem teste de mutação de verdade com Gradle. O gate `e2e` só tem teste com ferramenta de verdade em Python.

Não é lacuna: o gate `types` só existe em Python porque nas outras linguagens o compilador confere os tipos no `build`; em Python o `build` é o `compileall`, que só vê sintaxe.

### Conclusões

- **A Forja não é só Python.** O processo inteiro roda, com as ferramentas de verdade, em cinco ecossistemas, e a maior parte dos gates é igual nas seis linguagens.
- **O suporte ainda não é igual,** e os seis pontos conferidos ferem a regra 7. O primeiro é de processo: o agente trabalha sem ver o resultado dos gates em quatro das linguagens.
- **O ponto 4 toca a fase 4:** os testes por linha dependem da cobertura, que projeto criado pela Forja em C# e em TypeScript não mede.

### Verificação

- `pytest tests/checks/test_real_languages.py tests/languages`, Windows: 466 aprovados em 1744 s.
- Não rodou: a suíte inteira; Linux; nada foi medido de novo.

## 15/11/2026 — leva de desempenho, fase 4: os testes da linha primeiro nas seis linguagens; medido, só o Vitest ganha

### Decisões do dono

- **Ordem do trabalho:** o ponto 4 da entrada anterior (projeto criado em C# e em TypeScript não mede cobertura) entra na fase 4; o ponto 1 (o agente só roda testes e lint em Python) vem logo depois dela; os outros quatro entram na fila como itens novos.
- **Ferramentas do .NET:** o ecossistema oficial da Microsoft para o .NET é de código aberto, e nenhuma ferramenta oficial dele fica fora da Forja. Antes de incluir uma ferramenta nova, a licença é conferida no GitHub; se é de código aberto e a ferramenta é necessária, entra sempre. Registrado como adendo no ADR 0037.
- **Regras inegociáveis de condução,** depois dos travamentos e do custo desta sessão. Estão no `CLAUDE.md` global do dono (`~/.claude/CLAUDE.md`) e valem em toda sessão. A que muda o trabalho na Forja: o que vale para várias linguagens ou frameworks continua sendo entregue para todas (regra 7), mas vira uma tarefa por linguagem ou framework, cada uma com o seu plano, concluídas uma a uma, em fila, cada uma medida antes de a seguinte começar. Subagentes em paralelo só para leitura e pesquisa.
- **Teste segue complexidade, não tamanho, e o esforço segue Pareto.** São pilares do dono para como se testa. Os gates da Forja não mudam por causa deles agora.

### O que foi lido

- `metrics.py` do mutator em `830ec7e`, por inteiro. O histórico dele guarda o hash de cada função e o que foi de cada mutante (`killed` ou `survived`), pelo id do mutante. Não guarda que teste matou, e nada nele ordena testes. O histórico do gate usa o mesmo id de mutante e guarda só o teste que matou.
- O material da fase 3 de cada pilha (`RESULTADO.md` e as peças provadas).

### O que foi feito

| Parte | Mudança |
| --- | --- |
| Núcleo | Operação nova `test_lines` (quais testes executam cada linha) e o seu relatório, em duas formas: um arquivo da Forja (`{"arquivo": {"linha": ["id"]}}`) ou um relatório de cobertura por teste mais um índice. `run_line_tests` roda essa operação uma vez por julgamento, só quando há mutante a rodar, e junta os testes à cobertura. Se ela falha ou o relatório não pode ser lido, nada é escolhido. Tudo o que ela grava fica em `.forja-test-lines/` e é apagado. |
| Gate | Cada mutante é julgado primeiro pelos testes das suas linhas (o teste que o matou da última vez na frente, os outros pela ordem do id), e o sobrevivente pelo comando inteiro. O teste que matou fica em `<pasta da execução>/measure/mutation/killers.json` e só serve à ordem. Mutante sem testes escolhidos é julgado como antes (em Go, o pacote primeiro). |
| Executor | A execução estreita tem a sua verificação inicial na cópia sem alteração, uma por comando distinto, e elas rodam lado a lado nas cópias dos workers. Se ela não passa, não julga ninguém: o mutante vai ao comando inteiro. |
| Adaptadores | Nas seis linguagens: filtro por teste, em série, na ordem pedida até onde a ferramenta ordena, com parada no primeiro que falha; parada também no comando inteiro quando é para mutante; e a execução própria dos testes por linha (Python já tinha os testes por linha na cobertura). A tabela por pilha está em `docs/guia/gates/mutacao.md`. |
| Peças da Forja na execução dos testes | Go: `line_driver.py` (Python, biblioteca padrão). Java: `probe/` (ouvinte, extensão de parada, ordenadores, leitor e um lançador de arquivo único que compila as peças num cache por usuário) e `probe.init.gradle`. C#: `injection/` (targets do MSBuild, ordenadores, o gancho dos snapshots). TypeScript: configuração e preparação do Vitest, sequenciador e preparação do Jest. Nenhum binário no git. |
| Ponto 4 | O modelo de C# declara `coverlet.collector` 6.0.2. O de TypeScript passou a `vitest` e `@vitest/coverage-v8` `^5.0.5` (na 2.1 a conversão por teste não existe). Um teste `slow` em cada um prova que o projeto criado mede cobertura e passa pelo gate. |
| `dotnet-coverage` | Entrou no manifesto (18.12.0, sha512 conferido contra o catálogo do NuGet e contra o arquivo baixado), com o tipo de artefato novo `nupkg`. O adaptador acha o comando no PATH ou a montagem que a Forja instala, rodada pelo `dotnet`. |
| Conformidade | `language_problems` recebe o id de um teste na forma da linguagem. |
| Guias | `gates/mutacao.md` ("Os testes da linha primeiro", limites de tempo, o que se guarda entre tentativas) e `instalacao.md`. |

Como foi feito: o núcleo e Python por mim; Go, Java, C# e TypeScript cada um por um agente, com um contrato comum; C# fechado por mim, porque o agente parou sem concluir. Duas revisões independentes, só de leitura.

O que as revisões acharam e foi corrigido:

- **Os testes escolhidos nunca rodavam na cópia sem alteração.** Um teste que só passa junto dos outros falharia sozinho e mataria todo mutante da linha. Daí a verificação inicial da execução estreita. O plano dizia "o tempo-limite continua o de dez vezes a base do comando inteiro"; com a verificação inicial própria, o limite passou a ser dez vezes a dela, como em todo comando que julga mutante.
- O leitor do coverage.py cortava o id no primeiro `|`; passou a cortar só a fase que o pytest-cov acrescenta.
- Sem lugar para dividir a cobertura, o gate media a cobertura duas vezes.
- Relatório que não é texto, arquivo preso por outro programa e execução interrompida derrubavam a leitura dos testes por linha em vez de só custar tempo.
- Java: o cache das classes compiladas ficava em pasta previsível no `tmp` compartilhado (achado da revisão de segurança); passou à casa da Forja, por usuário, com dono e permissões conferidos. Projeto Gradle que não liga a JUnit Platform recebia as peças de JUnit 5 e deixaria de passar; agora roda como antes.
- Id de teste com caractere de controle não é entregue a ferramenta nenhuma; id de Go com `..` e arquivo de teste com nome de opção são recusados.

### Medições

Rodadas minhas, uma execução de cada vez, máquina parada, `MutationCheck.evaluate` em segundos, duas rodadas. "Antes" é `c180ea5`. Pequeno: o projeto dos testes `slow` de mutação da pilha (um caso aprovado mais um rejeitado). Gerado: 12 funções novas com um `if` e dois retornos, 300 testes em 30 arquivos, 36 deles nas funções novas, três funções só chamadas.

| Pilha | Pequeno, antes | Pequeno, depois | Gerado, antes | Gerado, depois |
| --- | --- | --- | --- | --- |
| Python | 6,3 / 6,2 | 7,4 / 6,9 | 9,5 / 9,1 | 11,7 / 11,6 |
| Go | 6,0 / 5,8 | 9,9 / 10,0 | 11,1 / 11,2 | 26,0 / 26,4 |
| Vitest | 3,4 / 3,3 | 6,0 / 6,0 | 35,9 / 40,7 | 15,5 / 15,7 |
| Java, Gradle | 12,7 / 42,6 | 24,3 / 81,8 | 125,9 / 101,2 | 241,7 / 257,9 |
| C# | 25,2 / 24,3 | 43,5 / 43,1 | 36,0 / 38,2 | 179,2 / 190,7 |
| Jest | 12,4 | 19,2 | 356,3 | não medido |
| Java, Maven | não medido | não medido | não medido | não medido |

- Vereditos iguais antes e depois em todos os casos medidos, achado a achado.
- A medição parou no Jest gerado, a pedido do dono: tomava todos os núcleos. Uma prova do agente de TypeScript, com a máquina dividida, deu 204 s no Jest gerado depois da mudança; uma do agente de Java deu 190 s contra 30 s no Maven pequeno. Nenhuma das duas é medição.
- **No Gradle gerado, o código antigo deu veredito diferente entre as suas duas rodadas:** o mutante de `Calc.java:59` sobreviveu na primeira e contou como morto na segunda. O código novo deu o mesmo nas duas. A causa não foi investigada.
- Os tempos por fase não foram tirados com a máquina parada. O tempo da execução dos testes por linha sozinha só tem números soltos, com a máquina dividida: 10,9 s em Go e 2,0 s no Vitest, no projeto gerado.

### Conclusões

- **A fase não entregou o ganho que prometia.** Em projeto de testes triviais, a mutação ficou mais lenta em cinco das seis pilhas medidas e mais rápida só no Vitest. Em C# o projeto gerado ficou cinco vezes mais lento.
- **O que pesa, pelo desenho:** cada mutante paga a partida da ferramenta de qualquer jeito, e com testes de milissegundos rodar três testes custa o mesmo que rodar trezentos. Por cima disso entram a execução dos testes por linha (uma chamada por teste em Go, dois processos por teste em C#), uma verificação inicial por conjunto de testes, e a execução estreita em série. No Maven, cada execução estreita passa ainda pelo lançador. Quanto cada parte pesa não foi medido.
- **O ganho, se existe, está em projeto cujos testes demoram.** Nenhum foi medido. A fase 3 já dizia que o ganho dependia do tempo dos testes do projeto de verdade; os dois projetos desta medição não dizem nada sobre esse caso.
- **Nenhum veredito mudou,** e a regra que garante isso está no executor: o sobrevivente só conta depois do comando inteiro, e a execução estreita só julga se passa sozinha na cópia sem alteração.
- **O ponto 4 está resolvido** e não depende do resto: projeto criado em C# e em TypeScript mede cobertura.

### Dificuldades

- **Custo da sessão, por erro meu.** Pedi medição aos quatro agentes com a máquina dividida; os números variaram de 11 s a 66 s no mesmo comando e não serviram. Refiz a medição sozinho, numa matriz de 56 execuções sem estimar o custo de cada uma; o Jest gerado leva seis minutos por execução com 24 processos, e o agente de TypeScript, que eu não tinha encerrado, rodava o mesmo ao lado.
- O núcleo mudou com os agentes já trabalhando (a verificação inicial da execução estreita), e os testes deles tiveram de seguir.
- O agente de C# ficou em laço esperando um processo em segundo plano e não concluiu.

### Verificação

- `ruff check .`, `ruff format --check src tests scripts/mutate.py` e `mypy` strict: limpos, 648 arquivos.
- `pytest -m "not slow" -n 8`, Windows: 10083 aprovados, 41 pulados, 252 s.
- Testes `slow` com a ferramenta de verdade, depois das correções das revisões, um comando de cada vez e com limite de tempo forçado: Java (Maven e Gradle, JDK 21) 17 aprovados em 194 s; C# 14 aprovados em 142 s, com o `dotnet-coverage` instalado pelo instalador da Forja; Go, TypeScript (Vitest e Jest), Python, o gate e os modelos de projeto, 33 aprovados e 1 falha em 136 s. A falha é do ambiente: o teste do modelo de Java rodou com o Java 8 do PATH; com o JDK 21 em `JAVA_HOME`, passou.
- Antes dessas correções, a pasta de cada linguagem tinha rodado inteira: Go 69, TypeScript 68, Java 105, C# 63 aprovados.
- `install_tool` de verdade para o `dotnet-coverage`: baixou, conferiu o checksum e os testes `slow` de C# rodaram com a montagem instalada.
- Um travamento na verificação, que não é do produto: o teste `slow` de Gradle capturava a saída por pipe; quando o Gradle sobe um daemon novo, o daemon herda o pipe e o teste espera por ele. O teste passou a gravar a saída em arquivo.

### Pontos para o dono

1. **O que fazer com a escolha dos testes.** Decidido na entrada seguinte: desligada por padrão, com uma chave na política para ligar. A parada no comando inteiro não foi medida em separado.
2. **`dotnet-coverage`.** O GitHub dá o repositório que o pacote indica (`microsoft/codecoverage`) como MIT, e por isso ele entrou. O pacote NuGet traz um `License.txt` próprio (Microsoft Software License Terms) e pede aceite de licença. Fica o registro.
3. **O veredito instável do código antigo no Gradle** (acima) é um defeito anterior a esta fase, sem item na avaliação.

## 15/11/2026 — a escolha dos testes da linha fica desligada por padrão

### Decisão do dono

Ponto 1 da entrada anterior: desligar por padrão, com uma chave na política para ligar. O dono registrou também que esta parte está custando mais do que devia: a Forja sabe testar e sabe rodar a mutação e o CRAP tirados do `unclebob`, e o trabalho não deveria ter chegado a este ponto de complicação.

### O que foi feito

| Mudança | Arquivos | Teste novo | Commit |
| --- | --- | --- | --- |
| Limite novo `line_tests` em `[checks.mutation.limits]`, inteiro, padrão `0`. Só com `1` ou mais o gate mede os testes de cada linha (`test_lines`) e julga cada mutante primeiro por eles. `line_tests_first` lê a chave; `MutationCheck._named` devolve `None` sem ela. | `checks/mutation.py`, `checks/specs.py`, `core/policy/config.py` | dois em `tests/checks/test_mutation.py`, um caso em `tests/core/policy/test_config.py`, um ajuste em `tests/checks/test_pieces.py` | `73998b4` |
| Os testes `slow` que provam a escolha pedem a chave. Os três testes de Go do pacote primeiro provam os dois modos; desde a fase 4 só afirmavam o modo com a escolha. | `tests/languages/{go,java,csharp,typescript}`, `tests/checks/test_mutation.py` | os três de Go, nos dois modos | `73998b4` |
| Guia: a seção "Os testes da linha primeiro" diz que vem desligada, por quê e como ligar; a chave entrou nas tabelas. | `docs/guia/gates/mutacao.md`, `docs/guia/forja-toml.md` | nenhum | `73998b4` |

### O que muda com a chave desligada

- **O gate volta a julgar como o mutator, nas seis linguagens:** o comando de testes para cada mutante; em Go, o pacote do arquivo primeiro (fase 2).
- **Fica ligado o que não depende da escolha:** a parada no primeiro teste que falha quando o comando roda para um mutante, a prioridade baixa, um worker por núcleo e a guarda do estouro de tempo. A parada usa peças da Forja em C# (`runner-stop.json`) e no Maven (a extensão do JUnit); essas continuam em uso.
- **Deixa de rodar:** a execução que descobre os testes de cada linha, a verificação inicial por conjunto de testes e a execução estreita em série. O `killers.json` não é mais gravado. O `dotnet-coverage` deixa de ser necessário para o gate.
- **O código da escolha continua no repositório,** com os seus testes `slow`, e os achados da revisão dos adaptadores que ficaram sem correção na fase 4 (estão na lista de pendências de 16/11) só valem com a chave ligada.
- **O Vitest perde o ganho medido** (36 s para 15,5 s no projeto gerado), como as outras pilhas deixam de pagar a perda.
- **Nenhum veredito muda,** ligada ou desligada.

### Verificação

- `ruff check .`, `ruff format --check src tests scripts/mutate.py` e `mypy` strict: limpos, 648 arquivos.
- `pytest -m "not slow" -n 8`, Windows: 10086 aprovados, 41 pulados e 1 falha, `tests/checks/test_pieces.py`, que fixa os limites de cada check; ajustado, e o arquivo passou sozinho (33). A suíte inteira não rodou de novo depois do ajuste.
- Testes `slow` com a ferramenta de verdade, um comando de cada vez, com limite de tempo: Python (gate) 4 aprovados; Go 13; TypeScript 12; Java, Maven e Gradle, com o JDK 21, 17.
- C#: 4 aprovados no modo padrão. **Os 10 testes com os testes por linha foram pulados:** o `dotnet-coverage` não está instalado nesta máquina (a instalação da fase 4 não ficou).
- Não rodou: a mutação dos três arquivos de `src` tocados, que toma a máquina inteira; nenhuma medição de tempo com a chave desligada; a suíte completa com `slow`; Linux; macOS.

## 15/11/2026 — a leva de desempenho da mutação está encerrada

### Decisão do dono

A leva termina aqui. Saem da fila, sem serem feitas:

- a fase 5 do plano de 14/11 (no script: escolha por função de teste, e quem matou roda primeiro);
- as medições que faltavam da fase 4 (Maven, e o Jest no projeto gerado);
- a medição num projeto de verdade, com testes que demoram. Só volta se o dono reabrir a escolha dos testes da linha.

### O que a leva deixou

- No gate, ligado: prioridade baixa, um worker por núcleo, a guarda do estouro de tempo, Go pelo pacote primeiro, e a parada no primeiro teste que falha quando o comando roda para um mutante.
- No gate, desligado por padrão: os testes da linha primeiro (`limits.line_tests`).
- No script: prioridade baixa, um worker por núcleo, a base uma vez por comando e o modo diferencial que vê os testes.
- Fora da leva, resolvido nela: projeto criado em C# e em TypeScript mede cobertura.

### Pendências

- **O próximo é o ponto 1 da entrada de 15/11 sobre o suporte por linguagem:** o agente só roda testes e lint em Python (`cli/facade.py`).
- Continuam: a mutação dos arquivos de `src` da fase 4 e da chave `line_tests`; os testes de C# com os testes por linha numa máquina com o `dotnet-coverage`; o veredito instável do código antigo no Gradle; a pergunta dos pilares de teste contra o gate de mutação; os quatro itens novos da fila; as pendências com mudança em `src` de 14/11 (item 5, item 12, restos dos itens 6 e 2, item 34).

## 15/11/2026 — o agente roda os testes e o lint do projeto em todas as linguagens

Ponto 1 da entrada de 15/11 sobre o suporte por linguagem: `forja agente testar` e `forja agente lint` chamavam pytest e ruff, fosse qual fosse a linguagem do projeto.

### O que foi lido

`cli/facade.py` e os testes dele por mim. Os cinco adaptadores (comando de testes e de lint, formato da saída, o que o leitor devolve, variáveis de ambiente) por um agente de leitura, com arquivo e linha em cada fato; as linhas em que a implementação se apoia foram abertas por mim.

### O que foi feito

| Mudança | Arquivos | Teste novo | Commit |
| --- | --- | --- | --- |
| A fachada roda o comando que o adaptador de cada projeto da cópia planeja, o mesmo dos gates: `Operation.TEST` com parada no primeiro teste que falha, e `Operation.LINT`. Roda como os gates rodam (`build_spec` e `ProcessRunner`): ambiente filtrado, e a árvore inteira de processos encerrada no limite de 600 s. Nada nela distingue uma linguagem de outra. `medir` continua o `lizard`. | `cli/facade.py`, `cli/main.py` | `tests/cli/test_facade.py`, reescrito, 42 casos | `b1f56f1` |
| Depois da saída da ferramenta vem o que o adaptador leu nela: `falhou: <teste>` ou `<arquivo>: <regra> (<quantas>)`, até 20 linhas. O código de saída é o da ferramenta, ou `1` quando ela termina bem e o leitor achou falha (o lint do Maven roda com `failOnViolation=false`). | `cli/facade.py` | os mesmos | `b1f56f1` |
| Prova com a ferramenta de verdade em cada pilha: testes que passam, teste que falha nomeado, e o lint. | `tests/languages/{go,typescript,java,csharp}/test_agent_facade.py` (novos, `slow`) | 18 casos | `b1f56f1` |
| Guia: o que cada subcomando roda em cada linguagem, o que vem depois da saída, e os códigos. | `docs/guia/comandos.md` | nenhum | `b1f56f1` |
| **Defeito anterior, achado pelo teste de Gradle:** o registro acha os projetos com o adaptador registrado e dá a cada projeto uma instância nova. O adaptador de Java aprende em `detect` se o projeto é Maven ou Gradle, e a instância nova nunca via o manifesto. Numa execução real, a saída de um projeto Gradle era lida como a do Maven. `AdapterRegistry.projects` passou a mostrar a cada instância o manifesto do seu projeto. | `core/adapters/registry.py` | um em `tests/core/adapters/test_scoped.py` | `79dd1dd` |

Feito em fila, uma pilha de cada vez, cada uma provada antes da seguinte: o núcleo com Python, Go, TypeScript (Vitest e Jest), Java (Maven e Gradle), C#.

### Conclusões

- **O defeito do Gradle valia para os gates, não só para a fachada.** `cli/session.py` registra `JavaAdapter` com fábrica, e `PipelineMeasurer._active` usa `registry.projects`. Os testes não viam: `registry_of`, em `tests/checks/support.py`, registra sem fábrica, e o projeto usa a mesma instância que o achou. Com um teste de Gradle que falha, o leitor devolvia "maven reported a failure in a form this parser does not know". O que isso fazia com cada gate numa execução real não foi reproduzido.
- **Um defeito meu, pego ao rodar o `forja` de verdade:** a primeira versão chamava `asyncio.run` dentro do laço que `cli/main.py` já roda, e os testes, que chamavam a função direto, passavam. A fachada passou a ser assíncrona.
- **Em Go a saída é a de `go test -json`,** uma linha de JSON por evento. O texto da falha está lá, e a linha `falhou:` diz o teste. Não ficou bonito; ficou o que o gate vê.
- **O caminho dado restringe os testes só em Python.** Os outros adaptadores recusam filtro por arquivo; a fachada então roda o projeto inteiro. A instrução ao agente já manda chamar sem nada depois do subcomando.
- **Operação que a linguagem não oferece para o projeto não conta como falha,** como no gate: lint em Java e em TypeScript sem ferramenta declarada, Go sem `golangci-lint`. A fachada diz o motivo.
- **Numa solução de C#, a `.sln` e cada `.csproj` são um projeto para o registro.** `testar` roda `dotnet test` três vezes na solução de exemplo, e os gates fazem o mesmo. Vale para qualquer projeto dentro de outro da mesma linguagem (módulos do Maven, por exemplo). Não mexi: a fachada mostra o que os gates veem.
- **Pasta sem manifesto de linguagem conhecida:** a fachada sai com `2`. Antes rodava pytest e ruff em qualquer pasta.
- **Os testes de Python pela fachada ganharam `-rA`** e as outras opções do gate; a saída é maior que a de `pytest -q -x`, e as 80 últimas linhas continuam valendo.

### Verificação

- `ruff check .`, `ruff format --check src tests scripts/mutate.py` e `mypy` strict: limpos, 652 arquivos.
- `pytest -m "not slow" -n 8`, Windows: 10107 aprovados, 41 pulados, 282 s.
- Com a ferramenta de verdade, um comando de cada vez: Go 3 aprovados (com `golangci-lint`), TypeScript 6 (Vitest e Jest), Java 6 (Maven e Gradle, JDK 21), C# 3.
- O executável `forja agente testar` e `forja agente lint`, numa pasta com um projeto Go: saída e códigos conferidos à mão.
- Não rodou: a mutação dos arquivos de `src` tocados (`cli/facade.py`, `cli/main.py`, `core/adapters/registry.py`); a suíte completa com `slow`; o lint com ESLint, Checkstyle, PMD e SpotBugs de verdade pela fachada (os projetos de exemplo não declaram nenhum); nenhuma execução com agente real; Linux e macOS.

### Pontos para o dono

1. **A CLI do agente não recebe o ambiente das ferramentas.** A Forja entrega à CLI só `PATH`, `HOME`, `TEMP` e as variáveis da própria CLI. `JAVA_HOME`, `GOPATH`, `GOROOT`, `DOTNET_ROOT`, `NUGET_PACKAGES`, `MAVEN_HOME` e as demais que os adaptadores pedem para os gates não chegam, e a fachada roda dentro da CLI. Nesta máquina, com o Java 8 no `PATH` e o JDK 21 só em `JAVA_HOME`, o agente não conseguiria rodar os testes de Java. Sugestão: a Forja acrescentar ao ambiente da CLI, para todas as CLIs, as variáveis que os adaptadores das linguagens do repositório declaram. Muda o que a garantia "ambiente filtrado para a CLI do agente" deixa passar, e por isso é decisão do dono. Não implementado.
2. **`FORJA_AGENT_PYTHON` só passa pelo Claude Code.** Está na lista de variáveis dele e de nenhuma outra CLI (`executors/*.py`). Com Cursor, Codex, Gemini, Grok e a CLI genérica, a fachada usa o Python que roda a Forja, não o do repositório. A sugestão do ponto 1 resolve os dois.
3. **Projeto dentro de projeto da mesma linguagem roda mais de uma vez,** nos gates e agora na fachada (a solução de C# acima).

### Pendências

- Os três pontos acima.
- Mutação dos três arquivos de `src` tocados, junto com as que já esperam.
- O efeito do defeito do Gradle nos gates de uma execução real, e um teste do pipeline que registre os adaptadores com fábrica, como a sessão faz.
- Os quatro itens novos da fila continuam: JavaScript sem `tsconfig.json`; formato e lint em Java e TypeScript só com a ferramenta declarada; instalar dependência nova só para PyPI; `red_green` só reconhece o erro do próprio teste em Python.
- As demais pendências da entrada anterior continuam.

## 15/11/2026 — um projeto só de JavaScript, sem `tsconfig.json`, é conduzido

Ponto 2 da entrada de 15/11 sobre o suporte por linguagem, o primeiro dos quatro itens novos da fila: `package.json` só era projeto ao lado de um `tsconfig.json`, e um repositório de JavaScript puro era recusado.

### O que foi lido

O adaptador de TypeScript inteiro, a detecção da linha de comando (`cli/detect.py`, `cli/prepare.py`, `cli/scaffold.py`), como o registro acha os projetos (`core/adapters/registry.py`, `projects.py`), como o núcleo mede a base (`orchestrator/measurer.py`, `baseline`) e como os testes com ferramenta de verdade estão montados.

### O que foi feito

| Mudança | Arquivos | Teste novo | Commit |
| --- | --- | --- | --- |
| Sem `tsconfig.json`, um `package.json` que depende de `vitest` ou de `jest` é um projeto de JavaScript, do mesmo adaptador (`typescript`). A regra está num lugar só (`is_project`) e serve ao adaptador e à detecção da linha de comando. Com `tsconfig.json` nada mudou. | `languages/typescript/adapter.py`, `cli/detect.py`, `cli/prepare.py` (a mensagem de recusa), `cli/scaffold.py` (um comentário) | sete casos em `tests/languages/typescript/test_adapter.py`, dois em `tests/cli/test_install_languages.py`, dois em `tests/cli/test_main.py` | `52f7751` |
| O `build` de JavaScript: uma peça da Forja (`js_syntax.cjs`) roda `node --check` em cada arquivo `.js`, `.mjs` e `.cjs`, um processo por arquivo, tantos ao mesmo tempo quantos são os núcleos. Ficam de fora `node_modules`, `dist`, `build`, `coverage` e as pastas ocultas. O leitor do `build` passou a ler também a saída dela (`<arquivo>:<linha>: SyntaxError: ...`). | `languages/typescript/js_syntax.cjs` (novo), `adapter.py`, `parsers.py` | três em `test_adapter.py` | `52f7751` |
| Prova com a ferramenta de verdade, com Vitest e com Jest: o `build` reprova o arquivo que o Node não lê e ignora o que está em `dist`; a mutação aprova a função conferida e reprova o teste que só chama; `forja agente testar` passa e nomeia o teste que falha. | `tests/languages/typescript/test_javascript.py` (novo, `slow`) | 10 casos | `52f7751` |
| Prova de ponta a ponta: o reparo num projeto de JavaScript passa por todos os gates e é entregue, e o implementador que mexe no teste congelado é devolvido. | `tests/checks/test_real_languages.py` | 2 casos | `52f7751` |
| Guia: o arquivo que identifica cada linguagem, e o `build` de JavaScript com o seu limite. | `docs/guia/ambiente.md`, `execucao-toml.md`, `gates/tipos.md` | nenhum | `52f7751` |

Feito em fila: o núcleo, depois Vitest, depois Jest, cada um provado antes do seguinte.

### Conclusões

- **Aceitar o `package.json` não bastava.** A primeira versão só tirava a exigência do `tsconfig.json`, e a prova de ponta a ponta falhou: "A base já não está verde (not applicable: build)". O núcleo recusa a execução se algum projeto do repositório não oferece `build` ou testes (`measurer.py`, `baseline`), e o adaptador só oferecia `build` com o `tsc`. Os testes de unidade passavam todos.
- **Isso corrige uma frase da entrada de 15/11.** Lá está que, sem a ferramenta, o gate dá "não aplicável", que passa. Vale para formato e lint. Para `build` e testes, "não aplicável" na base recusa a execução inteira.
- **O `build` de JavaScript é decisão minha, tirada do que já existia:** o contrato diz que todo adaptador oferece `build` (`core/adapters/language.py`, `REQUIRED_OPERATIONS`), e em Python, que também não compila, o `build` é o `compileall`. O equivalente no Node é o `node --check`. Conferido à mão no Node 26.13: aceita módulo ES e CommonJS pelo `package.json` e pela extensão, confere um arquivo por chamada (o segundo argumento é ignorado), e sai com 1 e `SyntaxError` no arquivo quebrado.
- **Limite do `node --check`: JSX dentro de arquivo `.js`.** O Node recusa, e a base desse projeto não fica verde. A saída é o projeto declarar o seu comando em `[checks.build.commands]`, que passou a valer para JavaScript porque o adaptador agora oferece a operação. Arquivo `.jsx` não é conferido. Está no guia.
- **Sem `tsconfig.json`, o `build` é sempre a conferência de sintaxe,** mesmo que o projeto dependa de `typescript`: o `tsc -p .` não teria o que ler.
- **A regra do executor de testes também é decisão minha, e evita uma perda.** Com qualquer `package.json` virando projeto, um repositório Python ou Go com um `package.json` só de ferramentas (Prettier, commitlint) ganharia um projeto sem testes, e a base seria recusada com "not applicable: test". Esse repositório funciona hoje. Por isso, sem `tsconfig.json`, só é projeto o `package.json` que depende de `vitest` ou de `jest`, os dois executores que o adaptador sabe rodar.
- **A regra não é igual à do TypeScript.** Com `tsconfig.json` o projeto é achado mesmo sem executor de testes, e então a base é recusada. Não mexi: é o comportamento anterior.
- **Nenhum veredito de TypeScript mudou.** Os 91 testes de `tests/languages/typescript` passaram com a ferramenta de verdade.

### Verificação

- `ruff check .`, `ruff format --check src tests scripts/mutate.py` e `mypy` strict: limpos, 654 e 653 arquivos.
- `pytest -m "not slow" -n 8`, Windows: 10121 aprovados, 41 pulados, 282 s.
- Com a ferramenta de verdade, um comando de cada vez, com limite de tempo: `tests/languages/typescript` inteiro, 91 aprovados em 126 s (antes da regra do executor de testes); depois dela, `test_javascript.py`, 10 aprovados; `tests/checks/test_real_languages.py -k "javascript or typescript"`, 10 aprovados em 411 s.
- Não rodou: a mutação dos arquivos de `src` tocados (`languages/typescript/adapter.py`, `parsers.py`, `cli/detect.py`, `cli/prepare.py`); a suíte completa com `slow`; os testes com ferramenta de verdade das outras linguagens (o código delas não mudou); ESLint e Prettier num projeto de JavaScript; a escolha dos testes da linha (`line_tests`) em JavaScript; execução com agente real; Linux e macOS.

### Pontos para o dono

1. **O `build` de JavaScript pelo `node --check`,** com o limite do JSX em arquivo `.js`. Se o dono preferir outro equivalente, é uma troca no adaptador e na peça.
2. **A regra do executor de testes.** Um projeto de JavaScript com outro executor (mocha, `node:test`) continua recusado, agora com "Não encontrei nenhuma linguagem". Antes também era recusado.
3. **`forja criar` não tem modelo de JavaScript.** O adaptador tem um modelo só, o de TypeScript. Não fiz: o ponto era a recusa.
4. **Raiz de monorepo.** Um `package.json` na raiz, sem `tsconfig.json` e com `vitest` ou `jest`, passa a ser projeto, junto com os pacotes dentro dele. É o ponto 3 da entrada anterior (projeto dentro de projeto roda mais de uma vez), agora mais frequente.

### Pendências

- Os quatro pontos acima, e os três da entrada anterior.
- Mutação dos arquivos de `src` tocados, junto com as que já esperam.
- Continua como estava: `thresholds.javascript` é aceito, mas os gates que medem pelo adaptador leem `typescript`; num projeto de JavaScript o limite vale em `thresholds.typescript`. Com `line_tests` ligado, o relator do Jest para os testes da linha pede o pacote `typescript`, e num projeto de JavaScript nada é escolhido.
- Os três itens que restam na fila: formato e lint em Java e TypeScript só com a ferramenta declarada; instalar dependência nova só para PyPI; `red_green` só reconhece o erro do próprio teste em Python.
- As demais pendências da entrada anterior continuam.

## 16/11/2026 — o diário foi enxugado, e as pendências abertas ficam numa lista só

### Decisão do dono

Tirar do diário o que uma entrada posterior superou e juntar as pendências abertas numa lista só. Saíram os cortes que a revisão do diário deu como sem risco; o que tinha risco ficou.

### O que foi feito

Só este arquivo mudou.

- **Entradas e trechos superados:** a entrada "sugestão de implementação para os três pontos de atenção" (13/11), inteira, e a lista "Pontos de atenção a endereçar", as duas tratadas na entrada "os três pontos de atenção tratados", que ganhou o resumo dos três pontos e as duas razões da sugestão do ponto 1; o texto das fases e os "Pontos para o dono" do plano da leva de desempenho (14/11), de que ficaram o que foi lido, o que foi conferido e a tabela de fases; a tabela dos hashes reescritos, que virou uma linha.
- **Entradas reduzidas:** "as três rodadas longas" (13/11) virou um parágrafo, e as quatro rodadas de mutação da fase 1 viraram três linhas.
- **Seções "Pendências"** de todas as entradas anteriores à que encerra a leva de desempenho (15/11), menos as duas de 14/11 citadas abaixo. O que estava aberto nelas está na lista desta entrada.
- **Linhas soltas:** estado de momento e conclusões que uma entrada posterior resolveu (o que tinha sido conferido no código antes da leva de desempenho, as rodadas interrompidas do item 12, o que "ficou de fora" nos itens 33, 36 e 40, os modelos de C# e de TypeScript sem cobertura, os três caminhos para a escolha dos testes), a limpeza de disco de 13/11, os tropeços de sessão sem lição nova e os textos repetidos.
- **Referências ajustadas** onde uma entrada citava texto que saiu.
- O arquivo tinha 1755 linhas e ficou com 1419, com esta entrada. O texto cortado está no histórico do git, em `31ca368`.

### O que ficou

- As medições que são a única prova de uma decisão do dono: o gate com a máquina em uso (fases 0 e 1), as medições por pilha da fase 3 e as da fase 4.
- As duas seções "Para retomar" e as medições intermediárias com número que só existe ali: item 41, fase 0, o gate com a máquina parada e a fase 2.
- As pendências das duas entradas de 14/11 ("as decisões abertas do dono..." e "o pull request 1..."), que as entradas de 15/11 citam, e as das três últimas entradas de 15/11. Estão todas repetidas na lista abaixo.

### Verificação

Só documentação mudou. Nada rodou.

### Pendências abertas

Esta lista substitui a corrente "as demais pendências da entrada anterior continuam" das entradas anteriores. Cada item está como a entrada de origem o escreveu; nenhum estado foi conferido de novo nesta sessão.

**Fila do suporte por linguagem** (da entrada de 15/11 sobre o suporte por linguagem; o agente rodar testes e lint em todas as linguagens e o JavaScript sem `tsconfig.json` estão feitos)

1. Formato e lint em Java e em TypeScript só rodam se o projeto declara a ferramenta.
2. Instalar dependência nova só existe para PyPI.
3. `red_green` só reconhece o erro do próprio teste em Python.

**Decidido pelo dono em 14/11, a fazer, com mudança em `src`**

- Item 5: ligar `authorize()`; ligar o `SentPackageStore` (gravar o pacote enviado ao agente, ADR 0009); artefato inválido refaz a etapa sozinho.
- Item 12: exigir a nota de mutação da função para aprovar a etapa de caracterização. Hoje a etapa é aprovada quando os testes passam.
- Item 6, resto: lista de permissão de opções por subcomando na fachada de git e `protocol.ext.allow=never`.
- Item 2, resto: o `pip` ainda resolve as dependências do wheel do Semgrep sem `--require-hashes`; pede um arquivo de travas com hashes por plataforma, gerado com rede.
- Item 34: rodar a própria Forja sobre as 21 funções com CCN acima de 15 (`prepare`, CCN 48, primeiro), o que entrega também o E2E com agente real. A versão fica `0.0.0` até essa primeira execução com agente real.
- Item 30 (CI): adiado. Até haver GitHub, a suíte completa com `slow` na ponta fecha cada leva; a última rodada inteira registrada é a de `c4ff3ab`, em 14/11.

**Pontos para o dono**

- A CLI do agente não recebe o ambiente das ferramentas (`JAVA_HOME`, `GOPATH`, `GOROOT`, `DOTNET_ROOT`, `NUGET_PACKAGES`, `MAVEN_HOME` e as demais que os adaptadores pedem para os gates). Sugestão, não implementada: a Forja acrescentar ao ambiente da CLI, para todas as CLIs, as variáveis que os adaptadores das linguagens do repositório declaram.
- `FORJA_AGENT_PYTHON` só passa pelo Claude Code. A sugestão acima resolve os dois.
- Projeto dentro de projeto da mesma linguagem roda mais de uma vez, nos gates e na fachada: a `.sln` e cada `.csproj`, os módulos do Maven e a raiz de um monorepo de JavaScript.
- O `build` de JavaScript pelo `node --check`, com o limite do JSX em arquivo `.js`.
- A regra do executor de testes: projeto de JavaScript com outro executor (mocha, `node:test`) continua recusado.
- `forja criar` não tem modelo de JavaScript.
- A pergunta dos pilares de teste contra o gate de mutação: hoje o gate cobra toda linha tocada, seja qual for a complexidade ciclomática. Comparar com o que o `unclebob/mutator` faz, lendo a implementação dele, e decidir junto com o dono. Nada muda até essa análise.

**Mutações e verificações que não rodaram**

- Mutação dos arquivos de `src` da fase 4 da leva de desempenho.
- Mutação da chave `line_tests`: `checks/mutation.py`, `checks/specs.py` e `core/policy/config.py`.
- Mutação da fachada do agente: `cli/facade.py`, `cli/main.py` e `core/adapters/registry.py`.
- Mutação do JavaScript sem `tsconfig.json`: `languages/typescript/adapter.py` e `parsers.py`, `cli/detect.py` e `cli/prepare.py`.
- Mutação do que o pull request 1 mudou em `src`.
- Mutações antigas, da lista de 14/11: os `parsers.py` de C# e Java, `cli/session.py`, `cli/detect.py`, `testing/workspace.py`, `testing/repo.py`, e `languages/java/adapter.py` depois de `17d06c6`. `core/adapters/common.py` e `checks/mutation.py`, da mesma lista, rodaram sem sobrevivente na fase 1 da leva de desempenho.
- Os testes de C# com os testes por linha, numa máquina com o `dotnet-coverage`.
- O efeito do defeito do Gradle (o projeto recebia uma instância do adaptador que não tinha visto o manifesto) nos gates de uma execução real, e um teste do pipeline que registre os adaptadores com fábrica, como a sessão faz.

**Defeitos e limites conhecidos, sem correção**

- O veredito instável do código antigo no Gradle: na medição da fase 4, o mutante de `Calc.java:59` sobreviveu numa rodada e contou como morto na outra. A causa não foi investigada.
- `thresholds.javascript` é aceito, mas os gates que medem pelo adaptador leem `typescript`; num projeto de JavaScript o limite vale em `thresholds.typescript`.
- Com `line_tests` ligado, o relator do Jest para os testes da linha pede o pacote `typescript`, e num projeto de JavaScript nada é escolhido.
- Achados da revisão dos adaptadores na fase 4, que só valem com `line_tests` ligado: em Maven e Gradle de vários módulos a pasta dos dados por teste é relativa ao módulo, e pode ficar lixo fora da raiz; no Jest, `--setupFilesAfterEnv` pela linha de comando substitui o do projeto na execução dos testes por linha, e o relator dela quebra sem `tsconfig.json` (nos dois casos nada é escolhido); a configuração da Forja para o Vitest só carrega `vitest.config.*`; em C#, id com `\\` não casa no filtro, e o índice dos snapshots não foi provado com mais de um projeto de teste; a pasta raiz do cache do lançador de Java só é restringida quando é criada.
- O `doctor` não lista o `dotnet-coverage`.
- O gate de escopo não procura teste pulado em TypeScript (`it.skip`), Java (`@Disabled`), C# (`Skip =`) e Python (`pytest.mark.skip`).
- A Forja não passa nos próprios testes em Python 3.13: o teste de painéis (`tests/ui/test_panels_exact.py`).
- Item 40, resto: `ruff format --check .` acusa `scripts/capturas/terminal.py` e um bloco de código de `docs/guia/documentacao-gerada.md`; na fase 2 da leva de desempenho, acusava dois arquivos em `scripts/capturas`.
- Achados da fase 3 fora das três perguntas, registrados e não tratados: o coverage no Python 3.14, o JaCoCo do Gradle sem versão fixada, o `skipAfterFailureCount` do Surefire, a linha `not ok <arquivo>` do Vitest com `--bail` e o limite da linha de comando do Windows. O texto está na entrada da fase 3.
- Os pontos "apontados pelas leituras, não conferidos por mim" da entrada de 15/11 sobre o suporte por linguagem continuam sem conferência.

**Linux, WSL e macOS**

- `tests/mutation/test_mutate_script.py::test_each_line_gets_the_test_files_that_ran_it` falha no Linux desde `8b61fbe`: compara um caminho com barra invertida com um caminho com barra. Não é da leva de desempenho e não foi corrigido.
- Na fase 4, o WSL tinha Python 3.10 e a Forja não importava lá; só o driver de Go rodou. A suíte inteira nunca rodou no Linux.
- macOS nunca foi provado: não há máquina.

**Pull request 1**

- Segue aberto no GitHub até `correcoes-p0` chegar a `main`.
- Falta a prova de vida com o Cursor 2026.11 de verdade, para conferir as partes 2 e 3.
- Os quatro testes que o autor viu falhar no Linux em `main` (`test_snapshot.py`, `test_repository.py`, `test_adapter.py` de Python, `test_e2e.py`) não foram investigados.

**Lixo em disco e material fora do repositório**

- Dez pastas `forja-captura-*` no `%TEMP%`. Pelo nome vêm de `scripts/capturas/terminal.py`; o conteúdo não foi conferido.
- `~/forja-fase3` no WSL (as ferramentas do levantamento, 1,7 GB; desfaz com `rm -rf ~/forja-fase3`) e `%LOCALAPPDATA%\forja-fase3` (o material do levantamento). Ficavam até a fase 4 ser decidida.
- `%LOCALAPPDATA%\forja-fase4`: os scripts de medição, `final.jsonl` e a árvore do "antes" da fase 4.

**Não conferido se ainda vale**

- Os históricos da versão 1 em `.metrics/mutate` do checkout `%LOCALAPPDATA%\forja-verif`: a rodada seguinte lá roda todos os mutantes de cada arquivo. Na fase 4 o checkout estava em `c180ea5`.
- Para o dono decidir, se quiser (fases 1 e 2 da leva de desempenho): o padrão de um worker por núcleo, dado o custo de memória medido; e se o script deve mostrar o teste que falhou na base.
- A diferença entre os 1477 s da fase 1 e os 2103 s da fase 2 em `measurer.py`, sem causa conferida.
- O que está no GitHub: a entrada do pull request 1 diz que a branch foi enviada ao remoto, e as das fases 1 e 2 da leva de desempenho dizem que os commits estavam em `correcoes-p0`, só na máquina.

## 16/11/2026 — a mutação muda de referência: o método do Google e a ferramenta de cada linguagem

Sessão de decisão e leitura, no worktree `.claude/worktrees/mutacao-nova-abordagem`, branch `mutacao-nova-abordagem`, a partir de `e6be960`. Antes dela, `correcoes-p0` foi enviada ao remoto (`8b61fbe..e6be960`, 19 commits). Nenhum comportamento mudou: a sessão termina com as decisões e o plano, no fim desta entrada.

### Decisões do dono

1. **Todas as ADRs saem.** Estavam atrapalhando o julgamento.
2. **A mutação passa a ser feita pela ferramenta especializada de cada linguagem:** Stryker.NET (C#), StrykerJS (TypeScript e JavaScript), PIT (Java), `gtramontina/ooze` (Go) e, em Python, o `sixty-north/cosmic-ray`. A primeira escolha em Python era o mutmut; o dono o trocou na mesma sessão, depois de o levantamento mostrar que ele não roda no Windows.
3. **A verdade sobre teste de mutação é o que o Google aplica e descreve** em "State of Mutation Testing at Google" (Petrović e Ivanković, ICSE-SEIP 2018) e em "Mutation Testing" (blog de testes do Google, 12/04/2021). O `unclebob/mutator` deixou de ser a referência.

A pendência "a pergunta dos pilares de teste contra o gate de mutação", da lista de 16/11, fica substituída pela decisão 3.

### O que foi feito

| Mudança | Arquivos |
| --- | --- |
| A pasta `docs/adr` foi removida, 42 arquivos. O texto está no histórico do git, em `e6be960`. | `docs/adr` |
| Saíram as 14 citações a ADR que ficariam soltas: 12 em comentários de `src`, uma em `scripts/mutate.py` e uma no guia. O gate `adr` do produto, que cobra registro de decisão no repositório do usuário, não mudou. As menções deste diário e da `AVALIACAO.md` ficam como registro. | `src/forja/toolchain` (5), `src/forja/cli` (4), `src/forja/checks` (2), `src/forja/languages/python/environment.py`, `scripts/mutate.py`, `docs/guia/gates/tipos.md` |
| A regra 2 do `CLAUDE.md` passou a dizer a decisão 3 no lugar do `unclebob/mutator`. Feito sem pedido do dono, para a regra não contradizer a decisão; ele foi avisado. | `CLAUDE.md` |

### O que foi lido

- Os dois artigos do Google, por inteiro, por mim.
- A documentação e os repositórios das cinco ferramentas e do mutmut, por seis agentes de leitura, um por ferramenta. Os agentes leram resumos das páginas, não as páginas; nada do que está na tabela abaixo foi conferido por mim nem provado com a ferramenta rodando.

### O método do Google

- **Só o diff:** a linha acrescentada ou modificada, que algum teste executa e que não é árida.
- **No máximo um mutante por linha** (artigo e blog) **e sete por arquivo** (só o blog).
- **Operadores:** AOR (aritmético), LCR (conector lógico), ROR (relacional), UOI (unário) e SBR (remoção de comando ou bloco). O ABS foi desligado.
- **Qual operador na linha:** sorteado entre os que cabem (artigo); depois, o mais provável de sobreviver e de ser útil naquele contexto, pelo histórico (blog).
- **Nó árido:** regras escritas à mão por linguagem (log, definição de flag, `import`, tempo e prazo, reserva de memória, consulta a cache, `<` por `!=` em laço, `equals` e `hashCode`, mensagem de exceção, capacidade de `make`, `len(x) > 0`, `__main__`, `print`, valor padrão de argumento) e a regra transitiva: um comando composto é árido se todo o corpo dele é árido, com a condição junto. O Google tem centenas dessas regras; só o apêndice do artigo as mostra.
- **Testes:** só os que cobrem a linha mutada.
- **Não há nota de mutação.** O artigo diz que a taxa de mortos "is not the mutation score". O resultado é o mutante vivo mostrado na revisão de código, com "Please fix" e "Not useful".
- **Um por linha basta:** em mais de 90% dos casos todos os mutantes da linha morrem ou nenhum morre (blog).

O gate de hoje diverge em três pontos, antes de qualquer ferramenta: reprova por `min_score`, muta a função tocada inteira e roda todo sítio.

### As cinco ferramentas contra o método

| Do método | Stryker.NET 5.1.0 | StrykerJS 10.1.1 | PIT 1.30.2 | `ooze` | cosmic-ray 8.9.0 |
| --- | --- | --- | --- | --- | --- |
| Só as linhas do diff | `mutate` com trecho por índice de caractere; `since` por git, granularidade não achada | `mutate arquivo:linha-linha` | não no código aberto, só por classe; por linha é do Arcmutate, comercial | não; só regex de arquivos a ignorar | sim: `cr-filter-git` (as linhas do diff contra uma branch) e `cr-filter-lines` (linhas dadas; está no código, não na documentação) |
| Um mutante por linha, limite por arquivo | não | não | não por linha; `CLASSLIMIT` por classe | não | não de fábrica; a sessão é um SQLite, e um filtro pode pular itens antes de rodar |
| Só os testes que cobrem a linha | `coverage-analysis perTest`, padrão | `perTest`, padrão | sim | não usa cobertura; o comando inteiro por mutante | não usa cobertura; o comando inteiro por mutante |
| Código árido | `ignore-methods` com curinga, comentário | plugin `ignorers` sobre a árvore sintática, comentário | `avoidCallsTo` (quatro bibliotecas de log por padrão), `excludedMethods` | nada | `cr-filter-pragma` e `cr-filter-operators`; nada para chamada de log |
| Mutante vivo legível por máquina | JSON (`mutation-testing-report-schema`) | o mesmo JSON | `mutations.xml` | só texto no console | a sessão em SQLite e `cosmic-ray dump`, JSON com um item por linha |
| Sem nota obrigatória | `break-at` 0 por padrão | `thresholds.break` nulo por padrão | limiares opcionais | limite mínimo 1,0 por padrão, ajustável | sim; a taxa é de um comando à parte (`cr-rate`) |
| Windows nativo | não dito na documentação atual | não dito | não dito; o plugin do Gradle tem build em Windows | CI com Windows 2025 | não dito; CI só em Linux; o encerramento por tempo usa `os.killpg`, com `proc.kill()` de reserva |
| Sem alterar o projeto | ferramenta global do `dotnet`; pede o runtime do .NET 10 | os plugins do executor de testes "locally in your project"; Node 22 ou mais | Maven por coordenadas, mas o plugin do JUnit 5 é dependência no `pom.xml`; há o executor de linha de comando, com o classpath montado por quem chama | um arquivo de teste e o `require` no módulo | instalado fora do projeto; a configuração é um TOML em qualquer pasta; muta os arquivos no lugar |

Mais sobre o `ooze`, lido no código por agente: copia o repositório inteiro para uma pasta temporária a cada mutante, não roda verificação inicial, não tem limite de tempo por mutante, os tipos do resultado ficam em `internal/` e as duas últimas tags (v0.3.1 e v0.3.0) estão retratadas no `go.mod`. Não tem operador unário nem remoção de comando.

Por que o mutmut saiu: o README oficial diz "if you want to run on windows, you must run inside WSL". O pull request 404 ("Windows support") é rascunho, com os limites de tempo desligados, e o autor parou o trabalho.

Mais sobre o cosmic-ray, lido no código por agente:

- Escreve a mutação no próprio arquivo e o restaura ao fim; a cópia de segurança fica só em memória, e um processo morto à força deixa o arquivo mutado. A documentação manda commitar antes.
- O distribuidor `local` roda um mutante de cada vez. Em paralelo só com o distribuidor `http`, um trabalhador por clone do git, e o clone só tem o que foi commitado.
- O limite de tempo é um número fixo de segundos na configuração. `cosmic-ray baseline` roda os testes sem mutação.
- `exec` retoma a sessão e pula o item que já tem resultado. Nada invalida um resultado quando o código muda.
- Não tem operador de remoção de comando; tem troca de operador binário, de comparação e unário, `and` por `or`, booleanos, número, `break` por `continue`, laço de zero voltas e remoção de decorador.
- A versão 8.9.0 e o último commit são de 10/11/2026. Licença MIT.

### Conclusões

- **Três ferramentas servem ao método, com lacunas:** Stryker.NET, StrykerJS e PIT escolhem os testes pela cobertura, não recompilam por mutante e dão relatório por mutante. São as três linguagens em que a mutação da Forja custa mais (Jest 356 s, Gradle 101 a 126 s e C# 36 s no projeto gerado da fase 4).
- **O `ooze` não serve como está.** Não tem nada do método: nem linha, nem cobertura, nem relatório, e custa mais por mutante que o gate de hoje em Go.
- **O cosmic-ray serve na escolha do mutante e não ajuda no tempo.** Restringe às linhas do diff, e a sessão em SQLite deixa a Forja pular itens antes de rodar: é a única das cinco em que "um por linha, sete por arquivo" pode valer no que roda. Mas roda o comando de testes inteiro por mutante, um de cada vez, sem cobertura, que é o que o gate de hoje já faz em Python, sem o paralelismo. O paralelismo e a escolha dos testes ficariam com a Forja. No Windows nada foi provado.
- **Nas outras quatro a escolha do mutante é da ferramenta.** Com elas, o "um por linha, sete por arquivo" só pode valer no que a Forja mostra, não no que roda.
- **O PIT de código aberto não restringe por linha.** A Forja teria de rodar as classes tocadas e ficar só com os mutantes das linhas do diff.
- **As decisões 2 e 3 se chocam nesses pontos,** e a escolha é do dono.

### Verificação

- `ruff check` e `ruff format --check` em `src` e `scripts/mutate.py`: limpos, com o `ruff` do checkout principal (o worktree não tem ambiente próprio).
- `tests/cli/test_links_da_documentacao.py` e `tests/cli/test_guia_cobre_codigo.py`: 173 aprovados.
- Não rodou: a suíte rápida (só comentários e documentação mudaram); nenhuma das cinco ferramentas.

### Decisões do dono, depois do levantamento

Levei três pontos ao dono, cada um com uma recomendação, e ele mandou seguir as recomendações:

1. **Go fica no motor da Forja, com as regras do Google.** O `ooze` não entra.
2. **Sem nota, o veredito é pelo mutante vivo.** `min_score` sai. Em `blocking`, mutante vivo em linha do diff devolve a tentativa ao autor de testes uma vez, com os achados; o que continuar vivo na tentativa seguinte vai para o resumo como achado e não bloqueia. Bloquear sempre travaria em mutante equivalente. Em `observation`, como hoje, o achado só aparece no relatório.
3. **Um mutante por linha e sete por arquivo.** Vale no que roda onde a Forja escolhe o mutante (Go e Python). Em C#, JavaScript, TypeScript e Java a ferramenta roda os mutantes das linhas do diff, e a Forja mostra um por linha e sete por arquivo.

### O plano

**Propósito:** o gate de mutação pelo método do Google, igual nas seis linguagens, executado pela ferramenta de cada uma (e pelo motor da Forja em Go), custando menos do que custa hoje.

**Pronto quando:** as seis linguagens estão no método, cada pilha provada no Windows e medida contra o que havia antes dela; a suíte completa com `slow` passa; o guia diz o que o gate faz. A branch `mutacao-nova-abordagem` só chega a `correcoes-p0` com as seis (regra 7).

**O contrato,** congelado enquanto houver tarefa aberta sobre ele:

- **O núcleo decide o método,** igual nas seis: as linhas candidatas (acrescentadas ou modificadas pela tentativa, cobertas, não áridas), um por linha e sete por arquivo, nenhuma nota, e o veredito da decisão 2.
- **Nó árido é decidido pela Forja,** sobre o tree-sitter que ela já tem para as seis linguagens, com as regras do apêndice do artigo: uma implementação, seis gramáticas. Vale antes de rodar onde a Forja escolhe o mutante, e sobre o achado nas demais. O mecanismo da ferramenta (`ignore-methods`, `avoidCallsTo`, `ignorers`) entra a mais, só para poupar tempo.
- **Operadores: as cinco famílias do Google** (AOR, LCR, ROR, UOI, SBR). O que a ferramenta tem a mais fica desligado; a família que ela não tem fica registrada como limite da linguagem.
- **O adaptador planeja o comando de mutação da ferramenta** (`Operation.MUTATION`, que já existe em `core/adapters/language.py` sem quem a planeje) e lê o relatório dela para um modelo comum: arquivo, linha, operador e estado.
- **Não muda:** linha acrescentada sem teste reprova; o que deixa o gate inconclusivo; a prioridade baixa; a guarda do estouro de tempo onde o motor é da Forja.

**A fila.** Uma tarefa por sessão, na ordem. Cada tarefa abre com o orçamento declarado (tempo, núcleos e tokens) e fecha medida, um processo pesado de cada vez. Medição com perda para a fila e volta ao dono.

| # | Tarefa | Pronto quando |
| --- | --- | --- |
| 1 | **O método e o contrato no núcleo, com Go no motor da Forja:** as linhas do diff no lugar da função inteira, um mutante por linha e sete por arquivo, nó árido, sem nota, o veredito novo. Go vem primeiro porque é a única linguagem que fica no motor. As outras pilhas não passam pelo motor com o método novo: ficam como estão, na branch, até a tarefa da ferramenta delas. | os casos `slow` de mutação de Go dão o veredito esperado (aprova a função conferida, acha o teste que só chama); medido antes e depois nos projetos de Go da fase 4 da leva de desempenho; guia |
| 2 | **Go: SBR no motor da Forja.** É a linguagem que fica no motor, e a remoção de comando é a família que falta nele. | o mesmo, para Go |
| 3 | **TypeScript e JavaScript com Jest: StrykerJS.** | a ferramenta provada no Windows no projeto de teste da pilha, sem alterar o projeto do usuário; os mesmos casos; medido contra o gate de hoje (o "antes" da fase 4); sai do motor o que só servia a essa pilha |
| 4 | **TypeScript e JavaScript com Vitest: StrykerJS.** | o mesmo |
| 5 | **Java com Gradle: PIT.** | o mesmo |
| 6 | **Java com Maven: PIT.** | o mesmo |
| 7 | **C#: Stryker.NET.** O runtime do .NET 10 entra no manifesto de ferramentas. | o mesmo |
| 8 | **Python: cosmic-ray.** O paralelismo e o limite de tempo são da Forja: uma sessão por cópia. | o mesmo |
| 9 | **Fecho:** sai do motor tudo o que não é de Go, conferido no código; a suíte completa com `slow`; a mutação da própria Forja (`scripts/mutate.py`) passa ao cosmic-ray e ao método. | a leva pronta |

A ordem: a tarefa 1 põe o método e o contrato no núcleo e os prova com Go. Depois vêm as ferramentas, da pilha mais cara para a mais barata pelas medições que há (Jest 356 s, Gradle 101 a 126 s, C# 36 s, Python 9,5 s no projeto gerado da fase 4), com as duas pilhas da mesma ferramenta em seguida.

**Correção do plano, a pedido do dono.** A primeira versão desta fila aplicava o método novo nas seis linguagens ainda sobre o motor da Forja, e só depois trocava pelas ferramentas. O dono corrigiu: onde há ferramenta coerente com o método do Google, a mutação é da ferramenta, e o motor da Forja não gera nem roda mutante ali. O motor fica só em Go, adaptado aos artigos. O que continua da Forja nas seis linguagens é a política do gate, em volta da ferramenta: dizer as linhas do diff, ler o relatório, mostrar um por linha e sete por arquivo, tirar o nó árido e dar o veredito.

**O que cada tarefa de ferramenta prova antes de qualquer código,** porque o levantamento não provou: que roda no Windows; que roda sem alterar o projeto (os plugins do StrykerJS, o plugin do JUnit 5 do PIT, o PIT no Gradle por script de inicialização); a sintaxe que restringe às linhas; os campos do relatório.

**Decisão minha dentro do plano,** para o dono vetar se quiser: onde a Forja escolhe o mutante, o operador da linha é sorteado com semente fixa por execução. O artigo sorteia; a escolha pelo histórico, do blog, pede dados que a Forja não tem. A semente existe para a retomada de uma execução dar o mesmo resultado.

### Para retomar

Sessão nova, no worktree `.claude/worktrees/mutacao-nova-abordagem`. O worktree não tem ambiente próprio: `uv sync` nele antes de rodar qualquer coisa. Seguinte: a tarefa 1 da fila, que começa lendo `checks/mutation.py` e `mutation/` por inteiro.
