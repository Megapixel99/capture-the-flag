const { CONFIG } = require('./config.js');
const { buildImage, startContainers, stopContainers } = require('./docker-manager.js');
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
      const child = spawn(ollamaPath, ['serve'], {
        detached: true, stdio: 'ignore',
        env: { ...process.env },
      });
      child.unref();
      console.log(`[Ollama] Started (PID ${child.pid})`);

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

  // Generate setup documentation
  await generateSetupDoc(sessionDir);

  // Create and initialize game engine
  const engine = new GameEngine({ testMode });

  try {
    await engine.initialize();

    logGameEvent({
      message: 'Game starting',
      config: CONFIG.game,
      players: CONFIG.players.map((p) => ({ id: p.id, model: p.model, container: p.container })),
    });

    // Run the game
    const finalScores = await engine.runGame();

    // If rate limited, delete the incomplete session and stop
    if (engine.rateLimited) {
      console.log('[Main] Game interrupted by rate limiting — deleting incomplete session.');
      try {
        const { rmSync } = require('fs');
        rmSync(sessionDir, { recursive: true, force: true });
        console.log(`[Main] Deleted: ${sessionDir}`);
      } catch (e) {
        console.error(`[Main] Failed to delete session: ${e.message}`);
      }
      await stopContainers();
      process.exit(1);
    }

    console.log('\n[Main] Game complete! Logs saved to:', sessionDir);
    console.log('[Main] Files generated:');
    console.log('  - game.json (all events)');
    console.log('  - events.log (human-readable)');
    for (const p of CONFIG.players) {
      console.log(`  - ${p.id}-attacker.json`);
      console.log(`  - ${p.id}-defender.json`);
    }

    return finalScores;
  } catch (err) {
    console.error('[FATAL]', err);
    logGameEvent({ message: `Fatal error: ${err.message}`, stack: err.stack });
  } finally {
    // Ask before stopping containers so user can inspect state
    if (!skipDocker) {
      console.log('\n[Main] Game finished. Containers are still running for inspection.');
      console.log('[Main] Run `npm run down` to stop and remove containers.');
    }
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
