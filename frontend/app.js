// ================== CONFIG ==================
const API_BASE = "http://127.0.0.1:8000";

let authToken = null;
let currentUserEmail = null;

let ws = null;
let currentConversationId = null;

// Gestion du stream
let streamingBubble = null; // La bulle en cours de rédaction par le bot
let isStreaming = false;
let lastChunkAt = 0;
let streamWatchTimer = null;

// ================== DOM ==================
const authScreen = document.getElementById("auth-screen");
const chatScreen = document.getElementById("chat-screen");

const loginForm = document.getElementById("login-form");
const authStatusEl = document.getElementById("auth-status");

const headerEmailEl = document.getElementById("header-email");
const logoutBtn = document.getElementById("logout-btn");

const uploadForm = document.getElementById("upload-form");
const uploadStatusEl = document.getElementById("upload-status");

const chatScroll = document.getElementById("chat");
const chatInner = document.getElementById("chat-inner");

const msgInput = document.getElementById("msg-input");
const chatForm = document.getElementById("chat-form");

const wsPill = document.getElementById("ws-pill");

const historyList = document.getElementById("history-list");
const historyCount = document.getElementById("history-count");
const newChatBtn = document.getElementById("new-chat-btn");

// ================== UI HELPERS ==================

function escapeHtml(str) {
  return (str || "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;");
}

function setWsStatus(connected) {
  wsPill.classList.toggle("on", connected);
  wsPill.classList.toggle("off", !connected);
  wsPill.textContent = connected ? "Connecté" : "Déconnecté";
}

function scrollToBottom() {
  chatScroll.scrollTop = chatScroll.scrollHeight;
}

function autosizeTextarea(el) {
  el.style.height = "auto";
  el.style.height = Math.min(el.scrollHeight, 140) + "px";
}

function showChatScreen() {
  authScreen.classList.add("hidden");
  chatScreen.classList.remove("hidden");
  headerEmailEl.textContent = currentUserEmail || "Invité";
}

function showAuthScreen() {
  chatScreen.classList.add("hidden");
  authScreen.classList.remove("hidden");
  headerEmailEl.textContent = "Invité";
  setWsStatus(false);
}

function clearChatUI() {
  chatInner.innerHTML = "";
  resetStreamState(); // Important : réinitialiser l'état du stream
}

/**
 * Réinitialise proprement les variables de stream
 * pour éviter d'écrire dans une ancienne bulle.
 */
function resetStreamState() {
  isStreaming = false;
  streamingBubble = null;
  if (streamWatchTimer) {
    clearInterval(streamWatchTimer);
    streamWatchTimer = null;
  }
}

/**
 * Ajoute un message.
 * opts.typing = true -> Affiche les points de suspension initiaux
 */
function addMessage(role, text, opts = {}) {
  const normalized = role === "assistant" ? "bot" : role;

  const row = document.createElement("div");
  row.className = "msg " + normalized;

  const avatar = document.createElement("div");
  avatar.className = "avatar " + (normalized === "user" ? "user" : "bot");
  avatar.textContent = normalized === "user" ? "YOU" : "BOT";

  const bubble = document.createElement("div");
  bubble.className = "bubble " + (normalized === "user" ? "user" : "bot");

  if (opts.typing) {
    bubble.classList.add("typing");
    // Structure : Un conteneur pour le texte futur + Un conteneur pour l'animation
    bubble.innerHTML = `
      <div class="bubble-content"></div>
      <div class="typing-dots">
        <span></span><span></span><span></span>
      </div>
    `;
  } else {
    // Message statique normal (historique ou user)
    // On met le texte directement dans bubble-content pour garder la structure cohérente
    bubble.innerHTML = `<div class="bubble-content">${escapeHtml(text)}</div>`;
  }

  row.appendChild(avatar);
  row.appendChild(bubble);
  chatInner.appendChild(row);

  scrollToBottom();
  return bubble;
}

/**
 * Ajoute un chunk de texte à la bulle en cours.
 * Gère la suppression de l'animation de typing au premier chunk.
 */
function appendToBubble(bubble, chunk) {
  if (!bubble) return;

  // 1. Chercher si l'animation de typing existe encore et la supprimer
  const typingDots = bubble.querySelector(".typing-dots");
  if (typingDots) {
    typingDots.remove(); // On retire les points dès qu'on reçoit du texte
    bubble.classList.remove("typing"); // On retire la classe CSS si besoin
  }

  // 2. Ajouter le texte dans le conteneur de contenu
  let contentDiv = bubble.querySelector(".bubble-content");
  
  // Sécurité : si la structure est cassée, on écrit directement dans la bulle
  if (!contentDiv) {
    bubble.textContent += chunk; 
  } else {
    contentDiv.textContent += chunk;
  }

  scrollToBottom();
}

// ================== API HELPERS ==================
async function apiGet(path) {
  const res = await fetch(`${API_BASE}${path}`, {
    headers: authToken ? { Authorization: `Bearer ${authToken}` } : undefined,
  });
  if (!res.ok) throw new Error(`${res.status}`);
  return res.json();
}

async function apiPost(path, bodyObj) {
  const res = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(authToken ? { Authorization: `Bearer ${authToken}` } : {}),
    },
    body: JSON.stringify(bodyObj || {}),
  });
  if (!res.ok) throw new Error(`${res.status}`);
  return res.json();
}

async function apiDelete(path) {
  const res = await fetch(`${API_BASE}${path}`, {
    method: "DELETE",
    headers: authToken ? { Authorization: `Bearer ${authToken}` } : undefined,
  });
  if (!res.ok) throw new Error(`${res.status}`);
  return res;
}

// ================== STREAM WATCHER ==================
function startStreamWatcher() {
  stopStreamWatcher(); // Sécurité
  
  streamWatchTimer = setInterval(async () => {
    if (!isStreaming) return;

    // FIX: Augmentation du délai de 900ms à 3000ms (3 secondes)
    // Cela laisse le temps à l'IA de réfléchir sans que le JS ne coupe la connexion
    if (Date.now() - lastChunkAt > 3000) {
      // console.log("Stream timeout - fin du message détectée");
      resetStreamState(); 
      
      try {
        await loadConversations();
      } catch (e) {
        console.error("Refresh after stream error:", e);
      }
    }
  }, 500); // Check toutes les 500ms
}
function stopStreamWatcher() {
  if (streamWatchTimer) {
    clearInterval(streamWatchTimer);
    streamWatchTimer = null;
  }
}

// ================== CONVERSATIONS ==================
function renderConversations(convs) {
  historyList.innerHTML = "";
  historyCount.textContent = String(convs.length);

  convs.forEach((c) => {
    const item = document.createElement("div");
    item.className = "hist-item";
    const isActive = c.id === currentConversationId;

    item.innerHTML = `
      <div class="hist-who">${isActive ? "✅ Conversation" : "Conversation"}</div>
      <div class="hist-txt">${escapeHtml(c.title || "Sans titre")}</div>
      <button class="hist-del" title="Supprimer" aria-label="Supprimer">🗑</button>
    `;

    item.addEventListener("click", async () => {
      await selectConversation(c.id);
    });

    item.querySelector(".hist-del").addEventListener("click", async (e) => {
      e.preventDefault();
      e.stopPropagation();
      if (!confirm("Supprimer cette conversation ?")) return;

      try {
        const deletingActive = (c.id === currentConversationId);
        if (deletingActive) resetStreamState();

        await apiDelete(`/conversations/${c.id}`);
        const newConvs = await loadConversations();

        if (deletingActive) {
          if (newConvs.length > 0) {
            currentConversationId = newConvs[0].id;
            await loadMessagesForCurrentConversation();
            connectWebSocket();
          } else {
            currentConversationId = null;
            clearChatUI();
            addMessage("assistant", "Conversation supprimée. Clique sur + Nouveau.");
            if (ws) { try { ws.close(); } catch {} ws = null; }
            setWsStatus(false);
          }
        }
      } catch (err) {
        console.error(err);
        alert("Erreur suppression.");
      }
    });

    historyList.appendChild(item);
  });
}

async function loadConversations() {
  try {
    const convs = await apiGet("/conversations");
    if (!currentConversationId && convs.length > 0) {
      currentConversationId = convs[0].id;
    }
    renderConversations(convs);
    return convs;
  } catch(e) {
    console.error(e);
    return [];
  }
}

async function createNewConversation(title) {
  resetStreamState(); // Reset avant nouvelle conv
  const conv = await apiPost("/conversations", { title: title || "Nouvelle conversation" });
  currentConversationId = conv.id;

  await loadConversations();
  await loadMessagesForCurrentConversation();
  connectWebSocket();
}

async function loadMessagesForCurrentConversation() {
  if (!currentConversationId) return;

  clearChatUI(); // Vide l'UI et reset le stream state
  
  try {
    const msgs = await apiGet(`/conversations/${currentConversationId}/messages?limit=200`);
    if (msgs.length === 0) {
      addMessage("assistant", `Bonjour ${currentUserEmail || ""} 👋 Pose ta question.`);
      return;
    }
    for (const m of msgs) {
      addMessage(m.sender, m.content);
    }
  } catch (e) {
    console.error("Erreur chargement messages", e);
  }
}

async function selectConversation(conversationId) {
  if (conversationId === currentConversationId) return;
  
  resetStreamState(); // Stop tout stream en cours sur l'ancienne conv
  currentConversationId = conversationId;
  
  await loadConversations();
  await loadMessagesForCurrentConversation();
  connectWebSocket();
}

// ================== WEBSOCKET ==================
function buildWsUrl() {
  const protocol = window.location.protocol === "https:" ? "wss" : "ws";
  const host = window.location.host || "127.0.0.1:8000";
  return `${protocol}://${host}/ws/chat?token=${authToken}&conversation_id=${currentConversationId}`;
}

function wsReady() {
  return ws && ws.readyState === WebSocket.OPEN;
}

function connectWebSocket() {
  if (!authToken || !currentConversationId) {
    setWsStatus(false);
    return;
  }

  if (ws) {
    try { ws.close(); } catch {}
  }

  ws = new WebSocket(buildWsUrl());

  ws.onopen = () => setWsStatus(true);
  ws.onclose = () => setWsStatus(false);
  ws.onerror = () => setWsStatus(false);

 ws.onmessage = (event) => {
    const chunk = event.data;
    lastChunkAt = Date.now();

    // Si on reçoit un message mais qu'aucune bulle de stream n'est active
    if (!streamingBubble) {
      // FIX: On vérifie d'abord si une bulle de "typing" existe déjà en bas (l'orpheline)
      const lastBubble = chatInner.querySelector(".msg.bot:last-child .bubble.typing");
      
      if (lastBubble) {
        streamingBubble = lastBubble;
        isStreaming = true;
        startStreamWatcher(); // On relance le timer car on a repris la main
      } else {
        // Sinon, on en crée une nouvelle (cas rare ou reconnexion)
        streamingBubble = addMessage("assistant", "", { typing: true });
        isStreaming = true;
        startStreamWatcher();
      }
    }
    
    appendToBubble(streamingBubble, chunk);
  };
}

// ================== LOGIN ==================
loginForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const email = document.getElementById("email").value.trim();
  const password = document.getElementById("password").value.trim();
  authStatusEl.textContent = "";

  try {
    const res = await fetch(`${API_BASE}/auth/login`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password }),
    });

    if (!res.ok) {
      authStatusEl.textContent = "Email ou mot de passe incorrect.";
      return;
    }

    const data = await res.json();
    authToken = data.access_token;
    currentUserEmail = email;

    showChatScreen();
    const convs = await loadConversations();

    if (convs.length === 0) {
      await createNewConversation("Nouvelle conversation");
    } else {
      await loadMessagesForCurrentConversation();
      connectWebSocket();
    }
    msgInput.focus();
  } catch (err) {
    console.error(err);
    authStatusEl.textContent = "Erreur réseau.";
  }
});

// ================== LOGOUT ==================
logoutBtn.addEventListener("click", () => {
  authToken = null;
  currentUserEmail = null;
  currentConversationId = null;
  
  resetStreamState();
  if (ws) { try { ws.close(); } catch {} ws = null; }

  clearChatUI();
  historyList.innerHTML = "";
  historyCount.textContent = "0";
  showAuthScreen();
});
// ================== GESTION INSCRIPTION & BASCULEMENT ==================

const registerForm = document.getElementById("register-form");
const btnShowRegister = document.getElementById("btn-show-register");
const btnShowLogin = document.getElementById("btn-show-login");
const authTitle = document.getElementById("auth-title");

// Basculer vers l'inscription
btnShowRegister.addEventListener("click", (e) => {
  e.preventDefault();
  loginForm.classList.add("hidden");
  registerForm.classList.remove("hidden");
  authTitle.textContent = "Créer un compte";
  authStatusEl.textContent = "";
  authStatusEl.className = "status"; // Reset classes
});

// Basculer vers la connexion
btnShowLogin.addEventListener("click", (e) => {
  e.preventDefault();
  registerForm.classList.add("hidden");
  loginForm.classList.remove("hidden");
  authTitle.textContent = "Connexion";
  authStatusEl.textContent = "";
  authStatusEl.className = "status";
});


// Logique d'envoi de l'inscription
registerForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  
  // 1. Récupérer le nom
  const fullName = document.getElementById("reg-name").value.trim();
  const email = document.getElementById("reg-email").value.trim();
  const password = document.getElementById("reg-password").value.trim();
  const confirm = document.getElementById("reg-password-confirm").value.trim();

  if (password !== confirm) {
    authStatusEl.textContent = "❌ Les mots de passe ne correspondent pas.";
    authStatusEl.className = "status error";
    return;
  }

  authStatusEl.textContent = "Création du compte...";
  authStatusEl.className = "status";

  try {
    // 2. L'ajouter dans l'envoi (bien utiliser 'full_name' comme en Python)
    const res = await fetch(`${API_BASE}/auth/register`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ 
          email: email, 
          password: password, 
          full_name: fullName // <-- AJOUT ICI
      }),
    });

    if (!res.ok) {
      const data = await res.json();
      throw new Error(data.detail || "Erreur lors de l'inscription.");
    }

    authStatusEl.textContent = "✅ Compte créé ! Connecte-toi.";
    authStatusEl.className = "status success";

    // Vider les champs
    document.getElementById("reg-name").value = ""; // Vider le nom aussi
    document.getElementById("reg-email").value = "";
    document.getElementById("reg-password").value = "";
    document.getElementById("reg-password-confirm").value = "";

    setTimeout(() => {
      registerForm.classList.add("hidden");
      loginForm.classList.remove("hidden");
      authTitle.textContent = "Connexion";
      document.getElementById("email").value = email;
    }, 2000);

  } catch (err) {
    console.error(err);
    authStatusEl.textContent = `❌ ${err.message}`;
    authStatusEl.className = "status error";
  }
});
// ================== NEW CHAT ==================
newChatBtn.addEventListener("click", async () => {
  if (!authToken) return;
  await createNewConversation("Nouvelle conversation");
});

// ================== COMPOSER ==================
msgInput.addEventListener("input", () => autosizeTextarea(msgInput));
msgInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    chatForm.requestSubmit();
  }
});

chatForm.addEventListener("submit", async (e) => {
  e.preventDefault();

  const text = msgInput.value.trim();
  if (!text) return;

  if (!wsReady()) {
    addMessage("assistant", "❌ WebSocket fermé. Reconnexion…");
    connectWebSocket();
    // On attend un peu que ça reco
    setTimeout(() => { 
        if(wsReady()) ws.send(text); 
    }, 1000);
    return;
  }

  // 1. Force la fin du stream précédent s'il y en avait un
  resetStreamState();

  // 2. Ajoute le message user
  addMessage("user", text);

  // 3. Prépare la bulle BOT en mode "typing"
  streamingBubble = addMessage("assistant", "", { typing: true });
  isStreaming = true;
  lastChunkAt = Date.now();
  
  // 4. Lance le watcher et envoie le message
  startStreamWatcher();
  ws.send(text);

  msgInput.value = "";
  autosizeTextarea(msgInput);
  msgInput.focus();
});

// ================== UPLOAD ==================
uploadForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const fileInput = document.getElementById("file-input");
  const file = fileInput.files[0];
  if (!file) {
    uploadStatusEl.textContent = "Choisis un fichier d’abord.";
    return;
  }

  const formData = new FormData();
  formData.append("file", file);

  try {
    uploadStatusEl.textContent = "Indexation en cours…";
    const res = await fetch(`${API_BASE}/ingest-pdf`, {
      method: "POST",
      headers: authToken ? { Authorization: `Bearer ${authToken}` } : undefined,
      body: formData,
    });

    if (!res.ok) {
      uploadStatusEl.textContent = "Erreur lors de l’upload.";
      return;
    }
    const data = await res.json();
    uploadStatusEl.textContent = `✅ Indexé (${data.chunks} chunks)`;
    fileInput.value = "";
  } catch (err) {
    console.error(err);
    uploadStatusEl.textContent = "Erreur réseau.";
  }
});

// INIT
setWsStatus(false);
autosizeTextarea(msgInput);