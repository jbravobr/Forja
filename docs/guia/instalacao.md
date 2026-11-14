# Instalação

Esta página diz como instalar a Forja, como ela instala as ferramentas externas de que depende, o que fica por sua conta, e como atualizar e remover. Os comandos estão em [comandos.md](comandos.md); as pastas e variáveis, em [ambiente.md](ambiente.md).

## O que precisa existir antes

| Item | Quem instala | Observação |
| --- | --- | --- |
| Python 3.12 ou mais | você (ou o `uv`, que baixa o Python que o pacote exige) | o pacote declara `requires-python >= 3.12` |
| `uv` (ou `pipx`) | você | instala o comando `forja` num ambiente isolado |
| `git` | você | a Forja não instala o git; `forja doctor` verifica |
| CLI do agente (por exemplo `claude`, `codex`, `cursor`, `grok`) | você | a Forja não instala a CLI nem faz o login; veja [clis.md](clis.md) |
| Rede | | necessária só na instalação das ferramentas e na instalação da própria Forja |

## Plataformas

| Plataforma | `forja instalar` | O resto |
| --- | --- | --- |
| Windows x64 | instala as ferramentas | suportado |
| Linux x64 | instala as ferramentas | não suportado: a Forja não é testada no Linux |
| macOS, ou Windows e Linux de outra arquitetura (por exemplo ARM) | não instala nada | `forja doctor` funciona; as ferramentas ficam por sua conta |

O manifesto da Forja traz arquivos para `windows-x64` e `linux-x64`, e só para eles. Em qualquer outra plataforma, `forja instalar` imprime `Nesta plataforma a Forja não instala ferramentas (...)` e sai com código `2`. Quem roda só `forja` nessa plataforma não recebe a oferta de instalação: a Forja segue sem instalar. Nesse caso, instale gitleaks, osv-scanner, jscpd, Semgrep e as ferramentas das suas linguagens pelos meios de cada uma, de modo que fiquem no `PATH`; `forja doctor` diz o que falta e onde buscar.

## Instalar a Forja

O pacote se chama `forja` e declara o comando `forja`. A forma de instalação descrita aqui parte de uma cópia do repositório:

```bash
# Na pasta da cópia do repositório da Forja.
uv tool install .
```

```bash
# Alternativa com pipx.
pipx install .
```

O comando `forja` passa a existir no diretório de executáveis do `uv` (ou do `pipx`). Se o shell não o achar, rode `uv tool update-shell` e abra um terminal novo. Para conferir:

```bash
forja --help      # lista os comandos
forja doctor      # verifica o ambiente (veja abaixo)
```

O pacote traz consigo as bibliotecas Python que usa, entre elas `ruff` e `lizard`; esses dois não precisam de instalação à parte. O `forja doctor` os lista como `(vem com a Forja)`.

## Verificar o ambiente: `forja doctor`

```bash
# Dentro do repositório que a Forja vai tratar.
forja doctor
forja doctor --cli codex                    # prova a CLI codex em vez da padrão
forja doctor --semgrep-config regras/       # inclui o Semgrep na verificação
```

O `doctor` só lê e diz o que falta; não instala nada. Cada item sai numa linha:

```text
[ok ] git: git no PATH
[FALTA] gitleaks: gitleaks ausente
        -> rode `forja instalar` ou instale seguindo https://github.com/gitleaks/gitleaks (as instruções de instalação estão no README)
```

- `[ok ]` indica item presente. `[FALTA]` indica item ausente, e a linha seguinte, com `->`, diz o que fazer.
- Quando a ferramenta foi encontrada na pasta de ferramentas da Forja, o detalhe diz `instalado pela Forja` e mostra o caminho; quando veio do sistema, diz `no PATH`.
- Sempre aparecem: `git`, `ruff`, `lizard`, `gitleaks`, `jscpd`, `disco` (espaço livre) e a CLI do agente. Esta última passa por uma prova de vida: a Forja pede à CLI uma tarefa pequena de teste, então a CLI precisa estar instalada e com login feito.
- Dentro de um repositório entram também `repositório limpo` e, conforme as linguagens detectadas, `pytest no repositório` e `mypy` (Python), `go` e `golangci-lint`, `node`, `dotnet`, `java` com `maven` ou `gradle`. O `semgrep` entra com `--semgrep-config` ou quando o repositório tem `.semgrep.yml` ou `.semgrep/`.
- `mypy`, `golangci-lint` e as CLIs de agente além da escolhida são opcionais: aparecem, mas não contam para o resultado. Sem o `golangci-lint`, o `lint` de um projeto Go fica inconclusivo, e com a política padrão a tarefa para (veja `gates/tipos.md`).
- A saída termina em `Tudo pronto.` ou em `N item(ns) a resolver.`

![Verificação do ambiente no terminal](imagens/terminal-doctor.png)

Verificação do ambiente num repositório limpo (`forja doctor`).

Código de saída: `0` se nenhum item obrigatório falta; `1` caso contrário. Detalhes e um exemplo completo em [comandos.md](comandos.md).

## Instalar as ferramentas: `forja instalar`

```bash
# Dentro do repositório alvo: as ferramentas das linguagens dele entram junto.
forja instalar
```

O comando instala o que falta e mostra o andamento de cada ferramenta (nome, para que serve, versão, origem, tamanho e etapa), com um resumo no fim. Ele não tem argumentos próprios.

![Ajuda de forja instalar no terminal](imagens/terminal-instalar-ajuda.png)

Ajuda do comando de instalação (`forja instalar --help`).

### O que é instalado

A versão de cada ferramenta é a do manifesto que acompanha a sua versão da Forja. O manifesto também fixa, por plataforma, a URL de origem, o tamanho e o checksum.

Para toda execução:

| Ferramenta | Para que serve | Formato do download |
| --- | --- | --- |
| `gitleaks` | segredos no código e no histórico | arquivo compactado |
| `osv-scanner` | vulnerabilidades conhecidas nas dependências | executável único |
| `jscpd` | código duplicado | arquivo compactado |
| `semgrep` | análise estática de segurança (SAST) | pacote Python (wheel) em ambiente virtual próprio |

Por linguagem, só quando ela é detectada no repositório:

| Linguagem | Ferramentas |
| --- | --- |
| Python | `mypy` (pacote Python em ambiente virtual próprio) |
| Go | `go`, `golangci-lint` |
| JavaScript e TypeScript | `node` |
| C# | `dotnet-sdk` (o SDK do .NET) |
| Java | `jdk` (Temurin), `maven` se o repositório usa Maven, `gradle` se usa Gradle |

Regras:

- Uma ferramenta que já está no `PATH` vale e não é instalada de novo.
- Uma ferramenta já instalada por inteiro pela Forja, na versão do manifesto, também não é baixada de novo. Rodar `forja instalar` outra vez é seguro e, com tudo no lugar, imprime `Nada a instalar: as ferramentas da Forja já estão no lugar.` e sai com `0`.
- As ferramentas são instaladas uma depois da outra. Se uma falha, a Forja tenta as seguintes. A mensagem de falha diz `rode forja instalar de novo; se persistir, instale <ferramenta> à mão`.
- Código de saída: `0` se todas ficaram no lugar; `1` se alguma falhou; `2` em plataforma sem suporte.

### Verificação de integridade

Cada arquivo baixado tem o tamanho e o checksum (SHA-256 ou SHA-512, conforme a ferramenta) conferidos contra o manifesto antes de ser aberto. O download é interrompido se a origem anuncia ou envia mais bytes que o tamanho do manifesto. Arquivos compactados que tentam escrever fora da pasta de destino são recusados.

Uma ferramenta fica instalada por inteiro ou não fica: o marcador `.forja-installed` é gravado por último, e uma falha em qualquer etapa remove o que foi feito. Não sobra instalação pela metade.

Exceção a observar: `semgrep` e `mypy` vêm como pacotes Python. A Forja confere o checksum do pacote principal, mas o `pip` que o instala resolve as dependências dele sem checksum do manifesto. Essa instalação cria um ambiente virtual com o Python que roda a Forja e precisa de acesso ao índice de pacotes (PyPI, ou o índice que o seu ambiente configurar para o `pip`).

### Onde ficam

Em `<pasta de dados da Forja>\tools\<ferramenta>\<versão>\`. A pasta de dados é `FORJA_HOME`, se definida; senão `%LOCALAPPDATA%\forja` no Windows e `~/.local/share/forja` no Linux.

```bash
# Instala as ferramentas num disco com espaço.
export FORJA_HOME=/d/forja-dados
forja instalar
```

Nada é instalado fora dessa pasta, não é preciso administrador e o `PATH` do sistema não muda. A Forja põe a pasta de cada ferramenta na frente do `PATH` dos processos que ela mesma lança. A exceção são `semgrep` e `mypy`: a pasta deles entra no fim do `PATH`, para o `python` do ambiente virtual não esconder o do seu projeto.

### Instalação durante uma execução

Quem roda só `forja` (ou um verbo, como `forja refatorar`) vê, antes da confirmação, a linha `Faltam ferramentas: <nome> <versão>, .... Instalação em <pasta>.`. Confirmar a execução confirma também a instalação. Se a instalação falha, a execução não começa.

Sem terminal (CI) ou com `--config`, a Forja não instala por conta própria. Ela instala só se o `execucao.toml` tiver `instalar_ferramentas = true`; sem isso, avisa e segue sem instalar. Veja [execucao-toml.md](execucao-toml.md).

## O que continua por sua conta

- **git**, **a CLI do agente** e o **login** dela.
- Em Windows e Linux x64, as ferramentas de linguagem que a Forja instala (`go`, `node`, `dotnet-sdk`, `jdk`, `maven`, `gradle`) são opcionais: você pode usar as suas, desde que estejam no `PATH`. `golangci-lint` e `mypy` também.
- Em outras plataformas, todas as ferramentas externas: gitleaks, osv-scanner, jscpd, Semgrep, mais `go`, `node`, `dotnet`, JDK e Maven ou Gradle conforme as linguagens dos seus repositórios.
- O ambiente do projeto alvo: o `pytest` precisa importar no Python do repositório (`forja doctor` confere), e as dependências de teste de cada linguagem são do projeto.
- Em repositório Java com `gradlew`, o `doctor` usa o wrapper do repositório e não pede o `gradle`.

## Atualizar

A Forja, a partir de uma cópia atualizada do repositório:

```bash
git pull                              # traz o código novo
uv tool install --reinstall .         # reinstala o comando forja
```

As ferramentas externas têm versão fixada no manifesto da Forja. Uma versão nova da Forja pode trazer versões novas delas; `forja instalar` instala as novas ao lado das antigas, e nada é apagado.

## Remover

A Forja não apaga nada por conta própria.

```bash
uv tool uninstall forja       # remove o comando forja (com pipx: pipx uninstall forja)
```

Para remover o que ela guardou, apague as pastas à mão:

| Pasta | Conteúdo |
| --- | --- |
| `<pasta de dados>\tools` | só as ferramentas instaladas por `forja instalar` |
| `<pasta de dados>` inteira | também execuções, travas e relatórios |
| `<pasta do perfil>` (`FORJA_CONFIG_HOME`) | `config.toml` e o cache das provas de vida |

As pastas padrão e as variáveis que as mudam estão em [ambiente.md](ambiente.md). As branches e os arquivos que a Forja criou nos seus repositórios (`forja-run/<id>`, `docs\mudancas\`) ficam; remova-os com o git, se quiser.
