const { mkdirSync, appendFileSync, writeFileSync } = require('fs');
const { join } = require('path');
const { CONFIG } = require('./config.js');

let sessionDir = null;
let sessionId = null;

// In-memory log buffers — flushed as JSON arrays on save/finalize
const logs = {
  game: [],
  agents: {},  // keyed by "playerId-role"
};

/**
 * Initialize a new logging session.
 */
function initSession() {
  sessionId = new Date().toISOString().replace(/[:.]/g, '-');
  sessionDir = join(CONFIG.game.logDir, `session-${sessionId}`);
  mkdirSync(sessionDir, { recursive: true });

  // Reset buffers
  logs.game = [];
  logs.agents = {};
  for (const player of CONFIG.players) {
    logs.agents[`${player.id}-attacker`] = [];
    logs.agents[`${player.id}-defender`] = [];
  }

  // Human-readable event log
  writeFileSync(join(sessionDir, 'events.log'), `=== CTF Game Session ${sessionId} ===\n\n`);

  console.log(`[Logger] Session initialized: ${sessionDir}`);
  return sessionDir;
}

/**
 * Log an agent action (thinking + command + result).
 */
function logAgentAction({ agent, role, turn, thinking, command, result, flagCaptured, meta }) {
  const entry = {
    timestamp: new Date().toISOString(),
    agent,
    role,
    turn,
    thinking,
    command,
    result: truncate(result, 5000),
    flagCaptured: flagCaptured || false,
    meta: meta || {},
  };

  logs.game.push(entry);

  const agentKey = `${agent}-${role}`;
  if (logs.agents[agentKey]) {
    logs.agents[agentKey].push(entry);
  }

  // Human-readable event log (append immediately)
  const readable = `[${entry.timestamp}] ${agent}/${role} (turn ${turn})\n` +
    `  THINKING: ${truncate(thinking, 200)}\n` +
    `  COMMAND:  ${command}\n` +
    `  RESULT:   ${truncate(result, 200)}\n` +
    (flagCaptured ? `  *** FLAG CAPTURED ***\n` : '') +
    '\n';
  appendFileSync(join(sessionDir, 'events.log'), readable);

  // Periodic flush every 50 entries as a safety net
  if (logs.game.length % 50 === 0) {
    flushAll();
  }
}

/**
 * Log a game-level event (round start, scoring, flag status, etc.)
 */
function logGameEvent(event) {
  const entry = {
    timestamp: new Date().toISOString(),
    type: 'game_event',
    ...event,
  };

  logs.game.push(entry);

  const readable = `[${entry.timestamp}] GAME: ${event.message || JSON.stringify(event)}\n`;
  appendFileSync(join(sessionDir, 'events.log'), readable);
}

/**
 * Log the final scoreboard.
 */
function logScoreboard(scores) {
  const entry = {
    timestamp: new Date().toISOString(),
    type: 'scoreboard',
    scores,
  };

  logs.game.push(entry);

  let readable = '\n========== FINAL SCOREBOARD ==========\n';
  const sorted = Object.entries(scores).sort(([, a], [, b]) => b.total - a.total);
  for (const [player, score] of sorted) {
    readable += `  ${player}: ${score.total} points (captured: ${score.flagsCaptured}, lost: ${score.flagsLost}, survived: ${score.roundsSurvived})\n`;
  }
  readable += '=======================================\n\n';
  appendFileSync(join(sessionDir, 'events.log'), readable);
  console.log(readable);

  // Final flush
  flushAll();
}

/**
 * Log API request/response details for research.
 */
function logApiCall({ agent, role, provider, requestMessages, responseText, tokensUsed, latencyMs }) {
  const entry = {
    timestamp: new Date().toISOString(),
    type: 'api_call',
    agent,
    role,
    provider,
    requestMessages,
    responseText: truncate(responseText, 10000),
    tokensUsed: tokensUsed || null,
    latencyMs,
  };

  const agentKey = `${agent}-${role}`;
  if (logs.agents[agentKey]) {
    logs.agents[agentKey].push(entry);
  }
}

/**
 * Flush all in-memory logs to JSON files.
 */
function flushAll() {
  if (!sessionDir) return;

  // Write combined game log
  writeFileSync(join(sessionDir, 'game.json'), JSON.stringify(logs.game, null, 2));

  // Write per-agent logs
  for (const [key, entries] of Object.entries(logs.agents)) {
    writeFileSync(join(sessionDir, `${key}.json`), JSON.stringify(entries, null, 2));
  }
}

function getSessionDir() {
  return sessionDir;
}

function getSessionId() {
  return sessionId;
}

function truncate(str, maxLen) {
  if (!str) return '';
  if (str.length <= maxLen) return str;
  return str.substring(0, maxLen) + '... [truncated]';
}

module.exports = { initSession, logAgentAction, logGameEvent, logScoreboard, logApiCall, flushAll, getSessionDir, getSessionId };
