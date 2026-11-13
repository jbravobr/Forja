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
