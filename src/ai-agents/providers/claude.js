const Anthropic = require('@anthropic-ai/sdk');
const { BaseAgent } = require('../base-agent.js');
const { CONFIG } = require('../../config.js');

/**
 * Anthropic Claude provider.
 */
class ClaudeAgent extends BaseAgent {
  constructor(opts) {
    super({ ...opts, provider: 'claude' });
    this.client = new Anthropic({ apiKey: CONFIG.api.claude.apiKey });
    this.model = CONFIG.players.find((p) => p.id === opts.playerId).model;
  }

  async callModel(messages) {
    // Separate system message from conversation
    const systemMessage = messages.find((m) => m.role === 'system')?.content || '';
    const conversationMessages = messages
      .filter((m) => m.role !== 'system')
      .map((m) => ({
        role: m.role,
        content: m.content,
      }));

    // Claude requires alternating user/assistant — merge consecutive same-role messages
    const merged = [];
    for (const msg of conversationMessages) {
      if (merged.length > 0 && merged[merged.length - 1].role === msg.role) {
        merged[merged.length - 1].content += '\n\n' + msg.content;
      } else {
        merged.push({ ...msg });
      }
    }

    // Ensure conversation starts with user message
    if (merged.length > 0 && merged[0].role !== 'user') {
      merged.unshift({ role: 'user', content: 'Begin.' });
    }

    const response = await this.client.messages.create({
      model: this.model,
      max_tokens: 2048,
      system: systemMessage,
      messages: merged,
    });

    // Extract text and thinking from response blocks
    let text = '';
    let thinking = '';
    for (const block of response.content) {
      if (block.type === 'thinking') {
        thinking += block.thinking;
      } else if (block.type === 'text') {
        text += block.text;
      }
    }

    return {
      text: text || '',
      thinking,
      tokensUsed: (response.usage?.input_tokens || 0) + (response.usage?.output_tokens || 0),
    };
  }
}

module.exports = { ClaudeAgent };
