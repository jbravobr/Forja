# Ambiente: variáveis, pastas, arquivos e interface

Esta página reúne o que a Forja lê do ambiente, o que ela cria no seu computador e no seu repositório, e como controlar a interface de terminal. Os comandos estão em `comandos.md`.

## Variáveis de ambiente

### Onde ficam os dados

| Variável | Efeito | Padrão |
| --- | --- | --- |
| `FORJA_HOME` | pasta raiz dos dados da Forja (execuções, travas, relatórios, ferramentas) | Windows: `%LOCALAPPDATA%\forja`; outras plataformas: `~/.local/share/forja` |
| `FORJA_CONFIG_HOME` | pasta do perfil (`config.toml`) | Windows: `%APPDATA%\forja`; outras: `$XDG_CONFIG_HOME/forja`, ou `~/.config/forja` se `XDG_CONFIG_HOME` não existir |
| `APPDATA` | base do perfil no Windows | definida pelo Windows |
| `XDG_CONFIG_HOME` | base do perfil fora do Windows | definida pelo sistema |
| `LOCALAPPDATA` | base dos dados no Windows | definida pelo Windows |

```bash
# Guarda tudo da Forja num disco com espaço.
export FORJA_HOME=/d/forja-dados
forja refatorar
```

```powershell
$env:FORJA_HOME = "D:\forja-dados"
forja refatorar
```

### Qual executável usar para cada CLI

| Variável | Efeito |
| --- | --- |
| `FORJA_EXECUTOR_<NOME>` | caminho do executável da CLI de agente `<NOME>`. O nome vai em maiúsculas e o que não for letra ou dígito vira `_`: `claude-code` vira `FORJA_EXECUTOR_CLAUDE_CODE` |
| `PATH` | onde a Forja procura executáveis quando nada mais foi dito |
| `PATHEXT` | extensões consideradas ao procurar executáveis no Windows |

A ordem de busca do executável de uma CLI é: `executavel` no `execucao.toml`, depois `FORJA_EXECUTOR_<NOME>`, depois `executaveis` do perfil, depois a descoberta (`PATH` e pastas conhecidas de cada CLI). Mais em `clis.md`.

```bash
export FORJA_EXECUTOR_CODEX=/opt/codex/bin/codex
forja corrigir "o frete grátis aparece para pedidos de R$ 99" --cli codex
```

### Chaves das CLIs de agente

A Forja repassa ao processo de cada CLI só as variáveis abaixo, além das que ela mesma define.

| CLI | Variáveis repassadas |
| --- | --- |
| `codex` | `CODEX_HOME`, `CODEX_API_KEY`, `APPDATA`, `LOCALAPPDATA`, `COMSPEC`, `PATHEXT`, `HTTPS_PROXY`, `HTTP_PROXY`, `NO_PROXY`, `NODE_EXTRA_CA_CERTS`, `SSL_CERT_FILE` |
| `cursor` | `CURSOR_API_KEY`, `APPDATA`, `LOCALAPPDATA`, `COMSPEC`, `PATHEXT`, `HTTPS_PROXY`, `HTTP_PROXY`, `NO_PROXY`, `NODE_USE_ENV_PROXY`, `NODE_EXTRA_CA_CERTS`, `SSL_CERT_FILE` |
| `grok` | `XAI_API_KEY`, `APPDATA`, `LOCALAPPDATA`, `COMSPEC`, `PATHEXT`, `HTTPS_PROXY`, `HTTP_PROXY`, `NO_PROXY` |

Com `cursor` e `grok`, o login do usuário na CLI não vale: a execução precisa de `CURSOR_API_KEY` ou de `XAI_API_KEY`. A Forja também define `NO_COLOR=1` para as três; para o `cursor` define `CURSOR_CONFIG_DIR` e para o `grok` define `GROK_HOME`, ambos dentro da pasta `.forja-agent` da cópia de trabalho. No Windows o sandbox do `grok` não existe.

### Rede, certificados e índices de pacote

A Forja repassa estas variáveis ao git e às instalações de dependência da execução, para que proxy e certificados corporativos funcionem: `HTTP_PROXY`, `HTTPS_PROXY`, `NO_PROXY`, `SSL_CERT_FILE`, `SSL_CERT_DIR`, `REQUESTS_CA_BUNDLE`, `PIP_INDEX_URL`, `PIP_EXTRA_INDEX_URL`, `PIP_CERT`, `PIP_NO_INDEX`, `PIP_FIND_LINKS`.

```bash
# Índice interno de pacotes e certificado da empresa.
export PIP_INDEX_URL=https://pypi.interno.exemplo/simple
export SSL_CERT_FILE=/etc/ssl/certs/empresa.pem
forja refatorar
```

Para os processos isolados (testes, build, ferramentas), o ambiente é limitado a `PATH`, `SYSTEMROOT`, `TEMP`, `TMP`, `TMPDIR`, `PATHEXT`, `COMSPEC`, `HOME`, `USERPROFILE`, `LANG`, `TERM` e as variáveis `LC_*`; a Forja acrescenta `PYTHONDONTWRITEBYTECODE=1`. O comando de e2e roda com variáveis de proxy apontando para uma armadilha local em `127.0.0.1` que responde 403, `NO_PROXY=localhost,127.0.0.1,::1`, `npm_config_offline=true`, `PIP_NO_INDEX=1`, `GOPROXY=off` e `NODE_USE_ENV_PROXY=1`; veja `gates\e2e-e-dados-de-teste.md`.

### Fachada do agente

| Variável | Efeito |
| --- | --- |
| `FORJA_AGENT_PYTHON` | o Python que `forja agente testar` usa. A Forja a define com o Python do repositório; sem ela, vale o Python que roda a Forja |

### Terminal

A Forja escolhe o nível de tela (completa, linha ou texto) e as cores olhando estas variáveis.

| Variável | Efeito |
| --- | --- |
| `WT_SESSION` | indica Windows Terminal |
| `TERM_PROGRAM` | `vscode`, `mintty`, `iTerm.app`: ajusta a detecção do terminal |
| `TMUX`, `STY` | indicam multiplexador (tmux, screen): a Forja não redimensiona a janela |
| `MSYSTEM` | indica Git Bash (mintty) |
| `TERM` | capacidade do terminal (`xterm*`, `dumb`, `*256color`) |
| `COLORTERM` | `truecolor` ou `24bit` liga cor de 24 bits |
| `NO_COLOR` | desliga a cor |
| `COLORFGBG` | diz se o fundo é claro ou escuro, para escolher o tema inicial |
| `NO_MOTION`, `REDUCE_MOTION` | qualquer valor não vazio reduz a animação |

```bash
NO_COLOR=1 forja refatorar --plano      # sem cor
REDUCE_MOTION=1 forja refatorar         # sem animação
```

## Onde a Forja guarda o que cria

### Pasta de dados (`FORJA_HOME`)

| Caminho | Conteúdo |
| --- | --- |
| `runs/<id>/events.jsonl` | registro de eventos da execução; o texto que você escreveu (fatos, pergunta, descrições) passa pela redação antes de ser gravado |
| `runs/<id>/logs/git` | logs do git |
| `runs/<id>/execucao.toml` | cópia das opções da execução |
| `runs/<id>/resumo.md` | resumo final da execução |
| `runs/<id>/semgrep-rules` | cópia das regras do Semgrep usadas |
| `locks/<repo>/` | trava que impede duas execuções no mesmo repositório |
| `revisoes/<id>.md` | relatório de `forja revisao` |
| `tools/` | ferramentas instaladas por `forja instalar`, com o marcador `.forja-installed` |

### Pasta do perfil (`FORJA_CONFIG_HOME`)

| Arquivo | Conteúdo |
| --- | --- |
| `config.toml` | suas escolhas lembradas: `cli`, `executaveis`, `clis` e `interface`. Veja `execucao-toml.md` |
| `provas.json` | cache das provas de vida das CLIs, cada uma com o momento em que foi feita; vale por um dia |

### No repositório

| Item | Detalhe |
| --- | --- |
| `forja-run/<id>` | branch da entrega, numa worktree própria |
| `forja/<execução>/<tarefa>` | branch de cada tarefa |
| autor dos commits | `Forja (<cli>)`, e-mail `forja@localhost` |
| `docs/mudancas/<tarefa>.md` | registro de mudança da tarefa, no mesmo commit |
| `docs/mudancas/<tarefa>-diario.md` | diário de bordo da tarefa; veja `diario-de-bordo.md` |
| `refs/notes/commits` | uma nota do git em cada commit de etapa, com as métricas |

A Forja nunca faz push e nunca altera a sua branch. Para ver o que ela entregou:

```bash
git log --oneline forja-run/<id>
```

### O que cada commit de etapa diz

O assunto segue Conventional Commits (`refactor:`, `fix:`, `feat:`, `test:`, `docs:`, `style:`). O corpo é o motivo que o agente deu. Os trailers são escritos pela Forja:

| Trailer | Conteúdo |
| --- | --- |
| `Task-Id`, `Stage-Id`, `Attempt-Id` | a tarefa, a etapa e a tentativa |
| `Agent` | a CLI e a versão |
| `Policy-Version` | a versão das políticas |
| `Evidence-Id` | um para cada check que aprovou a tentativa |
| `Metric-<nome>` | `<antes> -> <depois>`, só para a métrica que mudou; "antes" é a base da execução |
| `Assumption` | um para cada premissa que o agente declarou |

A nota do commit traz, em JSON, todas as métricas, mudadas ou não, e os checks:

```bash
git notes show <commit>
```

As notas são locais como a branch. `git push` não as leva; quem quiser publicá-las roda `git push <remoto> refs/notes/commits`. Squash e rebase criam commits novos, sem as notas dos antigos.

### Cópia de trabalho do agente

O agente trabalha numa cópia do projeto sem `.git`. A pasta `.forja-agent/` dentro dela é reservada:

| Arquivo | Conteúdo |
| --- | --- |
| `.forja-agent/package.md` | o pacote que a Forja entrega ao agente |
| `.forja-agent/saida.json` | a resposta do agente |
| `.forja-agent/diary.md` | o diário de bordo que o agente vê |

O agente não pode alterar o diário nem os arquivos de permissão.

### Arquivos de medição

As verificações escrevem relatórios durante a execução: `base.json` e `base-metrics.json` (a linha de base), `jscpd-report.json` (duplicação), `gitleaks.json` (segredos), relatórios `.json` do SAST e da auditoria de dependências, e cobertura em `target/coverage/python/coverage.json` (Python), `.forja-cover.out` (Go), `.forja-coverage/` (TypeScript e C#) e `target/site/jacoco/jacoco.xml` (Java com Maven). Esses arquivos pertencem à execução; não são entrada sua.

### Arquivos do projeto que a Forja lê

A auditoria de dependências lê os arquivos de trava que existirem: `requirements.txt`, `Pipfile.lock`, `poetry.lock`, `pdm.lock`, `uv.lock`, `pylock.toml`, `package-lock.json`, `pnpm-lock.yaml`, `yarn.lock`, `bun.lock`, `packages.lock.json`, `packages.config` e `deps.json`. A detecção da linguagem usa:

| Linguagem | Arquivo que a identifica |
| --- | --- |
| Python | `pyproject.toml` |
| Go | `go.mod` |
| Java | `pom.xml`, `build.gradle` ou `build.gradle.kts` (Maven se houver `pom.xml`) |
| C# | `*.sln` ou `*.csproj` |
| TypeScript | `package.json` junto de `tsconfig.json` |

Sem nenhum deles, a Forja pede a linguagem e lista os arquivos aceitos.

## Prevenção de suspensão

No Windows, a Forja pede ao sistema que não suspenda o computador enquanto a execução roda (`SetThreadExecutionState`) e libera no fim. Fora do Windows ela não pede nada.

## Interface de terminal

### Níveis de tela

| Nível | Quando | Requisito |
| --- | --- | --- |
| completa | terminal interativo | mínimo 60 colunas por 16 linhas; recomendado 120 por 36 |
| linha | janela pequena ou terminal limitado | uma linha ao vivo |
| texto | saída em pipe, CI ou sem terminal | texto simples, sem cursor |

Se a saída não for um terminal, a Forja usa o texto. Se a janela for menor que 60 por 16, cai para a linha e avisa "a janela está pequena (CxL); a tela completa pede ao menos 60x16". Só em `xterm`, `mintty` e `iTerm` a Forja amplia a janela, e nunca dentro de multiplexador ou terminal embutido. A chave `modo` da tabela `[interface]` do `execucao.toml` força um nível; veja `execucao-toml.md`.

![Tela completa durante a execução, em 120 por 36](imagens/execucao-em-andamento.svg)

Tela completa em andamento (`forja`).

### Teclas durante a execução

| Tecla | Efeito |
| --- | --- |
| `q` | parar; a primeira pergunta se salva, a segunda sai na hora |
| `ctrl+c`, `ctrl+k` | parar |
| `t` | muda o tema |

### Teclas da tela completa

| Tecla | Efeito |
| --- | --- |
| `d` | detalhes da tarefa atual (checks, tentativa, rejeições); de novo fecha |
| `l` | log completo da atividade; de novo fecha |
| `t` | muda o tema |
| `?` | ajuda |
| `q` | sair ou parar |
| `escape` | fecha a sobreposição aberta |
| `up`, `down`, `pageup`, `pagedown`, `end` | rolagem nos painéis |
| `ctrl+c`, `ctrl+k` | parar |

![Detalhes da tarefa atual](imagens/detalhes-da-tarefa.svg)

Tecla `d` durante a execução: detalhes da tarefa atual, com os checks por gate e as rejeições.

![Log completo da atividade](imagens/atividade-inteira.svg)

Tecla `l` durante a execução: a atividade inteira.

![Ajuda da tela completa](imagens/ajuda.svg)

Tecla `?` durante a execução: a ajuda.

![Aviso ao parar com q](imagens/execucao-interrompida.svg)

Tecla `q` durante a execução: o aviso de que tudo foi salvo e que `q` de novo sai na hora.

Na tela de instalação (`forja instalar`), `ctrl+c` cancela, `t` muda o tema e `q` sai.

![Tela da instalação das ferramentas](imagens/instalacao.svg)

Tela de `forja instalar` com um download em andamento.

### Temas

A tecla `t` percorre os temas nesta ordem. Sem escolha, o tema vem do fundo do terminal (`COLORFGBG`): claro ou escuro.

| Chave | Título | Fundo | Nota |
| --- | --- | --- | --- |
| `escuro` | Forja Escuro | escuro | padrão com fundo escuro ou desconhecido |
| `darcula` | Forja Darcula | escuro | |
| `claro` | Forja Claro | claro | padrão com fundo claro |
| `contraste` | Alto contraste | escuro | borda simples |
| `neon` | Forja Neon | escuro | títulos espaçados, borda por assunto |
| `dos` | Forja DOS | escuro | borda dupla, títulos de aba e sombra |

![Tema claro](imagens/tema-claro.svg)

Tema `claro` (`tema = "claro"` no `execucao.toml`, ou a tecla `t`).

![Tema dos](imagens/tema-dos.svg)

Tema `dos` (`tema = "dos"` no `execucao.toml`, ou a tecla `t`).

A chave `tema` do `execucao.toml` aceita esses nomes, sem distinguir maiúsculas; `ascii = true` troca os símbolos por ASCII. Mais em `execucao-toml.md`.

### Barra de gates

![Barra de gates com os nomes, numa janela larga](imagens/barra-de-gates.svg)

Barra dos gates 1 a 12 na tela de `forja`: os seis primeiros passaram e o sétimo roda. Em janela estreita a barra mostra só os números.

A barra mostra 12 gates. Cada um reúne um ou mais checks, e o gate falha se qualquer check dele falha, roda se algum roda e passa se todos passam.

| Gate | Nome na barra | Checks |
| --- | --- | --- |
| 1 | escopo | `scope`, `freeze` |
| 2 | segredos | `secrets`, `dependency_install` |
| 3 | lint | `format`, `lint`, `types` |
| 4 | build | `build` |
| 5 | testes | `tests`, `red_green` |
| 6 | e2e | `e2e`, `test_data`, `properties` |
| 7 | mutacao | `mutation` |
| 8 | metricas | `complexity`, `crap`, `pass_through`, `module_depth`, `duplication` |
| 9 | arquitetura | `dependency_cycles`, `layer_rules`, `package_metrics` |
| 10 | seguranca | `sast`, `dependency_audit`, `new_dependency` |
| 11 | diferencial | `differential` |
| 12 | voz | `voice`, `comments` |

Os gates acima de 12 não aparecem na barra. A lista dos 14 gates está em `gates\README.md`.
