const axios = require('axios');
const { BaseAgent } = require('../base-agent.js');
const { CONFIG } = require('../../config.js');

/**
 * Custom Bot — a fine-tuned 7B parameter LLM trained on 106 CTF game sessions.
 *
 * Base model: Qwen2.5-7B-Instruct
 * Training: LoRA fine-tuning (2000 iterations) on ~22,000 attack/defense examples
 *
 * This is a pure LLM agent — no scripted fallback. The 7B model should be capable
 * enough to produce THINKING/COMMAND format output on its own. If it doesn't,
 * the existing base-agent parser handles messy output.
 */
class CustomBotAgent extends BaseAgent {
  constructor(opts) {
    super({ ...opts, provider: 'custom-bot' });
    this.model = 'ctf-custom';
    this.baseUrl = CONFIG.api.ollama?.baseUrl || 'http://localhost:11434';
  }

  async callModel(messages) {
    const response = await axios.post(
      `${this.baseUrl}/api/chat`,
      {
        model: this.model,
        messages,
        stream: false,
        think: false,
        keep_alive: '30m',
        options: {
          temperature: 0.7,
          num_predict: 512,
        },
      },
      {
        headers: { 'Content-Type': 'application/json' },
        timeout: 120000, // 7B model may need more time
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

module.exports = { CustomBotAgent };
