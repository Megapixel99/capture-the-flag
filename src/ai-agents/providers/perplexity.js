const axios = require('axios');
const { BaseAgent } = require('../base-agent.js');
const { CONFIG } = require('../../config.js');

/**
 * Perplexity provider.
 * Uses OpenAI-compatible chat completions API.
 */
class PerplexityAgent extends BaseAgent {
  constructor(opts) {
    super({ ...opts, provider: 'perplexity' });
    this.model = CONFIG.players.find((p) => p.id === opts.playerId).model;
  }

  async callModel(messages) {
    const response = await axios.post(
      `${CONFIG.api.perplexity.baseUrl}/chat/completions`,
      {
        model: this.model,
        messages,
        max_tokens: 2048,
        temperature: 0.7,
      },
      {
        headers: {
          Authorization: `Bearer ${CONFIG.api.perplexity.apiKey}`,
          'Content-Type': 'application/json',
        },
        timeout: 60000,
      }
    );

    const choice = response.data.choices[0];
    return {
      text: choice.message.content || '',
      thinking: '',
      tokensUsed: response.data.usage?.total_tokens || null,
    };
  }
}

module.exports = { PerplexityAgent };
