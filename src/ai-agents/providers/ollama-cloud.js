const { Ollama } = require('ollama');
const { BaseAgent } = require('../base-agent.js');
const { CONFIG } = require('../../config.js');

/**
 * Ollama Cloud provider — uses ollama.com's hosted inference API.
 * Supports full-size models (gemma4, gpt-oss:120b, gemini-3-flash-preview)
 * without local GPU. Requires OLLAMA_API_KEY.
 *
 * Each agent gets its own Ollama client instance pointing at https://ollama.com.
 * Cloud API handles concurrency natively — no model swapping or GPU contention.
 */
class OllamaCloudAgent extends BaseAgent {
  constructor(opts) {
    super({ ...opts, provider: 'ollama-cloud' });
    const playerConfig = CONFIG.players.find((p) => p.id === opts.playerId);
    this.model = playerConfig.model;
    this.client = new Ollama({
      host: 'https://ollama.com',
      headers: { Authorization: 'Bearer ' + CONFIG.api.ollamaCloud.apiKey },
    });
  }

  async callModel(messages) {
    const response = await this.client.chat({
      model: this.model,
      messages,
      stream: false,
      options: {
        temperature: 0.7,
        num_predict: 2048,
      },
    });

    return {
      text: response.message?.content || '',
      thinking: '',
      tokensUsed: (response.prompt_eval_count || 0) + (response.eval_count || 0),
    };
  }
}

module.exports = { OllamaCloudAgent };
