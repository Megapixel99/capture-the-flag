const Docker = require('dockerode');
const { PassThrough } = require('stream');
const { execSync } = require('child_process');
const { CONFIG } = require('./config.js');

const docker = new Docker();

/**
 * Build the vulnerable CTF image.
 */
async function buildImage() {
  console.log('[Docker] Building CTF image...');
  const stream = await docker.buildImage(
    { context: './docker', src: ['Dockerfile', 'setup-vulnerabilities.sh'] },
    { t: CONFIG.docker.imageName }
  );
  await new Promise((resolve, reject) => {
    docker.modem.followProgress(stream, (err, output) => {
      if (err) reject(err);
      else resolve(output);
    }, (event) => {
      if (event.stream) process.stdout.write(event.stream);
    });
  });
  console.log('[Docker] Image built successfully.');
}

/**
 * Start all player containers using docker compose.
 */
async function startContainers() {
  console.log('[Docker] Starting containers...');

  // Build the list of services to start — only CTF player containers.
  // Ollama runs natively on the host (not in Docker) for best performance.
  let services;
  if (CONFIG.game.segmented) {
    const dmzServices = CONFIG.players.map((p) => p.dmzContainer);
    const intServices = CONFIG.players.map((p) => p.internalContainer);
    services = [...new Set([...dmzServices, ...intServices])];
  } else {
    services = CONFIG.players.map((p) => p.container);
  }
  const uniqueServices = [...new Set(services)];
  const serviceArgs = uniqueServices.join(' ');

  // If any service has "-open" suffix, we need the "all" profile
  const needsAllProfile = uniqueServices.some((s) => s.endsWith('-open'));
  const profileFlag = needsAllProfile ? '--profile all' : '';

  const composeFiles = CONFIG.game.segmented
    ? '-f docker/docker-compose.yml -f docker/docker-compose.segmented.yml'
    : '-f docker/docker-compose.yml';

  console.log(`[Docker] Services: ${serviceArgs}`);
  execSync(`docker compose ${composeFiles} ${profileFlag} up -d --build ${serviceArgs}`, {
    stdio: 'inherit',
    cwd: process.cwd(),
    timeout: 120000,
  });

  // Wait for containers to be healthy
  console.log('[Docker] Waiting for containers to initialize...');
  await sleep(CONFIG.game.segmented ? 8000 : 5000);

  // Verify all containers are running
  if (CONFIG.game.segmented) {
    for (const player of CONFIG.players) {
      // Verify DMZ
      const dmz = docker.getContainer(player.dmzContainer);
      const dmzInfo = await dmz.inspect();
      const dmzNets = dmzInfo.NetworkSettings.Networks;
      const dmzKey = Object.keys(dmzNets).find((k) => k.includes('ctf-dmz') || k.includes('ctf-net'));
      const dmzIp = dmzKey ? dmzNets[dmzKey].IPAddress : 'unknown';
      console.log(`[Docker] ${player.dmzContainer} (DMZ: ${dmzIp}) — running`);
      // Verify internal
      const int = docker.getContainer(player.internalContainer);
      const intInfo = await int.inspect();
      console.log(`[Docker] ${player.internalContainer} (Internal: ${player.internalIp}) — running`);
    }
  } else {
    for (const player of CONFIG.players) {
      const container = docker.getContainer(player.container);
      const info = await container.inspect();
      if (!info.State.Running) {
        throw new Error(`Container ${player.container} is not running`);
      }
      const nets = info.NetworkSettings.Networks;
      const netKey = Object.keys(nets).find((k) => k.includes('ctf-net'));
      const ip = netKey ? nets[netKey].IPAddress : 'unknown';
      console.log(`[Docker] ${player.container} (${ip}) — running`);
    }
  }
}

/**
 * Execute a command inside a player's container.
 * Returns { stdout, stderr, exitCode }.
 */
async function execCommand(containerName, command, timeoutMs = CONFIG.game.commandTimeoutMs) {
  const container = docker.getContainer(containerName);

  const exec = await container.exec({
    Cmd: ['bash', '-c', command],
    AttachStdout: true,
    AttachStderr: true,
  });

  return new Promise((resolve, reject) => {
    const timeout = setTimeout(() => {
      resolve({ stdout: '', stderr: 'Command timed out', exitCode: -1 });
    }, timeoutMs);

    exec.start({ hijack: true, stdin: false }, (err, stream) => {
      if (err) {
        clearTimeout(timeout);
        return reject(err);
      }

      let stdout = '';
      let stderr = '';

      // Docker multiplexes stdout/stderr with 8-byte headers per frame.
      // Use dockerode's demuxStream to split them properly.
      const stdoutStream = new PassThrough();
      const stderrStream = new PassThrough();

      stdoutStream.on('data', (chunk) => { stdout += chunk.toString('utf-8'); });
      stderrStream.on('data', (chunk) => { stderr += chunk.toString('utf-8'); });

      docker.modem.demuxStream(stream, stdoutStream, stderrStream);

      stream.on('end', async () => {
        clearTimeout(timeout);

        try {
          const inspectResult = await exec.inspect();
          resolve({ stdout: stdout.trim(), stderr: stderr.trim(), exitCode: inspectResult.ExitCode });
        } catch {
          resolve({ stdout: stdout.trim(), stderr: stderr.trim(), exitCode: -1 });
        }
      });

      stream.on('error', (streamErr) => {
        clearTimeout(timeout);
        reject(streamErr);
      });
    });
  });
}

/**
 * Read the flag from a specific container (used by game engine to verify captures).
 */
async function readFlag(containerName) {
  const result = await execCommand(containerName, `cat ${CONFIG.game.flagPath} 2>/dev/null`);
  return result.stdout.trim();
}

/**
 * Stop and remove all containers.
 */
async function stopContainers() {
  console.log('[Docker] Stopping containers...');
  try {
    const composeFiles = CONFIG.game.segmented
      ? '-f docker/docker-compose.yml -f docker/docker-compose.segmented.yml'
      : '-f docker/docker-compose.yml';
    execSync(`docker compose ${composeFiles} down`, {
      stdio: 'inherit',
      cwd: process.cwd(),
      timeout: 60000,
    });
  } catch (e) {
    console.error('[Docker] Error stopping containers:', e.message);
  }
}

/**
 * Get network info for all containers (IPs, hostnames).
 */
async function getNetworkInfo() {
  const info = {};
  if (CONFIG.game.segmented) {
    for (const player of CONFIG.players) {
      const dmz = docker.getContainer(player.dmzContainer);
      const dmzData = await dmz.inspect();
      const dmzNets = dmzData.NetworkSettings.Networks;
      const dmzKey = Object.keys(dmzNets).find((k) => k.includes('ctf-dmz') || k.includes('ctf-net'));
      info[player.id] = {
        dmzContainer: player.dmzContainer,
        internalContainer: player.internalContainer,
        hostname: dmzData.Config.Hostname,
        ip: dmzKey ? dmzNets[dmzKey].IPAddress : 'unknown',
        internalIp: player.internalIp,
      };
    }
  } else {
    for (const player of CONFIG.players) {
      const container = docker.getContainer(player.container);
      const data = await container.inspect();
      // Docker Compose prefixes network names (e.g., "docker_ctf-net"), so find the matching key
      const networks = data.NetworkSettings.Networks;
      const networkKey = Object.keys(networks).find((k) => k.includes('ctf-net'));
      const networkData = networkKey ? networks[networkKey] : null;
      info[player.id] = {
        container: player.container,
        hostname: data.Config.Hostname,
        ip: networkData?.IPAddress || 'unknown',
      };
    }
  }
  return info;
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/**
 * Reset containers between loop games — destroy and recreate without rebuilding the image.
 * Much faster than startContainers() which runs --build.
 */
async function resetContainers() {
  console.log('[Docker] Resetting containers (no rebuild)...');
  const composeFiles = CONFIG.game.segmented
    ? '-f docker/docker-compose.yml -f docker/docker-compose.segmented.yml'
    : '-f docker/docker-compose.yml';

  const services = CONFIG.game.segmented
    ? [...new Set(CONFIG.players.flatMap(p => [p.dmzContainer, p.internalContainer]))]
    : [...new Set(CONFIG.players.map(p => p.container))];
  const serviceArgs = services.join(' ');

  const needsAllProfile = services.some(s => s.endsWith('-open'));
  const profileFlag = needsAllProfile ? '--profile all' : '';

  // Force recreate containers (destroys old, creates new from existing image)
  execSync(`docker compose ${composeFiles} ${profileFlag} up -d --force-recreate ${serviceArgs}`, {
    stdio: 'inherit',
    cwd: process.cwd(),
    timeout: 60000,
  });

  console.log('[Docker] Waiting for containers to initialize...');
  await sleep(5000);

  // Quick verify
  for (const svc of services) {
    try {
      const c = docker.getContainer(svc);
      const info = await c.inspect();
      if (!info.State.Running) throw new Error(`${svc} not running`);
    } catch (err) {
      console.error(`[Docker] ${svc}: ${err.message}`);
    }
  }
  console.log('[Docker] Containers reset.');
}

module.exports = { buildImage, startContainers, execCommand, readFlag, stopContainers, resetContainers, getNetworkInfo };
