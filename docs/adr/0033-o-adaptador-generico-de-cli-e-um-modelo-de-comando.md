# 0033. Adaptador genérico de CLI: um modelo de comando, sem código

## Contexto

Cada CLI de agente tinha um adaptador próprio, registrado em `executors\__init__.py::KNOWN_CLIS`. Acrescentar uma CLI exigia um módulo Python e uma linha no registro. O plano do endurecimento (item 4) pediu que uma CLI possa ser descrita pelo usuário, sem código, com comando, pacote, modelo e diretório configurados.

O contrato mínimo já existia: `core\adapters\executor.py::TemplateExecutorAdapter` monta o comando a partir de um modelo com marcadores e recebe o pacote por arquivo ou por entrada padrão. Faltava ligá-lo à configuração do usuário e ao `execucao.toml`, e registrar o modelo no log para que a retomada não dependa do perfil.

## Decisão

- **Descrição por dados.** Uma tabela `[clis.<nome>]` com `comando`, `pacote_por_stdin`, `executaveis`, `versao` e `ambiente`. Marcadores `{executable}`, `{package}`, `{cwd}` e `{role}`; outro marcador é recusado. Um modelo sem `{package}` e sem entrada padrão também é recusado.
- **Onde declarar.** No perfil do usuário (`config.toml`) e no `execucao.toml`. Se o nome aparece nos dois, vale o do `execucao.toml`, inteiro.
- **Nome de CLI conhecida é recusado.** `clis.<nome>` que usa o nome de uma CLI de `KNOWN_CLIS` é recusado, para que a descrição não substitua em silêncio o adaptador que lê os eventos e as permissões dela.
- **O modelo vai para o log.** `ExecutorRef.template` grava a descrição usada. A retomada usa o modelo do log. O `execucao.toml` gravado leva só os modelos que a execução usa, para que `--config` a repita.
- **Limites declarados.** Sem permissão por papel (`enforces_permissions` falso; o resumo registra a limitação). Prova de vida que só exige `probe.txt` com `forja-ok`. Saída lida como texto, sem eventos nem consumo. `--config` só com arquivo em que se confia, porque o `comando` pode ser qualquer programa.

## Alternativas

- **Um adaptador por CLI nova.** É o caminho que existe: um módulo e uma linha em `KNOWN_CLIS`. Continua valendo para CLIs que precisam de eventos e permissões próprios. Não serve como caminho do usuário, porque exige uma alteração no código da Forja.
- **Plugin.** Carregar um módulo Python de fora da Forja. Rejeitado: executaria código que a Forja não conhece dentro do mesmo processo, com as permissões da execução, e o usuário ainda teria que escrever um adaptador. A descrição por comando executa só o programa que o usuário nomeia, como argumento.
- **Permissões por papel no modelo.** Um campo para declarar o que cada papel pode fazer. Não há como a Forja impor isso a uma CLI que não oferece controle de permissão. Declarar sem impor seria pior que não declarar, então o modelo não tem o campo; o resumo registra a limitação.

## Consequências

- Uma CLI nova pode ser usada sem código, com as limitações listadas acima.
- **Sem permissão por papel.** Os gates continuam lendo o diff da tentativa, mas a Forja não restringe o que a CLI faz em cada papel.
- **`--config` executa o que o arquivo manda.** Por isso o arquivo precisa ser de confiança do dono.
- **Erro sem mensagem própria.** Um modelo inválido levanta `ValueError` ao montar o adaptador. Não encontrei tratamento que o transforme em mensagem da Forja.
- **Retomada independente do perfil.** O modelo gravado no log é o que a retomada usa.
- **Testes.** `tests\cli\test_generic_cli.py`, além dos de `tests\core\adapters\` e `tests\executors\`.

O uso está em `docs/guia/clis.md`.
