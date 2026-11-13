# Gate 3: formato, lint e tipos

O gate 3 reprova a tentativa que deixa o código fora do formato canônico, que acrescenta violação de lint ou que acrescenta erro de tipo. Ele reúne três verificações, `format`, `lint` e `types`, e roda sobre os arquivos que a tentativa mudou. O gate 4 (`build`) vem logo depois e tem uma seção curta no fim desta página. O índice dos gates está em `README.md`; as chaves de política estão em `..\forja-toml.md`.

Antes de qualquer gate ler os arquivos, a Forja corrige o que a ferramenta corrige sozinha: nos arquivos que a tentativa mudou, roda a correção automática do linter e depois o formatador (a correção automática do linter existe em Python, TypeScript, Go e C#). O agente só responde pelo que sobra. A correção vale também para o autor de testes.

## Resumo

| Verificação | O que mede | Como mede | Modo padrão | Se não puder medir (`on_inconclusive`) |
| --- | --- | --- | --- | --- |
| `format` | arquivo mudado fora do formato canônico, ou que não lê | o formatador da linguagem, em modo de checagem | `blocking` | `block` |
| `lint` | violação de lint nova | o linter da linguagem, comparado com a base | `blocking` | `block` |
| `types` | erro de tipo novo | o verificador de tipos, comparado com a base | `observation` | `premise` |

O gate falha se qualquer uma das três falha. Cada uma mede só os arquivos que a tentativa mudou e que ainda existem, e só os que pertencem a um projeto de linguagem que a Forja reconhece.

## `format`

Todo arquivo que a tentativa escreveu precisa estar como o formatador escreveria. Não há catraca aqui: um arquivo mudado fora do formato reprova, mesmo que já estivesse assim antes. O formatador roda em modo de checagem e não altera nada.

| Linguagem | Formatador | Quando existe |
| --- | --- | --- |
| Python | `ruff format --check` | sempre |
| TypeScript | `prettier -l` | o `package.json` depende de `prettier` |
| Go | `gofmt -l` | sempre |
| Java | Spotless (`spotless:check` no Maven, `spotlessCheck` no Gradle) | o `pom.xml` ou o `build.gradle` declara o Spotless |
| C# | `dotnet format whitespace --verify-no-changes` | há uma solução ou um projeto único na raiz |

Os achados são dois: `the file is not in the canonical format` e `the file does not parse` (o formatador não leu o arquivo).

## `lint`

O lint usa catraca: reprova só a violação nova. A Forja conta as violações por arquivo e regra na tentativa e na versão anterior do mesmo arquivo, e reprova a regra cuja contagem aumentou. Violação antiga não reprova. Mover código dentro do arquivo ou renomear o arquivo não faz uma violação antiga parecer nova, porque um arquivo renomeado é comparado com o nome que tinha antes.

| Linguagem | Linter | Quando existe |
| --- | --- | --- |
| Python | `ruff check` | sempre |
| TypeScript | `eslint` | o `package.json` depende de `eslint` |
| Go | `golangci-lint run`, sobre os pacotes dos arquivos mudados | o `golangci-lint` está no `PATH` (a Forja não o instala) |
| Java | Checkstyle, PMD e SpotBugs | o `pom.xml` ou o `build.gradle` declara o plugin de cada um; só os declarados rodam |
| C# | `dotnet format analyzers --verify-no-changes` | há uma solução ou um projeto único na raiz |

O achado é `N new <regra> violation(s)`, com o arquivo, a contagem medida e a contagem da base.

Dois detalhes do Python: o `ruff` roda com `--ignore-noqa`, então um `# noqa` na tentativa não cala o lint; e o `ruff` usado é o da própria Forja, que lê a configuração de lint do repositório (`pyproject.toml`, `ruff.toml`, `.ruff.toml`).

Se o linter roda nos arquivos novos mas não roda na versão anterior deles, não há com o que comparar. O resultado é inconclusivo (`the <linguagem> linter cannot run on the old files`), nunca aprovado.

## `types`

O `types` reprova a tentativa que introduz erro de tipo novo. Só o Python tem esse check: o verificador é o `mypy`. Nas outras linguagens a Forja não tem comando de tipos, e o `types` fica como não aplicável. A análise de tipos delas vem do compilador, que o gate 4 roda.

O check nasce em `observation`: mede e reporta, e não reprova até o repositório promovê-lo.

### O que ele mede

- **Só erro novo.** É a mesma catraca do `lint`, por (arquivo, código de erro do `mypy`), na base e na tentativa. Número de linha, coluna e texto da mensagem não entram na chave, porque mudam com qualquer edição.
- **Só arquivos que a tentativa mudou.** Erro antigo, ou em arquivo que ela não tocou, não reprova.
- **Só `severity` igual a `error`.** Notas do `mypy` não contam.
- **Só o arquivo pedido.** O `mypy` roda com `--follow-imports=silent`: usa os tipos dos módulos importados e esconde os erros deles.
- **Import não achado não conta.** `import-not-found` e `import-untyped` ficam desligados, porque o ambiente do repositório pode não ter tudo.
- **As opções do repositório.** Com `strict` na configuração do `mypy`, a medição é estrita. Sem configuração valem os padrões do `mypy`, em que código sem anotação quase não é verificado.

### Como roda

```text
<mypy> --python-executable <python do repositório> --config-file=<cópia da configuração> --follow-imports=silent
       --output json --cache-dir <dispositivo nulo> --disable-error-code=import-not-found
       --disable-error-code=import-untyped -- <arquivos mudados>
```

- **`--python-executable`** é o interpretador do repositório, o mesmo que roda os testes. A Forja usa o do `.venv` ou do `venv` do repositório, se existir; senão, o primeiro `python` ou `python3` do `PATH`; senão, o Python que roda a própria Forja. Se a execução precisar instalar dependências novas, ela cria um ambiente virtual próprio, e o `mypy` passa a olhar esse. Assim os pacotes instalados no ambiente resolvem.
- **A configuração do repositório vale, por uma cópia.** A Forja lê a primeira de `mypy.ini`, `.mypy.ini`, `pyproject.toml` (`[tool.mypy]`) e `setup.cfg` (`[mypy]`) que tiver configuração, grava uma cópia fora da worktree e a passa em `--config-file`. A cópia não leva `plugins`, que é código do repositório que o `mypy` executaria, nem as chaves que gravam relatório (`*_report`, `junit_xml`). Sem configuração, o `mypy` roda com `--config-file=` e os padrões dele. A base e a tentativa leem a mesma configuração. A decisão está no ADR 0041.
- **Sem cache.** A base e a tentativa não se contaminam e nada cai na worktree.
- O `mypy` é procurado no `PATH`. `forja instalar` o instala quando detecta Python no repositório (veja `..\instalacao.md`).

### Quando fica sem resposta

Falha da ferramenta nunca aprova: o resultado é inconclusivo. Isso vale para o `mypy` ausente, para saída diferente de 0 e 1, para tempo esgotado, para JSON ilegível e para saída 1 sem nenhum erro lido. Vale também para a configuração que declara `plugins`: sem eles o `mypy` acusaria erros que o projeto não tem, então o `types` não mede (`the mypy settings in <arquivo> load plugins`). E para a configuração com nome de opção, nome de seção ou valor que a Forja não consegue copiar com segurança (`have a name or a value the Forja cannot copy`). Como o `types` tem `on_inconclusive = "premise"` por padrão, a tentativa segue e a lacuna é declarada como suposição. O `forja doctor` avisa quando o `mypy` falta, sem tornar isso obrigatório.

### Limites

- **A tentativa pode tentar esconder erro.** `# type: ignore` e `# mypy:` calam o `mypy`, que não tem opção para ignorá-los. O gate 1 (escopo) reprova a tentativa que acrescenta esses comentários, junto com `# noqa`, `# fmt: off`, `# pylint: disable`, `# nosec` e outros silenciadores. O que ele não vê, como uma opção de configuração que afrouxa o `mypy`, só a revisão do ramo pega. Os arquivos de configuração do `mypy` não são arquivos de produção, então o gate 1 não deixa o implementador mexer neles.
- **Erro novo pode vir de outro arquivo.** Mudar a assinatura de uma função quebra quem a chama, e a catraca é por arquivo mudado.

## O que acontece ao falhar

| Resultado | `format` e `lint` (bloqueantes) | `types` (observação) |
| --- | --- | --- |
| Reprovado | a tentativa é rejeitada e o motivo vai para a próxima tentativa | aparece no relatório e a tentativa segue |
| Inconclusivo | a tarefa é bloqueada, sem contar como falha do agente (`on_inconclusive = "block"`) | a tentativa segue e a lacuna é declarada como suposição (`on_inconclusive = "premise"`) |
| Não aplicável | passa | passa |

A tabela geral de modo e política de inconclusivo está em `..\forja-toml.md`.

## Promover o `types` para bloqueio

Para o `types` reprovar a tentativa, promova-o com `mode = "blocking"`. A promoção exige a decisão registrada em `promotion`, com `date` e `reason`. O passo a passo e os cuidados antes de promover um check de observação estão em [seguranca-e-dependencias.md#promover-para-bloqueio](seguranca-e-dependencias.md#promover-para-bloqueio).

```toml
# Torna o check de tipos bloqueante. Ele nasce em observação,
# então a promoção precisa de data e motivo.
[checks.types]
mode = "blocking"
on_inconclusive = "block"   # sem o mypy, a tarefa para em vez de seguir com a lacuna declarada
promotion = { date = 2030-01-31, reason = "o repositório passou a ter zero erros de tipo" }
```

Com `on_inconclusive = "premise"` (o padrão do `types`), a ausência do `mypy` não para a tarefa. Use `"block"` quando a checagem de tipos for requisito do repositório.

## Ajustar com `forja.toml`

As chaves aceitas pelas três verificações são o modo, a política de inconclusivo, a promoção e o comando. Elas não têm limiares nem limites.

| Chave | Padrão | Efeito |
| --- | --- | --- |
| `mode` | `blocking` em `format` e `lint`; `observation` em `types` | decide se o resultado reprova a tentativa |
| `on_inconclusive` | `block` em `format` e `lint`; `premise` em `types` | o que fazer quando a medição não pôde ser feita |
| `promotion` | ausente | obrigatória ao promover `types` a `blocking` |
| `commands.format` | o da linguagem | comando de formatação, em `[checks.format.commands]` |
| `commands.lint` | o da linguagem | comando do lint, em `[checks.lint.commands]` |
| `commands.types` | o da linguagem | comando de tipos, em `[checks.types.commands]` |

```toml
# Formato só informativo neste repositório: aparece no relatório e não reprova.
# Afrouxar uma verificação que bloqueia por padrão não exige promotion.
[checks.format]
mode = "observation"

# Lint: se o ruff não rodar, segue adiante e declara a lacuna como suposição.
[checks.lint]
on_inconclusive = "premise"
```

### Trocar o comando

O comando é uma lista (programa e argumentos), nunca um texto de shell. Ele vale para todas as linguagens do projeto e substitui a linha de comando inteira, inclusive a lista de arquivos: a Forja não acrescenta os arquivos mudados ao fim. Por isso o comando precisa trazer o alvo (por exemplo `"."`), e a medição passa a cobrir o que o alvo cobre. A Forja continua lendo a saída com o leitor da linguagem, então o comando precisa manter o formato de saída de que a leitura depende: `--output-format=concise` no `ruff check` e `--output json` no `mypy`.

```toml
# Lint com a configuração indicada, sobre o projeto todo.
# O leitor do ruff espera o formato concise.
[checks.lint.commands]
lint = ["ruff", "check", "--ignore-noqa", "--output-format=concise", "--no-cache", "--config", "pyproject.toml", "."]

# mypy em modo estrito. O comando substitui o da Forja inteiro, então traz
# o formato json, o interpretador do repositório e o alvo.
# "nul" é o dispositivo nulo do Windows; no Linux use "/dev/null".
[checks.types.commands]
types = ["mypy", "--strict", "--output", "json", "--cache-dir", "nul", "--python-executable", ".venv/Scripts/python.exe", "--", "."]
```

Se o primeiro item do comando é `python` ou `python3` e o adaptador da linguagem usa o interpretador do ambiente do repositório, a Forja usa esse interpretador. Isso vale para os comandos cujo programa padrão é o `python` (build e testes). O comando de tipos parte do `mypy`, então um `python` no início dele é o `python` do `PATH`.

## Gate 4: build

O gate 4 reprova a tentativa que deixa o projeto sem compilar. A verificação é `build`, bloqueante, com `on_inconclusive = "block"`. Roda uma vez por linguagem do projeto e não tem catraca: qualquer falha de build reprova. Os achados são `the file does not compile` (arquivo que não compila) e `the build failed` (build que falhou sem apontar arquivo).

| Linguagem | Comando | Quando existe |
| --- | --- | --- |
| Python | `python -m compileall` | sempre |
| TypeScript | `tsc --noEmit -p .` | o `package.json` depende de `typescript` |
| Go | `go build ./...` | sempre |
| Java | `mvn clean test-compile` ou `gradle compileJava compileTestJava` | há `pom.xml` ou `build.gradle` |
| C# | `dotnet build` | há uma solução ou um projeto único na raiz |

A única chave é `commands.build`, em `[checks.build.commands]`, com as mesmas regras do comando de lint (a lista substitui a linha inteira).

```toml
# Build do TypeScript por um script do próprio repositório.
[checks.build.commands]
build = ["npm", "run", "build"]
```

Nas linguagens sem check de tipos, o `build` faz o papel da análise de tipos: `tsc`, `javac` (pelo Maven ou Gradle), `go build` e o compilador do C#.
