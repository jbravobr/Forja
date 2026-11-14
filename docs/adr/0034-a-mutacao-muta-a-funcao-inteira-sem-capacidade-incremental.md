# 0034. A mutação cobre a função inteira das funções tocadas, sem a capacidade incremental

## Contexto

O gate de mutação mutava só as linhas que a tentativa adicionou. A Especificação põe a mutação "restrita ao código tocado" entre as mitigações do risco de lentidão (tabela de riscos, linha 247). O plano do endurecimento (item 5) pediu que os adaptadores declarassem `MUTATION_INCREMENTAL` onde o mutator já permite mutação restrita a arquivos e linhas mudadas.

Dois fatos mudaram a forma dessa decisão. O modo diferencial do `unclebob/mutator` reexecuta todo sítio de uma função cujo texto mudou, e não só as linhas do diff. E o contrato de conformidade (`tests\core\adapters\conformance.py`) define a capacidade por um comando: um adaptador que a declara precisa planejar um comando de mutação incremental, e nenhum adaptador planeja comando de mutação. A mutação é do mutator da Forja (ADR 0021), não de cada linguagem.

## Decisão

- **Mutar a função inteira.** Para cada função que contém uma linha adicionada pela tentativa, o gate monta mutantes para todos os sítios da função (`checks\mutation.py::touched_functions` e `build_mutants(whole_functions=True)`). Os trechos vêm de `mutation\sites.py::function_spans`.
- **Regra de reprovação inalterada para linha nova.** Linha adicionada sem teste continua reprovando. Linha antiga sem cobertura, numa função alterada, não reprova: o mutante não roda e fica fora da nota.
- **Sem snapshot entre tentativas.** Cada tentativa vale por si, com as funções que ela tocou. O mutator guarda estado num snapshot; a Forja não tem onde guardar um e não cobra sobrevivente antigo de função que a tentativa não tocou.
- **Nenhum adaptador declara `MUTATION_INCREMENTAL`.** A constante e o campo `OperationRequest.incremental` continuam no núcleo sem consumidor no gate. Declarar a capacidade exigiria um comando de mutação por adaptador que não existe.

## Alternativas

- **Declarar `MUTATION_INCREMENTAL` e planejar um comando por adaptador.** Pela conformidade, o adaptador teria de planejar um comando de mutação incremental. Isso faria cada linguagem rodar uma ferramenta de mutação própria, o que contraria a decisão de usar o mutator da Forja (ADR 0021), e exigiria inventar o comando de cada uma. Rejeitado.
- **Manter só as linhas adicionadas.** É o comportamento anterior. Não cobre o que o mutator cobre no modo diferencial: uma linha antiga, na mesma função, cuja vizinhança mudou, pode deixar de ser protegida por um teste. Rejeitado.
- **Snapshot dos sobreviventes entre tentativas.** Permitiria cobrar só o que é novo, mas exige um lugar para guardar o estado e um critério para a dívida antiga. A Forja não tem esse lugar hoje, e cobrar sobrevivente antigo de função que a tentativa não tocou iria contra a regra de catraca, não de faxina. Rejeitado.

## Consequências

- **Custo maior.** Uma tentativa roda todos os sítios das funções tocadas. Em funções grandes, são muitos mutantes a mais.
- **Sobrevivente antigo na função alterada entra na nota.** Uma linha antiga coberta, que sobrevive, conta como qualquer mutante executado e pode reprovar pelo `min_score`.
- **Função aninhada e a que a envolve mudam juntas.** A função de fora tem o trecho da de dentro.
- **Sobrecargas de mesmo nome qualificado mudam juntas.** O conjunto é escolhido pelo nome.
- **A capacidade fica sem uso.** `MUTATION_INCREMENTAL` e `OperationRequest.incremental` continuam como estão. Se um dia um mutator oferecer comando incremental real, a declaração volta a fazer sentido, e a conformidade passa a exigir o comando.
- **Testes.** `tests\checks\test_mutation.py` cobre a mutação de função inteira.

O uso está em `docs/guia/gates/mutacao.md`.

## Revisão de 13 e 14/11/2026

- **O gate continua sem snapshot.** Cada tentativa roda todos os mutantes das funções que tocou.
- **`scripts/mutate.py` ganhou o modo diferencial do mutator,** com o histórico em `.metrics/mutate/<arquivo>.json`. Vale só para a mutação da própria Forja, fora do gate. Os detalhes estão na revisão do [ADR 0021](0021-a-mutacao-segue-o-unclebob-mutator.md).
- **`MUTATION_INCREMENTAL` continua sem uso.** O Maven sem `clean` por mutante não passa por ela: o pedido ao adaptador ganhou o aviso `rerun`, que um adaptador pode ignorar. O contrato da capacidade é planejar um comando de mutação, e nenhum adaptador planeja.
