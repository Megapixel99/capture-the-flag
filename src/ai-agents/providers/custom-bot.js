const axios = require('axios');
const { BaseAgent } = require('../base-agent.js');
const { CONFIG } = require('../../config.js');

/**
 * Custom Bot — a fine-tuned 0.5B parameter LLM trained on 106 CTF game sessions.
 *
 * Base model: Qwen2.5-0.5B-Instruct
 * Training: LoRA fine-tuning (500 iterations) on ~22,000 attack/defense examples
 * Training data: successful attack commands, defense playbooks, and bonus flag discoveries
 * extracted from game logs. Successful captures are weighted 3x in training.
 *
 * The model runs locally via Ollama as "ctf-custom" (~994MB).
 * It generates commands in the same THINKING/COMMAND format as the cloud models.
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
        timeout: 60000,
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
