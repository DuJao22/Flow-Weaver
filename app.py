import os
import json
import logging
import re
import threading
import requests
from datetime import datetime
from flask import Flask, request, jsonify, render_template, redirect, url_for
from werkzeug.middleware.proxy_fix import ProxyFix
from google import genai
from google.genai import types
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

import sys
log_level = logging.INFO if os.environ.get("FLASK_ENV") == "production" else logging.DEBUG
logging.basicConfig(level=log_level, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', stream=sys.stdout)

scheduler = BackgroundScheduler()
scheduler.start()

ACTIVE_AUTOMATIONS = {}
AUTOMATIONS_FILE = "active_automations.json"

CURRENCY_CACHE = {
    "data": None,
    "timestamp": None,
    "ttl": 300,
    "last_request": None
}

app = Flask(__name__)
app.secret_key = os.environ.get("SESSION_SECRET")
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

from database import init_db, UserConfiguration, AutomationSchedule, SavedFlow, WorkflowProject, WorkflowNode, WorkflowEdge

init_db()

INTEGRATION_CREDENTIALS = {
    "telegram": {
        "name": "Telegram Bot",
        "keys": ["TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"],
        "instructions": {
            "TELEGRAM_BOT_TOKEN": "1. Abra o Telegram e procure por @BotFather\n2. Envie /newbot e siga as instruções\n3. Copie o token fornecido (formato: 123456789:ABCdefGHIjklMNOpqrsTUVwxyz)",
            "TELEGRAM_CHAT_ID": "1. Adicione seu bot a um grupo ou inicie uma conversa\n2. Envie uma mensagem para o bot\n3. Acesse: https://api.telegram.org/bot<SEU_TOKEN>/getUpdates\n4. Procure pelo chat.id no JSON retornado"
        },
        "docs_url": "https://core.telegram.org/bots/api"
    },
    "whatsapp": {
        "name": "WhatsApp Business API",
        "keys": ["WHATSAPP_API_KEY", "WHATSAPP_PHONE_ID"],
        "instructions": {
            "WHATSAPP_API_KEY": "1. Acesse developers.facebook.com\n2. Crie um app de negócios\n3. Configure o WhatsApp Business API\n4. Gere um token de acesso permanente",
            "WHATSAPP_PHONE_ID": "1. No painel do Meta Business\n2. Vá em WhatsApp > Configuração\n3. Copie o Phone Number ID"
        },
        "docs_url": "https://developers.facebook.com/docs/whatsapp"
    },
    "email": {
        "name": "Email (SMTP)",
        "keys": ["SMTP_SERVER", "SMTP_PORT", "SMTP_USER", "SMTP_PASSWORD"],
        "instructions": {
            "SMTP_SERVER": "Servidor SMTP do seu provedor (ex: smtp.gmail.com)",
            "SMTP_PORT": "Porta SMTP (geralmente 587 para TLS ou 465 para SSL)",
            "SMTP_USER": "Seu endereço de email",
            "SMTP_PASSWORD": "Senha de app (Gmail: myaccount.google.com/apppasswords)"
        },
        "docs_url": "https://support.google.com/mail/answer/7126229"
    },
    "slack": {
        "name": "Slack",
        "keys": ["SLACK_BOT_TOKEN", "SLACK_CHANNEL_ID"],
        "instructions": {
            "SLACK_BOT_TOKEN": "1. Acesse api.slack.com/apps\n2. Crie um novo app\n3. Adicione permissões de bot\n4. Instale no workspace\n5. Copie o Bot User OAuth Token",
            "SLACK_CHANNEL_ID": "1. No Slack, clique com botão direito no canal\n2. Selecione 'Copiar link'\n3. O ID está no final do link"
        },
        "docs_url": "https://api.slack.com/tutorials/tracks/getting-a-token"
    },
    "currency_api": {
        "name": "API de Cotações (AwesomeAPI)",
        "keys": [],
        "instructions": {},
        "docs_url": "https://docs.awesomeapi.com.br/api-de-moedas",
        "note": "API gratuita, não requer autenticação"
    },
    "gold_api": {
        "name": "API de Ouro/Commodities",
        "keys": ["GOLD_API_KEY"],
        "instructions": {
            "GOLD_API_KEY": "1. Acesse https://www.goldapi.io\n2. Crie uma conta gratuita\n3. Copie sua API key do dashboard"
        },
        "docs_url": "https://www.goldapi.io/dashboard"
    },
    "postgresql": {
        "name": "PostgreSQL",
        "keys": ["DATABASE_URL"],
        "instructions": {
            "DATABASE_URL": "Formato: postgresql://usuario:senha@host:porta/banco"
        },
        "docs_url": ""
    }
}

_client = None

def get_gemini_client():
    global _client
    if _client is None:
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            raise ValueError("GEMINI_API_KEY environment variable is not set")
        _client = genai.Client(api_key=api_key)
    return _client

MEMORY_FILE = "learning_memory.json"


def load_automations():
    """Carrega automações salvas do arquivo"""
    if os.path.exists(AUTOMATIONS_FILE):
        try:
            with open(AUTOMATIONS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return {}


def save_automations(automations):
    """Salva automações no arquivo"""
    try:
        with open(AUTOMATIONS_FILE, "w", encoding="utf-8") as f:
            json.dump(automations, f, indent=2, ensure_ascii=False)
    except IOError as e:
        logging.error(f"Erro ao salvar automações: {e}")


def fetch_currency_rates(currencies=None):
    """Busca cotações de moedas com fallback entre APIs"""
    global CURRENCY_CACHE
    
    if currencies is None:
        currencies = ["USD-BRL", "EUR-BRL", "BTC-BRL"]
    
    cache_key = ",".join(sorted(currencies))
    now = datetime.now()
    
    if (CURRENCY_CACHE["data"] is not None and 
        CURRENCY_CACHE["timestamp"] is not None and
        CURRENCY_CACHE.get("key") == cache_key and
        (now - CURRENCY_CACHE["timestamp"]).total_seconds() < CURRENCY_CACHE["ttl"]):
        logging.debug("Usando cache de cotações")
        return CURRENCY_CACHE["data"]
    
    result = _fetch_from_bcb()
    
    if not result["success"]:
        result = _fetch_from_awesome_api(currencies)
    
    if result["success"]:
        CURRENCY_CACHE["data"] = result
        CURRENCY_CACHE["timestamp"] = datetime.now()
        CURRENCY_CACHE["key"] = cache_key
        return result
    
    if CURRENCY_CACHE["data"] is not None:
        logging.warning("Usando cache anterior devido a erro nas APIs")
        return CURRENCY_CACHE["data"]
    
    return result


def _fetch_from_bcb():
    """Busca cotações do Banco Central do Brasil"""
    try:
        url = "https://olinda.bcb.gov.br/olinda/servico/PTAX/versao/v1/odata/CotacaoDolarDia(dataCotacao=@dataCotacao)?@dataCotacao='{}'&$format=json"
        today = datetime.now().strftime("%m-%d-%Y")
        
        response = requests.get(url.format(today), timeout=10)
        if response.status_code == 200:
            data = response.json()
            if data.get("value") and len(data["value"]) > 0:
                cotacao = data["value"][-1]
                result = {
                    "USDBRL": {
                        "nome": "Dólar Americano/Real Brasileiro",
                        "cotacao": float(cotacao.get("cotacaoCompra", 0)),
                        "variacao": 0,
                        "alta": float(cotacao.get("cotacaoVenda", 0)),
                        "baixa": float(cotacao.get("cotacaoCompra", 0)),
                        "data": cotacao.get("dataHoraCotacao", "")
                    }
                }
                return {"success": True, "data": result}
        
        from datetime import timedelta
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%m-%d-%Y")
        response = requests.get(url.format(yesterday), timeout=10)
        if response.status_code == 200:
            data = response.json()
            if data.get("value") and len(data["value"]) > 0:
                cotacao = data["value"][-1]
                result = {
                    "USDBRL": {
                        "nome": "Dólar Americano/Real Brasileiro",
                        "cotacao": float(cotacao.get("cotacaoCompra", 0)),
                        "variacao": 0,
                        "alta": float(cotacao.get("cotacaoVenda", 0)),
                        "baixa": float(cotacao.get("cotacaoCompra", 0)),
                        "data": cotacao.get("dataHoraCotacao", "")
                    }
                }
                return {"success": True, "data": result}
        
        return {"success": False, "error": "Dados não disponíveis no BCB"}
    except Exception as e:
        logging.error(f"Erro BCB: {e}")
        return {"success": False, "error": str(e)}


def _fetch_from_awesome_api(currencies):
    """Busca cotações da AwesomeAPI como fallback"""
    try:
        pairs = ",".join(currencies)
        response = requests.get(f"https://economia.awesomeapi.com.br/json/last/{pairs}", timeout=10)
        if response.status_code == 200:
            data = response.json()
            result = {}
            for key, value in data.items():
                result[key] = {
                    "nome": value.get("name", key),
                    "cotacao": float(value.get("bid", 0)),
                    "variacao": float(value.get("pctChange", 0)),
                    "alta": float(value.get("high", 0)),
                    "baixa": float(value.get("low", 0)),
                    "data": value.get("create_date", "")
                }
            return {"success": True, "data": result}
        elif response.status_code == 429:
            return {"success": False, "error": "APIs temporariamente indisponíveis"}
        return {"success": False, "error": f"Erro na API: {response.status_code}"}
    except Exception as e:
        return {"success": False, "error": str(e)}


def validate_integrations(integrations):
    """Valida se as integrações estão funcionando antes de entregar o fluxo"""
    validation_results = {
        "all_valid": True,
        "details": [],
        "warnings": [],
        "fixes_applied": []
    }
    
    for integration in integrations:
        if integration == "currency_api":
            result = fetch_currency_rates()
            if result["success"]:
                validation_results["details"].append({
                    "integration": "currency_api",
                    "status": "ok",
                    "message": "API de cotações funcionando"
                })
            else:
                validation_results["warnings"].append(
                    f"API de cotações: {result.get('error', 'erro desconhecido')}"
                )
                validation_results["fixes_applied"].append(
                    "Cache e fallback de APIs configurados automaticamente"
                )
        
        elif integration == "telegram":
            token = os.environ.get("TELEGRAM_BOT_TOKEN")
            chat_id = os.environ.get("TELEGRAM_CHAT_ID")
            
            if not token or not chat_id:
                validation_results["all_valid"] = False
                validation_results["details"].append({
                    "integration": "telegram",
                    "status": "error",
                    "message": "Credenciais não configuradas"
                })
            else:
                try:
                    url = f"https://api.telegram.org/bot{token}/getMe"
                    response = requests.get(url, timeout=5)
                    if response.status_code == 200:
                        bot_info = response.json()
                        if bot_info.get("ok"):
                            validation_results["details"].append({
                                "integration": "telegram",
                                "status": "ok",
                                "message": f"Bot @{bot_info['result'].get('username', 'N/A')} conectado"
                            })
                        else:
                            validation_results["all_valid"] = False
                            validation_results["details"].append({
                                "integration": "telegram",
                                "status": "error",
                                "message": "Token inválido"
                            })
                    else:
                        validation_results["all_valid"] = False
                        validation_results["details"].append({
                            "integration": "telegram",
                            "status": "error",
                            "message": f"Erro ao validar bot: {response.status_code}"
                        })
                except Exception as e:
                    validation_results["warnings"].append(f"Telegram: não foi possível validar ({str(e)})")
    
    return validation_results


def send_telegram_message(message, bot_token=None, chat_id=None):
    """Envia mensagem via Telegram Bot"""
    token = bot_token or os.environ.get("TELEGRAM_BOT_TOKEN")
    chat = chat_id or os.environ.get("TELEGRAM_CHAT_ID")
    
    if not token or not chat:
        return {"success": False, "error": "TELEGRAM_BOT_TOKEN ou TELEGRAM_CHAT_ID não configurados"}
    
    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = {
            "chat_id": chat,
            "text": message,
            "parse_mode": "HTML"
        }
        response = requests.post(url, json=payload, timeout=10)
        if response.status_code == 200:
            return {"success": True, "message": "Mensagem enviada com sucesso"}
        else:
            error_data = response.json()
            return {"success": False, "error": error_data.get("description", "Erro desconhecido")}
    except Exception as e:
        return {"success": False, "error": str(e)}


def execute_automation_task(automation_id):
    """Executa uma automação específica"""
    global ACTIVE_AUTOMATIONS
    
    if automation_id not in ACTIVE_AUTOMATIONS:
        logging.error(f"Automação {automation_id} não encontrada")
        return
    
    automation = ACTIVE_AUTOMATIONS[automation_id]
    intent = automation.get("intent", {})
    integrations = intent.get("integrations", [])
    
    logging.info(f"Executando automação: {automation.get('name', automation_id)}")
    
    results = []
    
    if "currency_api" in integrations:
        rates = fetch_currency_rates()
        if rates["success"]:
            results.append({"type": "currency", "data": rates["data"]})
        else:
            results.append({"type": "currency", "error": rates["error"]})
    
    if "telegram" in integrations and results:
        message = format_automation_message(automation, results)
        telegram_result = send_telegram_message(message)
        results.append({"type": "telegram", "result": telegram_result})
    
    automation["last_run"] = datetime.now().isoformat()
    automation["run_count"] = automation.get("run_count", 0) + 1
    automation["last_results"] = results
    
    saved_automations = load_automations()
    if automation_id in saved_automations:
        saved_automations[automation_id].update({
            "last_run": automation["last_run"],
            "run_count": automation["run_count"]
        })
        save_automations(saved_automations)
    
    logging.info(f"Automação {automation_id} executada com sucesso")


def format_automation_message(automation, results):
    """Formata mensagem para envio"""
    name = automation.get("name", "Automação")
    message_parts = [f"<b>{name}</b>\n"]
    
    for result in results:
        if result["type"] == "currency" and "data" in result:
            message_parts.append("\n<b>Cotações:</b>")
            for key, value in result["data"].items():
                variacao = value["variacao"]
                seta = "↑" if variacao > 0 else "↓" if variacao < 0 else "→"
                message_parts.append(
                    f"\n• {value['nome']}: R$ {value['cotacao']:.2f} ({seta} {variacao:.2f}%)"
                )
    
    message_parts.append(f"\n\n<i>Atualizado em: {datetime.now().strftime('%d/%m/%Y %H:%M')}</i>")
    
    return "".join(message_parts)


def load_memory():
    if os.path.exists(MEMORY_FILE):
        try:
            with open(MEMORY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return {"flows": [], "stats": {"total": 0, "approved": 0, "rejected": 0}}


def save_memory(memory):
    try:
        with open(MEMORY_FILE, "w", encoding="utf-8") as f:
            json.dump(memory, f, indent=2, ensure_ascii=False)
    except IOError as e:
        logging.error(f"Erro ao salvar memória: {e}")


def call_gemini_json(system_prompt: str, user_prompt: str) -> dict:
    try:
        client = get_gemini_client()
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=[
                types.Content(role="user", parts=[types.Part(text=user_prompt)])
            ],
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
                temperature=0.2,
                response_mime_type="application/json",
            ),
        )
        
        text = response.text if response.text else ""
        return extract_json_from_response(text)
        
    except Exception as e:
        logging.error(f"Erro ao chamar Gemini: {e}")
        raise


def extract_json_from_response(text: str) -> dict:
    if not text:
        raise ValueError("Resposta vazia da IA")
    
    text = text.strip()
    
    if text.startswith("```json"):
        text = text[7:]
    elif text.startswith("```"):
        text = text[3:]
    if text.endswith("```"):
        text = text[:-3]
    text = text.strip()
    
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    
    json_patterns = [
        r'\{[\s\S]*\}',
        r'\[[\s\S]*\]'
    ]
    
    for pattern in json_patterns:
        matches = re.findall(pattern, text)
        for match in matches:
            try:
                return json.loads(match)
            except json.JSONDecodeError:
                continue
    
    raise ValueError(f"Não foi possível extrair JSON válido da resposta: {text[:200]}")


def get_required_credentials(integrations):
    """Retorna as credenciais necessárias para as integrações especificadas"""
    required = []
    for integration in integrations:
        integration_lower = integration.lower()
        if integration_lower in INTEGRATION_CREDENTIALS:
            info = INTEGRATION_CREDENTIALS[integration_lower]
            cred_info = {
                "integration": integration_lower,
                "name": info["name"],
                "keys": info["keys"],
                "instructions": info["instructions"],
                "docs_url": info.get("docs_url", ""),
                "note": info.get("note", ""),
                "configured": all(os.environ.get(key) for key in info["keys"]) if info["keys"] else True
            }
            required.append(cred_info)
    return required


def check_credentials_status():
    """Verifica quais credenciais estão configuradas"""
    status = {}
    for integration, info in INTEGRATION_CREDENTIALS.items():
        keys_status = {}
        for key in info["keys"]:
            keys_status[key] = bool(os.environ.get(key))
        status[integration] = {
            "name": info["name"],
            "keys": keys_status,
            "all_configured": all(keys_status.values()) if keys_status else True
        }
    return status


def get_default_intent():
    return {
        "objective": "Não foi possível identificar o objetivo",
        "output_type": "file",
        "output_format": None,
        "integrations": [],
        "needs_credentials": False,
        "required_credentials": [],
        "summary": "Erro ao processar intenção"
    }


def get_default_flow(intent):
    return {
        "name": "Fluxo padrão",
        "description": intent.get("summary", "Fluxo gerado automaticamente"),
        "nodes": [
            {"id": "node_1", "type": "trigger", "name": "Início", "config": {}, "next": ["node_2"]},
            {"id": "node_2", "type": "process", "name": "Processamento", "config": {"action": intent.get("objective", "Processar dados")}, "next": ["node_3"]},
            {"id": "node_3", "type": "output", "name": "Saída", "config": {"format": intent.get("output_format", "json"), "destination": "local"}, "next": []}
        ],
        "connections": [
            {"from": "node_1", "to": "node_2"},
            {"from": "node_2", "to": "node_3"}
        ]
    }


def get_default_validation(approved=True):
    return {
        "approved": approved,
        "errors": [] if approved else ["Erro ao validar fluxo"],
        "warnings": [],
        "score": 80 if approved else 0,
        "recommendation": "Fluxo processado" if approved else "Revisar fluxo"
    }


def detect_integrations_from_prompt(prompt: str) -> list:
    """Detecta integrações com base em palavras-chave no prompt (fallback)"""
    prompt_lower = prompt.lower()
    detected = []
    
    keyword_map = {
        "telegram": ["telegram", "bot telegram", "telegrama"],
        "whatsapp": ["whatsapp", "whats", "zap", "zapzap"],
        "email": ["email", "e-mail", "enviar email", "smtp"],
        "slack": ["slack"],
        "currency_api": ["dólar", "dolar", "euro", "moeda", "cotação", "cotacao", "câmbio", "cambio", "real"],
        "gold_api": ["ouro", "prata", "commodities", "commodity", "gold"],
        "postgresql": ["postgres", "postgresql", "banco de dados", "database", "db"]
    }
    
    for integration, keywords in keyword_map.items():
        for keyword in keywords:
            if keyword in prompt_lower:
                if integration not in detected:
                    detected.append(integration)
                break
    
    return detected


def agent_intent(prompt: str) -> dict:
    available_integrations = list(INTEGRATION_CREDENTIALS.keys())
    detected_by_keywords = detect_integrations_from_prompt(prompt)
    
    system_prompt = f"""Você é o Agente de Intenção AVANÇADO. Analise profundamente o pedido do usuário.

REGRAS CRÍTICAS DE DETECÇÃO:
1. Leia TODO o pedido com atenção - procure por TODOS os serviços, APIs, ações mencionadas
2. Integre COMPLETAMENTE com o prompt - keywords, contexto, implicações
3. Para "buscar", "procurar", "encontrar" + [preço/dados/info] = precisa de integração apropriada
4. Para "enviar", "mandar", "notificar" = precisa de integração de comunicação
5. Sempre liste integrations mesmo que vazio - nunca null

INTEGRAÇÕES DISPONÍVEIS: {available_integrations}

ESTRUTURA JSON OBRIGATÓRIA:
{{
    "objective": "descrição técnica e clara do objetivo",
    "action_type": "busca/transformacao/envio/armazenamento/hibrido",
    "output_type": "file|api|message|database|notification|search",
    "output_format": "txt|json|csv|xml|html|null",
    "integrations": ["lista", "de", "integrações"],
    "needs_credentials": true|false,
    "complexity": "simples|media|complexa",
    "summary": "resumo executivo em uma frase"
}}

EXEMPLOS DE PROMPT -> JSON:
"busque preço de iphone" -> {{"objective": "Buscar preço de iPhone em mercados/APIs", "output_type": "file", "output_format": "json", "integrations": [], "complexity": "media"}}
"envie cotação de dólar pelo Telegram" -> {{"objective": "Buscar cotação USD e enviar via bot Telegram", "output_type": "message", "integrations": ["currency_api", "telegram"], "needs_credentials": true}}
"gere relatório em html" -> {{"objective": "Gerar relatório estruturado em HTML", "output_type": "file", "output_format": "html", "integrations": []}}"""

    user_prompt = f"Analise este pedido e extraia a intenção estruturada:\n\n{prompt}"
    
    try:
        result = call_gemini_json(system_prompt, user_prompt)
        
        # Validar e completar campos obrigatórios
        required_fields = {
            "objective": "Não especificado",
            "output_type": "file",
            "integrations": [],
            "needs_credentials": False,
            "summary": "Processamento de fluxo",
            "action_type": "transformacao",
            "complexity": "media"
        }
        
        for field, default in required_fields.items():
            if field not in result:
                result[field] = default
        
        # Garantir que integrations é sempre lista
        if not isinstance(result.get("integrations"), list):
            result["integrations"] = []
        
        # Combinar integrações detectadas por IA + keywords
        ai_integrations = [i.lower() for i in result.get("integrations", [])]
        all_integrations = list(set(ai_integrations + detected_by_keywords))
        result["integrations"] = all_integrations
        
        # Obter credenciais necessárias
        if all_integrations:
            result["required_credentials"] = get_required_credentials(all_integrations)
            if any(cred["keys"] for cred in result["required_credentials"]):
                result["needs_credentials"] = True
        else:
            result["required_credentials"] = []
        
        logging.info(f"Intent detectada: {result.get('summary')} - Integrações: {all_integrations}")
        return result
        
    except Exception as e:
        logging.error(f"Erro no Agente de Intenção: {e}")
        # Fallback intelligente baseado em keywords
        default = get_default_intent()
        default["objective"] = prompt[:150]
        default["summary"] = f"Processar: {prompt[:60]}"
        default["action_type"] = "hibrido" if detected_by_keywords else "transformacao"
        
        if detected_by_keywords:
            default["integrations"] = detected_by_keywords
            default["required_credentials"] = get_required_credentials(detected_by_keywords)
            default["needs_credentials"] = any(cred["keys"] for cred in default["required_credentials"])
        
        return default


def agent_builder(prompt: str, intent: dict) -> dict:
    system_prompt = """Você é o Agente Construtor AVANÇADO. Gere fluxos complexos e realistas.

ALGORITMO DE CONSTRUÇÃO:
1. Comece com TRIGGER (manual, schedule, webhook, ou event)
2. Adicione PROCESS nodes para executar ações (busca, transformação, integração)
3. Adicione CONDITION nodes se tiver lógica if/else
4. Adicione LOOP nodes se precisar iterar
5. Termine com OUTPUT node (arquivo, mensagem, API, etc)

TIPOS DE NODES DISPONÍVEIS:
- trigger (manual|schedule|webhook|event)
- search (buscar dados de APIs/web)
- transform (transformar/mapear dados)
- condition (if/else branches)
- loop (iteração)
- integration (telegram|email|slack|whatsapp)
- database (CRUD em DB)
- output (arquivo|message|api|notification)

REGRAS:
1. Sempre comece com trigger e termine com output
2. Cada node tem: id, type, name, config, next (lista de próximos)
3. Se integrations lista está vazia, NÃO adicione nodes de integração
4. Crie nodes relevantes para a ação solicitada
5. Mínimo 3 nodes, máximo 10 (complexidade apropriada)

RETORNE:
{
    "name": "nome descritivo",
    "description": "descrição clara",
    "nodes": [...nodes aqui...],
    "connections": [...connections aqui...]
}"""

    action_type = intent.get("action_type", "transformacao")
    complexity = intent.get("complexity", "media")
    integrations = intent.get("integrations", [])
    
    user_prompt = f"""Pedido: {prompt}

Intenção (resumida):
- Objetivo: {intent.get('objective', 'Não especificado')}
- Ação: {action_type}
- Complexidade: {complexity}
- Integrações: {integrations if integrations else 'nenhuma'}
- Output: {intent.get('output_type', 'file')} ({intent.get('output_format', 'n/a')})

Gere um fluxo detalhado com múltiplos nodes apropriados:"""

    try:
        result = call_gemini_json(system_prompt, user_prompt)
        
        # Validar estrutura básica
        if "nodes" not in result or not isinstance(result.get("nodes"), list) or len(result["nodes"]) == 0:
            logging.warning(f"Builder retornou nodes inválido, usando fallback")
            return get_default_flow(intent)
        
        # Validar que há pelo menos trigger e output
        has_trigger = any(n.get("type") == "trigger" for n in result["nodes"])
        has_output = any(n.get("type") == "output" for n in result["nodes"])
        
        if not has_trigger or not has_output:
            logging.warning(f"Builder não gerou trigger ou output, adicionando")
            if not has_trigger:
                result["nodes"].insert(0, {"id": "node_trigger", "type": "trigger", "name": "Início", "config": {}, "next": [result["nodes"][0].get("id", "node_1")]})
            if not has_output:
                last_node = result["nodes"][-1]
                result["nodes"].append({"id": "node_output", "type": "output", "name": "Saída", "config": {}, "next": []})
                if last_node and "next" in last_node:
                    last_node["next"] = ["node_output"]
        
        # Completar campos obrigatórios
        if "name" not in result or not result["name"]:
            result["name"] = intent.get("summary", "Fluxo Automático")[:50]
        if "description" not in result or not result["description"]:
            result["description"] = intent.get("objective", "Fluxo gerado automaticamente")[:200]
        if "connections" not in result:
            result["connections"] = []
        
        logging.info(f"Builder criou fluxo com {len(result['nodes'])} nodes")
        return result
        
    except Exception as e:
        logging.error(f"Erro no Agente Construtor: {e}")
        return get_default_flow(intent)


def agent_architect(prompt: str, intent: dict, flow: dict) -> dict:
    system_prompt = """Você é o Agente Arquiteto INTELIGENTE. Valide com critérios flexíveis mas rigorosos.

VALIDAÇÃO INTELIGENTE:
1. ✅ APROVAÇÃO: Fluxo começa com trigger + termina com output + usa integrações listadas
2. ✅ AVISOS (não reprova): Complexidade alta, muitos nodes, integrações opcionais
3. ❌ REJEIÇÃO: Integração NÃO listada em intent, ciclos infinitos, estrutura quebrada

CHECKLIST DE VALIDAÇÃO:
□ Começa com trigger? (trigger|schedule|webhook|event)
□ Termina com output? (output|message|file|notification)
□ Todas as integrações estão em intent.integrations?
□ Cada node tem id, type, name, config, next?
□ Não há ciclos infinitos?
□ Complexidade apropriada para o objetivo?

SCORING:
- 100: Perfeito, bem estruturado, integrado corretamente
- 80-99: Bom, alguns avisos menores
- 60-79: Aceitável, mas tem problemas
- <60: Rejeitar, problemas críticos

RETORNE:
{
    "approved": true|false,
    "errors": ["lista de erros críticos"],
    "warnings": ["lista de avisos"],
    "score": 85,
    "recommendation": "breve recomendação"
}"""

    integrations_allowed = set(intent.get("integrations", []))
    nodes = flow.get("nodes", [])
    
    user_prompt = f"""Valide este fluxo:

PEDIDO: {prompt}

INTENÇÃO:
- Objetivo: {intent.get('objective')}
- Integrações permitidas: {integrations_allowed if integrations_allowed else 'nenhuma'}
- Tipo de saída: {intent.get('output_type')}

FLUXO A VALIDAR:
{json.dumps(flow, indent=2, ensure_ascii=False)}

Validação rigorosa porém justa:"""

    try:
        result = call_gemini_json(system_prompt, user_prompt)
        
        # Completar campos obrigatórios com inteligência
        if "approved" not in result:
            # Validação automática se IA falhar
            has_trigger = any(n.get("type") == "trigger" for n in nodes)
            has_output = any(n.get("type") == "output" for n in nodes)
            result["approved"] = has_trigger and has_output and len(nodes) >= 2
        
        if "errors" not in result:
            result["errors"] = []
        if "warnings" not in result:
            result["warnings"] = []
        
        # Calcular score inteligentemente
        if "score" not in result:
            if result["approved"]:
                score = 85
                if len(result.get("warnings", [])) > 2:
                    score -= 10
                if len(nodes) > 8:
                    score -= 5
                result["score"] = max(60, min(100, score))
            else:
                score = 40
                if len(result.get("errors", [])) <= 1:
                    score = 55
                result["score"] = score
        
        if "recommendation" not in result:
            if result["approved"]:
                result["recommendation"] = "Fluxo aprovado e pronto para execução" if result["score"] >= 80 else "Fluxo aprovado com ressalvas"
            else:
                first_error = result.get("errors", ["Revisar estrutura do fluxo"])[0]
                result["recommendation"] = f"Revisar: {first_error}"
        
        logging.info(f"Architect score: {result['score']}, approved: {result['approved']}")
        return result
        
    except Exception as e:
        logging.error(f"Erro no Agente Arquiteto: {e}")
        # Fallback inteligente
        has_trigger = any(n.get("type") == "trigger" for n in nodes) if nodes else False
        has_output = any(n.get("type") == "output" for n in nodes) if nodes else False
        approved = has_trigger and has_output and len(nodes) >= 2
        return get_default_validation(approved)


def agent_learning(prompt: str, intent: dict, flow: dict, validation: dict):
    memory = load_memory()
    
    record = {
        "id": len(memory["flows"]) + 1,
        "timestamp": datetime.now().isoformat(),
        "prompt": prompt,
        "intent": intent,
        "flow": flow,
        "approved": validation.get("approved", False),
        "errors": validation.get("errors", []),
        "score": validation.get("score", 0)
    }
    
    memory["flows"].append(record)
    memory["stats"]["total"] += 1
    
    if validation.get("approved", False):
        memory["stats"]["approved"] += 1
    else:
        memory["stats"]["rejected"] += 1
    
    save_memory(memory)
    return record


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/settings")
def settings():
    return render_template("settings.html")


@app.route("/api/configurations", methods=["GET"])
def get_configurations():
    """Retorna todas as configurações salvas"""
    try:
        configs = UserConfiguration.get_all()
        return jsonify([
            {
                "key": c["key"],
                "value": c["value"],
                "integration": c["integration"],
                "updated_at": c["updated_at"]
            }
            for c in configs
        ])
    except Exception as e:
        logging.error(f"Erro ao buscar configurações: {e}")
        return jsonify({"error": "Erro ao acessar banco de dados", "details": str(e)}), 500


@app.route("/api/configurations", methods=["POST"])
def save_configurations():
    """Salva as configurações e aplica como variáveis de ambiente"""
    try:
        data = request.get_json()
        configurations = data.get("configurations", [])
        
        for config in configurations:
            key = config.get("key")
            value = config.get("value")
            integration = config.get("integration", "unknown")
            
            if not key or not value:
                continue
            
            existing = UserConfiguration.get_by_key(key)
            if existing:
                UserConfiguration.update(key, value, integration)
            else:
                UserConfiguration.create(key, value, integration)
            
            os.environ[key] = value
        
        return jsonify({
            "success": True,
            "message": "Configurações salvas com sucesso",
            "count": len(configurations)
        })
        
    except Exception as e:
        logging.error(f"Erro ao salvar configurações: {e}")
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


@app.route("/api/configurations/<key>", methods=["DELETE"])
def delete_configuration(key):
    """Remove uma configuração"""
    try:
        if UserConfiguration.delete(key):
            if key in os.environ:
                del os.environ[key]
            return jsonify({"success": True, "message": "Configuração removida"})
        return jsonify({"success": False, "error": "Configuração não encontrada"}), 404
    except Exception as e:
        logging.error(f"Erro ao remover configuração: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


def load_configurations_to_env():
    """Carrega configurações do banco para variáveis de ambiente na inicialização"""
    try:
        configs = UserConfiguration.get_all()
        for config in configs:
            if config["value"]:
                os.environ[config["key"]] = config["value"]
        logging.info(f"Carregadas {len(configs)} configurações do banco de dados")
    except Exception as e:
        logging.error(f"Erro ao carregar configurações: {e}")


load_configurations_to_env()


@app.route("/generate-flow", methods=["POST"])
def generate_flow():
    try:
        data = request.get_json()
        
        if not data or "prompt" not in data:
            return jsonify({"error": "Campo 'prompt' é obrigatório"}), 400
        
        prompt = data["prompt"].strip()
        
        if not prompt:
            return jsonify({"error": "O prompt não pode estar vazio"}), 400
        
        logging.info(f"Processando prompt: {prompt}")
        
        logging.info("Executando Agente de Intenção...")
        intent = agent_intent(prompt)
        logging.info(f"Intenção: {intent}")
        
        logging.info("Executando Agente Construtor...")
        flow = agent_builder(prompt, intent)
        logging.info(f"Fluxo gerado: {flow}")
        
        logging.info("Executando Agente Arquiteto...")
        validation = agent_architect(prompt, intent, flow)
        logging.info(f"Validação: {validation}")
        
        logging.info("Validando integrações antes de entregar...")
        integrations = intent.get("integrations", [])
        integration_check = validate_integrations(integrations)
        logging.info(f"Validação de integrações: {integration_check}")
        
        if integration_check["warnings"]:
            validation["warnings"] = validation.get("warnings", []) + integration_check["warnings"]
        if integration_check["fixes_applied"]:
            validation["fixes_applied"] = integration_check["fixes_applied"]
        if not integration_check["all_valid"]:
            for detail in integration_check["details"]:
                if detail["status"] == "error":
                    validation["errors"] = validation.get("errors", []) + [detail["message"]]
        
        validation["integration_status"] = integration_check["details"]
        
        logging.info("Executando Agente de Aprendizado...")
        learning_record = agent_learning(prompt, intent, flow, validation)
        
        if validation.get("approved", False):
            return jsonify({
                "status": "approved",
                "intent": intent,
                "flow": flow,
                "validation": {
                    "score": validation.get("score", 100),
                    "warnings": validation.get("warnings", []),
                    "recommendation": validation.get("recommendation", ""),
                    "integration_status": integration_check["details"],
                    "fixes_applied": integration_check.get("fixes_applied", [])
                },
                "record_id": learning_record["id"]
            })
        else:
            return jsonify({
                "status": "rejected",
                "errors": validation.get("errors", []),
                "intent": intent,
                "flow": flow,
                "validation": validation,
                "record_id": learning_record["id"]
            })
    
    except Exception as e:
        logging.error(f"Erro inesperado: {e}")
        return jsonify({"error": f"Erro ao processar: {str(e)}"}), 500


@app.route("/history", methods=["GET"])
def get_history():
    memory = load_memory()
    return jsonify({
        "stats": memory["stats"],
        "recent_flows": memory["flows"][-10:][::-1]
    })


@app.route("/stats", methods=["GET"])
def get_stats():
    memory = load_memory()
    stats = memory["stats"]
    
    approval_rate = 0
    if stats["total"] > 0:
        approval_rate = round((stats["approved"] / stats["total"]) * 100, 2)
    
    return jsonify({
        "total_flows": stats["total"],
        "approved": stats["approved"],
        "rejected": stats["rejected"],
        "approval_rate": approval_rate
    })


@app.route("/health", methods=["GET"])
def health_check():
    return jsonify({"status": "healthy", "timestamp": datetime.now().isoformat()})


@app.route("/credentials", methods=["GET"])
def get_credentials_status():
    status = check_credentials_status()
    return jsonify(status)


@app.route("/integrations", methods=["GET"])
def get_available_integrations():
    integrations = []
    for key, info in INTEGRATION_CREDENTIALS.items():
        integrations.append({
            "id": key,
            "name": info["name"],
            "keys_required": info["keys"],
            "docs_url": info.get("docs_url", ""),
            "note": info.get("note", ""),
            "configured": all(os.environ.get(k) for k in info["keys"]) if info["keys"] else True
        })
    return jsonify(integrations)


@app.route("/execute-flow", methods=["POST"])
def execute_flow():
    try:
        data = request.get_json()
        
        if not data or "flow" not in data:
            return jsonify({"success": False, "error": "Fluxo não fornecido"}), 400
        
        flow = data["flow"]
        intent = data.get("intent", {})
        
        logging.info(f"Executando fluxo: {flow.get('name', 'Sem nome')}")
        
        output_type = intent.get("output_type", "file")
        output_format = intent.get("output_format", "txt")
        
        system_prompt = """Você é um executor de automações. Execute o fluxo descrito e gere o resultado apropriado.

REGRAS:
1. Analise o fluxo e a intenção para entender o que deve ser gerado
2. Gere conteúdo real e útil baseado no objetivo
3. Se for um arquivo, retorne o conteúdo que deve ser salvo
4. Seja criativo e gere conteúdo de qualidade

Retorne um JSON com esta estrutura:
{
    "content": "conteúdo gerado (texto, dados, etc.)",
    "filename": "nome_sugerido.extensao",
    "summary": "resumo do que foi executado"
}"""

        user_prompt = f"""Execute este fluxo:

FLUXO:
{json.dumps(flow, indent=2, ensure_ascii=False)}

INTENÇÃO:
{json.dumps(intent, indent=2, ensure_ascii=False)}

Gere o resultado da execução:"""

        try:
            result = call_gemini_json(system_prompt, user_prompt)
            
            content = result.get("content", "")
            raw_filename = result.get("filename", f"output_{datetime.now().strftime('%Y%m%d_%H%M%S')}.{output_format or 'txt'}")
            summary = result.get("summary", "Execução concluída")
            
            safe_filename = os.path.basename(raw_filename)
            safe_filename = re.sub(r'[^\w\-_\.]', '_', safe_filename)
            if not safe_filename or safe_filename.startswith('.'):
                safe_filename = f"output_{datetime.now().strftime('%Y%m%d_%H%M%S')}.{output_format or 'txt'}"
            
            output_dir = "generated_outputs"
            if not os.path.exists(output_dir):
                os.makedirs(output_dir)
            
            filepath = os.path.join(output_dir, safe_filename)
            
            if isinstance(content, (dict, list)):
                content_str = json.dumps(content, indent=2, ensure_ascii=False)
            else:
                content_str = str(content)
            
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(content_str)
            
            logging.info(f"Arquivo gerado: {filepath}")
            
            preview = content_str[:500] + ('...' if len(content_str) > 500 else '')
            
            return jsonify({
                "success": True,
                "output": f"{summary}\n\nConteúdo gerado:\n{preview}",
                "file_created": filepath,
                "filename": safe_filename
            })
            
        except Exception as e:
            logging.error(f"Erro ao executar fluxo com IA: {e}")
            return jsonify({
                "success": False,
                "error": f"Erro ao gerar conteúdo: {str(e)}"
            }), 500
    
    except Exception as e:
        logging.error(f"Erro inesperado na execução: {e}")
        return jsonify({"success": False, "error": f"Erro ao executar: {str(e)}"}), 500


@app.route("/execute-real", methods=["POST"])
def execute_real():
    """Executa uma automação de verdade com APIs reais"""
    try:
        data = request.get_json()
        
        if not data or "flow" not in data:
            return jsonify({"success": False, "error": "Fluxo não fornecido"}), 400
        
        flow = data["flow"]
        intent = data.get("intent", {})
        integrations = intent.get("integrations", [])
        
        results = []
        output_parts = []
        
        if "currency_api" in integrations:
            rates = fetch_currency_rates()
            if rates["success"]:
                results.append({"type": "currency", "data": rates["data"]})
                output_parts.append("✅ Cotações obtidas com sucesso!")
                for key, value in rates["data"].items():
                    variacao = value["variacao"]
                    seta = "↑" if variacao > 0 else "↓" if variacao < 0 else "→"
                    output_parts.append(f"  • {value['nome']}: R$ {value['cotacao']:.2f} ({seta} {variacao:.2f}%)")
            else:
                results.append({"type": "currency", "error": rates["error"]})
                output_parts.append(f"❌ Erro ao obter cotações: {rates['error']}")
        
        if "telegram" in integrations:
            if not os.environ.get("TELEGRAM_BOT_TOKEN") or not os.environ.get("TELEGRAM_CHAT_ID"):
                output_parts.append("\n⚠️ Telegram não configurado. Configure TELEGRAM_BOT_TOKEN e TELEGRAM_CHAT_ID.")
                results.append({"type": "telegram", "error": "Credenciais não configuradas"})
            else:
                message = format_automation_message({"name": flow.get("name", "Automação")}, results)
                telegram_result = send_telegram_message(message)
                results.append({"type": "telegram", "result": telegram_result})
                if telegram_result["success"]:
                    output_parts.append("\n✅ Mensagem enviada ao Telegram com sucesso!")
                else:
                    output_parts.append(f"\n❌ Erro ao enviar Telegram: {telegram_result['error']}")
        
        if not results:
            output_parts.append("ℹ️ Nenhuma integração executável detectada neste fluxo.")
        
        return jsonify({
            "success": True,
            "output": "\n".join(output_parts),
            "results": results
        })
        
    except Exception as e:
        logging.error(f"Erro na execução real: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/automations", methods=["GET"])
def list_automations():
    """Lista todas as automações"""
    global ACTIVE_AUTOMATIONS
    
    automations_list = []
    for auto_id, automation in ACTIVE_AUTOMATIONS.items():
        job = scheduler.get_job(auto_id)
        automations_list.append({
            "id": auto_id,
            "name": automation.get("name", "Sem nome"),
            "active": job is not None,
            "interval_minutes": automation.get("interval_minutes", 60),
            "last_run": automation.get("last_run"),
            "run_count": automation.get("run_count", 0),
            "integrations": automation.get("intent", {}).get("integrations", []),
            "created_at": automation.get("created_at")
        })
    
    return jsonify(automations_list)


@app.route("/automations", methods=["POST"])
def create_automation():
    """Cria uma nova automação agendada"""
    global ACTIVE_AUTOMATIONS
    
    try:
        data = request.get_json()
        
        if not data or "flow" not in data:
            return jsonify({"success": False, "error": "Fluxo não fornecido"}), 400
        
        flow = data["flow"]
        intent = data.get("intent", {})
        interval_minutes = data.get("interval_minutes", 60)
        auto_start = data.get("auto_start", True)
        
        required_credentials = intent.get("required_credentials", [])
        missing_credentials = []
        for cred in required_credentials:
            for key in cred.get("keys", []):
                if not os.environ.get(key):
                    missing_credentials.append(key)
        
        if missing_credentials:
            return jsonify({
                "success": False,
                "error": "Credenciais não configuradas",
                "missing_credentials": missing_credentials
            }), 400
        
        auto_id = f"auto_{datetime.now().strftime('%Y%m%d%H%M%S')}_{len(ACTIVE_AUTOMATIONS)}"
        
        automation = {
            "id": auto_id,
            "name": flow.get("name", "Automação"),
            "flow": flow,
            "intent": intent,
            "interval_minutes": interval_minutes,
            "created_at": datetime.now().isoformat(),
            "run_count": 0,
            "last_run": None
        }
        
        ACTIVE_AUTOMATIONS[auto_id] = automation
        
        saved_automations = load_automations()
        saved_automations[auto_id] = automation
        save_automations(saved_automations)
        
        if auto_start:
            scheduler.add_job(
                execute_automation_task,
                trigger=IntervalTrigger(minutes=interval_minutes),
                id=auto_id,
                args=[auto_id],
                replace_existing=True
            )
            execute_automation_task(auto_id)
        
        return jsonify({
            "success": True,
            "automation_id": auto_id,
            "message": f"Automação criada e {'iniciada' if auto_start else 'pausada'}",
            "interval_minutes": interval_minutes
        })
        
    except Exception as e:
        logging.error(f"Erro ao criar automação: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/automations/<auto_id>/start", methods=["POST"])
def start_automation(auto_id):
    """Inicia uma automação pausada"""
    global ACTIVE_AUTOMATIONS
    
    if auto_id not in ACTIVE_AUTOMATIONS:
        return jsonify({"success": False, "error": "Automação não encontrada"}), 404
    
    automation = ACTIVE_AUTOMATIONS[auto_id]
    interval = automation.get("interval_minutes", 60)
    
    scheduler.add_job(
        execute_automation_task,
        trigger=IntervalTrigger(minutes=interval),
        id=auto_id,
        args=[auto_id],
        replace_existing=True
    )
    
    execute_automation_task(auto_id)
    
    return jsonify({"success": True, "message": "Automação iniciada"})


@app.route("/automations/<auto_id>/stop", methods=["POST"])
def stop_automation(auto_id):
    """Para uma automação em execução"""
    try:
        scheduler.remove_job(auto_id)
        return jsonify({"success": True, "message": "Automação pausada"})
    except Exception:
        return jsonify({"success": False, "error": "Automação não está em execução"}), 400


@app.route("/automations/<auto_id>", methods=["DELETE"])
def delete_automation(auto_id):
    """Remove uma automação"""
    global ACTIVE_AUTOMATIONS
    
    try:
        scheduler.remove_job(auto_id)
    except Exception:
        pass
    
    if auto_id in ACTIVE_AUTOMATIONS:
        del ACTIVE_AUTOMATIONS[auto_id]
    
    saved_automations = load_automations()
    if auto_id in saved_automations:
        del saved_automations[auto_id]
        save_automations(saved_automations)
    
    return jsonify({"success": True, "message": "Automação removida"})


@app.route("/automations/<auto_id>/run", methods=["POST"])
def run_automation_once(auto_id):
    """Executa uma automação manualmente uma vez"""
    global ACTIVE_AUTOMATIONS
    
    if auto_id not in ACTIVE_AUTOMATIONS:
        return jsonify({"success": False, "error": "Automação não encontrada"}), 404
    
    execute_automation_task(auto_id)
    automation = ACTIVE_AUTOMATIONS[auto_id]
    
    return jsonify({
        "success": True,
        "message": "Automação executada",
        "results": automation.get("last_results", [])
    })


def init_saved_automations():
    """Carrega e inicia automações salvas"""
    global ACTIVE_AUTOMATIONS
    
    saved = load_automations()
    for auto_id, automation in saved.items():
        ACTIVE_AUTOMATIONS[auto_id] = automation
        logging.info(f"Automação carregada: {automation.get('name', auto_id)}")


init_saved_automations()


@app.route("/saved-flows", methods=["GET"])
def list_saved_flows():
    """Lista todos os fluxos salvos"""
    try:
        flows = SavedFlow.get_all()
        return jsonify([SavedFlow.to_dict(flow) for flow in flows])
    except Exception as e:
        logging.error(f"Erro ao listar fluxos salvos: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/saved-flows", methods=["POST"])
def save_flow():
    """Salva um fluxo para execução futura"""
    try:
        data = request.get_json()
        
        if not data or "flow" not in data:
            return jsonify({"success": False, "error": "Fluxo não fornecido"}), 400
        
        flow = data["flow"]
        intent = data.get("intent", {})
        prompt = data.get("prompt", "")
        validation_score = data.get("validation_score", 0)
        
        if not isinstance(flow, dict):
            return jsonify({"success": False, "error": "Fluxo deve ser um objeto JSON válido"}), 400
        
        if "nodes" not in flow or not isinstance(flow.get("nodes"), list):
            return jsonify({"success": False, "error": "Fluxo deve conter uma lista de nodes"}), 400
        
        try:
            flow_json = json.dumps(flow, ensure_ascii=False)
            intent_json = json.dumps(intent, ensure_ascii=False)
        except (TypeError, ValueError) as e:
            return jsonify({"success": False, "error": f"Erro ao serializar dados: {str(e)}"}), 400
        
        flow_id = SavedFlow.create(
            name=flow.get("name", "Fluxo Sem Nome"),
            description=flow.get("description", intent.get("summary", "")),
            prompt=prompt if prompt else intent.get("objective", ""),
            flow_data=flow_json,
            intent_data=intent_json,
            validation_score=validation_score if isinstance(validation_score, int) else 0
        )
        
        saved_flow = SavedFlow.get_by_id(flow_id)
        
        return jsonify({
            "success": True,
            "message": "Fluxo salvo com sucesso",
            "flow_id": flow_id,
            "flow": SavedFlow.to_dict(saved_flow)
        })
        
    except Exception as e:
        logging.error(f"Erro ao salvar fluxo: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/saved-flows/<int:flow_id>", methods=["GET"])
def get_saved_flow(flow_id):
    """Obtém um fluxo salvo específico"""
    try:
        flow = SavedFlow.get_by_id(flow_id)
        if not flow:
            return jsonify({"error": "Fluxo não encontrado"}), 404
        return jsonify(SavedFlow.to_dict(flow))
    except Exception as e:
        logging.error(f"Erro ao obter fluxo: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/saved-flows/<int:flow_id>", methods=["DELETE"])
def delete_saved_flow(flow_id):
    """Remove um fluxo salvo"""
    try:
        if not SavedFlow.delete(flow_id):
            return jsonify({"success": False, "error": "Fluxo não encontrado"}), 404
        
        return jsonify({"success": True, "message": "Fluxo removido com sucesso"})
        
    except Exception as e:
        logging.error(f"Erro ao remover fluxo: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/saved-flows/<int:flow_id>/execute", methods=["POST"])
def execute_saved_flow(flow_id):
    """Executa um fluxo salvo"""
    try:
        saved_flow = SavedFlow.get_by_id(flow_id)
        if not saved_flow:
            return jsonify({"success": False, "error": "Fluxo não encontrado"}), 404
        
        flow = json.loads(saved_flow["flow_data"]) if saved_flow["flow_data"] else {}
        intent = json.loads(saved_flow["intent_data"]) if saved_flow["intent_data"] else {}
        integrations = intent.get("integrations", [])
        
        results = []
        output_parts = []
        
        if "currency_api" in integrations:
            rates = fetch_currency_rates()
            if rates["success"]:
                results.append({"type": "currency", "data": rates["data"]})
                output_parts.append("✅ Cotações obtidas com sucesso!")
                for key, value in rates["data"].items():
                    variacao = value["variacao"]
                    seta = "↑" if variacao > 0 else "↓" if variacao < 0 else "→"
                    output_parts.append(f"  • {value['nome']}: R$ {value['cotacao']:.2f} ({seta} {variacao:.2f}%)")
            else:
                results.append({"type": "currency", "error": rates["error"]})
                output_parts.append(f"❌ Erro ao obter cotações: {rates['error']}")
        
        if "telegram" in integrations:
            if not os.environ.get("TELEGRAM_BOT_TOKEN") or not os.environ.get("TELEGRAM_CHAT_ID"):
                output_parts.append("\n⚠️ Telegram não configurado. Configure TELEGRAM_BOT_TOKEN e TELEGRAM_CHAT_ID.")
                results.append({"type": "telegram", "error": "Credenciais não configuradas"})
            else:
                message = format_automation_message({"name": flow.get("name", "Automação")}, results)
                telegram_result = send_telegram_message(message)
                results.append({"type": "telegram", "result": telegram_result})
                if telegram_result["success"]:
                    output_parts.append("\n✅ Mensagem enviada ao Telegram com sucesso!")
                else:
                    output_parts.append(f"\n❌ Erro ao enviar Telegram: {telegram_result['error']}")
        
        if not integrations or (not results and "currency_api" not in integrations and "telegram" not in integrations):
            output_type = intent.get("output_type", "file")
            output_format = intent.get("output_format", "txt")
            
            system_prompt = """Você é um executor de automações. Execute o fluxo descrito e gere o resultado apropriado.
            
REGRAS:
1. Analise o fluxo e a intenção para entender o que deve ser gerado
2. Gere conteúdo real e útil baseado no objetivo
3. Se for um arquivo, retorne o conteúdo que deve ser salvo
4. Seja criativo e gere conteúdo de qualidade

Retorne um JSON com esta estrutura:
{
    "content": "conteúdo gerado (texto, dados, etc.)",
    "filename": "nome_sugerido.extensao",
    "summary": "resumo do que foi executado"
}"""

            user_prompt = f"""Execute este fluxo:

FLUXO:
{json.dumps(flow, indent=2, ensure_ascii=False)}

INTENÇÃO:
{json.dumps(intent, indent=2, ensure_ascii=False)}

Gere o resultado da execução:"""

            try:
                result = call_gemini_json(system_prompt, user_prompt)
                
                content = result.get("content", "")
                raw_filename = result.get("filename", f"output_{datetime.now().strftime('%Y%m%d_%H%M%S')}.{output_format or 'txt'}")
                summary = result.get("summary", "Execução concluída")
                
                safe_filename = os.path.basename(raw_filename)
                safe_filename = re.sub(r'[^\w\-_\.]', '_', safe_filename)
                if not safe_filename or safe_filename.startswith('.'):
                    safe_filename = f"output_{datetime.now().strftime('%Y%m%d_%H%M%S')}.{output_format or 'txt'}"
                
                output_dir = "generated_outputs"
                if not os.path.exists(output_dir):
                    os.makedirs(output_dir)
                
                filepath = os.path.join(output_dir, safe_filename)
                
                if isinstance(content, (dict, list)):
                    content_str = json.dumps(content, indent=2, ensure_ascii=False)
                else:
                    content_str = str(content)
                
                with open(filepath, "w", encoding="utf-8") as f:
                    f.write(content_str)
                
                output_parts.append(f"✅ {summary}")
                output_parts.append(f"📁 Arquivo gerado: {filepath}")
                
                preview = content_str[:300] + ('...' if len(content_str) > 300 else '')
                output_parts.append(f"\nConteúdo:\n{preview}")
                
                results.append({"type": "file", "filepath": filepath, "content_preview": preview})
                
            except Exception as e:
                output_parts.append(f"❌ Erro ao executar fluxo: {str(e)}")
                results.append({"type": "error", "error": str(e)})
        
        if not results:
            output_parts.append("ℹ️ Nenhuma ação executável detectada neste fluxo.")
        
        new_count = (saved_flow["execution_count"] or 0) + 1
        SavedFlow.update(flow_id, last_executed=datetime.utcnow().isoformat(), execution_count=new_count)
        
        return jsonify({
            "success": True,
            "output": "\n".join(output_parts),
            "results": results,
            "execution_count": new_count
        })
        
    except Exception as e:
        logging.error(f"Erro ao executar fluxo salvo: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/saved-flows/<int:flow_id>/schedule", methods=["POST"])
def schedule_saved_flow(flow_id):
    """Agenda um fluxo salvo para execução automática"""
    global ACTIVE_AUTOMATIONS
    
    try:
        saved_flow = SavedFlow.get_by_id(flow_id)
        if not saved_flow:
            return jsonify({"success": False, "error": "Fluxo não encontrado"}), 404
        
        data = request.get_json() or {}
        interval_minutes = data.get("interval_minutes", 60)
        auto_start = data.get("auto_start", True)
        
        flow = json.loads(saved_flow["flow_data"]) if saved_flow["flow_data"] else {}
        intent = json.loads(saved_flow["intent_data"]) if saved_flow["intent_data"] else {}
        
        required_credentials = intent.get("required_credentials", [])
        missing_credentials = []
        for cred in required_credentials:
            for key in cred.get("keys", []):
                if not os.environ.get(key):
                    missing_credentials.append(key)
        
        if missing_credentials:
            return jsonify({
                "success": False,
                "error": "Credenciais não configuradas",
                "missing_credentials": missing_credentials
            }), 400
        
        auto_id = f"auto_{datetime.now().strftime('%Y%m%d%H%M%S')}_{len(ACTIVE_AUTOMATIONS)}"
        
        automation = {
            "id": auto_id,
            "name": saved_flow["name"],
            "flow": flow,
            "intent": intent,
            "interval_minutes": interval_minutes,
            "created_at": datetime.now().isoformat(),
            "run_count": 0,
            "last_run": None,
            "saved_flow_id": flow_id
        }
        
        ACTIVE_AUTOMATIONS[auto_id] = automation
        
        saved_automations = load_automations()
        saved_automations[auto_id] = automation
        save_automations(saved_automations)
        
        if auto_start:
            scheduler.add_job(
                execute_automation_task,
                trigger=IntervalTrigger(minutes=interval_minutes),
                id=auto_id,
                args=[auto_id],
                replace_existing=True
            )
            execute_automation_task(auto_id)
        
        return jsonify({
            "success": True,
            "automation_id": auto_id,
            "message": f"Fluxo agendado e {'iniciado' if auto_start else 'pausado'}",
            "interval_minutes": interval_minutes
        })
        
    except Exception as e:
        logging.error(f"Erro ao agendar fluxo: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


NODE_TYPES = {
    "trigger": {
        "manual": {"name": "Trigger Manual", "icon": "fa-hand-pointer", "description": "Inicia o fluxo manualmente"},
        "schedule": {"name": "Agendamento", "icon": "fa-clock", "description": "Inicia em horários programados"},
        "webhook": {"name": "Webhook", "icon": "fa-bolt", "description": "Inicia quando recebe uma requisição"},
        "event": {"name": "Evento", "icon": "fa-bell", "description": "Inicia quando um evento ocorre"}
    },
    "action": {
        "telegram": {"name": "Telegram", "icon": "fa-paper-plane", "description": "Envia mensagem via Telegram"},
        "email": {"name": "Email", "icon": "fa-envelope", "description": "Envia email via SMTP"},
        "slack": {"name": "Slack", "icon": "fa-slack", "description": "Envia mensagem no Slack"},
        "whatsapp": {"name": "WhatsApp", "icon": "fa-whatsapp", "description": "Envia mensagem via WhatsApp"},
        "http": {"name": "HTTP Request", "icon": "fa-globe", "description": "Faz requisição HTTP"},
        "database": {"name": "Banco de Dados", "icon": "fa-database", "description": "Executa query no banco"}
    },
    "data": {
        "currency": {"name": "Cotações", "icon": "fa-dollar-sign", "description": "Busca cotações de moedas"},
        "transform": {"name": "Transformar", "icon": "fa-code", "description": "Transforma dados"},
        "filter": {"name": "Filtrar", "icon": "fa-filter", "description": "Filtra dados"},
        "merge": {"name": "Combinar", "icon": "fa-code-merge", "description": "Combina dados de múltiplas fontes"},
        "split": {"name": "Dividir", "icon": "fa-code-branch", "description": "Divide dados em múltiplas saídas"}
    },
    "flow": {
        "condition": {"name": "Condição (IF)", "icon": "fa-code-branch", "description": "Executa branch baseado em condição"},
        "switch": {"name": "Switch", "icon": "fa-route", "description": "Múltiplas condições"},
        "loop": {"name": "Loop", "icon": "fa-rotate", "description": "Repete ações N vezes"},
        "foreach": {"name": "Para Cada", "icon": "fa-list", "description": "Itera sobre uma lista"},
        "wait": {"name": "Aguardar", "icon": "fa-hourglass-half", "description": "Pausa a execução"},
        "error": {"name": "Tratamento de Erro", "icon": "fa-triangle-exclamation", "description": "Captura erros"}
    },
    "output": {
        "file": {"name": "Arquivo", "icon": "fa-file", "description": "Salva em arquivo"},
        "response": {"name": "Resposta", "icon": "fa-reply", "description": "Retorna resposta"},
        "log": {"name": "Log", "icon": "fa-terminal", "description": "Registra no log"}
    },
    "ai": {
        "gemini": {"name": "Gemini AI", "icon": "fa-robot", "description": "Processa com IA Gemini"},
        "prompt": {"name": "Prompt AI", "icon": "fa-comments", "description": "Gera texto com IA"}
    }
}


@app.route("/editor")
def editor_list():
    """Lista todos os projetos de workflow"""
    projects = WorkflowProject.get_all()
    return render_template("editor_list.html", projects=projects)


@app.route("/editor/<int:project_id>")
def editor(project_id):
    """Abre o editor visual para um projeto específico"""
    project = WorkflowProject.get_by_id(project_id)
    if not project:
        return "Projeto não encontrado", 404
    return render_template("editor.html", project=project, node_types=NODE_TYPES)


@app.route("/api/projects", methods=["GET"])
def api_get_projects():
    """Lista todos os projetos"""
    try:
        projects = WorkflowProject.get_all()
        return jsonify({
            "success": True,
            "projects": [WorkflowProject.to_dict(p) for p in projects]
        })
    except Exception as e:
        logging.error(f"Erro ao listar projetos: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/projects", methods=["POST"])
def api_create_project():
    """Cria um novo projeto"""
    try:
        data = request.get_json() or {}
        
        project_id = WorkflowProject.create(
            name=data.get("name", "Novo Projeto"),
            description=data.get("description", "")
        )
        
        project = WorkflowProject.get_by_id(project_id)
        
        return jsonify({
            "success": True,
            "project": WorkflowProject.to_dict(project)
        })
    except Exception as e:
        logging.error(f"Erro ao criar projeto: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/projects/import-flow", methods=["POST"])
def api_import_flow():
    """Importa um fluxo gerado pela IA para o editor visual"""
    try:
        data = request.get_json()
        if not data or "flow" not in data:
            return jsonify({"success": False, "error": "Fluxo não fornecido"}), 400
        
        flow = data["flow"]
        intent = data.get("intent", {})
        
        project_id = WorkflowProject.create(
            name=flow.get("name", "Fluxo Importado"),
            description=flow.get("description", intent.get("summary", "Fluxo gerado pela IA"))
        )
        
        nodes = flow.get("nodes", [])
        node_positions = {}
        
        def get_category_from_type(node_type):
            """Mapeia tipo de node para categoria do editor"""
            node_type_lower = node_type.lower() if node_type else "manual"
            category_map = {
                "trigger": "trigger",
                "manual": "trigger",
                "schedule": "trigger",
                "webhook": "trigger",
                "event": "trigger",
                "start": "trigger",
                "cron": "trigger",
                "interval": "trigger",
                "process": "data",
                "search": "data",
                "transform": "data",
                "filter": "data",
                "merge": "data",
                "split": "data",
                "currency": "data",
                "api": "data",
                "fetch": "data",
                "get": "data",
                "read": "data",
                "condition": "flow",
                "if": "flow",
                "decision": "flow",
                "branch": "flow",
                "loop": "flow",
                "foreach": "flow",
                "for": "flow",
                "while": "flow",
                "wait": "flow",
                "delay": "flow",
                "switch": "flow",
                "error": "flow",
                "try": "flow",
                "catch": "flow",
                "telegram": "action",
                "email": "action",
                "mail": "action",
                "smtp": "action",
                "slack": "action",
                "whatsapp": "action",
                "http": "action",
                "httprequest": "action",
                "request": "action",
                "post": "action",
                "put": "action",
                "delete": "action",
                "patch": "action",
                "database": "action",
                "db": "action",
                "sql": "action",
                "query": "action",
                "integration": "action",
                "send": "action",
                "notify": "action",
                "notification": "action",
                "output": "output",
                "file": "output",
                "save": "output",
                "write": "output",
                "response": "output",
                "return": "output",
                "result": "output",
                "log": "output",
                "print": "output",
                "gemini": "ai",
                "prompt": "ai",
                "ai": "ai",
                "gpt": "ai",
                "openai": "ai",
                "llm": "ai",
                "generate": "ai",
                "analyze": "ai"
            }
            return category_map.get(node_type_lower, "data")
        
        def get_editor_type(node_type):
            """Converte tipo genérico para tipo do editor"""
            node_type_lower = node_type.lower() if node_type else "manual"
            type_map = {
                "trigger": "manual",
                "start": "manual",
                "process": "transform",
                "output": "response",
                "result": "response",
                "return": "response",
                "integration": "http",
                "httprequest": "http",
                "request": "http",
                "api": "http",
                "fetch": "http",
                "get": "http",
                "post": "http",
                "put": "http",
                "delete": "http",
                "patch": "http",
                "if": "condition",
                "decision": "condition",
                "branch": "condition",
                "for": "loop",
                "while": "loop",
                "delay": "wait",
                "mail": "email",
                "smtp": "email",
                "db": "database",
                "sql": "database",
                "query": "database",
                "send": "telegram",
                "notify": "telegram",
                "notification": "telegram",
                "save": "file",
                "write": "file",
                "print": "log",
                "ai": "gemini",
                "gpt": "gemini",
                "openai": "gemini",
                "llm": "gemini",
                "generate": "gemini",
                "analyze": "gemini",
                "cron": "schedule",
                "interval": "schedule",
                "try": "error",
                "catch": "error",
                "read": "currency"
            }
            return type_map.get(node_type_lower, node_type_lower)
        
        for i, node in enumerate(nodes):
            node_id = node.get("id", f"node_{i}")
            node_type = node.get("type", "manual")
            editor_type = get_editor_type(node_type)
            category = get_category_from_type(node_type)
            
            pos_x = 150 + (i * 250)
            pos_y = 200
            node_positions[node_id] = {"x": pos_x, "y": pos_y}
            
            WorkflowNode.create(
                project_id=project_id,
                node_id=node_id,
                name=node.get("name", f"Node {i+1}"),
                node_type=editor_type,
                node_category=category,
                position_x=pos_x,
                position_y=pos_y,
                config=node.get("config", {})
            )
        
        connections = flow.get("connections", [])
        for i, conn in enumerate(connections):
            source = conn.get("from")
            target = conn.get("to")
            if source and target:
                WorkflowEdge.create(
                    project_id=project_id,
                    edge_id=f"edge_{i}",
                    source_node_id=source,
                    target_node_id=target,
                    source_port="output",
                    target_port="input",
                    label=conn.get("label")
                )
        
        for node in nodes:
            node_id = node.get("id")
            next_nodes = node.get("next", [])
            for j, next_id in enumerate(next_nodes):
                if next_id and node_id:
                    from database import get_db
                    with get_db() as conn:
                        cursor = conn.cursor()
                        cursor.execute(
                            "SELECT id FROM workflow_edges WHERE project_id = ? AND source_node_id = ? AND target_node_id = ?",
                            (project_id, node_id, next_id)
                        )
                        if not cursor.fetchone():
                            WorkflowEdge.create(
                                project_id=project_id,
                                edge_id=f"edge_next_{node_id}_{j}",
                                source_node_id=node_id,
                                target_node_id=next_id,
                                source_port="output",
                                target_port="input"
                            )
        
        project = WorkflowProject.get_by_id(project_id)
        
        logging.info(f"Fluxo importado com sucesso: projeto {project_id} com {len(nodes)} nodes")
        
        return jsonify({
            "success": True,
            "project_id": project_id,
            "project": WorkflowProject.to_dict(project),
            "message": f"Fluxo importado com {len(nodes)} nodes"
        })
        
    except Exception as e:
        logging.error(f"Erro ao importar fluxo: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/projects/<int:project_id>", methods=["GET"])
def api_get_project(project_id):
    """Retorna um projeto específico"""
    try:
        project = WorkflowProject.get_by_id(project_id)
        if not project:
            return jsonify({"success": False, "error": "Projeto não encontrado"}), 404
        
        return jsonify({
            "success": True,
            "project": WorkflowProject.to_dict(project)
        })
    except Exception as e:
        logging.error(f"Erro ao buscar projeto: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/projects/<int:project_id>", methods=["PATCH"])
def api_update_project(project_id):
    """Atualiza um projeto"""
    try:
        project = WorkflowProject.get_by_id(project_id)
        if not project:
            return jsonify({"success": False, "error": "Projeto não encontrado"}), 404
        
        data = request.get_json() or {}
        
        update_data = {}
        if "name" in data:
            update_data["name"] = data["name"]
        if "description" in data:
            update_data["description"] = data["description"]
        if "canvas_zoom" in data:
            update_data["canvas_zoom"] = data["canvas_zoom"]
        if "canvas_offset_x" in data:
            update_data["canvas_offset_x"] = data["canvas_offset_x"]
        if "canvas_offset_y" in data:
            update_data["canvas_offset_y"] = data["canvas_offset_y"]
        
        if update_data:
            WorkflowProject.update(project_id, **update_data)
        
        project = WorkflowProject.get_by_id(project_id)
        
        return jsonify({
            "success": True,
            "project": WorkflowProject.to_dict(project)
        })
    except Exception as e:
        logging.error(f"Erro ao atualizar projeto: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/projects/<int:project_id>", methods=["DELETE"])
def api_delete_project(project_id):
    """Deleta um projeto"""
    try:
        if not WorkflowProject.delete(project_id):
            return jsonify({"success": False, "error": "Projeto não encontrado"}), 404
        
        return jsonify({"success": True, "message": "Projeto deletado"})
    except Exception as e:
        logging.error(f"Erro ao deletar projeto: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/projects/<int:project_id>/nodes", methods=["POST"])
def api_create_node(project_id):
    """Cria um novo node"""
    try:
        project = WorkflowProject.get_by_id(project_id)
        if not project:
            return jsonify({"success": False, "error": "Projeto não encontrado"}), 404
        
        data = request.get_json()
        if not data:
            return jsonify({"success": False, "error": "Dados não fornecidos"}), 400
        
        node_id_value = data.get("node_id", f"node_{datetime.now().strftime('%Y%m%d%H%M%S%f')}")
        
        node_db_id = WorkflowNode.create(
            project_id=project_id,
            node_id=node_id_value,
            name=data.get("name", "Novo Node"),
            node_type=data.get("node_type", "manual"),
            node_category=data.get("node_category", "trigger"),
            position_x=data.get("position_x", 100),
            position_y=data.get("position_y", 100),
            config=data.get("config", {})
        )
        
        node = WorkflowNode.get_by_id(node_db_id)
        
        return jsonify({
            "success": True,
            "node": WorkflowNode.to_dict(node)
        })
    except Exception as e:
        logging.error(f"Erro ao criar node: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/projects/<int:project_id>/nodes/<string:node_id>", methods=["PATCH"])
def api_update_node(project_id, node_id):
    """Atualiza um node"""
    try:
        node = WorkflowNode.get_by_node_id(project_id, node_id)
        if not node:
            return jsonify({"success": False, "error": "Node não encontrado"}), 404
        
        data = request.get_json() or {}
        
        update_data = {}
        if "name" in data:
            update_data["name"] = data["name"]
        if "position_x" in data:
            update_data["position_x"] = data["position_x"]
        if "position_y" in data:
            update_data["position_y"] = data["position_y"]
        if "config" in data:
            update_data["config"] = data["config"]
        if "is_enabled" in data:
            update_data["is_enabled"] = 1 if data["is_enabled"] else 0
        
        if update_data:
            WorkflowNode.update(node["id"], **update_data)
        
        updated_node = WorkflowNode.get_by_id(node["id"])
        
        return jsonify({
            "success": True,
            "node": WorkflowNode.to_dict(updated_node)
        })
    except Exception as e:
        logging.error(f"Erro ao atualizar node: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/projects/<int:project_id>/nodes/<string:node_id>", methods=["DELETE"])
def api_delete_node(project_id, node_id):
    """Deleta um node e suas conexões"""
    try:
        node = WorkflowNode.get_by_node_id(project_id, node_id)
        if not node:
            return jsonify({"success": False, "error": "Node não encontrado"}), 404
        
        from database import get_db
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "DELETE FROM workflow_edges WHERE project_id = ? AND (source_node_id = ? OR target_node_id = ?)",
                (project_id, node_id, node_id)
            )
        
        WorkflowNode.delete(node["id"])
        
        return jsonify({"success": True, "message": "Node deletado"})
    except Exception as e:
        logging.error(f"Erro ao deletar node: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/projects/<int:project_id>/edges", methods=["POST"])
def api_create_edge(project_id):
    """Cria uma nova conexão entre nodes"""
    try:
        project = WorkflowProject.get_by_id(project_id)
        if not project:
            return jsonify({"success": False, "error": "Projeto não encontrado"}), 404
        
        data = request.get_json()
        if not data:
            return jsonify({"success": False, "error": "Dados não fornecidos"}), 400
        
        from database import get_db
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT id FROM workflow_edges WHERE project_id = ? AND source_node_id = ? AND target_node_id = ?",
                (project_id, data.get("source_node_id"), data.get("target_node_id"))
            )
            existing = cursor.fetchone()
        
        if existing:
            return jsonify({"success": False, "error": "Conexão já existe"}), 400
        
        edge_id_value = data.get("edge_id", f"edge_{datetime.now().strftime('%Y%m%d%H%M%S%f')}")
        
        edge_db_id = WorkflowEdge.create(
            project_id=project_id,
            edge_id=edge_id_value,
            source_node_id=data.get("source_node_id"),
            target_node_id=data.get("target_node_id"),
            source_port=data.get("source_port", "output"),
            target_port=data.get("target_port", "input"),
            label=data.get("label")
        )
        
        edge = WorkflowEdge.get_by_id(edge_db_id)
        
        return jsonify({
            "success": True,
            "edge": WorkflowEdge.to_dict(edge)
        })
    except Exception as e:
        logging.error(f"Erro ao criar conexão: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/projects/<int:project_id>/edges/<string:edge_id>", methods=["DELETE"])
def api_delete_edge(project_id, edge_id):
    """Deleta uma conexão"""
    try:
        from database import get_db
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT id FROM workflow_edges WHERE project_id = ? AND edge_id = ?",
                (project_id, edge_id)
            )
            edge = cursor.fetchone()
            
            if not edge:
                return jsonify({"success": False, "error": "Conexão não encontrada"}), 404
            
            WorkflowEdge.delete(edge["id"])
        
        return jsonify({"success": True, "message": "Conexão deletada"})
    except Exception as e:
        logging.error(f"Erro ao deletar conexão: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/projects/<int:project_id>/execute", methods=["POST"])
def api_execute_project(project_id):
    """Executa um projeto de workflow"""
    try:
        project = WorkflowProject.get_by_id(project_id)
        if not project:
            return jsonify({"success": False, "error": "Projeto não encontrado"}), 404
        
        flow = WorkflowProject.to_flow_json(project_id)
        
        nodes = WorkflowProject.get_nodes(project_id)
        integrations = []
        for node in nodes:
            if node["node_type"] in ["telegram", "email", "slack", "whatsapp"]:
                integrations.append(node["node_type"])
            elif node["node_type"] == "currency":
                integrations.append("currency_api")
        
        intent = {
            "objective": f"Executar workflow: {project['name']}",
            "integrations": list(set(integrations)),
            "output_type": "response"
        }
        
        results = []
        output_parts = []
        
        sorted_nodes = sorted(nodes, key=lambda n: n["position_x"])
        
        for node in sorted_nodes:
            if not node["is_enabled"]:
                continue
            
            config = json.loads(node["config"]) if node["config"] else {}
            
            if node["node_type"] == "currency":
                rates = fetch_currency_rates()
                if rates["success"]:
                    results.append({"node": node["name"], "type": "currency", "data": rates["data"]})
                    output_parts.append(f"[{node['name']}] Cotações obtidas com sucesso")
                    for key, value in rates["data"].items():
                        output_parts.append(f"  {value['nome']}: R$ {value['cotacao']:.2f}")
                else:
                    output_parts.append(f"[{node['name']}] Erro: {rates.get('error')}")
            
            elif node["node_type"] == "telegram":
                message = config.get("message", f"Executando: {project['name']}")
                if os.environ.get("TELEGRAM_BOT_TOKEN") and os.environ.get("TELEGRAM_CHAT_ID"):
                    result = send_telegram_message(message)
                    results.append({"node": node["name"], "type": "telegram", "result": result})
                    if result["success"]:
                        output_parts.append(f"[{node['name']}] Mensagem enviada ao Telegram")
                    else:
                        output_parts.append(f"[{node['name']}] Erro Telegram: {result.get('error')}")
                else:
                    output_parts.append(f"[{node['name']}] Telegram não configurado")
            
            elif node["node_type"] == "loop":
                loop_count = config.get("count", 3)
                loop_results = []
                for i in range(loop_count):
                    loop_results.append(f"Iteração {i + 1}")
                results.append({"node": node["name"], "type": "loop", "iterations": loop_count, "results": loop_results})
                output_parts.append(f"[{node['name']}] Loop executado {loop_count} vezes")
            
            elif node["node_type"] == "condition":
                condition = config.get("condition", "true")
                output_parts.append(f"[{node['name']}] Condição avaliada: {condition}")
                results.append({"node": node["name"], "type": "condition", "result": True})
            
            elif node["node_type"] == "wait":
                wait_seconds = config.get("seconds", 1)
                output_parts.append(f"[{node['name']}] Aguardando {wait_seconds}s")
                results.append({"node": node["name"], "type": "wait", "seconds": wait_seconds})
            
            elif node["node_type"] == "log":
                log_message = config.get("message", "Log entry")
                logging.info(f"[Workflow {project['name']}] {log_message}")
                output_parts.append(f"[{node['name']}] {log_message}")
                results.append({"node": node["name"], "type": "log", "message": log_message})
            
            else:
                output_parts.append(f"[{node['name']}] Executado ({node['node_type']})")
                results.append({"node": node["name"], "type": node["node_type"], "status": "executed"})
        
        new_count = (project["execution_count"] or 0) + 1
        WorkflowProject.update(project_id, last_executed=datetime.now().isoformat(), execution_count=new_count)
        
        return jsonify({
            "success": True,
            "output": "\n".join(output_parts),
            "results": results,
            "flow": flow
        })
        
    except Exception as e:
        logging.error(f"Erro ao executar projeto: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/node-types", methods=["GET"])
def api_get_node_types():
    """Retorna todos os tipos de nodes disponíveis"""
    return jsonify({
        "success": True,
        "node_types": NODE_TYPES
    })
