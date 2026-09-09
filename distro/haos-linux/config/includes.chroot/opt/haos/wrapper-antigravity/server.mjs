#!/usr/bin/env node
/**
 * antigravity-proxy — OpenAI-compatible proxy for Google Antigravity (Cloud Code Assist) API.
 *
 * Translates OpenAI /v1/chat/completions (stream and non-stream) to the
 * Antigravity v1internal:generateContent / :streamGenerateContent endpoints,
 * authenticating with the agy OAuth refresh token (Google AI Pro account).
 *
 * Usage: node server.mjs [port]
 * Env:   ANTIGRAVITY_PROXY_PORT (default 8787)
 *        ANTIGRAVITY_TOKEN_FILE (default ~/.gemini/antigravity-cli/antigravity-oauth-token)
 *        ANTIGRAVITY_OPENCODE_ACCOUNTS (default: Linux/macOS ~/.config/opencode/antigravity-accounts.json;
 *                                       Windows %APPDATA%\opencode\antigravity-accounts.json)
 *
 * Multiplataforma: Node.js puro, roda em Linux, macOS e Windows (o agy CLI é
 * nativo nas três plataformas). No Windows o caminho do opencode usa %APPDATA%.
 */
import http from 'node:http';
import http2 from 'node:http2';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import crypto from 'node:crypto';
import zlib from 'node:zlib';
import { Readable } from 'node:stream';
import { StringDecoder } from 'node:string_decoder';
import { pathToFileURL, fileURLToPath } from 'node:url';
import { execFile } from 'node:child_process';

// ---------------------------------------------------------------------------
// Multi-Provider Gateway Registry (providers.json)
// ---------------------------------------------------------------------------
const DATA_DIR = path.dirname(fileURLToPath(import.meta.url));
const PROVIDERS_FILE = process.env.ANTIGRAVITY_PROVIDERS_FILE || path.join(DATA_DIR, 'providers.json');
// Cache persistente de respostas (sobrevive a reinícios) e rastreamento de custo.
const CACHE_FILE = process.env.ANTIGRAVITY_CACHE_FILE || path.join(DATA_DIR, 'response-cache.json');
const SPEND_FILE = process.env.ANTIGRAVITY_SPEND_FILE || path.join(DATA_DIR, 'spend.json');

let externalProviders = [];
let apiRequestsEnabled = true; // Switch liga/desliga da API OpenAI

function loadExternalProviders() {
  try {
    if (fs.existsSync(PROVIDERS_FILE)) {
      const parsed = JSON.parse(fs.readFileSync(PROVIDERS_FILE, 'utf8'));
      if (Array.isArray(parsed.providers)) {
        externalProviders = parsed.providers;
        return;
      }
    }
  } catch (err) {
    log('WARN failed to load providers.json:', err.message);
  }
  externalProviders = [];
}

function saveExternalProviders(providers) {
  externalProviders = providers;
  modelsCatalogDirty = true; // invalida o cache do /v1/models
  fs.writeFileSync(PROVIDERS_FILE, JSON.stringify({ providers }, null, 2));
  syncDshSettingsWithExternalProviders();
}

/**
 * Sincroniza dinamicamente os modelos dos provedores externos
 * dentro do /root/.dsh/settings.yaml na seção do provedor antigravity.
 * Assim o DSH hot-recarrega os modelos automaticamente na UI!
 */
const DSH_SETTINGS_FILE = '/root/.dsh/settings.yaml';

function syncDshSettingsWithExternalProviders() {
  try {
    if (!fs.existsSync(DSH_SETTINGS_FILE)) return;
    const content = fs.readFileSync(DSH_SETTINGS_FILE, 'utf8');

    // Extrai todos os modelos externos ativos
    const customLines = [];
    for (const prov of externalProviders) {
      if (prov.enabled === false) continue;
      for (const m of (prov.models || [])) {
        const fullId = `${prov.id}_${m.id}`;
        const displayName = `${prov.id} ${m.id}`;
        customLines.push(`        - id: ${fullId}\n          name: ${displayName}`);
      }
    }

    const headerMarker = '        # --- Modelos Customizados do Wrapper (Multi-Provider) ---';
    const endMarker = 'agent-default-model:';

    let newContent;
    if (content.includes(headerMarker)) {
      const parts = content.split(headerMarker);
      const afterParts = parts[1].split(endMarker);
      const block = customLines.length > 0 ? '\n' + customLines.join('\n') + '\n' : '\n';
      newContent = parts[0] + headerMarker + block + endMarker + afterParts[1];
    } else if (content.includes(endMarker)) {
      const parts = content.split(endMarker);
      const block = headerMarker + (customLines.length > 0 ? '\n' + customLines.join('\n') + '\n' : '\n');
      newContent = parts[0] + block + endMarker + parts[1];
    } else {
      return;
    }

    if (newContent !== content) {
      fs.writeFileSync(DSH_SETTINGS_FILE, newContent, 'utf8');
      log(`[DSH Auto-Sync] Sincronizados ${customLines.length} modelos customizados no /root/.dsh/settings.yaml`);
    }
  } catch (err) {
    log('WARN failed to sync DSH settings.yaml:', err.message);
  }
}

/** Normaliza lista de chaves API (remove vazias e duplicadas). */
function normalizeApiKeys(keys) {
  if (!Array.isArray(keys)) return [];
  const out = [];
  const seen = new Set();
  for (const k of keys) {
    const s = String(k || '').trim();
    if (s && !seen.has(s)) { seen.add(s); out.push(s); }
  }
  return out;
}

/** Chaves efetivas de um provider (apiKeys ou apiKey legada). */
function providerApiKeys(provider) {
  const k = normalizeApiKeys(provider?.apiKeys);
  if (k.length > 0) return k;
  const legacy = String(provider?.apiKey || '').trim();
  return legacy ? [legacy] : [];
}

// Estado de seleção de chave por provider (somente memória, nunca persiste):
// - failover: ptr = última chave que funcionou (tenta ela primeiro)
// - round-robin: ptr avança 1 a cada request (cada request sai por chave diferente)
// Cooldown por chave evita re-tentar chave com 401/403/429 acabados de falhar.
const providerKeyPtr = new Map();      // id -> { ptr }
const providerKeyCooldown = new Map(); // id -> Map(idx -> até (ms))
const KEY_SWITCHABLE_CODES = new Set([401, 403, 429]);

/** Índice inicial da rodada de chaves do provider para esta requisição. */
function providerKeyStartIdx(provider) {
  const keys = providerApiKeys(provider);
  if (keys.length <= 1) return 0;
  const st = providerKeyPtr.get(provider.id) || { ptr: 0 };
  if (provider.keyMode === 'round-robin') {
    const next = (st.ptr + 1) % keys.length; // avança a cada request
    providerKeyPtr.set(provider.id, { ptr: next });
    return next;
  }
  return st.ptr % keys.length; // failover: última que funcionou
}

function providerKeyCooling(provider, idx) {
  const cd = providerKeyCooldown.get(provider.id);
  return cd ? (cd.get(idx) || 0) > Date.now() : false;
}

function setProviderKeyCooldown(provider, idx, ms) {
  const cd = providerKeyCooldown.get(provider.id) || new Map();
  cd.set(idx, Date.now() + ms);
  providerKeyCooldown.set(provider.id, cd);
}

function clearProviderKeyCooldown(provider, idx) {
  const cd = providerKeyCooldown.get(provider.id);
  if (!cd) return;
  cd.delete(idx);
  if (cd.size === 0) providerKeyCooldown.delete(provider.id);
}

function upsertExternalProvider(provider) {
  const cleanId = String(provider.id || '').toLowerCase().replace(/[^a-z0-9_-]/g, '');
  if (!cleanId) throw new Error('Provider id inválido');
  const existing = externalProviders.find(p => p.id === cleanId);
  const models = Array.isArray(provider.models) ? provider.models.map(m =>
    typeof m === 'string'
      ? { id: m.trim(), targetModel: m.trim() }
      : { id: String(m.id || m.targetModel || '').trim(), targetModel: String(m.targetModel || m.id || '').trim() }
  ).filter(m => m.id) : [];
  const hasParamsObj = provider.params && typeof provider.params === 'object';
  const src = hasParamsObj ? provider.params : {};
  const params = {
    tempDefault: numOr(src.tempDefault, 0),
    tempMin: numOr(src.tempMin, 0),
    tempMax: numOr(src.tempMax, 0),
    topPDefault: numOr(src.topPDefault, 0),
    topPMin: numOr(src.topPMin, 0),
    topPMax: numOr(src.topPMax, 0),
    maxTokensDefault: numOr(src.maxTokensDefault, 0),
    maxTokensCap: numOr(src.maxTokensCap, 0),
    cacheTtl: numOr(src.cacheTtl, 0),
    priceInPerM: numOr(src.priceInPerM, 0),
    priceOutPerM: numOr(src.priceOutPerM, 0),
    budgetDailyUSD: numOr(src.budgetDailyUSD, 0),
    customSystemPrompt: String(src.customSystemPrompt || '').slice(0, 8000),
  };
  // Salvamento sem objeto de params (ex.: form add/edit) NÃO deve zerar os
  // ajustes já configurados — preserva os existentes.
  if (!hasParamsObj && existing?.params) {
    Object.assign(params, existing.params);
  }

  // Chaves: payload com campo apiKeys é autoritativo (incl. [] = limpar).
  // Sem o campo, cai no legado: apiKey nova (≠ '********') ou mantém a atual.
  const hasApiKeysField = Array.isArray(provider.apiKeys);
  let apiKeys = hasApiKeysField ? normalizeApiKeys(provider.apiKeys) : [];
  const keyMode = provider.keyMode === 'round-robin' ? 'round-robin' : 'failover';
  if (!hasApiKeysField) {
    if (provider.apiKey !== undefined && provider.apiKey !== '' && provider.apiKey !== '********') {
      apiKeys = normalizeApiKeys([provider.apiKey]);
    } else if (existing) {
      apiKeys = normalizeApiKeys(existing.apiKeys).length > 0
        ? normalizeApiKeys(existing.apiKeys)
        : (String(existing.apiKey || '').trim() ? [String(existing.apiKey).trim()] : []);
    }
  }
  if (apiKeys.length === 0 && existing && !hasApiKeysField) {
    apiKeys = normalizeApiKeys(existing.apiKeys).length > 0
      ? normalizeApiKeys(existing.apiKeys)
      : (String(existing.apiKey || '').trim() ? [String(existing.apiKey).trim()] : []);
  }

  const record = {
    id: cleanId,
    name: String(provider.name || cleanId),
    baseUrl: String(provider.baseUrl || '').replace(/\/+$/, ''),
    apiKey: apiKeys[0] || '', // chave primária (legado/display)
    apiKeys,
    keyMode,
    enabled: provider.enabled !== false,
    models,
    params,
  };
  if (existing) {
    Object.assign(existing, record);
  } else {
    externalProviders.push(record);
  }
  saveExternalProviders(externalProviders);
  return record;
}

function deleteExternalProvider(providerId) {
  externalProviders = externalProviders.filter(p => p.id !== String(providerId || ''));
  saveExternalProviders(externalProviders);
}

/**
 * Consulta a lista de modelos disponíveis de um provider OpenAI-compatible
 * (GET {base}/models com fallback {base}/v1/models). Retorna array de nomes
 * ou lança erro.
 */
async function listModelsFromProvider(baseUrl, apiKey) {
  const base = String(baseUrl || '').trim().replace(/\/+$/, '');
  if (!base) throw new Error('URL Base é obrigatória');
  const candidates = [
    `${base}/models`,
    `${base.replace(/\/v1$/, '')}/v1/models`,
  ];
  let lastErr = null;
  for (const url of candidates) {
    try {
      const headers = { 'Content-Type': 'application/json', 'User-Agent': 'antigravity-proxy/1.0' };
      if (apiKey) headers['Authorization'] = `Bearer ${apiKey}`;
      const ctrl = new AbortController();
      const timer = setTimeout(() => ctrl.abort(), 10_000);
      const resp = await fetch(url, { headers, method: 'GET', signal: ctrl.signal });
      clearTimeout(timer);
      if (!resp.ok) {
        lastErr = new Error(`HTTP ${resp.status} em ${url}`);
        continue;
      }
      const parsed = await resp.json();
      const list = Array.isArray(parsed?.data)
        ? parsed.data.map(m => (typeof m === 'string' ? m : m?.id || m?.name || ''))
        : Array.isArray(parsed?.models)
          ? parsed.models.map(m => (typeof m === 'string' ? m : m?.id || m?.name || ''))
          : [];
      const clean = [...new Set(list.map(s => String(s).trim()).filter(Boolean))].sort();
      if (clean.length === 0) {
        lastErr = new Error(`Nenhum modelo encontrado em ${url}`);
        continue;
      }
      return { models: clean, source: url };
    } catch (e) {
      lastErr = e;
    }
  }
  throw new Error(`Não foi possível listar modelos: ${lastErr?.message || 'erro desconhecido'}`);
}

/** Lista modelos tentando cada chave do provider até uma funcionar.
 *  Aceita { apiKeys: [] } / { apiKey: '' } / string simples. */
async function listModelsFromProviderAnyKey(baseUrl, providerOrKey) {
  let keys = [];
  if (typeof providerOrKey === 'string') {
    keys = normalizeApiKeys([providerOrKey]);
  } else if (providerOrKey && typeof providerOrKey === 'object') {
    keys = normalizeApiKeys(providerOrKey.apiKeys);
    if (keys.length === 0) keys = normalizeApiKeys([providerOrKey.apiKey]);
  }
  const pool = keys.length > 0 ? keys : [null];
  let lastErr = null;
  for (const k of pool) {
    try {
      return await listModelsFromProvider(baseUrl, k || undefined);
    } catch (e) {
      lastErr = e;
    }
  }
  throw lastErr || new Error('Não foi possível listar modelos');
}

// ---------------------------------------------------------------------------
// Codex / ChatGPT — OAuth via sidecar "openai-oauth" (espelha o CLI Codex).
// Upstream: https://chatgpt.com/backend-api/codex  • credenciais em ~/.codex/auth.json
// O sidecar expõe /v1/chat/completions, /v1/responses e /v1/models localmente.
// Referência: https://github.com/EvanZhouDev/openai-oauth
// ---------------------------------------------------------------------------
const CODEX_SIDECAR_URL = (process.env.CODEX_SIDECAR_URL || 'http://127.0.0.1:10531').replace(/\/+$/, '');
const CODEX_AUTH_FILE = process.env.CODEX_AUTH_FILE || path.join(os.homedir(), '.codex', 'auth.json');
const CODEX_PKG = process.env.CODEX_PKG || 'openai-oauth@latest';

// --- Parâmetros OAuth espelhados do openai-oauth (o sidecar lê ~/.codex/auth.json) ---
const CODE_REDIRECT_URI = 'http://localhost:1455/auth/callback';
const CODE_OAUTH_ISSUER = 'https://auth.openai.com';
const CODE_CLIENT_ID = process.env.CODEX_OAUTH_CLIENT_ID || 'app_EMoamEEZ73f0CkXaXp7hrann';
const CODE_SCOPE = 'openid profile email offline_access';
// Login 100% dentro do proxy: guardamos verifier/state do PKCE em memória e o
// usuário pode colar o callback de QUALQUER rede (não depende de localhost aqui).
const codexPending = { verifier: '', state: '', url: '', createdAt: 0 };

function codexB64url(input) {
  return Buffer.from(input).toString('base64').replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}
function codexRandB64url(n) { return crypto.randomBytes(n).toString('base64url'); }
function codexSha256B64url(s) { return crypto.createHash('sha256').update(s).digest('base64url'); }

function codexParseJwtClaims(token) {
  if (typeof token !== 'string' || !token.includes('.')) return null;
  const parts = token.split('.');
  if (parts.length !== 3) return null;
  try {
    const pad = parts[1].replace(/-/g, '+').replace(/_/g, '/');
    const json = Buffer.from(pad + '='.repeat((4 - (pad.length % 4)) % 4), 'base64').toString('utf8');
    const parsed = JSON.parse(json);
    return parsed && typeof parsed === 'object' ? parsed : null;
  } catch { return null; }
}

function codexDeriveAccountId(token) {
  const claims = codexParseJwtClaims(token);
  if (!claims) return null;
  const authClaim = claims['https://api.openai.com/auth'];
  if (authClaim && typeof authClaim === 'object' && typeof authClaim.chatgpt_account_id === 'string') {
    return authClaim.chatgpt_account_id;
  }
  if (typeof claims.chatgpt_account_id === 'string') return claims.chatgpt_account_id;
  if (Array.isArray(claims.organizations) && claims.organizations[0] && typeof claims.organizations[0].id === 'string') {
    return claims.organizations[0].id;
  }
  return null;
}

async function codexNewLoginUrl() {
  const verifier = codexRandB64url(48);
  const challenge = codexSha256B64url(verifier);
  const state = codexRandB64url(24);
  const q = new URLSearchParams({
    response_type: 'code',
    client_id: CODE_CLIENT_ID,
    redirect_uri: CODE_REDIRECT_URI,
    scope: CODE_SCOPE,
    state,
    code_challenge: challenge,
    code_challenge_method: 'S256',
    id_token_add_organizations: 'true',
    codex_cli_simplified_flow: 'true',
  });
  codexPending.verifier = verifier;
  codexPending.state = state;
  codexPending.url = `${CODE_OAUTH_ISSUER}/oauth/authorize?${q.toString()}`;
  codexPending.createdAt = Date.now();
  return codexPending.url;
}

function codexPendingFresh() {
  return Boolean(codexPending.state) && (Date.now() - codexPending.createdAt) < 10 * 60 * 1000;
}

/** Troca o code do callback colado por tokens e grava ~/.codex/auth.json. */
async function codexCompletePaste(callbackUrl) {
  let parsed;
  try { parsed = new URL(String(callbackUrl || '').trim()); }
  catch { throw new Error('URL inválida. Cole a URL completa que o navegador mostrou (começa com http://localhost:1455/auth/callback?...).'); }
  const code = parsed.searchParams.get('code');
  if (!code) {
    throw new Error('A URL não contém "code". Cole a URL COMPLETA da barra de endereço (ela vem de http://localhost:1455/auth/callback?code=...&state=...).');
  }
  if (!codexPendingFresh()) {
    throw new Error('O link de login expirou (10 min). Clique em 🔑 Login para gerar um novo e autorize de novo.');
  }
  const state = parsed.searchParams.get('state');
  if (state && state !== codexPending.state) {
    throw new Error('state da URL não confere com o link gerado — certifique-se de colar o callback do ÚLTIMO link (clique em 🔑 Login de novo se preciso).');
  }
  const res = await fetch(`${CODE_OAUTH_ISSUER}/oauth/token`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: new URLSearchParams({
      grant_type: 'authorization_code',
      code,
      redirect_uri: CODE_REDIRECT_URI,
      client_id: CODE_CLIENT_ID,
      code_verifier: codexPending.verifier,
    }).toString(),
  });
  const text = await res.text();
  if (!res.ok) {
    let detail = '';
    try {
      const j = JSON.parse(text);
      if (j && typeof j === 'object') detail = j.error_description || j.error?.message || j.message || j.error || '';
    } catch { /* corpo não-JSON */ }
    throw new Error(`Troca do código falhou (HTTP ${res.status}). ${detail} ${text.slice(0, 160)}`.trim());
  }
  let payload;
  try { payload = JSON.parse(text); } catch { throw new Error('Resposta de token inválida (não-JSON).'); }
  const accessToken = payload.access_token;
  const idToken = payload.id_token;
  const refreshToken = payload.refresh_token;
  if (!accessToken) throw new Error('Resposta sem access_token.');
  const accountId = codexDeriveAccountId(idToken) || codexDeriveAccountId(accessToken);
  if (!accountId) throw new Error('Não foi possível derivar o account_id da sessão.');
  const email = codexParseJwtClaims(idToken)?.email || null;
  const file = {
    auth_mode: 'chatgpt',
    tokens: {
      id_token: idToken || undefined,
      access_token: accessToken,
      refresh_token: refreshToken || undefined,
      account_id: accountId,
    },
    last_refresh: new Date().toISOString(),
  };
  fs.mkdirSync(path.dirname(CODEX_AUTH_FILE), { recursive: true });
  fs.writeFileSync(CODEX_AUTH_FILE, JSON.stringify(file, null, 2), { mode: 0o600 });
  log(`[Codex OAuth] Login concluído via callback colado. account=${accountId}${email ? ' email=' + email : ''}`);
  codexPending.verifier = '';
  codexPending.state = ''; // esgota o link após o uso
  return { accountId, email, hasRefresh: Boolean(refreshToken) };
}

function execAsync(cmd, args, timeoutMs = 90000) {
  return new Promise((resolve) => {
    execFile(cmd, args, {
      timeout: timeoutMs,
      maxBuffer: 8 * 1024 * 1024,
      env: { ...process.env, npm_config_yes: 'true', NO_COLOR: '1' },
    }, (err, stdout, stderr) => resolve({ err, stdout: String(stdout || ''), stderr: String(stderr || '') }));
  });
}

async function codexSidecarProbe() {
  try {
    const ctrl = new AbortController();
    const t = setTimeout(() => ctrl.abort(), 2500);
    const res = await fetch(`${CODEX_SIDECAR_URL}/v1/models`, { method: 'GET', signal: ctrl.signal });
    clearTimeout(t);
    if (!res.ok) return null;
    const parsed = await res.json();
    const models = Array.isArray(parsed?.data)
      ? parsed.data.map(m => (typeof m === 'string' ? m : m?.id)).filter(Boolean)
      : [];
    return { models: [...new Set(models)].sort() };
  } catch { return null; }
}

function codexAuthInfo() {
  try {
    if (!fs.existsSync(CODEX_AUTH_FILE)) return { loggedIn: false };
    const raw = JSON.parse(fs.readFileSync(CODEX_AUTH_FILE, 'utf8'));
    const t = raw.tokens || {};
    const access = t.access_token;
    const idToken = t.id_token;
    const accountId = t.account_id || null;
    const email = codexParseJwtClaims(idToken)?.email || null;
    let expiresInSecs = null;
    const claims = codexParseJwtClaims(access || idToken);
    if (claims && typeof claims.exp === 'number') {
      expiresInSecs = Math.max(0, Math.round(claims.exp - Date.now() / 1000));
    }
    return {
      loggedIn: Boolean(access),
      accountId,
      email,
      hasRefresh: Boolean(t.refresh_token),
      expiresInSecs,
    };
  } catch (e) {
    return { loggedIn: false, error: e.message };
  }
}

function codexProviderExists() {
  return externalProviders.some(p => p.id === 'codex');
}

function codexSidecarStart() {
  return execAsync('npx', ['--yes', CODEX_PKG, '--detach'], 120000);
}

function codexSidecarStop() {
  return execAsync('npx', ['--yes', CODEX_PKG, 'stop'], 60000);
}

/**
 * Conecta o catálogo: registra/atualiza o provider "codex" (baseUrl do sidecar)
 * com os modelos descobertos na conta. Models = "codex_<id>" no DSH.
 */
async function codexConnectToCatalog() {
  const probe = await codexSidecarProbe();
  if (!probe || probe.models.length === 0) {
    throw new Error('Sidecar não está respondendo ou conta sem modelos. Inicie e faça login primeiro.');
  }
  const auth = codexAuthInfo();
  const models = probe.models.map(name => ({ id: name, targetModel: name }));
  const provider = {
    id: 'codex',
    name: 'Codex (ChatGPT)',
    baseUrl: `${CODEX_SIDECAR_URL}/v1`,
    apiKeys: ['openai-oauth'], // placeholder — o sidecar usa a sessão OAuth local
    keyMode: 'failover',
    enabled: true,
    models,
  };
  upsertExternalProvider(provider);
  return { models: probe.models, loggedIn: auth.loggedIn };
}

async function codexStatus() {
  const auth = codexAuthInfo();
  const probe = await codexSidecarProbe();
  const registered = codexProviderExists() ? externalProviders.find(p => p.id === 'codex') : null;
  return {
    sidecar: probe ? { running: true, modelsCount: probe.models.length } : { running: false },
    auth,
    login: {
      hasPending: codexPendingFresh(),
      url: codexPendingFresh() ? codexPending.url : null,
      pendingSecsLeft: codexPendingFresh() ? Math.max(0, Math.round((codexPending.createdAt + 10 * 60 * 1000 - Date.now()) / 1000)) : 0,
    },
    provider: registered ? { enabled: registered.enabled !== false, modelCount: (registered.models || []).length } : null,
    authFile: CODEX_AUTH_FILE,
    sidecarUrl: `${CODEX_SIDECAR_URL}/v1`,
  };
}

/**
 * Auto-populate (boot): provedores cadastrados sem modelos são preenchidos
 * automaticamente pela API do provider, um pouco após o servidor subir.
 */
async function autoPopulateMissingModels() {
  try {
    let changed = false;
    for (const prov of externalProviders) {
      if (prov.enabled === false) continue;
      if (!prov.baseUrl || (Array.isArray(prov.models) && prov.models.length > 0)) continue;
      try {
        const { models } = await listModelsFromProviderAnyKey(prov.baseUrl, prov);
        prov.models = models.map(name => ({ id: name, targetModel: name }));
        log(`[Auto-Fetch] ${prov.name}: ${models.length} modelos carregados automaticamente via ${prov.baseUrl}`);
        changed = true;
      } catch (err) {
        log(`[Auto-Fetch] ${prov.name}: sem modelos (${err.message})`);
      }
    }
    if (changed) saveExternalProviders(externalProviders);
  } catch (err) {
    log('WARN auto-populate failed:', err.message);
  }
}

loadExternalProviders();
// spendState/cache são declarados abaixo; loadSpendState()/loadPersistentCache()
// são invocados no fim do módulo (depois das declarações no fluxo ESM).
// Dispara auto-populate 3s após o boot (sem travar a inicialização).
setTimeout(() => { autoPopulateMissingModels(); }, 3000);

/**
 * Resolve se o modelo requisitado pertence a um provider externo (de-para).
 * Formatos suportados:
 *  1. ID direto: "deepseek_deepseek-chat", "openrouter_deepseek-r1" (provider_modelo)
 *  2. Prefixo com barra: "deepseek/deepseek-chat"
 *  3. Alias puro mapeado no provider
 */
function resolveExternalProvider(requestedModel) {
  const modelStr = String(requestedModel || '').trim();
  for (const prov of externalProviders) {
    if (prov.enabled === false) continue;
    const pId = prov.id.toLowerCase();
    
    // Formato provider_modelo (ex: deepseek_deepseek-chat, groq_llama-3.3-70b)
    const prefixUnder = `${pId}_`;
    const prefixSlash = `${pId}/`;
    
    if (modelStr.toLowerCase().startsWith(prefixUnder)) {
      const innerModel = modelStr.slice(prefixUnder.length);
      const matchedModel = (prov.models || []).find(m => m.id.toLowerCase() === innerModel.toLowerCase());
      const targetModel = matchedModel?.targetModel || matchedModel?.id || innerModel;
      return { provider: prov, targetModel, resolvedId: modelStr };
    }
    
    if (modelStr.toLowerCase().startsWith(prefixSlash)) {
      const innerModel = modelStr.slice(prefixSlash.length);
      const matchedModel = (prov.models || []).find(m => m.id.toLowerCase() === innerModel.toLowerCase());
      const targetModel = matchedModel?.targetModel || matchedModel?.id || innerModel;
      return { provider: prov, targetModel, resolvedId: modelStr };
    }

    // Busca direta na lista de modelos do provider
    for (const m of (prov.models || [])) {
      if (m.id.toLowerCase() === modelStr.toLowerCase()) {
        return { provider: prov, targetModel: m.targetModel || m.id, resolvedId: modelStr };
      }
    }
  }
  return null;
}

// ---------------------------------------------------------------------------
// Config
// ---------------------------------------------------------------------------
const PORT = Number(process.env.ANTIGRAVITY_PROXY_PORT || 8787);
const SERVER_STARTED_AT = Date.now();
// Caminho do refresh token do agy CLI. No Windows o agy também grava em
// %USERPROFILE%\.gemini\antigravity-cli\... (os.homedir() resolve corretamente).
const TOKEN_FILE = process.env.ANTIGRAVITY_TOKEN_FILE
  || path.join(os.homedir(), '.gemini/antigravity-cli/antigravity-oauth-token');
// Contas do opencode: no Windows ficam em %APPDATA%\opencode\, no Linux/macOS
// em ~/.config/opencode/. Sobrescreva via ANTIGRAVITY_OPENCODE_ACCOUNTS.
const OPENCODE_ACCOUNTS = process.env.ANTIGRAVITY_OPENCODE_ACCOUNTS
  || (process.platform === 'win32'
    ? path.join(process.env.APPDATA || path.join(os.homedir(), 'AppData', 'Roaming'), 'opencode', 'antigravity-accounts.json')
    : path.join(os.homedir(), '.config/opencode/antigravity-accounts.json'));

const CLIENT_ID = process.env.ANTIGRAVITY_CLIENT_ID
  || '1071006060591-tmhssin2h21lcre235vtolojh4g403ep.apps.googleusercontent.com';
// Credencial do cliente OAuth. Sempre que possível injete via env
// (ANTIGRAVITY_CLIENT_SECRET) em vez de deixar o fallback embutido; o fallback
// existe apenas para não quebrar instalações existentes.
const CLIENT_SECRET = process.env.ANTIGRAVITY_CLIENT_SECRET
  || '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';

// Concorrência máxima de requests upstream (semáforo FIFO). Rajadas paralelas
// do DSH (subagentes) viram fila em vez de estourar a cota da conta.
let MAX_CONCURRENT = Number(process.env.ANTIGRAVITY_MAX_CONCURRENT || 30);
// Timeouts (ms): evita requests órfãos quando a Google pendura.
// Timeout de conexão e recebimento dos primeiros headers aumentado para 2 minutos (120s)
const TIMEOUT_UPSTREAM_MS = Number(process.env.ANTIGRAVITY_TIMEOUT_MS || 120_000);
// Idle de stream (tempo máximo sem chunks entre tokens) aumentado para 2 minutos (120s)
const TIMEOUT_STREAM_IDLE_MS = Number(process.env.ANTIGRAVITY_STREAM_IDLE_MS || 120_000);
// Backoff máximo ao devolver 429 retryable ao cliente (ms).
const MAX_RATE_LIMIT_BACKOFF_MS = Number(process.env.ANTIGRAVITY_MAX_RATE_BACKOFF_MS || 30_000);
// Debug: ANTIGRAVITY_DEBUG=1 registra o corpo Gemini (sanitizado) de cada
// request; ANTIGRAVITY_DEBUG_MAX limita o tamanho logado (default 200 KB).
const DEBUG = /^(1|true|yes)$/i.test(process.env.ANTIGRAVITY_DEBUG || '');
const DEBUG_MAX = Number(process.env.ANTIGRAVITY_DEBUG_MAX || 200_000);
// One-shot recorder: ANTIGRAVITY_RECORD_TOOLS=/caminho/arquivo.json grava o
// array `tools` do PRIMEIRO request recebido (schemas reais que o DSH envia)
// para alimentar o teste de snapshot de schemas. Desligado por padrão.
const RECORD_TOOLS_FILE = process.env.ANTIGRAVITY_RECORD_TOOLS || '';
let toolsRecorded = false;

/** Erro HTTP do upstream com status preservado para mapear resposta ao cliente. */
class UpstreamHttpError extends Error {
  constructor(status, message) {
    super(message);
    this.name = 'UpstreamHttpError';
    this.status = status;
  }
}

/**
 * Converte erro/status do upstream em uma resposta clara e acionável para o
 * cliente (DSH), em vez de um 500 genérico:
 *  - 404 modelo inexistente -> sugere atualizar MODEL_ALIASES
 *  - 400 "Unknown name"    -> sugere o whitelist GEMINI_PARAMETERS_KEYWORDS
 *  - 401                   -> sugere re-autenticar (token expirado)
 */
function classifyUpstreamError(status, rawText, requestedModel) {
  const text = String(rawText || '');
  if (status === 404 || /Requested entity was not found|NOT_FOUND/.test(text)) {
    return {
      status: 404,
      message: `Antigravity não conhece o modelo "${requestedModel}" (404). Os ids aceitos pelo upstream estão em /v1/models; se "${requestedModel}" é um nome novo do app, adicione um alias em MODEL_ALIASES (server.mjs) e reinicie o proxy.`,
    };
  }
  const unk = text.match(/Unknown name "([^"]+)"/);
  if (status === 400 || /Invalid JSON payload|Unknown name/.test(text)) {
    const kw = unk ? ` "${unk[1]}"` : '';
    return {
      status: 400,
      message: `Antigravity rejeitou o payload das tools: keyword${kw} está fora do subconjunto Gemini de JSON Schema. Se persistir com o proxy atualizado, adicione a keyword ao whitelist GEMINI_PARAMETERS_KEYWORDS em server.mjs e reinicie. Detalhe: ${text.slice(0, 400)}`,
    };
  }
  if (status === 401) {
    return {
      status: 401,
      message: `Autenticação Antigravity falhou (401) — o refresh token pode ter expirado; refaça o login do agy/opencode (arquivos de conta do proxy). Detalhe: ${text.slice(0, 300)}`,
    };
  }
  return {
    status: typeof status === 'number' && status >= 100 ? status : 502,
    message: `Falha no upstream Antigravity (HTTP ${status}): ${text.slice(0, 400)}`,
  };
}

/** Erro de rate limit que o handler traduz em 429 retryable para o cliente. */
class RateLimitError extends Error {
  constructor(message, retryAfterSeconds = null) {
    super(message);
    this.name = 'RateLimitError';
    this.status = 429;
    this.retryAfterSeconds = retryAfterSeconds;
  }
}

/**
 * Erro de provider externo com resposta crua a ser repassada ao cliente
 * (usado no roteamento multi-provider para preservar o corpo original).
 */
class ProviderErrorPassthrough extends Error {
  constructor(status, text) {
    super(`Provider HTTP ${status}`);
    this.name = 'ProviderErrorPassthrough';
    this.status = status;
    this.text = text;
  }
}

/**
 * Corpo do Antigravity com .error (ex.: 400 de tools). Transporta o JSON cru
 * para que a classificação (classifyUpstreamError) rode por-requisição, igual
 * ao comportamento original — inclusive para chamadas coalescidas.
 */
class UpstreamBadBodyError extends Error {
  constructor(parsed) {
    super('Upstream error body');
    this.name = 'UpstreamBadBodyError';
    this.parsed = parsed;
  }
}

// Antigravity endpoints, in fallback order (primary first). Cada endpoint tem
// quota individual própria (verificado empiricamente): produção pode estar 429
// enquanto o daily responde 200 OK com a mesma conta. daily-cloudcode-pa sem
// ".sandbox" foi extraído do binário oficial do agy CLI (endpoint de produção
// do canal daily) e confirmado funcional.
//
// ORDEM IMPORTANTE: daily-cloudcode-pa vem PRIMEIRO porque nos logs reais o
// endpoint de produção (cloudcode-pa) acumulou 165x 429 e o daily ZERO 429.
// Começar pelo daily evita o ciclo "429 + backoff sleep ~1.5-2.5s + retry" a
// cada request (e no primeiro request após cada restart). O sticky
// healthyEndpointIndex por conta continua aprendendo qual endpoint respondeu
// 200 por último.
const ENDPOINTS = [
  'https://daily-cloudcode-pa.googleapis.com',
  'https://cloudcode-pa.googleapis.com',
  'https://autopush-cloudcode-pa.sandbox.googleapis.com',
  'https://daily-cloudcode-pa.sandbox.googleapis.com',
];
// Nomes curtos para exibição no dashboard (mesma ordem de ENDPOINTS).
const ENDPOINT_LABELS = ['daily (prod)', 'prod (estável)', 'autopush (sandbox)', 'daily (sandbox)'];
// Contadores globais por endpoint (todas as contas) — exibidos no painel de hosts.
const endpointHealth = ENDPOINTS.map(url => ({ url, ok: 0, r429: 0, err: 0, last429At: 0, lastOkAt: 0 }));
function bumpEndpointHealth(idx, kind, at = Date.now()) {
  const h = endpointHealth[idx];
  if (!h) return;
  if (kind === 'ok') { h.ok++; h.lastOkAt = at; }
  else if (kind === '429') { h.r429++; h.last429At = at; }
  else if (kind === 'err') h.err++;
}
// Managed project discovered via v1internal:onboardUser for the agy account.
const DEFAULT_PROJECT = process.env.ANTIGRAVITY_PROJECT || 'aicode-consumers';
// Antigravity-style randomized user agent (device fingerprint).
const USER_AGENT = process.env.ANTIGRAVITY_UA || 'antigravity/1.18.3 darwin/arm64';
const VERSION = '1.6.0';

// Upstream model name aliases: OpenAI-requested -> Antigravity model id.
//
// Fonte da verdade (validado empiricamente em 2026-09-05 contra o backend):
// catálogo real de `/v1internal:fetchAvailableModels` + chamadas reais de
// `generateContent`. Cada geração marketing é UM MODELO DISTINTO no upstream —
// nunca colapsar gerações num único id. O backend aceita APENAS ids canônicos
// (nomes marketing com sufixo de geração como "gemini-3.7-flash-high" dão 404):
//   flash:  gemini-3-flash | gemini-3.5-flash{-low,-extra-low}
//           | gemini-3.6-flash{-low,-medium,-high} | gemini-3.7-flash-tiered
//           | gemini-3.8-flash-tiered
//   pro:    gemini-3.1-pro-low | gemini-pro-agent (3.1 Pro High — o id
//           gemini-3.1-pro-high está deprecado no upstream -> 400, use
//           gemini-pro-agent) | legado gemini-3-pro-low/high (ainda aceitos)
//   legado: gemini-2.5-flash | gemini-2.5-pro
const MODEL_ALIASES = {
  // Gemini 3.8 Flash (geração atual; só existe o canal "tiered" no upstream).
  'gemini-3.8-flash': 'gemini-3.8-flash-tiered',
  'gemini-3.8-flash-low': 'gemini-3.8-flash-tiered',
  'gemini-3.8-flash-medium': 'gemini-3.8-flash-tiered',
  'gemini-3.8-flash-high': 'gemini-3.8-flash-tiered',
  'gemini-3.8-flash-none': 'gemini-3.8-flash-tiered',
  // Gemini 3.7 Flash (idem: canal "tiered").
  'gemini-3.7-flash': 'gemini-3.7-flash-tiered',
  'gemini-3.7-flash-low': 'gemini-3.7-flash-tiered',
  'gemini-3.7-flash-medium': 'gemini-3.7-flash-tiered',
  'gemini-3.7-flash-high': 'gemini-3.7-flash-tiered',
  // Gemini 3.6 Flash (ids canônicos com nível; default do agy = -high).
  'gemini-3.6-flash': 'gemini-3.6-flash-high',
  'gemini-3.6-flash-low': 'gemini-3.6-flash-low',
  'gemini-3.6-flash-medium': 'gemini-3.6-flash-medium',
  'gemini-3.6-flash-high': 'gemini-3.6-flash-high',
  // Gemini 3.5 Flash (só -low/-extra-low existem; nível médio/alto via config).
  'gemini-3.5-flash': 'gemini-3.5-flash-low',
  'gemini-3.5-flash-low': 'gemini-3.5-flash-low',
  'gemini-3.5-flash-extra-low': 'gemini-3.5-flash-extra-low',
  // Gemini 3 Flash (legado/evergreen; o nível é carregado no thinkingConfig).
  'gemini-3-flash': 'gemini-3-flash',
  'gemini-3-flash-latest': 'gemini-3-flash',
  'gemini-3-flash-low': 'gemini-3-flash',
  'gemini-3-flash-medium': 'gemini-3-flash',
  'gemini-3-flash-high': 'gemini-3-flash',
  // Gemini 3.1 Pro (atuais). -high deprecado -> vira gemini-pro-agent.
  'gemini-3.1-pro-low': 'gemini-3.1-pro-low',
  'gemini-3.1-pro-high': 'gemini-pro-agent',
  'gemini-pro-agent': 'gemini-pro-agent',
  // Pro legado (ids ainda aceitos pelo upstream; nível via thinkingConfig).
  // Obs: NÃO existe id -medium no upstream (404); medium roda na lane low.
  'gemini-3-pro': 'gemini-3-pro-low',
  'gemini-3-pro-low': 'gemini-3-pro-low',
  'gemini-3-pro-medium': 'gemini-3-pro-low',
  'gemini-3-pro-high': 'gemini-3-pro-high',
  // Gemini 2.5 (legado).
  'gemini-2.5-flash': 'gemini-2.5-flash',
  'gemini-2.5-pro': 'gemini-2.5-pro',
};

let THINKING_LEVEL = process.env.ANTIGRAVITY_THINKING_LEVEL || 'low';

function resolveModel(requested) {
  return MODEL_ALIASES[requested] || requested;
}

// ---------------------------------------------------------------------------
// Logging
// ---------------------------------------------------------------------------
// Logging: por padrão, grava apenas no stdout/console (gerenciado pelo systemd).
// O arquivo em disco proxy.log só é gravado se ANTIGRAVITY_LOG_FILE ou ANTIGRAVITY_DEBUG estiver ativo.
const ENABLE_FILE_LOG = Boolean(process.env.ANTIGRAVITY_LOG_FILE || process.env.ANTIGRAVITY_DEBUG === '1');
const LOG_FILE_PATH = process.env.ANTIGRAVITY_LOG_FILE
  || path.join(path.dirname(fileURLToPath(import.meta.url)), 'proxy.log');

function log(...args) {
  const line = `[${new Date().toISOString()}] ${args.join(' ')}`;
  console.log(line);
  if (ENABLE_FILE_LOG) {
    try {
      fs.appendFileSync(LOG_FILE_PATH, line + '\n');
    } catch { /* ignore */ }
  }
}

// ---------------------------------------------------------------------------
// Rede: fetch com timeout, semáforo de concorrência, backoff
// ---------------------------------------------------------------------------
const sleep = (ms) => new Promise(r => setTimeout(r, ms));

/** 
 * fetch() com Keep-Alive e timeout configurável.
 * Se cancelOnHeaders === true, o timeout cancela o timer assim que os headers chegarem,
 * garantindo que o stream de dados em si não seja abortado por um timer fixo.
 */
async function fetchWithTimeout(url, options = {}, timeoutMs = TIMEOUT_UPSTREAM_MS, cancelOnHeaders = false) {
  if (!cancelOnHeaders) {
    return fetch(url, { ...options, keepalive: true, signal: AbortSignal.timeout(timeoutMs) });
  }
  const ctrl = new AbortController();
  const timer = setTimeout(() => {
    ctrl.abort(new Error(`Timeout de conexão/headers upstream (${timeoutMs}ms)`));
  }, timeoutMs);
  try {
    const res = await fetch(url, { ...options, keepalive: true, signal: ctrl.signal });
    clearTimeout(timer);
    return res;
  } catch (err) {
    clearTimeout(timer);
    throw err;
  }
}

// ---------------------------------------------------------------------------
// HTTP/2 Client Session Pool (Multiplexado com ALPN h2 e fallback seguro)
// Mantém conexões HTTP/2 ativas para os endpoints do Google Cloud Code,
// eliminando overhead de handshakes TCP/TLS repetidos e reduzindo o TTFT.
// ---------------------------------------------------------------------------
const h2Sessions = new Map(); // origin (ex: 'https://daily-cloudcode-pa.sandbox.googleapis.com') -> ClientHttp2Session

function getH2Session(origin) {
  let session = h2Sessions.get(origin);
  if (session && !session.closed && !session.destroyed) {
    return session;
  }
  try {
    session = http2.connect(origin, {
      settings: {
        enablePush: false,
        initialWindowSize: 4 * 1024 * 1024, // 4MB flow-control window
      }
    });
    session.on('error', (err) => {
      // Falhas silenciosas de sessão são normais ao expirar keep-alive
      h2Sessions.delete(origin);
    });
    session.on('goaway', () => {
      h2Sessions.delete(origin);
    });
    session.on('close', () => {
      h2Sessions.delete(origin);
    });
    // Manter o socket com keepAlive ativo
    session.setKeepAlive(true, 15000);
    h2Sessions.set(origin, session);
    return session;
  } catch (e) {
    return null;
  }
}

/**
 * Executa uma requisição upstream via HTTP/2 multiplexado nativo.
 * Retorna um objeto Response-compatível com .status, .ok, .headers, .text(), .json() e .body (ReadableStream).
 * Se o HTTP/2 falhar na negociação ou no transporte inicial, recorre transparentemente ao fetchWithTimeout (HTTP/1.1).
 */
async function fetchUpstreamH2(urlStr, options = {}, timeoutMs = TIMEOUT_UPSTREAM_MS, isStream = false) {
  let parsedUrl;
  try {
    parsedUrl = new URL(urlStr);
  } catch (e) {
    return fetchWithTimeout(urlStr, options, timeoutMs, isStream);
  }

  const origin = parsedUrl.origin;
  const pathWithQuery = parsedUrl.pathname + parsedUrl.search;
  const session = getH2Session(origin);
  if (!session) {
    return fetchWithTimeout(urlStr, options, timeoutMs, isStream);
  }

  return new Promise((resolve, reject) => {
    let settled = false;
    let timer = null;

    const cleanup = () => {
      if (timer) {
        clearTimeout(timer);
        timer = null;
      }
    };

    if (timeoutMs > 0) {
      timer = setTimeout(() => {
        if (!settled) {
          settled = true;
          try { h2Req.close(http2.constants.NGHTTP2_CANCEL); } catch (_) {}
          reject(new Error(`Timeout upstream HTTP/2 (${timeoutMs}ms)`));
        }
      }, timeoutMs);
    }

    const headersToSend = {
      ':method': options.method || 'POST',
      ':path': pathWithQuery,
    };

    if (options.headers) {
      for (const [k, v] of Object.entries(options.headers)) {
        const lk = k.toLowerCase();
        if (!lk.startsWith(':') && lk !== 'connection' && lk !== 'host' && lk !== 'keep-alive') {
          headersToSend[lk] = v;
        }
      }
    }

    let h2Req;
    try {
      h2Req = session.request(headersToSend);
    } catch (err) {
      cleanup();
      // Se a sessão estiver instável, tenta fallback imediato HTTP/1.1
      h2Sessions.delete(origin);
      return fetchWithTimeout(urlStr, options, timeoutMs, isStream).then(resolve, reject);
    }

    h2Req.on('error', (err) => {
      cleanup();
      if (!settled) {
        settled = true;
        // Fallback para HTTP/1.1 se a requisição quebrou antes de receber headers
        fetchWithTimeout(urlStr, options, timeoutMs, isStream).then(resolve, reject);
      }
    });

    h2Req.on('response', (headers) => {
      cleanup(); // Headers recebidos: limpa o timer de headers/conexão
      settled = true;

      const status = Number(headers[':status'] || 200);
      const ok = status >= 200 && status < 300;

      // Converte a stream do Node h2Req em um ReadableStream compatível com o getReader() do fetch nativo
      const nodeReadable = isStream ? Readable.from(h2Req) : null;
      const webStream = isStream ? Readable.toWeb(nodeReadable) : null;

      const responseAdapter = {
        status,
        ok,
        headers: {
          get: (name) => headers[name.toLowerCase()] || null,
        },
        body: webStream,
        async text() {
          let chunks = '';
          h2Req.setEncoding('utf8');
          for await (const chunk of h2Req) chunks += chunk;
          return chunks;
        },
        async json() {
          const raw = await this.text();
          return JSON.parse(raw);
        }
      };

      resolve(responseAdapter);
    });

    if (options.body) {
      h2Req.write(options.body);
    }
    h2Req.end();
  });
}

// Cache persistente em memória de assinaturas reais de thought por tool_call.id (Gemini 3)
const thoughtSignaturesByCallId = new Map();

// Telemetria e Dashboard em memória
const recentRequests = [];
const requestTimestamps = []; // timestamps para cálculo de RPM da janela de 60s
const modelUsageStats = {};   // model -> { requests: number, totalTokens: number }
let totalRequestsCount = 0;
let totalLatencySum = 0;
let cachedQuotaInfo = null;
let lastQuotaFetchTime = 0;

function recordRequestTelemetry(model, stream, status, duration, usage = null, cacheState = null) {
  const now = Date.now();
  totalRequestsCount++;
  totalLatencySum += duration;
  requestTimestamps.push(now);

  const tokens = usage?.total_tokens || 0;
  if (!modelUsageStats[model]) {
    modelUsageStats[model] = { requests: 0, totalTokens: 0 };
  }
  modelUsageStats[model].requests++;
  modelUsageStats[model].totalTokens += tokens;

  recentRequests.unshift({
    model,
    stream: Boolean(stream),
    status,
    duration,
    tokens,
    time: new Date().toLocaleTimeString('pt-BR')
  });
  if (recentRequests.length > 50) recentRequests.pop();

  usageStatsIngest(model, status, duration, usage, cacheState);
}

// ---------------------------------------------------------------------------
// Estatística de uso (tokens/h 24h, janela 60min, por modelo, cache HIT/MISS)
// Estruturas em memória; alimentadas por usageStatsIngest() a cada request.
// ---------------------------------------------------------------------------
const USAGE_HOUR_MS = 3600e3;
const USAGE_MIN_MS = 60e3;
const usageStats = {
  bootAt: Date.now(),
  cacheHits: 0,
  cacheHitsStream: 0,
  cacheHitsSemantic: 0,
  cacheMisses: 0,
  totals: { requests: 0, errors: 0, in: 0, out: 0, cached: 0, ms: 0, cacheSavedTokens: 0 },
  byModel: {},                       // model -> agregado
  hours: new Map(),                  // ts(início da hora) -> bucket
  minutes: new Map(),                // ts(início do minuto) -> bucket
};

function usageBucketBase() {
  return { req: 0, err: 0, in: 0, out: 0, cached: 0, hits: 0, hitsStream: 0, hitsSemantic: 0, misses: 0 };
}

function usageStatsIngest(model, status, durationMs, usage, cacheState) {
  try {
    const now = Date.now();
    const isErr = Number(status) >= 400;
    const inTok = Number(usage?.prompt_tokens) || 0;
    const outTok = Number(usage?.completion_tokens) || 0;
    const cachedTok = Number(usage?.prompt_tokens_details?.cached_tokens)
      || Number(usage?.cached_tokens)
      || Number(usage?.cache_read_input_tokens) || 0;
    const hitStream = cacheState === 'HIT_STREAM_LOCAL';
    const hitJson = cacheState === 'HIT';
    const hitSemantic = cacheState === 'HIT_SEMANTIC';
    const hit = hitStream || hitJson || hitSemantic;
    const miss = cacheState === 'MISS' || cacheState === 'COALESCED';

    const T = usageStats.totals;
    T.requests++; if (isErr) T.errors++;
    if (hit) {
      // Servido do cache local: não consome tokens no upstream — conta a economia.
      if (hitStream) usageStats.cacheHitsStream++;
      else if (hitSemantic) usageStats.cacheHitsSemantic++;
      else usageStats.cacheHits++;
      T.cacheSavedTokens += inTok + outTok;
    } else {
      T.in += inTok; T.out += outTok; T.cached += cachedTok; T.ms += durationMs;
      if (miss) usageStats.cacheMisses++;
    }

    const M = usageStats.byModel[model] || (usageStats.byModel[model] =
      { requests: 0, errors: 0, in: 0, out: 0, cached: 0, hits: 0, hitsStream: 0, hitsSemantic: 0, misses: 0, ms: 0, last: 0 });
    M.requests++; if (isErr) M.errors++;
    if (hit) {
      if (hitStream) M.hitsStream = (M.hitsStream || 0) + 1;
      else if (hitSemantic) M.hitsSemantic = (M.hitsSemantic || 0) + 1;
      else M.hits++;
    } else {
      M.in += inTok; M.out += outTok; M.cached += cachedTok; M.ms += durationMs;
      if (miss) M.misses++;
    }
    M.last = now;

    const hTs = Math.floor(now / USAGE_HOUR_MS) * USAGE_HOUR_MS;
    let hb = usageStats.hours.get(hTs);
    if (!hb) { hb = { ts: hTs, ...usageBucketBase() }; usageStats.hours.set(hTs, hb); }
    const mTs = Math.floor(now / USAGE_MIN_MS) * USAGE_MIN_MS;
    let mb = usageStats.minutes.get(mTs);
    if (!mb) { mb = { ts: mTs, ...usageBucketBase() }; usageStats.minutes.set(mTs, mb); }
    const bump = (b) => {
      b.req++; if (isErr) b.err++;
      if (hit) {
        if (hitStream) b.hitsStream = (b.hitsStream || 0) + 1;
        else if (hitSemantic) b.hitsSemantic = (b.hitsSemantic || 0) + 1;
        else b.hits++;
        return;
      }
      b.in += inTok; b.out += outTok; b.cached += cachedTok;
      if (miss) b.misses++;
    };
    bump(hb); bump(mb);

    // Poda: mantém 26h de horas e 61min de minutos.
    const cutoffH = now - 26 * USAGE_HOUR_MS;
    for (const k of usageStats.hours.keys()) if (k < cutoffH) usageStats.hours.delete(k);
    const cutoffM = now - 61 * USAGE_MIN_MS;
    for (const k of usageStats.minutes.keys()) if (k < cutoffM) usageStats.minutes.delete(k);
  } catch (e) {
    log('WARN usageStatsIngest:', e.message);
  }
}

function usagePad2(n) { return String(n).padStart(2, '0'); }
function usageHourLabel(ts) { const d = new Date(ts); return `${usagePad2(d.getHours())}:00`; }
function usageMinLabel(ts) { const d = new Date(ts); return `${usagePad2(d.getHours())}:${usagePad2(d.getMinutes())}`; }

function usageStatsSnapshot() {
  const now = Date.now();
  const hourStart = Math.floor(now / USAGE_HOUR_MS) * USAGE_HOUR_MS;
  const minStart = Math.floor(now / USAGE_MIN_MS) * USAGE_MIN_MS;

  const hours = [];
  for (let i = 23; i >= 0; i--) {
    const ts = hourStart - i * USAGE_HOUR_MS;
    const b = usageStats.hours.get(ts) || usageBucketBase();
    hours.push({
      label: usageHourLabel(ts),
      requests: b.req, errors: b.err,
      in: b.in, out: b.out, cached: b.cached,
      hits: b.hits, hitsStream: b.hitsStream || 0, hitsSemantic: b.hitsSemantic || 0, misses: b.misses,
    });
  }
  const minutes = [];
  for (let i = 59; i >= 0; i--) {
    const ts = minStart - i * USAGE_MIN_MS;
    const b = usageStats.minutes.get(ts) || usageBucketBase();
    minutes.push({
      label: usageMinLabel(ts),
      requests: b.req, errors: b.err,
      in: b.in, out: b.out, cached: b.cached,
      hits: b.hits, hitsStream: b.hitsStream || 0, hitsSemantic: b.hitsSemantic || 0, misses: b.misses,
    });
  }

  const models = Object.entries(usageStats.byModel)
    .map(([model, m]) => ({
      model,
      requests: m.requests, errors: m.errors,
      in: m.in, out: m.out, cached: m.cached,
      hits: m.hits,
      hitsStream: m.hitsStream || 0,
      hitsSemantic: m.hitsSemantic || 0,
      misses: m.misses,
      avgMs: m.requests ? Math.round(m.ms / m.requests) : 0,
      last: m.last,
    }))
    .sort((a, b) => (b.in + b.out + b.cached) - (a.in + a.out + a.cached));

  let tok24 = 0, req24 = 0, cached24 = 0;
  for (const h of hours) { tok24 += h.in + h.out; req24 += h.requests; cached24 += h.cached; }
  let req60 = 0;
  for (const m of minutes) req60 += m.requests;

  const T = usageStats.totals;
  return {
    uptimeSec: Math.round((now - usageStats.bootAt) / 1000),
    kpi: {
      tokens24h: tok24,
      tokensIn24h: hours.reduce((s, h) => s + h.in, 0),
      tokensOut24h: hours.reduce((s, h) => s + h.out, 0),
      cachedInput24h: cached24,
      requests24h: req24,
      requests60min: req60,
      requestsTotal: T.requests,
      errorsTotal: T.errors,
      avgMs: T.requests ? Math.round(T.ms / T.requests) : 0,
      cacheHits: usageStats.cacheHits,
      cacheHitsStream: usageStats.cacheHitsStream || 0,
      cacheHitsSemantic: usageStats.cacheHitsSemantic || 0,
      cacheMisses: usageStats.cacheMisses,
      cacheSavedTokens: T.cacheSavedTokens,
    },
    costToday: spendState?.days?.[localDayKey()]?.totalCost || 0,
    hours,
    minutes,
    models,
  };
}

async function fetchAntigravityQuotas() {
  const now = Date.now();
  if (cachedQuotaInfo && (now - lastQuotaFetchTime < 30_000)) {
    return cachedQuotaInfo;
  }
  ensureAccountsPool();
  if (accountsPool.length === 0) return null;

  const accountsQuota = [];
  for (let i = 0; i < accountsPool.length; i++) {
    const acc = accountsPool[i];
    try {
      const token = await acc.getAccessToken();
      const ep = 'https://daily-cloudcode-pa.sandbox.googleapis.com/v1internal:fetchAvailableModels';
      const resp = await fetch(ep, {
        method: 'POST',
        headers: {
          'Authorization': `Bearer ${token}`,
          'Content-Type': 'application/json',
          'User-Agent': 'antigravity'
        },
        body: JSON.stringify({})
      });
      if (resp.ok) {
        const data = await resp.json();
        const models = data.models || {};
        const claudeInfo = models['claude-sonnet-4-6']?.quotaInfo || models['claude-opus-4-6-thinking']?.quotaInfo;
        const geminiInfo = models['gemini-3.8-flash-tiered']?.quotaInfo || models['gemini-3.6-flash-high']?.quotaInfo;
        
        const calcFraction = (info) => {
          if (!info) return 1;
          if (typeof info.remainingFraction === 'number') return info.remainingFraction;
          // Se a Google enviou resetTime mas omitiu remainingFraction, significa cota individual esgotada (0%)!
          if (info.resetTime) return 0;
          return 1;
        };

        const coolingSeconds = accountCoolingSeconds(acc);
        const claudeFrac = calcFraction(claudeInfo);
        // Se a conta está em cooldown por 429 de cota, a fração do Gemini é 0
        const geminiFrac = coolingSeconds > 0 ? 0 : calcFraction(geminiInfo);

        accountsQuota.push({
          accountIndex: i + 1,
          accountName: acc.name,
          coolingFor: coolingSeconds || undefined,
          claude: claudeInfo ? {
            remainingFraction: claudeFrac,
            resetTime: claudeInfo.resetTime,
            isExhausted: claudeFrac === 0,
          } : null,
          gemini: geminiInfo ? {
            remainingFraction: geminiFrac,
            resetTime: geminiInfo.resetTime,
            isExhausted: geminiFrac === 0,
          } : null
        });
      }
    } catch (err) {
      log(`WARN failed to fetch quota for account ${acc.name}:`, err.message);
    }
  }

  cachedQuotaInfo = {
    accountsQuota,
    fetchedAt: new Date().toISOString()
  };
  lastQuotaFetchTime = now;
  return cachedQuotaInfo;
}

function calculateRpm() {
  const cutoff = Date.now() - 60_000;
  // Remove timestamps mais antigos que 60 segundos
  while (requestTimestamps.length > 0 && requestTimestamps[0] < cutoff) {
    requestTimestamps.shift();
  }
  return requestTimestamps.length;
}

// Semáforo FIFO simples: limita requests upstream concorrentes.
let activeUpstream = 0;
const upstreamWaiters = [];
async function acquireUpstream() {
  if (activeUpstream < MAX_CONCURRENT) {
    activeUpstream += 1;
    return;
  }
  await new Promise(resolve => upstreamWaiters.push(resolve));
  activeUpstream += 1;
}
function releaseUpstream() {
  activeUpstream -= 1;
  const next = upstreamWaiters.shift();
  if (next) next();
}

/** Backoff exponencial com jitter para espera entre tentativas. */
function backoffDelay(attempt, retryAfterSeconds = null) {
  if (typeof retryAfterSeconds === 'number' && retryAfterSeconds > 0) {
    return Math.min(retryAfterSeconds * 1000, MAX_RATE_LIMIT_BACKOFF_MS);
  }
  const base = Math.min(1500 * Math.pow(2, attempt), MAX_RATE_LIMIT_BACKOFF_MS);
  return base + Math.random() * 500;
}

/** 429 é quota da conta ou do endpoint? Quota da conta não melhora trocando endpoint. */
function isAccountQuota(bodyText) {
  return /quota|RESOURCE_EXHAUSTED|limit|rate/i.test(bodyText || '');
}

// ---------------------------------------------------------------------------
// OAuth token management
// ---------------------------------------------------------------------------
// ---------------------------------------------------------------------------
// Account Pool Management
// ---------------------------------------------------------------------------
class Account {
  constructor(name, refreshToken, source) {
    this.name = name;
    this.refreshToken = refreshToken;
    this.source = source;
    this.accessToken = null;
    this.expiresAt = 0;
    this.projectId = DEFAULT_PROJECT;
    this.projectChecked = false;
    this.healthyEndpointIndex = 0;
    this.refreshInFlight = null;
    // Controle de endpoint por conta (painel "Hosts Antigravity")
    this.pinnedEndpointIndex = null;          // null = sistema escolhe (auto); 0..3 = fixo
    this.currentIndex = 0;                    // endpoint do último sucesso
    this.lastSwitchAt = 0;                    // quando o endpoint em uso mudou
    this.endpointStats = ENDPOINTS.map(() => ({ ok: 0, r429: 0, err: 0 }));
    // Latência EWMA (Exponential Moving Average) por endpoint: inicializa com 500ms
    this.endpointEwma = ENDPOINTS.map(() => 500);
    // Circuit breaker local por endpoint/conta: timestamp até quando o endpoint está isolado
    this.endpointQuarantineUntil = ENDPOINTS.map(() => 0);
  }

  /** Ordem de tentativa: fixo ou inteligente via EWMA com desempate por histórico de saúde. */
  endpointOrder() {
    const n = ENDPOINTS.length;
    if (this.pinnedEndpointIndex !== null && this.pinnedEndpointIndex !== undefined
      && this.pinnedEndpointIndex >= 0 && this.pinnedEndpointIndex < n) {
      return [this.pinnedEndpointIndex];
    }
    const now = Date.now();
    const indices = [];
    for (let i = 0; i < n; i++) indices.push(i);

    // Ordena pelo menor score (prioriza endpoints fora de quarentena e com menor EWMA de latência)
    indices.sort((a, b) => {
      const qA = this.endpointQuarantineUntil[a] > now ? 100_000 : 0;
      const qB = this.endpointQuarantineUntil[b] > now ? 100_000 : 0;
      const ewmaA = (this.endpointEwma[a] || 500) + qA;
      const ewmaB = (this.endpointEwma[b] || 500) + qB;
      return ewmaA - ewmaB;
    });

    return indices;
  }

  recordEndpointLatency(idx, ms) {
    if (idx < 0 || idx >= ENDPOINTS.length) return;
    const alpha = 0.25; // peso da observação mais recente
    const prev = this.endpointEwma[idx] || 500;
    this.endpointEwma[idx] = Math.round(prev * (1 - alpha) + ms * alpha);
  }

  quarantineEndpoint(idx, durationMs = 60_000) {
    if (idx >= 0 && idx < ENDPOINTS.length) {
      this.endpointQuarantineUntil[idx] = Date.now() + durationMs;
    }
  }

  bumpEndpointStats(idx, kind) {
    const s = this.endpointStats[idx];
    if (!s) return;
    if (kind === 'ok') {
      s.ok++;
      this.endpointQuarantineUntil[idx] = 0; // sucesso remove quarentena imediatamente
    } else if (kind === '429') {
      s.r429++;
      this.quarantineEndpoint(idx, 60_000); // 429 isola o endpoint por 60s
    } else if (kind === 'err') {
      s.err++;
      this.quarantineEndpoint(idx, 15_000); // erro de rede isola por 15s
    }
  }

  async getAccessToken() {
    const now = Date.now();
    if (this.accessToken && this.expiresAt > now + 15_000) {
      // Renovação proativa em background: se faltam menos de 5 minutos para expirar,
      // dispara a renovação sem bloquear a requisição atual do usuário (~300ms economizados).
      if (this.expiresAt < now + 300_000 && !this.refreshInFlight) {
        this.refreshAccessToken().catch(err => log(`[Account ${this.name}] Background token refresh WARN: ${err.message}`));
      }
      return this.accessToken;
    }
    return this.refreshAccessToken();
  }

  async refreshAccessToken() {
    if (this.refreshInFlight) return this.refreshInFlight;
    this.refreshInFlight = (async () => {
      const body = new URLSearchParams({
        grant_type: 'refresh_token',
        refresh_token: this.refreshToken,
        client_id: CLIENT_ID,
        client_secret: CLIENT_SECRET,
      });
      const res = await fetchWithTimeout('https://oauth2.googleapis.com/token', {
        method: 'POST',
        headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
        body,
      }, 15000);
      if (!res.ok) {
        const text = await res.text().catch(() => '');
        throw new Error('Token refresh failed (' + res.status + '): ' + text.slice(0, 300));
      }
      const data = await res.json();
      this.accessToken = data.access_token;
      this.expiresAt = Date.now() + (data.expires_in || 3599) * 1000;
      log(`[Account ${this.name}] Refreshed access token (expires in ${data.expires_in || 3599}s)`);
      return this.accessToken;
    })();
    try {
      return await this.refreshInFlight;
    } finally {
      this.refreshInFlight = null;
    }
  }

  async ensureProject(accessToken) {
    // Cache permanente: o discovery roda UMA vez por processo/conta. O bug
    // clássico (`&& this.projectId !== DEFAULT_PROJECT`) tornava a condição
    // sempre falsa quando o projeto da conta == DEFAULT_PROJECT, então o
    // proxy executava loadCodeAssist/onboardUser em até 4 endpoints em CADA
    // request (~4-6s perdidos por mensagem).
    if (this.projectChecked) return this.projectId;
    let projId = DEFAULT_PROJECT;
    const headers = {
      'Content-Type': 'application/json',
      Authorization: 'Bearer ' + accessToken,
      'User-Agent': 'google-api-nodejs-client/9.15.1',
      'X-Goog-Api-Client': 'google-cloud-sdk vscode_cloudshelleditor/0.1',
    };
    try {
      for (const ep of ENDPOINTS) {
        try {
          const res = await fetchWithTimeout(ep + '/v1internal:loadCodeAssist', {
            method: 'POST',
            headers,
            body: JSON.stringify({ metadata: { ideType: 'ANTIGRAVITY', platform: 1, pluginType: 'GEMINI' } }),
          }, 10000);
          if (res.ok) {
            const payload = await res.json();
            const managed = payload?.cloudaicompanionProject?.id || payload?.cloudaicompanionProject;
            if (typeof managed === 'string' && managed) {
              projId = managed;
              break;
            }
          }
        } catch { /* try next endpoint */ }
      }
    } catch (e) {
      log(`[Account ${this.name}] WARN project discovery failed:`, e.message);
    }

    if (projId === DEFAULT_PROJECT) {
      for (const ep of ENDPOINTS) {
        try {
          const res = await fetchWithTimeout(ep + '/v1internal:onboardUser', {
            method: 'POST',
            headers: {
              'Content-Type': 'application/json',
              Authorization: 'Bearer ' + accessToken,
              'User-Agent': USER_AGENT,
              'X-Goog-Api-Client': 'google-cloud-sdk vscode_cloudshelleditor/0.1',
              'Client-Metadata': JSON.stringify({ ideType: 'ANTIGRAVITY', platform: 'WINDOWS', pluginType: 'GEMINI' }),
            },
            body: JSON.stringify({
              tierId: 'standard-tier',
              metadata: { ideType: 'ANTIGRAVITY', platform: 1, pluginType: 'GEMINI' },
            }),
          }, 10000);
          if (res.ok) {
            const payload = await res.json();
            const id = payload?.response?.cloudaicompanionProject?.id;
            if (id) {
              projId = id;
              break;
            }
          }
        } catch { /* next */ }
      }
    }
    this.projectId = projId;
    this.projectChecked = true;
    log(`[Account ${this.name}] Project context: ${this.projectId}`);
    return this.projectId;
  }
}

let accountsPool = [];
let activeAccountIndex = 0;
let accountPinned = false; // true = usuário fixou uma conta prioritária no painel
let lastPoolLoad = 0;

// Cooldown por conta: após falhas de cota (429), 5xx ou rede, a conta fica
// temporariamente fora das tentativas — evita "queimar" o request em conta sem cota.
// Chave = refreshToken (estável mesmo se o pool recarregar e os índices mudarem).
const accountCooldownUntil = new Map(); // refreshToken -> timestamp (ms)

function accCooldownKey(acc) {
  return acc?.refreshToken || acc?.name || 'unknown';
}

function cooldownMsForError(e) {
  if (!e) return 0;
  if (e instanceof RateLimitError || e.status === 429) {
    const ra = e.retryAfterSeconds;
    if (Number.isFinite(ra) && ra > 0) return Math.min(ra * 1000 + 1000, 5 * 60 * 1000);
    return 3 * 60 * 1000; // 429 sem retry-after: 3 min
  }
  if (e instanceof UpstreamHttpError || (typeof e.status === 'number' && e.status >= 500)) {
    return 30_000; // 5xx/erro de rede: 30s
  }
  return 0; // 4xx de request (400/401 etc.): sem cooldown (erro do cliente/conteúdo)
}

function accountCoolingSeconds(acc) {
  const until = accountCooldownUntil.get(accCooldownKey(acc)) || 0;
  return until > Date.now() ? Math.ceil((until - Date.now()) / 1000) : 0;
}

function loadAccountsPool() {
  const nextPool = [];
  const seen = new Set();

  const addOrReuse = (name, rt, source) => {
    if (seen.has(rt)) return;
    seen.add(rt);
    const existing = accountsPool.find(a => a.refreshToken === rt);
    if (existing) {
      nextPool.push(existing);
    } else {
      nextPool.push(new Account(name, rt, source));
    }
  };

  // 1) Conta do agy CLI
  try {
    if (fs.existsSync(TOKEN_FILE)) {
      const data = JSON.parse(fs.readFileSync(TOKEN_FILE, 'utf8'));
      const rt = data?.token?.refresh_token;
      if (rt) {
        // Tenta identificar o email se disponível ou usa nome dinâmico
        addOrReuse('agy-cli (savagelaneband@gmail.com)', rt, TOKEN_FILE);
      }
    }
  } catch (e) {
    log('WARN token file unreadable:', e.message);
  }

  // 2) Contas do opencode
  try {
    if (fs.existsSync(OPENCODE_ACCOUNTS)) {
      const data = JSON.parse(fs.readFileSync(OPENCODE_ACCOUNTS, 'utf8'));
      if (Array.isArray(data?.accounts)) {
        for (const acc of data.accounts) {
          if (acc.enabled !== false && acc.refreshToken) {
            const name = acc.email ? `opencode (${acc.email})` : `opencode-acc-${nextPool.length}`;
            addOrReuse(name, acc.refreshToken, OPENCODE_ACCOUNTS);
          }
        }
      }
    }
  } catch (e) {
    log('WARN opencode accounts unreadable:', e.message);
  }

  if (nextPool.length === 0) {
    log('FATAL: No accounts loaded in pool!');
  } else {
    // Apenas logar se o pool de fato mudou (novas contas adicionadas/removidas)
    const changed = nextPool.length !== accountsPool.length || nextPool.some((a, i) => accountsPool[i]?.refreshToken !== a.refreshToken);
    if (changed) {
      log(`Loaded ${nextPool.length} accounts into pool.`);
      for (const acc of nextPool) {
        log(`  - Account: ${acc.name} (source: ${acc.source})`);
      }
      // Pre-warming de conexão TLS com o endpoint primário
      fetchWithTimeout(ENDPOINTS[0] + '/v1internal:loadCodeAssist', { method: 'HEAD' }, 3000).catch(() => {});
    }
  }
  accountsPool = nextPool;
  if (activeAccountIndex >= accountsPool.length) {
    activeAccountIndex = 0;
    accountPinned = false; // pool encolheu: pin não é mais válido
  }
}

function ensureAccountsPool() {
  if (accountsPool.length === 0 || Date.now() - lastPoolLoad > 60_000) {
    loadAccountsPool();
    lastPoolLoad = Date.now();
  }
}

// ---------------------------------------------------------------------------
// Translation: OpenAI -> Antigravity (Gemini)
// ---------------------------------------------------------------------------
function textFromOpenAiContent(content) {
  if (typeof content === 'string') return content;
  if (Array.isArray(content)) {
    return content
      .map(b => (typeof b === 'string' ? b : (b?.type === 'text' ? b.text : '')))
      .filter(Boolean)
      .join('\n');
  }
  return String(content ?? '');
}

// ---------------------------------------------------------------------------
// Tool parameter schema sanitization (OpenAI JSON Schema -> Gemini subset)
// ---------------------------------------------------------------------------
// Gemini's `parameters` field accepts only a small subset of OpenAPI 3.0 JSON
// Schema. Clients such as DSH send full JSON Schema (draft 2020-12): `$defs`
// + `$ref` for repeated/recursive shapes plus extra keywords such as
// `uniqueItems`/`pattern`/`minLength`/`oneOf`. The Antigravity API rejects
// anything outside its subset with a 400 "Unknown name ..." (observed so far:
// `$ref`, `$defs`, `uniqueItems`), so we whitelist what it accepts:
//   - dereference every `$ref` against the collected $defs/definitions
//   - drop every keyword outside the supported set
//   - map `oneOf` -> `anyOf` (documented as interpreted the same)
const GEMINI_PARAMETERS_KEYWORDS = new Set([
  'type', 'format', 'description', 'nullable', 'enum', 'items', 'properties',
  'required', 'anyOf', 'additionalProperties', 'propertyOrdering',
]);

/** Recursively collect every $defs/definitions table reachable from `root`. */
function collectSchemaDefinitions(root, into = {}) {
  if (Array.isArray(root)) {
    for (const item of root) collectSchemaDefinitions(item, into);
    return into;
  }
  if (!root || typeof root !== 'object') return into;
  for (const key of ['$defs', 'definitions']) {
    const table = root[key];
    if (table && typeof table === 'object' && !Array.isArray(table)) {
      for (const [name, def] of Object.entries(table)) {
        // First/outermost occurrence wins on name clashes.
        if (!(name in into)) into[name] = def;
      }
    }
  }
  for (const value of Object.values(root)) collectSchemaDefinitions(value, into);
  return into;
}

/**
 * Produce a Gemini/OpenAPI-safe schema from a JSON Schema node.
 * - resolves `$ref` (`#/$defs/Name`, `#/definitions/Name`) against `defs`
 * - strips JSON Schema meta keys ($defs, $schema, $id, ...)
 * - guards recursive definitions: a ref repeated on the current expansion
 *   path becomes `{}` (unconstrained) instead of inlining forever
 */
function toGeminiSchema(node, defs, expanding = new Set()) {
  if (Array.isArray(node)) return node.map(item => toGeminiSchema(item, defs, expanding));
  if (!node || typeof node !== 'object') return node;

  if (typeof node.$ref === 'string') {
    const prefixDollar = '#/$defs/';
    const prefixDefs = '#/definitions/';
    const name = node.$ref.startsWith(prefixDollar)
      ? node.$ref.slice(prefixDollar.length)
      : node.$ref.startsWith(prefixDefs)
        ? node.$ref.slice(prefixDefs.length)
        : null;
    if (name !== null && Object.prototype.hasOwnProperty.call(defs, name)) {
      if (expanding.has(name)) return {}; // recursive shape: stop inlining
      const next = new Set(expanding);
      next.add(name);
      const resolved = toGeminiSchema(defs[name], defs, next);
      // Keep sibling annotations (e.g. description) that travel with the $ref.
      const result = { ...resolved };
      for (const [key, value] of Object.entries(node)) {
        if (key === '$ref') continue;
        if (!(key in result)) result[key] = toGeminiSchema(value, defs, next);
      }
      return result;
    }
    // Unresolvable pointer: drop the constraint rather than send a bad $ref.
    return {};
  }

  const result = {};
  for (const [key, value] of Object.entries(node)) {
    if (key === 'oneOf') {
      // Documented: oneOf is interpreted the same as anyOf.
      result.anyOf = toGeminiSchema(value, defs, expanding);
      continue;
    }
    if (!GEMINI_PARAMETERS_KEYWORDS.has(key)) continue; // drops $ref/$defs/etc.
    if (key === 'properties' && value && typeof value === 'object' && !Array.isArray(value)) {
      // `properties` is a map of arbitrary parameter names -> schemas; the
      // names are NOT keywords and must not be filtered by the whitelist.
      const props = {};
      for (const [name, schema] of Object.entries(value)) {
        props[name] = toGeminiSchema(schema, defs, expanding);
      }
      result.properties = props;
      continue;
    }
    result[key] = toGeminiSchema(value, defs, expanding);
  }
  return result;
}

/**
 * Cache LRU em memória para Schemas de Tools sanitizadas.
 * Como o DSH envia um conjunto idêntico de dezenas de ferramentas a cada turno,
 * ordenar e converter os schemas sob hash SHA-256 poupa tempo de CPU e
 * garante que a declaração no Gemini tenha prefixo 100% canônico e estável.
 */
const toolSchemaCache = new Map();
const TOOL_SCHEMA_CACHE_MAX = 200;

function getCanonicalToolDeclarations(tools) {
  if (!Array.isArray(tools) || tools.length === 0) return [];
  
  // Hash canônico das ferramentas recebidas
  const rawKey = sha256Hex(stableSerialize(tools));
  const cached = toolSchemaCache.get(rawKey);
  if (cached) {
    // Move para o final (LRU)
    toolSchemaCache.delete(rawKey);
    toolSchemaCache.set(rawKey, cached);
    return cached;
  }

  // Ordena deterministicamente por nome para estabilidade perfeita do Prefix Cache do Google
  const validFunctions = tools
    .filter(t => t?.type === 'function' && t.function && t.function.name)
    .slice()
    .sort((a, b) => String(a.function.name).localeCompare(String(b.function.name)));

  const declarations = validFunctions.map(t => ({
    name: t.function.name,
    description: t.function.description || '',
    parameters: sanitizeToolParameters(t.function.parameters),
  }));

  if (toolSchemaCache.size >= TOOL_SCHEMA_CACHE_MAX) {
    const oldestKey = toolSchemaCache.keys().next().value;
    if (oldestKey) toolSchemaCache.delete(oldestKey);
  }
  toolSchemaCache.set(rawKey, declarations);
  return declarations;
}

/** Convert an OpenAI function `parameters` object to a Gemini-safe schema. */
function sanitizeToolParameters(parameters) {
  const base = parameters && typeof parameters === 'object'
    ? parameters
    : { type: 'object', properties: {} };
  const defs = collectSchemaDefinitions(base);
  return toGeminiSchema(base, defs);
}

/**
 * Estabilizador de Prefixo para Prompt Caching (Google Gemini & OpenAI):
 * Separa linhas voláteis (ex: "Current time: ...", "Session ID: ...", "Date: ...")
 * do corpo do system prompt estático.
 * O prefixo estático permanece idêntico entre requests consecutivos, permitindo
 * que o upstream acerte o Prompt Cache (>1024 tokens) com até 80% de desconto.
 * As linhas voláteis são movidas para um anexo temporal no final.
 */
// ---------------------------------------------------------------------------
// Estabilização de System Prompt (Prefix Caching)
// ---------------------------------------------------------------------------
function stabilizeSystemPrompt(rawText) {
  if (!rawText || typeof rawText !== 'string') return { staticText: '', volatileText: '' };
  const lines = rawText.split('\n');
  const staticLines = [];
  const volatileLines = [];

  // Padrões típicos de timestamps dinâmicos injetados por agentes (DSH, Hermes, Cursor, Cline, etc.)
  const VOLATILE_PATTERNS = [
    /^(current\s*(time|date|timestamp)|today('?s)?\s*date|data\s*(e\s*hora|atual)|hora\s*atual)[\s:=]/i,
    /^(session\s*(id|uuid)|request\s*id|workspace\s*session)[\s:=]/i,
    /^(the\s*current\s*(time|date)\s*is)[\s:=]/i,
  ];

  for (const line of lines) {
    const trimmed = line.trim();
    if (VOLATILE_PATTERNS.some(pat => pat.test(trimmed))) {
      volatileLines.push(line);
    } else {
      staticLines.push(line);
    }
  }

  return {
    staticText: staticLines.join('\n').trim(),
    volatileText: volatileLines.join('\n').trim(),
  };
}

/**
 * Compactador de Contexto para saídas antigas de ferramentas (role: 'tool'):
 * Preserva as últimas 2 execuções de tools intactas.
 * Para saídas anteriores a 2 turnos que excedem maxChars (default 1500 chars),
 * trunca o miolo mantendo início e fim com marcador informativo, economizando
 * milhares de tokens de repetição em conversas longas.
 */
function compactOldToolOutput(text, isOlderThan2Turns, maxChars = 1500) {
  if (!isOlderThan2Turns || typeof text !== 'string' || text.length <= maxChars) {
    return text;
  }
  const keepHead = Math.floor(maxChars * 0.4);
  const keepTail = Math.floor(maxChars * 0.4);
  const omitted = text.length - (keepHead + keepTail);
  return (
    text.slice(0, keepHead) +
    `\n\n[... Omitido pelo proxy: ${omitted} caracteres de saída anterior já processada pelo agente ...]\n\n` +
    text.slice(-keepTail)
  );
}

function translateOpenAiToGemini(req) {
  const messages = Array.isArray(req.messages) ? req.messages : [];
  const systemParts = [];
  const contents = [];
  let pendingToolResults = [];
  const toolCallNames = new Map();
  const targetModel = resolveModel(String(req.model || 'gemini-3.6-flash-high'));
  const isGemini3 = targetModel.includes('gemini-3');

  // Identifica onde estão os últimos tool results para preservar os mais recentes
  const toolMsgIndices = [];
  messages.forEach((m, idx) => {
    if (m.role === 'tool') toolMsgIndices.push(idx);
  });
  const recentToolThreshold = toolMsgIndices.length > 2
    ? toolMsgIndices[toolMsgIndices.length - 2]
    : -1;

  let volatileSystemContext = '';

  for (let i = 0; i < messages.length; i++) {
    const msg = messages[i];
    const role = msg.role;
    if (role === 'system') {
      const rawSys = textFromOpenAiContent(msg.content);
      const { staticText, volatileText } = stabilizeSystemPrompt(rawSys);
      if (staticText) systemParts.push({ text: staticText });
      if (volatileText) {
        volatileSystemContext = (volatileSystemContext ? volatileSystemContext + '\n' : '') + volatileText;
      }
      continue;
    }
    if (role === 'user') {
      if (pendingToolResults.length > 0) {
        contents.push({ role: 'user', parts: pendingToolResults.map(tr => ({
          functionResponse: { name: tr.name, response: tr.response, ...(tr.id ? { id: tr.id } : {}) },
        })) });
        pendingToolResults = [];
      }
      let text = textFromOpenAiContent(msg.content);
      // Se tivermos contexto volátil (ex: horário atual), anexamos no final do último user message
      // para manter o bloco de system prompt rigorosamente estático byte-a-byte (hit do prompt cache).
      if (i === messages.length - 1 && volatileSystemContext) {
        text = (text ? text + '\n\n' : '') + '[Contexto temporal]: ' + volatileSystemContext;
        volatileSystemContext = '';
      }
      if (text) {
        contents.push({ role: 'user', parts: [{ text }] });
      }
      continue;
    }
    if (role === 'assistant') {
      if (pendingToolResults.length > 0) {
        contents.push({ role: 'user', parts: pendingToolResults.map(tr => ({
          functionResponse: { name: tr.name, response: tr.response, ...(tr.id ? { id: tr.id } : {}) },
        })) });
        pendingToolResults = [];
      }
      const parts = [];
      
      // Preserve history thinking/reasoning passback.
      // No Gemini 3, a Google exige rigorosamente que pensamentos anteriores
      // estejam acompanhados da respectiva assinatura criptográfica (thoughtSignature).
      // Blocos de thought de turnos antigos sem assinatura fazem o validador interno
      // do modelo abortar o turno seguinte logo no início da geração.
      // Portanto, para Gemini 3:
      // - Se houver tool_calls, mantemos o thought pois o primeiro functionCall
      //   carregará o sentinela skip_thought_signature_validator.
      // - Se NÃO houver tool_calls (mensagem de assistente pura), pensamentos antigos
      //   não devem ser passados como thought:true. Se o assistente não tinha gerado
      //   conteúdo de texto, convertemos o raciocínio em texto comum para preservar o contexto
      //   sem quebrar a validação criptográfica do Gemini 3.
      if (msg.reasoning_content) {
        if (!Array.isArray(msg.tool_calls) || msg.tool_calls.length === 0) {
          if (!isGemini3) {
            parts.push({ text: msg.reasoning_content, thought: true });
          } else if (!msg.content) {
            parts.push({ text: msg.reasoning_content });
          }
        } else {
          parts.push({ text: msg.reasoning_content, thought: true });
        }
      }
      
      const text = textFromOpenAiContent(msg.content);
      if (text) parts.push({ text });
      if (Array.isArray(msg.tool_calls)) {
        let first = true;
        for (const tc of msg.tool_calls) {
          if (tc.id && tc.function && tc.function.name) toolCallNames.set(tc.id, tc.function.name);
          let args = {};
          try { args = JSON.parse(tc.function?.arguments || '{}'); } catch { args = {}; }
          if (args === null || typeof args !== 'object' || Array.isArray(args)) {
            args = { result: args };
          }
          const fc = {
            functionCall: {
              name: tc.function?.name,
              args,
              ...(tc.id ? { id: tc.id } : {}),
            }
          };
          // Gemini 3 thoughtSignature: prioriza a assinatura real recebida do modelo
          if (first) {
            first = false;
            const realSig = tc.id ? thoughtSignaturesByCallId.get(tc.id) : null;
            fc.thoughtSignature = realSig || 'skip_thought_signature_validator';
          }
          parts.push(fc);
        }
      }
      if (parts.length > 0) contents.push({ role: 'model', parts });
      continue;
    }
    if (role === 'tool') {
      let response = textFromOpenAiContent(msg.content);
      const isOld = recentToolThreshold >= 0 && i < recentToolThreshold;
      if (typeof response === 'string') {
        response = compactOldToolOutput(response, isOld, 1500);
        try { response = JSON.parse(response); } catch { /* keep string */ }
      }
      // google.protobuf.Struct requires an object: plain-text tool output (e.g.
      // bash stdout) must be wrapped, not sent raw as a string.
      if (response === null || typeof response !== 'object' || Array.isArray(response)) {
        response = { result: response };
      }
      // function_response.name must be non-empty: fall back through
      // msg.name -> remembered tool_call_id -> a stable placeholder.
      let name = msg.name || toolCallNames.get(msg.tool_call_id) || '';
      if (typeof name !== 'string' || name.trim() === '') {
        name = 'tool_result_' + (pendingToolResults.length + 1);
      }
      pendingToolResults.push({ name: name.trim(), response, id: msg.tool_call_id });
      continue;
    }
    // Unknown role: drop.
  }
  if (pendingToolResults.length > 0) {
    contents.push({ role: 'user', parts: pendingToolResults.map(tr => ({
      functionResponse: { name: tr.name, response: tr.response, ...(tr.id ? { id: tr.id } : {}) },
    })) });
  }

  // Dynamic thinkingConfig based on model version (Gemini 2.5 vs Gemini 3).
  // Sufixo "-none" (ex.: gemini-3.8-flash-none) omite o thinkingConfig por
  // completo -> respostas diretas sem cadeia de raciocínio (~1s).
  const isGemini25 = targetModel.includes('gemini-2.5');

  let thinkingLevel = THINKING_LEVEL;
  const modelLower = String(req.model || '').toLowerCase();
  if (modelLower.includes('-none')) {
    thinkingLevel = null; // sem thinkingConfig abaixo
  } else if (modelLower.includes('-medium')) {
    thinkingLevel = 'medium';
  } else if (modelLower.includes('-high')) {
    thinkingLevel = 'high';
  } else if (modelLower.includes('-low')) {
    thinkingLevel = 'low';
  }

  const generationConfig = {};
  if (thinkingLevel !== null) {
    generationConfig.thinkingConfig = isGemini25
      ? { thinkingBudget: 2048, includeThoughts: true }
      : { thinkingLevel: thinkingLevel, includeThoughts: true };
  }

  // Aceita max_tokens (OpenAI clássico) e max_completion_tokens (novo nome).
  const maxTokens = typeof req.max_tokens === 'number'
    ? req.max_tokens
    : (typeof req.max_completion_tokens === 'number' ? req.max_completion_tokens : undefined);
  if (typeof maxTokens === 'number') {
    generationConfig.maxOutputTokens = isGemini3 ? Math.min(maxTokens, 65536) : maxTokens;
  }
  // Gemini 3.8 migration guide: temperature, topP e topK são deprecados e não devem ser enviados
  if (!isGemini3) {
    if (typeof req.temperature === 'number') generationConfig.temperature = req.temperature;
    if (typeof req.top_p === 'number') generationConfig.topP = req.top_p;
  }

  // SessionId estável: usa hash das primeiras mensagens em vez de UUID aleatório por request,
  // permitindo que o upstream Google Gemini aproveite 100% de afinidade e Prompt Prefix Caching.
  const sessionSeed = (messages[0]?.content ? String(messages[0].content).slice(0, 500) : '') +
                      (messages[1]?.content ? String(messages[1].content).slice(0, 200) : '');
  const stableSessionId = 'proxy-' + sha256Hex(sessionSeed || 'default').slice(0, 24);

  const gemini = {
    project: DEFAULT_PROJECT,
    model: targetModel,
    requestType: 'agent',
    userAgent: 'antigravity',
    requestId: 'agent-' + crypto.randomUUID(),
    request: {
      sessionId: stableSessionId,
      contents,
      generationConfig,
    },
  };
  const tools = Array.isArray(req.tools) ? req.tools : [];
  if (tools.length > 0) {
    // Para modelos Gemini com pensamento ativo e tools, adicionamos uma instrução
    // de reforço para que o modelo nunca encerre a geração no thinking sem emitir
    // a functionCall quando decidir usar ferramentas.
    systemParts.push({
      text: 'CRITICAL INSTRUCTION: When you decide to call a tool or inspect something, you MUST ALWAYS emit the function call block immediately. Do not describe the tool call in thinking without generating the actual function call.'
    });
  }
  if (systemParts.length > 0) {
    gemini.request.systemInstruction = { role: 'user', parts: systemParts };
  }
  const functions = getCanonicalToolDeclarations(tools);
  if (functions.length > 0) {
    gemini.request.tools = [{ functionDeclarations: functions }];
    gemini.request.toolConfig = {
      functionCallingConfig: {
        mode: 'AUTO'
      }
    };
  }
  return gemini;
}

// ---------------------------------------------------------------------------
// Translation: Antigravity (Gemini) -> OpenAI
// ---------------------------------------------------------------------------
function normalizeGeminiResponse(parsed) {
  // SSE frames arrive as { response: {...} }; non-stream as {...} directly.
  const resp = parsed?.response ?? parsed;
  if (!resp || typeof resp !== 'object') return null;
  const candidates = resp.candidates || [];
  return { ...resp, candidates };
}

function translateGeminiToOpenAi(geminiResp, requestedModel) {
  const g = normalizeGeminiResponse(geminiResp);
  if (!g) return null;
  const cand = g.candidates?.[0];
  const parts = cand?.content?.parts || [];
  let content = '';
  let reasoning_content = '';
  const toolCalls = [];
  parts.forEach((part, idx) => {
    if (part?.text) {
      if (part.thought || part.type === 'thinking') {
        reasoning_content += part.text;
      } else {
        content += part.text;
      }
    }
    if (part?.functionCall) {
      let args = part.functionCall.args;
      if (typeof args === 'string') { try { args = JSON.parse(args); } catch { /* keep */ } }
      toolCalls.push({
        id: 'call_' + (part.functionCall.id || crypto.randomUUID().replace(/-/g, '').slice(0, 24)),
        type: 'function',
        function: { name: part.functionCall.name, arguments: JSON.stringify(args ?? {}) },
      });
    }
  });
  const finishMap = {
    STOP: 'stop', MAX_TOKENS: 'length', SAFETY: 'content_filter', RECITATION: 'content_filter',
    BLOCKLIST: 'content_filter', PROHIBITED_CONTENT: 'content_filter', SPII: 'content_filter',
    IMAGE_SAFETY: 'content_filter', MALFORMED_FUNCTION_CALL: 'tool_calls', UNKNOWN: 'stop',
  };
  const finishReason = toolCalls.length > 0
    ? 'tool_calls'
    : (finishMap[String(cand?.finishReason || 'STOP')] || 'stop');
  const usage = g.usageMetadata ? {
    prompt_tokens: g.usageMetadata.promptTokenCount ?? 0,
    completion_tokens: g.usageMetadata.candidatesTokenCount ?? 0,
    total_tokens: g.usageMetadata.totalTokenCount ?? 0,
  } : undefined;
  if (toolCalls.length === 0 && !content) {
    content = (reasoning_content && reasoning_content.trim()) ? reasoning_content : '...';
  }
  return {
    content,
    reasoning_content: reasoning_content || undefined,
    toolCalls,
    finishReason,
    usage,
    modelVersion: g.modelVersion,
    responseId: g.responseId,
  };
}

function buildOpenAiResponse(translated, requestedModel, id, created) {
  const message = { role: 'assistant', content: translated.content || null };
  if (translated.reasoning_content) {
    message.reasoning_content = translated.reasoning_content;
  }
  if (translated.toolCalls.length > 0) message.tool_calls = translated.toolCalls;
  const choice = { index: 0, message, finish_reason: translated.finishReason };
  const body = {
    id, object: 'chat.completion', created,
    model: requestedModel, choices: [choice],
  };
  if (translated.usage) body.usage = translated.usage;
  return body;
}

// ---------------------------------------------------------------------------
// Antigravity API call
// ---------------------------------------------------------------------------
// Endpoint saudável (último que respondeu OK): os 429 são por-endpoint, não
// da conta — o painel da conta pode estar com 93% de cota e um endpoint
// individual (ex.: produção) estar exausto enquanto a sandbox diária responde
// OK. Tentamos primeiro o endpoint que funcionou por último.
let healthyEndpointIndex = 0;

async function callWithAccount(acc, geminiBody, stream) {
  const accessToken = await acc.getAccessToken();
  const proj = await acc.ensureProject(accessToken);

  // Clona o body para evitar modificação concorrente de projeto por outra conta
  const bodyClone = JSON.parse(JSON.stringify(geminiBody));
  bodyClone.project = proj;

  const action = stream ? 'streamGenerateContent' : 'generateContent';
  const headers = {
    'Content-Type': 'application/json',
    Authorization: 'Bearer ' + accessToken,
    'User-Agent': USER_AGENT,
  };
  if (stream) headers.Accept = 'text/event-stream';

  const order = acc.endpointOrder();

  let lastError = null;
  for (let attempt = 0; attempt < order.length; attempt++) {
    const ei = order[attempt];
    const ep = ENDPOINTS[ei];
    const u = ep + '/v1internal:' + action + (stream ? '?alt=sse' : '');
    const t0 = Date.now();
    try {
      // Prioriza HTTP/2 multiplexado com fallback automático para HTTP/1.1
      const res = await fetchUpstreamH2(u, { method: 'POST', headers, body: JSON.stringify(bodyClone) }, TIMEOUT_UPSTREAM_MS, Boolean(stream));
      const latencyMs = Date.now() - t0;
      if (res.status === 429) {
        const bodyText = await res.text().catch(() => '');
        log(`WARN [Account ${acc.name}] rate limited at ${ep}: ${bodyText.slice(0, 120)}`);
        acc.bumpEndpointStats(ei, '429');
        bumpEndpointHealth(ei, '429');
        const retryAfter = Number(res.headers.get('retry-after') || NaN);
        lastError = new RateLimitError(
          `Antigravity 429 [Account ${acc.name}] at ${ep}: ${bodyText.slice(0, 150)}`,
          Number.isFinite(retryAfter) && retryAfter > 0 ? retryAfter : null,
        );
        await sleep(backoffDelay(attempt, Number.isFinite(retryAfter) ? retryAfter : null));
        continue;
      }
      if (res.status === 401) {
        acc.accessToken = null; // force refresh and retry once
        const newToken = await acc.refreshAccessToken();
        headers.Authorization = 'Bearer ' + newToken;
        const retry = await fetchUpstreamH2(u, { method: 'POST', headers, body: JSON.stringify(bodyClone) }, TIMEOUT_UPSTREAM_MS, Boolean(stream));
        if (retry.ok) {
          const retryLatency = Date.now() - t0;
          acc.recordEndpointLatency(ei, retryLatency);
          acc.bumpEndpointStats(ei, 'ok');
          bumpEndpointHealth(ei, 'ok');
          if (acc.currentIndex !== ei) { acc.lastSwitchAt = Date.now(); acc.currentIndex = ei; }
          acc.healthyEndpointIndex = ei;
          return retry;
        }
        acc.bumpEndpointStats(ei, 'err');
        bumpEndpointHealth(ei, 'err');
        lastError = new UpstreamHttpError(401,
          `Antigravity 401 retry failed [Account ${acc.name}]: ` + (await retry.text()).slice(0, 300));
        continue;
      }
      if (res.ok) {
        acc.recordEndpointLatency(ei, latencyMs);
        acc.bumpEndpointStats(ei, 'ok');
        bumpEndpointHealth(ei, 'ok');
        if (acc.currentIndex !== ei) { acc.lastSwitchAt = Date.now(); acc.currentIndex = ei; }
        acc.healthyEndpointIndex = ei;
        return res;
      }
      const errText = (await res.text()).slice(0, 400);
      log(`WARN [Account ${acc.name}] endpoint ${ep} returned ${res.status}: ${errText}`);
      acc.bumpEndpointStats(ei, 'err');
      bumpEndpointHealth(ei, 'err');
      lastError = new UpstreamHttpError(res.status, `Antigravity ${res.status} [Account ${acc.name}] at ${ep}: ${errText}`);
      if (res.status >= 400 && res.status < 500 && res.status !== 408 && res.status !== 429) {
        // Client error (invalid request): no point trying other endpoints.
        break;
      }
    } catch (e) {
      acc.bumpEndpointStats(ei, 'err');
      bumpEndpointHealth(ei, 'err');
      log(`WARN [Account ${acc.name}] endpoint ${ep} threw: ${e.message}`);
      lastError = e;
    }
  }
  throw lastError || new Error(`Account ${acc.name} failed all endpoints`);
}

async function callAntigravity(geminiBody, stream, skipAcquire = false) {
  if (!skipAcquire) await acquireUpstream();
  let upstreamHeld = !skipAcquire;
  ensureAccountsPool();
  if (accountsPool.length === 0) {
    if (upstreamHeld) releaseUpstream();
    throw new Error('No accounts available in pool');
  }

  try {
    const accountOrder = [];
    for (let i = 0; i < accountsPool.length; i++) {
      accountOrder.push((activeAccountIndex + i) % accountsPool.length);
    }

    // Se TODAS estiverem em cooldown, tenta mesmo assim (nunca silencioso):
    const hasEligible = accountOrder.some(idx => accountCoolingSeconds(accountsPool[idx]) === 0);
    const probeAll = !hasEligible;

    let lastError = null;
    for (let i = 0; i < accountOrder.length; i++) {
      const accIdx = accountOrder[i];
      const acc = accountsPool[accIdx];
      if (!probeAll && accountCoolingSeconds(acc) > 0) continue;
      try {
        const res = await callWithAccount(acc, geminiBody, stream);
        accountCooldownUntil.delete(accCooldownKey(acc)); // funcionou: sai do cooldown
        // Conta fixada pelo usuário permanece #1; senão, mantém a que funcionou.
        if (!accountPinned) activeAccountIndex = accIdx;
        // Se for streaming e nós adquirimos a vaga, transferimos a posse da vaga
        // para streamOpenAiResponse liberar ao final da transmissão SSE.
        if (stream && !skipAcquire) {
          upstreamHeld = false;
        }
        return res;
      } catch (e) {
        log(`WARN Account ${acc.name} failed: ${e.message}`);
        lastError = e;
        const cd = cooldownMsForError(e);
        if (cd > 0) {
          accountCooldownUntil.set(accCooldownKey(acc), Date.now() + cd);
          log(`  -> ${acc.name} em cooldown por ${Math.round(cd / 1000)}s`);
        }
        // Se for erro do cliente (4xx), aborta o fallback para evitar loops
        if (e.status !== 429 && !(e.message && e.message.includes('400')) && !(e.message && e.message.includes('401'))) {
          break;
        }
      }
    }
    throw lastError || new Error('All accounts failed');
  } finally {
    if (upstreamHeld) {
      releaseUpstream();
    }
  }
}

// ---------------------------------------------------------------------------
// Streaming: Antigravity SSE -> OpenAI SSE
// ---------------------------------------------------------------------------
function createOpenAiChunk(id, created, model, delta, finishReason, usage) {
  const chunk = {
    id, object: 'chat.completion.chunk', created, model,
    choices: [{ index: 0, delta, finish_reason: finishReason ?? null }],
  };
  if (usage) chunk.usage = usage;
  return 'data: ' + JSON.stringify(chunk) + '\n\n';
}

async function streamOpenAiResponse(res, upstream, requestedModel, openAiId, created, geminiBody = null, onComplete = null) {
  try {
    const reader = upstream.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    // Each SSE frame carries a NON-CUMULATIVE text fragment (see below);
    // tool calls arrive complete in one frame.
    let toolState = []; // index -> { id, name, args }
    let finishReason = null;
    let usage = null;
    let clientAborted = false;
    let roleSent = false;
    let accumulatedReasoning = '';
    let accumulatedContent = '';
    let streamReaderError = null;

    res.on('close', () => {
      clientAborted = true;
      try { reader.cancel().catch(() => {}); } catch {}
      while (drainWaiters.length > 0) {
        const w = drainWaiters.shift();
        if (typeof w === 'function') w();
      }
    });

  // Backpressure: se o buffer do socket encher, aguarda 'drain' antes de
  // continuar (cliente lento não faz a memória do proxy crescer sem limite).
  let drainWaiters = [];
  const recordedChunks = onComplete ? [] : null;
  const send = (chunk) => {
    if (clientAborted) return;
    if (recordedChunks) recordedChunks.push(chunk);
    if (!res.write(chunk)) {
      drainWaiters.push(new Promise(resolve => res.once('drain', resolve)));
    }
  };
  const flush = async () => {
    while (drainWaiters.length > 0) {
      const w = drainWaiters.shift();
      await w;
      if (clientAborted) return;
    }
  };

  const sendDelta = (delta, finishReason = null, usage = null) => {
    if (!roleSent) {
      delta = { role: 'assistant', ...delta };
      roleSent = true;
    }
    send(createOpenAiChunk(openAiId, created, requestedModel, delta, finishReason, usage));
  };

  // Idle timeout: se o upstream parar de enviar frames, não pendura o socket.
  let lastActivity = Date.now();
  const idleTimer = setInterval(() => {
    if (clientAborted) return;
    if (Date.now() - lastActivity > TIMEOUT_STREAM_IDLE_MS) {
      log('Stream idle timeout (' + TIMEOUT_STREAM_IDLE_MS + 'ms) — aborting');
      clientAborted = true;
      try { reader.cancel().catch(() => {}); } catch {}
    }
  }, 5000);

  while (!clientAborted) {
    let result;
    try {
      result = await reader.read();
      lastActivity = Date.now();
    } catch (e) {
      log('Stream reader error:', e.message);
      streamReaderError = e.message;
      break;
    }
    const { done, value } = result;
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let idx;
    while ((idx = buffer.indexOf('\n')) !== -1) {
      const line = buffer.slice(0, idx).trim();
      buffer = buffer.slice(idx + 1);
      if (!line.startsWith('data:')) continue;
      const payload = line.slice(5).trim();
      if (!payload || payload === '[DONE]') continue;
      let parsed;
      try { parsed = JSON.parse(payload); } catch { continue; }
      const g = normalizeGeminiResponse(parsed);
      if (!g) continue;
      const cand = g.candidates?.[0];
      if (!cand) {
        if (g.usageMetadata) usage = {
          prompt_tokens: g.usageMetadata.promptTokenCount ?? 0,
          completion_tokens: g.usageMetadata.candidatesTokenCount ?? 0,
          total_tokens: g.usageMetadata.totalTokenCount ?? 0,
        };
        continue;
      }
      const parts = cand?.content?.parts || [];
      // 1) Text: each SSE frame carries a NON-CUMULATIVE fragment (verified
      // against usageMetadata.candidatesTokenCount, which grows by exactly the
      // fragment size). Emit each fragment as-is; the client concatenates.
      for (const p of parts) {
        if (p && typeof p.text === 'string' && p.text.length > 0) {
          if (p.thought || p.type === 'thinking') {
            accumulatedReasoning += p.text;
            sendDelta({ reasoning_content: p.text });
          } else {
            accumulatedContent += p.text;
            sendDelta({ content: p.text });
          }
        }
      }
      // 2) Tool calls: arrive COMPLETE in a single frame (args as object).
      const calls = [];
      parts.forEach((p) => {
        if (p && p.functionCall) {
          const argsStr = typeof p.functionCall.args === 'string'
            ? p.functionCall.args
            : JSON.stringify(p.functionCall.args ?? {});
          const callId = p.functionCall.id || ('call_' + crypto.randomUUID().toString(36).slice(2, 10));
          if (p.thoughtSignature) {
            thoughtSignaturesByCallId.set(callId, p.thoughtSignature);
          }
          calls.push({
            id: callId,
            name: p.functionCall.name,
            args: argsStr,
          });
        }
      });
      for (let i = 0; i < calls.length; i++) {
        const cur = calls[i];
        const prev = toolState[i];
        if (!prev) {
          toolState[i] = cur;
          sendDelta({
            tool_calls: [{ index: i, id: cur.id, type: 'function', function: { name: cur.name, arguments: '' } }],
          });
          if (cur.args.length > 0) {
            sendDelta({
              tool_calls: [{ index: i, function: { arguments: cur.args } }],
            });
          }
        } else if (cur.args.length > prev.args.length) {
          toolState[i] = cur;
          sendDelta({
            tool_calls: [{ index: i, function: { arguments: cur.args.slice(prev.args.length) } }],
          });
        }
      }
      if (cand.finishReason) finishReason = cand.finishReason;
      if (g.usageMetadata) usage = {
        prompt_tokens: g.usageMetadata.promptTokenCount ?? 0,
        completion_tokens: g.usageMetadata.candidatesTokenCount ?? 0,
        total_tokens: g.usageMetadata.totalTokenCount ?? 0,
      };
    }
    await flush();
  }
  clearInterval(idleTimer);
  if (!clientAborted) {
    // Se o modelo emitiu pensamento (thinking) mas parou sem emitir
    // nenhuma chamada de ferramenta (tool_calls) e nenhum texto (content):
    // Quando o Gemini entra em loops longos de raciocínio, ele frequentemente
    // descreve a intenção de executar uma ferramenta no thought mas fecha em STOP.
    // Em vez de abortar ou perguntar ao usuário, o proxy agora dispara uma
    // continuação imediata (auto-continue loop) para o Google Gemini continuar o stream
    // e finalmente emitir a tool call!
    if (accumulatedReasoning && !accumulatedContent && toolState.length === 0 && geminiBody) {
      log(`[Auto-Continue] Gemini parou apenas no thinking (${accumulatedReasoning.length} chars). Disparando continue transparente...`);
      try {
        // Clona e anexa o pensamento como assistente e adiciona um empurrão user "continue"
        const nextBody = JSON.parse(JSON.stringify(geminiBody));
        nextBody.request.contents.push({
          role: 'model',
          parts: [{ text: accumulatedReasoning }]
        });
        nextBody.request.contents.push({
          role: 'user',
          parts: [{ text: 'Continue immediately and emit the tool call or your final response.' }]
        });
        // Dispara o segundo stream transparente no mesmo SSE (skipAcquire: já possuímos a vaga deste stream)
        const nextUpstream = await callAntigravity(nextBody, true, true);
        const nextReader = nextUpstream.body.getReader();
        let nextBuffer = '';
        while (true) {
          const { done, value } = await nextReader.read();
          if (done) break;
          nextBuffer += decoder.decode(value, { stream: true });
          const lines = nextBuffer.split('\n');
          nextBuffer = lines.pop() || '';
          for (const line of lines) {
            if (!line.startsWith('data: ')) continue;
            const dataStr = line.slice(6).trim();
            if (dataStr === '[DONE]') continue;
            let parsed;
            try { parsed = JSON.parse(dataStr); } catch { continue; }
            const g = parsed.response || parsed;
            const cand = g?.candidates?.[0];
            if (!cand) continue;
            const parts = cand?.content?.parts || [];
            for (const p of parts) {
              if (p && typeof p.text === 'string' && p.text.length > 0) {
                if (p.thought || p.type === 'thinking') {
                  accumulatedReasoning += p.text;
                  sendDelta({ reasoning_content: p.text });
                } else {
                  accumulatedContent += p.text;
                  sendDelta({ content: p.text });
                }
              }
            }
            const calls = [];
            parts.forEach((p) => {
              if (p && p.functionCall) {
                const argsStr = typeof p.functionCall.args === 'string'
                  ? p.functionCall.args
                  : JSON.stringify(p.functionCall.args ?? {});
                calls.push({
                  id: p.functionCall.id || ('call_' + crypto.randomUUID().toString(36).slice(2, 10)),
                  name: p.functionCall.name,
                  args: argsStr,
                });
              }
            });
            for (let i = 0; i < calls.length; i++) {
              const cur = calls[i];
              const prev = toolState[i];
              if (!prev) {
                toolState[i] = cur;
                sendDelta({
                  tool_calls: [{ index: i, id: cur.id, type: 'function', function: { name: cur.name, arguments: '' } }],
                });
                if (cur.args.length > 0) {
                  sendDelta({
                    tool_calls: [{ index: i, function: { arguments: cur.args } }],
                  });
                }
              } else if (cur.args.length > prev.args.length) {
                toolState[i] = cur;
                sendDelta({
                  tool_calls: [{ index: i, function: { arguments: cur.args.slice(prev.args.length) } }],
                });
              }
            }
            if (cand.finishReason) finishReason = cand.finishReason;
            if (g.usageMetadata) usage = {
              prompt_tokens: g.usageMetadata.promptTokenCount ?? 0,
              completion_tokens: g.usageMetadata.candidatesTokenCount ?? 0,
              total_tokens: g.usageMetadata.totalTokenCount ?? 0,
            };
          }
        }
      } catch (autoErr) {
        log('[Auto-Continue] WARN auto-continue falhou:', autoErr.message);
      }
    }

    // Se houve erro real de transporte no meio da leitura do stream (socket reset, timeout),
    // NÃO mascare como um sucesso vazio (STOP). Propague o erro estruturado para o harness
    // para que o cliente acione o retry nativo.
    if (streamReaderError && toolState.length === 0 && !accumulatedContent) {
      log('Transport error on SSE stream:', streamReaderError);
      send('data: ' + JSON.stringify({
        error: {
          message: `Stream transport failure: ${streamReaderError}`,
          type: 'upstream_error',
          code: 'UPSTREAM_FAILURE',
        }
      }) + '\n\n');
      send('data: [DONE]\n\n');
      await flush();
      return;
    }

    // Se o modelo concluiu normalmente no upstream mas parou sem emitir conteúdo de texto
    // e sem tool calls, garante fallback mínimo para evitar erro fatal de EMPTY_RESPONSE no DSH.
    if (toolState.length === 0 && !accumulatedContent) {
      const fallbackText = (accumulatedReasoning && accumulatedReasoning.trim())
        ? accumulatedReasoning
        : '...';
      sendDelta({ content: fallbackText });
      accumulatedContent += fallbackText;
    }

    const finishMap = {
      STOP: 'stop', MAX_TOKENS: 'length', SAFETY: 'content_filter', RECITATION: 'content_filter',
      BLOCKLIST: 'content_filter', PROHIBITED_CONTENT: 'content_filter', SPII: 'content_filter',
      IMAGE_SAFETY: 'content_filter', MALFORMED_FUNCTION_CALL: 'tool_calls', UNKNOWN: 'stop',
    };
    // Se houve tool_calls emitidos no stream, o finish_reason DEVE ser 'tool_calls'.
    // Caso contrário, se o modelo terminou com STOP mas gerou tool calls, ou se finishReason
    // veio como STOP, nunca podemos marcar como stop se há chamadas pendentes.
    const fr = toolState.length > 0 ? 'tool_calls' : (finishMap[String(finishReason)] || 'stop');
    sendDelta({}, fr, usage);
    send('data: [DONE]\n\n');
    await flush();

    // Se a resposta completou normalmente e NÃO teve chamadas de ferramentas (tool_calls),
    // entrega os chunks e usage para o callback de cacheamento local.
    if (typeof onComplete === 'function' && !clientAborted && toolState.length === 0 && recordedChunks) {
      try { onComplete({ chunks: recordedChunks, usage, finishReason: fr }); }
      catch (err) { log('WARN onComplete stream cache:', err.message); }
    }
  }
} finally {
  releaseUpstream();
}
}

// ---------------------------------------------------------------------------
// HTTP server
// ---------------------------------------------------------------------------
function sendJson(res, status, obj, extraHeaders = null) {
  const body = JSON.stringify(obj);
  res.writeHead(status, {
    'Content-Type': 'application/json',
    'Content-Length': Buffer.byteLength(body),
    ...(extraHeaders || {}),
  });
  res.end(body);
}

// ---------------------------------------------------------------------------
// Embeddings & Cache Persistente em Disco
// ---------------------------------------------------------------------------
const MODULE_DIR = path.dirname(fileURLToPath(import.meta.url));
const EMBEDDINGS_CACHE_FILE = path.join(MODULE_DIR, 'cache', 'embeddings.json');
const EMBEDDINGS_SETTINGS_FILE = path.join(MODULE_DIR, 'cache', 'embeddings-config.json');
const PROXY_SETTINGS_FILE = path.join(MODULE_DIR, 'cache', 'proxy-settings.json');

function loadProxySettings() {
  try {
    if (fs.existsSync(PROXY_SETTINGS_FILE)) {
      const cfg = JSON.parse(fs.readFileSync(PROXY_SETTINGS_FILE, 'utf8'));
      if (typeof cfg.activeAccountIndex === 'number') activeAccountIndex = cfg.activeAccountIndex;
      if (typeof cfg.accountPinned === 'boolean') accountPinned = cfg.accountPinned;
      if (cfg.thinkingLevel) THINKING_LEVEL = cfg.thinkingLevel;
      if (typeof cfg.maxConcurrent === 'number') MAX_CONCURRENT = cfg.maxConcurrent;
      log(`[Settings] Configurações persistidas carregadas: conta #${activeAccountIndex + 1} (pinned: ${accountPinned})`);
    }
  } catch (e) {
    log('WARN loadProxySettings:', e.message);
  }
}

function saveProxySettings() {
  try {
    const dir = path.dirname(PROXY_SETTINGS_FILE);
    if (!fs.existsSync(dir)) fs.mkdirSync(dir, { recursive: true });
    fs.writeFileSync(PROXY_SETTINGS_FILE, JSON.stringify({
      activeAccountIndex,
      accountPinned,
      thinkingLevel: THINKING_LEVEL,
      maxConcurrent: MAX_CONCURRENT,
    }, null, 2));
  } catch (e) {
    log('WARN saveProxySettings:', e.message);
  }
}

// Carrega chave salva do Gemini para embeddings
let geminiApiKey = process.env.GEMINI_API_KEY || '';
try {
  if (fs.existsSync(EMBEDDINGS_SETTINGS_FILE)) {
    const cfg = JSON.parse(fs.readFileSync(EMBEDDINGS_SETTINGS_FILE, 'utf8'));
    if (cfg.geminiApiKey) geminiApiKey = cfg.geminiApiKey;
  }
} catch (e) {
  log('WARN loading embeddings config:', e.message);
}
loadProxySettings();
ensureAccountsPool();

function saveEmbeddingsConfig() {
  try {
    const dir = path.dirname(EMBEDDINGS_SETTINGS_FILE);
    if (!fs.existsSync(dir)) fs.mkdirSync(dir, { recursive: true });
    fs.writeFileSync(EMBEDDINGS_SETTINGS_FILE, JSON.stringify({ geminiApiKey }, null, 2));
  } catch (e) {
    log('WARN saving embeddings config:', e.message);
  }
}

// Telemetria de Embeddings
const embeddingsStats = {
  totalRequests: 0,
  totalItems: 0,
  cacheHits: 0,
  cacheMisses: 0,
  tokensTotal: 0,
  msTotal: 0,
};

// Cache em memória persistido em disco: hash -> vetor float[]
const embeddingsCache = new Map();
let embeddingsDiskDirty = false;
let embeddingsSaveTimer = null;

function loadEmbeddingsCache() {
  try {
    if (fs.existsSync(EMBEDDINGS_CACHE_FILE)) {
      const data = JSON.parse(fs.readFileSync(EMBEDDINGS_CACHE_FILE, 'utf8'));
      if (data && typeof data === 'object') {
        for (const [k, v] of Object.entries(data)) {
          embeddingsCache.set(k, v);
        }
        log(`[Embeddings] Carregados ${embeddingsCache.size} vetores do cache em disco.`);
      }
    }
  } catch (e) {
    log('WARN loading embeddings cache:', e.message);
  }
}
loadEmbeddingsCache();

function scheduleSaveEmbeddings() {
  embeddingsDiskDirty = true;
  if (embeddingsSaveTimer) return;
  embeddingsSaveTimer = setTimeout(() => {
    embeddingsSaveTimer = null;
    if (!embeddingsDiskDirty) return;
    try {
      const dir = path.dirname(EMBEDDINGS_CACHE_FILE);
      if (!fs.existsSync(dir)) fs.mkdirSync(dir, { recursive: true });
      const obj = {};
      for (const [k, v] of embeddingsCache.entries()) obj[k] = v;
      fs.writeFileSync(EMBEDDINGS_CACHE_FILE, JSON.stringify(obj));
      embeddingsDiskDirty = false;
    } catch (e) {
      log('WARN persisting embeddings cache:', e.message);
    }
  }, 1000);
}

// ---------------------------------------------------------------------------
// Semantic Cache — respostas para perguntas "quase iguais" (similaridade
// de cosseno sobre embeddings locais). Complementa o cache exato (SHA-256):
// o exato só acerta quando o payload é byte-idêntico; o semântico acerta
// quando o cliente reformula/parafraseia a mesma pergunta.
//
// Regras de segurança (crítico para um proxy de AGENTES):
//  1) Só consulta/grava quando o request é DETERMINÍSTICO (temperature<=0.2
//     ou x-cache-ttl) e NÃO declara tools nem tem tool_calls no histórico —
//     respostas que dependeriam de execução de ferramentas nunca são
//     reutilizadas, evitando repetir efeitos colaterais.
//  2) O "texto canônico" usado no embedding inclui TODO o histórico textual
//     (system + user + assistant sem tool outputs). Assim só há match quando
//     os históricos são semanticamente idênticos → resposta correta.
//  3) Requer geminiApiKey (mesmo embedder do /v1/embeddings).
//  4) Lookup/gravação em lote limitado (concorrência 2) p/ nunca segurar fila.
// ---------------------------------------------------------------------------
const SEMANTIC_CACHE_MAX = 250;         // entradas LRU em memória
const SEMANTIC_THRESHOLD_DEFAULT = 0.93; // cosseno mínimo p/ considerar hit
const SEMANTIC_EMBED_MODEL = 'gemini-embedding-001';
const SEMANTIC_EMBED_DIM = 768;

const semanticStats = {
  lookups: 0,        // tentativas de lookup semântico
  hits: 0,           // respondeu do cache semântico
  misses: 0,         // não achou similar acima do limiar
  stored: 0,         // respostas indexadas
  evicted: 0,
  embedCalls: 0,
  savedTokens: 0,    // tokens economizados (in+out das respostas reutilizadas)
  disabled: true,    // vira true quando sem geminiApiKey / desligado
};

// Configuração dinâmica via dashboard (POST /api/dashboard/config)
let semanticEnabled = true;
let semanticThreshold = Number(process.env.SEMANTIC_THRESHOLD) || SEMANTIC_THRESHOLD_DEFAULT;

function syncSemanticDisabled() {
  semanticStats.disabled = !geminiApiKey || !semanticEnabled;
}
syncSemanticDisabled();

// key -> { vector, kind: 'stream'|'json', model, ctxHash, query, chunks|null, payload:null|translated,
//          usage, until, at, hits }
const semanticCache = new Map();
let semanticEmbedBusy = 0;

function semanticCacheKey(kind, model, ctxHash, queryHash) {
  return `${kind}|${model}|${ctxHash}|${queryHash}`;
}

function cosineSim(a, b) {
  if (!a || !b || a.length === 0 || a.length !== b.length) return 0;
  let dot = 0, na = 0, nb = 0;
  for (let i = 0; i < a.length; i++) { dot += a[i] * b[i]; na += a[i] * a[i]; nb += b[i] * b[i]; }
  if (na === 0 || nb === 0) return 0;
  return dot / (Math.sqrt(na) * Math.sqrt(nb));
}

/**
 * Separa o request em (query, ctxHash):
 *  - query    = conteúdo textual da ÚLTIMA mensagem (a instrução atual);
 *  - ctxHash  = hash do histórico ANTERIOR (tudo antes da última mensagem).
 * Só há match semântico dentro do MESMO ctxHash: se o histórico mudou, a
 * resposta correta pode mudar — nunca reutilizamos entre contextos distintos.
 */
function semanticPartsFromReq(openAiReq) {
  const msgs = Array.isArray(openAiReq?.messages) ? openAiReq.messages : [];
  if (msgs.length === 0) return null;
  const last = msgs[msgs.length - 1];
  if (!last || last.role !== 'user') return null;
  const query = typeof last.content === 'string' ? last.content.trim() : '';
  if (query.length < 8 || query.length > 4000) return null;
  const ctxParts = [];
  for (let i = 0; i < msgs.length - 1; i++) {
    const m = msgs[i];
    const role = m?.role;
    if (role === 'system' || role === 'user' || role === 'assistant') {
      const t = typeof m?.content === 'string' ? m.content : '';
      if (t) ctxParts.push(t);
    }
  }
  return { query, ctxHash: sha256Hex(ctxParts.join('\n')) };
}

/** Verifica se um request pode participar do cache semântico. */
function semanticEligibleReq(openAiReq, ttl) {
  if (!semanticEnabled || !geminiApiKey || semanticStats.disabled) return false;
  if (!openAiReq || ttl <= 0) return false;
  // Não participa se declara tools ou se houve tool_calls em qualquer turno.
  if (Array.isArray(openAiReq.tools) && openAiReq.tools.length > 0) return false;
  if (openAiReq.tool_choice && openAiReq.tool_choice !== 'auto' && openAiReq.tool_choice !== 'none') return false;
  const msgs = Array.isArray(openAiReq.messages) ? openAiReq.messages : [];
  for (const m of msgs) {
    if (m?.role === 'tool' || m?.role === 'function') return false;
    if (m?.role === 'assistant' && Array.isArray(m.tool_calls) && m.tool_calls.length > 0) return false;
  }
  return semanticPartsFromReq(openAiReq) !== null;
}

/** Embedding via cache local (disco) ou upstream Gemini — sem bloquear a fila. */
async function semanticEmbedText(text, timeoutMs = 6000) {
  const cacheKey = SEMANTIC_EMBED_MODEL + ':' + sha256Hex(text);
  const hit = embeddingsCache.get(cacheKey);
  if (hit) return hit;
  if (semanticEmbedBusy >= 2 || !geminiApiKey) return null;
  semanticEmbedBusy++;
  semanticStats.embedCalls++;
  try {
    const googleUrl = `https://generativelanguage.googleapis.com/v1beta/models/${SEMANTIC_EMBED_MODEL}:embedContent?key=${encodeURIComponent(geminiApiKey)}`;
    const upResp = await fetch(googleUrl, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        content: { parts: [{ text }] },
        outputDimensionality: SEMANTIC_EMBED_DIM,
      }),
      signal: AbortSignal.timeout(timeoutMs),
    });
    if (!upResp.ok) {
      const errTxt = await upResp.text().catch(() => '');
      log('WARN semantic embed HTTP ' + upResp.status + ': ' + errTxt.slice(0, 160));
      return null;
    }
    const data = await upResp.json();
    const vector = data.embedding?.values || null;
    if (Array.isArray(vector) && vector.length > 0) {
      embeddingsCache.set(cacheKey, vector);
      scheduleSaveEmbeddings();
      return vector;
    }
    return null;
  } catch (e) {
    log('WARN semantic embed error:', e.message);
    return null;
  } finally {
    semanticEmbedBusy--;
  }
}

/** Lookup: retorna { hit:boolean, sim, entry|null } para kind/model. */
async function semanticLookup(kind, model, openAiReq, ttl) {
  if (!semanticEligibleReq(openAiReq, ttl)) return { hit: false, sim: 0, entry: null, skipped: true };
  const parts = semanticPartsFromReq(openAiReq);
  if (!parts) return { hit: false, sim: 0, entry: null, skipped: true };
  const now = Date.now();
  // Poda de entradas expiradas deste bucket durante a varredura.
  let bucketHas = false;
  for (const [key, entry] of semanticCache) {
    if (entry.kind !== kind || entry.model !== model || entry.ctxHash !== parts.ctxHash) continue;
    bucketHas = true;
    if (entry.until < now) { semanticCache.delete(key); semanticStats.evicted++; }
  }
  if (!bucketHas) { semanticStats.lookups++; semanticStats.misses++; return { hit: false, sim: 0, entry: null, skipped: true }; }
  semanticStats.lookups++;
  const vector = await semanticEmbedText(parts.query, 1500); // timeout curto: nunca atrasa o usuário
  if (!vector) return { hit: false, sim: 0, entry: null, skipped: true };
  let bestSim = 0, bestEntry = null;
  for (const [key, entry] of semanticCache) {
    if (entry.kind !== kind || entry.model !== model || entry.ctxHash !== parts.ctxHash) continue;
    if (entry.until < now) continue;
    const sim = cosineSim(vector, entry.vector);
    if (sim > bestSim) { bestSim = sim; bestEntry = entry; }
  }
  if (bestEntry && bestSim >= semanticThreshold) {
    semanticStats.hits++;
    bestEntry.hits = (bestEntry.hits || 0) + 1;
    bestEntry.at = now;
    return { hit: true, sim: bestSim, entry: bestEntry };
  }
  semanticStats.misses++;
  return { hit: false, sim: bestSim, entry: null };
}

/** Grava resposta (stream chunks ou payload json) indexada por embedding. */
async function semanticStore(kind, model, openAiReq, ttl, data, usage, vectorHint = null) {
  try {
    if (!semanticEligibleReq(openAiReq, ttl)) return;
    const parts = semanticPartsFromReq(openAiReq);
    if (!parts) return;
    const vector = vectorHint || await semanticEmbedText(parts.query);
    if (!vector) return;
    if (semanticCache.size >= SEMANTIC_CACHE_MAX) {
      const oldestKey = semanticCache.keys().next().value;
      if (oldestKey) { semanticCache.delete(oldestKey); semanticStats.evicted++; }
    }
    semanticCache.set(semanticCacheKey(kind, model, parts.ctxHash, sha256Hex(parts.query)), {
      vector, kind, model, ctxHash: parts.ctxHash, query: parts.query.slice(0, 220),
      chunks: kind === 'stream' ? data : null,
      payload: kind === 'json' ? data : null,
      usage: usage || null,
      until: Date.now() + 6 * 3600e3, // TTL de 6h em memória
      at: Date.now(), hits: 1,
    });
    semanticStats.stored++;
    if (usage) semanticStats.savedTokens += (usage.prompt_tokens || 0) + (usage.completion_tokens || 0);
  } catch (e) {
    log('WARN semanticStore:', e.message);
  }
}

// Helper para telemetria de CPU do processo
let lastCpuUsage = process.cpuUsage();
let lastCpuCheckTime = Date.now();
let cachedCpuPercent = 0;

function getProcessMetrics() {
  const now = Date.now();
  const timeDelta = (now - lastCpuCheckTime) * 1000; // microsegundos
  if (timeDelta > 500_000) { // recalcula a cada 500ms
    const usage = process.cpuUsage(lastCpuUsage);
    lastCpuUsage = process.cpuUsage();
    lastCpuCheckTime = now;
    const cpuTotal = (usage.user + usage.system);
    const cpus = os.cpus().length || 1;
    // Porcentagem relativa ao total de núcleos
    cachedCpuPercent = Math.min(100, Math.round((cpuTotal / (timeDelta * cpus)) * 1000) / 10);
  }

  const mem = process.memoryUsage();
  return {
    cpuPercent: cachedCpuPercent,
    rssMB: Math.round((mem.rss / (1024 * 1024)) * 10) / 10,
    heapUsedMB: Math.round((mem.heapUsed / (1024 * 1024)) * 10) / 10,
    heapTotalMB: Math.round((mem.heapTotal / (1024 * 1024)) * 10) / 10,
    uptimeSec: Math.round(process.uptime()),
    loadAvg: os.loadavg().map(v => Math.round(v * 100) / 100),
  };
}

// ---------------------------------------------------------------------------
// Otimizações de requisição:
//   1) Coalescing in-flight — chamadas concorrentes idênticas compartilham
//      uma única chamada upstream (ideal para fan-out de subagentes).
//   2) Cache opt-in — header "x-cache-ttl: <segundos>" cacheia a resposta
//      completa não-streaming por aquele tempo.
//   3) Catálogo /v1/models com cache curto + gzip.
// ---------------------------------------------------------------------------
function stableSerialize(value) {
  if (value === undefined || value === null) return 'null';
  const t = typeof value;
  if (t === 'number' || t === 'boolean') return String(value);
  if (t === 'string') return JSON.stringify(value);
  if (Array.isArray(value)) return '[' + value.map(stableSerialize).join(',') + ']';
  if (t === 'object') {
    const keys = Object.keys(value).filter(k => value[k] !== undefined).sort();
    return '{' + keys.map(k => JSON.stringify(k) + ':' + stableSerialize(value[k])).join(',') + '}';
  }
  return JSON.stringify(value);
}

function sha256Hex(text) {
  return crypto.createHash('sha256').update(String(text)).digest('hex');
}

function makeOpenAiId() {
  return 'chatcmpl-' + crypto.randomUUID().replace(/-/g, '').slice(0, 24);
}

// Header opt-in de cache: "x-cache-ttl: <segundos>" (1..86400).
// Se ausente, usa TTL padrão automático (120s) caso temperature <= 0.2 ou ausente,
// ou 0 (desligado) para requests com alta aleatoriedade.
function optInTtlSeconds(req, body = null) {
  const v = parseInt(String(req.headers['x-cache-ttl'] || ''), 10);
  if (Number.isFinite(v) && v > 0) return Math.min(v, 86400);
  if (body) {
    const temp = body.temperature !== undefined ? Number(body.temperature) : 0;
    // Requests determinísticos ou com temperatura muito baixa ganham cache automático de 2 minutos
    if (Number.isFinite(temp) && temp <= 0.2) return 120;
  }
  return 0;
}

// Cache de streaming SSE em memória (chunks pré-formatados prontos para reemissão)
// key -> { until, chunks: string[], usage: object|null }
const streamResponseCache = new Map();
const STREAM_CACHE_MAX = 150;

function getStreamCache(key) {
  const item = streamResponseCache.get(key);
  if (item && item.until > Date.now()) return item;
  if (item) streamResponseCache.delete(key);
  return null;
}

function setStreamCache(key, ttlSeconds, chunks, usage) {
  if (ttlSeconds <= 0 || !chunks || chunks.length === 0) return;
  if (streamResponseCache.size >= STREAM_CACHE_MAX) {
    const firstKey = streamResponseCache.keys().next().value;
    if (firstKey) streamResponseCache.delete(firstKey);
  }
  streamResponseCache.set(key, {
    until: Date.now() + ttlSeconds * 1000,
    chunks,
    usage: usage || null,
  });
}

/**
 * Reemite uma sequência gravada de chunks SSE com ticks ultrarrápidos (~5ms)
 * para o cliente receber em streaming suave com latência de resposta quase zero.
 */
async function replaySseStream(res, cachedItem, clientOpenAiId, requestedModel, cacheLabel = 'HIT_STREAM_LOCAL', extraHeaders = null) {
  res.writeHead(200, {
    'Content-Type': 'text/event-stream',
    'Cache-Control': 'no-cache',
    Connection: 'keep-alive',
    'X-Accel-Buffering': 'no',
    'X-Cache': cacheLabel,
    ...(extraHeaders || {}),
  });
  if (typeof res.flushHeaders === 'function') res.flushHeaders();

  for (const chunk of cachedItem.chunks) {
    // Substitui o id da mensagem gravada pelo id deste cliente
    let transformed = chunk;
    if (clientOpenAiId) {
      transformed = chunk.replace(/"id":"chatcmpl-[^"]+"/, `"id":"${clientOpenAiId}"`);
    }
    res.write(transformed);
    // Micro-pausa para não sobrecarregar socket local e manter naturalidade de streaming
    if (cachedItem.chunks.length > 5) {
      await new Promise(r => setTimeout(r, 4));
    }
  }
  res.end();
}
// Cache de respostas, chaveado por payload upstream canônico. Persistido em
// disco (CACHE_FILE) para sobreviver a reinícios do proxy.
const optInResponseCache = new Map();
const OPT_IN_CACHE_MAX = 200;
let cacheDiskDirty = false;
let cacheSaveTimer = null;

function persistCacheSoon() {
  cacheDiskDirty = true;
  if (cacheSaveTimer) return;
  cacheSaveTimer = setTimeout(() => {
    cacheSaveTimer = null;
    if (!cacheDiskDirty) return;
    cacheDiskDirty = false;
    try {
      const now = Date.now();
      const entries = {};
      for (const [k, v] of optInResponseCache) {
        // 'antigravity:' (rota gratuita própria) não vai para disco.
        if (!k.startsWith('antigravity:') && v.until > now) entries[k] = { until: v.until, payload: v.payload };
      }
      fs.writeFileSync(CACHE_FILE, JSON.stringify(entries));
    } catch (err) {
      log('WARN failed to persist response cache:', err.message);
    }
  }, 600);
}

function loadPersistentCache() {
  try {
    if (!fs.existsSync(CACHE_FILE)) return;
    const entries = JSON.parse(fs.readFileSync(CACHE_FILE, 'utf8'));
    const now = Date.now();
    let restored = 0;
    for (const [k, v] of Object.entries(entries || {})) {
      if (v && typeof v.until === 'number' && v.until > now && k.startsWith('antigravity:') === false) {
        optInResponseCache.set(k, { until: v.until, payload: v.payload });
        restored++;
      }
    }
    if (restored > 0) log(`[Cache] ${restored} respostas restauradas do disco (response-cache.json)`);
  } catch (err) {
    log('WARN failed to load persistent cache:', err.message);
  }
}

// Calls in-flight idênticas (não-streaming) — coalescing p/ fan-out de agentes.
const inFlightCalls = new Map();

/**
 * Coordena uma chamada não-streaming:
 *  - estado HIT: resposta servida do cache (persistente) dentro do TTL;
 *  - estado COALESCED: chamada idêntica já em voo — espera a mesma promise;
 *  - estado MISS/DIRECT: chamada real ao upstream.
 * ttlSeconds > 0 habilita o cache. isCacheable(payload) permite excluir
 * respostas não-cacheáveis (ex.: com tool_calls) do armazenamento.
 */
async function coordinatedCall(key, ttlSeconds, fn, isCacheable = null) {
  if (ttlSeconds > 0) {
    const hit = optInResponseCache.get(key);
    if (hit && hit.until > Date.now()) return { state: 'HIT', payload: hit.payload };
  }
  let joined = false;
  let p = inFlightCalls.get(key);
  if (!p) {
    p = (async () => fn())();
    inFlightCalls.set(key, p);
    p.then(
      () => inFlightCalls.delete(key),
      () => inFlightCalls.delete(key),
    );
  } else {
    joined = true;
  }
  const payload = await p;
  const cacheable = ttlSeconds > 0 && (!isCacheable || isCacheable(payload));
  if (cacheable) {
    if (optInResponseCache.size >= OPT_IN_CACHE_MAX) {
      const firstKey = optInResponseCache.keys().next().value;
      if (firstKey) optInResponseCache.delete(firstKey);
    }
    optInResponseCache.set(key, { until: Date.now() + ttlSeconds * 1000, payload });
    persistCacheSoon();
  }
  return { state: joined ? 'COALESCED' : (ttlSeconds > 0 ? 'MISS' : 'DIRECT'), payload };
}

// ---------------------------------------------------------------------------
// Rastreamento de custo (USD) + orçamento diário por provider.
// O preço por milhão de tokens vem de params.priceInPerM / params.priceOutPerM.
// ---------------------------------------------------------------------------
let spendState = { days: {} }; // days: { 'YYYY-MM-DD': { totalCost, providers: { id: { cost, requests, in, out } } } }
let spendDiskDirty = false;
let spendSaveTimer = null;

function localDayKey(now = Date.now()) {
  const d = new Date(now);
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${d.getFullYear()}-${m}-${day}`;
}

function persistSpendSoon() {
  spendDiskDirty = true;
  if (spendSaveTimer) return;
  spendSaveTimer = setTimeout(() => {
    spendSaveTimer = null;
    if (!spendDiskDirty) return;
    spendDiskDirty = false;
    try { fs.writeFileSync(SPEND_FILE, JSON.stringify(spendState, null, 2)); }
    catch (err) { log('WARN failed to persist spend:', err.message); }
  }, 800);
}

function loadSpendState() {
  try {
    if (fs.existsSync(SPEND_FILE)) {
      spendState = JSON.parse(fs.readFileSync(SPEND_FILE, 'utf8'));
      if (!spendState || typeof spendState !== 'object' || !spendState.days) spendState = { days: {} };
    }
  } catch (err) {
    log('WARN failed to load spend state:', err.message);
  }
}

function providerTodaySpend(providerId) {
  const day = spendState.days[localDayKey()];
  return day?.providers?.[providerId]?.cost || 0;
}

/** Se o orçamento diário foi atingido, devolve segundos até a meia-noite; senão null. */
function budgetSecondsRemaining(provider) {
  const budget = numOr(provider?.params?.budgetDailyUSD, 0);
  if (!(budget > 0)) return null;
  if (providerTodaySpend(provider.id) < budget) return null;
  const now = new Date();
  const midnight = new Date(now.getFullYear(), now.getMonth(), now.getDate() + 1, 0, 0, 0, 0);
  return Math.max(1, Math.round((midnight - now) / 1000));
}

/** Registra custo de um request (usage do provider) se houver preço configurado. */
function recordProviderSpend(provider, usage) {
  if (!provider || !usage) return;
  const priceIn = numOr(provider.params?.priceInPerM, 0);
  const priceOut = numOr(provider.params?.priceOutPerM, 0);
  if (!(priceIn > 0) && !(priceOut > 0)) return;
  const inTokens = Number(usage.prompt_tokens) || 0;
  const outTokens = Number(usage.completion_tokens) || 0;
  if (!inTokens && !outTokens) return;
  const cost = (inTokens / 1e6) * priceIn + (outTokens / 1e6) * priceOut;
  const key = localDayKey();
  if (!spendState.days[key]) spendState.days[key] = { totalCost: 0, providers: {} };
  const day = spendState.days[key];
  if (!day.providers[provider.id]) day.providers[provider.id] = { cost: 0, requests: 0, in: 0, out: 0 };
  const rec = day.providers[provider.id];
  rec.cost += cost;
  rec.requests += 1;
  rec.in += inTokens;
  rec.out += outTokens;
  day.totalCost += cost;
  persistSpendSoon();
}

function getSpendSummary() {
  const today = localDayKey();
  const todayDay = spendState.days[today];
  const allCost = Object.values(spendState.days).reduce((s, d) => s + (d.totalCost || 0), 0);
  const providers = {};
  for (const prov of externalProviders) {
    const rec = todayDay?.providers?.[prov.id];
    providers[prov.id] = {
      cost: rec?.cost || 0,
      requests: rec?.requests || 0,
      budget: numOr(prov.params?.budgetDailyUSD, 0),
      priceIn: numOr(prov.params?.priceInPerM, 0),
      priceOut: numOr(prov.params?.priceOutPerM, 0),
    };
  }
  return { date: today, todayCost: todayDay?.totalCost || 0, totalCost: allCost, providers };
}

function numOr(v, dflt) {
  const n = Number(v);
  return Number.isFinite(n) ? n : dflt;
}

/**
 * Aplica parâmetros por provider (defaults quando ausentes + clamps):
 * temperature, top_p, max_tokens e aliases (max_completion_tokens).
 */
function applyProviderParams(body, provider) {
  const pr = provider?.params || {};
  const out = { ...body };
  const capMt = numOr(pr.maxTokensCap, 0);
  const defMt = numOr(pr.maxTokensDefault, 0);
  if (capMt > 0 && typeof out.max_tokens === 'number') out.max_tokens = Math.min(out.max_tokens, capMt);
  if (capMt > 0 && typeof out.max_completion_tokens === 'number') {
    out.max_completion_tokens = Math.min(out.max_completion_tokens, capMt);
  }
  if (defMt > 0 && out.max_tokens === undefined && out.max_completion_tokens === undefined) {
    out.max_tokens = defMt;
  }
  const tMin = numOr(pr.tempMin, 0);
  const tMax = numOr(pr.tempMax, 0);
  const tDef = numOr(pr.tempDefault, 0);
  if (typeof out.temperature === 'number') {
    if (tMin || tMax) out.temperature = Math.min(tMax || 2, Math.max(tMin, out.temperature));
  } else if (tDef > 0) {
    out.temperature = tDef;
  }
  const pMin = numOr(pr.topPMin, 0);
  const pMax = numOr(pr.topPMax, 0);
  const pDef = numOr(pr.topPDefault, 0);
  if (typeof out.top_p === 'number') {
    if (pMin || pMax) out.top_p = Math.min(pMax || 1, Math.max(pMin, out.top_p));
  } else if (pDef > 0) {
    out.top_p = pDef;
  }
  // Instrução/prompt customizado injetado no início da conversa de CADA request.
  const customSys = typeof pr.customSystemPrompt === 'string' ? pr.customSystemPrompt.trim() : '';
  if (customSys) {
    const msgs = Array.isArray(out.messages) ? out.messages.slice() : [];
    msgs.unshift({ role: 'system', content: customSys });
    out.messages = msgs;
  }
  return out;
}

// Catálogo /v1/models: montado uma vez, cacheado ~5s e servido gzip quando o
// cliente aceitar. Invalidado automaticamente a cada alteração de providers.
let modelsCatalogCache = { at: 0, json: null, gz: null };
let modelsCatalogDirty = true;
const MODELS_CATALOG_TTL_MS = 5000;

function buildModelsCatalog() {
  const models = Object.keys(MODEL_ALIASES).map(id => ({
    id, object: 'model', created: 0, owned_by: 'antigravity',
  }));
  // Expõe os modelos padrão de embeddings
  models.push({ id: 'gemini-embedding-001', object: 'model', created: 0, owned_by: 'google' });
  models.push({ id: 'gemini-embedding-2', object: 'model', created: 0, owned_by: 'google' });
  models.push({ id: 'text-embedding-004', object: 'model', created: 0, owned_by: 'google-alias' });
  models.push({ id: 'text-embedding-3-small', object: 'model', created: 0, owned_by: 'openai-alias' });

  for (const prov of externalProviders) {
    if (prov.enabled === false) continue;
    for (const m of (prov.models || [])) {
      models.push({ id: `${prov.id}_${m.id}`, object: 'model', created: 0, owned_by: prov.id });
    }
  }
  return { object: 'list', data: models };
}

function serveModelsCatalog(res, req) {
  const now = Date.now();
  if (modelsCatalogDirty || now - modelsCatalogCache.at > MODELS_CATALOG_TTL_MS) {
    const json = JSON.stringify(buildModelsCatalog());
    modelsCatalogCache = { at: now, json, gz: zlib.gzipSync(Buffer.from(json)) };
    modelsCatalogDirty = false;
  }
  const acceptsGzip = /\bgzip\b/i.test(req.headers['accept-encoding'] || '');
  const buf = acceptsGzip ? modelsCatalogCache.gz : Buffer.from(modelsCatalogCache.json);
  res.writeHead(200, {
    'Content-Type': 'application/json',
    'Content-Length': buf.length,
    'Cache-Control': 'public, max-age=5',
    ...(acceptsGzip ? { 'Content-Encoding': 'gzip', 'Vary': 'Accept-Encoding' } : {}),
  });
  res.end(buf);
}

const server = http.createServer(async (req, res) => {
  if (req.socket) req.socket.setNoDelay(true);
  if (res.socket) res.socket.setNoDelay(true);
  const hostHeader = req.headers.host || '127.0.0.1';
  const url = new URL(req.url, `http://${hostHeader}`);
  const pathname = url.pathname.replace(/\/+$/, '') || '/';
  try {
    if (req.method === 'GET' && (pathname === '/' || pathname === '/dashboard')) {
      const htmlPath = path.join(path.dirname(fileURLToPath(import.meta.url)), 'public', 'index.html');
      try {
        const html = fs.readFileSync(htmlPath, 'utf8');
        res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' });
        return res.end(html);
      } catch (err) {
        return sendJson(res, 500, { error: 'Dashboard view not found: ' + err.message });
      }
    }
    if (req.method === 'GET' && url.pathname === '/api/usage/stats') {
      return sendJson(res, 200, usageStatsSnapshot());
    }
    if (req.method === 'GET' && url.pathname === '/api/dashboard/metrics') {
      ensureAccountsPool();
      const accountsSummary = accountsPool.map((acc, idx) => ({
        name: acc.name,
        source: acc.source,
        projectId: acc.projectId,
        valid: Boolean(acc.accessToken && acc.expiresAt > Date.now()),
        expiresIn: acc.expiresAt > Date.now() ? `${Math.round((acc.expiresAt - Date.now()) / 60000)}m restantes` : 'Ativo',
        coolingFor: accountCoolingSeconds(acc) || undefined,
        pinnedEndpointIndex: acc.pinnedEndpointIndex,   // null = auto
        currentEndpoint: acc.currentIndex ?? 0,
        healthyEndpointIndex: acc.healthyEndpointIndex,
        lastSwitchAt: acc.lastSwitchAt || null,
        endpointStats: acc.endpointStats || ENDPOINTS.map(() => ({ ok: 0, r429: 0, err: 0 })),
      }));
      const quota = await fetchAntigravityQuotas();
      return sendJson(res, 200, {
        version: VERSION,
        serverStartedAt: SERVER_STARTED_AT,
        uptimeSeconds: Math.floor((Date.now() - SERVER_STARTED_AT) / 1000),
        rpm: calculateRpm(),
        totalRequests: totalRequestsCount,
        avgLatency: totalRequestsCount > 0 ? Math.round(totalLatencySum / totalRequestsCount) : 0,
        activeUpstream,
        maxConcurrent: MAX_CONCURRENT,
        activeAccountIndex,
        accountPinned,
        apiRequestsEnabled,
        queueLength: upstreamWaiters.length,
        accounts: accountsSummary,
        endpoints: { labels: ENDPOINT_LABELS, list: endpointHealth },
        externalProviders: externalProviders.map(p => {
          const keys = providerApiKeys(p);
          return {
            id: p.id,
            name: p.name,
            baseUrl: p.baseUrl,
            apiKey: keys[0] || '',
            enabled: p.enabled !== false,
            hasKey: keys.length > 0,
            keysCount: keys.length,
            keyMode: p.keyMode === 'round-robin' ? 'round-robin' : 'failover',
            models: p.models || []
          };
        }),
        quota,
        modelUsageStats,
        recentRequests,
        spend: getSpendSummary(),
        process: getProcessMetrics(),
        embeddings: {
          hasKey: Boolean(geminiApiKey),
          geminiApiKeyMasked: geminiApiKey ? geminiApiKey.slice(0, 4) + '...' + geminiApiKey.slice(-4) : '',
          stats: {
            ...embeddingsStats,
            cacheSize: embeddingsCache.size,
          }
        },
        semantic: {
          enabled: semanticEnabled && Boolean(geminiApiKey),
          hasKey: Boolean(geminiApiKey),
          threshold: semanticThreshold,
          size: semanticCache.size,
          ...semanticStats,
        },
      });
    }
    // Controle de host por conta: fixar um endpoint (sem fallback) ou voltar ao auto.
    if (req.method === 'POST' && url.pathname === '/api/dashboard/endpoints') {
      let body = '';
      for await (const chunk of req) body += chunk;
      try {
        const data = JSON.parse(body);
        ensureAccountsPool();
        if (data.action === 'pin') {
          const accName = String(data.account || '');
          let pin = null;
          if (data.index !== null && data.index !== undefined && data.index !== '') {
            pin = Number(data.index);
            if (!Number.isInteger(pin) || pin < 0 || pin >= ENDPOINTS.length) {
              return sendJson(res, 400, { error: `index deve ser 0..${ENDPOINTS.length - 1} ou null (auto)` });
            }
          }
          let targets = [];
          if (accName === 'all') {
            targets = accountsPool;
          } else {
            const found = accountsPool.find(a => a.name === accName);
            if (!found) return sendJson(res, 404, { error: `Conta "${accName}" não encontrada no pool` });
            targets = [found];
          }
          for (const a of targets) {
            a.pinnedEndpointIndex = pin;
            if (pin !== null) a.healthyEndpointIndex = pin;
            log(`[Endpoints] Conta "${a.name}" ${pin === null ? 'AUTO (sistema escolhe)' : 'FIXA em ' + ENDPOINT_LABELS[pin]}`);
          }
          return sendJson(res, 200, { ok: true });
        }
        if (data.action === 'resetCounters') {
          for (const h of endpointHealth) { h.ok = 0; h.r429 = 0; h.err = 0; h.last429At = 0; h.lastOkAt = 0; }
          for (const a of accountsPool) {
            if (a.endpointStats) for (const s of a.endpointStats) { s.ok = 0; s.r429 = 0; s.err = 0; }
          }
          log('[Endpoints] Contadores zerados');
          return sendJson(res, 200, { ok: true });
        }
        return sendJson(res, 400, { error: 'Ação desconhecida' });
      } catch (e) {
        return sendJson(res, 400, { error: e.message });
      }
    }
    if (req.method === 'POST' && url.pathname === '/api/dashboard/restart') {
      log('[Dashboard] Comando de reinicialização recebido via painel web.');
      sendJson(res, 200, { ok: true, message: 'Reiniciando wrapper proxy...' });
      setTimeout(() => {
        // Encerra o processo Node com código 0; o systemd com Restart=always reinicia o serviço instantaneamente
        process.exit(0);
      }, 500);
      return;
    }
    if (req.method === 'POST' && url.pathname === '/api/dashboard/config') {
      let body = '';
      for await (const chunk of req) body += chunk;
      try {
        const data = JSON.parse(body);
        ensureAccountsPool();
        if (data.thinkingLevel) THINKING_LEVEL = data.thinkingLevel;
        if (typeof data.maxConcurrent === 'number' && data.maxConcurrent >= 1) {
          MAX_CONCURRENT = data.maxConcurrent;
        }
        if (typeof data.apiRequestsEnabled === 'boolean') {
          apiRequestsEnabled = data.apiRequestsEnabled;
          log(`[Dashboard Hot-Reload] API externa OpenAI ${apiRequestsEnabled ? 'ATIVADA' : 'DESATIVADA'}`);
        }
        if (typeof data.activeAccountIndex === 'number' && data.activeAccountIndex >= 0 && data.activeAccountIndex < accountsPool.length) {
          activeAccountIndex = data.activeAccountIndex;
          accountPinned = true; // usuário escolheu explicitamente: fixa a conta
          lastQuotaFetchTime = 0;
          cachedQuotaInfo = null;
          log(`[Dashboard Hot-Reload] Conta prioritária FIXADA em #${activeAccountIndex + 1} (${accountsPool[activeAccountIndex]?.name})`);
        }
        if (typeof data.accountPinned === 'boolean') {
          accountPinned = data.accountPinned;
          lastQuotaFetchTime = 0;
          cachedQuotaInfo = null;
          if (!accountPinned) log('[Dashboard Hot-Reload] Conta solta: voltando ao modo automático (última que funcionou)');
        }
        if (data.geminiApiKey !== undefined) {
          geminiApiKey = String(data.geminiApiKey || '').trim();
          saveEmbeddingsConfig();
          syncSemanticDisabled();
          log(`[Dashboard Hot-Reload] Gemini API Key para embeddings ${geminiApiKey ? 'atualizada' : 'removida'}`);
        }
        if (typeof data.semanticEnabled === 'boolean') {
          semanticEnabled = data.semanticEnabled;
          syncSemanticDisabled();
          log(`[Dashboard Hot-Reload] Cache Semântico ${semanticEnabled ? 'ATIVADO' : 'DESATIVADO'}`);
        }
        if (typeof data.semanticThreshold === 'number') {
          semanticThreshold = Math.min(1, Math.max(0, data.semanticThreshold));
          log(`[Dashboard Hot-Reload] Threshold semântico = ${semanticThreshold}`);
        }
        if (Array.isArray(data.externalProviders)) {
          saveExternalProviders(data.externalProviders);
          log(`[Dashboard Hot-Reload] ${data.externalProviders.length} Provedores externos atualizados via dashboard`);
        }
        saveProxySettings();
        return sendJson(res, 200, { ok: true, thinkingLevel: THINKING_LEVEL, maxConcurrent: MAX_CONCURRENT, activeAccountIndex, accountPinned });
      } catch (e) {
        return sendJson(res, 400, { error: e.message });
      }
    }
    if (req.method === 'GET' && url.pathname === '/healthz') {
      return sendJson(res, 200, { ok: true, version: VERSION });
    }
    if (req.method === 'GET' && (url.pathname === '/v1/models' || url.pathname === '/v1' || url.pathname === '/models')) {
      return serveModelsCatalog(res, req);
    }
    // =====================================================================
    // Endpoint /v1/embeddings (Padrão OpenAI com Cache Persistente em Disco)
    // =====================================================================
    if (req.method === 'POST' && url.pathname === '/v1/embeddings') {
      let body = '';
      for await (const chunk of req) body += chunk;
      const t0 = Date.now();
      try {
        const reqData = JSON.parse(body);
        const requestedModel = String(reqData.model || 'gemini-embedding-001');
        // Normaliza aliases legados / OpenAI para o modelo ativo do Google
        const modelName = (requestedModel === 'text-embedding-004' || requestedModel.startsWith('text-embedding-3'))
          ? 'gemini-embedding-001'
          : requestedModel;
        const rawInput = reqData.input;
        const inputs = Array.isArray(rawInput) ? rawInput : [rawInput];

        if (inputs.length === 0 || inputs.some(i => typeof i !== 'string')) {
          return sendJson(res, 400, {
            error: { message: 'Campo input deve ser uma string ou array de strings.', type: 'invalid_request_error' }
          });
        }

        embeddingsStats.totalRequests++;
        embeddingsStats.totalItems += inputs.length;

        const results = new Array(inputs.length);
        const missingIndices = [];
        const missingTexts = [];

        // 1. Tenta resolver cada item pelo cache persistente em disco (Hash SHA-256)
        for (let i = 0; i < inputs.length; i++) {
          const txt = inputs[i];
          const cacheKey = modelName + ':' + sha256Hex(txt);
          const cachedVector = embeddingsCache.get(cacheKey);
          if (cachedVector) {
            results[i] = cachedVector;
            embeddingsStats.cacheHits++;
          } else {
            missingIndices.push(i);
            missingTexts.push(txt);
          }
        }

        // 2. Se houver itens faltantes no cache, chama o upstream (Google AI Studio gemini-embedding-001)
        if (missingTexts.length > 0) {
          embeddingsStats.cacheMisses++;

          if (!geminiApiKey) {
            return sendJson(res, 401, {
              error: {
                message: 'Nenhuma Google Gemini API Key configurada para embeddings no wrapper. Adicione a chave no painel ⚙ Config ou defina a variável GEMINI_API_KEY.',
                type: 'authentication_error'
              }
            });
          }

          // Executa embedContent em paralelo para cada texto faltante
          const embedPromises = missingTexts.map(async (txt) => {
            const googleUrl = `https://generativelanguage.googleapis.com/v1beta/models/${encodeURIComponent(modelName)}:embedContent?key=${encodeURIComponent(geminiApiKey)}`;
            const upResp = await fetch(googleUrl, {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({
                content: { parts: [{ text: txt }] },
                outputDimensionality: 768,
              }),
              signal: AbortSignal.timeout(12000),
            });
            if (!upResp.ok) {
              const errText = await upResp.text().catch(() => '');
              throw new Error(`Google Gemini Embedding API error (${upResp.status}): ${errText.slice(0, 300)}`);
            }
            const upData = await upResp.json();
            return upData.embedding?.values || [];
          });

          let fetchedVectors;
          try {
            fetchedVectors = await Promise.all(embedPromises);
          } catch (embedErr) {
            log(`[Embeddings Upstream Error]: ${embedErr.message}`);
            return sendJson(res, 502, {
              error: { message: embedErr.message, type: 'upstream_error' }
            });
          }

          for (let m = 0; m < missingTexts.length; m++) {
            const vector = fetchedVectors[m];
            const originalIdx = missingIndices[m];
            results[originalIdx] = vector;

            // Grava no cache persistente
            const cacheKey = modelName + ':' + sha256Hex(missingTexts[m]);
            embeddingsCache.set(cacheKey, vector);
          }
          scheduleSaveEmbeddings();
        }

        const duration = Date.now() - t0;
        embeddingsStats.msTotal += duration;

        // Estima tokens de forma aproximada (~4 chars por token)
        const approxTokens = Math.max(1, Math.round(inputs.reduce((acc, t) => acc + t.length, 0) / 4));
        embeddingsStats.tokensTotal += approxTokens;

        // Formata resposta estrita compatível com OpenAI /v1/embeddings
        const openAiResp = {
          object: 'list',
          data: results.map((emb, idx) => ({
            object: 'embedding',
            index: idx,
            embedding: emb
          })),
          model: modelName,
          usage: {
            prompt_tokens: approxTokens,
            total_tokens: approxTokens
          }
        };

        return sendJson(res, 200, openAiResp, {
          'X-Cache-Hits': String(inputs.length - missingIndices.length),
          'X-Cache-Misses': String(missingIndices.length),
        });
      } catch (err) {
        log('ERROR /v1/embeddings:', err.message);
        return sendJson(res, 500, {
          error: { message: err.message, type: 'server_error' }
        });
      }
    }
    // =====================================================================
    // =====================================================================
    // Fontes OAuth — Codex/ChatGPT (sidecar openai-oauth)
    // =====================================================================
    if (url.pathname === '/api/oauth/codex') {
      if (req.method === 'GET') {
        return sendJson(res, 200, await codexStatus());
      }
      if (req.method === 'POST') {
        let body = '';
        for await (const chunk of req) body += chunk;
        let data = {};
        try { data = JSON.parse(body); } catch { /* vazio */ }
        try {
          const act = data.action;
          if (act === 'start') {
            const r = await codexSidecarStart();
            if (r.err) return sendJson(res, 500, { error: `Falha ao iniciar sidecar: ${(r.stderr || r.stdout).slice(0, 300)}` });
            log('[Codex OAuth] Sidecar iniciado (detach): ' + r.stdout.slice(0, 200));
            return sendJson(res, 200, { ok: true, note: r.stdout.slice(0, 300) });
          }
          if (act === 'stop') {
            const r = await codexSidecarStop();
            log('[Codex OAuth] Sidecar parado: ' + (r.stdout || r.stderr || '').slice(0, 200));
            return sendJson(res, 200, { ok: true, note: (r.stdout || r.stderr || '').slice(0, 200) });
          }
          if (act === 'login') {
            const url = await codexNewLoginUrl();
            return sendJson(res, 200, { ok: true, url });
          }
          if (act === 'paste') {
            const r = await codexCompletePaste(data.callbackUrl);
            log(`[Codex OAuth] auth.json gravado (account=${r.accountId})`);
            return sendJson(res, 200, { ok: true, ...r });
          }
          if (act === 'connect') {
            const r = await codexConnectToCatalog();
            log(`[Codex OAuth] Conectado ao catálogo: ${r.models.length} modelos`);
            return sendJson(res, 200, { ok: true, ...r });
          }
          return sendJson(res, 400, { error: 'Ação desconhecida' });
        } catch (e) {
          return sendJson(res, 400, { error: e.message });
        }
      }
    }

    if (url.pathname === '/api/providers') {
      if (req.method === 'GET') {
        return sendJson(res, 200, { providers: externalProviders });
      }
      if (req.method === 'POST') {
        let body = '';
        for await (const chunk of req) body += chunk;
        try {
          const data = JSON.parse(body);
          if (data.action === 'delete') {
            deleteExternalProvider(data.id);
            return sendJson(res, 200, { ok: true, providers: externalProviders });
          }
          if (data.action === 'save' && data.provider) {
            const providerIn = data.provider;
            const hasModels = Array.isArray(providerIn.models) && providerIn.models.length > 0;
            const saved = upsertExternalProvider(providerIn);
            // Auto-fetch: se o usuário não informou modelos e há URL Base,
            // puxamos a lista automaticamente da API do provider.
            let autoFetched = false;
            if (!hasModels && saved.baseUrl) {
              try {
                const { models } = await listModelsFromProviderAnyKey(saved.baseUrl, saved);
                saved.models = models.map(name => ({ id: name, targetModel: name }));
                saveExternalProviders(externalProviders);
                autoFetched = true;
                log(`[Config Panel] Auto-Fetch OK: ${saved.name} — ${models.length} modelos carregados automaticamente`);
              } catch (e) {
                log(`[Config Panel] Auto-Fetch falhou para ${saved.name}: ${e.message}`);
              }
            }
            if (!autoFetched) {
              log(`[Config Panel] Provedor ${saved.name} (${saved.id}) salvo — ${saved.models.length} modelos`);
            }
            return sendJson(res, 200, { ok: true, provider: saved, autoFetched, providers: externalProviders });
          }
          if (data.action === 'toggle' && data.id) {
            const target = externalProviders.find(p => p.id === String(data.id));
            if (target) {
              target.enabled = data.enabled !== false;
              saveExternalProviders(externalProviders);
              log(`[Config Panel] Provedor ${target.name} ${target.enabled ? 'ATIVADO' : 'DESATIVADO'}`);
            }
            return sendJson(res, 200, { ok: true, providers: externalProviders });
          }
          if (data.action === 'fetchModels') {
            try {
              const { models, source } = await listModelsFromProviderAnyKey(data.baseUrl, data);
              log(`[Config Panel] Fetch Models OK: ${models.length} modelos via ${source}`);
              return sendJson(res, 200, { ok: true, models, source });
            } catch (e) {
              return sendJson(res, 400, { error: e.message });
            }
          }
          return sendJson(res, 400, { error: 'Ação desconhecida' });
        } catch (e) {
          return sendJson(res, 400, { error: e.message });
        }
      }
    }
    if (req.method === 'POST' && url.pathname === '/v1/chat/completions') {
      if (!apiRequestsEnabled) {
        return sendJson(res, 503, {
          error: {
            message: 'API externa desativada temporariamente no painel de controle do proxy.',
            type: 'service_unavailable'
          }
        });
      }
      const t0 = Date.now();
      let openAiReq = null;
      let requestedModel = '?';
      let reqStatus = 200;
      let reqDetail = '';
      let usage = null;
      let cacheStateSeen = null;
      try {
        let raw = '';
        for await (const chunk of req) {
          raw += chunk;
          if (raw.length > 5 * 1024 * 1024) {
            reqStatus = 413;
            return sendJson(res, 413, { error: { message: 'Request entity too large', type: 'invalid_request_error' } });
          }
        }
        try { openAiReq = JSON.parse(raw); }
        catch {
          reqStatus = 400;
          return sendJson(res, 400, { error: { message: 'Invalid JSON body', type: 'invalid_request_error' } });
        }

        requestedModel = String(openAiReq.model || 'gemini-3.6-flash-high');

        // =====================================================================
        // Roteamento Multi-Provider (De-Para para Provedores Externos)
        // =====================================================================
        const externalRoute = resolveExternalProvider(requestedModel);
        if (externalRoute) {
          const { provider, targetModel } = externalRoute;
          reqDetail = ` -> [${provider.name}] (${targetModel})`;
          log(`ROUTE ${requestedModel} -> Provider ${provider.name} (${targetModel})`);

          // Orçamento diário: se estourado, responde 429 com Retry-After até a meia-noite.
          const budgetWait = budgetSecondsRemaining(provider);
          if (budgetWait !== null) {
            reqStatus = 429;
            const msg = `Orçamento diário do provedor "${provider.name}" esgotado (US$ ${numOr(provider.params?.budgetDailyUSD, 0)}/dia). Reinicia à meia-noite.`;
            log(`BUDGET ${provider.name} bloqueado: ${msg}`);
            return sendJson(res, 429, { error: { message: msg, type: 'rate_limit_error' } }, { 'Retry-After': String(budgetWait) });
          }

          // Aplica defaults/clamps de parâmetros configurados no painel (pop-up 🎛).
          const forwardBody = applyProviderParams({ ...openAiReq, model: targetModel }, provider);
          const extSemanticModel = 'ext:' + provider.id + '|' + targetModel;
          const providerBase = provider.baseUrl.replace(/\/+$/, '');
          const targetUrl = `${providerBase}/chat/completions`;

          const baseHeaders = { 'Content-Type': 'application/json' };
          const keys = providerApiKeys(provider);

          // Realiza o POST para o provider tentando as chaves em sequência:
          //  - sem chave: igual ao legado (sem Authorization)
          //  - round-robin: começa pela chave da vez (diferente a cada request)
          //  - failover: começa pela última que funcionou; em 401/403/429 (ou erro
          //    de rede) pula para a próxima chave e coloca a falha em cooldown.
          // Erros não-2xx preservam o corpo cru (ProviderErrorPassthrough).
          const doFetch = async () => {
            if (keys.length === 0) {
              const provResp = await fetch(targetUrl, { method: 'POST', headers: baseHeaders, body: JSON.stringify(forwardBody) });
              reqStatus = provResp.status;
              if (!provResp.ok) {
                const errText = await provResp.text();
                throw new ProviderErrorPassthrough(provResp.status, errText);
              }
              return provResp;
            }
            const n = keys.length;
            const start = providerKeyStartIdx(provider);
            const order = [];
            for (let i = 0; i < n; i++) order.push((start + i) % n);
            const probeAll = !order.some(idx => !providerKeyCooling(provider, idx));
            let lastErr = null;
            for (const idx of order) {
              if (!probeAll && providerKeyCooling(provider, idx)) continue;
              const key = keys[idx];
              let provResp;
              try {
                provResp = await fetch(targetUrl, {
                  method: 'POST',
                  headers: { ...baseHeaders, Authorization: `Bearer ${key}` },
                  body: JSON.stringify(forwardBody)
                });
              } catch (e) {
                lastErr = e; // erro de rede: tenta a próxima chave
                continue;
              }
              reqStatus = provResp.status;
              if (provResp.ok) {
                providerKeyPtr.set(provider.id, { ptr: idx });
                clearProviderKeyCooldown(provider, idx);
                return provResp;
              }
              const errText = await provResp.text();
              if (KEY_SWITCHABLE_CODES.has(provResp.status)) {
                const ms = provResp.status === 429 ? 45_000 : 5 * 60_000;
                setProviderKeyCooldown(provider, idx, ms);
                log(`Provider ${provider.id}: chave ${idx + 1}/${n} falhou (HTTP ${provResp.status}) -> cooldown ${Math.round(ms / 1000)}s, tentando a próxima`);
              }
              lastErr = new ProviderErrorPassthrough(provResp.status, errText);
              if (!KEY_SWITCHABLE_CODES.has(provResp.status)) break; // 400/404/5xx: sem troca de chave
            }
            throw lastErr || new Error(`Provider ${provider.id}: nenhuma chave disponível`);
          };

          const openAiId = makeOpenAiId();
          if (openAiReq.stream) {
            const ttl = optInTtlSeconds(req, openAiReq) || numOr(provider.params?.cacheTtl, 0);
            const streamKey = 'stream:ext:' + provider.id + '|' + requestedModel + ':' + sha256Hex(stableSerialize(forwardBody));
            const cachedStream = ttl > 0 ? getStreamCache(streamKey) : null;
            if (cachedStream) {
              cacheStateSeen = 'HIT_STREAM_LOCAL';
              usage = cachedStream.usage;
              await replaySseStream(res, cachedStream, openAiId, requestedModel);
              return;
            }

            // Cache semântico (se opt-in de cache ativo)
            if (ttl > 0) {
              const sem = await semanticLookup('stream', extSemanticModel, forwardBody, ttl);
              if (sem.hit && sem.entry?.chunks) {
                cacheStateSeen = 'HIT_SEMANTIC';
                usage = sem.entry.usage;
                await replaySseStream(
                  res, sem.entry, openAiId, requestedModel, 'HIT_SEMANTIC',
                  { 'X-Cache-Similarity': sem.sim.toFixed(3) },
                );
                return;
              }
            }

            let provResp;
            try { provResp = await doFetch(); }
            catch (e) {
              if (e instanceof ProviderErrorPassthrough) {
                res.writeHead(e.status, { 'Content-Type': 'application/json' });
                return res.end(e.text);
              }
              throw e;
            }
            res.writeHead(200, {
              'Content-Type': 'text/event-stream',
              'Cache-Control': 'no-cache',
              Connection: 'keep-alive',
              'X-Accel-Buffering': 'no',
              'X-Cache': ttl > 0 ? 'MISS' : 'DIRECT',
            });
            if (typeof res.flushHeaders === 'function') res.flushHeaders();
            cacheStateSeen = ttl > 0 ? 'MISS' : null;

            // Captura os chunks SSE para playback e usage
            const decoder = new StringDecoder('utf8');
            let textBuf = '';
            let lastUsage = null;
            let hasToolCalls = false;
            const recordedChunks = ttl > 0 ? [] : null;
            const reader = provResp.body.getReader();
            while (true) {
              const { done, value } = await reader.read();
              if (done) break;
              res.write(value);
              if (recordedChunks) recordedChunks.push(Buffer.isBuffer(value) ? value.toString('utf8') : new TextDecoder().decode(value));
              textBuf += decoder.write(value);
              let idx;
              while ((idx = textBuf.indexOf('\n')) >= 0) {
                const line = textBuf.slice(0, idx).replace(/\r$/, '');
                textBuf = textBuf.slice(idx + 1);
                if (line.startsWith('data:')) {
                  const pl = line.slice(5).trim();
                  if (pl && pl !== '[DONE]') {
                    try {
                      const parsedLine = JSON.parse(pl);
                      if (parsedLine?.usage) lastUsage = parsedLine.usage;
                      const delta = parsedLine?.choices?.[0]?.delta;
                      if (delta?.tool_calls && delta.tool_calls.length > 0) hasToolCalls = true;
                    } catch { /* chunk parcial/irrelevante */ }
                  }
                }
              }
            }
            usage = lastUsage;
            recordProviderSpend(provider, lastUsage);
            if (ttl > 0 && !hasToolCalls && recordedChunks && recordedChunks.length > 0) {
              setStreamCache(streamKey, ttl, recordedChunks, lastUsage);
              semanticStore('stream', extSemanticModel, forwardBody, ttl, recordedChunks, lastUsage).catch(() => {});
            }
            res.end();
            return;
          }

          // Non-stream: coalescing (dedupe in-flight) + cache (header ou TTL do provider).
          const ttl = optInTtlSeconds(req, openAiReq) || numOr(provider.params?.cacheTtl, 0);
          const key = 'ext:' + provider.id + '|' + requestedModel + ':' + sha256Hex(stableSerialize(forwardBody));
          const isCacheable = (data) => !(data?.choices || []).some(c =>
            c?.message?.tool_calls && c.message.tool_calls.length > 0
          );

          if (ttl > 0) {
            const sem = await semanticLookup('json', extSemanticModel, forwardBody, ttl);
            if (sem.hit && sem.entry?.payload) {
              cacheStateSeen = 'HIT_SEMANTIC';
              usage = sem.entry.usage;
              const payload = { ...sem.entry.payload, id: makeOpenAiId(), model: requestedModel };
              return sendJson(res, 200, payload, {
                'X-Cache': 'HIT_SEMANTIC',
                'X-Cache-Similarity': sem.sim.toFixed(3),
              });
            }
          }
          let outcome;
          try {
            outcome = await coordinatedCall(key, ttl, async () => {
              const provResp = await doFetch();
              const data = await provResp.json();
              // Mantém o id do modelo original do cliente
              data.model = requestedModel;
              return data;
            }, isCacheable);
          } catch (e) {
            if (e instanceof ProviderErrorPassthrough) {
              res.writeHead(e.status, { 'Content-Type': 'application/json' });
              return res.end(e.text);
            }
            throw e;
          }
          // Respostas compartilhadas (coalescidas/cache) ganham id próprio por cliente.
          const payload = outcome.state === 'DIRECT'
            ? outcome.payload
            : { ...outcome.payload, id: makeOpenAiId(), model: requestedModel };
          usage = payload.usage;
          cacheStateSeen = outcome.state;
          if (outcome.state !== 'HIT') recordProviderSpend(provider, payload.usage);
          if (ttl > 0 && (outcome.state === 'MISS' || outcome.state === 'DIRECT') && isCacheable(outcome.payload)) {
            semanticStore('json', extSemanticModel, forwardBody, ttl, outcome.payload, payload.usage).catch(() => {});
          }
          return sendJson(res, 200, payload, { 'X-Cache': outcome.state });
        }

        // =====================================================================
        // Roteamento Padrão: Google Antigravity
        // =====================================================================
        const geminiBody = translateOpenAiToGemini(openAiReq);

        // Debug/registro: corpo sanitizado + captura one-shot das tools reais.
        if (DEBUG) {
          const bodyJson = JSON.stringify(geminiBody);
          const capped = bodyJson.length > DEBUG_MAX
            ? bodyJson.slice(0, DEBUG_MAX) + '\n...[truncado em ' + bodyJson.length + ' bytes]'
            : bodyJson;
          log('DEBUG gemini request (' + bodyJson.length + ' bytes): ' + capped);
        }
        if (RECORD_TOOLS_FILE && !toolsRecorded) {
          toolsRecorded = true;
          try {
            fs.writeFileSync(RECORD_TOOLS_FILE, JSON.stringify({
              recordedAt: new Date().toISOString(),
              model: requestedModel,
              toolCount: Array.isArray(openAiReq.tools) ? openAiReq.tools.length : 0,
              tools: Array.isArray(openAiReq.tools) ? openAiReq.tools : [],
            }, null, 2));
            log('Recorded incoming tools (' + (Array.isArray(openAiReq.tools) ? openAiReq.tools.length : 0) + ') -> ' + RECORD_TOOLS_FILE);
          } catch (err) {
            log('WARN failed to record tools: ' + err.message);
          }
        }

        const openAiId = 'chatcmpl-' + crypto.randomUUID().replace(/-/g, '').slice(0, 24);
        const created = Math.floor(Date.now() / 1000);

        if (openAiReq.stream) {
          const ttl = optInTtlSeconds(req, openAiReq);
          // O hash da chave de cache para Gemini deve usar openAiReq (sem os UUIDs voláteis requestId/sessionId)
          const streamKey = 'stream:antigravity:' + requestedModel + ':' + sha256Hex(stableSerialize(openAiReq));
          const cachedStream = ttl > 0 ? getStreamCache(streamKey) : null;
          if (cachedStream) {
            cacheStateSeen = 'HIT_STREAM_LOCAL';
            usage = cachedStream.usage;
            await replaySseStream(res, cachedStream, openAiId, requestedModel);
            return;
          }

          // Cache SEMÂNTICO: mesmo sem byte-hit, uma pergunta "quase igual"
          // (cosseno >= threshold) reemite a resposta gravada sem ir ao upstream.
          if (!cachedStream) {
            const sem = await semanticLookup('stream', requestedModel, openAiReq, ttl);
            if (sem.hit && sem.entry && sem.entry.chunks && sem.entry.chunks.length > 0) {
              cacheStateSeen = 'HIT_SEMANTIC';
              usage = sem.entry.usage;
              await replaySseStream(
                res, sem.entry, openAiId, requestedModel, 'HIT_SEMANTIC',
                { 'X-Cache-Similarity': sem.sim.toFixed(3) },
              );
              return;
            }
          }

          const upstream = await callAntigravity(geminiBody, true);
          let streamStarted = false;
          try {
            res.writeHead(200, {
              'Content-Type': 'text/event-stream',
              'Cache-Control': 'no-cache',
              Connection: 'keep-alive',
              'X-Accel-Buffering': 'no',
              'X-Cache': ttl > 0 ? 'MISS' : 'DIRECT',
            });
            if (typeof res.flushHeaders === 'function') res.flushHeaders();
            cacheStateSeen = ttl > 0 ? 'MISS' : null;

            streamStarted = true;
            await streamOpenAiResponse(
              res,
              upstream,
              requestedModel,
              openAiId,
              created,
              geminiBody,
              (finishedData) => {
                usage = finishedData.usage;
                if (ttl > 0 && finishedData.chunks && finishedData.chunks.length > 0) {
                  setStreamCache(streamKey, ttl, finishedData.chunks, finishedData.usage);
                  // Indexa também no cache semântico (async; não atrasa o cliente).
                  const chunks = finishedData.chunks;
                  const u = finishedData.usage;
                  semanticStore('stream', requestedModel, openAiReq, ttl, chunks, u).catch(() => {});
                }
              }
            );
          } catch (e) {
            reqStatus = 502;
            log('ERROR stream:', e.message);
            res.write('data: ' + JSON.stringify({ error: { message: e.message, type: 'upstream_error' } }) + '\n\n');
          } finally {
            if (!streamStarted) releaseUpstream();
          }
          res.end();
          return;
        }

        // Non-stream: coalescing (dedupe in-flight) + cache opt-in por header.
        // Respostas com tool_calls NUNCA são cacheadas (segurança de re-execução).
        const ttl = optInTtlSeconds(req, openAiReq);
        const key = 'antigravity:' + requestedModel + ':' + sha256Hex(stableSerialize(openAiReq));
        const isCacheable = (translated) => !(translated.toolCalls && translated.toolCalls.length > 0);

        // Cache SEMÂNTICO (JSON): só tenta quando o cache exato NÃO vai acertar,
        // para não pagar o custo do embedding à toa em cima de um hit exato.
        if (ttl > 0) {
          const exactHit = optInResponseCache.get(key);
          if (!(exactHit && exactHit.until > Date.now())) {
            const sem = await semanticLookup('json', requestedModel, openAiReq, ttl);
            if (sem.hit && sem.entry && sem.entry.payload) {
              cacheStateSeen = 'HIT_SEMANTIC';
              usage = sem.entry.usage;
              return sendJson(
                res,
                200,
                buildOpenAiResponse(sem.entry.payload, requestedModel, makeOpenAiId(), Math.floor(Date.now() / 1000)),
                { 'X-Cache': 'HIT_SEMANTIC', 'X-Cache-Similarity': sem.sim.toFixed(3) },
              );
            }
          }
        }

        let outcome;
        try {
          outcome = await coordinatedCall(key, ttl, async () => {
            const upstream = await callAntigravity(geminiBody, false);
            const text = await upstream.text();
            let parsed;
            try { parsed = JSON.parse(text); } catch {
              throw new UpstreamHttpError(502, 'Bad upstream response: ' + text.slice(0, 200));
            }
            if (parsed?.error) {
              // Transporta o corpo cru; classificação roda por-requisição abaixo.
              throw new UpstreamBadBodyError(parsed);
            }
            const translated = translateGeminiToOpenAi(parsed, requestedModel);
            if (!translated) throw new UpstreamHttpError(502, 'Empty upstream response');
            return translated;
          }, isCacheable);
        } catch (e) {
          if (e instanceof UpstreamBadBodyError) {
            const raw = e.parsed.error.message || JSON.stringify(e.parsed.error);
            const code = Number(e.parsed.error.code) || undefined;
            const c = classifyUpstreamError(code, raw, requestedModel);
            reqStatus = c.status;
            return sendJson(res, c.status, { error: { message: c.message, type: 'invalid_request_error' } });
          }
          throw e;
        }
        const translated = outcome.payload;
        if (translated.usage) {
          reqDetail = ' usage=' + translated.usage.prompt_tokens + 'p/' + translated.usage.completion_tokens + 'c';
        }
        usage = translated.usage;
        cacheStateSeen = outcome.state;
        // Indexa no cache semântico quando veio do upstream (MISS/DIRECT) e é
        // textual (sem tool_calls). Async: não atrasa o cliente.
        if (ttl > 0 && (outcome.state === 'MISS' || outcome.state === 'DIRECT')
            && !(translated.toolCalls && translated.toolCalls.length > 0)) {
          semanticStore('json', requestedModel, openAiReq, ttl, translated, translated.usage).catch(() => {});
        }
        return sendJson(
          res,
          200,
          buildOpenAiResponse(translated, requestedModel, makeOpenAiId(), Math.floor(Date.now() / 1000)),
          { 'X-Cache': outcome.state },
        );
      } catch (e) {
        if (e instanceof RateLimitError) {
          // Contrato 429 retryable: o cliente (DSH) faz backoff e retenta.
          reqStatus = 429;
          if (!res.headersSent) {
            const headers = { 'Content-Type': 'application/json' };
            if (e.retryAfterSeconds) headers['Retry-After'] = String(e.retryAfterSeconds);
            const body = JSON.stringify({ error: { message: e.message, type: 'rate_limit_error' } });
            res.writeHead(429, { ...headers, 'Content-Length': Buffer.byteLength(body) });
            res.end(body);
            return;
          }
          res.write('data: ' + JSON.stringify({ error: { message: e.message, type: 'rate_limit_error' } }) + '\n\n');
          res.end();
          return;
        }
        log('ERROR request:', e.message);
        reqStatus = 500;
        if (!res.headersSent) {
          if (e instanceof UpstreamHttpError) {
            const c = classifyUpstreamError(e.status, e.message, requestedModel);
            reqStatus = c.status;
            return sendJson(res, c.status, { error: { message: c.message, type: 'invalid_request_error' } });
          }
          return sendJson(res, 500, { error: { message: e.message, type: 'server_error' } });
        }
        res.end();
      } finally {
        const ms = Date.now() - t0;
        recordRequestTelemetry(requestedModel, openAiReq?.stream, reqStatus, ms, usage, cacheStateSeen);
        log('REQ ' + requestedModel + (openAiReq?.stream ? ' stream' : '') + ' -> ' + reqStatus + ' in ' + ms + 'ms' + reqDetail);
      }
    }
    return sendJson(res, 404, { error: { message: 'Not found: ' + url.pathname, type: 'invalid_request_error' } });
  } catch (e) {
    log('ERROR request:', e.message);
    if (!res.headersSent) {
      return sendJson(res, 500, { error: { message: e.message, type: 'server_error' } });
    }
    res.end();
  }
});

const HOST = process.env.ANTIGRAVITY_PROXY_HOST || '0.0.0.0';

// Restaura estado persistente (spend + cache em disco) antes de escutar.
loadSpendState();
loadPersistentCache();

// Só inicia o servidor quando executado diretamente (node server.mjs);
// importações (testes) não abrem porta.
const isMain = process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href;
if (isMain) {
  server.listen(PORT, HOST, () => {
    log('antigravity-proxy ' + VERSION + ' listening on http://' + HOST + ':' + PORT);
  });
}

export {
  translateOpenAiToGemini,
  translateGeminiToOpenAi,
  buildOpenAiResponse,
  streamOpenAiResponse,
  createOpenAiChunk,
  RateLimitError,
  UpstreamHttpError,
  classifyUpstreamError,
  backoffDelay,
  isAccountQuota,
  MODEL_ALIASES,
  resolveModel,
  GEMINI_PARAMETERS_KEYWORDS,
  // Semantic Cache (exposto para os testes de contrato)
  semanticPartsFromReq,
  semanticEligibleReq,
  semanticLookup,
  semanticStore,
  semanticCache,
  semanticCacheKey,
  semanticStats,
  cosineSim,
  sha256Hex,
  embeddingsCache,
  EMBEDDINGS_SETTINGS_FILE,
  SEMANTIC_EMBED_MODEL,
};

// Setters de teste (chave/limiar) — usados apenas pela suíte de contrato.
export function semanticTestSetKey(v) { geminiApiKey = String(v || ''); syncSemanticDisabled(); }
export function semanticTestSetThreshold(v) { semanticThreshold = Number(v); }
export function semanticTestSetEnabled(v) { semanticEnabled = Boolean(v); syncSemanticDisabled(); }
