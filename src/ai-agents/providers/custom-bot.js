const axios = require('axios');
const { BaseAgent } = require('../base-agent.js');
const { CONFIG } = require('../../config.js');

/**
 * Custom Bot — a fine-tuned 3B parameter LLM trained on 106 CTF game sessions.
 *
 * Base model: Qwen2.5-3B-Instruct
 * Training: LoRA fine-tuning (1000 iterations, batch=1) on ~22,000 attack/defense examples
 * Quantized to Q4_K_M for 5x faster inference (~10 tok/s vs ~2 tok/s at F16)
 *
 * This is a pure LLM agent. The Q4 quantized model keeps turns under ~15s,
 * allowing ~15+ commands per 5-min battle instead of 6-7 at F16 precision.
 */
class CustomBotAgent extends BaseAgent {
  constructor(opts) {
    super({ ...opts, provider: 'custom-bot' });
    this.model = 'ctf-custom-q4';
    this.baseUrl = CONFIG.api.ollama?.baseUrl || 'http://localhost:11434';
    this.maxHistoryTurns = 8; // Keep history short for fast prompt eval
  }

  async callModel(messages) {
    // Trim conversation history to keep prompt eval fast (~2s vs ~10s)
    const trimmed = this._trimHistory(messages);

    // Inject a few-shot example so the model stays in THINKING/COMMAND format
    const augmented = [...trimmed];
    const sysIdx = augmented.findIndex(m => m.role === 'system');
    if (sysIdx >= 0) {
      augmented.splice(sysIdx + 1, 0,
        { role: 'user', content: 'What is your next action? Respond with THINKING: then COMMAND:' },
        { role: 'assistant', content: 'THINKING: I need to check if the target has SSH open with default credentials.\nCOMMAND: sshpass -p \'toor\' ssh -o StrictHostKeyChecking=no root@10.10.0.20 \'cat /root/flag.txt\' 2>&1' },
        { role: 'user', content: 'Permission denied, please try again.\n\nWhat is your next action?' },
        { role: 'assistant', content: 'THINKING: Default root password failed. Try command injection on the web service.\nCOMMAND: curl -s -m 10 "http://10.10.0.20:8080/ping?host=127.0.0.1;cat%20/root/flag.txt"' },
      );
    }

    const response = await axios.post(
      `${this.baseUrl}/api/chat`,
      {
        model: this.model,
        messages: augmented,
        stream: false,
        think: false,
        keep_alive: '30m',
        options: {
          temperature: 0.7,
          num_predict: 150,
        },
      },
      {
        headers: { 'Content-Type': 'application/json' },
        timeout: 90000, // ~15s typical when warm, up to ~60s on cold load with GPU contention
      }
    );

    const data = response.data;
    return {
      text: data.message?.content || '',
      thinking: '',
      tokensUsed: (data.prompt_eval_count || 0) + (data.eval_count || 0),
    };
  }

  /**
   * Keep only the system prompt + last N user/assistant pairs.
   * Reduces prompt eval from ~500 tokens to ~200, saving ~5s per turn.
   */
  _trimHistory(messages) {
    const sys = messages.filter(m => m.role === 'system');
    const rest = messages.filter(m => m.role !== 'system');
    const maxMsgs = this.maxHistoryTurns * 2; // user+assistant pairs
    if (rest.length <= maxMsgs) return messages;
    return [...sys, ...rest.slice(-maxMsgs)];
  }
}

module.exports = { CustomBotAgent };
