# FlowAI - Automação Inteligente com Render

## ✅ Otimizações Aplicadas

Este projeto foi totalmente otimizado para deployment no Render usando **SQLite3 puro** (sem SQLAlchemy).

### 📦 Arquitetura

- **Banco de Dados**: SQLite3 puro (sem ORM)
- **Persistência**: Arquivo local `flowai.db`
- **No Render**: Usa disco persistente em `/data/flowai.db`

### 📁 Arquivos de Deployment

- **Procfile**: Configuração gunicorn para Render
- **render.yaml**: Config com disco persistente para SQLite
- **requirements.txt**: Dependências limpas (sem SQLAlchemy)
- **database.py**: Camada de dados com sqlite3 puro
- **.gitignore**: Configurado para produção

### 🚀 Como Deploy

1. **Push seu código para GitHub**
2. **Acesse https://dashboard.render.com**
3. **Crie novo Web Service** (conectar GitHub)
4. **Configure:**
   - Build: `pip install -r requirements.txt`
   - Start: `gunicorn -w 4 -b 0.0.0.0:$PORT main:app`
   - Environment: `FLASK_ENV=production`
   - Disco persistente: Monte em `/data` (1GB)
   - Env var: `SQLITE_DB_PATH=/data/flowai.db`

### 🔑 Variáveis de Ambiente (Render Dashboard)

**Obrigatórias:**
- `GEMINI_API_KEY` - Chave API Gemini
- `SESSION_SECRET` - Chave para sessões
- `SQLITE_DB_PATH` - Caminho do banco (ex: `/data/flowai.db`)

**Opcionais (Integrações):**
- `TELEGRAM_BOT_TOKEN` - Token Telegram
- `TELEGRAM_CHAT_ID` - Chat ID Telegram
- `SMTP_*` - Email (SMTP_SERVER, SMTP_PORT, SMTP_USER, SMTP_PASSWORD)
- `GOLD_API_KEY` - API Commodities

### ⚙️ Mudanças Técnicas

**Convertido de SQLAlchemy para SQLite3 puro:**
- ✅ Removido Flask-SQLAlchemy
- ✅ Removido SQLAlchemy
- ✅ Removido psycopg2-binary
- ✅ Criado database.py com classes helper
- ✅ Todas as queries convertidas para sqlite3 puro
- ✅ Sem dependência de PostgreSQL

**Benefícios:**
- Menos dependências
- Deploy mais simples
- Sem necessidade de banco externo
- Funciona com disco persistente no Render

### 📊 Performance

- Gunicorn: 4 workers
- SQLite3: Leve e rápido para aplicações de porte médio
- Cache de APIs: Configurado para cotações

### 💾 Persistência no Render

Configure um disco persistente no Render:
- Path: `/data`
- Tamanho: 1GB
- Env var: `SQLITE_DB_PATH=/data/flowai.db`

Isso garante que seus dados sobrevivam a restarts do serviço.

---

**Status: ✅ PRONTO PARA RENDER COM SQLITE3 PURO**
