# 0024. O usuário fornece as regras do Semgrep; sem elas o SAST não executa

## Contexto

A Especificação lista o Semgrep como SAST do gate 10, com "regras para as seis linguagens". Isso
sugere que a Forja traria as regras. Distribuí-las significaria escrever e manter um conjunto
próprio, ou fixar uma cópia de regras públicas com licença a conferir, e ainda assim um padrão
nosso poderia dar ao usuário uma falsa sensação de cobertura. A regra geral dos linters já diz que
a configuração do repositório prevalece sobre qualquer perfil padrão.

## Decisão

- **A Forja não tem configuração padrão do Semgrep.** O usuário fornece um arquivo ou diretório de
  regras locais, por `--semgrep-config`, pelo `execucao.toml` ou, na falta dos dois, `.semgrep.yml`
  ou `.semgrep/` na raiz do commit base.
- **Sem configuração o check `sast` não executa.** O resultado é `NotApplicable`, nunca
  `Approved`. O resumo da preparação e o da entrega dizem em destaque "SAST não executado: sem
  configuração". O `--help` e a documentação dizem que o usuário deve fornecer a configuração.
- **Só regras locais.** Referências de registro (`auto`, `p/...`, URL) são recusadas como erro de
  configuração; a Forja sempre passa `--metrics=off --disable-version-check`.
- **As regras não vêm da worktree da tentativa.** São lidas do commit base ou de uma cópia feita
  na preparação, fora da worktree, e alterar o arquivo de regras numa tentativa é alteração
  protegida pelo gate de escopo. Assim o agente não silencia achados editando as regras.
- **Mesmo princípio para licenças.** A lista de licenças proibidas é da política e não tem padrão;
  sem ela, a checagem de licenças não executa e o resumo diz isso. As vulnerabilidades do
  OSV-Scanner não dependem de configuração e rodam sempre.
- **Emenda à Especificação:** a linha de SAST da seção "O que o agente produz" deixa de prometer
  regras para as seis linguagens.

## Consequências

Não há conjunto de regras a manter nem licença de regras a conferir, e o Semgrep serve a qualquer
política de segurança que o usuário tenha. O custo é de adoção: quem não fornece regras fica sem
SAST, e a Forja só consegue deixar isso visível, não impedir. A documentação deve apontar para a
documentação do Semgrep e trazer um exemplo mínimo que a Forja nunca usa sozinha.
