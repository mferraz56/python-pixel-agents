import * as crypto from 'crypto';
import * as fs from 'fs';
import * as http from 'http';
import * as os from 'os';
import * as path from 'path';

import type { ViewerMessage } from '../../shared/messages.js';
import {
  HOOK_API_PREFIX,
  MAX_HOOK_BODY_SIZE,
  SERVER_JSON_DIR,
  SERVER_JSON_NAME,
  VIEWER_EVENTS_PATH,
  VIEWER_ROUTE_PREFIX,
  VIEWER_SSE_KEEPALIVE_MS,
} from './constants.js';

/** Discovery file written to ~/.pixel-agents/server.json so hook scripts can find the server. */
export interface ServerConfig {
  /** Port the HTTP server is listening on */
  port: number;
  /** PID of the process that owns the server */
  pid: number;
  /** Auth token required in Authorization header for hook requests */
  token: string;
  /** Timestamp (ms) when the server started */
  startedAt: number;
}

/** Callback invoked when a hook event is received from a provider's hook script. */
type HookEventCallback = (providerId: string, event: Record<string, unknown>) => void;
type ViewerBootstrapCallback = () => ViewerMessage[] | Promise<ViewerMessage[]>;

/**
 * HTTP server that receives hook events from CLI tool hook scripts.
 *
 * Routes:
 * - `POST /api/hooks/:providerId` -- hook event (auth required, 64KB body limit)
 * - `GET /api/health` -- health check (no auth)
 *
 * Discovery: writes `~/.pixel-agents/server.json` with port, PID, and auth token.
 * Multi-window: second VS Code window detects running server via server.json and
 * reuses it (does not start a second server).
 *
 * This will becomes the standalone server with added WebSocket and SPA serving.
 */
export class PixelAgentsServer {
  private server: http.Server | null = null;
  private config: ServerConfig | null = null;
  private ownsServer = false;
  private callback: HookEventCallback | null = null;
  private viewerBootstrapCallback: ViewerBootstrapCallback | null = null;
  private viewerClients = new Set<http.ServerResponse>();
  private viewerRoot: string | null = null;
  private startTime = Date.now();

  /** Register a callback for incoming hook events from any provider. */
  onHookEvent(callback: HookEventCallback): void {
    this.callback = callback;
  }

  setViewerBootstrapCallback(callback: ViewerBootstrapCallback): void {
    this.viewerBootstrapCallback = callback;
  }

  setViewerRoot(viewerRoot: string): void {
    this.viewerRoot = viewerRoot;
  }

  broadcastViewerMessage(message: ViewerMessage): void {
    const payload = this.formatSseEvent('message', message);
    for (const client of [...this.viewerClients]) {
      try {
        client.write(payload);
      } catch {
        this.viewerClients.delete(client);
      }
    }
  }

  /**
   * Start the HTTP server. If another instance is already running (detected via
   * server.json PID check), reuses that server's config without starting a new one.
   * @returns The server config (port, token) for hook script discovery.
   */
  async start(): Promise<ServerConfig> {
    // Check if another instance already has a server running
    const existing = this.readServerJson();
    if (existing && isProcessRunning(existing.pid)) {
      // Another VS Code window owns the server, reuse its config
      this.config = existing;
      this.ownsServer = false;
      console.log(
        `[Pixel Agents] Reusing existing server on port ${existing.port} (PID ${existing.pid})`,
      );
      return existing;
    }

    // Start our own server
    const token = crypto.randomUUID();
    this.startTime = Date.now();

    return new Promise((resolve, reject) => {
      this.server = http.createServer((req, res) => {
        void this.handleRequest(req, res);
      });

      this.server.on('error', reject);
      this.server.setTimeout(5000);

      this.server.listen(0, '0.0.0.0', () => {
        const addr = this.server?.address();
        if (addr && typeof addr === 'object') {
          this.config = {
            port: addr.port,
            pid: process.pid,
            token,
            startedAt: this.startTime,
          };
          this.ownsServer = true;
          this.writeServerJson(this.config);
          // Replace startup error handler with runtime error handler
          this.server!.removeListener('error', reject);
          this.server!.on('error', (err) => {
            console.error(`[Pixel Agents] Server: error: ${err}`);
          });
          console.log(`[Pixel Agents] Server: listening on 0.0.0.0:${addr.port}`);
          resolve(this.config);
        } else {
          reject(new Error('Failed to get server address'));
        }
      });
    });
  }

  /** Stop the HTTP server and clean up server.json (only if we own it). */
  stop(): void {
    for (const client of this.viewerClients) {
      try {
        client.end();
      } catch {
        // ignore close errors
      }
    }
    this.viewerClients.clear();
    if (this.server) {
      this.server.close();
      this.server = null;
    }
    // Only delete server.json if we own it (our PID)
    if (this.ownsServer) {
      this.deleteServerJson();
    }
    this.config = null;
    this.ownsServer = false;
  }

  /** Returns the current server config, or null if not started. */
  getConfig(): ServerConfig | null {
    return this.config;
  }

  /** Top-level request router. Dispatches to health or hook handler based on method + path. */
  private async handleRequest(
    req: http.IncomingMessage,
    res: http.ServerResponse,
  ): Promise<void> {
    const requestUrl = new URL(req.url ?? '/', 'http://127.0.0.1');
    const pathname = requestUrl.pathname;

    // Health endpoint (no auth required)
    if (req.method === 'GET' && pathname === '/api/health') {
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(
        JSON.stringify({
          status: 'ok',
          uptime: Math.floor((Date.now() - this.startTime) / 1000),
          pid: process.pid,
        }),
      );
      return;
    }

    if (req.method === 'GET' && pathname === VIEWER_EVENTS_PATH) {
      await this.handleViewerEventsRequest(req, res, requestUrl);
      return;
    }

    if (req.method === 'GET' && (pathname === VIEWER_ROUTE_PREFIX || pathname === `${VIEWER_ROUTE_PREFIX}/`)) {
      if (pathname === VIEWER_ROUTE_PREFIX) {
        res.writeHead(302, { Location: `${VIEWER_ROUTE_PREFIX}/` });
        res.end();
        return;
      }
      this.serveViewerFile(res, 'index.html');
      return;
    }

    if (req.method === 'GET' && pathname.startsWith(`${VIEWER_ROUTE_PREFIX}/`)) {
      const relativePath = pathname.slice(VIEWER_ROUTE_PREFIX.length + 1);
      this.serveViewerFile(res, relativePath);
      return;
    }

    // Hook event endpoint: POST /api/hooks/:providerId
    if (req.method === 'POST' && pathname.startsWith(HOOK_API_PREFIX + '/')) {
      this.handleHookRequest(req, res, pathname);
      return;
    }

    res.writeHead(404);
    res.end();
  }

  private async handleViewerEventsRequest(
    req: http.IncomingMessage,
    res: http.ServerResponse,
    requestUrl: URL,
  ): Promise<void> {
    if (!this.isViewerAuthorized(req, requestUrl)) {
      res.writeHead(401);
      res.end('unauthorized');
      return;
    }

    const bootstrapMessages = this.viewerBootstrapCallback
      ? await Promise.resolve(this.viewerBootstrapCallback())
      : [];

    res.writeHead(200, {
      'Content-Type': 'text/event-stream',
      'Cache-Control': 'no-cache, no-transform',
      Connection: 'keep-alive',
      'Access-Control-Allow-Origin': '*',
    });
    res.write(this.formatSseEvent('bootstrap', bootstrapMessages));

    const keepAlive = setInterval(() => {
      try {
        res.write(':keepalive\n\n');
      } catch {
        clearInterval(keepAlive);
      }
    }, VIEWER_SSE_KEEPALIVE_MS);

    this.viewerClients.add(res);
    req.on('close', () => {
      clearInterval(keepAlive);
      this.viewerClients.delete(res);
    });
  }

  private isViewerAuthorized(req: http.IncomingMessage, requestUrl: URL): boolean {
    const queryToken = requestUrl.searchParams.get('token') ?? '';
    const authHeader = req.headers['authorization'] ?? '';
    const expectedRaw = this.config?.token ?? '';
    const expectedBearer = `Bearer ${expectedRaw}`;
    return (
      this.timingSafeMatches(queryToken, expectedRaw) ||
      this.timingSafeMatches(authHeader, expectedBearer)
    );
  }

  private serveViewerFile(res: http.ServerResponse, relativePath: string): void {
    if (!this.viewerRoot) {
      res.writeHead(404);
      res.end('viewer unavailable');
      return;
    }

    const root = path.resolve(this.viewerRoot);
    const targetPath = path.resolve(root, relativePath || 'index.html');
    if (!targetPath.startsWith(root + path.sep) && targetPath !== root) {
      res.writeHead(403);
      res.end('forbidden');
      return;
    }

    let filePath = targetPath;
    if (!fs.existsSync(filePath) || !fs.statSync(filePath).isFile()) {
      filePath = path.join(root, 'index.html');
    }
    if (!fs.existsSync(filePath)) {
      res.writeHead(404);
      res.end('not found');
      return;
    }

    res.writeHead(200, { 'Content-Type': this.getContentType(filePath) });
    fs.createReadStream(filePath).pipe(res);
  }

  private getContentType(filePath: string): string {
    switch (path.extname(filePath).toLowerCase()) {
      case '.html':
        return 'text/html; charset=utf-8';
      case '.js':
        return 'application/javascript; charset=utf-8';
      case '.css':
        return 'text/css; charset=utf-8';
      case '.json':
        return 'application/json; charset=utf-8';
      case '.svg':
        return 'image/svg+xml';
      case '.png':
        return 'image/png';
      case '.jpg':
      case '.jpeg':
        return 'image/jpeg';
      case '.webp':
        return 'image/webp';
      case '.woff':
        return 'font/woff';
      case '.woff2':
        return 'font/woff2';
      default:
        return 'application/octet-stream';
    }
  }

  private formatSseEvent(event: string, payload: unknown): string {
    return `event: ${event}\ndata: ${JSON.stringify(payload)}\n\n`;
  }

  private timingSafeMatches(actual: string, expected: string): boolean {
    const actualBuf = Buffer.from(actual);
    const expectedBuf = Buffer.from(expected);
    return (
      actualBuf.length === expectedBuf.length &&
      crypto.timingSafeEqual(actualBuf, expectedBuf)
    );
  }

  /** Handle POST /api/hooks/:providerId. Validates auth, enforces body size limit, parses JSON. */
  private handleHookRequest(
    req: http.IncomingMessage,
    res: http.ServerResponse,
    url: string,
  ): void {
    // Validate auth token (timing-safe comparison prevents side-channel attacks)
    const authHeader = req.headers['authorization'] ?? '';
    const expectedToken = `Bearer ${this.config?.token ?? ''}`;
    const authBuf = Buffer.from(authHeader);
    const expectedBuf = Buffer.from(expectedToken);
    if (authBuf.length !== expectedBuf.length || !crypto.timingSafeEqual(authBuf, expectedBuf)) {
      res.writeHead(401);
      res.end('unauthorized');
      return;
    }

    // Extract and validate provider ID from URL: /api/hooks/claude -> "claude"
    const providerId = url.slice(HOOK_API_PREFIX.length + 1);
    if (!providerId || !/^[a-z0-9-]+$/.test(providerId)) {
      res.writeHead(400);
      res.end('invalid provider id');
      return;
    }

    // Read body with size limit and response guard
    let body = '';
    let bodySize = 0;
    let responded = false;

    req.on('data', (chunk: Buffer) => {
      bodySize += chunk.length;
      if (bodySize > MAX_HOOK_BODY_SIZE && !responded) {
        responded = true;
        res.writeHead(413);
        res.end('payload too large');
        req.destroy();
        return;
      }
      if (!responded) {
        body += chunk.toString();
      }
    });

    req.on('end', () => {
      if (responded) return;
      try {
        const event = JSON.parse(body) as Record<string, unknown>;
        if (event.session_id && event.hook_event_name) {
          this.callback?.(providerId, event);
        }
        res.writeHead(200);
        res.end('ok');
      } catch {
        res.writeHead(400);
        res.end('invalid json');
      }
    });
  }

  /** Returns the absolute path to ~/.pixel-agents/server.json. */
  private getServerJsonPath(): string {
    return path.join(os.homedir(), SERVER_JSON_DIR, SERVER_JSON_NAME);
  }

  /** Read and parse server.json. Returns null if missing or malformed. */
  private readServerJson(): ServerConfig | null {
    try {
      const filePath = this.getServerJsonPath();
      if (!fs.existsSync(filePath)) return null;
      return JSON.parse(fs.readFileSync(filePath, 'utf-8')) as ServerConfig;
    } catch {
      return null;
    }
  }

  /** Write server.json atomically (tmp + rename) with mode 0o600. */
  private writeServerJson(config: ServerConfig): void {
    const filePath = this.getServerJsonPath();
    const dir = path.dirname(filePath);
    try {
      if (!fs.existsSync(dir)) {
        fs.mkdirSync(dir, { recursive: true, mode: 0o700 });
      }
      // Atomic write with restricted permissions
      const tmpPath = filePath + '.tmp';
      fs.writeFileSync(tmpPath, JSON.stringify(config, null, 2), { mode: 0o600 });
      fs.renameSync(tmpPath, filePath);
    } catch (e) {
      console.error(`[Pixel Agents] Failed to write server.json: ${e}`);
    }
  }

  /** Delete server.json only if the PID inside matches our process (safe for multi-window). */
  private deleteServerJson(): void {
    try {
      const filePath = this.getServerJsonPath();
      if (!fs.existsSync(filePath)) return;
      // Only delete if our PID matches (don't delete another instance's server file)
      const existing = JSON.parse(fs.readFileSync(filePath, 'utf-8')) as ServerConfig;
      if (existing.pid === process.pid) {
        fs.unlinkSync(filePath);
      }
    } catch {
      // File may already be gone
    }
  }
}

/** Check if a process is alive by sending signal 0 (no-op, just checks existence). */
function isProcessRunning(pid: number): boolean {
  try {
    process.kill(pid, 0);
    return true;
  } catch {
    return false;
  }
}
