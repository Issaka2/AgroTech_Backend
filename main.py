from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import httpx
import os
from dotenv import load_dotenv
from supabase import create_client, Client
from groq import Groq

load_dotenv()

app = FastAPI(title="AgroNiger API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Clients initialisés au démarrage de l'app (pas au niveau module)
supabase: Client = None
groq_client: Groq = None

@app.on_event("startup")
async def startup():
    global supabase, groq_client
    supabase = create_client(
        os.getenv("SUPABASE_URL"),
        os.getenv("SUPABASE_KEY")
    )
    groq_client = Groq(api_key=os.getenv("GROQ_API_KEY"))

WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN")
WHATSAPP_PHONE_ID = os.getenv("WHATSAPP_PHONE_ID")
VERIFY_TOKEN = os.getenv("VERIFY_TOKEN", "agroniger_webhook_2026")


# ─── Vérification webhook Meta ──────────────────────────────────────────────
@app.get("/webhook")
async def verify_webhook(request: Request):
    params = dict(request.query_params)
    if (
        params.get("hub.mode") == "subscribe"
        and params.get("hub.verify_token") == VERIFY_TOKEN
    ):
        return int(params["hub.challenge"])
    raise HTTPException(status_code=403, detail="Token invalide")


# ─── Réception des messages WhatsApp ────────────────────────────────────────
@app.post("/webhook")
async def receive_message(request: Request):
    body = await request.json()

    try:
        entry = body["entry"][0]["changes"][0]["value"]
        message = entry["messages"][0]
        phone = message["from"]
        texte = message["text"]["body"]
    except (KeyError, IndexError):
        return {"status": "ignored"}

    agent_result = supabase.table("agents").select("*").eq("telephone", f"+{phone}").single().execute()
    agent = agent_result.data

    conseil = generer_conseil(texte, agent)

    supabase.table("messages_whatsapp").insert({
        "agent_id": agent["id"] if agent else None,
        "contenu_brut": texte,
        "reponse_ia": conseil,
        "region": agent["region"] if agent else None,
        "statut": "traité",
        "type_message": detecter_type(texte),
    }).execute()

    await envoyer_whatsapp(phone, conseil)
    return {"status": "ok"}


# ─── Générer conseil avec Groq ───────────────────────────────────────────────
def generer_conseil(message: str, agent: dict | None) -> str:
    region = agent["region"] if agent else "Niger"
    ong = agent["ong_nom"] if agent else "une ONG agricole"

    response = groq_client.chat.completions.create(
        model="llama-3.1-8b-instant",
        max_tokens=400,
        messages=[{
            "role": "user",
            "content": f"""Tu es un expert agricole au Niger travaillant avec {ong} dans la région de {region}.
Un agent terrain t'envoie ce message : "{message}"

Réponds en français avec :
1. Un conseil agricole précis adapté au Niger (climat sahélien, cultures : mil, sorgho, niébé, oignon, arachide)
2. Une action concrète à faire maintenant
3. Maximum 3 phrases courtes

Sois direct et pratique."""
        }]
    )
    return response.choices[0].message.content


# ─── Envoyer message WhatsApp ────────────────────────────────────────────────
async def envoyer_whatsapp(phone: str, message: str):
    url = f"https://graph.facebook.com/v19.0/{WHATSAPP_PHONE_ID}/messages"
    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json",
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": phone,
        "type": "text",
        "text": {"body": message},
    }
    async with httpx.AsyncClient() as client:
        await client.post(url, json=payload, headers=headers)


# ─── Détecter le type de message ─────────────────────────────────────────────
def detecter_type(texte: str) -> str:
    texte_lower = texte.lower()
    if any(w in texte_lower for w in ["maladie", "ravageur", "criquet", "insecte", "jaune", "mort"]):
        return "incident"
    if any(w in texte_lower for w in ["prix", "marché", "vente", "fcfa"]):
        return "prix"
    if any(w in texte_lower for w in ["récolte", "semis", "plantation", "rendement"]):
        return "récolte"
    return "question"


# ─── Routes de test ───────────────────────────────────────────────────────────
@app.get("/")
def root():
    return {"status": "AgroNiger API en ligne ✅"}

@app.get("/test-supabase")
def test_supabase():
    result = supabase.table("agents").select("nom, region").execute()
    return {"agents": result.data}

@app.get("/test-groq")
def test_groq():
    conseil = generer_conseil("Mon mil est jaune, que faire ?", None)
    return {"conseil": conseil}