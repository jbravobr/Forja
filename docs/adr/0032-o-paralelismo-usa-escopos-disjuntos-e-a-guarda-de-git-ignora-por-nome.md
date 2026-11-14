# 0032. Paralelismo por escopos disjuntos, com guarda de git por nome

## Contexto

A Especificação pede paralelismo só entre tarefas de escopos disjuntos, decidido de forma determinística, com a versão inicial sequencial (linhas 505 e 542). Até o endurecimento, `orchestrator\phases.py::_execute` percorria as tarefas em série e só a ordem do log decidia quem vinha antes.

Rodar tarefas juntas exigia três coisas. Primeira, decidir quem pode começar sem depender da ordem de término. Segunda, serializar a integração, porque o merge, o gate de integração e o rollback mexem na worktree e no ramo da execução. Terceira, lidar com a guarda de git (ADR 0008): enquanto um agente roda, a guarda reprova qualquer mudança que não seja do agente. Com outra tarefa em curso, o ramo e as worktrees dela mudam sem que o agente tenha feito nada, e a guarda reprovaria tudo.

## Decisão

- **Disjunção pura.** Duas tarefas são disjuntas quando nenhuma entrada de escopo de produção de uma é igual à da outra nem está sob um diretório dela, com a normalização de `checks\scope.py` (`\` como `/`, sem diferença de maiúsculas, espaço e ponto no fim das partes ignorados). O diretório de testes fica fora. Função em `orchestrator\schedule.py::disjoint`.
- **Quem começa depende só do log e dos escopos.** `schedule.startable` percorre as tarefas na ordem do log: dentro do limite, só tarefa cujo escopo é disjunto de cada tarefa aberta anterior, e só com as dependências de `depends_on` integradas. Com limite 1, a regra é a de antes e `depends_on` não é lido.
- **Limite por configuração.** `paralelismo` no `execucao.toml`, padrão 1, mínimo 1. Sem opção de linha de comando. O valor vai para o log e a retomada o relê.
- **Integração serializada.** Merge, gate de integração e rollback rodam sob um `Lock`.
- **Parada.** `StopRun` ou erro de uma tarefa cancela as outras; a execução pausa, e a retomada repõe as que estavam em andamento a partir do log.
- **Guarda de git por nome.** Com mais de uma tarefa, `orchestrator\workspace_git.py::others_work` ignora o trabalho da Forja nas outras tarefas da execução, identificado pelo nome: os ramos `forja/<execução>/<tarefa>` das outras tarefas, o ramo da execução e as worktrees do layout, exceto a da tentativa. O ramo e a worktree da própria tarefa, refs de fora da execução, a configuração e os hooks seguem vigiados. Com limite 1, a guarda é a de antes.

## Alternativas

- **Dependências produzidas pelo planejador.** A Forja já lê `depends_on` do esquema, mas o planejador não o preenche. Gerar dependências faria o planejador decidir ordem entre tarefas, o que não foi feito nesta fase. A ordem fica com a disjunção e com o log.
- **Trabalho dentro da tarefa.** Paralelizar etapas dentro de uma tarefa, em vez de tarefas inteiras. Fora de escopo: a Especificação fala de tarefas.
- **Tríades exatas desde já.** A guarda conferiria `(ref, antes, depois)` que a própria Forja registra em cada operação. É a correção certa, mas muda todas as operações do workspace. Não foi feita agora; fica registrada como pendência, e `paralelismo` acima de 1 não é recomendado até ela existir.
- **Escopos que se tocam, com integração resolvendo.** Fora de escopo pela Especificação. Conflito em arquivo comum fica para a integração.

## Consequências

- Sem configuração, nada muda: a execução segue sequencial.
- **Limite de segurança.** Com `paralelismo` acima de 1, um agente que mexa no ramo da execução, no ramo de outra tarefa ou na worktree de outra tarefa não é visto pela guarda. A guarda ignora esse trabalho por nome, não por mudança exata. Esse é o risco aceito por esta decisão até a correção (tríades exatas). Por isso o uso acima de 1 não é recomendado antes dela.
- **Conflito de merge.** `IntegrationConflict` é levantado quando o merge falha; `TaskCycle._integrate_alone` o captura e a tarefa fica `BLOCKED` com `reason` `integration_conflict`, a worktree da execução limpa (o `merge --abort` é do `GitWorkspace`) e as outras tarefas seguindo. Corrigido no item 9 da fila: antes disso a exceção não era capturada e a execução terminava com erro. Com o paralelismo, o arquivo de teste comum a duas tarefas disjuntas torna o conflito mais provável.
- **Tela.** Mostra a primeira tarefa em andamento e declara as outras com `+N em andamento`.
- **Testes.** `tests\orchestrator\test_schedule.py`, `test_together.py`, `test_together_git.py` e `test_together_resume.py`.

O uso e o limite estão em `docs/guia/paralelismo.md`.
