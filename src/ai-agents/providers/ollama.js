const axios = require('axios');
const { BaseAgent } = require('../base-agent.js');
const { CONFIG } = require('../../config.js');

/**
 * Ollama provider — serves local models (Qwen, Gemma, SmolLM, Granite, Llama, etc.)
 * via the Ollama REST API (OpenAI-compatible chat endpoint).
 *
 * Ollama runs as a Docker service on the host, accessible at localhost:11434.
 * The CTF containers CANNOT reach Ollama (different network).
 * Only the Node.js orchestrator on the host talks to Ollama.
 */
class OllamaAgent extends BaseAgent {
  constructor(opts) {
    super({ ...opts, provider: 'ollama' });
    this.model = CONFIG.players.find((p) => p.id === opts.playerId).model;
    this.baseUrl = CONFIG.api.ollama.baseUrl;
  }

  async callModel(messages) {
    const response = await axios.post(
      `${this.baseUrl}/api/chat`,
      {
        model: this.model,
        messages,
        stream: false,
        think: false,  // Disable "thinking" mode — we capture reasoning via the THINKING: format instead
        keep_alive: '30m', // Keep model loaded for 30 min between requests
        options: {
          temperature: 0.7,
          num_predict: 2048,
        },
      },
      {
        headers: { 'Content-Type': 'application/json' },
        // Local models can be slow, especially on CPU — generous timeout
        timeout: 300000,
      }
    );

    const data = response.data;
    return {
      text: data.message?.content || '',
      thinking: '',
      tokensUsed: (data.prompt_eval_count || 0) + (data.eval_count || 0),
    };
  }
}

/**
 * Pull a model into Ollama if not already present.
 * Called during game initialization.
 */
async function pullOllamaModel(modelName) {
  const baseUrl = CONFIG.api.ollama.baseUrl;

  // Check if model is already available
  try {
    const tagsResp = await axios.get(`${baseUrl}/api/tags`, { timeout: 5000 });
    const models = tagsResp.data.models || [];
    if (models.some((m) => m.name === modelName || m.name.startsWith(modelName + ':'))) {
      console.log(`[Ollama] Model "${modelName}" already available.`);
      return;
    }
  } catch {
    // Tags endpoint failed — try pulling anyway
  }

  console.log(`[Ollama] Pulling model "${modelName}" (this may take a while on first run)...`);
  try {
    const resp = await axios.post(
      `${baseUrl}/api/pull`,
      { name: modelName, stream: false },
      { timeout: 3600000 } // 60 minute timeout for large model downloads
    );
    console.log(`[Ollama] Model "${modelName}" ready.`);
    return resp.data;
  } catch (err) {
    const detail = err.response?.data?.error || err.response?.data || err.message;
    throw new Error(`Failed to pull Ollama model "${modelName}": ${detail}`);
  }
}

/**
 * Check if Ollama is reachable.
 */
async function checkOllamaHealth() {
  try {
    const resp = await axios.get(`${CONFIG.api.ollama.baseUrl}/api/tags`, { timeout: 5000 });
    return resp.status === 200;
  } catch {
    return false;
  }
}

/**
 * Pre-warm a model by sending a tiny request so it's loaded into memory.
 * This avoids cold-start delays during the actual game.
 */
async function warmModel(modelName) {
  const baseUrl = CONFIG.api.ollama.baseUrl;
  try {
    await axios.post(
      `${baseUrl}/api/chat`,
      {
        model: modelName,
        messages: [{ role: 'user', content: 'hi' }],
        stream: false,
        keep_alive: '30m',
        options: { num_predict: 1 },
      },
      { timeout: 300000 }
    );
    return true;
  } catch {
    return false;
  }
}

module.exports = { OllamaAgent, pullOllamaModel, checkOllamaHealth, warmModel };
