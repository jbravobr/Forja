# Regras do trabalho na Forja

Mandam sobre qualquer plano ou nota anterior.

A construção da Forja terminou e ela caminha para a V1. O trabalho agora são as melhorias da avaliação técnica de 11/11/2026, feita pelo Fable 5.2 e guardada em [AVALIACAO.md](AVALIACAO.md) (P0 a P4, itens 1 a 40). O [DIARIO.md](DIARIO.md) conduz esse trabalho: registra o que foi feito, o que foi verificado, as conclusões e as decisões do dono. Não há mais documento de Especificação.

1. **Ler o diário antes de começar.** O andamento de cada item e as decisões já tomadas estão no `DIARIO.md`; o item em si está na `AVALIACAO.md`. Cada execução de trabalho termina com a sua entrada no diário.
2. **Referências do dono são seguidas como estão.** Repositórios e bibliografia que o dono indicou não são reinterpretados, não servem de "inspiração" nem são reescritos. Para mutação, por decisão do dono em 16/11/2026: a verdade é o que o Google aplica e descreve em [State of Mutation Testing at Google](https://storage.googleapis.com/gweb-research2023-media/pubtools/4203.pdf) e em [Mutation Testing](https://testing.googleblog.com/2021/04/mutation-testing.html), e a execução é da ferramenta especializada de cada linguagem. O `unclebob/mutator` deixou de ser a referência.
3. **Ler a referência por inteiro antes de implementar.** Nada de construir a partir de leitura parcial.
4. **Não inventar escopo.** Sem calibração extra, validação extra, leitores, formatos ou linguagens que ninguém pediu. Fazer só o que o item da avaliação e o dono pediram.
5. **Não inventar perguntas.** Perguntar só quando a avaliação, o diário e as referências não respondem. Caso contrário, decidir e seguir.
6. **Sem rodeios.** Sem retomar o que já foi decidido, sem listar alternativas que não serão feitas. Menos sessões, menos custo.
7. **Os pilares da Forja valem igual para todas as linguagens, frameworks e sistemas operacionais do requisito básico.** Processo, qualidade, assertividade e usabilidade não mudam de uma linguagem para outra. Cada linguagem, framework e sistema tem particularidades, e é a Forja que as absorve: nenhuma melhoria de gate, de desempenho ou de comportamento é proposta ou entregue só para uma linguagem, nem "por enquanto". Se uma delas não comporta a técnica, o trabalho é achar o equivalente nela antes de entregar para qualquer outra.
