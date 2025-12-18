# FlowAI - Sistema de Automação Inteligente

## Visão Geral
Sistema backend estilo n8n que usa IA (Gemini) para gerar fluxos de automação automaticamente. O usuário descreve em texto o fluxo que deseja criar, e o sistema usa 4 agentes de IA para interpretar, construir, validar e aprender com cada geração.

**NOVO**: Editor visual drag-and-drop estilo n8n para criar workflows visualmente, com suporte a loops, condições e todas as funcionalidades de automação.

## Arquitetura de Agentes (MELHORADA)

### 1. Agente de Intenção AVANÇADO
- Análise profunda do pedido do usuário (não apenas keywords)
- Detecta: objetivo, ação, complexidade, tipo de saída, integrações
- Suporta ações: busca, transformação, envio, armazenamento, híbrido
- Fallback inteligente combinando IA + keywords
- Identifica credenciais necessárias com precisão

### 2. Agente Construtor AVANÇADO
- Gera fluxos com múltiplos tipos de nodes (3-10 nodes)
- Suporta: trigger, search, transform, condition, loop, integration, database, output
- Construção estruturada: começa com trigger, termina com output
- Auto-validação: adiciona componentes faltantes se necessário
- Adapta complexidade ao objetivo (simples/média/complexa)

### 3. Agente Arquiteto INTELIGENTE
- Validação rigorosa mas justa (não rejeita fluxos válidos)
- Checklist: trigger, output, integrações corretas, estrutura válida
- Scoring inteligente: 100 (perfeito) até 30 (rejeitado)
- Avisos não reprovam - mas alertam sobre melhorias
- Fallback automático se IA falhar

### 4. Agente de Aprendizado
- Salva todos os fluxos em learning_memory.json
- Registra prompts, intenções, resultados, scores, erros
- Serve como base para melhorias futuras

## Funcionalidades de Automação Real

### Integrações Suportadas
- **Telegram Bot**: Envia mensagens via bot
- **API de Cotações**: Busca dólar, euro, BTC (AwesomeAPI gratuita)
- **Email (SMTP)**: Envio de emails
- **Slack**: Notificações em canais
- **WhatsApp Business**: Mensagens via API

### Agendamento de Automações
- Automações podem rodar em intervalos definidos (minutos/horas)
- Persistência em active_automations.json
- Iniciam automaticamente com o servidor
- Controle: pausar, iniciar, executar manualmente, remover

## Estrutura do Projeto
```
├── app.py                    # Aplicação Flask com agentes e automações
├── main.py                   # Ponto de entrada do servidor
├── learning_memory.json      # Memória persistente do sistema
├── active_automations.json   # Automações agendadas
├── generated_outputs/        # Arquivos gerados pelas automações
├── templates/
│   └── index.html            # Interface web completa
└── design_guidelines.md      # Diretrizes de design
```

## API Endpoints

### Geração de Fluxos
- **POST /generate-flow**: Gera novo fluxo de automação
- **POST /execute-flow**: Executa fluxo gerando arquivo
- **POST /execute-real**: Executa fluxo com APIs reais

### Gerenciamento de Automações
- **GET /automations**: Lista automações agendadas
- **POST /automations**: Cria nova automação agendada
- **POST /automations/<id>/start**: Inicia automação pausada
- **POST /automations/<id>/stop**: Pausa automação
- **POST /automations/<id>/run**: Executa uma vez manualmente
- **DELETE /automations/<id>**: Remove automação

### Biblioteca de Fluxos Salvos
- **GET /saved-flows**: Lista todos os fluxos salvos
- **POST /saved-flows**: Salva um novo fluxo
- **GET /saved-flows/<id>**: Retorna detalhes de um fluxo
- **POST /saved-flows/<id>/execute**: Executa um fluxo salvo
- **POST /saved-flows/<id>/schedule**: Agenda um fluxo salvo
- **DELETE /saved-flows/<id>**: Remove um fluxo salvo

### Configuração
- **GET /credentials**: Status das credenciais configuradas
- **GET /integrations**: Lista integrações disponíveis

### Utilitários
- **GET /stats**: Estatísticas do sistema
- **GET /history**: Histórico de fluxos
- **GET /health**: Health check

## Tecnologias
- Python 3.11
- Flask
- SQLite3 puro (sem ORM)
- google-genai (Gemini API)
- APScheduler (agendamento)
- Requests (chamadas HTTP)
- Gunicorn

## Variáveis de Ambiente

### Obrigatórias
- `GEMINI_API_KEY`: Chave da API do Gemini
- `SESSION_SECRET`: Chave secreta para sessões Flask

### Integrações (opcionais)
- `TELEGRAM_BOT_TOKEN`: Token do bot Telegram (@BotFather)
- `TELEGRAM_CHAT_ID`: ID do chat para enviar mensagens
- `GOLD_API_KEY`: Chave para API de commodities
- `SMTP_SERVER`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`: Config email

## Preferências do Usuário
- Interface em português brasileiro
- Sistema completo em um único arquivo app.py
- Sem uso de pydantic
- Pronto para deploy no Render
- Automações devem executar de verdade, não apenas gerar JSON
