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
    const maxRetries = 3;
    for (let attempt = 1; attempt <= maxRetries; attempt++) {
      try {
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
      } catch (err) {
        const status = err.status_code || err.status || err.response?.status;
        const msg = err.message || '';
        const isRateLimit = status === 429 || msg.includes('rate limit') || msg.includes('capacity');

        if (isRateLimit && attempt < maxRetries) {
          console.log(`  [${this.playerId}/${this.role}] Rate limited — waiting 60s (attempt ${attempt}/${maxRetries})`);
          await new Promise(r => setTimeout(r, 60000));
          continue;
        }
        if (isRateLimit) {
          const rateLimitErr = new Error('RATE_LIMIT_EXHAUSTED');
          rateLimitErr.isRateLimitExhausted = true;
          throw rateLimitErr;
        }
        throw err;
      }
    }
  }
}

module.exports = { OllamaCloudAgent };
