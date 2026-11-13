# Comandos

Esta página descreve todo comando e todo argumento de `forja`. Cada um vem com um exemplo comentado. Para rodar sem perguntas, a partir de um arquivo, veja `execucao-toml.md`. Para escolher a CLI de agente de cada papel, veja `clis.md`.

## Visão geral

```bash
forja                      # sem verbo: a Forja pergunta o que fazer
forja <verbo> [argumentos] # com verbo: roda esse objetivo
```

| Comando | O que faz |
| --- | --- |
| `forja refatorar` | melhora o código sem mudar o comportamento |
| `forja corrigir` | corrige um erro que você descreve |
| `forja criar` | cria um projeto, num diretório vazio, a partir de uma ideia |
| `forja investigar` | responde uma pergunta sobre o código e escreve a documentação |
| `forja doctor` | verifica se o ambiente está pronto |
| `forja instalar` | instala as ferramentas externas que faltam, na pasta da Forja |
| `forja revisao` | diz o que de uma execução chegou à branch principal |
| `forja agente` | fachada usada pelo agente; não aparece na ajuda |

Rode a Forja de dentro do repositório. Ela nunca envia nada para fora e nunca altera a sua branch: a entrega é a branch `forja-run/<id>`, em uma worktree própria.

![Ajuda da Forja no terminal](imagens/terminal-ajuda.png)

Ajuda geral no terminal real, com 170 colunas (`forja --help`).

## Opções comuns aos quatro objetivos

Valem para `refatorar`, `corrigir`, `criar` e `investigar`. Podem vir antes ou depois do verbo.

| Opção | Valor | Padrão | O que faz |
| --- | --- | --- | --- |
| `--escopo CAMINHO` | caminho; repetível | o repositório inteiro | limita o que a execução pode mudar |
| `--cli NOME` | `claude-code`, `codex`, `cursor` ou `grok` | `claude-code` | CLI de agente que escreve o código |
| `--cli-testes NOME` | mesma lista | a de `--cli` | CLI de agente que escreve os testes |
| `--modelo NOME` | texto | o configurado na CLI | modelo do agente de código |
| `--modelo-testes NOME` | texto | o configurado na CLI | modelo do agente de testes |
| `--plano` | sem valor | desligado | mostra o plano e sai com código 0, sem executar |
| `--semgrep-config CAMINHO` | arquivo ou pasta | `.semgrep.yml` ou `.semgrep/` do repositório | regras do Semgrep que você fornece |
| `--fatos ARQUIVO` | caminho | nenhum | arquivo de fatos (veja "Fatos") |
| `--config ARQUIVO` | caminho | nenhum | roda sem perguntas com um `execucao.toml` |

Exemplos:

```bash
# Só o que está em src/pagamento e src/frete pode mudar.
forja refatorar --escopo src/pagamento --escopo src/frete

# Mostra o plano da execução e sai. Nada é escrito no repositório.
forja refatorar --plano

# Escolhe a CLI e o modelo do agente de código.
forja refatorar --cli codex --modelo nome-do-modelo

# Código escrito por uma CLI e testes escritos por outra.
forja corrigir "o frete grátis aparece para pedidos de R$ 99" --cli claude-code --cli-testes codex

# Mesma CLI, outro modelo para os testes.
forja corrigir "o frete grátis aparece para pedidos de R$ 99" --modelo-testes outro-modelo
```

Detalhes que valem para cada opção:

- `--escopo`: o caminho é relativo ao repositório. Repita a opção para mais de um caminho.
- `--cli-testes` e `--modelo-testes`: só têm efeito em `corrigir` e em `criar`, que são os objetivos com autor de testes. Se `--cli-testes` for igual a `--cli`, ou se faltar, a mesma CLI escreve código e testes. Se você der só `--modelo-testes`, a mesma CLI roda com outro modelo para os testes. Para um autor de testes de outra CLI, veja `clis.md`.
- `--modelo` e `--modelo-testes`: viram `--model <valor>` na linha de comando da CLI. Vazio significa o modelo que a própria CLI tem configurado.
- `--semgrep-config`: a Forja não traz regras. Sem esta opção e sem `.semgrep.yml` ou `.semgrep/` no commit base, a análise de segurança (SAST) não roda e a execução mostra um aviso. Referência de registro (`auto`, `p/...`, `r/...`, `s/...`, `semgrep.dev/...`, `://`) é recusada. Veja `gates\seguranca-e-dependencias.md`.
- `--config`: veja "Rodar sem perguntas".

## Descrição da tarefa

Os quatro objetivos aceitam a descrição de dois modos.

| Argumento | Valor | O que faz |
| --- | --- | --- |
| `texto` (posicional, opcional) | texto | a descrição: o defeito, a pergunta ou a ideia |
| `-f ARQUIVO`, `--arquivo ARQUIVO` | caminho de arquivo UTF-8 | lê a descrição de um arquivo |

```bash
forja corrigir "o total do carrinho ignora o desconto"
forja corrigir -f defeito.txt
```

Sem `texto` nem `-f`, a Forja pergunta. Sem terminal, e sem a descrição no `execucao.toml`, ela recusa e diz o que faltou.

## `forja refatorar`

Melhora a estrutura do código sem mudar o comportamento. Não precisa de descrição.

A Forja escolhe as funções acima do limite de complexidade. A ordem é a da complexidade vezes o número de commits que mudaram o arquivo, do maior para o menor: código complexo que muda sempre vem antes de código complexo em que ninguém mexe.

Antes da primeira tarefa, a Forja mede os testes de cada função escolhida, com a cobertura e a mutação do gate 7. Quando uma linha da função não tem teste, ou a nota de mutação fica abaixo do limite (`min_score`), a tarefa começa por uma etapa a mais: o agente escreve testes do que o código faz hoje, que precisam passar sem nenhuma mudança no código. Esses testes ficam congelados, e a refatoração vem depois. Função que não pode ser medida (linguagem sem cobertura, testes que não rodam) não ganha essa etapa.

```bash
forja refatorar                         # a Forja pergunta o que ela precisa
forja refatorar --escopo src/inv        # só a pasta src/inv
```

## `forja corrigir`

Corrige um erro que você descreve. O fluxo é: especificador (se não houver critérios), autor de testes, implementador, documentador.

| Argumento | Valor | O que faz |
| --- | --- | --- |
| `--criterios ARQUIVO` | JSON | critérios de aceite do conserto; com ele, o especificador não roda |

```bash
# Sem critérios: um agente os escreve a partir da descrição.
forja corrigir "pedido cancelado continua somando no relatório"

# Com critérios seus.
forja corrigir "pedido cancelado continua somando no relatório" --criterios criterios.json
```

Sem `--criterios`, a Forja procura `criterios.json` ao lado do arquivo dado em `-f`. Se não achar, um agente escreve os critérios.

![Plano de um conserto no terminal](imagens/terminal-corrigir-plano.png)

Plano de um conserto, sem executar (`forja corrigir "soma(2, 3) devolve -1 em vez de 5" --plano`).

O especificador corta a descrição em elementos com id estável (`E1`, `E2`, ...): cada item de lista e cada parágrafo é um elemento; um título sozinho não conta. Cada critério aponta de quais elementos veio, e o resumo mostra a cadeia elemento, critério, teste na seção "Rastreabilidade".

Formato do arquivo de critérios:

```json
{
  "criteria": [
    {
      "id": 1,
      "text": "um pedido cancelado não entra na soma do relatório",
      "level": "unit",
      "origin": ["E1"]
    }
  ]
}
```

| Campo | Regra |
| --- | --- |
| `criteria` | lista com pelo menos um item; ids únicos |
| `id` | inteiro, 1 ou mais |
| `text` | texto não vazio |
| `level` | `unit`, `integration` ou `e2e` |
| `origin` | lista não vazia de ids dos elementos da descrição; um texto único vira lista de um |

A origem que você escreve não é conferida pela Forja.

## `forja investigar`

Responde uma pergunta sobre o código e escreve a documentação em `docs/`. O fluxo é: arqueólogo, depois documentador. Nenhum código é alterado.

```bash
forja investigar "como o pedido chega ao estoque?"
forja investigar -f pergunta.txt
```

O arqueólogo responde com afirmações (`claims`), cada uma com citação, e com premissas (`assumptions`). A Forja classifica cada afirmação:

| Rótulo | Quando |
| --- | --- |
| provado | a citação confere por máquina: o arquivo e as linhas existem, ou a evidência foi medida |
| confirmado | cita um fato que você registrou |
| premissa | o agente declarou como suposição; entra no resumo com um nome estável (`p` mais 8 caracteres) |

O documentador recebe as três listas e não transforma premissa em afirmação. O registro de mudança sai sem código, porque a tarefa não alterou código.

## `forja criar`

Cria um projeto a partir de uma ideia. Roda num diretório vazio (no máximo com `.git`) e recusa um diretório que não esteja vazio.

```bash
mkdir conversor && cd conversor
forja criar "um conversor de moedas de linha de comando"
```

A Forja pergunta a linguagem e o nome do projeto. O nome sugerido é o do diretório e deve ser palavras em minúsculas e dígitos ligadas por hífen, como `meu-projeto`, com até 40 caracteres. Sem terminal, a linguagem vem de `linguagem` e o nome de `nome` no `execucao.toml`; veja `execucao-toml.md`.

Linguagens com modelo: Python, Go, TypeScript, C# e Java. JavaScript não tem modelo.

| Linguagem | O que o modelo escreve |
| --- | --- |
| Python | `pyproject.toml`, `src/<pacote>` e `tests/` com um teste que passa |
| Go | `go.mod` e um `*_test.go` |
| TypeScript | `package.json`, `tsconfig.json` e vitest; roda `npm install` |
| C# | o `.sln`, o projeto e o projeto de teste |
| Java | `pom.xml`, `src/main/java` e `src/test/java` |

O andaime roda depois da sua confirmação: escreve os arquivos, faz `git init` se faltar, faz o commit `chore: scaffold <linguagem> project <nome>` (autor `Forja (andaime)`) e roda o comando de preparação do modelo, com limite de 900 segundos. Depois disso o repositório é existente e o fluxo é o de funcionalidade: especificador, autor de testes, implementador e documentador. Build e testes verdes do modelo são a linha de base da execução.

Limites: `npm install` e o download de JUnit e de pacotes NuGet precisam de rede. Se o comando de preparação falhar, o projeto fica criado e comitado, e a mensagem diz isso.

## `forja doctor`

Verifica se o ambiente está pronto. Só lê; não altera nada.

```bash
forja doctor
forja doctor --cli codex                              # prova a CLI codex em vez da padrão
forja doctor --semgrep-config regras/                 # confere também o Semgrep
```

Cada item sai numa linha, na forma `[ok ] nome: detalhe` ou `[FALTA] nome: detalhe`. Quando há correção, uma segunda linha com `->` diz o que fazer. Exemplo real:

```text
[ok ] git: git no PATH
[ok ] ruff: lint do código do projeto (vem com a Forja)
[ok ] lizard: complexidade ciclomática (vem com a Forja)
[ok ] gitleaks: gitleaks no PATH
[ok ] jscpd: jscpd instalado pela Forja (C:\Users\voce\AppData\Local\forja\tools\jscpd\5.4.2\jscpd.exe)
[ok ] disco: 361.2 GiB livres
[FALTA] repositório limpo: 2 arquivo(s) alterado(s)
        -> faça commit ou stash antes de rodar a Forja
[ok ] pytest no repositório: C:\...\.venv\Scripts\python.exe importa pytest
[ok ] mypy: mypy no PATH
[ok ] CLI claude-code: C:\Users\voce\.local\bin\claude.exe (2.1.319) responde a uma tarefa de teste

A Forja roda neste terminal e não cria sandbox: leitura fora da cópia e rede são garantidas pelo seu ambiente.

1 item(ns) a resolver.
```

![Verificação do ambiente no terminal](imagens/terminal-doctor.png)

Verificação do ambiente num repositório limpo (`forja doctor`).

Os itens variam com o repositório. Para cada linguagem detectada entram as ferramentas dela: `go` e `golangci-lint`, `node`, `dotnet`, `java` com `mvn` ou `gradle`. O `semgrep` só entra com `--semgrep-config` ou com `.semgrep.yml` ou `.semgrep/` no repositório. Itens opcionais (`mypy`, `golangci-lint` e as CLIs além da escolhida) aparecem como `[ok ]` ou `[FALTA]`, mas não contam para a saída.

Código de saída: `0` se nenhum item obrigatório falta; `1` caso contrário. A linha final é `Tudo pronto.` ou `N item(ns) a resolver.`.

Para instalar o que falta, veja `forja instalar`.

## `forja instalar`

Instala as ferramentas externas que a Forja usa e que faltam: as de uso geral e as das linguagens detectadas no diretório atual. Não tem argumentos próprios.

```bash
forja instalar
```

![Ajuda de forja instalar no terminal](imagens/terminal-instalar-ajuda.png)

Ajuda do comando de instalação (`forja instalar --help`).

- As ferramentas vão para `<dados da Forja>\tools` (no Windows, `%LOCALAPPDATA%\forja\tools`; em outras plataformas, `~/.local/share/forja/tools`). A Forja não mexe no `PATH` do seu sistema.
- Uma ferramenta já no `PATH`, ou já instalada por inteiro na pasta da Forja, não é baixada de novo.
- Com tudo no lugar, a saída é `Nada a instalar: as ferramentas da Forja já estão no lugar.` e o código é `0`.
- Uma falha numa ferramenta não impede as outras. A mensagem diz `rode forja instalar de novo; se persistir, instale <x> à mão`.
- Em plataforma sem suporte, a Forja diz que não instala ferramentas e sai com código `2`.

Código de saída: `0` se nenhuma instalação falhou; `1` se alguma falhou. A lista de ferramentas e de versões está em `instalacao.md`.

## `forja revisao`

Depois do `git fetch`, diz o que de uma execução chegou à branch principal. Só lê: não inicia execução e não altera o repositório.

```bash
git fetch
forja revisao                                  # a execução mais recente do repositório
forja revisao 10084a23b4fb                      # uma execução pelo id
forja revisao --principal origin/main          # branch principal explícita
forja revisao --fatos comentarios.txt          # com os comentários do revisor
```

| Argumento | Valor | Padrão | O que faz |
| --- | --- | --- | --- |
| `execucao` (posicional) | id da execução | a mais recente do repositório | qual execução revisar |
| `--principal REF` | referência git | `origin/HEAD` | a branch principal a ler |
| `--fatos ARQUIVO` | caminho | nenhum | aqui o arquivo carrega os comentários do revisor |

O terminal recebe uma linha: `Relatório da revisão da execução <id>: <caminho>`. O relatório fica em `<dados da Forja>\revisoes\<id>.md` e traz:

- por tarefa e etapa, se chegou à principal (inteira, em parte ou não);
- commits de estilo e commits só com `Task-Id`, contados e não interpretados;
- tabelas por papel, CLI e tipo de tarefa, e por gate;
- os comentários do arquivo de fatos, ligados às premissas que confirmam ou corrigem, e as premissas sem resposta.

A coleta casa os commits da principal com os da execução por `Stage-Id` e `Task-Id`. Squash ou rebase apagam esses trailers, e então a coleta só responde sobre o que o git ainda mostra.

Erros (todos saem com código `2`):

| Situação | Mensagem |
| --- | --- |
| fora de um repositório | "Rode `forja revisao` dentro do repositório da execução." |
| nenhuma execução | "Nenhuma execução da Forja neste repositório. Rode `forja revisao` nele." |
| id desconhecido | "A execução <id> não é deste repositório ou não existe." |
| sem `origin/HEAD` | "Não sei qual é a branch principal (não há `origin/HEAD`). Diga qual: `forja revisao --principal origin/main`." |
| principal inexistente | "A branch principal `X` não existe. Faça o `git fetch` e confira o nome." |

## `forja agente`

Fachada que a Forja oferece ao agente para ele rodar testes, lint e medição dentro da cópia de trabalho. Você não precisa chamá-la; está aqui porque aparece nos pacotes do agente.

```bash
forja agente testar tests/test_frete.py
forja agente lint src
forja agente medir src/inv
```

| Subcomando | Ferramenta | Sem `CAMINHO` |
| --- | --- | --- |
| `testar` | `pytest -q -x -p no:cacheprovider`, com o Python do repositório | o pytest decide o que rodar |
| `lint` | `ruff check --no-cache` | `.` |
| `medir` | `lizard` | `.` |

O caminho deve ser relativo, existir dentro do diretório atual, não começar com `-` e não conter `..`. Senão a fachada sai com código `2` e diz "caminho não permitido" ou "caminho não encontrado dentro do projeto". Uso incorreto imprime `uso: forja agente testar|lint|medir [CAMINHO...]` e sai com `2`. A ferramenta tem limite de 600 segundos (código `124` e a mensagem "interrompido: passou de 600 s"); erro de sistema ao abrir a ferramenta sai com `127`. A saída mostra as últimas 80 linhas.

## Rodar sem perguntas

```bash
forja --config execucao.toml
```

Com `--config`, a Forja não pergunta nada: tudo vem do arquivo. Serve para CI e para repetir uma execução. O arquivo e as regras de cada objetivo estão em `execucao-toml.md`.

![Plano no modo sem perguntas](imagens/terminal-modo-ci-plano.png)

Plano lido do `execucao.toml`, sem executar (`forja --config execucao.toml --plano`).

Exigências comuns:

- o repositório precisa estar limpo;
- não pode haver execução incompleta; senão a Forja recusa com "Resolva a execução incompleta (rode `forja` sem --config) antes.";
- ferramentas que faltam só são instaladas com `instalar_ferramentas = true`.

## Execução incompleta

Se uma execução anterior parou no repositório, `forja` oferece duas saídas:

- **retomar de onde parou**: continua do último ponto salvo;
- **descartar e recomeçar**: não apaga nada; mostra os comandos para você limpar as branches.

Quando há várias instalações da CLI de agente, a Forja pergunta qual usar e oferece lembrar a escolha ("Lembrar de usar sempre <caminho> para <cli>?"); a resposta vai para o perfil (veja `ambiente.md`).

## Interromper

Durante a execução, a tecla `q` interrompe com segurança: a primeira pergunta se deve salvar, a segunda sai na hora. `ctrl+c` também para. Depois de interromper, rode `forja` de novo no repositório para retomar. As teclas da tela cheia (`d`, `l`, `t`, `?`) estão em `ambiente.md`.

## Códigos de saída

| Código | Nome | Significado |
| --- | --- | --- |
| 0 | entregue | a execução entregou |
| 1 | falhou | a execução falhou |
| 2 | configuração inválida | configuração ou política inválida, ou repositório ocupado |
| 3 | pausada | rode `forja` de novo para retomar |
| 4 | nada integrado | entregue, mas havia tarefas bloqueadas e nenhuma foi integrada |
| 5 | algumas bloqueadas | entregue, com algumas tarefas bloqueadas |
| 130 | interrompida | você interrompeu |

![Entrega concluída](imagens/entrega-concluida.svg)

Fim de uma execução entregue (`forja`, todas as tarefas integradas).

![Entrega com uma tarefa bloqueada](imagens/tarefa-bloqueada.svg)

Fim de uma execução entregue com uma tarefa bloqueada (`forja`, código de saída 5).

![Rejeição de gate](imagens/rejeicao-de-gate.svg)

Tentativa rejeitada no gate 5 (`forja`): as rejeições aparecem no painel de baixo.

![Execução em andamento no terminal](imagens/terminal-execucao-meio.png)

Execução real em andamento, no terminal, com o `claude-code` (`forja --config execucao.toml`, objetivo `corrigir`).

![Execução entregue no terminal](imagens/terminal-execucao-fim.png)

Fim da mesma execução, com a tarefa integrada (`forja --config execucao.toml`).

Mensagens de fechamento:

```text
Interrompida. Tudo foi salvo.
Rode `forja` de novo neste repositório para retomar de onde parou.

2 de 3 tarefa(s) integrada(s); 1 bloqueada(s)
Nenhuma tarefa foi integrada: a branch não tem mudanças.
Branch da entrega: forja-run/<id> (sua branch não foi alterada; nada foi enviado).
Resumo: <pasta da execução>/resumo.md
Para ver: git log --oneline <branch>
```

## Fatos

Um arquivo de texto seu, com uma linha por fato. Linhas em branco e as que começam com `#` são ignoradas.

```text
# fatos do pedido 114
f1 | maria | 2026-10-26 | o estoque é baixado pelo worker, não pela API
f2 | maria | 2026-10-26 | o prazo de 30 dias vale só para B2B | corrige:p3a9c01de
```

Campos, separados por `|`: `id`, autor, data, texto e, se o fato resolve uma premissa do resumo, `confirma:<id>` ou `corrige:<id>`.

| Campo | Regra |
| --- | --- |
| `id` | começa com letra ou dígito; letras, dígitos, `_` e `-`; até 64 caracteres; único |
| data | `AAAA-MM-DD` |
| `confirma:<id>` ou `corrige:<id>` | `<id>` é o da premissa que o resumo mostra: `p` mais 8 caracteres hexadecimais |

Uma linha malformada recusa a execução e o erro cita a linha. Aponte o arquivo com `--fatos` ou com `fatos` no `execucao.toml`. Os fatos entram no pacote de todo papel como evidência e podem ser citados; o resumo marca a premissa resolvida como "Confirmada por" ou "Corrigida por". A Forja não guarda fatos entre execuções: o arquivo é seu.

Em `forja revisao`, o mesmo formato carrega os comentários do revisor.

## Arquivo de regras do Semgrep

`--semgrep-config` (ou `semgrep_config` no `execucao.toml`) aceita um arquivo ou uma pasta de regras locais. A execução usa uma cópia dele. Nomes com barra invertida, dois pontos, `..` ou `.` são recusados. Sem esta opção, a Forja procura `.semgrep.yml` ou a pasta `.semgrep/` no commit base. Mais em `gates\seguranca-e-dependencias.md`.
