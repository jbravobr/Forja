# 0037. A Forja instala as ferramentas externas OSS, exceto o git

## Contexto

O ADR 0017 fixou que `forja doctor` "verifica e explica; não instala" e deixou a "instalação assistida de ferramentas" para depois. O dono decidiu em 02/11/2026 que a Forja precisa instalar as ferramentas de terceiros de que o fluxo completo depende, que todas têm de ser software livre e que o git é a única que não é responsabilidade dela. A instalação deve ser visual, clara e compreensível, e a Forja deve ter uma forma de instalação própria, para o usuário usá-la por um comando ou pela UI sem preparar o ambiente. O plano está em `docs\plano-instalacao.md` (fila 14).

## Decisão

- **A Forja instala as ferramentas externas OSS do fluxo completo, exceto o git.** As transversais (gitleaks, semgrep, osv-scanner, jscpd) quando faltarem; as de linguagem (go, golangci-lint, node, SDK dotnet, JDK, Maven ou Gradle) só para as linguagens detectadas. O `doctor` continua verificando o git e aponta onde instalá-lo.
- **Fora do que a Forja instala:** a CLI do agente e o login do usuário (a regra é só OSS e a CLI do agente não é); o que o projeto alvo declara (prettier, eslint, typescript, vitest, jest, Istanbul/c8, coverlet, JaCoCo, Checkstyle); pytest e coverage, que são do interpretador do repositório (ADR 0036).
- **Instalação isolada**, numa pasta da Forja no `<home>`, sem administrador e sem alterar o PATH do usuário; a Forja põe essa pasta na frente do PATH dos processos que lança. Ferramenta já presente em versão aceita continua valendo.
- **Versões fixadas e checksum conferido.** Um manifesto de dados lista, por ferramenta e plataforma, versão, origem oficial e checksum; sem checksum conferido a instalação não vale. Nunca `curl | sh`.
- **A confirmação única do ADR 0017 cobre a instalação.** `forja instalar` instala e é idempotente; `forja` sem argumentos mostra o que falta no resumo; em CI só instala se o `execucao.toml` pedir. A Forja não apaga nada: remover é apagar a pasta, que ela mostra.
- **A instalação é visual** nas três camadas da UI (tela cheia, linha viva, texto simples), alimentada por eventos do instalador.
- **Plataformas:** Windows e Linux. macOS suspenso pelo dono.

## Substitui

A linha do ADR 0017 "`forja doctor` verifica e explica; não instala" e a frase "Instalação assistida de ferramentas [...] ficam para depois". O restante do ADR 0017 continua valendo.

## Alternativas

- **Só apontar onde instalar (o que o `doctor` faz hoje).** Obriga o usuário a preparar o ambiente à mão. Rejeitado pelo dono.
- **Instalar no sistema, com administrador.** Contamina o ambiente do usuário e exige privilégio. Rejeitado.
- **Instalar o git também.** Decisão do dono: pré-requisito do usuário.
- **Sandbox para o instalador.** Contra o ADR 0018. Rejeitado.

## Consequências

- **Instalar executa e guarda código de terceiros**, sem sandbox, com as permissões do usuário. A defesa é a origem oficial, a versão fixada e o checksum; ferramenta cujo checksum oficial não se obtém fica fora do manifesto.
- **Exige rede** na instalação; sem rede, falha com mensagem clara e sem instalação parcial.
- **Licenças copyleft** (golangci-lint GPL-3.0, semgrep LGPL-2.1, Temurin GPLv2 com Classpath Exception): a Forja baixa e executa os binários, não os redistribui nem os empacota.
- **Semgrep só existe como pacote Python** (leitura do passo 0): fica em ambiente virtual próprio, criado com o Python da Forja.

## Adendo de 15/11/2026: ferramentas oficiais do ecossistema .NET, e como conferir a licença

Decisão do dono em 15/11/2026, ao tratar do `dotnet-coverage`:

- **O ecossistema oficial da Microsoft para o .NET é de código aberto, e a Forja usa as ferramentas oficiais dele.** Nenhuma ferramenta oficial da Microsoft para o .NET fica de fora por causa da licença.
- **Antes de incluir uma ferramenta nova, a licença é conferida no GitHub**, no repositório que a ferramenta indica. Se a licença é de código aberto e a ferramenta é necessária, ela entra no manifesto.

Com isso entrou o `dotnet-coverage` (necessário à mutação em C#, para saber que testes executam cada linha). O pacote indica o repositório `microsoft/codecoverage`, que o GitHub dá como MIT. O pacote NuGet traz também um `License.txt` próprio (Microsoft Software License Terms) e pede aceite de licença; fica registrado.

O manifesto ganhou o tipo de artefato `nupkg`: um pacote NuGet, que é um zip, cujo executável é a montagem de uma ferramenta .NET, rodada pelo `dotnet`.
