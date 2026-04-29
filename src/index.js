const { CONFIG } = require('./config.js');
const { buildImage, startContainers, stopContainers, resetContainers } = require('./docker-manager.js');
const { initSession, logGameEvent, getSessionDir } = require('./logger.js');
const { GameEngine } = require('./game-engine.js');
const { generateSetupDoc } = require('./generate-setup.js');
const { pullOllamaModel, checkOllamaHealth, warmModel } = require('./ai-agents/providers/ollama.js');

const playerNames = CONFIG.players.map((p) => p.name).join(' | ');
const BANNER = `
╔═══════════════════════════════════════════════════════════════╗
║              AI CTF ATTACK-DEFEND GAME                        ║
║         Academic Research — Cybersecurity AI Competition      ║
╠═══════════════════════════════════════════════════════════════╣
║  Players: ${playerNames.padEnd(48)}║
║  Each VM: 1 Attacker AI + 1 Defender AI                       ║
║  Goal: Capture /root/flag.txt from enemy VMs                  ║
╚═══════════════════════════════════════════════════════════════╝
`;

async function main() {
  console.log(BANNER);

  const isDryRun = process.argv.includes('--dry-run');
  const skipDocker = process.argv.includes('--skip-docker');
  const testMode = process.argv.includes('--test');

  if (testMode) {
    console.log('[Mode] TEST MODE — using scripted bots instead of AI APIs (no API keys required)\n');
    // Use fewer rounds in test mode unless explicitly overridden
    if (!process.env.GAME_ROUNDS) {
      CONFIG.game.rounds = 10;
    }
    // Faster turns for testing
    if (!process.env.TURN_DELAY_SECONDS) {
      CONFIG.game.turnDelayMs = 1000;
    }
  }

  if (CONFIG.game.selfPlay) {
    console.log(`[Mode] SELF-PLAY — 6 copies of custom bot competing against each other.`);
    console.log(`[Mode] Round-robin scheduling (one agent at a time on GPU).\n`);
  } else if (CONFIG.game.customBot) {
    console.log('[Mode] CUSTOM BOT enabled — log-trained agent competing as additional player.\n');
  }

  if (CONFIG.game.cloudMode && !testMode) {
    console.log(`[Mode] CLOUD — ${CONFIG.players.length} models via Ollama Cloud API (ollama.com). True parallel execution.\n`);
    if (!CONFIG.api.ollamaCloud.apiKey) {
      console.error('[ERROR] OLLAMA_API_KEY is required for cloud mode.');
      console.error('Get an API key at https://ollama.com/settings/keys');
      console.error('Then set it: export OLLAMA_API_KEY=your_key');
      process.exit(1);
    }

    // Pre-flight usage check — make a tiny test request to verify API access and capacity
    const { Ollama } = require('ollama');
    const testClient = new Ollama({
      host: 'https://ollama.com',
      headers: { Authorization: 'Bearer ' + CONFIG.api.ollamaCloud.apiKey },
    });

    let ready = false;
    while (!ready) {
      console.log('[Cloud] Checking API availability...');
      try {
        await testClient.chat({
          model: CONFIG.players[0].model,
          messages: [{ role: 'user', content: 'hi' }],
          stream: false,
          options: { num_predict: 1 },
        });
        console.log('[Cloud] API is available.\n');
        ready = true;
      } catch (err) {
        const status = err.status_code || err.status || err.response?.status;
        const msg = err.message || '';

        if (status === 429 || msg.includes('rate limit') || msg.includes('capacity') || msg.includes('queue is full')) {
          console.log('[Cloud] API is at capacity (rate limited).');
          console.log('[Cloud] Waiting 30 minutes before retrying... (Ctrl+C to cancel)');
          logGameEvent({ message: 'Cloud API at capacity — waiting 30 minutes', error: msg });
          await new Promise(r => setTimeout(r, 30 * 60 * 1000));
        } else if (status === 401 || msg.includes('unauthorized') || msg.includes('invalid')) {
          console.error('[ERROR] Invalid OLLAMA_API_KEY. Check your key at https://ollama.com/settings/keys');
          process.exit(1);
        } else {
          console.error(`[Cloud] API check failed: ${msg}`);
          console.log('[Cloud] Waiting 30 minutes before retrying... (Ctrl+C to cancel)');
          await new Promise(r => setTimeout(r, 30 * 60 * 1000));
        }
      }
    }
  } else if (CONFIG.game.segmented && !testMode) {
    console.log(`[Mode] SEGMENTED — ${CONFIG.players.length} teams, DMZ + Internal zones, lateral movement required\n`);
  } else if (CONFIG.game.allMode && !testMode) {
    console.log(`[Mode] ALL — ${CONFIG.players.length} teams: cloud APIs vs open models side-by-side\n`);
  } else if (CONFIG.game.localOnly && !testMode) {
    console.log('[Mode] LOCAL-ONLY — 5 open models via Ollama. No API keys required.\n');
  } else if (CONFIG.game.useLocalModels && !testMode) {
    console.log('[Mode] LOCAL MODELS — using Ollama for Qwen and Gemma (no API keys needed for those)\n');
  }

  // Validate API keys (skip in test mode, skip Ollama-served players)
  if (!testMode) {
    const missingKeys = [];

    // Only check keys for API-based providers (not Ollama-served players)
    const apiPlayers = CONFIG.players.filter((p) => p.provider !== 'ollama');
    for (const player of apiPlayers) {
      const keyMap = {
        openai: { key: CONFIG.api.openai.apiKey, env: 'OPENAI_API_KEY' },
        gemini: { key: CONFIG.api.gemini.apiKey, env: 'GEMINI_API_KEY' },
        claude: { key: CONFIG.api.claude.apiKey, env: 'ANTHROPIC_API_KEY' },
        grok: { key: CONFIG.api.grok.apiKey, env: 'XAI_API_KEY' },
        perplexity: { key: CONFIG.api.perplexity.apiKey, env: 'PERPLEXITY_API_KEY' },
      };
      const check = keyMap[player.provider];
      if (check && !check.key) {
        missingKeys.push(`${check.env} (${player.name})`);
      }
    }

    if (missingKeys.length > 0) {
      console.error('[ERROR] Missing API keys. Set the following in your .env file:');
      for (const key of missingKeys) {
        console.error(`  - ${key}`);
      }
      console.error('\nCopy .env.example to .env and fill in your keys.');
      console.error('Or use --test to run with scripted bots (no API keys needed).');
      console.error('Or use --local to use Ollama for Qwen and Gemma (reduces required keys).');
      if (!isDryRun) {
        process.exit(1);
      } else {
        console.log('\n[DRY RUN] Continuing without API keys for validation...');
      }
    }
  }

  // Initialize logging session
  const sessionDir = initSession();
  console.log(`[Main] Log directory: ${sessionDir}`);

  if (isDryRun) {
    console.log('\n[DRY RUN] Validation complete. The following would happen in a real run:');
    console.log('  1. Docker image built with vulnerable Ubuntu 22.04');
    console.log(`  2. ${CONFIG.players.length} containers started on isolated network`);
    console.log(`  3. Defense phase: ${CONFIG.game.defensePhaseMinutes} minutes (defenders only)`);
    console.log(`  4. Battle phase: ${CONFIG.game.battlePhaseMinutes} minutes (attackers + defenders simultaneously)`);
    console.log('  5. Full logs written to', sessionDir);
    console.log('\nConfiguration:');
    console.log(JSON.stringify(CONFIG.game, null, 2));
    console.log('\nPlayers:');
    for (const p of CONFIG.players) {
      console.log(`  - ${p.name} (${p.model}) → container: ${p.container}`);
    }

    // Generate setup doc
    await generateSetupDoc(sessionDir);
    console.log('\n[DRY RUN] set-up.txt generated.');
    return;
  }

  // Set up Docker containers
  if (!skipDocker) {
    try {
      await startContainers();
    } catch (err) {
      console.error('[ERROR] Failed to start Docker containers:', err.message);
      console.error('Make sure Docker is running and docker-compose is available.');
      process.exit(1);
    }
  }

  // Launch single Ollama instance with parallel request support for all local models
  if (CONFIG.game.useLocalModels && !testMode) {
    const { execSync, spawn } = require('child_process');
    const axios = require('axios');
    const ollamaPlayers = CONFIG.players.filter((p) => p.provider === 'ollama');
    const url = CONFIG.api.ollama.baseUrl;

    // Find ollama binary
    let ollamaPath = null;
    try {
      ollamaPath = execSync('which ollama 2>/dev/null', { encoding: 'utf-8' }).trim();
    } catch {
      for (const c of ['/usr/local/bin/ollama', '/opt/homebrew/bin/ollama', '/usr/bin/ollama']) {
        try { require('fs').accessSync(c, require('fs').constants.X_OK); ollamaPath = c; break; } catch { /* */ }
      }
    }
    if (!ollamaPath) {
      console.error('[ERROR] Ollama binary not found. Install from https://ollama.com/download');
      process.exit(1);
    }

    // Check if already running
    console.log(`[Ollama] Checking ${url}...`);
    let alive = false;
    try {
      const r = await axios.get(`${url}/api/tags`, { timeout: 2000 });
      alive = r.status === 200;
    } catch { /* not running */ }

    if (!alive) {
      // Force ARM64 on macOS to ensure Metal GPU access (Rosetta/x86 has no GPU)
      const isMac = process.platform === 'darwin';
      const spawnCmd = isMac ? '/usr/bin/arch' : ollamaPath;
      const spawnArgs = isMac ? ['-arm64', ollamaPath, 'serve'] : ['serve'];
      const child = spawn(spawnCmd, spawnArgs, {
        detached: true, stdio: 'ignore',
        env: { ...process.env },
      });
      child.unref();
      console.log(`[Ollama] Started${isMac ? ' (ARM64)' : ''} (PID ${child.pid})`);

      for (let i = 0; i < 15; i++) {
        await new Promise((r) => setTimeout(r, 1000));
        try {
          const r2 = await axios.get(`${url}/api/tags`, { timeout: 2000 });
          if (r2.status === 200) { alive = true; break; }
        } catch { /* retry */ }
      }
      if (!alive) { console.error('[ERROR] Ollama failed to start.'); process.exit(1); }
    } else {
      console.log('[Ollama] Already running.');
    }

    // Pull and warm each model sequentially (they stay loaded in memory)
    console.log(`[Ollama] Loading ${ollamaPlayers.length} models...`);
    const uniqueModels = [...new Set(ollamaPlayers.map(p => p.model))];
    for (const model of uniqueModels) {
      process.stdout.write(`[Ollama]   ${model}...`);
      try {
        const tags = await axios.get(`${url}/api/tags`, { timeout: 5000 });
        const existing = (tags.data.models || []).map(m => m.name);
        if (!existing.some(n => n === model || n.startsWith(model + ':'))) {
          await axios.post(`${url}/api/pull`, { name: model, stream: false }, { timeout: 3600000 });
        }
        await axios.post(`${url}/api/chat`, {
          model, messages: [{ role: 'user', content: 'hi' }],
          stream: false, keep_alive: '60m', options: { num_predict: 1 },
        }, { timeout: 300000 });
        console.log(' ready');
      } catch (err) {
        console.log(` failed (${err.message})`);
      }
    }
    console.log(`[Ollama] All models ready.\n`);

  }

  // --- Custom bot: ensure local Ollama is running and model is warm ---
  if (CONFIG.game.customBot && !testMode) {
    const axios = require('axios');
    const { execSync, spawn } = require('child_process');
    const url = CONFIG.api.ollama?.baseUrl || 'http://localhost:11434';

    // Make sure Ollama is running (it may not have been started if we're in cloud-only mode)
    let ollamaAlive = false;
    try {
      const r = await axios.get(`${url}/api/tags`, { timeout: 2000 });
      ollamaAlive = r.status === 200;
    } catch { /* not running */ }

    if (!ollamaAlive) {
      console.log('[Custom Bot] Local Ollama not running — starting it for custom bot...');
      let ollamaPath = null;
      try {
        ollamaPath = execSync('which ollama 2>/dev/null', { encoding: 'utf-8' }).trim();
      } catch {
        for (const c of ['/usr/local/bin/ollama', '/opt/homebrew/bin/ollama', '/usr/bin/ollama']) {
          try { require('fs').accessSync(c, require('fs').constants.X_OK); ollamaPath = c; break; } catch { /* */ }
        }
      }
      if (ollamaPath) {
        // Force ARM64 on macOS to ensure Metal GPU access (Rosetta/x86 has no GPU)
        const isMac = process.platform === 'darwin';
        const spawnCmd = isMac ? '/usr/bin/arch' : ollamaPath;
        const spawnArgs = isMac ? ['-arm64', ollamaPath, 'serve'] : ['serve'];
        const child = spawn(spawnCmd, spawnArgs, { detached: true, stdio: 'ignore', env: { ...process.env } });
        child.unref();
        for (let i = 0; i < 15; i++) {
          await new Promise(r => setTimeout(r, 1000));
          try {
            const r2 = await axios.get(`${url}/api/tags`, { timeout: 2000 });
            if (r2.status === 200) { ollamaAlive = true; break; }
          } catch { /* retry */ }
        }
      }
    }

    if (ollamaAlive) {
      // Warm the model and verify it landed on GPU
      process.stdout.write('[Custom Bot] Warming up ctf-custom-q4...');
      try {
        await axios.post(`${url}/api/chat`, {
          model: 'ctf-custom-q4',
          messages: [{ role: 'user', content: 'hi' }],
          stream: false, keep_alive: '60m', options: { num_predict: 1 },
        }, { timeout: 300000 });

        // Check GPU allocation — Ollama silently falls back to CPU which is ~45x slower
        const ps = await axios.get(`${url}/api/ps`, { timeout: 5000 });
        const botModel = (ps.data.models || []).find(m => m.name.startsWith('ctf-custom-q4'));
        const vram = botModel?.size_vram || 0;
        const total = botModel?.size || 1;
        const gpuPct = Math.round((vram / total) * 100);

        if (gpuPct < 50) {
          console.log(` on CPU (${gpuPct}% GPU) — restarting Ollama for GPU access...`);
          // Kill and restart Ollama to reclaim GPU
          try { execSync('pkill ollama 2>/dev/null'); } catch { /* */ }
          await new Promise(r => setTimeout(r, 2000));

          let ollamaPath2 = null;
          try { ollamaPath2 = execSync('which ollama 2>/dev/null', { encoding: 'utf-8' }).trim(); } catch {
            for (const c of ['/usr/local/bin/ollama', '/opt/homebrew/bin/ollama', '/usr/bin/ollama']) {
              try { require('fs').accessSync(c, require('fs').constants.X_OK); ollamaPath2 = c; break; } catch { /* */ }
            }
          }
          if (ollamaPath2) {
            const isMac2 = process.platform === 'darwin';
            const cmd2 = isMac2 ? '/usr/bin/arch' : ollamaPath2;
            const args2 = isMac2 ? ['-arm64', ollamaPath2, 'serve'] : ['serve'];
            const child2 = spawn(cmd2, args2, { detached: true, stdio: 'ignore', env: { ...process.env } });
            child2.unref();
            for (let i = 0; i < 15; i++) {
              await new Promise(r => setTimeout(r, 1000));
              try {
                const r2 = await axios.get(`${url}/api/tags`, { timeout: 2000 });
                if (r2.status === 200) break;
              } catch { /* retry */ }
            }
            // Re-warm after restart
            await axios.post(`${url}/api/chat`, {
              model: 'ctf-custom-q4',
              messages: [{ role: 'user', content: 'hi' }],
              stream: false, keep_alive: '60m', options: { num_predict: 1 },
            }, { timeout: 300000 });

            const ps2 = await axios.get(`${url}/api/ps`, { timeout: 5000 });
            const botModel2 = (ps2.data.models || []).find(m => m.name.startsWith('ctf-custom-q4'));
            const gpuPct2 = Math.round(((botModel2?.size_vram || 0) / (botModel2?.size || 1)) * 100);
            console.log(`[Custom Bot] After restart: ${gpuPct2}% GPU — ${gpuPct2 >= 50 ? 'ready (~90 tok/s)' : 'still on CPU, expect slow turns'}`);
          }
        } else {
          console.log(` ready (${gpuPct}% GPU, ~90 tok/s)`);
        }
      } catch (err) {
        console.log(` failed (${err.message})`);
      }
    } else {
      console.warn('[Custom Bot] WARNING: Could not start Ollama — custom bot will not work.');
    }
  }

  // --- Game loop (runs once normally, repeats with --loop) ---
  const loopMode = process.argv.includes('--loop');
  let gameNumber = 0;

  while (true) {
    gameNumber++;
    if (loopMode && gameNumber > 1) {
      console.log(`\n${'='.repeat(60)}`);
      console.log(`[Main] Starting game #${gameNumber}...`);
      console.log('='.repeat(60));
    }

    // Fresh session for each game
    const gameSessionDir = initSession();
    console.log(`[Main] Log directory: ${gameSessionDir}`);
    await generateSetupDoc(gameSessionDir);

    const engine = new GameEngine({ testMode });

    try {
      await engine.initialize();

      logGameEvent({
        message: `Game #${gameNumber} starting`,
        config: CONFIG.game,
        players: CONFIG.players.map((p) => ({ id: p.id, model: p.model, container: p.container })),
        gameNumber,
      });

      const finalScores = await engine.runGame();

      // Rate limited — delete incomplete session and stop looping
      if (engine.rateLimited) {
        console.log('[Main] Game interrupted by rate limiting — deleting incomplete session.');
        try {
          const { rmSync } = require('fs');
          rmSync(gameSessionDir, { recursive: true, force: true });
          console.log(`[Main] Deleted: ${gameSessionDir}`);
        } catch (e) {
          console.error(`[Main] Failed to delete session: ${e.message}`);
        }
        await stopContainers();
        process.exit(1);
      }

      console.log(`\n[Main] Game #${gameNumber} complete! Logs saved to: ${gameSessionDir}`);

    } catch (err) {
      console.error('[FATAL]', err.message || err);

      // Delete the failed session folder
      try {
        const { rmSync } = require('fs');
        rmSync(gameSessionDir, { recursive: true, force: true });
      } catch { /* ignore */ }

      // Check for rate limiting — exit the loop entirely
      const errMsg = (err.message || '').toLowerCase();
      if (err.isRateLimitExhausted || errMsg.includes('rate_limit') || errMsg.includes('rate limit')) {
        console.log('[Main] Rate limit detected — stopping loop.');
        await stopContainers();
        process.exit(1);
      }

      if (!loopMode) {
        process.exit(1);
      }

      // In loop mode, check if it's a container error — restart containers
      if (errMsg.includes('not running') || errMsg.includes('409') || errMsg.includes('container')) {
        console.log('[Main] Container error detected — restarting containers...');
        try {
          await startContainers();
          console.log('[Main] Containers restarted.');
        } catch (restartErr) {
          console.error('[FATAL] Could not restart containers:', restartErr.message);
          process.exit(1);
        }
      }
    }

    // If not looping, exit after one game
    if (!loopMode) {
      if (!skipDocker) {
        console.log('\n[Main] Game finished. Containers are still running for inspection.');
        console.log('[Main] Run `npm run down` to stop and remove containers.');
      }
      break;
    }

    // Reset containers between games — force-recreate without rebuilding the image
    try {
      await resetContainers();
      console.log('[Main] Starting next game in 5 seconds... (Ctrl+C to stop)');
    } catch (restartErr) {
      console.error('[Main] Reset failed, trying full restart...');
      try { await stopContainers(); await startContainers(); } catch (e) {
        console.error('[FATAL] Could not restart containers:', e.message);
        process.exit(1);
      }
    }
    await new Promise(r => setTimeout(r, 5000));
  }
}

// Handle graceful shutdown
process.on('SIGINT', async () => {
  console.log('\n[Main] Received SIGINT — shutting down...');
  logGameEvent({ message: 'Game interrupted by SIGINT' });
  await stopContainers();
  process.exit(0);
});

process.on('SIGTERM', async () => {
  console.log('\n[Main] Received SIGTERM — shutting down...');
  logGameEvent({ message: 'Game interrupted by SIGTERM' });
  await stopContainers();
  process.exit(0);
});

main().catch((err) => {
  console.error('[FATAL] Unhandled error:', err);
  process.exit(1);
});
