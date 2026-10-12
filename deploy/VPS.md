# Rodando o desk numa VPS (laptop fechado)

> **Jurisdição:** o IP da VPS é o que o Polymarket enxerga. Use uma VPS no país onde **você** pode operar legalmente. Usar uma VPS em outro país para contornar o bloqueio do seu país viola os termos do Polymarket — o desk recusa ordens se o geobloqueio disser "bloqueado", mas a responsabilidade é sua.

1. **Servidor:** Ubuntu 24.04, 1 vCPU / 1 GB basta. Crie um usuário: `sudo adduser desk && sudo su - desk`.
2. **uv:** `curl -LsSf https://astral.sh/uv/install.sh | sh` e reabra o shell.
3. **Claude Code:** `curl -fsSL https://claude.ai/install.sh | bash`. Faça login uma vez com `claude` (siga o link no navegador do seu PC). Para servidores sem navegador, gere um token no seu PC com `claude setup-token` e coloque `CLAUDE_CODE_OAUTH_TOKEN=...` no `~/.profile` do usuário `desk` e no topo do crontab.
4. **Código:** `git clone <seu-repositorio> ~/polymarket-desk && cd ~/polymarket-desk && uv sync --extra live`.
5. **Estado:** copie `state/` do PC se quiser manter o histórico paper (`scp -r state desk@vps:~/polymarket-desk/`).
6. **Segredos:** `cp .env.example .env && chmod 600 .env` e preencha (ver `docs/GUIA.md`, seção Modo real).
7. **Teste:** `.venv/bin/python -m desk analyze <slug>` e `.venv/bin/python -m desk live-check`.
8. **Agendar:** ajuste `deploy/crontab` (caminhos e PATH) e rode `crontab deploy/crontab`. Confira com `crontab -l` e `tail -f state/cron.log`.
9. **Desligar o PC:** remova as tarefas do Windows (`scripts\uninstall_task.ps1`) para não rodar dois desks com estados diferentes.
