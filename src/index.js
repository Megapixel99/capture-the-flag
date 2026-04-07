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

  if (CONFIG.game.segmented && !testMode) {
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

  // Pre-pull Ollama models if using local models
  if (CONFIG.game.useLocalModels && !testMode) {
    console.log('[Ollama] Checking Ollama at', CONFIG.api.ollama.baseUrl, '...');
    let healthy = await checkOllamaHealth();
    if (!healthy) {
      // Try to auto-start Ollama
      console.log('[Ollama] Not running. Attempting to start automatically...');
      const { execSync, spawn } = require('child_process');
      let ollamaPath = null;
      try {
        ollamaPath = execSync('which ollama 2>/dev/null', { encoding: 'utf-8' }).trim();
      } catch {
        // Check common install locations
        const candidates = [
          '/usr/local/bin/ollama',
          '/opt/homebrew/bin/ollama',
          '/usr/bin/ollama',
        ];
        for (const p of candidates) {
          try {
            require('fs').accessSync(p, require('fs').constants.X_OK);
            ollamaPath = p;
            break;
          } catch { /* not found */ }
        }
      }

      if (ollamaPath) {
        // Start ollama serve in the background
        const child = spawn(ollamaPath, ['serve'], {
          detached: true,
          stdio: 'ignore',
        });
        child.unref();
        console.log(`[Ollama] Started "ollama serve" (PID ${child.pid}). Waiting for it to be ready...`);

        // Poll until ready (up to 15 seconds)
        for (let i = 0; i < 15; i++) {
          await new Promise((r) => setTimeout(r, 1000));
          healthy = await checkOllamaHealth();
          if (healthy) break;
        }
      }

      if (!healthy) {
        console.error('[ERROR] Could not start Ollama automatically.');
        console.error('');
        console.error('Install and start Ollama manually:');
        console.error('  macOS:   Download from https://ollama.com/download');
        console.error('  Linux:   curl -fsSL https://ollama.com/install.sh | sh');
        console.error('  Then:    ollama serve');
        process.exit(1);
      }
    }
    console.log('[Ollama] Connected. Pre-pulling models...');

    const ollamaPlayers = CONFIG.players.filter((p) => p.provider === 'ollama');
    for (const player of ollamaPlayers) {
      await pullOllamaModel(player.model);
    }
    // Pre-warm each model so it's loaded into GPU memory before the game starts
    console.log('[Ollama] Pre-warming models (loading into memory)...');
    const uniqueModels = [...new Set(ollamaPlayers.map((p) => p.model))];
    for (const model of uniqueModels) {
      process.stdout.write(`[Ollama]   Warming ${model}...`);
      const ok = await warmModel(model);
      console.log(ok ? ' ready' : ' failed (will retry during game)');
    }
    console.log('[Ollama] All local models ready.\n');
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
