# Gates 7 e 8: mutação e métricas

Esta página descreve os dois gates que medem a qualidade do código que a tentativa escreveu: o gate 7 (`mutation`) e o gate 8 (`complexity`, `crap`, `pass_through`, `module_depth` e `duplication`). Para o índice dos gates, veja `README.md`. Para o formato do `forja.toml`, veja `..\forja-toml.md`. Na barra de gates da tela, o gate 7 aparece como `mutacao` e o gate 8 como `metricas` (veja `..\ambiente.md`).

## Resumo

| `<id>` | Gate | `mode` padrão | `on_inconclusive` padrão | O que mede | Chaves |
| --- | --- | --- | --- | --- | --- |
| `mutation` | 7 | `observation` | `premise` | os testes percebem mudanças no código que a tentativa escreveu ou tocou | `thresholds.min_score`, `limits.workers` |
| `complexity` | 8 | `blocking` | `premise` | nenhuma função nova ou piorada passa do limite de complexidade | `thresholds.max_ccn` |
| `crap` | 8 | `observation` | `premise` | função complexa e pouco coberta pelos testes | `thresholds.max_crap` |
| `pass_through` | 8 | `observation` | `premise` | função nova que só repassa os próprios argumentos | nenhuma |
| `module_depth` | 8 | `observation` | `premise` | módulo raso: mostra tanto quanto esconde | `thresholds.max_interface_ratio`, `thresholds.min_statements` |
| `duplication` | 8 | `observation` | `premise` | código duplicado novo | `limits.min_tokens`, `limits.min_lines` (veja `duplicacao.md`) |

`complexity` é a única verificação desses gates que bloqueia por padrão. As demais medem e reportam. Um gate falha se qualquer verificação dele falha.

### Como o modo decide o que acontece

| Resultado | `mode = "observation"` | `mode = "blocking"`, `on_inconclusive = "block"` | `mode = "blocking"`, `on_inconclusive = "premise"` |
| --- | --- | --- | --- |
| Aprovado ou não aplicável | passa | passa | passa |
| Reprovado | só aparece no relatório | reprova a tentativa; o motivo vai para a próxima | reprova a tentativa; o motivo vai para a próxima |
| Inconclusivo | só aparece no relatório | bloqueia a tarefa | segue adiante e declara a lacuna como suposição |

Promover uma verificação de `observation` a `blocking` exige a decisão registrada em `promotion` (data e motivo). A regra completa está em `..\forja-toml.md`, seção "Promoção".

### O que é "código da tentativa"

Todas as verificações olham só arquivos de produção. Arquivos de teste, fixtures e arquivos de configuração das ferramentas ficam de fora. Quem decide o que é produção é o adaptador de cada linguagem (por exemplo, em Python, `test_*.py`, `*_test.py`, `conftest.py` e pastas de teste são testes). Em repositório com mais de um projeto (várias linguagens ou várias pastas com manifesto), `mutation`, `complexity` e `crap` rodam uma vez por projeto, com os limites da linguagem dele, e o resultado é o conjunto delas.

## `mutation` (gate 7)

### O que mede

A Forja faz pequenas alterações no código que a tentativa tocou, uma por vez, e roda os testes depois de cada uma. Cada alteração é um mutante. Se algum teste falha, o mutante foi morto: os testes perceberam a mudança. Se todos passam, o mutante sobreviveu: a mudança passou despercebida, o que indica um teste fraco ou ausente.

A nota é a fração de mutantes mortos:

```text
nota = mortos / (mortos + sobreviventes)
```

Um mutante que estoura o tempo conta como morto. Um mutante em linha que nenhum teste executa não roda e fica fora da nota; num comando que ocupa várias linhas, basta que o relatório de cobertura marque a linha do mutante ou a linha onde o comando começa. Se nenhum mutante rodou, não há nota e o gate passa. Uma nota abaixo de `min_score` reprova.

### Que alterações a Forja faz

As regras são as do `unclebob/mutator`. O conjunto do Java é a base, e as demais linguagens são esse conjunto escrito na sintaxe delas.

| Categoria | Alteração |
| --- | --- |
| Aritmética | `+` por `-`, `-` por `+`, `*` por `/`, `/` por `*` |
| Comparação | `>` por `>=`, `>=` por `>`, `<` por `<=`, `<=` por `<` |
| Igualdade | `==` por `!=` e o inverso; em JavaScript e TypeScript, também `===` por `!==` e o inverso |
| Lógica | `&&` por `\|\|` e o inverso; em Python, `and` por `or` e o inverso; em JavaScript e TypeScript, `??` por `\|\|` |
| Negação | `!` ou `not` é removido; o `-` unário é removido |
| Booleano | `true` por `false` e o inverso (`True` e `False` em Python) |
| Constante | só `0` por `1` e `1` por `0` |
| Encadeamento opcional | em JavaScript e TypeScript, `?.` vira `.` (ou some, na chamada e no índice) |

Regras gerais:

- Um operador só muda dentro de uma expressão binária.
- Texto de string e comentário nunca muda.
- Código fora de função nunca muda. Em Java, construtor não conta como função.
- Em TypeScript, o que está dentro de um tipo nunca muda: o `true` de `{ ok: true }` num tipo de retorno não é executado.
- Em Go, `/` não muda; `*` vira `/`.
- A mutação serve Python, Go, Java, C#, JavaScript e TypeScript. JavaScript e TypeScript são tratados como uma família só.

### Quando roda

A verificação só age se a tentativa acrescentou linhas em arquivo de produção de uma dessas linguagens. Sem linha acrescentada, o resultado é aprovado. O fluxo:

1. A Forja roda a cobertura de testes da linguagem, para saber quais linhas algum teste executa.
2. Se alguma linha acrescentada com ponto de mutação não tem cobertura, o gate reprova nesse ponto, sem rodar mutantes (veja "Linha sem teste").
3. A Forja confirma que os testes passam numa cópia do projeto sem alteração. Se falham, o resultado é inconclusivo.
4. Cada mutante roda em cópia descartável: troca-se o arquivo, roda-se o comando de testes inteiro da linguagem, restaura-se o arquivo. O repositório e a cópia de trabalho da tentativa nunca são alterados.

Limites de tempo: a verificação inicial dos testes pode levar até 600 segundos. Cada mutante tem como limite dez vezes a duração dessa verificação inicial, nunca menos de 2 segundos. Estourar o limite mata o mutante.

O comando de testes dos mutantes é o do adaptador da linguagem. Em projeto Maven ele é `mvn test`, sem o `clean` do gate de testes: os mutantes de um trabalhador rodam um depois do outro na mesma cópia, e o compilador refaz o que mudou. Um comando de teste trocado em `[checks.tests.commands]` não vale para a mutação, e a mutação não aceita `commands`.

### Mutação incremental

A Forja não muta o projeto todo, só o que a tentativa tocou. Entram:

- todo ponto de mutação nas linhas que a tentativa acrescentou;
- todo ponto de mutação dentro das funções que contêm alguma linha acrescentada, mesmo nas linhas antigas dessas funções.

Uma função que a tentativa não tocou não entra, e um sobrevivente que já existia nela não é cobrado. A Forja também não guarda resultado de uma tentativa para a seguinte: cada tentativa vale por si. A regra é de catraca, não de faxina: o gate cobra o que a tentativa mexeu, não a dívida antiga.

| Situação | O que acontece |
| --- | --- |
| Linha acrescentada sem cobertura | reprova (veja "Linha sem teste") |
| Linha antiga, sem cobertura, em função tocada | não reprova; o mutante não roda e fica fora da nota |
| Linha antiga, coberta, em função tocada | o mutante roda como qualquer outro; se sobreviver, entra na nota |
| Função aninhada | a função de fora tem como trecho as linhas da de dentro; tocar numa das duas põe as duas no conjunto |
| Duas definições com o mesmo nome qualificado (sobrecarga) | entram juntas |
| Função grande | tocá-la custa muitos mutantes a mais; funções menores deixam a mutação mais rápida |

### Chaves

| Chave | Tipo | Padrão | Efeito |
| --- | --- | --- | --- |
| `mode` | `blocking` ou `observation` | `observation` | `blocking` exige `promotion` |
| `on_inconclusive` | `block` ou `premise` | `premise` | o que fazer quando a medição não pôde ser feita |
| `thresholds.<linguagem>.min_score` | número maior que `0` e até `1` | `0.80`; `0.70` para `python` e `go` | nota mínima. Valor fora dessa faixa é ignorado e vale o padrão. |
| `limits.workers` | inteiro maior que `0` | o menor entre `4` e o número de processadores | quantos mutantes rodam em paralelo. Cada trabalhador usa uma cópia do projeto. Valor que não é inteiro positivo é ignorado. |

Os padrões menores de Python e Go existem porque a mutação nessas linguagens é mais lenta e mais frágil.

```toml
# Torna a mutação bloqueante. Ela nasce em observação, então a promoção
# precisa de data e motivo. Sem os dois, o forja.toml é recusado.
[checks.mutation]
mode = "blocking"
on_inconclusive = "premise"   # se os testes não rodarem, segue com a lacuna declarada
promotion = { date = 2030-01-31, reason = "a suíte do repositório já mata mais de 90% dos mutantes" }

# Nota mínima mais exigente só para Python. As outras linguagens mantêm os padrões.
[checks.mutation.thresholds.python]
min_score = 0.85

# Nota mínima para TypeScript, que não tem valor próprio por padrão (vale 0.80).
[checks.mutation.thresholds.typescript]
min_score = 0.75

# Dois mutantes em paralelo, para uma máquina compartilhada ou com pouca memória.
[checks.mutation.limits]
workers = 2
```

O exemplo faz três coisas independentes, e cada bloco pode ser usado sozinho. Sem `[checks.mutation]`, a mutação continua em observação. `min_score` é por linguagem porque a nota depende de quão frágil a mutação é em cada uma. `workers` vale para o repositório todo.

### Linha sem teste

Se a tentativa acrescenta uma linha com ponto de mutação que nenhum teste executa, não há contra o que mutar. O resultado é reprovado, com um achado por linha, que traz o arquivo e o número da linha:

```text
no test runs this new line; a test for it is needed before mutation
```

Em observação, o achado só aparece no relatório. Em bloqueio, a tentativa volta ao agente com a lista de linhas.

### Quando a nota fica abaixo do limite

O resultado é reprovado, com:

- um achado `the tests do not notice enough changes to the new code`, com a nota medida (duas casas) e o limite;
- um achado por mutante sobrevivente, no formato `change not noticed (survived): <original> -> <mutante>`, com arquivo e linha.

A lista de sobreviventes diz ao agente que teste falta: cada linha é uma mudança que nenhum teste percebe.

### Quando fica inconclusivo

| Situação | Mensagem |
| --- | --- |
| os testes falham na cópia sem alteração | `mutation: the tests do not pass on the unchanged copy` |
| o comando de testes não executa | `mutation: the tests could not run (<motivo>)` |
| a ferramenta de cobertura não executa, termina com erro ou não escreve o relatório | `coverage: ...` |
| a linguagem não tem cobertura disponível | não aplicável |

Em `observation`, o inconclusivo só aparece no relatório. Em `blocking`, `on_inconclusive` decide entre bloquear a tarefa (`block`) e seguir declarando a lacuna (`premise`).

## `complexity` (gate 8)

### O que mede

A complexidade ciclomática (CCN) de cada função, medida pelo `lizard`, que a Forja traz. Ela conta os caminhos independentes: cada `if`, laço, `case`, operador lógico e tratamento de exceção soma um. O `lizard` lê todas as linguagens da Forja, então a verificação não depende de adaptador.

### Quando roda

Em toda tentativa que alterou arquivo de produção. A verificação mede os arquivos alterados agora e a versão deles antes da tentativa. Uma função reprova só se as duas condições valem:

1. a CCN dela passa do limite;
2. ela é nova, ou a CCN dela ficou maior que antes.

O passivo antigo não reprova: uma função já complexa que a tentativa não piorou passa. Se a tentativa renomeia o arquivo, a comparação segue o arquivo pelo nome antigo. Métodos com o mesmo nome no mesmo arquivo contam pelo pior.

### Chaves

| Chave | Tipo | Padrão | Efeito |
| --- | --- | --- | --- |
| `mode` | `blocking` ou `observation` | `blocking` | afrouxar para `observation` não exige `promotion` |
| `on_inconclusive` | `block` ou `premise` | `premise` | se o `lizard` não rodar, a Forja segue e declara a lacuna |
| `thresholds.<linguagem>.max_ccn` | número maior que `0` | `15` | CCN máxima de uma função. A parte decimal é descartada. Valor zero ou negativo é ignorado e vale o padrão. |

O padrão `15` é o do próprio `lizard`.

```toml
# Python mais rígido que o padrão (15); TypeScript um pouco mais.
# As demais linguagens continuam em 15.
[checks.complexity.thresholds.python]
max_ccn = 10

[checks.complexity.thresholds.typescript]
max_ccn = 12

# Só reporta, sem reprovar a tentativa. Como complexity já bloqueia por
# padrão, voltar a observação não precisa de promotion.
[checks.complexity]
mode = "observation"
```

Os blocos de limiar valem em qualquer modo. O bloco `[checks.complexity]` é opcional: quem quer manter o bloqueio não o escreve.

### Ao falhar

O achado diz `function <nome> is too complex`, com a CCN medida e o limite, e aponta para o arquivo e a função. Em bloqueio, a tentativa volta ao agente com esse motivo.

## `crap` (gate 8)

### O que mede

O índice CRAP de cada função: a complexidade pesada pela falta de cobertura.

```text
CRAP = CCN^2 * (1 - cobertura)^3 + CCN
```

A cobertura é a fração das linhas executáveis da função que algum teste executa (de `0` a `1`). Um arquivo que a ferramenta de cobertura não viu conta como não testado. Uma função sem linha executável conta como totalmente coberta.

| CCN | Cobertura | CRAP |
| --- | --- | --- |
| 6 | 0% | 42,0 |
| 6 | 50% | 10,5 |
| 10 | 0% | 110,0 |
| 10 | 50% | 22,5 |
| 10 | 80% | 10,8 |
| 10 | 100% | 10,0 |

Com o limite padrão `30,0`, uma função de CCN 10 precisa de cerca de 42% de cobertura. Uma função de CCN até 5 nunca passa do limite, nem sem teste algum.

### Quando roda

Como `complexity`: em toda tentativa que alterou arquivo de produção, e só reprova função nova ou que ficou mais complexa que antes. O passivo antigo não reprova. A cobertura é cara, então a Forja só a mede se alguma função alterada passaria do limite mesmo sem nenhuma cobertura. Quando mede, é a mesma cobertura que a mutação usa, medida uma vez por tentativa.

### Chaves

| Chave | Tipo | Padrão | Efeito |
| --- | --- | --- | --- |
| `mode` | `blocking` ou `observation` | `observation` | `blocking` exige `promotion` |
| `on_inconclusive` | `block` ou `premise` | `premise` | o que fazer quando a medição não pôde ser feita |
| `thresholds.<linguagem>.max_crap` | número maior que `0` | `30.0` | CRAP máximo de uma função. Valor zero ou negativo é ignorado e vale o padrão. |

```toml
# Limite mais baixo para Go. As demais linguagens continuam em 30.0.
[checks.crap.thresholds.go]
max_crap = 20.0

# Torna o CRAP bloqueante.
[checks.crap]
mode = "blocking"
promotion = { date = 2030-01-31, reason = "a cobertura do repositório passou de 80%" }
```

### Ao falhar

O achado diz `function <nome> is complex and little tested`, com o CRAP medido (uma casa) e o limite, e aponta para o arquivo e a função. O remédio é escrever testes que executem a função ou reduzir a complexidade dela.

## `pass_through` (gate 8)

### O que mede

Função nova cujo corpo inteiro só chama outra função com os próprios parâmetros, sem alteração e na mesma ordem. Ela acrescenta um nome e um salto, e nada mais. A verificação existe para que dividir funções, para baixar a complexidade, não deixe um rastro de funções que só repassam trabalho.

Exemplo do que conta, em Python:

```python
def total(itens, desconto):
    return calcular_total(itens, desconto)
```

Não conta como repasse:

- função que faz qualquer coisa além da chamada (outro argumento, outra instrução, um valor calculado);
- um parâmetro passado fora de ordem, com outro nome ou como expressão;
- em Python, função com decorador, método especial (`__init__`, `__repr__` e semelhantes) e método de classe `Protocol`.

Em Python, o primeiro parâmetro `self` ou `cls` é desprezado na comparação, e uma docstring antes da chamada não impede que a função conte.

### Quando roda

Em toda tentativa que alterou arquivo de produção, nas seis linguagens. Só reprova função que passou a ser repasse nesta tentativa, comparada pelo nome qualificado com a versão do arquivo antes dela. Repasse que já existia não reprova.

### Chaves

Nenhum limiar nem limite. Só `mode` e `on_inconclusive`.

| Chave | Tipo | Padrão | Efeito |
| --- | --- | --- | --- |
| `mode` | `blocking` ou `observation` | `observation` | `blocking` exige `promotion` |
| `on_inconclusive` | `block` ou `premise` | `premise` | arquivo que não compila deixa o resultado inconclusivo |

```toml
# Repasse novo passa a reprovar a tentativa.
[checks.pass_through]
mode = "blocking"
promotion = { date = 2030-01-31, reason = "os repasses novos eram sempre removidos na revisão" }
```

### Ao falhar

O achado diz `function <nome> only passes its arguments on` e aponta para o arquivo e a função. A correção é chamar a função de destino direto ou dar à nova função uma razão de existir.

## `module_depth` (gate 8)

### O que mede

Um módulo bom esconde mais do que mostra. A profundidade é a razão entre a superfície pública do arquivo e o tamanho da implementação:

```text
razão = superfície / instruções
```

- Superfície: um ponto por nome público, mais um por parâmetro. Uma classe pública soma um ponto, mais os pontos dos métodos públicos dela.
- Instruções: o número de instruções do arquivo, sem contar `import`.
- Nome público: em Python, o que não começa com `_` (ou o que `__all__` lista, se existir); em Go, o que começa com maiúscula; em Java e C#, o que é `public`; em JavaScript e TypeScript, o que é exportado.

Razão acima do limite quer dizer módulo raso: a interface custa ao leitor quase tanto quanto o código atrás dela.

### Quando roda

Em toda tentativa que alterou arquivo de produção, nas seis linguagens. Um arquivo reprova só se as três condições valem:

1. tem pelo menos `min_statements` instruções (módulo pequeno é deixado em paz);
2. a razão dele passa de `max_interface_ratio`;
3. ele é novo, ou a razão dele ficou maior que antes.

### Chaves

| Chave | Tipo | Padrão | Efeito |
| --- | --- | --- | --- |
| `mode` | `blocking` ou `observation` | `observation` | `blocking` exige `promotion` |
| `on_inconclusive` | `block` ou `premise` | `premise` | arquivo que não compila deixa o resultado inconclusivo |
| `thresholds.<linguagem>.max_interface_ratio` | número maior que `0` | `1.0` | razão máxima entre superfície e instruções. Valor zero ou negativo é ignorado. |
| `thresholds.<linguagem>.min_statements` | número maior que `0` | `10` | módulo com menos instruções que isso é deixado em paz. Valor zero ou negativo é ignorado. |

Com os padrões, um módulo de 20 instruções e 25 pontos de superfície (razão 1,25) é raso. Com 40 instruções e os mesmos 25 pontos (razão 0,63), não.

```toml
# Java é prolixo: aceita uma razão maior e só olha arquivos com 20 instruções ou mais.
[checks.module_depth.thresholds.java]
max_interface_ratio = 1.5
min_statements = 20

# Python mais exigente, olhando até módulos pequenos.
[checks.module_depth.thresholds.python]
max_interface_ratio = 0.8
min_statements = 6
```

### Ao falhar

O achado diz `module shows about as much as it hides`, com a razão medida (duas casas) e o limite, e aponta para o arquivo. Para baixar a razão, esconda o que não precisa ser público ou ponha mais lógica atrás da mesma interface.

## `duplication` (gate 8)

Detecta código duplicado novo nos arquivos de produção alterados. Chaves: `limits.min_tokens` e `limits.min_lines`. A página própria é `duplicacao.md`.

## Resumo dos usos

| Quero | Configuração |
| --- | --- |
| mutação bloqueando | `[checks.mutation]` com `mode = "blocking"` e `promotion` |
| nota de mutação diferente em uma linguagem | `[checks.mutation.thresholds.<linguagem>]` com `min_score` |
| menos processos de mutação em paralelo | `[checks.mutation.limits]` com `workers` |
| limite de complexidade por linguagem | `[checks.complexity.thresholds.<linguagem>]` com `max_ccn` |
| complexidade só reportando | `[checks.complexity]` com `mode = "observation"` |
| CRAP bloqueando | `[checks.crap]` com `mode = "blocking"` e `promotion` |
| outro limite de CRAP | `[checks.crap.thresholds.<linguagem>]` com `max_crap` |
| repasse bloqueando | `[checks.pass_through]` com `mode = "blocking"` e `promotion` |
| outra tolerância a módulo raso | `[checks.module_depth.thresholds.<linguagem>]` com `max_interface_ratio` e `min_statements` |

Antes de pedir confirmação, a Forja resume a execução e lista cada modo, limite e promoção que o `forja.toml` mudou em relação aos padrões (veja `..\forja-toml.md`). Para conferir se as ferramentas estão presentes (`lizard`, cobertura de cada linguagem), use `forja doctor`; para instalar o que falta, `forja instalar` (veja `..\comandos.md`).
