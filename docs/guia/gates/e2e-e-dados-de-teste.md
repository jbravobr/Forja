# Testes, e2e e dados de teste (gates 5 e 6)

Esta página cobre quatro verificações: `tests` e `red_green` (gate 5), `e2e` e `test_data` (gate 6). O `properties`, que também é do gate 6, está em [teste-de-propriedades.md](teste-de-propriedades.md). O índice dos gates está em [README.md](README.md); as chaves do arquivo estão em [forja.toml](..\forja-toml.md).

| `<id>` | Gate | `mode` | `on_inconclusive` | O que mede | Chaves aceitas |
| --- | --- | --- | --- | --- | --- |
| `tests` | 5 | `blocking` | `block` | os testes do projeto passam | `commands.test` |
| `red_green` | 5 | `blocking` | `block` | os testes novos do autor de testes falham no código com o defeito e nada mais falha | `commands.test` |
| `e2e` | 6 | `observation` | `block` | os testes de ponta a ponta passam e nenhum tentou alcançar a rede | nenhuma em `[checks.e2e]`; o comando vem de `[adapters.<linguagem>.commands]` |
| `test_data` | 6 | `observation` | `premise` | os arquivos de teste não ganharam dados pessoais | nenhuma |

As chaves comuns a toda verificação (`mode`, `on_inconclusive`, `promotion`) valem para as quatro e estão descritas em [forja.toml](..\forja-toml.md).

## `tests`: os testes do projeto

Roda o comando de testes de cada linguagem do projeto e soma o que acha. O comando padrão vem do adaptador da linguagem:

| Linguagem | Comando padrão |
| --- | --- |
| Python | `pytest` com o Python do repositório, sem cache, saída resumida |
| Go | `go test -count=1 -json ./...` |
| TypeScript e JavaScript | o `vitest run` ou o `jest` que o projeto declara como dependência; sem nenhum dos dois, não há comando |
| C# | `dotnet test` sobre o `.sln` ou o projeto |
| Java | `mvn clean test` |

O que reprova:

- teste que falha ou dá erro, exceto os que a base já mostrou instáveis;
- teste que passava na base e sumiu, foi pulado ou deixou de passar. Apagar um teste não esconde uma falha.

Antes da primeira tentativa, a Forja roda os testes duas vezes no código original. Teste que muda de resultado entre as duas execuções vai para a quarentena e não reprova a tentativa. Teste que falha nas duas é registrado como problema da base.

Se a linguagem não oferece o comando (por exemplo, um projeto TypeScript sem `vitest` nem `jest`), o resultado é "não aplicável", nunca aprovação.

### Trocar o comando de testes

```toml
# O repositório roda os testes por um alvo próprio.
[checks.tests.commands]
test = ["python", "-m", "pytest", "-q", "-x"]
```

O comando é uma lista (programa e argumentos), nunca texto de shell. A Forja troca só a linha de comando: diretório, ambiente e leitura da saída continuam sendo os do adaptador. Se o primeiro item é `python` ou `python3` e o adaptador escolheu o interpretador do ambiente virtual do repositório, vale o do ambiente virtual. A troca vale para todas as linguagens do projeto, e o comando só entra onde o adaptador oferece a operação.

## `red_green`: os testes do autor de testes

Em `forja corrigir` e em `forja criar`, um agente escreve os testes antes do código. Nessa etapa, `red_green` ocupa o lugar de `tests`, porque os testes novos precisam falhar. O agente declara, no arquivo de resposta, qual teste cobre cada critério (`criterion_id`, `test_id`, `path`). A Forja confere a declaração e depois roda os testes.

A declaração reprova quando:

- um critério da tarefa não tem teste (`no test is declared for this criterion`);
- um teste declarado não cobre nenhum critério da tarefa;
- o arquivo declarado não existe ou não é arquivo de teste;
- o identificador do teste não pertence ao arquivo. Em Python é o formato do pytest, `caminho::nome`; nas demais linguagens, o adaptador decide.

A execução reprova quando:

- o teste declarado não rodou;
- o teste declarado passa no código com o defeito (`the declared test passes on the code with the defect`);
- o teste declarado falha por erro dele mesmo: em Python, um `NameError`, que é um nome que o teste usa e ninguém definiu (`the declared test fails on its own mistake`). Nenhuma mudança no código faria esse teste passar;
- o teste declarado dá erro de importação, sintaxe ou coleta em vez de falhar (`the declared test errors out instead of failing`). Erro desse tipo não prova o defeito;
- outro teste, que não foi declarado, falha;
- um teste que passava na base sumiu ou deixou de passar.

Quando uma tentativa do agente de código deixa trechos sem teste, a Forja volta ao autor de testes para uma etapa extra. Nessa etapa os testes descrevem código que já existe e precisam passar, então vale `tests`, e o autor só pode acrescentar arquivos, não alterar os que já existem.

### Onde o autor de testes escreve: `[tests]`

| Chave | Tipo | Padrão | Efeito |
| --- | --- | --- | --- |
| `root` | texto | `tests` num repositório que é um só projeto Python; nenhum nas demais linguagens, onde os testes ficam junto do código | diretório em que o agente de testes pode escrever |

O caminho é relativo, em formato POSIX, dentro do repositório (sem `\`, sem `..`, sem começar com `/`, sem unidade de disco). Sem `root`, o agente recebe a instrução de escrever "nos arquivos de teste do projeto", como as linguagens os classificam. Os testes que o autor entrega ficam congelados para o agente de código: mexer neles reprova a tentativa no gate 1 (`freeze`).

### Trocar o comando do `red_green`

```toml
[checks.red_green.commands]
test = ["python", "-m", "pytest", "-q", "-x"]
```

Funciona como em `tests`. Os dois são independentes: quem quer o mesmo comando nos dois declara nos dois.

## `e2e`: ponta a ponta

### O comando é seu

A Forja não traz comando de e2e e não inventa um. Você declara, por linguagem, a chave `e2e` em `[adapters.<linguagem>.commands]`. A linguagem é a do adaptador: `python`, `go`, `typescript`, `csharp` ou `java`. JavaScript é atendido pelo adaptador `typescript`: a seção `[adapters.javascript]` é recusada, porque nenhum adaptador tem esse nome. Use `[adapters.typescript.commands]`.

```toml
# Python: roda só a pasta de e2e. O primeiro item "python" vira o
# interpretador do ambiente virtual do repositório.
[adapters.python.commands]
e2e = ["python", "-m", "pytest", "tests/e2e", "-q"]

# TypeScript no mesmo repositório (monorepo): comando próprio.
[adapters.typescript.commands]
e2e = ["npm", "run", "test:e2e"]
```

O comando roda com o diretório, o ambiente e o interpretador que o adaptador usa para rodar testes, e a saída é lida como a de qualquer execução de testes. Por isso o e2e só roda numa linguagem em que o adaptador sabe rodar testes: um projeto TypeScript sem `vitest` nem `jest` é deixado de fora, mesmo com `e2e` declarado. Em monorepo, roda uma vez por projeto da linguagem.

Das operações que a seção `[adapters.<linguagem>.commands]` aceita (`format`, `lint`, `lint_fix`, `types`, `build`, `test`, `coverage`, `e2e`), só `e2e` tem efeito. As outras são aceitas, mas a Forja não as lê. Para trocar o comando de testes, use `[checks.tests.commands]`, como acima.

Quando o arquivo declara um comando, o resumo antes da confirmação traz a linha `<linguagem>: roda no lugar do padrão (e2e): <comando>`.

### Sem comando

Sem comando em nenhuma linguagem, o check devolve "não aplicável" e o resumo diz `E2E não executado: sem comando (seção [adapters.<linguagem>.commands] e2e).` Nunca é uma aprovação.

### O que reprova

- teste de e2e que falha ou dá erro;
- teste de e2e que passava na base e sumiu ou deixou de passar;
- tentativa de acesso à rede, mesmo que todos os testes tenham passado. A mensagem é `tried to reach the network` e traz o destino (`host:porta`), nunca caminho nem parâmetros.

Testes de e2e que mudam de resultado entre as duas execuções da base vão para a quarentena e não reprovam a tentativa. A base roda o e2e duas vezes; falha ou tentativa de rede na base é registrada como problema da base.

Se o comando não terminar (tempo esgotado, programa que não abre), o resultado é inconclusivo e, no padrão `block`, a tarefa para. Uma rejeição vale mais que um comando que não rodou: se um e2e já falhou ou tentou a rede, a tentativa é reprovada mesmo que outra linguagem tenha ficado inconclusiva.

O comando de `tests` não passa pela armadilha de rede. Se o comando de testes alcança a pasta dos testes de e2e (o padrão do Python roda tudo), eles rodam ali sem a armadilha. Para separar, aponte `[checks.tests.commands]` para fora dessa pasta, como no segundo exemplo no fim desta página.

### Tempo máximo

O e2e usa o mesmo executor das demais ferramentas: processo isolado, ambiente filtrado e tempo máximo de 300 segundos por execução do comando. O valor é fixo: nem `forja.toml` nem `execucao.toml` têm chave de tempo para o e2e ou para `tests`.

## Como a rede é desligada e o que isso não é

A Forja não cria sandbox. Para cada execução de e2e (e para cada caso do teste diferencial) ela abre uma armadilha de proxy em `127.0.0.1`, numa porta efêmera, e entrega ao processo `HTTP_PROXY`, `HTTPS_PROXY` e `ALL_PROXY`, em maiúsculas e minúsculas, apontando para ela, com `NO_PROXY=localhost,127.0.0.1,::1`. Fakes em loopback continuam alcançáveis. Um cliente que respeita o proxy e tenta sair cai na armadilha, que registra o destino (host e porta, nunca o caminho), responde 403 e fecha a conexão.

Além do proxy, o processo recebe:

| Variável | Para quê |
| --- | --- |
| `NODE_USE_ENV_PROXY=1` | o Node ignora as variáveis de proxy sem ela |
| `JAVA_TOOL_OPTIONS` com `-Dhttp.proxyHost` e afins | a JVM só respeita as propriedades próprias; o valor que você já tinha é preservado e as propriedades entram depois dele |
| `npm_config_offline=true`, `PIP_NO_INDEX=1`, `GOPROXY=off` | modo offline dos gerenciadores de pacote |

Medidos com clientes reais no Windows, Python `urllib`, Go `net/http` e .NET `HttpClient` honram o proxy, e Node e Java honram com as chaves acima.

Isso não é contenção. A armadilha só vê o cliente que usa o proxy:

- soquete cru, em qualquer linguagem, passa sem ser visto;
- Python `http.client` resolve o DNS direto e também passa;
- `GOPROXY=off` é a chave documentada do Go, mas não ficou provado que ela barre o download;
- Maven, Gradle e NuGet ficam só com a armadilha: `-o` e `--offline` são argumentos do comando, que é seu.

Quem precisa de contenção forte roda a Forja já dentro de um ambiente sem rede. O resumo diz isso em toda execução com e2e declarado: `E2E: rede desligada por armadilha de proxy; não impede cliente que ignora o proxy (soquete cru, Python http.client). Contenção forte é do ambiente.`

## Fakes

A Forja não valida fakes por contrato. Toda execução com e2e declarado registra no resumo `E2E: fakes não validados por contrato; podem divergir do serviço real.` Relógio fake, sementes fixas e o fake em si são responsabilidade do teste. O que a Forja garante é só o que lhe cabe: as duas execuções da base, que separam o teste instável do estável.

## `test_data`: dados pessoais em teste e fixture

O check lê os arquivos de teste e de fixture que a tentativa mudou (a classificação é do adaptador da linguagem) e procura, com os detectores da redação, CPF, CNPJ, e-mail, telefone e cartão. A lista `pii` de `[redaction]` não o afeta: os cinco detectores valem sempre aqui.

Reprova só o que aumentou, por arquivo e tipo, em relação ao commit pai. Mover linha ou renomear o arquivo não torna dado antigo "novo". A mensagem diz o tipo, a quantidade e o arquivo (por exemplo, `2 new cpf value(s) in test data`), nunca o valor.

Não contam:

- CPF, CNPJ e cartão que falham no dígito verificador ou no Luhn;
- e-mail em domínio reservado: `example.com`, `example.org`, `example.net`, `localhost`, e os sufixos `.test`, `.invalid`, `.example`, `.localhost` e `.local`;
- telefone fora dos DDDs válidos.

Arquivo ilegível dá resultado inconclusivo; no padrão `premise`, a tarefa segue com a lacuna declarada no resumo.

Use valores sintéticos: CPF com dígitos que não fecham, e-mail em domínio reservado. Não há lista de exceções por repositório. O autor de testes pode declarar geradores de dados com semente (`name`, `seed`, `path`) na resposta; a Forja os guarda no manifesto de testes da tarefa, mas não os executa nem os confere.

O scanner acha padrões; não prova ausência de dado real.

## Exemplos

Os dois exemplos abaixo foram carregados com a política da Forja e são aceitos.

```toml
# Torna o e2e bloqueante. Ele nasce em observação, então a promoção
# precisa de data e motivo. Se o e2e não puder rodar, a tarefa segue
# com a lacuna declarada em vez de parar.
[checks.e2e]
mode = "blocking"
on_inconclusive = "premise"
promotion = { date = 2030-02-01, reason = "a suíte de e2e ficou estável por um mês" }

# Torna o scanner de dados pessoais bloqueante, com o mesmo registro.
[checks.test_data]
mode = "blocking"
promotion = { date = 2030-02-01, reason = "fixtures sem dado real desde a última auditoria" }
```

```toml
# O autor de testes escreve em src/test, no lugar do padrão da linguagem.
[tests]
root = "src/test"

# Os testes comuns e o red_green usam o mesmo comando próprio, sem a pasta de e2e.
[checks.tests.commands]
test = ["python", "-m", "pytest", "-q", "-x", "--ignore=tests/e2e"]

[checks.red_green.commands]
test = ["python", "-m", "pytest", "-q", "-x", "--ignore=tests/e2e"]

# O e2e roda no próprio comando, com a armadilha de rede.
[adapters.python.commands]
e2e = ["python", "-m", "pytest", "tests/e2e", "-q"]
```

No segundo exemplo, `--ignore=tests/e2e` tira a pasta de e2e dos testes comuns, para que ela rode só com a armadilha de rede. Quando o `forja.toml` troca algo disso, o resumo antes da confirmação traz uma linha por diferença, como `tests: roda no lugar do padrão (test): python -m pytest -q -x --ignore=tests/e2e`.
