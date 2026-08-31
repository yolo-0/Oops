const STORAGE_KEY = 'oops.frontend.settings'

const BACKENDS = {
  python: { label: 'Oops Python', baseUrl: '/api/python' },
  java: { label: 'Oops Java', baseUrl: '/api/java' },
}

export function backendMeta(backend, settings) {
  return { ...(BACKENDS[backend] || BACKENDS.python) }
}

export function createInitialSettings() {
  let saved = {}
  try {
    saved = JSON.parse(localStorage.getItem(STORAGE_KEY) || '{}')
  } catch (e) {
    saved = {}
  }
  return {
    backend: saved.backend || 'python',
    userId: saved.userId || 'u1001',
    conversationId: saved.conversationId || '',
  }
}

export function saveSettings(settings) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify({
      backend: settings.backend,
      userId: settings.userId,
      conversationId: settings.conversationId,
    }))
  } catch (e) {
    /* localStorage 不可用时静默失败 */
  }
}

function baseUrl(backend) {
  return (BACKENDS[backend] || BACKENDS.python).baseUrl
}

async function request(backend, path, options = {}) {
  const url = `${baseUrl(backend)}${path}`
  const res = await fetch(url, options)
  if (!res.ok) {
    let detail = ''
    try {
      const data = await res.json()
      detail = data.detail || JSON.stringify(data)
    } catch (e) {
      /* ignore */
    }
    throw new Error(detail || `HTTP ${res.status}`)
  }
  return res.json()
}

export function requestHealth(backend, settings) {
  return request(backend, '/health')
}

export async function requestChat(backend, settings, message) {
  const body = {
    message,
    user_id: settings.userId || 'anonymous',
  }
  if (settings.conversationId) {
    body.conv_id = settings.conversationId
  }

  const data = await request(backend, '/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })

  return {
    conversationId: data.conv_id ?? data.conversationId,
    response: data.response,
    intent: data.intent,
    agentType: data.agent_type ?? data.agentType,
    knowledgeUsed: data.knowledge_used ?? data.knowledgeUsed,
    escalated: data.escalated,
  }
}

export function requestKnowledgeStats(backend, settings) {
  return request(backend, '/knowledge/stats')
}

export function requestMonitor(backend, settings) {
  return request(backend, '/monitor')
}

export function requestSearch(backend, settings, query, topK = 5) {
  const params = new URLSearchParams({ query, top_k: String(topK) })
  return request(backend, `/search?${params.toString()}`, { method: 'POST' })
}

export function addKnowledge(backend, settings, documents) {
  return request(backend, '/knowledge/add', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ documents }),
  })
}

export function uploadKnowledge(backend, settings, file) {
  const form = new FormData()
  form.append('file', file)
  return request(backend, '/knowledge/upload', {
    method: 'POST',
    body: form,
  })
}
