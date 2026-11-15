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

### Dificuldades

- Um script embutido no shell trocou `\n` por quebra de linha dentro de uma string de teste, de novo. Corrigido pela ferramenta de edição.

### Verificação

- `ruff check .`, `ruff format --check src tests` e `mypy` strict: limpos, 621 arquivos.
- `pytest -m "not slow" -n 6`: 9638 aprovados, 41 pulados, nenhuma falha, 328 s.
- `tests/checks/test_real_run.py` (`slow`), com git de verdade e a fachada: 2 aprovados; o commit da etapa sai com `Evidence-Id: scope`, `Metric-functions: 2 -> 3` e a nota.
- Mutação em `2808d03`, no checkout de verificação: `orchestrator/task_cycle.py` (78 mutantes) e `orchestrator/measurer.py` (93, 26 min) sem sobrevivente; `orchestrator/workspace_git.py` (28) com um, `:351` `or -> and` em `GitWorkspace.watch`, código antigo. Um teste novo no contrato dos workspaces o mata, em `9ea30b4` (`test_a_watch_told_only_the_task_or_only_the_attempt_overlooks_nothing`): conferido à mão, com a mutação aplicada os dois casos de git falham. O script não rodou de novo sobre o arquivo.
- Não rodou: suíte completa com `slow` na ponta; mutação dos arquivos da entrada anterior (`common.py`, os `parsers.py` de C# e Java, `ports.py`, `cli/session.py`, `cli/detect.py`, `cli/prepare.py`, `cli/doctor.py`); E2E com agente real.

### Pendências

- **Itens 15 a 20 e 31 a 40** (o 30 adiado), e os restos dos itens 2 e 6.
- **Verificação que falta:** a da lista acima.
- **Decisões do dono ainda abertas:** ADR 0041 (item 24); item 29; as três do item 5; a observação sobre a etapa de caracterização (item 12).

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

### Pendências

- **Itens 16 a 20 e 31 a 40** (o 30 adiado), e os restos dos itens 2 e 6.
- **Verificação que falta:** suíte completa com `slow`; mutação de `common.py`, dos `parsers.py` de C# e Java, de `ports.py`, `cli/session.py`, `cli/detect.py`, `cli/prepare.py`, `cli/doctor.py`, `testing/workspace.py` e `testing/repo.py`.
- **Decisões do dono ainda abertas:** ADR 0041 (item 24); item 29; as três do item 5; a observação sobre a etapa de caracterização (item 12).

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

### Pendências

- **Realinhamento da mutação, o que falta:** decisão sobre o overlay de links; `anchor_line` no gate; `_in_type` em `sites.py`; `mvn -q test` sem `clean` por mutante; snapshot com modo diferencial em `scripts/mutate.py`; `--max-workers`.
- **`scripts/mutate.py` carrega `contexts.json` (1,9 GB) inteiro na memória.** Não medido; suspeito das quedas por falta de memória das entradas anteriores.
- **CRAP pelo `crapper`:** cobertura por instrução e por ramo; contagem com tree-sitter no lugar do `lizard`. Não iniciado.
- **Itens 31 a 40** (o 30 adiado), e os restos dos itens 2 e 6.
- **Verificação que falta:** suíte completa com `slow`; mutação de `common.py`, dos `parsers.py` de C# e Java, de `cli/session.py`, `cli/detect.py`, `testing/workspace.py` e `testing/repo.py`.
- **Decisões do dono ainda abertas:** ADR 0041 (item 24); item 29; as três do item 5; a observação sobre a etapa de caracterização (item 12).

## 13/11/2026 — o resto do realinhamento da mutação com o `unclebob/mutator`

Continuação da entrada anterior, na mesma sessão. As duas entradas anteriores estavam datadas de 14/11; os commits são todos de 13/11 e os títulos foram corrigidos (`95f79db`, que era `621524b` antes da reescrita registrada na última entrada).

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

### Pendências

- **Overlay de links: decisão do dono, ainda aberta.** Recomendação: não fazer. Ganho medido de 0,65 s por worker contra o risco de escrita no lugar descrito na entrada anterior.
- **Não adotados do mutator:** um worker por núcleo; em Go, só o pacote do arquivo; base uma vez por comando no lugar de uma por arquivo.
- **ADRs 0021 e 0034** descrevem a mutação de antes do realinhamento e não foram atualizadas.
- **CRAP pelo `crapper`:** não iniciado; depende de ordem do dono.
- As demais pendências da entrada anterior continuam: itens 31 a 40, restos dos itens 2 e 6, suíte com `slow`, mutações antigas e as decisões abertas do dono.

### Pontos de atenção a endereçar

O dono pediu, em 13/11, que estes três pontos sejam tratados. Os três foram tratados depois; veja a última entrada de 13/11. Os hashes `621524b` e `b50146e` citados abaixo e na entrada seguinte são os de antes da reescrita.

1. **O modo diferencial não vê os testes.**
   - O que acontece: o histórico guarda o hash do texto de cada função e o destino de cada mutante. Se um teste for enfraquecido ou apagado e a função não mudar, o mutante continua dado como morto e não roda.
   - Consequência: `scripts/mutate.py` pode sair com 0 tendo um sobrevivente que só `--mutate-all` mostraria.
   - Hoje: avisado no texto de ajuda do script. Nada impede o engano.
   - A endereçar: fazer o histórico depender também dos testes. Caminho a avaliar: guardar junto de cada função um hash dos arquivos de teste que executam suas linhas (o script já sabe quais são, pela cobertura) e rodar de novo os mutantes da função quando esse hash mudar. É divergência do mutator, que não olha os testes; pede decisão do dono.
   - Até lá: rodar `--mutate-all` depois de qualquer mudança em `tests/` e antes de cada release.

2. **O histórico é JSON por arquivo-fonte, não o EDN por namespace do mutator.**
   - O que acontece: `.metrics/mutate/<arquivo>.json` no lugar de `.metrics/mutate/<namespace>.edn`.
   - Consequência: o `uml-viewer`, que lê o EDN do mutator, não lê o histórico da Forja. Não é cópia fiel neste ponto.
   - A endereçar: decisão do dono entre manter o JSON ou escrever o EDN do mutator. O EDN pede o namespace e o identificador de forma (`defn/nome`, `defn-/nome`) que no mutator vêm do `crapper`; faz sentido decidir junto com a adoção do `crapper`.

3. **O commit `621524b` tem a mensagem errada.**
   - O que acontece: a mensagem diz que é o diário do realinhamento, mas o commit só corrige duas datas de título (14/11 para 13/11). A entrada está em `b50146e`, com a mesma mensagem.
   - Consequência: dois commits seguidos com a mesma mensagem; o histórico engana quem ler.
   - A endereçar: reescrever a mensagem de `621524b` antes de enviar a branch. Nada foi enviado ainda, então a reescrita não afeta ninguém; mesmo assim muda os hashes dos commits seguintes, e por isso espera ordem do dono.

### Limpeza do que as rodadas antigas deixaram

Pedida pelo dono em 13/11, depois de conferir o disco.

- **Apagado:** `target/coverage/python/contexts.json` da árvore principal (1,8 GB; `target/` caiu de 1,7 GB para 2,5 MB); cinco pastas `forja-mutants-*` no `%TEMP%`, restos de rodadas interrompidas (3 MB); o projeto Maven e os scripts de medição do scratchpad da sessão.
- **Já não existia:** as cópias de worker de 15 GB no `%TEMP%`; o `contexts.json` do checkout de verificação, apagado pelo script novo; processos de mutação pendurados.
- **Mantido:** `.coverage` e `lcov.info` (usados por `--reuse-coverage`); `.metrics/mutate/` do checkout de verificação (o histórico do modo diferencial); o checkout `%LOCALAPPDATA%\forja-verif`.
- **Não tocado, a conferir:** dez pastas `forja-captura-*` no `%TEMP%`. Pelo nome vêm de `scripts/capturas/terminal.py`; o conteúdo não foi conferido.

## 13/11/2026 — sugestão de implementação para os três pontos de atenção

Pedido do dono: a melhor implementação para cada ponto, se fizer sentido. Nada foi implementado; esta entrada só propõe. Base: `scripts/mutate.py` e o histórico do git, lidos nesta sessão. O `unclebob/mutator` não foi relido; o que se diz dele aqui vem das duas entradas anteriores.

### Resumo

| Ponto | Faz sentido implementar? | Sugestão |
| --- | --- | --- |
| 1. O modo diferencial não vê os testes | Sim | Guardar, por mutante morto, o hash dos arquivos de teste que o julgaram; aproveitar o veredito só se esse hash for o mesmo. |
| 2. Histórico em JSON, não em EDN | Não agora | Manter o JSON. Decidir junto com a adoção do `crapper`. |
| 3. Mensagem errada em `621524b` | Sim | Reescrever a mensagem agora, antes do primeiro envio, e corrigir no diário os hashes que mudam. |

### Ponto 1: o veredito aproveitado passa a depender dos testes que o deram

**A sugestão é por mutante, não por função.** A entrada anterior propunha um hash, por função, dos arquivos de teste que executam as linhas dela. Lendo `judge_file`, isso deixa um furo: o mutante que sobrevive à rodada estreita é julgado de novo por todos os testes do arquivo-fonte, e pode ser morto por um teste que a cobertura não liga a nenhuma linha da função. Esse teste ficaria fora do hash da função. O conjunto certo é o que de fato julgou cada mutante.

**O que guardar.** Para cada mutante dado como morto, o hash do conjunto de arquivos de teste que o julgou:

- morto na rodada estreita: os arquivos de `tests_of_line` para a linha dele;
- morto na reconferência: todos os testes do arquivo-fonte (`tests`).

Entram no hash, além do nome e do conteúdo de cada arquivo do conjunto:

- todo `conftest.py` sob `tests/`. Não só os das pastas de cima: `tests/core/locking/conftest.py` importa um fixture de `tests/core/idempotency/conftest.py`, como a entrada do item 41 registrou;
- a lista de `deselected()`, porque um teste retirado da rodada vale por um teste apagado.

Arquivo de teste que não existe mais entra como ausente, e o hash muda.

**A regra.** `carried` ganha uma quarta condição: o hash guardado para o mutante é igual ao de agora. As três de hoje ficam (linha coberta, morto na última rodada, texto da função igual).

**Mudanças em `scripts/mutate.py`.**

- `judge_file` devolve também os índices dos mutantes reconferidos. Hoje devolve só a contagem.
- Função nova que dá o hash de um conjunto de arquivos de teste, com o hash de cada arquivo calculado uma vez por rodada.
- `write_history` grava `"judges": {id do mutante: hash}` e `"version": 2`. Mutante aproveitado leva adiante o hash que tinha.
- `load_history` lê `judges`. Histórico da versão 1 não tem `judges`: nenhum mutante é aproveitado, e a primeira rodada depois da mudança roda todos, uma vez.
- O texto de ajuda e o guia `gates/mutacao.md` deixam de dizer que `--mutate-all` é obrigatório depois de mexer num teste.

**Testes a escrever,** em `tests/mutation/test_mutate_script.py`: teste enfraquecido faz o mutante rodar de novo; arquivo de teste apagado, também; `conftest.py` alterado, também; nada mudou, o mutante é aproveitado; histórico da versão 1 não aproveita nenhum; mutante morto na reconferência guarda o conjunto inteiro, e uma mudança num teste fora da rodada estreita o faz rodar.

**Custo.** O SHA-256 dos arquivos de teste, uma vez por rodada. Editar um arquivo de teste faz rodar de novo os mutantes que ele julgou; mutante morto para no primeiro teste que falha (`-x`), então a rodada a mais é curta.

**Por que a divergência do mutator é aceitável.** Ela só acrescenta rodadas. Todo mutante que o mutator rodaria continua rodando; alguns que ele aproveitaria passam a rodar. Nenhum veredito fica mais frouxo que o da referência. Mesmo assim é divergência, e a regra 1 pede a ordem do dono.

**O que continua sem ser visto.**

- Código de apoio dos testes fora de `tests/`, como `src/forja/testing`.
- Arquivos de dados que um teste lê.
- Mudança em outra função de que o teste dependia para matar o mutante. É o limite do próprio mutator, que só olha o texto da função dona do sítio.

Por esses três, `--mutate-all` antes de cada release continua valendo.

### Ponto 2: manter o JSON

**Não faz sentido trocar agora.** Três motivos:

- O único leitor do EDN é o `uml-viewer`, que ninguém pediu (regra 3).
- O EDN do mutator é por namespace e identifica cada forma por `defn/nome` ou `defn-/nome`, que vêm do `crapper`. A Forja não tem o `crapper`. Escrever EDN hoje seria inventar esses nomes para Python, e um `uml-viewer` leria um arquivo que parece o do mutator e não é.
- O histórico é local e refeito por uma rodada com `--mutate-all`. Trocar o formato depois não perde nada.

**Sugestão.** Nenhuma mudança de código. O ponto fica ligado à decisão sobre o CRAP pelo `crapper`: se o `crapper` for adotado, a Forja passa a ter os nomes de forma da referência, e aí o histórico pode ser escrito no EDN do mutator, lendo o esquema inteiro antes (regra 2). Se o `crapper` não for adotado, o JSON fica e a divergência vai registrada no guia `gates/mutacao.md`.

**Efeito do ponto 1 sobre este.** O campo `judges` não existe no histórico do mutator. Num EDN futuro ele seria uma chave a mais, que um leitor do formato original ignora ou não; a conferir quando o esquema for lido.

### Ponto 3: reescrever a mensagem de `621524b`

**Conferido nesta sessão.** `origin/main` está em `699d936` e a branch `correcoes-p0` não existe no remoto: nada do que seria reescrito foi enviado. `621524b` altera só duas linhas de título de `DIARIO.md`.

**Reescrever, não juntar.** Juntar `621524b` a `b50146e` daria um commit com duas coisas, contra o costume da branch de um commit por coisa.

**Passos,** com a árvore limpa:

1. `git checkout --detach 621524b`
2. `git commit --amend -m "docs: the two logbook entries dated 14/11 are dated 13/11, the day of their commits"`, com o rodapé de coautoria que o commit já tem.
3. `git rebase --onto HEAD 621524b correcoes-p0`
4. Conferir: `git diff e6f1229 correcoes-p0` não mostra nada além dos commits feitos depois de `e6f1229`, e nenhuma mensagem aparece duas vezes em `git log --format=%s main..correcoes-p0`.
5. Um commit novo do diário, que troca pelos hashes novos as citações de `621524b` e `b50146e` (na entrada do resto do realinhamento e no ponto 3 da lista) e marca o ponto como tratado.

**O que muda.** Os hashes de `621524b` e de todos os commits depois dele. Os commits anteriores, até `a74d680`, ficam como estão, e são eles que o resto do diário cita. O checkout `%LOCALAPPDATA%\forja-verif` e o histórico em `.metrics/mutate` não são afetados.

**Quando.** Antes de qualquer envio, e de preferência já: cada entrada nova do diário que citar um desses hashes aumenta o passo 5.

### Pendências

- **Ordens do dono que esta entrada espera:** ponto 1, aceitar a divergência do mutator e implementar; ponto 2, confirmar que o JSON fica até a decisão do `crapper`; ponto 3, autorizar a reescrita.
- As demais pendências da entrada anterior continuam.

## 13/11/2026 — os três pontos de atenção tratados

### Decisões do dono

O dono concordou com as três sugestões da entrada anterior:

- **Ponto 1:** aceitar a divergência do mutator e implementar.
- **Ponto 2:** o histórico fica em JSON até a decisão sobre o `crapper`. Nenhuma mudança de código.
- **Ponto 3:** reescrever a mensagem do commit.

### Ponto 3: a mensagem reescrita

Feito primeiro, com a árvore limpa, pelos passos da entrada anterior.

| Antes | Depois | Mensagem |
| --- | --- | --- |
| `621524b` | `95f79db` | passou a ser `docs: the two logbook entries dated 14/11 are dated 13/11, the day of their commits` |
| `b50146e` | `9314f28` | a mesma |
| `e6f1229` | `436a973` | a mesma |

- A entrada das sugestões foi commitada antes da reescrita e ficou em `d6a152a`.
- Conferido: a árvore da ponta é idêntica à de antes da reescrita (`git diff` vazio), e nenhuma mensagem aparece duas vezes em `main..correcoes-p0`.
- No diário, a citação na entrada do resto do realinhamento passou a `95f79db`. A lista dos pontos de atenção e a entrada das sugestões continuam com os hashes antigos, com um aviso: são o registro do que se via na hora.

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
- **O guia não mudou.** `docs/guia/gates/mutacao.md` não fala do modo diferencial do script; a entrada anterior supôs que falava. O texto de ajuda do script foi atualizado.
- **`--mutate-all` antes de release continua valendo,** pelos três limites da entrada anterior: código de apoio fora de `tests/`, arquivos de dados, e mudança em outra função.

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

### Pendências

- **Ponto 2 fica ligado ao CRAP pelo `crapper`,** que depende de ordem do dono. Se o EDN for adotado, conferir o que fazer com `judges` e `tests`, que o histórico do mutator não tem.
- **O checkout `%LOCALAPPDATA%\forja-verif` tem históricos da versão 1** em `.metrics/mutate`. A próxima rodada lá roda todos os mutantes de cada arquivo.
- As demais pendências continuam: itens 31 a 40, restos dos itens 2 e 6, suíte com `slow`, mutações antigas, ADRs 0021 e 0034, e as decisões abertas do dono (overlay de links, ADR 0041, item 29, as três do item 5, a etapa de caracterização).

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
- **Item 33, conflito de integração ficou de fora.** O plano falava em "conflito de integração"; o conflito não passa por `rollback_integration` (a workspace desfaz o merge sozinha). Um cenário de conflito com cortes pede duas tarefas que mexem no mesmo arquivo; não foi feito.
- **Item 34, o pacote é o mesmo.** O `build_package` novo foi comparado com o de `cec13d5` em 221.184 combinações de papel, escopo, relatórios e textos fixos: nenhum caractere de diferença.
- **Item 34, a supressão de mutação mandou no desenho.** `mutation-suppressions.toml` nomeia `build_package` e `True -> False` (a marca de item essencial). A primeira divisão levou os `True` para as funções auxiliares e `tests/mutation/test_sites.py` falhou. A marca voltou para dentro de `build_package` (`6eb9e2a`); a supressão aprovada não foi tocada.
- **Item 34, `judge` mantém a ordem.** As leituras de git, a instalação de dependências e a lista de checks seguem a ordem de antes.
- **Item 34, `read_facts` (CCN 27) e outras 20 funções continuam acima de 15.** O plano do item pedia `build_package` e `judge`. As maiores que ficaram: `cli/prepare.py::prepare` (48), `summary.py::read_facts` (27), `languages/go/parsers.py::parse_tests` (22).
- **Item 36, a segunda metade não foi feita.** "Registrar os checks por lista, sem import direto" pede tirar de `measurer.py` a montagem de cada check. Cada um recebe coisas diferentes do medidor (ferramentas, ambiente, pastas, leituras de git). Isso é um desenho novo, que o plano não descreve; fica para decisão do dono.
- **Item 38, o código não mudou.** `forja instalar` continua instalando no Linux x64. O que mudou é o que a documentação promete.
- **Item 39, as ADRs continuam citando "questão N do plano" e `plano-3.md`.** São o registro do que se via na hora; só saíram os ponteiros para arquivos que não existem.
- **Item 40, `OUTSIDE` era resto mesmo.** O commit `219dc76` trocou a negação de `../**` pela permissão só sob `./**` e deixou a constante.
- **Item 40, o que ficou.** A versão `0.0.0` (o número é do dono). A pasta `dist/` (um `.whl` e um `.tar.gz` de 10/11, fora do git): a remoção foi negada pela política de permissões da sessão.

### Verificação

- `ruff check .`, `ruff format --check src tests` e `mypy` strict: limpos, 628 arquivos.
- `pytest -m "not slow" -n 6` em `ee3bd81`: 9855 aprovados, 41 pulados, nenhuma falha, 360 s.
- `tests/orchestrator/test_resume_end_to_end.py` e `tests/orchestrator/test_resume_rollback.py` (os dois `slow`): 120 aprovados, 196 s.
- `scripts/mutate.py` sobre os sete arquivos de `src` alterados, com cobertura nova: saída 0. Mutantes: `core/events/state.py` 27, `orchestrator/operations.py` 17, `task_cycle.py` 78, `agent_runner.py` 101, `measurer.py` 94, `core/events/sink.py` 1. Os únicos sobreviventes são as marcas `True -> False` de `build_package`, que `mutation-suppressions.toml` já nomeia. `core/task.py` não teve mutante julgado: o arquivo só declara `TaskInfo`, e nenhuma linha dele roda dentro de um teste.
- Não rodou: a suíte completa com `slow` (só os dois arquivos de retomada acima); E2E com agente real.

### Pendências

- **Item 32 não foi iniciado:** os 64 `sleep(` dos testes e os testes sem asserção.
- **Item 36, segunda metade:** registrar os checks por lista. Pede um desenho que o plano não descreve; fica para o dono.
- **Item 33:** o conflito de integração (o outro caminho que desfaz a integração) não tem cenário com corte em cada linha.
- **Item 34:** 21 funções continuam com CCN acima de 15 (`prepare` 48, `read_facts` 27, `parse_tests` 22).
- **Item 40:** a versão `0.0.0` e a pasta `dist/` ficam com o dono.
- As demais pendências continuam: o item 30 adiado, restos dos itens 2 e 6, suíte com `slow`, mutações antigas, ADRs 0021 e 0034, o ponto 2 (JSON ou EDN, ligado ao CRAP pelo `crapper`), os históricos da versão 1 em `%LOCALAPPDATA%\forja-verif`, e as decisões abertas do dono (overlay de links, ADR 0041, item 29, as três do item 5, a etapa de caracterização).

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

### O que foi conferido no código

- **Nenhum processo de mutação roda com prioridade baixa.** A busca por `priority`, `nice` e `affinity` em `src` e `scripts` só achou `_CREATE_NEW_PROCESS_GROUP | _CREATE_SUSPENDED` em `core/process/_windows.py`. Os testes dos mutantes disputam CPU de igual para igual com o resto da máquina.
- **O gate roda o comando de testes inteiro para cada mutante** (`AdapterRunner`, `Operation.TEST`, com `-x` em Python). A escolha de testes pela linha mutada, a ordem dos testes e o histórico entre rodadas existem só em `scripts/mutate.py`.
- **O padrão de workers é 4 no gate** (`mutation/executor.py`) e 8 no script.
- **O gate já faz uma base por rodada.** "Base uma vez por comando" só falta no script, que faz uma por arquivo.

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

### Pendências

- **Leva de desempenho da mutação, a fazer, à frente do item 5:** plano primeiro; prioridade baixa; um worker por núcleo; Go só no pacote; base uma vez por comando no script; cobertura por teste em cada linguagem, e com ela a escolha pela linha, a ordem dos testes e o histórico no gate; nova medição de `measurer.py`, a escolha por função de teste no lugar de arquivo, e rodar primeiro quem matou da última vez.
- **Reler o `unclebob/mutator` por inteiro antes de implementar** (regra 3), conferindo se ele trata prioridade de processo.
- As pendências da entrada de 14/11 sobre as decisões do dono continuam, agora depois desta leva.

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
6. **Correção à entrada anterior: o gate não usa `-x` em Python.** `languages/python/adapter.py:251` planeja `pytest -p no:cacheprovider -q -rA --color=no`. `-x` só aparece em `scripts/mutate.py:587` e em `cli/facade.py:63`. No gate, nas seis linguagens, o mutante morto roda a suíte até o fim.
7. **O modelo de cobertura do gate já guarda os testes de cada linha.** `FileCoverage.tests` (`core/reports/models.py:31`) é preenchido pelo leitor do coverage.py, e o driver de Python já grava o contexto por função de teste (`coverage_driver.py:30`, no formato `tests/test_a.py::test_x`). `MutationCheck` não lê esse campo. Os leitores das outras linguagens (istanbul, JaCoCo, Cobertura, perfil do Go) não o preenchem.
8. **Filtro de testes nos adaptadores:** só Python aceita `test_files` (`TEST_FILTER_BY_FILE`). Nenhum adaptador aceita `tests`. Go, Java (Maven e Gradle), C# e TypeScript/JavaScript respondem `NotSupported` a qualquer filtro.
9. **As cópias dos workers são feitas uma depois da outra** (`executor.py:182`). A 0,96 s por cópia (medida de 13/11 na árvore da Forja), 24 workers custam cerca de 23 s antes da base.
10. **O script faz até duas bases por arquivo:** `judge_file` chama `run_mutants` uma vez para a rodada estreita e outra para a reconferência, e cada chamada roda a sua base.
11. **A cobertura de Go é medida com `-coverpkg=./...`** (`languages/go/adapter.py:186`): uma linha pode estar coberta só por teste de outro pacote.
12. **Esta máquina tem WSL com Ubuntu** (`wsl -l -q`). O que há instalado lá não foi conferido. macOS não há.

### O plano

Valem as regras da entrada anterior: morto pelo subconjunto é morto pelo conjunto; sobrevivente só conta depois do conjunto inteiro; nada de amostrar, encurtar o tempo-limite, pular a reconferência ou descartar por equivalência; toda mudança com medição antes e depois e comparação de vereditos. Um commit por item, com teste.

| Fase | O que | Depende de |
| --- | --- | --- |
| 0 | Medição de base | nada |
| 1 | Prioridade baixa e um worker por núcleo | 0 |
| 2 | Go só no pacote, e a base uma vez por comando no script | 1 |
| 3 | Levantamento: filtro, parada e teste por linha em cada linguagem | nada |
| 4 | No gate: testes da linha, ordem e histórico, nas seis linguagens de uma vez | 2, 3 e a decisão do dono sobre o levantamento |
| 5 | No script: escolha por função de teste, e quem matou roda primeiro | 2 |

**Fase 0: medição de base.** Nenhum código muda.

- Script: `orchestrator/measurer.py` com `--mutate-all` e cobertura nova, com a máquina parada. Guardar o tempo de cada parte (cobertura, base, mutantes, reconferidos), o pico de memória e o veredito de cada mutante.
- Gate: uma rodada do `MutationCheck` num projeto de cada linguagem, com o mesmo registro. Quais projetos de exemplo servem não foi conferido; os testes `slow` de mutação real são o ponto de partida.
- Esses vereditos são a referência de comparação de todas as fases.

**Fase 1: prioridade baixa e um worker por núcleo.** A prioridade vem antes: 24 workers em prioridade normal piorariam a queixa.

- Prioridade no gate: `ProcessSpec` ganha um campo que pede prioridade baixa, e `AdapterRunner` o liga para a base e para os mutantes. No Windows, a classe de prioridade é imposta pelo Job Object que `WindowsJobGroup` já cria (o campo `PriorityClass` já está declarado em `_BasicLimits`), e assim vale para a árvore inteira. No POSIX, a prioridade é dada ao grupo de processos em `PosixGroup.attach`; a sessão nova já existe. As chamadas (`JOB_OBJECT_LIMIT_PRIORITY_CLASS`, `os.setpriority`) estão aqui de memória e têm de ser conferidas na documentação antes de escrever.
- Prioridade no script: o próprio processo baixa a sua prioridade no começo de `main`; canário, cobertura, base e mutantes herdam.
- Nível: o mais baixo de cada sistema (classe ociosa no Windows, 19 no POSIX).
- Teste: um processo neto diz a própria prioridade. No Windows aqui; no Linux pelo WSL.
- Workers: o padrão passa a `os.cpu_count()` no gate (`executor.py:179`) e no script (`mutate.py:694`), como no mutator. `limits.workers` e `--max-workers` continuam como teto. As cópias dos workers passam a ser feitas em paralelo (achado 9). O guia `gates/mutacao.md` muda no padrão de `limits.workers`.
- Medir: tempo e pico de memória com 24 workers, com a máquina parada e com a máquina em uso.
- Risco: o tempo-limite é de relógio. Com a máquina ocupada, um mutante em prioridade baixa pode estourar o tempo sem ter laço nenhum e contar como morto. `_alone` já julga de novo, sozinho, quem estourou, e isso fica. Se a medição com a máquina em uso der veredito diferente, a fase para e o caso volta ao dono.

**Fase 2: Go só no pacote, e a base uma vez por comando no script.**

- O executor passa a aceitar uma base já medida por quem o chama, como no mutator, e a fazer duas passagens: a estreita e, para quem sobreviveu a ela, a inteira. É o que `judge_file` faz hoje por fora, com duas chamadas. O runner passa a saber qual arquivo foi mutado.
- Go: o pedido ao adaptador ganha um aviso com o arquivo mutado, do tipo do `rerun`, que só o Go lê: `go test -count=1 -json` no pacote do arquivo. Uma base por comando distinto, como no mutator. O sobrevivente do pacote é julgado por `./...` antes de contar, por causa do achado 11. Nas outras linguagens o mutator roda o comando inteiro; o ganho equivalente nelas é a fase 4.
- Script: uma base por comando, com a união dos testes dos arquivos pedidos, e o tempo-limite de dez vezes essa base, como no mutator. O `--timeout` por teste do pytest continua a pegar o laço em segundos. Base que falha: saída 2 e histórico intacto, como hoje.
- Medir: `measurer.py` e uma rodada de vários arquivos, antes e depois; no gate, um projeto Go com mais de um pacote.

**Fase 3: levantamento.** Nenhum código de produção muda. Para Python, Go, Java com Maven, Java com Gradle, C#, e TypeScript e JavaScript com Vitest e com Jest, três respostas, tiradas da documentação de cada ferramenta e de uma prova de vida num projeto pequeno, no Windows e no Linux:

- (a) como rodar só os testes escolhidos, e qual é a unidade (função, classe, arquivo ou pacote);
- (b) como parar no primeiro teste que falha e como impor a ordem;
- (c) como saber quais testes executam cada linha, e quanto custa medir.

Ponto de partida: Python tem (a) por arquivo e (c) por função, já no gate. As outras não têm nada disso na Forja. De memória, sem conferir: JaCoCo, coverlet, istanbul e o perfil do Go não dizem o teste de cada linha num relatório só; o equivalente provável é uma medição por unidade de teste, e o custo dela é o que decide.

Saída: uma tabela por linguagem com (a), (b) e (c) provados e o custo medido. A técnica só segue para a fase 4 se as seis tiverem as três respostas.

**Fase 4: no gate, nas seis linguagens de uma vez.**

- Adaptadores: o filtro por teste, que o contrato já prevê (`tests`, `test_files`), e a parada no primeiro teste que falha quando o pedido é `rerun` (achado 6).
- Leitores de cobertura: preenchem `FileCoverage.tests`.
- `MutationCheck`: cada mutante é julgado primeiro pelos testes das suas linhas (`site.lines`, com a âncora); o sobrevivente, pelo comando inteiro. O tempo-limite continua o de dez vezes a base do comando inteiro.
- Ordem: primeiro o teste que matou o mesmo mutante da última vez.
- Histórico: serve só para a ordem. Nenhum mutante deixa de rodar no gate (ponto 2 para o dono).
- Onde o gate guarda o histórico entre uma tentativa e outra não foi lido; faz parte da fase.
- Medir, por linguagem: antes e depois, mesmos mutantes, mesmos vereditos. O guia `gates/mutacao.md` muda em "Quando roda" e em "Mutação incremental".

**Fase 5: no script.**

- Escolha por função de teste: os contextos do `coverage` já são por função, e o script os reduz a arquivo. Passa a pedir os testes pelo id. A reconferência continua pelo conjunto inteiro. Atenção ao que o item 41 registrou: o que roda na preparação de um fixture fica na conta do teste anterior, e por função isso pesa mais; a medição conta os reconferidos.
- Quem matou roda primeiro: o histórico de hoje guarda os arquivos que julgaram, não o teste que falhou. Passa a guardar o teste que falhou (versão 3) e a rodá-lo primeiro.
- Medir: `measurer.py`, contra a fase 0 e a fase 2.

**Verificação de cada fase:** `ruff check .`, `ruff format --check`, `mypy`, a suíte rápida e a mutação dos arquivos de `src` que a fase mudou. A suíte completa com `slow` fecha a leva, sozinha na máquina.

### Pontos para o dono

1. **Nível da prioridade.** O plano usa o mais baixo de cada sistema. É uma constante; se o dono quiser outro nível, muda nela.
2. **Histórico no gate só para a ordem.** No script, o veredito aproveitado tem a rede do `--mutate-all` antes de cada release. No gate não há essa rede, e entre uma tentativa e outra o agente muda justamente o código em volta: um "morto" aproveitado poderia ser um sobrevivente. Rodando todos, com o teste que matou na frente, o mutante morto custa um teste.
3. **macOS não pode ser provado nesta máquina.** O caminho POSIX é o mesmo do Linux, que roda no WSL.

### Verificação

Nada rodou: nenhum código mudou.

### Pendências

- **A leva, pelas fases acima,** a começar pela 0, depois da ordem do dono.
- **Reler `metrics.py` do mutator** antes das fases 4 e 5.
- As pendências da entrada anterior sobre as decisões do dono continuam, depois desta leva.

## 14/11/2026 — leva de desempenho, fases 0 e 1: a prioridade baixa aprova o que devia reprovar com a máquina toda ocupada

A fase 1 parou na medição com a máquina em uso, como o plano previa. O código das duas mudanças está escrito e testado na árvore de trabalho, **sem commit**, à espera da decisão do dono.

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

### Dificuldades

- Um script embutido no shell deixou de aplicar cinco trocas com `\n` no medidor, sem erro. O mesmo tropeço das sessões anteriores; refeito pela ferramenta de edição.
- Duas rodadas com carga passaram dos dez minutos do comando e seguiram em segundo plano.

### Verificação

- `ruff check`, `ruff format --check` e `mypy` strict: limpos, 631 arquivos.
- `pytest -m "not slow" -n auto`: 9873 aprovados, 41 pulados, 267 s.
- Linux, pelo WSL (Ubuntu 22.04, Python 3.12, ambiente próprio): os testes de prioridade, os dois do script, o do `AdapterRunner` e `test_common.py`, 22 aprovados. A suíte inteira não rodou no Linux.
- Não rodou: a mutação dos arquivos de `src` tocados; a nova medição de `measurer.py` com o código novo; a suíte completa com `slow`; Java, C# e Go com 24 processos de carga; macOS.

### Pendências

- **Decisão do dono: como a prioridade baixa deixa de trocar sobrevivente por morto.** Sugestão: o estouro de tempo só conta como morte se, logo depois dele, a cópia sem mutação ainda passa dentro do mesmo limite. Quando não passa, a máquina estava ocupada demais para julgar, e o gate fica inconclusivo em vez de contar o mutante como morto. É divergência do mutator, que conta todo estouro como morte. Custa uma base a mais por mutante que estoura duas vezes.
- **Depois da decisão:** medir de novo com 24 processos de carga nas cinco linguagens, mutação dos arquivos tocados, nova medição de `measurer.py`, e os commits (um por item).
- **A árvore de trabalho tem as duas mudanças da fase 1 sem commit,** além do `CLAUDE.md` e do diário.
- As fases 2 a 5 do plano esperam a fase 1.

## 14/11/2026 — leva de desempenho, fase 1 fechada: o estouro de tempo só mata se a máquina não for a causa

### Decisão do dono

A sugestão da entrada anterior foi aceita ("sim, implemente"): o estouro de tempo só conta como morte se, logo depois dele, a cópia sem mutação ainda passa dentro do mesmo limite. Quando não passa, a rodada fica sem veredito. É divergência do mutator, que conta todo estouro como morte.

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

Quatro rodadas de `scripts/mutate.py --mutate-all`, 24 workers, prioridade ociosa, com vigia da memória livre.

| Rodada | Resultado |
| --- | --- |
| 1 (5306 s) | `mutation/executor.py` (24), `core/process/runner.py` (23), `model.py` (13) e `group.py` (1) sem sobrevivente. `checks/mutation.py` (45): 3 sobreviventes, `mkdir(parents, exist_ok)` e `unlink(missing_ok)` em `AdapterRunner.__call__`, linhas que o meu teste novo passou a cobrir sem conferir. `_windows.py`: base falhou, saída 2. Memória livre mínima 2,16 GB. |
| 2 (5944 s) | `checks/mutation.py` sem sobrevivente (453 s). `_windows.py` (21): 6 sobreviventes, 3474 s, cinco rejulgados por todos os testes. `core/adapters/common.py` (16) e `orchestrator/measurer.py` (94) sem sobrevivente. Memória livre mínima 3,25 GB. |
| 3 (427 s) | `_windows.py`: base falhou de novo. |
| 4 (787 s) | `_windows.py` (20, 340 s) e `checks/differential.py` (16, 139 s) sem sobrevivente. Saída 0. |

A falha da rodada 3 era outro teste: `tests/checks/test_differential_check.py::test_a_slower_version_rejects_with_the_ratio_and_the_limit`, que pedia razão acima de 2 entre programas de 0,6 s e 0,05 s e mediu 1,85 (e 1,67 na suíte rápida). Só apareceu rodando a base numa cópia, como o script faz, em 1 de 4 repetições; na árvore principal passou 4 de 4. `checks/differential.py` entrou na rodada 4 porque afrouxei um teste que o protege.

Ficam sem rodar 15 sítios em linhas que nenhum teste rápido executa: 12 em `checks/mutation.py` (eram 18) e 3 em `_windows.py`. `_posix.py` não foi mutado: o script roda no Windows, onde nenhum teste executa as linhas dele.

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

### Pendências

- **Fases 2 a 5 do plano.** Antes das fases 4 e 5, reler `metrics.py` do mutator.
- **Para o dono decidir, se quiser:** o padrão de um worker por núcleo, dado o custo de memória medido; e se o script deve mostrar o teste que falhou na base.
- O teste do Linux que falha desde `8b61fbe`.
- Nada foi enviado ao GitHub: os commits estão em `correcoes-p0`, só na máquina.