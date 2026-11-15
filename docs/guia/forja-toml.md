# forja.toml

O `forja.toml` é a política do repositório. Ele fica na raiz e diz à Forja o que muda em relação aos padrões dela: quais verificações bloqueiam, quais limites valem, quais comandos o repositório usa no lugar dos da Forja, quais camadas existem e quais casos o teste diferencial roda. Esta página descreve cada chave. Para o raciocínio de cada gate, veja `gates\README.md` e a página do gate citada em cada seção. Para o que a execução recebe pela linha de comando ou por `execucao.toml`, veja `comandos.md` e `execucao-toml.md`.

## Como a Forja lê o arquivo

| Situação | O que acontece |
| --- | --- |
| O arquivo não existe | Vale tudo o que é padrão. Não é erro. |
| O arquivo existe | Os valores dele são aplicados sobre os padrões, valor a valor. Listas (`sensitive`, `branches`, `licenses`, `voice`, `architecture`, `differential`) somam ao que a Forja já traz. |
| O arquivo tem erro | A Forja recusa a execução e escreve `O forja.toml deste repositório tem erros:`, seguido de todos os problemas encontrados de uma vez. |
| O arquivo muda entre pausar e retomar | A Forja recusa a retomada (veja "Política mudada entre pausa e retomada"). |

A Forja só lê o `forja.toml` da raiz do repositório. Não há arquivo de usuário nem arquivo por execução para a política.

O arquivo só define valores. Quais verificações existem, que chaves cada uma aceita e o que ela faz por padrão é decidido pela Forja. Uma chave que a Forja não conhece é erro, não é ignorada.

### Seções aceitas

| Seção | Para que serve |
| --- | --- |
| `[checks.<id>]` | modo, política de inconclusivo, limiares, limites e comandos de uma verificação |
| `[adapters.<linguagem>.commands]` | comando do repositório para uma operação da linguagem |
| `[sensitive]` | arquivos sensíveis a mais |
| `[branches]` | branches protegidas a mais |
| `[tests]` | onde o agente de testes escreve |
| `[licenses]` | licenças proibidas |
| `[voice]` | marcadores de voz ligados e desligados |
| `[redaction]` | redação de segredos e dados pessoais |
| `[architecture]` | camadas do repositório |
| `[differential]` | casos do teste diferencial |

Qualquer outra seção de primeiro nível dá o erro `unknown section`.

### Mensagens de erro

Cada problema aparece como `<origem>: <onde>: <problema>`, onde a origem é `repository forja.toml (<caminho do arquivo>)`. As mensagens estão em inglês. As mais comuns:

| Mensagem | Causa |
| --- | --- |
| `not valid TOML: ...` | o arquivo não é TOML |
| `<seção>: unknown section` | seção de primeiro nível que não existe |
| `checks.<id>: unknown check` | `<id>` não é uma verificação da Forja |
| `<onde>.<chave>: unknown key` | chave que a seção não aceita |
| `checks.<id>.mode: '<valor>' is not one of: blocking, observation` | valor fora da lista |
| `checks.<id>.thresholds.<linguagem>: unknown language; use: csharp, go, java, javascript, python, typescript` | linguagem que a Forja não conhece |
| `checks.<id>.thresholds.<linguagem>.<nome>: this check has no such threshold` | limiar que a verificação não tem |
| `checks.<id>.limits.<nome>: this check has no such limit` | limite que a verificação não tem |
| `checks.<id>.commands.<nome>: this check has no such command` | comando que a verificação não tem |

Erros de valor (tipo errado, número infinito, expressão regular inválida) dizem o que era esperado. Todos os erros de todas as seções saem na mesma mensagem.

## `[checks.<id>]`

Cada verificação da Forja pertence a um gate (de 1 a 14) e tem um `<id>`. A tabela completa está em "Verificações". Cada chave abaixo é opcional.

| Chave | Tipo | Valores | Padrão | Efeito |
| --- | --- | --- | --- | --- |
| `mode` | texto | `blocking`, `observation` | o da verificação (veja "Verificações") | `blocking`: o resultado decide a tentativa. `observation`: mede e reporta, nunca bloqueia. |
| `on_inconclusive` | texto | `block`, `premise` | o da verificação | O que fazer quando a medição não pôde ser feita (ferramenta ausente, tempo esgotado). Só tem efeito em verificação bloqueante. |
| `promotion` | tabela | exatamente `date` e `reason` | ausente | Registro da decisão de promover uma verificação de observação a bloqueante. |
| `thresholds.<linguagem>.<nome>` | número finito | linguagens: `java`, `go`, `python`, `csharp`, `javascript`, `typescript` | o da verificação | Limiar por linguagem. O `<nome>` tem que existir na verificação. |
| `limits.<nome>` | número finito, zero ou mais | | o da verificação | Limite que vale para o repositório todo. O `<nome>` tem que existir na verificação. |
| `commands.<nome>` | lista de textos, não vazia | sem texto vazio | o comando da linguagem | Comando que o repositório usa no lugar do da Forja. O `<nome>` tem que existir na verificação. |

### Modo e política de inconclusivo

O que a Forja faz com o resultado de uma verificação depende de `mode` e de `on_inconclusive`:

| Resultado | `mode = "observation"` | `mode = "blocking"`, `on_inconclusive = "block"` | `mode = "blocking"`, `on_inconclusive = "premise"` |
| --- | --- | --- | --- |
| Aprovado ou não aplicável | passa | passa | passa |
| Reprovado | só aparece no relatório | reprova a tentativa; o motivo vai para a próxima | reprova a tentativa; o motivo vai para a próxima |
| Inconclusivo | só aparece no relatório | bloqueia a tarefa; não conta como falha do agente | segue adiante e declara a lacuna como suposição |

Toda verificação traz um `on_inconclusive` padrão, então qualquer combinação de `mode` e `on_inconclusive` é aceita. `on_inconclusive` em uma verificação de observação fica guardado para o dia em que ela virar bloqueante.

`voice` e `comments` nunca aceitam `mode = "blocking"`: a Forja instrui o agente e reporta o que mediu, sem reprovar.

### Promoção

Promover é mudar uma verificação que começa em `observation` para `blocking`. Isso exige a decisão registrada:

| Padrão da verificação | `mode` no arquivo | `promotion` |
| --- | --- | --- |
| `observation` | `blocking` | obrigatório, com `date` e `reason` |
| `observation` | `observation` ou ausente | proibido |
| `blocking` | `blocking` ou ausente | proibido |
| `blocking` | `observation` | proibido |

| Chave de `promotion` | Tipo | Efeito |
| --- | --- | --- |
| `date` | data TOML (`2030-01-31`) ou texto ISO `"2030-01-31"` | quando a decisão foi tomada |
| `reason` | texto não vazio | por que a verificação passou a bloquear |

Faltando `promotion` ao promover, o erro é `promoting a check from observation to blocking needs a recorded decision: promotion = { date = ..., reason = ... }`. Sobrando, é `promotion is only valid on a check promoted to blocking`.

```toml
# Torna a checagem de tipos bloqueante. Ela nasce em observação,
# então a promoção precisa de data e motivo.
[checks.types]
mode = "blocking"
on_inconclusive = "premise"   # se o verificador de tipos não rodar, segue com a lacuna declarada
promotion = { date = 2030-01-31, reason = "o repositório passou a ter zero erros de tipo" }

# Afrouxa uma verificação que bloqueia por padrão: não precisa de promotion.
[checks.complexity]
mode = "observation"
```

## Verificações

São 34 verificações em 14 gates. Para cada uma: o gate, o modo e a política de inconclusivo padrão, e as chaves que o `forja.toml` aceita. Chave que não está na linha não existe para a verificação.

### Gates 1 a 5

| `<id>` | Gate | `mode` | `on_inconclusive` | O que mede | Chaves |
| --- | --- | --- | --- | --- | --- |
| `scope` | 1 | `blocking` | `block` | a tentativa mexeu só no que a tarefa permitia, e não demais | `limits.max_files`, `limits.max_lines` |
| `freeze` | 1 | `blocking` | `block` | o agente de código não alterou os testes que o agente de testes entregou | |
| `secrets` | 2 | `blocking` | `block` | nenhum segredo nas linhas adicionadas | |
| `dependency_install` | 2 | `blocking` | `block` | dependências novas existem no registro e instalam antes de qualquer execução | |
| `format` | 3 | `blocking` | `block` | arquivos no formato canônico | `commands.format` |
| `lint` | 3 | `blocking` | `block` | nenhuma violação nova de lint | `commands.lint` |
| `types` | 3 | `observation` | `premise` | nenhum erro de tipo novo | `commands.types` |
| `build` | 4 | `blocking` | `block` | o projeto ainda compila | `commands.build` |
| `tests` | 5 | `blocking` | `block` | os testes ainda passam | `commands.test` |
| `red_green` | 5 | `blocking` | `block` | testes novos falham no código com o defeito e nada mais falha | `commands.test` |

Páginas: `gates\seguranca-e-dependencias.md` (gate 2), `gates\tipos.md` (`types`).

### Gates 6 a 8

| `<id>` | Gate | `mode` | `on_inconclusive` | O que mede | Chaves |
| --- | --- | --- | --- | --- | --- |
| `e2e` | 6 | `observation` | `block` | os testes de ponta a ponta passam e nenhum tentou alcançar a rede | |
| `test_data` | 6 | `observation` | `premise` | os arquivos de teste não ganharam dados pessoais | |
| `properties` | 6 | `observation` | `premise` | código de produção alterado vem com teste de propriedades | |
| `mutation` | 7 | `observation` | `premise` | os testes percebem mudanças nas funções que a tentativa tocou | `thresholds.min_score`, `limits.workers` |
| `complexity` | 8 | `blocking` | `premise` | nenhuma função nova ou piorada acima do limite de complexidade | `thresholds.max_ccn` |
| `crap` | 8 | `observation` | `premise` | função complexa e pouco coberta pelos testes | `thresholds.max_crap` |
| `pass_through` | 8 | `observation` | `premise` | função nova que só repassa os próprios argumentos | |
| `module_depth` | 8 | `observation` | `premise` | módulo raso: mostra tanto quanto esconde | `thresholds.max_interface_ratio`, `thresholds.min_statements` |
| `duplication` | 8 | `observation` | `premise` | código duplicado novo nos arquivos de produção alterados | `limits.min_tokens`, `limits.min_lines` |

Páginas: `gates\e2e-e-dados-de-teste.md` (`e2e`, `test_data`), `gates\teste-de-propriedades.md` (`properties`), `gates\mutacao.md` (`mutation`), `gates\duplicacao.md` (`duplication`).

### Gates 9 a 11

| `<id>` | Gate | `mode` | `on_inconclusive` | O que mede | Chaves |
| --- | --- | --- | --- | --- | --- |
| `dependency_cycles` | 9 | `observation` | `block` | ciclo novo de dependência entre diretórios | |
| `layer_rules` | 9 | `observation` | `block` | camada que passou a depender de uma camada acima dela | |
| `package_metrics` | 9 | `observation` | `premise` | diretório que se afastou da sequência principal (métricas de Martin) | `thresholds.max_distance`, `thresholds.min_types` |
| `sast` | 10 | `observation` | `block` | nenhum achado novo do Semgrep nos arquivos alterados | |
| `dependency_audit` | 10 | `observation` | `block` | nenhuma vulnerabilidade nova nem licença proibida | |
| `new_dependency` | 10 | `observation` | `premise` | dependência nova existe no registro oficial, não é recente demais nem pouco usada | `thresholds.min_age_days`, `thresholds.min_downloads` |
| `differential` | 11 | `observation` | `premise` | a nova versão dá as mesmas saídas da base em cada caso, com custo parecido | `thresholds.max_time_ratio`, `thresholds.max_memory_ratio`, `thresholds.runs` |

Páginas: `gates\arquitetura-e-dependencias.md` (gate 9), `gates\seguranca-e-dependencias.md` (gate 10), `gates\teste-diferencial.md` (gate 11).

### Gates 12 a 14

| `<id>` | Gate | `mode` | `on_inconclusive` | O que mede | Chaves |
| --- | --- | --- | --- | --- | --- |
| `voice` | 12 | `observation` | `premise` | marcadores de texto escrito no piloto automático e excesso de travessões | `limits.max_dashes` |
| `comments` | 12 | `observation` | `premise` | comentário novo que repete o código; símbolo público novo sem comentário | |
| `citations` | 13 | `observation` | `block` | cada afirmação do documentador cita algo que existe | |
| `traceability` | 13 | `observation` | `block` | cada elemento da entrada vira critério e cada critério tem origem | |
| `change_record` | 14 | `observation` | `block` | o registro de mudança da tarefa traz os fatos medidos pela Forja | |
| `readme` | 14 | `observation` | `premise` | o README existe, responde o que um leitor precisa e seus comandos são os do projeto | |
| `doc_drift` | 14 | `observation` | `premise` | documento que cita caminho, comando ou símbolo que não existe mais | |
| `adr` | 14 | `observation` | `premise` | dependência ou módulo novo vem com um registro de decisão em `docs/adr` | |

O raciocínio dos gates 12 a 14 está em `gates\README.md`.

## Limiares: `[checks.<id>.thresholds.<linguagem>]`

Um limiar vale para uma linguagem. A tabela abaixo traz, por verificação, o nome, o padrão e como a Forja lê o valor. Um valor que a Forja não aceita como limiar (por exemplo, zero ou negativo onde o limiar precisa ser positivo) não dá erro: a Forja usa o padrão no lugar. O erro só aparece para nome inexistente, linguagem desconhecida ou valor que não é número finito.

| Verificação | Nome | Padrão | Valor lido | Efeito |
| --- | --- | --- | --- | --- |
| `complexity` | `max_ccn` | `15` | maior que zero; a parte decimal é descartada | complexidade ciclomática máxima de uma função. Reprova só função nova ou que piorou; o passivo antigo não reprova. |
| `crap` | `max_crap` | `30.0` | maior que zero | índice CRAP máximo de uma função, que é a complexidade ao quadrado vezes (1 - cobertura) ao cubo, mais a complexidade. Reprova só função nova ou que piorou. |
| `mutation` | `min_score` | `0.80`; `0.70` para `python` e `go` | maior que zero e no máximo `1` | fração mínima de mutantes mortos (mortos sobre mortos mais sobreviventes). Abaixo disso, reprova. |
| `module_depth` | `max_interface_ratio` | `1.0` | maior que zero | razão máxima entre a superfície pública (um por nome público, mais um por parâmetro) e as instruções do módulo. Acima dela o módulo é raso. Reprova só módulo novo ou que ficou mais raso. |
| `module_depth` | `min_statements` | `10` | maior que zero | módulo com menos instruções que isso é deixado em paz. |
| `package_metrics` | `max_distance` | `0.8` | maior que zero | distância máxima de um diretório à sequência principal. Reprova só diretório tocado pela tentativa, acima do limite e que se afastou ou é novo. |
| `package_metrics` | `min_types` | `3` | maior que zero | diretório com menos tipos que isso é deixado em paz. |
| `new_dependency` | `min_age_days` | `30` | zero ou mais; a parte decimal é descartada | idade mínima, em dias, desde a primeira publicação da dependência. |
| `new_dependency` | `min_downloads` | `100` | zero ou mais; a parte decimal é descartada | mínimo de downloads, onde o registro informa (npm: última semana; NuGet: total). |
| `differential` | `max_time_ratio` | `1.25` | maior que zero | razão máxima entre a mediana de tempo da tentativa e a da base. |
| `differential` | `max_memory_ratio` | `1.25` | maior que zero | razão máxima entre o pico de memória da tentativa e o da base, só quando os dois foram medidos. |
| `differential` | `runs` | `3` | maior que zero; a parte decimal é descartada | quantas vezes cada caso roda na tentativa; a mediana é o que conta. |

Dois detalhes do `differential`: um caso não pertence a uma linguagem, então a Forja usa o maior valor entre as linguagens do projeto, e uma linguagem sem valor no arquivo entra com o padrão. Para apertar um limite em um projeto com duas linguagens, defina-o nas duas. O mesmo vale para `runs`, que nunca fica abaixo de `1`.

```toml
# Complexidade máxima por linguagem. O que não for definido usa o padrão (15).
[checks.complexity.thresholds.python]
max_ccn = 10

[checks.complexity.thresholds.typescript]
max_ccn = 12

# Pontuação mínima de mutação só para Go (o padrão do Go é 0.70).
[checks.mutation.thresholds.go]
min_score = 0.75

# Dependências novas precisam ter ao menos 60 dias de publicação em Python.
[checks.new_dependency.thresholds.python]
min_age_days = 60
```

## Limites: `[checks.<id>.limits]`

Um limite vale para o repositório todo, sem linguagem. O arquivo aceita qualquer número finito, zero ou mais; cada verificação só usa o valor se ele for do tipo que ela espera. Um valor que ela não usa é ignorado, e vale o padrão.

| Verificação | Nome | Padrão | Valor usado | Efeito |
| --- | --- | --- | --- | --- |
| `scope` | `max_files` | `8` arquivos por tarefa | inteiro maior que zero | máximo de arquivos que a tarefa pode mudar. A Forja aplica o menor entre o limite da tarefa e o seu; a política só pode apertar. |
| `scope` | `max_lines` | `400` linhas por tarefa | inteiro maior que zero | máximo de linhas mudadas. Mesma regra. |
| `mutation` | `workers` | o número de processadores | inteiro maior que zero | quantos mutantes rodam em paralelo, no máximo. |
| `duplication` | `min_tokens` | `50` | inteiro | tamanho mínimo, em tokens, de um trecho para contar como clone. O agente recebe o mesmo valor na instrução. |
| `duplication` | `min_lines` | `5` | inteiro | tamanho mínimo, em linhas, de um trecho para contar como clone. O agente recebe o mesmo valor na instrução. |
| `voice` | `max_dashes` | `3` | inteiro | travessões que um arquivo (ou a mensagem de commit) pode acrescentar. Acima disso, o achado é reportado. O agente é instruído a não usar nenhum. |

O gate 1 não conta o que o agente de documentação muda: os limites de `scope` valem para o código da tarefa.

```toml
# Tarefas menores do que o padrão da Forja.
[checks.scope.limits]
max_files = 5
max_lines = 200

# Mutação com dois processos em paralelo, por exemplo numa máquina compartilhada.
[checks.mutation.limits]
workers = 2

# Duplicação mais sensível: trechos menores já contam.
[checks.duplication.limits]
min_tokens = 40
min_lines = 4

# Nenhum travessão tolerado.
[checks.voice.limits]
max_dashes = 0
```

## Comandos: `[checks.<id>.commands]`

Uma verificação que roda uma ferramenta da linguagem aceita que o repositório diga o comando. O comando é uma lista (programa e argumentos), nunca um texto de shell. A lista substitui o comando inteiro, inclusive o formato de saída que a Forja lê e os arquivos alterados que ela acrescentaria: ela precisa trazer tudo isso. Diretório, ambiente e leitura da saída continuam sendo os do adaptador da linguagem. O comando só entra se o adaptador da linguagem suporta a operação. A troca vale para todas as linguagens do projeto.

| Verificação | Nome | Efeito |
| --- | --- | --- |
| `format` | `format` | comando que formata |
| `lint` | `lint` | comando do lint |
| `types` | `types` | comando do verificador de tipos |
| `build` | `build` | comando de build |
| `tests` | `test` | comando de testes |
| `red_green` | `test` | comando de testes do agente de testes |

Se o primeiro item é `python` ou `python3` e o adaptador escolheu o interpretador do ambiente virtual do repositório, a Forja usa o interpretador do ambiente virtual.

```toml
# O repositório roda os testes por um alvo próprio, nas duas verificações que usam o comando de teste.
[checks.tests.commands]
test = ["python", "-m", "pytest", "-q", "-x"]

[checks.red_green.commands]
test = ["python", "-m", "pytest", "-q", "-x"]

# Lint com a configuração do repositório.
[checks.lint.commands]
lint = ["ruff", "check", "--ignore-noqa", "--output-format=concise", "--no-cache", "--config", "pyproject.toml", "."]
```

## `[adapters.<linguagem>.commands]`

Comandos por linguagem e por operação. A linguagem é uma de `java`, `go`, `python`, `csharp`, `javascript`, `typescript`. A operação é uma de `format`, `lint`, `lint_fix`, `types`, `build`, `test`, `coverage`, `e2e`.

| Chave | Tipo | Padrão | Efeito |
| --- | --- | --- | --- |
| `commands.<operação>` | lista de textos, não vazia, sem texto vazio | nenhum | comando do repositório para a operação |

Só `e2e` é usado hoje por esta seção. Ele não tem comando padrão: sem ele, o gate de ponta a ponta não roda e a Forja avisa `E2E não executado: sem comando (seção [adapters.<linguagem>.commands] e2e).` Para trocar o comando das demais operações, use `[checks.<id>.commands]`. As outras operações são aceitas nesta seção, mas a Forja não as lê.

O comando `e2e` roda com a rede desligada na medida do possível: as variáveis de proxy (`HTTP_PROXY`, `HTTPS_PROXY`, `ALL_PROXY`, em maiúsculas e minúsculas) apontam para uma armadilha local, `NO_PROXY` vale `localhost,127.0.0.1,::1`, e valem `npm_config_offline=true`, `PIP_NO_INDEX=1`, `GOPROXY=off` e `NODE_USE_ENV_PROXY=1`. Para Java, as propriedades de proxy entram em `JAVA_TOOL_OPTIONS`, depois do que você já tiver definido ali. Veja `gates\e2e-e-dados-de-teste.md`.

```toml
# Comando de ponta a ponta de um projeto Python.
[adapters.python.commands]
e2e = ["python", "-m", "pytest", "tests/e2e", "-q"]

# Um projeto TypeScript no mesmo repositório.
[adapters.typescript.commands]
e2e = ["npm", "run", "test:e2e"]
```

## `[sensitive]`

| Chave | Tipo | Padrão | Efeito |
| --- | --- | --- | --- |
| `paths` | lista de padrões | vazia | arquivos sensíveis a mais. Somam aos da Forja e não os removem. |

Os padrões da Forja são `.env`, `.env.*`, `*.pem`, `*.key`, `*.pfx`, `*.p12`, `id_rsa*`, `id_ed25519*`, `.npmrc`, `.pypirc`, `.netrc`, `credentials*`, `secrets/**`, `.aws/**` e `.ssh/**`.

Um arquivo sensível fica fora da cópia de trabalho do agente, e a autorização recusa enviá-lo ao agente. Se um diretório casa com o padrão, tudo dentro dele é sensível.

Regras do padrão: caminho relativo com `/`; `*` casa qualquer coisa dentro de um nome, sem atravessar `/`; `?` casa um caractere; `**` tem que ser um nome inteiro do caminho e casa vários níveis. Um padrão sem `/` casa em qualquer profundidade; com `/`, parte da raiz do repositório. Não podem ter `\`, `!` no início, `/` no fim, nem os nomes `.` e `..`. Padrão inválido dá erro.

```toml
[sensitive]
paths = [
  "*.keystore",        # em qualquer pasta
  "config/prod/**",    # tudo dentro de config/prod, a partir da raiz
]
```

## `[branches]`

| Chave | Tipo | Padrão | Efeito |
| --- | --- | --- | --- |
| `protected` | lista de nomes | vazia | branches em que a Forja nunca escreve, além das que ela já protege |

A Forja sempre protege `main`, `master` e a branch em que o repositório estava quando a execução começou. Um nome não pode ter espaço nem começar com `-`.

```toml
[branches]
protected = ["release", "staging"]
```

## `[tests]`

| Chave | Tipo | Padrão | Efeito |
| --- | --- | --- | --- |
| `root` | texto | `tests` num repositório que é um só projeto Python; nenhum nas demais linguagens, onde os testes ficam junto do código | diretório em que o agente de testes pode escrever |

Tem que ser um caminho relativo em formato POSIX, dentro do repositório (sem `\`, sem `..`, sem começar com `/`, sem unidade de disco).

```toml
[tests]
root = "src/test"
```

## `[licenses]`

| Chave | Tipo | Padrão | Efeito |
| --- | --- | --- | --- |
| `forbidden` | lista de identificadores SPDX | vazia | licenças que as dependências novas não podem ter |

Não há lista padrão. Sem `forbidden`, a auditoria de dependências (`dependency_audit`, gate 10) olha só vulnerabilidades, e a Forja avisa `Licenças não verificadas: a política não lista licenças proibidas (seção [licenses] forbidden).` Com a lista, uma licença proibida em qualquer ponto de uma expressão SPDX conta, mesmo onde um `OR` deixaria escolha. Um pacote novo cuja licença é desconhecida deixa o resultado inconclusivo. O identificador não pode ter espaço. Veja `gates\seguranca-e-dependencias.md`.

```toml
[licenses]
forbidden = ["GPL-3.0-only", "AGPL-3.0-only"]
```

## `[voice]`

Ajusta os marcadores do gate 12 (`voice`): padrões que denunciam texto escrito no piloto automático, procurados na prosa que a tentativa acrescenta (documentação e comentários).

| Chave | Tipo | Padrão | Efeito |
| --- | --- | --- | --- |
| `markers_extra` | lista de expressões regulares | vazia | padrões proibidos a mais. Cada um vira o marcador `repository-N`, em que N é a posição na lista, a partir de 1. |
| `markers_off` | lista de identificadores | vazia | marcadores da Forja que o repositório desliga |

As expressões são as do Python (`re`), aplicadas sem diferenciar maiúsculas de minúsculas, e não podem casar o texto vazio. Os identificadores seguem `[a-z][a-z0-9-]*`. Os marcadores da Forja são:

| Identificador | O que marca |
| --- | --- |
| `greeting` | abertura que cumprimenta em vez de dizer algo |
| `self-reference` | o modelo falando de si mesmo |
| `inflated` | vocabulário que infla e não diz nada |
| `announcement` | frase que anuncia que algo importa em vez de dizê-lo |
| `offer` | oferta de mais ajuda |

Um identificador que a Forja não tem é ignorado, e a lista continua tão rígida quanto era. A instrução ao agente também reflete o que o repositório ligou e desligou. O limite de travessões é `[checks.voice.limits] max_dashes`.

```toml
[voice]
markers_off = ["offer"]                    # este repositório aceita oferta de ajuda
markers_extra = ["\\bsinergia\\b"]         # proíbe a palavra "sinergia"
```

## `[redaction]`

Ajusta a redação de segredos e dados pessoais. Os detectores de segredo da Forja nunca são opcionais.

| Chave | Tipo | Padrão | Efeito |
| --- | --- | --- | --- |
| `patterns` | tabela `nome = "expressão regular"` | vazia | detectores de segredo a mais. O nome segue `[a-z][a-z0-9-]{0,40}`; a expressão tem no máximo um grupo de captura (o segredo) e não casa o texto vazio. |
| `allow` | lista de textos | vazia | valores exatos que nunca são mascarados. Cada item tem de 1 a 500 caracteres. |
| `allow_patterns` | lista de expressões regulares | vazia | expressões cujo resultado nunca é mascarado. Cada item tem de 1 a 500 caracteres e é uma expressão válida. |
| `pii` | lista | `["cpf", "cnpj", "email", "phone", "card"]` | detectores de dados pessoais ligados. Valores aceitos: `cpf`, `cnpj`, `email`, `phone`, `card`. |
| `entropy_threshold` | número de 0 a 8 | `2.5` | limiar de entropia do detector genérico `chave = valor` |

`pii = []` desliga todos os detectores de dados pessoais.

```toml
[redaction]
pii = ["cpf", "cnpj", "card"]              # e-mail e telefone deixam de ser mascarados
allow = ["fulano@example.com"]
allow_patterns = ["^TOKEN_DE_TESTE_[A-Z]+$"]
entropy_threshold = 3.0                    # menos mascaramento do detector genérico

[redaction.patterns]
chave-interna = "intk_([A-Za-z0-9]{32})"   # o grupo de captura é o segredo
```

## `[architecture]`

Declara as camadas do repositório para o gate 9. A Forja nunca deduz camadas. Sem `[architecture]`, a verificação `layer_rules` não roda e a Forja avisa `Camadas NÃO verificadas: sem regras em [architecture].` Veja `gates\arquitetura-e-dependencias.md`.

| Chave | Tipo | Padrão | Efeito |
| --- | --- | --- | --- |
| `layers` | lista de tabelas `{ name, paths }` | vazia | camadas, da de cima para a de baixo |
| `layers[].name` | texto não vazio | | nome da camada. Único no arquivo. |
| `layers[].paths` | lista de textos, não vazia | | diretórios da camada, como caminhos relativos POSIX a partir da raiz do repositório |

Uma camada pode depender de si mesma e das que estão abaixo dela. Só uma dependência nova da tentativa, de uma camada para outra acima, reprova; o que já apontava para cima é passado do repositório. Um diretório que não está em nenhuma camada é livre. Se um diretório cabe em mais de uma camada, vale a que tem o caminho mais longo.

```toml
# Da camada mais alta para a mais baixa.
[[architecture.layers]]
name = "interface"
paths = ["src/app/cli", "src/app/web"]

[[architecture.layers]]
name = "dominio"
paths = ["src/app/dominio"]

[[architecture.layers]]
name = "infra"
paths = ["src/app/infra"]
```

## `[differential]`

Declara os casos do teste diferencial (gate 11). Sem casos, o gate não roda; num refatoramento a Forja avisa `Teste diferencial não executado: sem casos (seção [[differential.cases]]).` O gate só julga refatoração. Veja `gates\teste-diferencial.md`.

Cada caso é uma tabela em `[[differential.cases]]`:

| Chave | Tipo | Padrão | Efeito |
| --- | --- | --- | --- |
| `name` | texto | obrigatório | nome do caso: letras minúsculas, dígitos, `-` ou `_`, começando por letra ou dígito. Único. |
| `command` | lista de textos, não vazia | obrigatório | programa e argumentos que rodam na versão antiga e na nova. O programa não pode ser um shell (`sh`, `bash`, `zsh`, `dash`, `ksh`, `fish`, `cmd`, `powershell`, `pwsh`, com ou sem `.exe`). |
| `cwd` | texto | a raiz do repositório | diretório de execução, caminho relativo POSIX |
| `stdin` | texto | sem entrada | arquivo do repositório entregue como entrada, caminho relativo POSIX |
| `outputs` | lista de tabelas, não vazia | `stdout` e `exit_code`, ambos com a regra `bytes` | o que comparar |

Cada item de `outputs`:

| Chave | Tipo | Padrão | Efeito |
| --- | --- | --- | --- |
| `kind` | texto | obrigatório | `stdout`, `exit_code` ou `file` |
| `path` | texto | | obrigatório em `file`, proibido nos outros: arquivo (relativo POSIX) que o comando escreve |
| `rule` | texto | `bytes` | `bytes`: igualdade exata. `json`: compara como JSON. `ignore`: descarta as linhas que casam com `ignore` e compara o resto byte a byte. Em `exit_code` só vale `bytes`. |
| `ignore` | lista de expressões regulares | | obrigatória e não vazia com `rule = "ignore"`, proibida nas outras regras. Nenhuma pode casar o texto vazio, pois descartaria todas as linhas. |

Um par (`kind`, `path`) repetido no mesmo caso é recusado. A comparação de tempo e memória usa `thresholds.max_time_ratio`, `thresholds.max_memory_ratio` e `thresholds.runs` de `[checks.differential.thresholds.<linguagem>]`.

```toml
[[differential.cases]]
name = "relatorio-mensal"
command = ["python", "-m", "app.relatorio", "--mes", "2030-01"]
cwd = "servicos/relatorio"                 # opcional; omita para usar a raiz
stdin = "fixtures/entrada.txt"             # opcional
outputs = [
  { kind = "stdout", rule = "ignore", ignore = ["^Gerado em "] },  # ignora a linha com a data
  { kind = "exit_code" },
  { kind = "file", path = "saida/relatorio.json", rule = "json" },
]
```

## O que a Forja imprime quando a política difere dos padrões

Antes de pedir confirmação, a Forja resume a execução. Quando o `forja.toml` muda algo em relação aos padrões, o resumo inclui o bloco `forja.toml do repositório muda o padrão:`, com uma linha por diferença. Quem confirma vê tudo o que o repositório afrouxou, trocou, desligou ou acrescentou: arquivos sensíveis (`[sensitive]`), branches protegidas (`[branches]`), licenças proibidas (`[licenses]`), a raiz dos testes (`[tests]`), os padrões de redação (`[redaction.patterns]`), as camadas (`[architecture]`) e os casos do teste diferencial (`[differential]`).

| Linha | Quando aparece |
| --- | --- |
| `<id>: modo ou política diferente do padrão (<modo>, se não puder medir: <política>)` | `mode` ou `on_inconclusive` diferente do padrão da verificação |
| `<id>: roda no lugar do padrão (<nome>): <comando>` | `[checks.<id>.commands]` |
| `<id>: limite <nome> de <linguagem> = <valor>` | `[checks.<id>.thresholds.<linguagem>]` |
| `<id>: limite <nome> = <valor>` | `[checks.<id>.limits]` |
| `<id>: promovido de observação a bloqueio (<data>)` | `promotion` |
| `<linguagem>: roda no lugar do padrão (<operação>): <comando>` | `[adapters.<linguagem>.commands]` |
| `redação: valores liberados pelo repositório (nunca mascarados)` | `allow` ou `allow_patterns` |
| `redação: detectores de dados pessoais desligados: <lista>` | `pii` sem algum dos cinco padrões |
| `redação: limiar de entropia mais alto (<valor>)` | `entropy_threshold` acima de `2.5` |
| `voz: marcadores desligados pelo repositório: <lista>` | `markers_off` |
| `voz: padrões proibidos pelo repositório: <lista>` | `markers_extra` |

As demais seções (`[sensitive]`, `[branches]`, `[tests]`, `[licenses]`, `[architecture]`, `[differential]` e `redaction.patterns`) não geram linha nesse bloco. Para elas o resumo mostra os avisos de gates sem configuração: `Licenças não verificadas...` sem `[licenses]`, `Camadas NÃO verificadas...` sem `[architecture]`, `E2E não executado...` sem comando `e2e` e `Teste diferencial não executado...` sem casos, num refatoramento.

## Política mudada entre pausa e retomada

A Forja guarda, na confirmação da execução, uma identificação da política em vigor: `sha256:` mais 16 caracteres do resumo do conteúdo efetivo. Ao retomar, ela lê o `forja.toml` de novo e compara. Se o conteúdo efetivo mudou, a retomada é recusada com:

```text
O forja.toml mudou desde que esta execução foi confirmada. Restaure o arquivo como estava ou descarte a execução; a Forja não retoma com outra política.
```

A comparação é do valor efetivo da política, não do texto do arquivo: mudar um comentário, a ordem das chaves ou a formatação não conta. Mudar qualquer valor que altere a política, sim, inclusive um valor que passa a ser igual ao padrão, se antes era diferente.
