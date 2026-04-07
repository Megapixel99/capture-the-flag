const OpenAI = require('openai');
const { BaseAgent } = require('../base-agent.js');
const { CONFIG } = require('../../config.js');

/**
 * ChatGPT (OpenAI GPT-4o) provider.
 */
class OpenAIAgent extends BaseAgent {
  constructor(opts) {
    super({ ...opts, provider: 'openai' });
    this.client = new OpenAI({ apiKey: CONFIG.api.openai.apiKey });
    this.model = CONFIG.players.find((p) => p.id === opts.playerId).model;
  }

  async callModel(messages) {
    const response = await this.client.chat.completions.create({
      model: this.model,
      messages,
      max_tokens: 2048,
      temperature: 0.7,
    });

    const choice = response.choices[0];
    return {
      text: choice.message.content || '',
      thinking: '', // GPT-4o doesn't expose chain-of-thought separately
      tokensUsed: response.usage?.total_tokens || null,
    };
  }
}

module.exports = { OpenAIAgent };
