require("dotenv").config();
const { createHmac, timingSafeEqual } = require("crypto");
const express = require("express");
const { Pool } = require("pg");


const app = express();

app.use(express.json({
    verify: (req, res, buffer) => {
        req.rawBody = Buffer.from(buffer);
    }
}));

const db = new Pool({
    host: process.env.POSTGRES_HOST || "localhost",
    port: Number(process.env.POSTGRES_PORT || 5432),
    database: process.env.POSTGRES_DB,
    user: process.env.POSTGRES_USER,
    password: process.env.POSTGRES_PASSWORD
});

function normalizarTelefone(numero) {
    const digitos = String(numero || "").replace(/\D/g, "");
    return digitos.startsWith("244") ? digitos.slice(3) : digitos;
}

async function listarContactos(numero) {
    const telefone = normalizarTelefone(numero);
    if (!telefone) return { autorizado: false };

    const utilizadores = await db.query(
        `SELECT company_id
         FROM usuarios
         WHERE regexp_replace(COALESCE(whatsapp, ''), '[^0-9]', '', 'g') IN ($1, $2)
         LIMIT 2`,
        [telefone, `244${telefone}`]
    );

    if (utilizadores.rows.length !== 1 || !utilizadores.rows[0].company_id) {
        return { autorizado: false };
    }

    const resultado = await db.query(
        `SELECT name, email, phone, company, stage
         FROM contacts
         WHERE company_id = $1
         ORDER BY created_at DESC
         LIMIT 20`,
        [utilizadores.rows[0].company_id]
    );

    return { autorizado: true, contactos: resultado.rows };
}

function formatarContactos(contactos) {
    if (contactos.length === 0) return "Não há contactos no CRM da sua empresa.";

    const linhas = contactos.map((contacto, indice) => {
        const detalhes = [contacto.phone, contacto.email, contacto.company, contacto.stage].filter(Boolean);
        return `${indice + 1}. ${contacto.name}${detalhes.length ? ` - ${detalhes.join(" | ")}` : ""}`;
    });

    return `Contactos do CRM (até 20 mais recentes):\n${linhas.join("\n")}`;
}

function validarAssinatura(req) {
    const segredo = process.env.WHATSAPP_APP_SECRET;
    const assinatura = req.get("x-hub-signature-256");
    if (!segredo || !assinatura?.startsWith("sha256=") || !req.rawBody) return false;

    const esperada = Buffer.from(`sha256=${createHmac("sha256", segredo).update(req.rawBody).digest("hex")}`);
    const recebida = Buffer.from(assinatura);
    return recebida.length === esperada.length && timingSafeEqual(recebida, esperada);
}

async function enviarMensagem(numero, mensagem) {
    const resposta = await fetch(
        `https://graph.facebook.com/v25.0/${process.env.WHATSAPP_PHONE_NUMBER_ID}/messages`,
        {
            method: "POST",
            headers: {
                Authorization: `Bearer ${process.env.WHATSAPP_ACCESS_TOKEN}`,
                "Content-Type": "application/json"
            },
            body: JSON.stringify({
                messaging_product: "whatsapp",
                to: numero,
                type: "text",
                text: { body: mensagem }
            })
        }
    );

    if (!resposta.ok) {
        console.error("Erro ao enviar mensagem:", await resposta.text());
    }
}

// Verificação da Meta
app.get("/health", (req, res) => res.status(200).send("ok"));

app.get("/webhook/whatsapp", (req, res) => {
    const mode = req.query["hub.mode"];
    const token = req.query["hub.verify_token"];
    const challenge = req.query["hub.challenge"];

    const VERIFY_TOKEN = process.env.WHATSAPP_VERIFY_TOKEN;

    if (mode === "subscribe" && token === VERIFY_TOKEN) {
        return res.status(200).send(challenge);
    }

    return res.sendStatus(403);
});

// Eventos recebidos
app.post("/webhook/whatsapp", (req, res) => {
    if (!validarAssinatura(req)) return res.sendStatus(401);

    console.log("Webhook recebido:");
    console.log(JSON.stringify(req.body, null, 2));

    const valor = req.body.entry?.[0]?.changes?.[0]?.value;
    const mensagemRecebida = valor?.messages?.[0];
    const contacto = valor?.contacts?.[0];

    if (mensagemRecebida?.type === "text") {
        const numero = mensagemRecebida.from;
        const texto = mensagemRecebida.text.body.trim().toLowerCase();
        const nome = contacto?.profile?.name || "utilizador";
        let resposta;

        if (texto === "ola" || texto === "olá") {
            resposta = `Olá, ${nome}! Boas-vindas. O seu número é ${numero}.`;
        } else if (texto === "/cliente" || texto === "/crm") {
            listarContactos(numero)
                .then((resultado) => {
                    const mensagem = resultado.autorizado
                        ? formatarContactos(resultado.contactos)
                        : "Este número de telefone não está autorizado a consultar o CRM.";
                    return enviarMensagem(numero, mensagem);
                })
                .catch((erro) => {
                    console.error("Erro ao consultar contactos do CRM:", erro.message);
                    return enviarMensagem(numero, "Não foi possível consultar o CRM neste momento.");
                })
                .catch((erro) => console.error("Erro ao enviar resposta:", erro.message));
        }

        if (resposta) {
            enviarMensagem(numero, resposta).catch((erro) => {
                console.error("Erro ao enviar resposta:", erro.message);
            });
        }
    }

    res.sendStatus(200);
});

const PORT = process.env.PORT || 3000;

app.listen(PORT, () => {
    console.log(`Servidor rodando na porta CARLOS ${PORT}`);
    console.log(`Webhook de verificação disponível em: http://localhost:${PORT}/webhook/whatsapp`);
});
