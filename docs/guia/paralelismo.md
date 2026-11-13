# Paralelismo de tarefas

Por padrão a Forja roda uma tarefa de cada vez, na ordem em que as tarefas foram planejadas. Com `paralelismo` acima de 1, ela roda várias tarefas ao mesmo tempo, desde que os escopos delas não se toquem. Esta página diz quando vale ligar, como ligar, o que a Forja faz e o que aparece no resumo e na tela. As demais chaves do arquivo estão em `execucao-toml.md`.

## Quando usar

- A execução tem várias tarefas (`tarefas` acima de 1) e elas mexem em partes diferentes do código, como `src/pedidos` e `src/frete`.
- Você aceita gastar o orçamento de tokens mais depressa, porque vários agentes trabalham juntos.

Se todas as tarefas mexem na mesma pasta, ligar o paralelismo não ajuda: as tarefas com escopo que se toca esperam a vez, uma de cada vez.

## Como ligar

A única forma é a chave `paralelismo` do `execucao.toml`. Não existe opção de linha de comando para isso, nem chave no perfil (`config.toml`).

- Tipo: inteiro. Padrão: `1`. Valores: 1 ou mais.
- Com `1` a execução é sequencial.
- O valor fica gravado no registro da execução. Ao retomar, a Forja usa o valor gravado, não o do arquivo.

```toml
# execucao.toml: até duas tarefas ao mesmo tempo
objetivo = "refatorar"
escopo = ["src"]
tarefas = 4        # quatro tarefas no total
paralelismo = 2    # no máximo duas em andamento juntas
```

```bash
# Roda sem perguntas, com o arquivo acima.
forja --config execucao.toml
```

Sem `--config`, a Forja não pergunta o paralelismo: num terminal, o valor é sempre `1`. Para usar outro valor, passe o arquivo com `--config`. Quando a execução incompleta é retomada num terminal (`forja`, sem `--config`), vale o valor gravado.

## Quando duas tarefas podem rodar juntas

Duas tarefas são disjuntas quando nenhuma entrada do escopo de uma é igual a uma entrada da outra, nem está dentro de um diretório que a outra lista. A comparação normaliza os caminhos:

- `\` vale como `/`;
- maiúsculas e minúsculas não contam;
- espaço e ponto no fim de cada parte do caminho são ignorados, como o Windows trata esses nomes;
- barra no fim é ignorada.

| Escopo A | Escopo B | Disjuntos? |
| --- | --- | --- |
| `src/app` | `src/lib` | sim |
| `src/app` | `src/app/util.py` | não: `util.py` está dentro de `src/app` |
| `Src/App` | `src/app/` | não: é o mesmo caminho depois da normalização |

A regra vale só para o escopo de produção que cada tarefa declara. A pasta de testes do autor de testes fica fora dela. Se duas tarefas mexem no mesmo arquivo de teste, o conflito aparece na integração (veja abaixo).

### Quem começa e quem espera

A Forja percorre as tarefas na ordem do plano. Uma tarefa começa quando:

- cabe no limite de `paralelismo`;
- o escopo dela é disjunto do escopo de cada tarefa ainda aberta que vem antes dela (em andamento ou esperando);
- toda tarefa de que ela depende já foi integrada. Hoje o plano não declara dependências entre tarefas, então esta condição não segura nenhuma.

Uma tarefa que espera começa quando a que a segurava termina. Tarefa integrada ou bloqueada sai da conta. Se uma tarefa é bloqueada, as que esperavam só por escopo continuam e começam normalmente.

Com `paralelismo = 1`, só a primeira tarefa aberta pode começar, como sempre foi.

## Integração

Cada tarefa trabalha na própria branch e na própria worktree. Quando termina, é integrada à branch da execução (`forja-run/<id>`) por merge.

- Merge, portão de integração e desfazer a integração rodam um de cada vez, mesmo com várias tarefas em andamento.
- A ordem de integração é a ordem em que as tarefas terminam, não a do plano. Duas execuções iguais podem integrar em ordens diferentes, e o histórico da branch muda de acordo.
- Depois do merge, o portão de integração roda sobre a branch da execução. Se ele reprova, a Forja desfaz o merge e a tarefa fica bloqueada com o motivo `integration_gate`.

### Conflito de merge

Quando duas tarefas alteram o mesmo arquivo de forma incompatível, o merge falha. A Forja desfaz o merge e:

- a tarefa que não integrou fica bloqueada, com o motivo `integration_conflict`;
- a worktree da execução volta ao estado de antes do merge, sem arquivos pela metade;
- as outras tarefas seguem, com `paralelismo` 1 ou acima.

A Forja não tenta o merge de novo. A tarefa bloqueada precisa de revisão sua e não volta sozinha numa retomada.

Para reduzir conflitos, dê escopos que não se toquem e evite que tarefas diferentes alterem os mesmos arquivos de teste.

## Orçamento de tokens

O orçamento (`tokens`) é da execução inteira. Os tokens de todas as tarefas em andamento somam no mesmo contador.

- Com o orçamento esgotado, nenhuma tarefa nova começa.
- Uma tarefa em andamento termina a tentativa em curso. Se precisar de outra tentativa, a etapa esgota com o motivo `token_budget_exhausted` e a tarefa fica bloqueada.
- Como vários agentes gastam ao mesmo tempo, o total pode passar um pouco do limite antes de a execução parar. Quanto maior o `paralelismo`, maior pode ser essa folga.

## Parada e retomada

Quando uma tarefa não pode continuar (o agente falha de modo que não dá para seguir, falhas de infraestrutura seguidas, rede que não volta, disco quase cheio) ou quando um erro escapa de uma tarefa, a Forja cancela as outras tarefas em andamento e pausa a execução.

Na retomada, rode `forja` de novo no repositório, num terminal, e escolha retomar. Com `--config` ou sem terminal a Forja recusa retomar (veja "Execução incompleta" em `execucao-toml.md`). Ao retomar:

- as tarefas integradas ou bloqueadas continuam como estavam;
- as tarefas que estavam em andamento voltam juntas, a partir do registro, respeitando o mesmo `paralelismo` gravado e as mesmas regras de escopo;
- um merge cortado no meio pela parada não deixa a branch da execução pela metade.

## Resumo, diário e tela

- O resumo e o diário listam as tarefas na ordem do plano, não na de término. No resumo, a tarefa bloqueada aparece com o motivo, por exemplo `integration_conflict`. Veja `diario-de-bordo.md` e `documentacao-gerada.md`.
- A tela mostra a primeira tarefa em andamento. Na tela de barra viva aparece `+N em andamento`. No modo simples, a linha da tarefa recebe `(+N em andamento)`.
- O contador "tarefa N/M" e o feed de atividade do agente são da tarefa que está na tela.
- Os tokens somam todas as tarefas.

## Limites

- Escopos que se tocam não rodam em paralelo.
- A Forja não reordena tarefas para aproveitar o paralelismo.
- Não há paralelismo dentro de uma tarefa, entre as etapas dela.
- Com `paralelismo` acima de 1, a conferência de git feita durante a chamada ao agente não reprova mudanças nas branches e nas worktrees das outras tarefas da execução, nem na branch da execução. Continuam vigiadas a branch e a worktree da própria tarefa, as branches de fora da execução, a configuração e os hooks do git. Com `paralelismo = 1`, qualquer mudança externa ao agente reprova.
