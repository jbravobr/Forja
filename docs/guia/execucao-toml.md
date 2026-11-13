# execucao.toml e modo CI

O `execucao.toml` guarda as respostas da preparação de uma execução. Com `forja --config execucao.toml` a Forja lê o arquivo e não pergunta nada, o que serve para CI. Esta página descreve cada chave, o modo sem perguntas, o perfil do usuário (`config.toml`) e exemplos completos por objetivo.

Regras do arquivo:

- É TOML em UTF-8.
- Só aceita as chaves listadas aqui. Chave desconhecida recusa o arquivo, com a mensagem `<caminho>: <campo>: <motivo>`. TOML inválido também recusa.
- Chave ausente vale o padrão.
- Caminho do Windows vai entre aspas simples (`'C:\Ferramentas\claude\claude.exe'`). Entre aspas duplas, a barra invertida vira escape.
- Caminhos relativos de `arquivo`, `criterios` e `fatos` contam a partir do diretório em que `forja` roda.
- Toda execução que começa grava uma cópia do que usou em `runs/<id>/execucao.toml`, dentro de `FORJA_HOME`, com os caminhos já completos. Essa cópia pode ser reaproveitada em `--config`.

As opções da linha de comando (por exemplo `--cli`, `--modelo`, `--escopo`, `--fatos`, `--semgrep-config`, o texto do verbo e `-f`) têm precedência sobre a chave correspondente do arquivo. Elas estão em `comandos.md`.

![Plano no modo sem perguntas](imagens/terminal-modo-ci-plano.png)

Plano lido do `execucao.toml` (`forja --config execucao.toml --plano`).

## Chaves do arquivo

### `objetivo`

- Tipo: texto. Padrão: `"refatorar"`.
- Valores: `"refatorar"`, `"corrigir"`, `"investigar"`, `"criar"`.
- Efeito: o que a execução faz. O verbo na linha de comando (`forja corrigir`) vale mais que a chave.

```toml
# Corrige um erro em vez de refatorar.
objetivo = "corrigir"
descricao = "o frete grátis aparece para pedidos abaixo de R$ 100"
```

### `descricao`

- Tipo: texto. Padrão: `""`.
- Efeito: em `corrigir`, o erro; em `investigar`, a pergunta; em `criar`, a ideia. Em `refatorar` é ignorada: a medição escolhe as funções e só os `fatos` conduzem o agente.
- Quando falta e `arquivo` também falta, a execução recusa com "Descreva o erro", "Faça a pergunta" ou "Conte a ideia".

```toml
objetivo = "investigar"
# A pergunta que a investigação responde.
descricao = "Onde o cálculo de frete é decidido e quem o chama?"
```

### `arquivo`

- Tipo: texto (caminho). Padrão: `""`.
- Efeito: arquivo UTF-8 com o texto que iria em `descricao`. `descricao` vale mais quando as duas existem.
- Em `corrigir`, se não houver `criterios`, a Forja procura `criterios.json` na mesma pasta desse arquivo.

```toml
objetivo = "corrigir"
# O erro está descrito neste arquivo.
arquivo = "docs/erro-frete.md"
```

### `criterios`

- Tipo: texto (caminho). Padrão: `""`.
- Efeito: só em `corrigir`. Arquivo JSON com os critérios de aceitação. Sem ele, o agente que especifica escreve os critérios a partir da descrição.
- Formato: um objeto com `criteria`, lista de pelo menos um item. Cada item tem `id` (inteiro de 1 em diante, único), `text` (não vazio), `level` (`unit`, `integration` ou `e2e`) e `origin` (lista não vazia de ids; um texto único vira lista de um).
- Arquivo que não serve recusa a execução, citando o campo.

```toml
objetivo = "corrigir"
descricao = "o frete grátis aparece para pedidos abaixo de R$ 100"
# Critérios que você escreveu, no lugar dos que o agente escreveria.
criterios = "criterios.json"
```

### `cli`

- Tipo: texto. Padrão: `""`.
- Valores: `claude-code`, `codex`, `cursor`, `grok`, ou o nome de uma CLI descrita em `[clis.<nome>]`.
- Efeito: a CLI do agente que implementa. Vazio: a `cli` do perfil, senão `claude-code`. Nome desconhecido recusa a execução e lista as conhecidas.

```toml
# Usa o Codex como implementador.
cli = "codex"
```

### `executavel`

- Tipo: texto (caminho). Padrão: `""`.
- Efeito: caminho do executável da `cli`. Vale mais que todas as outras formas de achá-lo (ver "Ordem de resolução do executável"). O caminho precisa existir.
- Sem terminal e com mais de uma instalação encontrada, é obrigatório.

```toml
cli = "claude-code"
# No Windows: 'C:\Ferramentas\claude\claude.exe'
executavel = "/opt/ferramentas/claude"
```

### `cli_testes`

- Tipo: texto. Padrão: `""`.
- Valores: os mesmos de `cli`.
- Efeito: a CLI do agente que escreve os testes, em `corrigir` e `criar`. Vazio: o implementador também escreve os testes. Se for igual a `cli`, é o mesmo agente. Em `refatorar` e `investigar` é ignorada.

```toml
objetivo = "corrigir"
cli = "claude-code"
# Um agente de outra CLI escreve os testes.
cli_testes = "codex"
```

### `executavel_testes`

- Tipo: texto (caminho). Padrão: `""`.
- Efeito: caminho do executável de `cli_testes`. Só vale quando `cli_testes` é uma CLI diferente de `cli`. Obrigatório sem terminal quando há mais de uma instalação de `cli_testes`.

```toml
cli_testes = "codex"
executavel_testes = "/opt/codex/bin/codex"
```

### `modelo`

- Tipo: texto. Padrão: `""`.
- Valores: o nome de um modelo, como a CLI o aceita.
- Efeito: modelo do implementador. Vazio: o modelo configurado na própria CLI.

```toml
# Fixa o modelo do implementador.
modelo = "claude-sonnet-4-5"
```

### `modelo_testes`

- Tipo: texto. Padrão: `""`.
- Efeito: modelo do autor de testes, em `corrigir` e `criar`. Vazio: o do implementador. Se `cli_testes` está vazio e `modelo_testes` não, o autor de testes é a mesma CLI com outro modelo.

```toml
objetivo = "criar"
modelo = "claude-sonnet-4-5"
# O autor de testes usa outro modelo, na mesma CLI.
modelo_testes = "claude-opus-4-1"
```

### `escopo`

- Tipo: lista de textos. Padrão: `[]`.
- Valores: caminhos do repositório.
- Efeito: limita o que a execução pode alterar. Vazio: todo o código. A opção `--escopo` na linha de comando substitui a lista.

```toml
# A execução só mexe nestes caminhos.
escopo = ["src/pedidos", "src/frete"]
```

### `tokens`

- Tipo: inteiro. Padrão: `2000000`.
- Valores: 1 ou mais.
- Efeito: orçamento de tokens da execução inteira.

```toml
# Metade do orçamento padrão.
tokens = 1000000
```

### `agente_roda_testes`

- Tipo: booleano. Padrão: `true`.
- Efeito: `true` deixa o agente rodar os testes do projeto na cópia dele. `false` faz o agente só editar e consultar lint e complexidade.

```toml
# O agente não roda os testes; só os portões da Forja rodam.
agente_roda_testes = false
```

### `tarefas`

- Tipo: inteiro. Padrão: `5`.
- Valores: 1 ou mais.
- Efeito: quantas das piores funções a execução assume como tarefas.

```toml
# Uma execução curta: só a pior função.
tarefas = 1
```

### `paralelismo`

- Tipo: inteiro. Padrão: `1`.
- Valores: 1 ou mais.
- Efeito: quantas tarefas de escopo disjunto rodam ao mesmo tempo.

```toml
# Até duas tarefas de escopo disjunto ao mesmo tempo.
paralelismo = 2
```

### `semgrep_config`

- Tipo: texto (arquivo ou diretório local). Padrão: `""`.
- Efeito: as regras do Semgrep da execução. Referência de registro (`auto`, `p/...`, `r/...`, `s/...`, endereço de rede) é recusada. Vazio: a Forja usa `.semgrep.yml` ou a pasta `.semgrep/` do commit base, se houver. Sem nenhuma regra, o SAST não roda e o resumo avisa.

```toml
# Regras do Semgrep que ficam fora do repositório.
semgrep_config = "regras/semgrep.yml"
```

### `linguagem`

- Tipo: texto. Padrão: `""`.
- Valores: `python`, `go`, `typescript`, `csharp`, `java`.
- Efeito: só em `criar`. A linguagem do projeto novo. Não há como detectá-la num diretório vazio, por isso é obrigatória sem terminal. Outro valor recusa a execução.

```toml
objetivo = "criar"
descricao = "uma CLI que converte CSV em JSON"
linguagem = "python"
```

### `nome`

- Tipo: texto. Padrão: `""`.
- Valores: palavras em minúsculas e dígitos ligadas por hífen, começando por letra, até 40 caracteres (`meu-projeto`).
- Efeito: só em `criar`. O nome do projeto novo. Vazio: a Forja tenta o nome do diretório atual, em minúsculas e com hífens; se ele não serve, recusa e pede `nome`.

```toml
objetivo = "criar"
descricao = "uma CLI que converte CSV em JSON"
linguagem = "python"
nome = "csv-para-json"
```

### `fatos`

- Tipo: texto (caminho). Padrão: `""`.
- Efeito: arquivo de fatos que você mantém, em qualquer objetivo. Em `refatorar` é a única forma de conduzir o agente. Vazio: nenhum fato.
- Formato: uma linha por fato, `id | autor | data | texto`. Uma quinta parte opcional, `confirma:<id>` ou `corrige:<id>`, liga o fato a uma premissa que a Forja registrou. Linhas em branco e iniciadas por `#` são ignoradas. O `id` tem letras, dígitos, `_` e `-`, até 64 caracteres, e é único. A `data` é `AAAA-MM-DD`. O id de premissa tem a forma `p` mais oito dígitos hexadecimais. Erro no arquivo recusa a execução, citando a linha.

```toml
# Fatos do dono: o que o agente não pode adivinhar.
fatos = "fatos.txt"
```

### `instalar_ferramentas`

- Tipo: booleano. Padrão: `false`.
- Efeito: sem terminal (ou com `--config`), a Forja só instala as ferramentas externas que faltam se isto for `true`. Com `false`, ela avisa e segue sem instalar. Se a instalação falhar, a execução recusa antes de começar. Fora do modo sem perguntas a instalação é oferecida na tela e esta chave não faz diferença.

```toml
# O pipeline instala o que faltar (gitleaks, semgrep, e outras).
instalar_ferramentas = true
```

### `[clis.<nome>]`

Descreve uma CLI de agente que a Forja não conhece, por um modelo de comando. Chaves: `comando` (lista de textos, obrigatória, com os marcadores `{executable}`, `{package}`, `{cwd}` e `{role}`), `pacote_por_stdin`, `executaveis`, `versao` e `ambiente`. O `<nome>` não pode ser o de uma CLI conhecida (`claude-code`, `codex`, `cursor`, `grok`). O arquivo `clis.md` explica cada chave e traz exemplos.

```toml
cli = "minha-cli"

[clis.minha-cli]
comando = ["{executable}", "--arquivo", "{package}"]
```

Descrições de `[clis.<nome>]` no `execucao.toml` valem mais que as do perfil quando o nome é o mesmo. Quando a Forja grava a cópia da execução, ela guarda só as CLIs que a execução usou.

### `[interface]`

Como a tela aparece. Em `--config`, vale esta tabela (com os padrões abaixo se ela faltar), e não a do perfil.

| Chave | Tipo | Padrão | Valores | Efeito |
| --- | --- | --- | --- | --- |
| `modo` | texto | `"auto"` | `auto`, `completa`, `linha`, `texto` | força o nível de tela. `texto` é o de pipe e CI |
| `tema` | texto | `""` | `escuro`, `darcula`, `claro`, `contraste`, `neon`, `dos` | vazio segue o fundo do terminal. Outro nome é erro ao abrir a tela |
| `ascii` | booleano | `false` | `true`, `false` | usa glifos ASCII |
| `redimensionar` | booleano | `true` | `true`, `false` | tenta ampliar a janela da tela completa (só em xterm, mintty e iTerm) |

Saída que não é um terminal sempre cai em texto, qualquer que seja o `modo`.

```toml
[interface]
# Tela de texto simples, sem glifos especiais: o que um log de CI lê melhor.
modo = "texto"
ascii = true
```

## Limites do orçamento

A execução tem um orçamento com cinco limites. Dois têm chave no `execucao.toml`:

| Limite | Padrão | Chave |
| --- | --- | --- |
| tokens da execução | `2_000_000` (2.000.000) | `tokens` |
| tarefas por execução | `5` | `tarefas` |
| tentativas por tarefa | `3` | nenhuma |
| arquivos por tarefa | `8` | nenhuma |
| linhas por tarefa | `400` | nenhuma |

Tentativas, arquivos e linhas por tarefa não podem ser mudados pelo `execucao.toml`: não existe chave para eles, e uma chave com esse nome recusa o arquivo.

## Modo CI

O modo sem perguntas liga em dois casos:

- `forja --config <arquivo>`: lê o `execucao.toml` e não pergunta nada, mesmo num terminal interativo. Assim a execução de CI e a mesma execução digitada à mão dão o mesmo resultado.
- Sem terminal interativo (entrada ou saída redirecionada), mesmo sem `--config`.

Nesse modo a Forja repassa o que tem a dizer e responde a cada pergunta com o valor padrão. Também não há a confirmação final "Confirma e começa?": a execução começa direto. `forja --plano` mostra o resumo e sai com código 0 sem começar.

O valor padrão só serve quando não muda o resultado. Quando falta uma resposta sem padrão seguro, a Forja recusa a execução (código de saída 2) e diz onde está a resposta:

| O que falta ou é ambíguo | O que resolve |
| --- | --- |
| Objetivo | `objetivo` no `execucao.toml`, ou o verbo na linha de comando |
| Várias instalações da mesma CLI | `executavel` (ou `executavel_testes`, para o autor de testes) com o caminho desejado. A mensagem lista os caminhos achados |
| CLI não encontrada | instalar a CLI e entrar na conta (`forja doctor` mostra o que falta), ou dar o caminho em `executavel` |
| CLI desconhecida | usar uma das conhecidas, ou descrevê-la em `[clis.<nome>]` |
| Linguagem de `criar` | `linguagem` |
| Descrição, pergunta ou ideia vazias | `descricao`, ou `arquivo` apontando um arquivo com o texto |
| Nome do projeto em `criar` | `nome`, quando o nome do diretório não serve |
| Execução incompleta neste repositório | rodar `forja` num terminal, sem `--config`, e escolher retomar ou descartar |

Uma CLI conhecida com instalação única não gera pergunta nem recusa: a Forja usa essa instalação.

### Exigências por objetivo

Valem para qualquer objetivo, antes de tudo: repositório limpo (abaixo), ao menos 2 GiB livres em disco, `forja.toml` do repositório sem erros, CLI achada, com versão aceita e que passe na prova de que funciona (uma tarefa de teste, lembrada por caminho e versão). O espaço em disco é conferido de novo antes de cada tentativa, no disco do repositório e no da pasta da Forja: com menos de 2 GiB livres a execução pausa, e `forja` a retoma depois de liberado o espaço.

| Objetivo | Chaves e condições sem terminal |
| --- | --- |
| `refatorar` | nada além do repositório git com commits e ao menos uma linguagem detectada. Todas as outras chaves têm padrão |
| `corrigir` | o erro em `descricao` ou `arquivo`. `criterios` é opcional (sem ele o agente escreve os critérios). `cli_testes` e `executavel_testes` só se o autor de testes for outra CLI |
| `investigar` | a pergunta em `descricao` ou `arquivo` |
| `criar` | a ideia em `descricao` ou `arquivo`, `linguagem` e um `nome` válido (ou um diretório cujo nome sirva). O diretório precisa estar vazio, ou ter só um repositório sem commits |

Os objetivos que alteram um repositório existente (`refatorar`, `corrigir`, `investigar`) recusam um diretório vazio ou sem commits, e `criar` recusa um diretório que já tem projeto.

### Repositório limpo

Em `refatorar`, `corrigir` e `investigar`, a Forja recusa a execução se houver alterações não salvas no repositório, dizendo quantos arquivos e três exemplos. Faça commit ou stash antes: a Forja nunca mexe no que você ainda não salvou. As linguagens detectadas são Python (`pyproject.toml`), Go (`go.mod`), TypeScript (`package.json` com `tsconfig.json`), C# (`.sln` ou `.csproj`) e Java (`pom.xml` ou `build.gradle[.kts]`). Sem nenhuma delas, a execução recusa.

### Execução incompleta

Se uma execução anterior neste repositório parou no meio, num terminal a Forja oferece retomar de onde parou ou descartar e recomeçar. Com `--config` ou sem terminal ela recusa ("Resolva a execução incompleta (rode `forja` sem --config) antes") e sai com código 2. Descartar não apaga nada e mostra os comandos para limpar as branches.

### Instalação de ferramentas

Ferramentas externas que faltam (como gitleaks e semgrep) são anunciadas. Sem terminal, só são instaladas com `instalar_ferramentas = true`; sem a chave, a Forja diz "Seguindo sem instalar" e continua. Também é possível rodar `forja instalar` antes. Detalhes em `comandos.md`.

### Confiança no arquivo

Não há arquivo de confiança de executáveis: a Forja não guarda lista de executáveis aprovados e não confere o conteúdo do `execucao.toml`. `--config` executa o que o arquivo manda. Ele pode, por exemplo:

- apontar `executavel` ou `executavel_testes` para um programa qualquer;
- declarar em `[clis.<nome>]` o `comando` de um programa qualquer;
- apontar arquivos que a Forja lê (`arquivo`, `criterios`, `fatos`, `semgrep_config`).

A Forja só confere que o executável responde à versão e passa na prova de que funciona. Use `--config` só com um arquivo que você controla e revisa como código, versionado no repositório ou guardado no pipeline. Não use arquivo recebido de fora, nem um que alguém possa alterar sem revisão.

### Prevenção de suspensão

Só no Windows. Antes de rodar a execução, nova ou retomada, a Forja pede ao sistema que não entre em suspensão (`SetThreadExecutionState`, com os indicadores de execução contínua e de sistema necessário). Ao sair, por qualquer caminho (fim normal, interrupção ou erro), devolve o comportamento normal.

- Mantém o sistema acordado, mas não a tela acesa: ela pode apagar.
- Se o sistema recusar o pedido, a Forja não faz nada e não devolve nada.
- Fora do Windows, não pede nada.
- A proteção cobre a execução, não a preparação nem a confirmação.

## Perfil do usuário: config.toml

O perfil guarda o que é do usuário e não do repositório. Fica em:

| Plataforma | Pasta |
| --- | --- |
| qualquer, com `FORJA_CONFIG_HOME` definida | o valor de `FORJA_CONFIG_HOME` |
| Windows | `%APPDATA%\forja` |
| outras, com `XDG_CONFIG_HOME` definida | `$XDG_CONFIG_HOME/forja` |
| outras, sem ela | `~/.config/forja` |

O arquivo é `config.toml` dentro dessa pasta. Se não existir, vale um perfil vazio. Vale a mesma regra do `execucao.toml`: chave desconhecida recusa o arquivo.

| Chave | Tipo | Padrão | Efeito |
| --- | --- | --- | --- |
| `cli` | texto | `""` | CLI preferida, usada quando nem a linha de comando nem o `execucao.toml` dizem qual |
| `executaveis` | tabela de nome da CLI para caminho | vazia | executável de cada CLI |
| `clis` | tabelas `[clis.<nome>]` | vazia | CLIs descritas por comando, no mesmo formato do `execucao.toml` |
| `interface` | tabela `[interface]` | padrões acima | aparência da tela, usada quando não há `--config` |

```toml
# config.toml do perfil
cli = "claude-code"

[executaveis]
claude-code = '/home/ana/.local/bin/claude'
codex = '/opt/codex/bin/codex'

[interface]
tema = "darcula"
```

Num terminal, quando você escolhe uma instalação entre várias, a Forja pergunta "Lembrar de usar sempre ... para ...?". Respondendo que sim, ela grava `cli` e a entrada em `executaveis` no perfil. Sem terminal ela nunca grava o perfil.

### Ordem de resolução do executável

Para cada CLI, a Forja usa o primeiro que existir, nesta ordem:

1. `executavel` do `execucao.toml` (ou `executavel_testes`, para o autor de testes).
2. A variável `FORJA_EXECUTOR_<NOME>`, com o nome da CLI em maiúsculas e o que não for letra ou dígito virando `_` (`FORJA_EXECUTOR_CLAUDE_CODE`).
3. A entrada de `executaveis` no perfil.
4. A descoberta: o `PATH` e as pastas conhecidas de cada CLI. Se achar mais de uma instalação, a Forja pergunta num terminal; sem terminal, recusa e pede `executavel`.

Um caminho dado pelos três primeiros meios precisa existir como arquivo. Se não existir, não há volta para o seguinte.

A CLI do implementador é escolhida nesta ordem: `--cli`, `cli` do `execucao.toml`, `cli` do perfil, `claude-code`. A do autor de testes: `--cli-testes`, `cli_testes`. Detalhes de variáveis e pastas em `ambiente.md`.

## Exemplos completos

Cada exemplo abaixo foi carregado pela Forja sem erro. Troque os caminhos pelos do seu ambiente.

### Refatorar, em CI

```toml
# execucao.toml de CI: refatoração de um pacote, sem perguntas
objetivo = "refatorar"
cli = "claude-code"
executavel = "/opt/ferramentas/claude"   # no Windows: 'C:\Ferramentas\claude\claude.exe'
modelo = ""                              # vazio: o modelo configurado na própria CLI
escopo = ["src/meuapp"]
tokens = 2000000
agente_roda_testes = true
tarefas = 5
paralelismo = 1
semgrep_config = ""                      # vazio: regras do repositório, se houver
fatos = ""                               # arquivo de fatos opcional
instalar_ferramentas = true              # o pipeline instala o que faltar

[interface]
modo = "texto"
ascii = true
```

Comando: `forja --config execucao.toml`. O repositório precisa estar limpo e sem execução incompleta.

### Corrigir, com fatos e critérios

```toml
# execucao.toml: corrige um erro com critérios e fatos escritos por você
objetivo = "corrigir"
descricao = "o frete grátis aparece para pedidos abaixo de R$ 100"
criterios = "criterios.json"             # critérios de aceitação (JSON)
fatos = "fatos.txt"                      # id | autor | data | texto
cli = "claude-code"
executavel = "/opt/ferramentas/claude"
cli_testes = "codex"                     # outro agente escreve os testes
executavel_testes = "/opt/codex/bin/codex"
modelo_testes = ""
escopo = ["src/frete"]
tokens = 1500000
tarefas = 2

[interface]
modo = "texto"
```

Exemplo de `fatos.txt` para esse arquivo:

```
# id | autor | data | texto
f1 | ana | 2026-03-02 | O limite do frete grátis é R$ 100, definido em src/frete/regras.py
```

### Investigar

```toml
# execucao.toml: investiga uma pergunta e escreve a documentação
objetivo = "investigar"
descricao = "Onde o cálculo de frete é decidido e quem o chama?"
cli = "claude-code"
executavel = "/opt/ferramentas/claude"
escopo = ["src"]
tokens = 800000

[interface]
modo = "texto"
```

A investigação não altera arquivos do projeto nem roda comandos; o que o agente afirma precisa citar arquivo e linhas, medição ou fato seu.

### Criar

```toml
# execucao.toml: cria um projeto novo num diretório vazio
objetivo = "criar"
descricao = "uma CLI que converte arquivos CSV em JSON, com validação de colunas"
linguagem = "python"
nome = "csv-para-json"
cli = "claude-code"
executavel = "/opt/ferramentas/claude"
cli_testes = "codex"
executavel_testes = "/opt/codex/bin/codex"
instalar_ferramentas = true

[interface]
modo = "texto"
ascii = true
```

Rode num diretório vazio (ou com um repositório sem commits). A Forja cria os arquivos do modelo da linguagem, o repositório e o commit inicial depois da confirmação automática, e a execução só segue se o build e os testes do modelo passarem.
