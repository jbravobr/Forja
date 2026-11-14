# 0019. A medição 2 decide seguir, com ressalvas

## Contexto

O incremento 2 mediu a Forja (portões, tentativas guiadas, integração por tarefa) contra o mesmo Claude Code com um único prompt, no objetivo "refatorar". A regra de decisão foi fixada antes de qualquer execução contada (`medicao-2.md`). Relatório e números: `medicao-2-relatorio.md`.

## Decisão

Seguir com o plano. Nas tarefas médias a Forja aceitou 6 de 6 e a linha de base 4 de 6 (33 pontos percentuais), as duas sem regressão, com 1,37× os tokens. A regra 2 ("Seguir") se aplica; a regra 1 ("Inconclusivo") não, porque a linha de base ficou abaixo de 90%.

Ressalvas que acompanham a decisão:

- As duas reprovações da linha de base foram só por formatação, e a Forja normaliza o estilo e a linha de base não. A medição não separa o efeito dos portões do efeito da formatação.
- Amostra pequena (6 execuções médias por braço); sem afirmação de significância.
- A Forja usou 3 a 4 tentativas nas tarefas médias e 1,33× o tempo.
- Só Python, só refatoração, só Claude Code.

## Consequências

Os incrementos seguintes continuam. A pergunta aberta (o ganho sobrevive se a linha de base também formata?) fica registrada e pode virar uma rodada curta futura, com protocolo escrito antes. A decisão é do dono do repositório e foi tomada em 12/10/2026.
