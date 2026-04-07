const { GoogleGenerativeAI } = require('@google/generative-ai');
const { BaseAgent } = require('../base-agent.js');
const { CONFIG } = require('../../config.js');

/**
 * Google Gemini provider.
 */
class GeminiAgent extends BaseAgent {
  constructor(opts) {
    super({ ...opts, provider: 'gemini' });
    this.genAI = new GoogleGenerativeAI(CONFIG.api.gemini.apiKey);
    this.model = CONFIG.players.find((p) => p.id === opts.playerId).model;
  }

  async callModel(messages) {
    const model = this.genAI.getGenerativeModel({ model: this.model });

    // Convert OpenAI-format messages to Gemini format
    const systemInstruction = messages.find((m) => m.role === 'system')?.content || '';
    const chatMessages = messages
      .filter((m) => m.role !== 'system')
      .map((m) => ({
        role: m.role === 'assistant' ? 'model' : 'user',
        parts: [{ text: m.content }],
      }));

    // Gemini requires alternating user/model messages — merge consecutive same-role messages
    const merged = [];
    for (const msg of chatMessages) {
      if (merged.length > 0 && merged[merged.length - 1].role === msg.role) {
        merged[merged.length - 1].parts.push(...msg.parts);
      } else {
        merged.push({ ...msg });
      }
    }

    // Extract the last user message as the prompt, use the rest as history
    const lastMessage = merged.pop();

    const chat = model.startChat({
      systemInstruction,
      history: merged,
      generationConfig: {
        maxOutputTokens: 2048,
        temperature: 0.7,
      },
    });

    const result = await chat.sendMessage(lastMessage.parts.map((p) => p.text).join('\n'));
    const response = result.response;

    return {
      text: response.text(),
      thinking: '', // Gemini thinking is embedded in the response
      tokensUsed: response.usageMetadata?.totalTokenCount || null,
    };
  }
}

module.exports = { GeminiAgent };
