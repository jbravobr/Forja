# 0021. A mutação das seis linguagens segue o unclebob/mutator

## Contexto

A ADR 0020 manteve um motor próprio para Python e deixou PIT, gremlins e Stryker para as outras
linguagens. Isso criou um motor só de Python, leitores de três formatos de relatório que nenhuma
linguagem usava ainda e um passo de calibração contra ferramentas de referência. O dono definiu que
a referência é o `unclebob/mutator`, que já cobre Java, Go, TypeScript/JavaScript e Python com uma só
tabela de regras sobre tree-sitter, e que as referências indicadas são seguidas como estão
(`CLAUDE.md`).

## Decisão

- Os sítios de mutação vêm das regras do mutator, portadas para `src/forja/mutation/sites.py` sobre
  tree-sitter. Medido contra o `_visit` original sobre `src` e `tests` da Forja: nenhuma diferença.
- C# não existe no mutator; segue o conjunto de Java, com o nó unário do C# acrescentado.
- Cada mutante roda o comando de testes inteiro. O mutante morre quando os testes falham ou estouram
  o tempo (10 vezes o baseline, no mínimo 2 s). Linha sem cobertura não é mutada.
- A nota é mortos / (mortos + sobreviventes); linha sem cobertura fica fora.
- Os leitores de PIT, gremlins e Stryker foram removidos. A Especificação não nomeia mais
  ferramenta de mutação por linguagem.
- Fica de fora o que o mutator faz por causa do Unix: links simbólicos e `killpg`. A Forja copia a
  árvore para cada worker.

## Consequências

A ADR 0020 está substituída. Não há passo de calibração contra ferramenta de referência para a
mutação: a referência é o próprio mutator. O gate de mutação aprova quando nenhuma linha nova está
coberta; exigir cobertura mínima é assunto de outro check.

## Revisão de 13 e 14/11/2026

A mutação foi comparada com o `unclebob/mutator` no commit `830ec7e` e realinhada. O que mudou em
relação ao texto acima:

- **Tempo esgotado.** Com mais de um worker, o mutante que estourou o tempo é julgado de novo,
  sozinho, antes de contar como morto. O baseline é medido com um processo só.
- **Linha coberta.** O sítio conta como coberto pela própria linha ou pela linha onde o comando
  começa (`anchor_line`), como no mutator.
- **Tipos de TypeScript.** Literal dentro de um tipo não é sítio (`_in_type`), como no mutator.
- **Maven.** O mutante roda `mvn test`, sem `clean`, como no mutator. O gate de testes continua
  com `clean`.
- **Arquivo que as regras não leem.** A medição fica inconclusiva (`unsupported_construct`). Antes
  o arquivo era pulado.
- **Cópia do worker.** Continua sendo cópia. Ficam de fora, na raiz, `target`, `coverage`,
  `.metrics`, `.forja-coverage` e `.forja-cover.out`, e os caches em qualquer nível. `dist`,
  `build` e `out` ficam, porque o comando de testes pode rodar o que foi construído ali. Nunca há
  mais workers que mutantes a rodar.
- **Overlay de links: não adotado, por decisão do dono em 14/11/2026.** O ganho medido sobre a
  cópia enxuta é de 0,65 s por worker. Num worker de links, uma ferramenta que reescreve um
  arquivo existente altera a árvore que o gate está julgando.

O que a Forja mantém de diferente do mutator, por decisão do dono:

- A função dona do sítio é qualquer função. No mutator ela vem do `unclebob/crapper`, que só
  aceita função de topo, método de classe, `const f = () =>` de topo e callback de rota.
- A reconferência do mutante que estourou o tempo, o canário, o arquivo de supressões e C#.
- Um worker por núcleo, só o pacote do arquivo em Go e a base uma vez por comando não foram
  adotados.

`scripts/mutate.py`, que roda a mutação sobre a própria Forja, tem o que o gate não tem:

- `--max-workers`, com o nome do mutator.
- Escolha dos testes pela linha mutada, com reconferência do sobrevivente pelo conjunto inteiro.
- Modo diferencial, com o histórico em `.metrics/mutate/<arquivo>.json`. O formato é JSON por
  arquivo-fonte, não o EDN por namespace do mutator: decisão do dono.
- O veredito aproveitado depende também dos arquivos de teste que mataram o mutante. O mutator
  só olha o texto da função. A divergência só acrescenta rodadas, e o dono a aceitou.

O CRAP não segue o `crapper`, por decisão do dono em 14/11/2026: a complexidade continua vindo do
`lizard` e a cobertura, da fração de linhas.
